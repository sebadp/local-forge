"""ContextBuilder: consolidates multiple context sections into a single structured system prompt.

Uses XML-delimited sections so qwen3.5:9b can navigate the context efficiently.
Replaces the pattern of appending multiple separate system messages.

Usage:
    builder = ContextBuilder(system_prompt)
    builder.add_section("user_memories", memories_text)
    builder.add_section("recent_activity", daily_logs)
    system_msg = builder.build_system_message()
    context = [ChatMessage(role="system", content=system_msg)] + history
"""

from __future__ import annotations


class ContextBuilder:
    """Builds a structured system prompt with XML-delimited sections.

    Each section is wrapped in <tag>...</tag> blocks appended after the base prompt.
    Empty or None sections are skipped automatically.
    """

    def __init__(self, system_prompt: str) -> None:
        self._base_prompt = system_prompt
        self._sections: list[tuple[str, str]] = []  # (tag_name, content)

    def add_section(self, tag: str, content: str | None) -> ContextBuilder:
        """Add a named section. Skipped if content is empty or None."""
        if content:
            self._sections.append((tag, content))
        return self

    def build_system_message(self) -> str:
        """Consolidate into a single system prompt with XML sections."""
        if not self._sections:
            return self._base_prompt
        parts = [self._base_prompt]
        for tag, content in self._sections:
            parts.append(f"\n<{tag}>\n{content}\n</{tag}>")
        return "\n".join(parts)
