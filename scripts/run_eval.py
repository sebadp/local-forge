#!/usr/bin/env python
"""Offline eval benchmark — runs regression evals against the eval dataset.

Usage:
    python scripts/run_eval.py [options]

Modes:
    classify    Level 1: test intent classification (fast, ~1-2s per entry)
    tools       Level 2: test classification + tool selection
    e2e         Level 3: full LLM-as-judge end-to-end (default, slow)
    guardrails  Level G: run deterministic guardrail checks on responses (no LLM, <1s)
    memory      Level M: test memory retrieval quality (Precision@5 + Recall)
    plan        Level P: test agent plan quality (deterministic + LLM judge)

Options:
    --db PATH           Path to SQLite database (default: data/localforge.db)
    --ollama URL        Ollama base URL (default: from Settings)
    --model MODEL       Ollama model name (default: from Settings)
    --mode MODE         Eval mode: classify | tools | e2e (default: e2e)
    --entry-type TYPE   Filter by entry type: correction | golden | failure | all (default: all)
    --limit N           Max entries to evaluate (default: 100)
    --threshold FLOAT   Accuracy threshold for exit code 0/1 (default: 0.7)
    --tag TAG           Filter by tag (e.g., "section:math", "level:classify")
    --section SECTION   Shorthand for --tag section:SECTION
    --langfuse          Sync results to Langfuse Experiments (requires LANGFUSE_* env vars)
    --run-name NAME     Name for experiment run in Langfuse (default: auto-generated)
    -v, --verbose       Show detailed per-entry output (actual response, judge reasoning, tools, latency)

Exit codes:
    0   accuracy >= threshold
    1   accuracy < threshold (useful for CI)
    2   no evaluatable entries found
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path when running from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import Settings
from app.database.db import init_db
from app.database.repository import Repository
from app.llm.client import OllamaClient
from app.models import ChatMessage
from app.skills.router import TOOL_CATEGORIES, classify_intent, select_tools

_settings = Settings()

# ---------------------------------------------------------------------------
# Judge prompts (QAG multi-criteria)
# ---------------------------------------------------------------------------

_JUDGE_PROMPT = (
    "Evaluate this response. Answer each criterion with YES or NO and a brief reason.\n\n"
    "Input: {input_text}\n"
    "Expected: {expected}\n"
    "Actual: {actual}\n\n"
    "1. CORRECTNESS: Does the actual response answer the question correctly? (YES/NO - reason)\n"
    "2. COMPLETENESS: Does it address the full question without missing key parts? (YES/NO - reason)\n"
    "{tool_section}"
    "\n{verdict_line}"
)

_JUDGE_TOOL_CRITERION = (
    "3. TOOL_USAGE: Did the model call the correct tools? "
    "Expected tools: {expected_tools}. Called tools: {called_tools}. (YES/NO - reason)\n"
)

_JUDGE_VERDICT_2 = "VERDICT: Based on the above, is the response acceptable? Reply PASS or FAIL."
_JUDGE_VERDICT_3 = (
    "VERDICT: Based on the above 3 criteria, is the response acceptable? Reply PASS or FAIL."
)

# Legacy prompt kept for backward compat (used by run_quick_eval skill)
_JUDGE_PROMPT_SIMPLE = (
    "Question: {input_text}\n"
    "Expected answer: {expected}\n"
    "Actual answer: {actual}\n\n"
    "Does the actual answer correctly and completely answer the question? "
    "Reply ONLY 'yes' or 'no'."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_judge_prompt_simple(input_text: str, expected: str, actual: str) -> str:
    """Binary LLM-as-judge prompt — shared with eval_tools.py run_quick_eval."""
    return _JUDGE_PROMPT_SIMPLE.format(
        input_text=input_text[:300], expected=expected[:300], actual=actual[:300]
    )


def _build_eval_tools_map() -> dict[str, dict]:
    """Build minimal tool schemas from TOOL_CATEGORIES for eval (no registry needed)."""
    tools_map: dict[str, dict] = {}
    for tool_names in TOOL_CATEGORIES.values():
        for name in tool_names:
            if name not in tools_map:
                tools_map[name] = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"Tool: {name}",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
    return tools_map


def _parse_metadata(entry: dict) -> dict:
    """Parse metadata JSON from entry, returning empty dict on failure."""
    raw = entry.get("metadata", "{}")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _score_categories(expected: list[str], actual: list[str]) -> float:
    """Recall-based scoring: len(expected & actual) / len(expected)."""
    if not expected:
        return 1.0
    exp_set = set(expected)
    act_set = set(actual)
    # Special case: both are ["none"]
    if exp_set == {"none"} and act_set == {"none"}:
        return 1.0
    if exp_set == {"none"} and act_set != {"none"}:
        return 0.0
    if exp_set != {"none"} and act_set == {"none"}:
        return 0.0
    return len(exp_set & act_set) / len(exp_set)


def _score_tools(expected: list[str], selected_names: list[str]) -> float:
    """Check if at least one expected tool is in the selection."""
    if not expected:
        return 1.0
    exp_set = set(expected)
    sel_set = set(selected_names)
    return len(exp_set & sel_set) / len(exp_set)


# ---------------------------------------------------------------------------
# QAG Judge
# ---------------------------------------------------------------------------

# Match numbered criteria lines: "1. CORRECTNESS: YES - reason" or "1. YES - reason"
# Also accept SI/NO (Spanish) since judge input is often Spanish
_RE_CRITERION = re.compile(r"^\d+\.\s*\w+.*?\b(YES|NO|SI|SÍ)\b", re.IGNORECASE)
_RE_VERDICT = re.compile(r"VERDICT.*?\b(PASS|FAIL)\b", re.IGNORECASE)
# Fallback: scan entire raw text for YES/NO counts
_RE_YES = re.compile(r"\b(YES|SI|SÍ)\b", re.IGNORECASE)
_RE_NO = re.compile(r"\bNO\b", re.IGNORECASE)


def _parse_judge_response(raw: str, has_tools: bool) -> dict:
    """Parse QAG judge output into structured criteria scores.

    Returns dict with keys: criteria (dict), score (float), passed (bool),
    raw_reasoning (str).
    """
    criteria: dict[str, bool] = {}
    reasons: list[str] = []
    expected_criteria = ["correctness", "completeness"]
    if has_tools:
        expected_criteria.append("tool_usage")

    criterion_idx = 0
    for line in raw.strip().splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Try to match a numbered criterion line
        m = _RE_CRITERION.match(line_stripped)
        if m and criterion_idx < len(expected_criteria):
            matched = m.group(1).upper()
            value = matched in ("YES", "SI", "SÍ")
            criteria[expected_criteria[criterion_idx]] = value
            reasons.append(line_stripped)
            criterion_idx += 1
            continue

        # Try to match VERDICT line
        vm = _RE_VERDICT.search(line_stripped)
        if vm:
            reasons.append(line_stripped)
            continue

        # Accumulate other reasoning lines
        if reasons:
            reasons.append(line_stripped)

    # Extract verdict from LLM
    verdict_match = _RE_VERDICT.search(raw)

    # Compute score from criteria if parsed
    if criteria:
        score = sum(1.0 for v in criteria.values() if v) / len(criteria)
    elif verdict_match:
        # No criteria parsed but verdict found — derive score from verdict
        score = 1.0 if verdict_match.group(1).upper() == "PASS" else 0.0
    else:
        # Neither criteria nor verdict parsed — fallback to YES/NO counting
        yes_count = len(_RE_YES.findall(raw))
        no_count = len(_RE_NO.findall(raw))
        total_votes = yes_count + no_count
        score = yes_count / total_votes if total_votes else 0.0

    # Determine passed: prefer verdict, fallback to score
    if verdict_match:
        passed = verdict_match.group(1).upper() == "PASS"
    else:
        passed = score >= 0.5

    return {
        "criteria": criteria,
        "score": score,
        "passed": passed,
        "raw_reasoning": " | ".join(reasons) if reasons else raw[:300],
    }


async def _judge_response(
    client: OllamaClient,
    input_text: str,
    expected: str,
    actual: str,
    meta: dict,
    tool_calls_made: list[str] | None = None,
) -> dict:
    """Run QAG multi-criteria judge and return structured result.

    Returns dict with: criteria, score, passed, raw_reasoning, tokens.
    Uses chat_with_tools(tools=None) to capture ChatResponse with token metrics.
    """
    expected_tools = meta.get("expected_tools", [])
    has_tools = bool(expected_tools)

    # Build tool section conditionally
    tool_section = ""
    if has_tools:
        called_str = ", ".join(tool_calls_made) if tool_calls_made else "(none)"
        expected_str = ", ".join(expected_tools)
        tool_section = _JUDGE_TOOL_CRITERION.format(
            expected_tools=expected_str, called_tools=called_str
        )

    verdict_line = _JUDGE_VERDICT_3 if has_tools else _JUDGE_VERDICT_2

    # Handle empty actual response (model only called tools, no text)
    display_actual = actual if actual else "(no text -- model only called tools)"

    prompt = _JUDGE_PROMPT.format(
        input_text=input_text[:300],
        expected=expected[:300],
        actual=display_actual[:500],
        tool_section=tool_section,
        verdict_line=verdict_line,
    )

    try:
        # Use chat_with_tools(tools=None) to get full ChatResponse with token metrics
        judge_resp = await client.chat_with_tools(
            [ChatMessage(role="user", content=prompt)],
            tools=None,
            think=False,
        )
        raw = judge_resp.content.strip() if judge_resp.content else ""
        result = _parse_judge_response(raw, has_tools)
        result["tokens"] = {
            "input": judge_resp.input_tokens,
            "output": judge_resp.output_tokens,
            "duration_ms": judge_resp.total_duration_ms,
        }
        return result
    except Exception as exc:
        return {
            "criteria": {},
            "score": 0.0,
            "passed": False,
            "raw_reasoning": f"JUDGE ERROR: {exc}",
            "tokens": {},
        }


# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------


def _print_results(results: list[dict], mode: str, verbose: bool = False) -> tuple[float, int, int]:
    """Print results table and return (accuracy, correct, total)."""
    col_w = [8, 12, 8, 12, 52]
    header = (
        f"{'id':<{col_w[0]}} {'section':<{col_w[1]}} {'pass':<{col_w[2]}} "
        f"{'score':<{col_w[3]}} input"
    )
    sep = "-" * (sum(col_w) + 4)
    print(header)
    print(sep)
    for r in results:
        icon = "PASS" if r["passed"] else "FAIL"
        score_str = f"{r.get('score', 0.0):.0%}"
        print(
            f"{r['id']:<{col_w[0]}} {r.get('section', '?'):<{col_w[1]}} {icon:<{col_w[2]}} "
            f"{score_str:<{col_w[3]}} {r['input_preview']!r}"
        )
        # Always show detail line if present
        if r.get("detail"):
            print(f"         {r['detail']}")

        # For e2e mode: always show criteria summary + tools for failed entries,
        # or for all entries when verbose is on
        show_extra = verbose or (mode == "e2e" and not r["passed"])
        if show_extra:
            # Criteria breakdown
            criteria = r.get("criteria")
            if criteria:
                crit_parts = [f"{k}={'YES' if v else 'NO'}" for k, v in criteria.items()]
                print(f"         {' '.join(crit_parts)}")
            # Tool calls
            tools = r.get("tool_calls")
            if tools:
                print(f"         tools_called: {tools}")
            # Actual response (truncated)
            actual = r.get("actual_response")
            if actual:
                print(f"         actual: {actual[:150]!r}")
            # Judge reasoning
            reasoning = r.get("judge_reasoning")
            if reasoning:
                print(f"         judge: {reasoning[:250]}")
        # Always show latency in verbose
        if verbose:
            latency = r.get("latency_ms")
            if latency is not None:
                print(f"         latency: {latency:.0f}ms")
    print()

    correct = sum(1 for r in results if r["passed"])
    total = len(results)
    accuracy = correct / total if total else 0.0
    print(f"Summary ({mode}): {correct}/{total} correct ({accuracy:.1%})")

    # Breakdown by section
    sections = sorted({r.get("section", "?") for r in results})
    if len(sections) > 1:
        for s in sections:
            s_results = [r for r in results if r.get("section") == s]
            s_correct = sum(1 for r in s_results if r["passed"])
            print(f"  section:{s}: {s_correct}/{len(s_results)} ({s_correct / len(s_results):.1%})")

    # Latency summary
    latencies = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    if latencies:
        avg_lat = sum(latencies) / len(latencies)
        max_lat = max(latencies)
        total_lat = sum(latencies)
        print(
            f"\n  Latency: avg={avg_lat:.0f}ms  max={max_lat:.0f}ms  total={total_lat / 1000:.1f}s"
        )

    return accuracy, correct, total


# ---------------------------------------------------------------------------
# Mode runners
# ---------------------------------------------------------------------------


async def _run_classify(entries: list[dict], client: OllamaClient) -> list[dict]:
    """Level 1: test intent classification only."""
    results: list[dict] = []
    for entry in entries:
        meta = _parse_metadata(entry)
        expected_cats = meta.get("expected_categories", [])
        if not expected_cats:
            continue

        t0 = time.monotonic()
        try:
            actual_cats = await classify_intent(entry["input_text"], client)
            elapsed = (time.monotonic() - t0) * 1000
            score = _score_categories(expected_cats, actual_cats)
            passed = score >= 0.5
            results.append(
                {
                    "id": entry["id"],
                    "section": meta.get("section", "?"),
                    "passed": passed,
                    "score": score,
                    "input_preview": entry["input_text"][:50].replace("\n", " "),
                    "detail": f"expected={expected_cats} actual={actual_cats}",
                    "latency_ms": elapsed,
                }
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            results.append(
                {
                    "id": entry["id"],
                    "section": meta.get("section", "?"),
                    "passed": False,
                    "score": 0.0,
                    "input_preview": entry["input_text"][:50].replace("\n", " "),
                    "detail": f"ERROR: {exc}",
                    "latency_ms": elapsed,
                }
            )
    return results


async def _run_tools(entries: list[dict], client: OllamaClient) -> list[dict]:
    """Level 2: test classification + tool selection."""
    eval_tools_map = _build_eval_tools_map()
    results: list[dict] = []
    for entry in entries:
        meta = _parse_metadata(entry)
        expected_cats = meta.get("expected_categories", [])
        expected_tools = meta.get("expected_tools", [])
        if not expected_cats and not expected_tools:
            continue

        t0 = time.monotonic()
        try:
            actual_cats = await classify_intent(entry["input_text"], client)
            cat_score = _score_categories(expected_cats, actual_cats)

            tool_score = 1.0
            selected_names: list[str] = []
            if expected_tools:
                selected = select_tools(actual_cats, eval_tools_map)
                selected_names = [t.get("function", {}).get("name", "") for t in selected]
                tool_score = _score_tools(expected_tools, selected_names)

            elapsed = (time.monotonic() - t0) * 1000
            combined = (cat_score + tool_score) / 2.0 if expected_tools else cat_score
            passed = combined >= 0.5
            results.append(
                {
                    "id": entry["id"],
                    "section": meta.get("section", "?"),
                    "passed": passed,
                    "score": combined,
                    "input_preview": entry["input_text"][:50].replace("\n", " "),
                    "detail": (
                        f"cats: expected={expected_cats} actual={actual_cats} ({cat_score:.0%}) | "
                        f"tools: expected={expected_tools} selected={selected_names} ({tool_score:.0%})"
                    ),
                    "latency_ms": elapsed,
                }
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            results.append(
                {
                    "id": entry["id"],
                    "section": meta.get("section", "?"),
                    "passed": False,
                    "score": 0.0,
                    "input_preview": entry["input_text"][:50].replace("\n", " "),
                    "detail": f"ERROR: {exc}",
                    "latency_ms": elapsed,
                }
            )
    return results


async def _run_guardrails(entries: list[dict], client: OllamaClient) -> list[dict]:
    """Level G: run deterministic guardrail checks on stored responses (no LLM needed)."""
    from app.guardrails.pipeline import run_guardrails

    results: list[dict] = []
    for entry in entries:
        meta = _parse_metadata(entry)
        # Need both input and output to run guardrails
        output = entry.get("output_text") or entry.get("expected_output") or ""
        input_text = entry.get("input_text", "")
        if not output:
            continue

        t0 = time.monotonic()
        try:
            report = await run_guardrails(
                user_text=input_text,
                reply=output,
                tool_calls_used=bool(meta.get("expected_tools")),
            )
            elapsed = (time.monotonic() - t0) * 1000

            # Per-check detail
            check_details = {r.check_name: r.passed for r in report.results}
            failed = [r.check_name for r in report.results if not r.passed]
            score = sum(1.0 for r in report.results if r.passed) / len(report.results) if report.results else 1.0

            results.append(
                {
                    "id": entry["id"],
                    "section": meta.get("section", "?"),
                    "passed": report.passed,
                    "score": score,
                    "input_preview": input_text[:50].replace("\n", " "),
                    "detail": f"checks={check_details}" + (f" FAILED={failed}" if failed else ""),
                    "criteria": check_details,
                    "latency_ms": elapsed,
                }
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            results.append(
                {
                    "id": entry["id"],
                    "section": meta.get("section", "?"),
                    "passed": False,
                    "score": 0.0,
                    "input_preview": input_text[:50].replace("\n", " "),
                    "detail": f"ERROR: {exc}",
                    "latency_ms": elapsed,
                }
            )
    return results


async def _run_memory(entries: list[dict], client: OllamaClient) -> list[dict]:
    """Level M: test memory retrieval quality (Precision@5 + Recall)."""
    results: list[dict] = []
    _settings_local = Settings()

    conn_mem, _ = await init_db(_settings_local.database_path)
    repo_mem = Repository(conn_mem)

    for entry in entries:
        meta = _parse_metadata(entry)
        expected_keywords = meta.get("expected_memory_keywords", [])
        if not expected_keywords:
            continue

        t0 = time.monotonic()
        try:
            # Embed the query and search for similar memories
            emb_result = await client.embed(
                [entry["input_text"][:500]], model=_settings_local.embedding_model
            )
            embedding = emb_result[0] if emb_result else None
            if not embedding:
                results.append({
                    "id": entry["id"],
                    "section": meta.get("section", "?"),
                    "passed": False,
                    "score": 0.0,
                    "input_preview": entry["input_text"][:50].replace("\n", " "),
                    "detail": "ERROR: embedding failed",
                    "latency_ms": (time.monotonic() - t0) * 1000,
                })
                continue

            memories = await repo_mem.search_similar_memories(embedding, top_k=5)
            elapsed = (time.monotonic() - t0) * 1000

            # Precision: how many returned memories contain expected keywords
            relevant_count = 0
            for mem in memories:
                mem_lower = mem.lower()
                if any(kw.lower() in mem_lower for kw in expected_keywords):
                    relevant_count += 1
            precision = relevant_count / len(memories) if memories else 0.0

            # Recall: how many expected keywords were found in any memory
            found_keywords = []
            for kw in expected_keywords:
                kw_lower = kw.lower()
                if any(kw_lower in m.lower() for m in memories):
                    found_keywords.append(kw)
            recall = len(found_keywords) / len(expected_keywords) if expected_keywords else 0.0

            score = (precision + recall) / 2.0
            passed = score >= 0.3  # lenient threshold for new benchmark

            results.append({
                "id": entry["id"],
                "section": meta.get("section", "?"),
                "passed": passed,
                "score": score,
                "input_preview": entry["input_text"][:50].replace("\n", " "),
                "detail": (
                    f"P@5={precision:.0%} R={recall:.0%} "
                    f"found={found_keywords} memories={len(memories)}"
                ),
                "latency_ms": elapsed,
            })
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            results.append({
                "id": entry["id"],
                "section": meta.get("section", "?"),
                "passed": False,
                "score": 0.0,
                "input_preview": entry["input_text"][:50].replace("\n", " "),
                "detail": f"ERROR: {exc}",
                "latency_ms": elapsed,
            })

    await conn_mem.close()
    return results


async def _run_plan(entries: list[dict], client: OllamaClient) -> list[dict]:
    """Level P: test agent plan quality (deterministic + LLM judge)."""
    from app.agent.planner import create_plan

    results: list[dict] = []
    for entry in entries:
        meta = _parse_metadata(entry)
        expected_plan_tasks = meta.get("expected_plan_tasks")
        expected_plan_categories = meta.get("expected_plan_categories", [])
        if expected_plan_tasks is None and not expected_plan_categories:
            continue

        t0 = time.monotonic()
        try:
            plan = await create_plan(entry["input_text"], client)
            elapsed = (time.monotonic() - t0) * 1000

            # Deterministic scoring
            det_score = 0.0
            det_checks: dict[str, bool] = {}

            # Check minimum task count
            if expected_plan_tasks is not None:
                has_enough = len(plan.tasks) >= expected_plan_tasks
                det_checks["min_tasks"] = has_enough
                det_score += 1.0 if has_enough else 0.0

            # Check expected categories in task descriptions/tools
            if expected_plan_categories:
                plan_text = " ".join(
                    f"{t.description} {' '.join(t.tools)}" for t in plan.tasks
                ).lower()
                found_cats = [c for c in expected_plan_categories if c.lower() in plan_text]
                cat_score = len(found_cats) / len(expected_plan_categories)
                det_checks["categories"] = cat_score >= 0.5
                det_score += cat_score

            n_checks = len(det_checks)
            det_avg = det_score / n_checks if n_checks else 0.0

            # LLM judge for coherence/completeness/feasibility
            plan_text_full = "\n".join(
                f"  {t.id}. [{t.worker_type}] {t.description} (tools: {', '.join(t.tools) or 'none'})"
                for t in plan.tasks
            )
            judge_prompt = (
                f"Evaluate this execution plan for the objective.\n\n"
                f"OBJECTIVE: {entry['input_text']}\n\n"
                f"PLAN:\n{plan_text_full}\n\n"
                f"1. COHERENCE: Do the tasks logically follow from the objective? (YES/NO)\n"
                f"2. COMPLETENESS: Does the plan cover the main steps needed? (YES/NO)\n"
                f"3. FEASIBILITY: Are the tasks actionable and realistic? (YES/NO)\n"
                f"VERDICT: Is this an acceptable plan? Reply PASS or FAIL."
            )

            try:
                judge_resp = await client.chat_with_tools(
                    [ChatMessage(role="user", content=judge_prompt)],
                    tools=None,
                    think=False,
                )
                judge_raw = judge_resp.content.strip() if judge_resp.content else ""
                judge_result = _parse_judge_response(judge_raw, has_tools=False)
                llm_score = judge_result["score"]
            except Exception:
                llm_score = 0.5  # neutral on judge failure

            # Combined score: 50% deterministic + 50% LLM
            combined = (det_avg + llm_score) / 2.0
            passed = combined >= 0.4

            results.append({
                "id": entry["id"],
                "section": meta.get("section", "?"),
                "passed": passed,
                "score": combined,
                "input_preview": entry["input_text"][:50].replace("\n", " "),
                "detail": (
                    f"tasks={len(plan.tasks)} det={det_avg:.0%} llm={llm_score:.0%} "
                    f"checks={det_checks}"
                ),
                "criteria": {**det_checks, "llm_quality": llm_score >= 0.5},
                "latency_ms": elapsed,
            })
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            results.append({
                "id": entry["id"],
                "section": meta.get("section", "?"),
                "passed": False,
                "score": 0.0,
                "input_preview": entry["input_text"][:50].replace("\n", " "),
                "detail": f"ERROR: {exc}",
                "latency_ms": elapsed,
            })
    return results


async def _run_e2e(entries: list[dict], client: OllamaClient) -> list[dict]:
    """Level 3: LLM-as-judge end-to-end evaluation with tool support.

    For tool-dependent entries where the model produces tool calls but no text,
    scoring is deterministic (tool match) — no LLM judge needed since tools
    aren't executed in eval mode.
    """
    eval_tools_map = _build_eval_tools_map()
    results: list[dict] = []

    for entry in entries:
        meta = _parse_metadata(entry)
        if not entry.get("expected_output"):
            continue

        entry_id = entry["id"]
        expected_tools = meta.get("expected_tools", [])
        needs_tools = bool(expected_tools)

        t0 = time.monotonic()
        try:
            messages = [ChatMessage(role="user", content=entry["input_text"])]

            # Use chat_with_tools when the entry expects tool usage.
            # Always get ChatResponse (not str) to capture token metrics.
            tool_calls_made: list[str] = []
            model_tokens: dict = {}
            if needs_tools:
                # Select tools based on expected categories (not LLM classification)
                expected_cats = meta.get("expected_categories", [])
                selected = select_tools(
                    expected_cats if expected_cats else ["time", "math", "weather", "search"],
                    eval_tools_map,
                )
                response = await client.chat_with_tools(messages, tools=selected, think=False)
                actual = response.content.strip() if response.content else ""
                if response.tool_calls:
                    tool_calls_made = [
                        tc.get("function", {}).get("name", "") for tc in response.tool_calls
                    ]
            else:
                # Use chat_with_tools(tools=None) to get full ChatResponse with tokens
                response = await client.chat_with_tools(messages, tools=None, think=False)
                actual = response.content.strip() if response.content else ""

            model_tokens = {
                "input": response.input_tokens,
                "output": response.output_tokens,
                "duration_ms": response.total_duration_ms,
            }

            elapsed_total = (time.monotonic() - t0) * 1000

            # --- Scoring strategy ---
            # Tool-only response (called tools, no/minimal text): score deterministically.
            # Since we don't execute tools in eval, the LLM can't produce a text answer
            # for tool-dependent queries. Evaluating text quality is meaningless here —
            # what matters is whether it identified the right tools.
            if needs_tools and tool_calls_made and len(actual) < 20:
                tool_score = _score_tools(expected_tools, tool_calls_made)
                passed = tool_score >= 0.5
                criteria = {"tool_usage": tool_score >= 0.5}
                results.append(
                    {
                        "id": entry_id,
                        "section": meta.get("section", "?"),
                        "passed": passed,
                        "score": tool_score,
                        "input_preview": entry["input_text"][:50].replace("\n", " "),
                        "detail": (
                            f"[tool-only] expected={expected_tools} "
                            f"called={tool_calls_made} match={tool_score:.0%}"
                        ),
                        "criteria": criteria,
                        "actual_response": actual if actual else "(tool calls only)",
                        "tool_calls": tool_calls_made,
                        "latency_ms": elapsed_total,
                        "model_tokens": model_tokens,
                        "judge_tokens": {},
                        "input_text": entry["input_text"],
                        "expected_output": entry.get("expected_output", ""),
                    }
                )
                continue

            # Tool-dependent but model didn't call any tools: deterministic FAIL
            if needs_tools and not tool_calls_made and len(actual) < 20:
                results.append(
                    {
                        "id": entry_id,
                        "section": meta.get("section", "?"),
                        "passed": False,
                        "score": 0.0,
                        "input_preview": entry["input_text"][:50].replace("\n", " "),
                        "detail": (
                            f"[no tools called] expected={expected_tools} actual={actual[:80]!r}"
                        ),
                        "criteria": {"tool_usage": False},
                        "actual_response": actual if actual else "(empty)",
                        "tool_calls": [],
                        "latency_ms": elapsed_total,
                        "model_tokens": model_tokens,
                        "judge_tokens": {},
                        "input_text": entry["input_text"],
                        "expected_output": entry.get("expected_output", ""),
                    }
                )
                continue

            # Text response (chat entries or tool entries that also produced text):
            # use LLM-as-judge
            judge_result = await _judge_response(
                client,
                entry["input_text"],
                entry["expected_output"],
                actual,
                meta,
                tool_calls_made=tool_calls_made if needs_tools else None,
            )

            elapsed_total = (time.monotonic() - t0) * 1000

            results.append(
                {
                    "id": entry_id,
                    "section": meta.get("section", "?"),
                    "passed": judge_result["passed"],
                    "score": judge_result["score"],
                    "input_preview": entry["input_text"][:50].replace("\n", " "),
                    "criteria": judge_result.get("criteria"),
                    "actual_response": actual,
                    "judge_reasoning": judge_result.get("raw_reasoning"),
                    "tool_calls": tool_calls_made if tool_calls_made else None,
                    "latency_ms": elapsed_total,
                    "model_tokens": model_tokens,
                    "judge_tokens": judge_result.get("tokens", {}),
                    "input_text": entry["input_text"],
                    "expected_output": entry.get("expected_output", ""),
                }
            )
        except Exception as exc:
            elapsed_total = (time.monotonic() - t0) * 1000
            print(f"  [ERROR] entry #{entry_id}: {exc}")
            results.append(
                {
                    "id": entry_id,
                    "section": meta.get("section", "?"),
                    "passed": False,
                    "score": 0.0,
                    "input_preview": entry.get("input_text", "")[:50].replace("\n", " "),
                    "detail": f"ERROR: {exc}",
                    "latency_ms": elapsed_total,
                }
            )
    return results


# ---------------------------------------------------------------------------
# Main eval orchestrator
# ---------------------------------------------------------------------------


async def _run_eval(
    db_path: str,
    ollama_url: str,
    model: str,
    mode: str,
    entry_type: str | None,
    limit: int,
    threshold: float,
    tag: str | None = None,
    use_langfuse: bool = False,
    verbose: bool = False,
    run_name: str | None = None,
) -> int:
    """Core evaluation loop. Returns process exit code."""
    conn, _ = await init_db(db_path)
    repository = Repository(conn)

    async with httpx.AsyncClient(timeout=60.0) as http:
        client = OllamaClient(http_client=http, base_url=ollama_url, model=model)

        # Fetch entries
        fetch_type = None if entry_type == "all" else entry_type
        entries = await repository.get_dataset_entries(entry_type=fetch_type, tag=tag, limit=limit)

        # Filter by mode compatibility: entry must have required metadata for the mode
        filtered = []
        for e in entries:
            meta = _parse_metadata(e)
            eval_types = meta.get("eval_types", [])
            # If entry declares eval_types, it must include this mode
            if eval_types and mode not in eval_types:
                continue
            # Classify/tools modes require expected_categories in metadata
            if mode in ("classify", "tools") and not meta.get("expected_categories"):
                continue
            # e2e mode requires expected_output
            if mode == "e2e" and not e.get("expected_output"):
                continue
            # guardrails mode requires some text to check
            if mode == "guardrails" and not (e.get("output_text") or e.get("expected_output")):
                continue
            # memory mode requires expected_memory_keywords
            if mode == "memory" and not meta.get("expected_memory_keywords"):
                continue
            # plan mode requires expected_plan_tasks or expected_plan_categories
            if mode == "plan" and not (meta.get("expected_plan_tasks") is not None or meta.get("expected_plan_categories")):
                continue
            filtered.append(e)

        if not filtered:
            print(
                f"No evaluatable entries found (mode={mode}, entry_type={entry_type or 'all'}, "
                f"tag={tag or 'none'}, limit={limit}).\n"
                f"Run: python scripts/seed_eval_dataset.py --db {db_path}"
            )
            await conn.close()
            return 2

        print(
            f"Evaluating {len(filtered)} entries "
            f"(mode={mode}, model={model}, threshold={threshold:.0%})\n"
        )

        # Dispatch to mode runner
        if mode == "classify":
            results = await _run_classify(filtered, client)
        elif mode == "tools":
            results = await _run_tools(filtered, client)
        elif mode == "guardrails":
            results = await _run_guardrails(filtered, client)
        elif mode == "memory":
            results = await _run_memory(filtered, client)
        elif mode == "plan":
            results = await _run_plan(filtered, client)
        else:  # e2e
            results = await _run_e2e(filtered, client)

    await conn.close()

    if not results:
        print("No entries could be evaluated in this mode.")
        return 2

    # --- Langfuse experiment sync ---
    if use_langfuse and results:
        try:
            from datetime import UTC, datetime

            from langfuse import Langfuse

            # Pass Langfuse config from Settings (which loads .env via pydantic)
            # so the eval script works without exporting env vars manually.
            lf_kwargs: dict = {}
            if _settings.langfuse_public_key:
                lf_kwargs["public_key"] = _settings.langfuse_public_key
            if _settings.langfuse_secret_key:
                lf_kwargs["secret_key"] = _settings.langfuse_secret_key
            if _settings.langfuse_host:
                lf_kwargs["host"] = _settings.langfuse_host
            lf = Langfuse(**lf_kwargs)
            timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            effective_run = run_name or f"{mode}-{model}-{timestamp}"
            dataset_name = f"localforge-eval-{mode}"

            # Ensure dataset exists (idempotent)
            lf.create_dataset(name=dataset_name)

            for r in results:
                # Deterministic trace ID per entry+run for idempotency
                lf_trace_id = lf.create_trace_id(seed=f"eval-{r['id']}-{effective_run}")

                # --- Root span ---
                input_text = r.get("input_text", r.get("input_preview", ""))
                root = lf.start_span(
                    trace_context={"trace_id": lf_trace_id},
                    name=f"eval_{mode}",
                    input=input_text,
                )
                root.update_trace(
                    metadata={
                        "model": model,
                        "mode": mode,
                        "section": r.get("section", "unknown"),
                        "run_name": effective_run,
                    },
                )

                # --- Generation span: model response ---
                model_tok = r.get("model_tokens", {})
                if model_tok:
                    usage = {}
                    if model_tok.get("input"):
                        usage["input"] = model_tok["input"]
                    if model_tok.get("output"):
                        usage["output"] = model_tok["output"]
                    model_gen = root.start_generation(
                        name="model_response",
                        input=input_text,
                        output=r.get("actual_response", ""),
                        model=model,
                        usage_details=usage if usage else None,
                    )
                    if model_tok.get("duration_ms"):
                        model_gen.update(metadata={"duration_ms": model_tok["duration_ms"]})
                    model_gen.end()

                # --- Generation span: judge ---
                judge_tok = r.get("judge_tokens", {})
                if judge_tok:
                    usage = {}
                    if judge_tok.get("input"):
                        usage["input"] = judge_tok["input"]
                    if judge_tok.get("output"):
                        usage["output"] = judge_tok["output"]
                    judge_gen = root.start_generation(
                        name="judge",
                        output=r.get("judge_reasoning", ""),
                        model=model,
                        usage_details=usage if usage else None,
                    )
                    if judge_tok.get("duration_ms"):
                        judge_gen.update(metadata={"duration_ms": judge_tok["duration_ms"]})
                    judge_gen.end()

                # --- Scores per criterion ---
                criteria = r.get("criteria")
                if criteria:
                    for crit_name, crit_val in criteria.items():
                        lf.create_score(
                            trace_id=lf_trace_id,
                            name=crit_name,
                            value=1.0 if crit_val else 0.0,
                        )
                lf.create_score(
                    trace_id=lf_trace_id,
                    name="overall",
                    value=r.get("score", 1.0 if r["passed"] else 0.0),
                )

                root.end()

                # --- Dataset item + experiment run link (best-effort) ---
                try:
                    item = lf.create_dataset_item(
                        dataset_name=dataset_name,
                        input={"text": input_text},
                        expected_output={"text": r.get("expected_output", "")},
                        metadata={
                            "section": r.get("section"),
                            "entry_id": r["id"],
                        },
                    )
                    item.link(
                        trace_or_observation=root,
                        run_name=effective_run,
                    )
                except Exception:
                    pass  # dataset run linking is best-effort

            lf.flush()
            print(
                f"[Langfuse] Synced {len(results)} results "
                f"(run: {effective_run}, dataset: {dataset_name})"
            )
        except Exception as exc:
            print(f"[Langfuse] Failed to sync: {exc}")

    # --- Print results ---
    accuracy, correct, total = _print_results(results, mode, verbose=verbose)

    print()
    if accuracy >= threshold:
        print(f"PASS -- accuracy {accuracy:.1%} >= threshold {threshold:.1%}")
        return 0
    else:
        print(f"FAIL -- accuracy {accuracy:.1%} < threshold {threshold:.1%}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regression eval suite for LocalForge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default="data/localforge.db", help="Path to SQLite database")
    parser.add_argument("--ollama", default=_settings.ollama_base_url, help="Ollama base URL")
    parser.add_argument("--model", default=_settings.ollama_model, help="Ollama model name")
    parser.add_argument(
        "--mode",
        default="e2e",
        choices=["classify", "tools", "e2e", "guardrails", "memory", "plan"],
        help="Eval mode (default: e2e). guardrails=deterministic checks, memory=retrieval quality, plan=agent planning.",
    )
    parser.add_argument(
        "--entry-type",
        default="all",
        choices=["all", "correction", "golden", "failure"],
        help="Filter dataset by entry type",
    )
    parser.add_argument("--limit", type=int, default=100, help="Max entries to evaluate")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Accuracy threshold for exit code 0 (default: 0.7)",
    )
    parser.add_argument("--tag", help="Filter by tag (e.g., section:math, level:classify)")
    parser.add_argument("--section", help="Shorthand for --tag section:SECTION")
    parser.add_argument(
        "--langfuse",
        action="store_true",
        help="Sync results to Langfuse Experiments (requires LANGFUSE_* env vars)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed per-entry output (actual response, judge reasoning, tools, latency)",
    )
    parser.add_argument(
        "--run-name",
        help="Name for this experiment run in Langfuse (default: auto-generated from mode+model+timestamp)",
    )
    args = parser.parse_args()

    # --section is shorthand for --tag section:X
    tag = args.tag
    if args.section:
        tag = f"section:{args.section}"

    exit_code = asyncio.run(
        _run_eval(
            db_path=args.db,
            ollama_url=args.ollama,
            model=args.model,
            mode=args.mode,
            entry_type=args.entry_type if args.entry_type != "all" else None,
            limit=args.limit,
            threshold=args.threshold,
            tag=tag,
            use_langfuse=args.langfuse,
            verbose=args.verbose,
            run_name=args.run_name,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
