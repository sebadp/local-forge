from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.llm.client import OllamaClient

from app.models import ChatMessage
from app.tracing.context import get_current_trace

logger = logging.getLogger(__name__)

# Maps category names to tool names that belong to that category.
# Builtin tools use their exact registered names.
# MCP tools use the names exposed by the MCP server.
TOOL_CATEGORIES: dict[str, list[str]] = {
    "time": [
        "get_current_datetime",
        "convert_timezone",
        "schedule_task",
        "list_schedules",
        "create_cron",
        "list_crons",
        "delete_cron",
    ],
    "math": ["calculate"],
    "weather": ["get_weather"],
    "search": ["web_search"],
    "news": ["search_news", "add_news_preference"],
    "notes": ["save_note", "list_notes", "search_notes", "delete_note", "get_note"],
    "files": [
        "read_file",
        "write_file",
        "list_directory",
        "search_files",
        "read_text_file",
        "list_allowed_directories",
    ],
    "memory": [
        "create_entities",
        "add_observations",
        "search_nodes",
        "read_graph",
        "open_nodes",
        "delete_entities",
        "delete_observations",
        "delete_relations",
        "create_relations",
    ],
    "github": [
        "get_file_contents",
        "search_repositories",
        "list_issues",
        "list_pull_requests",
        "create_issue",
        "create_pull_request",
        "create_or_update_file",
    ],
    "tools": ["list_tool_categories", "list_category_tools"],
    "selfcode": [
        "get_version_info",
        "read_source_file",
        "list_source_files",
        "get_runtime_config",
        "get_system_health",
        "search_source_code",
        "get_skill_details",
        "get_recent_logs",
        "get_file_outline",
        "read_lines",
    ],
    "expand": [
        "search_mcp_registry",
        "get_mcp_server_info",
        "install_from_smithery",
        "install_mcp_server",
        "remove_mcp_server",
        "list_mcp_servers",
        "preview_skill_from_url",
        "install_skill_from_url",
        "reload_capabilities",
    ],
    "projects": [
        "create_project",
        "list_projects",
        "get_project",
        "add_task",
        "update_task",
        "delete_task",
        "project_progress",
        "update_project_status",
        "add_project_note",
        "search_project_notes",
    ],
    "evaluation": [
        "get_eval_summary",
        "list_recent_failures",
        "diagnose_trace",
        "propose_correction",
        "add_to_dataset",
        "get_dataset_stats",
        "run_quick_eval",
        "propose_prompt_change",
        "get_dashboard_stats",
    ],
    "debugging": [
        "review_interactions",
        "get_tool_output_full",
        "get_interaction_context",
        "write_debug_report",
        "get_conversation_transcript",
    ],
    "conversation": ["get_recent_messages", "get_conversation_transcript"],
    "shell": ["run_command", "manage_process"],
    "workspace": ["list_workspaces", "switch_workspace", "get_workspace_info"],
    "documentation": [
        "create_feature_docs",
        "update_architecture_rules",
        "update_agent_docs",
    ],
}

DEFAULT_CATEGORIES = ["time", "math", "weather", "search", "documentation"]

# Maps worker_type -> list of TOOL_CATEGORIES that the worker should use.
# Used by the planner-orchestrator to give each worker a focused tool set.
WORKER_TOOL_SETS: dict[str, list[str]] = {
    # Order matters: first categories get priority when max_tools < len(categories).
    # Research/action tools first, introspection tools last.
    "reader": ["search", "fetch", "github", "files", "news", "notes", "conversation", "debugging"],
    "analyzer": ["github", "search", "evaluation", "selfcode", "debugging", "news"],
    "coder": ["selfcode", "shell", "github"],
    "reporter": ["evaluation", "notes", "debugging"],
    "general": ["search", "fetch", "github", "files", "shell", "selfcode", "notes", "conversation"],
}

_CLASSIFIER_PROMPT_TEMPLATE = (
    "Classify this message into tool categories. "
    'Reply with ONLY category names separated by commas, or "none".\n'
    "Categories: {categories}, none\n\n"
    "Examples:\n"
    '"what time is it" → time\n'
    '"15% of 230" → math\n'
    '"remember that I like coffee" → notes\n'
    '"search for restaurants nearby" → search\n'
    '"show my projects" → projects\n'
    '"tell me a joke" → none\n'
    '"recuerdame en 5 minutos" → time\n'
    '"set a reminder for tomorrow at 3pm" → time\n'
    '"programa un cron diario a las 9" → time\n'
    '"agrega una nota al proyecto X" → projects\n'
    '"what are the latest news about AI" → news\n'
    '"busca informacion sobre Python 3.13" → search\n\n'
    "{recent_context}"
    "Message to classify: {user_message}"
)


def register_dynamic_category(category: str, tool_names: list[str]) -> None:
    """Add or update a category in TOOL_CATEGORIES at runtime.

    Used by McpManager when hot-adding servers so the classifier can
    route messages to the new tools.
    """
    existing = TOOL_CATEGORIES.get(category, [])
    # Merge without duplicates, preserving order
    merged = list(existing)
    for name in tool_names:
        if name not in merged:
            merged.append(name)
    TOOL_CATEGORIES[category] = merged
    logger.info("Dynamic category registered: %s (%d tools)", category, len(merged))


async def classify_intent(
    user_message: str,
    ollama_client: OllamaClient,
    recent_messages: list[ChatMessage] | None = None,
    sticky_categories: list[str] | None = None,
    repository: object | None = None,
) -> list[str]:
    """Classify the user message into tool categories with optional conversational context.

    Args:
        user_message: The latest user message to classify.
        ollama_client: LLM client for classification.
        recent_messages: Last few conversation messages for context. Helps classify
            ambiguous follow-ups like 'Ambos' or 'Los de los últimos meses'.
        sticky_categories: Categories from the previous tool-using turn. Used as
            fallback when the classifier returns 'none' for short follow-ups.
    """
    categories_str = ", ".join(TOOL_CATEGORIES.keys())

    # Fetch active prompt template (from DB/registry if versioning enabled, else hardcoded)
    if repository is not None:
        try:
            from app.eval.prompt_manager import get_active_prompt

            classifier_template = await get_active_prompt("classifier", repository)
        except Exception:
            logger.warning("Failed to fetch classifier prompt from DB, using hardcoded")
            classifier_template = _CLASSIFIER_PROMPT_TEMPLATE
    else:
        classifier_template = _CLASSIFIER_PROMPT_TEMPLATE

    # Fast-path for URLs: if the message contains a URL, ensure 'fetch' is an option
    # so the agent has the web browsing tools available.
    url_pattern = re.compile(r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+")
    has_url = bool(url_pattern.search(user_message))

    # Build recent context block (last 3 turns = up to 6 messages)
    recent_context = ""
    if recent_messages:
        context_lines = []
        for msg in recent_messages[-6:]:
            if msg.role not in ("user", "assistant"):
                continue
            role_label = "User" if msg.role == "user" else "Assistant"
            content_preview = msg.content[:200].replace("\n", " ")
            context_lines.append(f"{role_label}: {content_preview}")
        if context_lines:
            recent_context = (
                "Recent conversation (for context only):\n" + "\n".join(context_lines) + "\n\n"
            )

    prompt = classifier_template.format(
        categories=categories_str,
        user_message=user_message,
        recent_context=recent_context,
    )
    messages = [ChatMessage(role="user", content=prompt)]

    try:
        logger.debug("Intent Classifier FULL PROMPT:\n%s", prompt)
        trace = get_current_trace()
        if trace:
            async with trace.span("llm:classify_intent", kind="generation") as _cls_span:
                _cls_span.set_input({"user_message": user_message[:200]})
                response = await ollama_client.chat_with_tools(messages, tools=None, think=False)
                _cls_span.set_metadata(
                    {
                        "gen_ai.usage.input_tokens": response.input_tokens,
                        "gen_ai.usage.output_tokens": response.output_tokens,
                        "gen_ai.request.model": response.model,
                    }
                )
                raw = response.content.strip().lower()
                _cls_span.set_output({"raw": raw})
        else:
            response = await ollama_client.chat_with_tools(messages, tools=None, think=False)
            raw = response.content.strip().lower()
        logger.debug("Intent Classifier RAW OUTPUT: %r", raw)

        if raw == "none":
            # Fast-path override: even if the LLM says 'none', if there's a URL, we must fetch
            if has_url and "fetch" in TOOL_CATEGORIES:
                logger.info("URL detected but classifier returned 'none', overriding to ['fetch'].")
                return ["fetch"]

            # Sticky fallback: if the user is continuing a tool-heavy conversation
            # (e.g., asking a follow-up about GitHub repos), reuse last categories.
            if sticky_categories:
                logger.info(
                    "Classifier returned 'none', falling back to sticky categories: %s",
                    sticky_categories,
                )
                return sticky_categories
            return ["none"]

        # Parse comma-separated categories, keep only valid ones
        valid = set(TOOL_CATEGORIES.keys())
        categories = [c.strip() for c in raw.split(",") if c.strip() in valid]

        if has_url and "fetch" not in categories:
            logger.info("URL detected in message. Forcing 'fetch' category.")
            categories.append("fetch")

        if not categories:
            logger.warning("Classifier returned no valid categories from: %r, using defaults", raw)
            return DEFAULT_CATEGORIES

        return categories

    except Exception:
        logger.exception("Intent classification failed, using defaults")
        return DEFAULT_CATEGORIES


def select_tools(
    categories: list[str],
    all_tools: dict[str, dict],
    max_tools: int = 8,
) -> list[dict]:
    """Given categories and a map of all available tools (name -> ollama schema), return filtered list.

    Budget is distributed proportionally across categories so every category receives
    representation when multiple categories are requested:

        per_cat = max(1, max_tools // len(categories))

    A single category falls back to the full budget (per_cat == max_tools).
    The result is always capped at max_tools via a final slice.

    Args:
        categories: List of category names from classify_intent.
        all_tools: Dict mapping tool name to its Ollama tool schema dict.
        max_tools: Maximum number of tools to return.

    Returns:
        List of Ollama tool schema dicts, capped at max_tools.
    """
    if not categories:
        return []

    selected: list[dict] = []
    seen: set[str] = set()
    per_cat = max(1, max_tools // len(categories))

    for category in categories:
        tool_names = TOOL_CATEGORIES.get(category, [])
        cat_count = 0
        for name in tool_names:
            if name in seen:
                continue
            if name in all_tools:
                selected.append(all_tools[name])
                seen.add(name)
                cat_count += 1
            if cat_count >= per_cat:
                break

    return selected[:max_tools]


# ---------------------------------------------------------------------------
# Meta-tool: request_more_tools
# ---------------------------------------------------------------------------

REQUEST_MORE_TOOLS_NAME = "request_more_tools"


def build_request_more_tools_schema(available_categories: list[str]) -> dict:
    """Build the Ollama tool schema for the request_more_tools meta-tool.

    The meta-tool is always prepended to the active tool list so the LLM can
    request additional categories when the initial selection is insufficient.
    It is handled inline by execute_tool_loop — it never reaches the skill
    registry or the security policy engine.
    """
    categories_str = ", ".join(sorted(available_categories))
    return {
        "type": "function",
        "function": {
            "name": REQUEST_MORE_TOOLS_NAME,
            "description": (
                "Request additional tool categories when the current tools are insufficient "
                "for the task. Call this before attempting a task if you need tools that are "
                f"not in the current set. Available categories: {categories_str}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            f"Category names to load. Must be chosen from: {categories_str}"
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why these tools are needed",
                    },
                },
                "required": ["categories"],
            },
        },
    }
