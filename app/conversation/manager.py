from __future__ import annotations

import asyncio
from collections import OrderedDict

from app.database.repository import Repository
from app.models import ChatMessage, Note

_CONV_ID_CACHE_MAX = 10_000


class ConversationManager:
    def __init__(self, repository: Repository, max_messages: int = 20):
        self._repo = repository
        self._max_messages = max_messages
        self._conv_id_cache: OrderedDict[str, int] = OrderedDict()

    async def _get_conv_id(self, phone_number: str) -> int:
        """Return conversation ID, creating it if needed. Result is cached with LRU eviction."""
        if phone_number in self._conv_id_cache:
            self._conv_id_cache.move_to_end(phone_number)
            return self._conv_id_cache[phone_number]
        conv_id = await self._repo.get_or_create_conversation(phone_number)
        self._conv_id_cache[phone_number] = conv_id
        if len(self._conv_id_cache) > _CONV_ID_CACHE_MAX:
            self._conv_id_cache.popitem(last=False)
        return conv_id

    async def is_duplicate(self, wa_message_id: str) -> bool:
        return await self._repo.is_duplicate(wa_message_id)

    async def add_message(
        self,
        phone_number: str,
        role: str,
        content: str,
        wa_message_id: str | None = None,
    ) -> None:
        conv_id = await self._get_conv_id(phone_number)
        await self._repo.save_message(conv_id, role, content, wa_message_id)

    async def get_history(self, phone_number: str) -> list[ChatMessage]:
        conv_id = await self._get_conv_id(phone_number)
        return await self._repo.get_recent_messages(conv_id, self._max_messages)

    async def get_windowed_history(
        self,
        phone_number: str,
        verbatim_count: int = 8,
    ) -> tuple[list[ChatMessage], str | None]:
        """Return recent messages verbatim + summary of older ones.

        Returns:
            (recent_messages, summary_of_older)
            - recent_messages: last `verbatim_count` messages verbatim
            - summary_of_older: existing DB summary if history > verbatim_count, else None

        Zero-latency: uses DB summary already computed by maybe_summarize, no new LLM call.
        """
        conv_id = await self._get_conv_id(phone_number)
        # Launch both queries in parallel — summary is cheap (~1ms with index)
        # and discarded if history is short enough
        history, summary = await asyncio.gather(
            self._repo.get_recent_messages(conv_id, self._max_messages),
            self._repo.get_latest_summary(conv_id),
        )

        if len(history) <= verbatim_count:
            return history, None

        return history[-verbatim_count:], summary

    async def get_context(
        self,
        phone_number: str,
        system_prompt: str,
        memories: list[str],
        skills_summary: str | None = None,
        daily_logs: str | None = None,
        relevant_notes: list[Note] | None = None,
    ) -> list[ChatMessage]:
        conv_id = await self._get_conv_id(phone_number)
        summary, history = await asyncio.gather(
            self._repo.get_latest_summary(conv_id),
            self._repo.get_recent_messages(conv_id, self._max_messages),
        )

        context = [ChatMessage(role="system", content=system_prompt)]
        if memories:
            memory_block = "Important user information:\n" + "\n".join(f"- {m}" for m in memories)
            context.append(ChatMessage(role="system", content=memory_block))
        if relevant_notes:
            notes_block = "Relevant notes:\n" + "\n".join(
                f"- [{n.id}] {n.title}: {n.content[:200]}" for n in relevant_notes
            )
            context.append(ChatMessage(role="system", content=notes_block))
        if daily_logs:
            context.append(
                ChatMessage(role="system", content=f"Recent activity log:\n{daily_logs}")
            )
        if skills_summary:
            context.append(ChatMessage(role="system", content=skills_summary))
        if summary:
            context.append(
                ChatMessage(role="system", content=f"Previous conversation summary:\n{summary}")
            )
        context.extend(history)
        return context

    async def clear(self, phone_number: str) -> None:
        conv_id = await self._get_conv_id(phone_number)
        await self._repo.clear_conversation(conv_id)

    async def get_conversation_id(self, phone_number: str) -> int:
        return await self._get_conv_id(phone_number)
