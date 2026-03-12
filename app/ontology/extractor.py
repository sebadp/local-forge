"""TopicExtractor: extracts topics and relations from text using regex heuristics.

No LLM in hot path — uses pre-compiled regex patterns and name matching.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Regex to find @mentions (person references)
_RE_MENTION = re.compile(r"@(\w+)")

# Simple topic keywords — expandable over time
_TOPIC_PATTERNS = [
    (
        re.compile(r"\b(python|fastapi|django|flask|typescript|javascript|react|vue|node)\b", re.I),
        "programming",
    ),
    (re.compile(r"\b(deploy|deployment|docker|kubernetes|k8s|ci/?cd|pipeline)\b", re.I), "devops"),
    (re.compile(r"\b(bug|error|fix|issue|crash|exception|traceback)\b", re.I), "debugging"),
    (
        re.compile(r"\b(meeting|standup|sprint|task|ticket|jira|deadline)\b", re.I),
        "project_management",
    ),
    (re.compile(r"\b(database|sql|sqlite|postgres|mysql|mongodb|redis)\b", re.I), "databases"),
    (re.compile(r"\b(test|testing|pytest|unittest|coverage|mock)\b", re.I), "testing"),
    (re.compile(r"\b(api|endpoint|rest|graphql|webhook|http|request)\b", re.I), "api_development"),
    (re.compile(r"\b(machine learning|ml|ai|llm|embedding|neural|model)\b", re.I), "ai_ml"),
    (
        re.compile(r"\b(git|commit|branch|merge|pr|pull request|repo|repository)\b", re.I),
        "version_control",
    ),
    (
        re.compile(r"\b(security|auth|token|password|encrypt|ssl|tls|vulnerability)\b", re.I),
        "security",
    ),
]

# Project name references: detect "project X" or "el proyecto X"
_RE_PROJECT_REF = re.compile(r"\b(?:project|proyecto)\s+([A-Z][a-zA-Z0-9_\-]+|\w{3,})", re.I)


def extract_topics(text: str) -> list[str]:
    """Return topic labels found in text."""
    topics = set()
    text_lower = text.lower()
    for pattern, topic in _TOPIC_PATTERNS:
        if pattern.search(text_lower):
            topics.add(topic)
    return sorted(topics)


def extract_mentions(text: str) -> list[str]:
    """Return @mention names found in text."""
    return _RE_MENTION.findall(text)


def extract_project_refs(text: str) -> list[str]:
    """Return project names referenced in text."""
    matches = _RE_PROJECT_REF.findall(text)
    return [m.strip() for m in matches if m.strip()]


def extract_memory_name(content: str, max_len: int = 120) -> str:
    """Generate a display name for a memory from its content."""
    # Use first non-empty line, truncated
    for line in content.split("\n"):
        line = line.strip()
        if line:
            return line[:max_len]
    return content[:max_len]
