"""In-memory cache for active prompt versions.

Allows prompt versioning without changing the system prompt on every request.
The cache is invalidated when a new version is activated via /approve-prompt.

Fallback chain:
  1. In-memory cache (fastest path)
  2. DB active version
  3. prompt_registry.PROMPT_DEFAULTS (hardcoded catalog)
  4. Explicit `default` param (for backward compat)
  5. Raise ValueError if nothing found

Usage:
    base_prompt = await get_active_prompt("system_prompt", repository)
    invalidate_prompt_cache("system_prompt")  # after activating a new version
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# module-level cache: prompt_name → active content
_active_prompts: dict[str, str] = {}


def _try_langfuse_prompt(name: str) -> str | None:
    """Try to fetch a prompt from Langfuse Prompt Management. Returns None on failure."""
    try:
        from app.config import Settings

        settings = Settings()  # type: ignore[call-arg]
        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            return None
        from langfuse import Langfuse

        langfuse = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        prompt = langfuse.get_prompt(name, label="production")
        compiled = prompt.compile()
        if compiled:
            logger.debug("Loaded prompt '%s' from Langfuse", name)
            return compiled
    except Exception:
        logger.debug("Langfuse prompt fallback failed for '%s'", name)
    return None


async def get_active_prompt(
    prompt_name: str,
    repository: object,
    default: str | None = None,
) -> str:
    """Return the active prompt content, with in-memory cache.

    Fallback order:
    1. Cache
    2. DB active version
    3. Langfuse prompt management (label="production")
    4. prompt_registry default
    5. Explicit `default` param
    """
    if prompt_name not in _active_prompts:
        try:
            row = await repository.get_active_prompt_version(prompt_name)  # type: ignore[attr-defined]
            if row:
                _active_prompts[prompt_name] = row["content"]
            else:
                # Try Langfuse prompt management (sync call → offload to thread)
                langfuse_content = await asyncio.to_thread(_try_langfuse_prompt, prompt_name)
                if langfuse_content:
                    _active_prompts[prompt_name] = langfuse_content
                else:
                    # Fall back to registry, then explicit default
                    from app.eval.prompt_registry import get_default

                    registry_default = get_default(prompt_name)
                    resolved = registry_default or default
                    if resolved is None:
                        raise ValueError(f"No content found for prompt '{prompt_name}'")
                    _active_prompts[prompt_name] = resolved
        except ValueError:
            raise
        except Exception as exc:
            logger.exception("Failed to load active prompt %s from DB, using default", prompt_name)
            from app.eval.prompt_registry import get_default

            registry_default = get_default(prompt_name)
            resolved = registry_default or default
            if resolved is None:
                raise ValueError(f"No content found for prompt '{prompt_name}'") from exc
            _active_prompts[prompt_name] = resolved
    return _active_prompts[prompt_name]


async def activate_with_eval(
    prompt_name: str,
    version: int,
    repository: object,
    ollama_client: object,
    eval_threshold: float = 0.7,
) -> dict:
    """Run eval suite with a candidate prompt version, return results without activating.

    Uses LLM-as-judge with binary yes/no to score the candidate against dataset entries.
    The caller decides whether to activate based on the returned score.

    Returns:
        {
            "passed": bool,      # score >= eval_threshold
            "score": float,      # fraction of entries passed (0.0–1.0)
            "details": str,      # human-readable summary
            "activated": bool,   # always False — human must confirm
            "entries_evaluated": int,
        }
        or {"error": str} if the candidate prompt is not found.
    """
    # Get candidate content
    row = await repository.get_prompt_version(prompt_name, version)  # type: ignore[attr-defined]
    if not row:
        return {"error": f"Version {version} of '{prompt_name}' not found"}

    candidate_content = row["content"]

    # Fetch eval dataset entries that have expected_output
    try:
        entries = await repository.get_dataset_entries(entry_type=None, limit=20)  # type: ignore[attr-defined]
    except Exception:
        logger.exception("activate_with_eval: failed to fetch dataset entries")
        return {
            "passed": False,
            "score": 0.0,
            "details": "Could not fetch eval dataset",
            "activated": False,
            "entries_evaluated": 0,
        }

    entries_with_expected = [e for e in entries if e.get("expected_output")]
    if not entries_with_expected:
        return {
            "passed": False,
            "score": 0.0,
            "details": "No dataset entries with expected_output found. Add correction pairs first.",
            "activated": False,
            "entries_evaluated": 0,
        }

    from app.models import ChatMessage

    correct = 0
    total = 0
    for entry in entries_with_expected[:10]:  # cap at 10 for speed
        try:
            # Generate response with candidate as system prompt
            messages = [
                ChatMessage(role="system", content=candidate_content),
                ChatMessage(role="user", content=entry["input_text"]),
            ]
            resp = await ollama_client.chat(messages, think=False)  # type: ignore[attr-defined]
            actual = str(resp).strip()
            expected = entry["expected_output"]

            # LLM-as-judge — binary yes/no
            judge_prompt = (
                f"Question: {entry['input_text'][:300]}\n"
                f"Expected answer: {expected[:300]}\n"
                f"Actual answer: {actual[:300]}\n\n"
                "Does the actual answer correctly answer the question? "
                "Reply ONLY 'yes' or 'no'."
            )
            judge_resp = await ollama_client.chat(  # type: ignore[attr-defined]
                [ChatMessage(role="user", content=judge_prompt)],
                think=False,
            )
            if str(judge_resp).strip().lower().startswith("yes"):
                correct += 1
            total += 1
        except Exception:
            logger.exception("activate_with_eval: inference failed for entry %s", entry.get("id"))

    if total == 0:
        return {
            "passed": False,
            "score": 0.0,
            "details": "No entries could be evaluated",
            "activated": False,
            "entries_evaluated": 0,
        }

    score = correct / total
    passed = score >= eval_threshold
    details = f"{correct}/{total} entries passed ({score:.0%}, threshold={eval_threshold:.0%})"
    logger.info(
        "activate_with_eval: '%s' v%d → score=%.2f (%s)",
        prompt_name,
        version,
        score,
        "PASS" if passed else "FAIL",
    )
    return {
        "passed": passed,
        "score": round(score, 3),
        "details": details,
        "activated": False,
        "entries_evaluated": total,
    }


def invalidate_prompt_cache(prompt_name: str | None = None) -> None:
    """Invalidate the cache for a specific prompt, or all prompts."""
    if prompt_name:
        _active_prompts.pop(prompt_name, None)
        logger.debug("Prompt cache invalidated: %s", prompt_name)
    else:
        _active_prompts.clear()
        logger.debug("All prompt caches invalidated")
