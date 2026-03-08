"""ConversationContext: state object that flows through the message pipeline.

Replaces the ad-hoc variable passing in _handle_message. Every subsystem reads
from this object instead of receiving individual parameters.

Key design decisions:
- Built in one call via ConversationContext.build() that runs DB fetches in parallel
- Immutable after construction (scratchpad is the only mutable field)
- Carries routing state (sticky_categories) so the classifier can fall back intelligently
- Carries user_facts for tool loop injection without extra DB queries
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.context.fact_extractor import extract_facts

if TYPE_CHECKING:
    from app.config import Settings
    from app.conversation.manager import ConversationManager
    from app.database.repository import Repository
    from app.llm.client import OllamaClient
    from app.memory.daily_log import DailyLog
    from app.models import ChatMessage, Note

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """State snapshot for a single user message, built before the pipeline starts."""

    # Identity
    phone_number: str
    user_text: str
    conv_id: int

    # Pre-fetched data (built in parallel during .build())
    user_facts: dict[str, str] = field(default_factory=dict)
    """Structured key→value facts extracted from memories (e.g. github_username='sebadp')."""

    memories: list[str] = field(default_factory=list)
    """All active memory strings for this user."""

    history: list[ChatMessage] = field(default_factory=list)
    """Recent conversation messages (windowed to verbatim_count)."""

    summary: str | None = None
    """Latest conversation summary (covers messages older than the verbatim window)."""

    daily_logs: str | None = None
    """Recent activity logs injected into context."""

    relevant_notes: list[Note] = field(default_factory=list)
    """Semantically relevant notes for this message."""

    projects_summary: str | None = None
    """Brief summary of active projects for this user."""

    # Routing state
    sticky_categories: list[str] = field(default_factory=list)
    """Categories from the last turn that used tools. Used as fallback for ambiguous follow-ups."""

    current_categories: list[str] = field(default_factory=list)
    """Resolved categories for this turn (set after classify_intent)."""

    # Tool loop state (written during execution, read at the end)
    scratchpad: str = ""
    """Structured notes the agent writes during the tool loop for cross-iteration coherence."""

    # Embeddings (optional)
    query_embedding: list[float] | None = None

    # Semantic search stats (populated during build, best-effort)
    search_stats: dict = field(default_factory=dict)

    # Token budget estimate (populated after build)
    token_estimate: int = 0

    @classmethod
    async def build(
        cls,
        phone_number: str,
        user_text: str,
        repository: Repository,
        conversation_manager: ConversationManager,
        ollama_client: OllamaClient | None = None,
        settings: Settings | None = None,
        daily_log: DailyLog | None = None,
        vec_available: bool = False,
    ) -> ConversationContext:
        """Build a ConversationContext by fetching all data in parallel.

        Extends the base build with:
        - query_embedding (via Ollama embed)
        - daily_logs (from DailyLog)
        - relevant_notes (semantic search)
        - projects_summary (active projects)
        - windowed history (verbatim_count latest + summary of older)

        Falls back gracefully if any optional dependency is unavailable.
        """
        conv_id = await conversation_manager.get_conversation_id(phone_number)

        async def _none() -> None:
            return None

        async def _empty_list() -> list[str]:
            return []

        async def _get_query_embedding() -> list[float] | None:
            if (
                ollama_client is None
                or settings is None
                or not settings.semantic_search_enabled
                or not vec_available
                or not user_text
            ):
                return None
            try:
                result = await ollama_client.embed(
                    [user_text],
                    model=settings.embedding_model,
                )
                return result[0]
            except Exception:
                logger.warning(
                    "ConversationContext: failed to compute query embedding", exc_info=True
                )
                return None

        async def _get_daily_logs() -> str | None:
            if daily_log is None or settings is None:
                return None
            try:
                return await daily_log.load_recent(days=settings.daily_log_days)
            except Exception:
                logger.warning("ConversationContext: failed to load daily logs", exc_info=True)
                return None

        async def _get_relevant_notes(embedding: list[float] | None) -> list[Note]:
            if (
                settings is None
                or not settings.semantic_search_enabled
                or not vec_available
                or embedding is None
            ):
                return []
            try:
                return await repository.search_similar_notes(embedding, top_k=5)
            except Exception:
                logger.warning("ConversationContext: semantic note search failed", exc_info=True)
                return []

        async def _get_projects_summary() -> str | None:
            try:
                projects = await repository.list_projects(phone_number, status="active")
                if not projects:
                    return None
                capped = projects[:5]
                lines = ["Active projects:"]
                for p in capped:
                    progress = await repository.get_project_progress(p.id)
                    total = progress["total"]
                    done = progress["done"]
                    pct = int(done / total * 100) if total > 0 else 0
                    lines.append(f"  - {p.name}: {done}/{total} tasks ({pct}%)")
                return "\n".join(lines)
            except Exception:
                logger.warning(
                    "ConversationContext: failed to fetch projects summary", exc_info=True
                )
                return None

        async def _get_memories_with_threshold(
            embedding: list[float] | None,
        ) -> tuple[list[str], dict]:
            stats: dict = {
                "search_mode": "full_fallback",
                "memories_retrieved": 0,
                "memories_passed": 0,
                "memories_returned": 0,
            }
            if embedding is not None and settings is not None:
                try:
                    results = await repository.search_similar_memories_with_distance(
                        embedding,
                        top_k=settings.semantic_search_top_k,
                    )
                    stats["memories_retrieved"] = len(results)
                    threshold = settings.memory_similarity_threshold
                    passed = [content for content, dist in results if dist < threshold]
                    stats["memories_passed"] = len(passed)
                    if not passed and results:
                        passed = [content for content, _ in results[:3]]
                        stats["search_mode"] = "fallback_threshold"
                    else:
                        stats["search_mode"] = "semantic"
                    stats["memories_returned"] = len(passed)
                    return passed, stats
                except Exception:
                    logger.warning(
                        "ConversationContext: semantic memory search failed, falling back",
                        exc_info=True,
                    )
            top_k = settings.semantic_search_top_k if settings else 10
            memories = await repository.get_active_memories(limit=top_k)
            stats["memories_returned"] = len(memories)
            return memories, stats

        verbatim_count = settings.history_verbatim_count if settings else 8

        # Step 1: get query embedding first (needed for memories and notes)
        query_embedding = await _get_query_embedding()

        # Step 2: parallel fetches (all independent now that embedding is ready)
        (
            memories_and_stats,
            windowed,
            sticky,
            logs,
            relevant_notes,
            projects_summary,
        ) = await asyncio.gather(
            _get_memories_with_threshold(query_embedding),
            conversation_manager.get_windowed_history(phone_number, verbatim_count=verbatim_count),
            repository.get_sticky_categories(conv_id) if conv_id else _empty_list(),
            _get_daily_logs(),
            _get_relevant_notes(query_embedding),
            _get_projects_summary(),
        )

        memories_raw, search_stats = memories_and_stats
        history, summary = windowed

        user_facts = extract_facts(memories_raw)
        if user_facts:
            logger.debug(
                "ConversationContext: extracted %d user facts: %s",
                len(user_facts),
                list(user_facts.keys()),
            )

        logger.debug(
            "ConversationContext: search_stats=%s",
            search_stats,
            extra={"search_stats": search_stats, "phone": phone_number},
        )

        return cls(
            phone_number=phone_number,
            user_text=user_text,
            conv_id=conv_id or 0,
            user_facts=user_facts,
            memories=memories_raw,
            history=history,
            summary=summary,
            daily_logs=logs,
            relevant_notes=relevant_notes,
            projects_summary=projects_summary,
            sticky_categories=sticky or [],
            query_embedding=query_embedding,
            search_stats=search_stats,
        )
