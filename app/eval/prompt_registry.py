"""Centralized prompt catalog with default content for all named prompts.

This module is the single source of truth for all prompt defaults.
It is imported by seed_default_prompts() at startup to insert v1 for any
prompt_name not yet in prompt_versions.

Modules continue to hold their own constants as fallbacks, but at runtime
get_active_prompt() will prefer the DB version over the hardcoded one.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Main system prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are a helpful personal assistant on WhatsApp. "
    "Be friendly. Answer in the same language the user writes in. "
    "Adapt your response length to the user's request — be brief for simple questions, "
    "detailed when asked for long or thorough answers. "
    "TOOL AWARENESS: You have REAL web search and page fetching tools. "
    "When you use a tool and receive results, present those results directly to the user. "
    "NEVER say you cannot access the internet or lack web search capabilities. "
    "CRITICAL: When the user provides a URL and you have URL-reading tools available, "
    "ALWAYS use them to fetch the content before responding. "
    "Do NOT assume a page is inaccessible without trying the tool first. "
    "GROUNDING RULE: Never fabricate specific facts (tech stacks, percentages, metrics, "
    "file contents) without reading actual data via tools first. If you only have partial "
    "information (e.g. a directory listing), say what you see and use tools to read key "
    "files (README, config files, package.json, requirements.txt) before making claims. "
    "If a tool call fails, report the error honestly — do not invent the answer. "
    "When asked about current events, recent software versions, or news, "
    "ALWAYS use search or fetch tools. Never answer from memory for time-sensitive topics. "
    "Always provide a natural language response after using tools — never return an empty message."
)

# ---------------------------------------------------------------------------
# Intent classifier (app/skills/router.py)
# Has placeholders: {categories}, {recent_context}, {user_message}
# ---------------------------------------------------------------------------
_CLASSIFIER_PROMPT = (
    "Classify this message into tool categories. "
    'Reply with ONLY category names separated by commas, or "none".\n'
    "Categories: {categories}, none\n\n"
    "Examples:\n"
    '"what time is it" → time\n'
    '"15% of 230" → math\n'
    '"remember that I like coffee" → notes\n'
    '"search for restaurants nearby" → search\n'
    '"show my projects" → projects\n'
    '"tell me a joke" → none\n\n'
    "{recent_context}"
    "Message to classify: {user_message}"
)

# ---------------------------------------------------------------------------
# Conversation summarizer (app/conversation/summarizer.py)
# ---------------------------------------------------------------------------
_SUMMARIZER_PROMPT = (
    "Summarize the following conversation in 2-3 short paragraphs, "
    "capturing the main topics, decisions, and important details. "
    "Write the summary in the same language the conversation is in."
)

# ---------------------------------------------------------------------------
# Memory flush — extract facts/events before compaction
# Has placeholders: {existing_memories}, {conversation}
# ---------------------------------------------------------------------------
_FLUSH_TO_MEMORY_PROMPT = (
    "Review this conversation fragment. Extract ONLY what's worth remembering long-term.\n\n"
    "Existing memories (do NOT repeat these):\n{existing_memories}\n\n"
    "Conversation:\n{conversation}\n\n"
    'Respond in JSON only:\n{{"facts": ["new stable fact 1"], "events": ["notable event 1"]}}\n'
    'If nothing new, respond: {{"facts": [], "events": []}}'
)

# ---------------------------------------------------------------------------
# Memory consolidator (app/memory/consolidator.py)
# Has placeholder: {memories}
# ---------------------------------------------------------------------------
_CONSOLIDATOR_PROMPT = (
    "Review these user memories. Your job:\n"
    "1. Identify duplicates or near-duplicates → keep the better one\n"
    "2. Identify contradictions → keep the most recent one (higher ID = more recent)\n"
    "3. Do NOT remove anything that isn't clearly duplicate or contradicted\n\n"
    "Current memories (oldest first):\n{memories}\n\n"
    'Return JSON: {{"remove_ids": [id1, id2]}}\n'
    'If nothing to remove: {{"remove_ids": []}}'
)

# ---------------------------------------------------------------------------
# Compaction system message (app/formatting/compaction.py)
# ---------------------------------------------------------------------------
_COMPACTION_SYSTEM_PROMPT = (
    "You are a context-compaction agent. "
    "Preserve exact identifiers. Never hallucinate placeholder names."
)

# ---------------------------------------------------------------------------
# Planner prompts (app/agent/planner.py)
# Have various placeholders — stored as templates
# ---------------------------------------------------------------------------
_PLANNER_CREATE_PROMPT = """\
You are a task planner. Your job is to decompose an objective into concrete steps.

OBJECTIVE: {objective}

{context_block}

Create a JSON plan with this exact structure:
{{
  "context_summary": "1-2 sentence summary of what you understand about the task",
  "tasks": [
    {{
      "id": 1,
      "description": "Clear action to take",
      "worker_type": "general",
      "depends_on": []
    }}
  ]
}}

Worker types:
- "reader": reads information (files, messages, logs, traces)
- "analyzer": analyzes data, finds patterns, diagnoses issues
- "coder": reads and modifies source code
- "reporter": synthesizes findings into a report
- "general": does anything (fallback)

Rules:
- Keep tasks small and specific (1-3 tool calls each)
- Use depends_on to express ordering (e.g. analyze after read)
- Maximum 6 tasks per plan
- Output ONLY valid JSON, nothing else
"""

_PLANNER_REPLAN_PROMPT = """\
You are a task planner reviewing results from completed steps.

OBJECTIVE: {objective}

COMPLETED STEPS AND RESULTS:
{completed_steps}

REMAINING STEPS:
{remaining_steps}

Based on the results so far, decide:
1. If the objective is achieved, output: {{"action": "done", "summary": "brief summary of findings"}}
2. If remaining steps are still valid, output: {{"action": "continue"}}
3. If the plan needs adjustment, output a NEW plan:
{{
  "action": "replan",
  "context_summary": "updated understanding",
  "tasks": [... new tasks ...]
}}

Output ONLY valid JSON, nothing else.
"""

_PLANNER_SYNTHESIZE_PROMPT = """\
You are summarizing the results of a completed agent session.

OBJECTIVE: {objective}

CONTEXT: {context_summary}

ALL STEP RESULTS:
{all_results}

Write a concise, actionable summary of what was accomplished and any key findings.
Keep it under 500 words. Use markdown formatting.
"""

# ---------------------------------------------------------------------------
# Public catalog
# ---------------------------------------------------------------------------
PROMPT_DEFAULTS: dict[str, str] = {
    "system_prompt": _SYSTEM_PROMPT,
    "classifier": _CLASSIFIER_PROMPT,
    "summarizer": _SUMMARIZER_PROMPT,
    "flush_to_memory": _FLUSH_TO_MEMORY_PROMPT,
    "consolidator": _CONSOLIDATOR_PROMPT,
    "compaction_system": _COMPACTION_SYSTEM_PROMPT,
    "planner_create": _PLANNER_CREATE_PROMPT,
    "planner_replan": _PLANNER_REPLAN_PROMPT,
    "planner_synthesize": _PLANNER_SYNTHESIZE_PROMPT,
}


def get_default(prompt_name: str) -> str | None:
    """Return the hardcoded default for a named prompt, or None if unknown."""
    return PROMPT_DEFAULTS.get(prompt_name)
