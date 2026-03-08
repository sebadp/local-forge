"""Self-evaluation skill tools.

Allows the agent to inspect its own performance, curate the eval dataset,
and run quick evaluations — all via WhatsApp.

register() receives: registry, repository, ollama_client (optional).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.database.repository import Repository
    from app.llm.client import OllamaClient
    from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_SKILL_NAME = "eval"


def register(
    registry: SkillRegistry,
    repository: Repository,
    ollama_client: OllamaClient | None = None,
) -> None:
    """Register all evaluation tools into the skill registry."""

    # ------------------------------------------------------------------ #
    # Tool implementations (closures over repository + ollama_client)
    # ------------------------------------------------------------------ #

    async def get_eval_summary(days: int = 7) -> str:
        """Return a formatted performance summary for the last N days."""
        try:
            data = await repository.get_eval_summary(days=days)
        except Exception:
            logger.exception("get_eval_summary failed")
            return "Error retrieving eval summary."

        lines = [
            f"*Eval summary — last {days} days*",
            f"Traces: {data['total_traces']} total, "
            f"{data['completed_traces']} completed, {data['failed_traces']} failed",
            "",
        ]
        if data["scores"]:
            lines.append("*Scores by metric:*")
            for s in data["scores"]:
                lines.append(
                    f"- {s['name']} ({s['source']}): "
                    f"avg={s['avg']:.2f}, min={s['min']:.2f}, max={s['max']:.2f} "
                    f"(n={s['count']})"
                )
        else:
            lines.append("No score data yet.")
        return "\n".join(lines)

    async def list_recent_failures(limit: int = 10) -> str:
        """Return recent traces with at least one low score (<0.5)."""
        try:
            traces = await repository.get_failed_traces(limit=limit)
        except Exception:
            logger.exception("list_recent_failures failed")
            return "Error retrieving failure list."

        if not traces:
            return f"No failures found in the last {limit} traces checked."

        lines = [f"*Recent failures ({len(traces)}):*"]
        for t in traces:
            input_preview = (t["input_text"] or "")[:80]
            lines.append(
                f"- `{t['id'][:12]}…` [{t['started_at'][:16]}] "
                f"min_score={t['min_score']:.2f}\n  Input: {input_preview}"
            )
        return "\n".join(lines)

    async def diagnose_trace(trace_id: str) -> str:
        """Deep-dive into a trace: spans, scores, full input/output."""
        try:
            trace = await repository.get_trace_with_spans(trace_id)
        except Exception:
            logger.exception("diagnose_trace failed for %s", trace_id)
            return f"Error retrieving trace {trace_id}."

        if not trace:
            return f"Trace `{trace_id}` not found."

        lines = [
            f"*Trace: {trace_id}*",
            f"Phone: {trace['phone_number']} | Type: {trace['message_type']} | Status: {trace['status']}",
            f"Started: {trace['started_at']} | Completed: {trace.get('completed_at', 'N/A')}",
            "",
            f"*Input:* {(trace['input_text'] or '')[:200]}",
            f"*Output:* {(trace['output_text'] or 'N/A')[:200]}",
        ]

        if trace.get("scores"):
            lines.append("")
            lines.append("*Scores:*")
            for s in trace["scores"]:
                lines.append(
                    f"- {s['name']} ({s['source']}): {s['value']:.2f}"
                    + (f" — {s['comment']}" if s.get("comment") else "")
                )

        if trace.get("spans"):
            lines.append("")
            lines.append("*Spans:*")
            for sp in trace["spans"]:
                latency = f"{sp['latency_ms']:.0f}ms" if sp.get("latency_ms") else "?"
                lines.append(f"- {sp['name']} ({sp['kind']}) [{latency}] {sp['status']}")

        return "\n".join(lines)

    async def propose_correction(trace_id: str, correction: str) -> str:
        """Propose what the agent should have said — saved as a correction pair."""
        try:
            trace = await repository.get_trace_with_spans(trace_id)
            if not trace:
                return f"Trace `{trace_id}` not found."

            await repository.add_dataset_entry(
                trace_id=trace_id,
                entry_type="correction",
                input_text=trace["input_text"] or "",
                output_text=trace["output_text"],
                expected_output=correction,
                metadata={"source": "agent_proposal"},
            )
        except Exception:
            logger.exception("propose_correction failed for %s", trace_id)
            return "Error saving correction."

        return f"Correction pair saved for trace `{trace_id[:12]}…`. Thanks for improving me!"

    async def add_to_dataset(trace_id: str, entry_type: str = "failure") -> str:
        """Manually curate a trace into the eval dataset."""
        valid_types = {"golden", "failure", "correction"}
        if entry_type not in valid_types:
            return f"Invalid entry_type '{entry_type}'. Use: {', '.join(sorted(valid_types))}."

        try:
            trace = await repository.get_trace_with_spans(trace_id)
            if not trace:
                return f"Trace `{trace_id}` not found."

            await repository.add_dataset_entry(
                trace_id=trace_id,
                entry_type=entry_type,
                input_text=trace["input_text"] or "",
                output_text=trace["output_text"],
                metadata={"source": "manual"},
            )
        except Exception:
            logger.exception("add_to_dataset failed for %s", trace_id)
            return "Error adding to dataset."

        return f"Trace `{trace_id[:12]}…` added to dataset as `{entry_type}`."

    async def get_dataset_stats() -> str:
        """Return dataset composition: counts by type and top tags."""
        try:
            stats = await repository.get_dataset_stats()
        except Exception:
            logger.exception("get_dataset_stats failed")
            return "Error retrieving dataset stats."

        lines = [
            "*Dataset stats:*",
            f"Total: {stats['total']} entries",
            f"- Golden: {stats['golden']}",
            f"- Failure: {stats['failure']}",
            f"- Correction: {stats['correction']}",
        ]
        if stats.get("top_tags"):
            lines.append("")
            lines.append("*Top tags:*")
            for tag, count in stats["top_tags"].items():
                lines.append(f"- {tag}: {count}")
        return "\n".join(lines)

    async def run_quick_eval(
        category: str = "all",
        prompt_name: str | None = None,
        prompt_version: int | None = None,
    ) -> str:
        """Run a quick evaluation against the dataset for a category.

        Uses LLM-as-judge with a binary yes/no prompt to assess whether the model's
        response correctly answers the question, compared against expected_output.
        Uses ollama_client.chat() directly (no tool loop) to avoid recursion.

        Args:
            category: Filter dataset entries by type (default: all correction pairs).
            prompt_name: Optional — if provided with prompt_version, evaluates the candidate
                prompt version (used to preview a proposal before activating it).
            prompt_version: Required when prompt_name is set.
        """
        if not ollama_client:
            return "Cannot run eval: Ollama client not available."

        # Resolve optional prompt override
        system_prompt_override: str | None = None
        override_label = ""
        if prompt_name and prompt_version is not None:
            override_row = await repository.get_prompt_version(prompt_name, prompt_version)
            if not override_row:
                return f"No encontré '{prompt_name}' v{prompt_version} en la DB."
            system_prompt_override = override_row["content"]
            override_label = f" [override: {prompt_name} v{prompt_version}]"

        try:
            entries = await repository.get_dataset_entries(
                entry_type="correction" if category != "all" else None,
                limit=5,
            )
        except Exception:
            logger.exception("run_quick_eval failed fetching entries")
            return "Error loading eval dataset."

        if not entries:
            return "No dataset entries found. Build the dataset first with add_to_dataset()."

        from app.models import ChatMessage

        results = []
        for entry in entries:
            if not entry.get("expected_output"):
                continue
            try:
                # Step 1: generate the model's actual response (with optional system prompt)
                if system_prompt_override:
                    inference_messages = [
                        ChatMessage(role="system", content=system_prompt_override),
                        ChatMessage(role="user", content=entry["input_text"]),
                    ]
                else:
                    inference_messages = [ChatMessage(role="user", content=entry["input_text"])]

                resp = await ollama_client.chat(inference_messages, think=False)
                actual = str(resp).strip() if resp else ""
                expected = entry["expected_output"]

                # Step 2: LLM-as-judge — binary yes/no, think=False for determinism
                judge_prompt = (
                    f"Question: {entry['input_text'][:300]}\n"
                    f"Expected answer: {expected[:300]}\n"
                    f"Actual answer: {actual[:300]}\n\n"
                    "Does the actual answer correctly and completely answer the question? "
                    "Reply ONLY 'yes' or 'no'."
                )
                judge_resp = await ollama_client.chat(
                    [ChatMessage(role="user", content=judge_prompt)],
                    think=False,
                )
                passed = str(judge_resp).strip().lower().startswith("yes")
                results.append({"entry_id": entry["id"], "passed": passed})
            except Exception:
                logger.exception("run_quick_eval inference failed for entry %s", entry["id"])

        if not results:
            return "No correction entries with expected_output found. Try add_to_dataset() first."

        correct = sum(1 for r in results if r["passed"])
        lines = [
            f"*Quick eval results* ({len(results)} entries, category={category}{override_label})",
            f"Correct: {correct}/{len(results)} ({correct / len(results):.0%})",
            "",
            "*Per entry:*",
        ]
        for r in results:
            icon = "✅" if r["passed"] else "❌"
            lines.append(f"- entry #{r['entry_id']}: {icon}")
        return "\n".join(lines)

    async def get_latency_stats(span_name: str = "all", days: int = 7) -> str:
        """Return p50/p95/p99 latency stats per pipeline span for the last N days."""
        try:
            target = None if span_name == "all" else span_name
            stats = await repository.get_latency_percentiles(target, days=days)
        except Exception:
            logger.exception("get_latency_stats failed")
            return "Error retrieving latency stats."

        if not stats:
            return (
                f"No latency data found for span='{span_name}' in the last {days} days. "
                "Make sure tracing_enabled=True and some interactions have been processed."
            )

        lines = [f"*Latencias p50/p95/p99 — últimos {days} días*", ""]
        for s in stats:
            lines.append(
                f"- `{s['span']}`: p50={s['p50']:.0f}ms  p95={s['p95']:.0f}ms  "
                f"p99={s['p99']:.0f}ms  max={s['max']:.0f}ms  (n={s['n']})"
            )
        return "\n".join(lines)

    async def get_search_stats(days: int = 7) -> str:
        """Return distribution of semantic search modes (hit vs fallback) for the last N days."""
        try:
            stats = await repository.get_search_hit_rate(days=days)
        except Exception:
            logger.exception("get_search_stats failed")
            return "Error retrieving search stats."

        if not stats:
            return (
                f"No hay datos de búsqueda semántica en los últimos {days} días. "
                "Asegurate de que tracing_enabled=True y que los spans Phase B tengan metadata."
            )

        total = sum(s["n"] for s in stats)
        lines = [f"*Búsqueda semántica — últimos {days} días (n={total})*", ""]
        for s in stats:
            pct = s["n"] / total * 100 if total else 0
            lines.append(
                f"- `{s['mode']}`: {s['n']} requests ({pct:.0f}%)  "
                f"recuperadas={s['avg_retrieved']:.1f}  pasaron_threshold={s['avg_passed']:.1f}"
            )
        return "\n".join(lines)

    async def get_dashboard_stats(days: int = 30) -> str:
        """Return a comprehensive dashboard: failure trend + score distribution by check."""
        try:
            trend = await repository.get_failure_trend(days=days)
            scores = await repository.get_score_distribution()
        except Exception:
            logger.exception("get_dashboard_stats failed")
            return "Error retrieving dashboard stats."

        lines = [f"*Dashboard — últimos {days} días*", ""]

        if trend:
            total = sum(r["total"] for r in trend)
            failed = sum(r["failed"] for r in trend)
            pass_rate = (total - failed) / total * 100 if total > 0 else 0.0
            lines += [
                "*Tendencia general:*",
                f"- Interacciones: {total}",
                f"- Con fallos: {failed} ({100 - pass_rate:.1f}%)",
                f"- Tasa de éxito: {pass_rate:.1f}%",
                "",
                "*Últimos 7 días:*",
            ]
            for r in trend[:7]:
                lines.append(f"  {r['day']}: {r['total']} total, {r['failed']} fallidos")
        else:
            lines.append("Sin datos de trazas aún.")

        if scores:
            lines += ["", "*Scores por check:*"]
            for s in scores:
                lines.append(
                    f"  {s['check']}: avg={s['avg_score']:.2f}, fallos={s['failures']}/{s['count']}"
                )

        return "\n".join(lines)

    async def propose_prompt_change(
        prompt_name: str,
        diagnosis: str,
        proposed_change: str,
    ) -> str:
        """Propose a modification to a system prompt. Saves as draft for human approval."""
        if not ollama_client:
            return "Cannot propose prompt change: Ollama client not available."
        try:
            from app.eval.evolution import propose_prompt_change as _propose

            result = await _propose(
                prompt_name=prompt_name,
                diagnosis=diagnosis,
                proposed_change=proposed_change,
                ollama_client=ollama_client,
                repository=repository,
            )
        except Exception:
            logger.exception("propose_prompt_change failed")
            return "Error generating prompt proposal."

        if "error" in result:
            return result["error"]

        return (
            f"Prompt proposal saved: '{prompt_name}' v{result['version']}.\n"
            f"Review and activate with: /approve-prompt {prompt_name} {result['version']}\n\n"
            f"*Preview (first 200 chars):*\n{result['content'][:200]}…"
        )

    # ------------------------------------------------------------------ #
    # Tool registration
    # ------------------------------------------------------------------ #

    registry.register_tool(
        name="get_eval_summary",
        description="Get summary of agent performance metrics for the last N days",
        parameters={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (default 7)",
                },
            },
        },
        handler=get_eval_summary,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="list_recent_failures",
        description="List recent traces that have low scores or negative user feedback",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of failures to return (default 10)",
                },
            },
        },
        handler=list_recent_failures,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="diagnose_trace",
        description="Deep-dive into a specific trace: spans, scores, full input and output",
        parameters={
            "type": "object",
            "properties": {
                "trace_id": {
                    "type": "string",
                    "description": "The trace ID to inspect (get from list_recent_failures)",
                },
            },
            "required": ["trace_id"],
        },
        handler=diagnose_trace,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="propose_correction",
        description="Propose what the agent should have said for a specific trace",
        parameters={
            "type": "object",
            "properties": {
                "trace_id": {
                    "type": "string",
                    "description": "The trace ID of the problematic interaction",
                },
                "correction": {
                    "type": "string",
                    "description": "The correct response that should have been given",
                },
            },
            "required": ["trace_id", "correction"],
        },
        handler=propose_correction,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="add_to_dataset",
        description="Manually curate a trace into the eval dataset",
        parameters={
            "type": "object",
            "properties": {
                "trace_id": {
                    "type": "string",
                    "description": "The trace ID to add to the dataset",
                },
                "entry_type": {
                    "type": "string",
                    "enum": ["golden", "failure", "correction"],
                    "description": "Type of dataset entry (default: failure)",
                },
            },
            "required": ["trace_id"],
        },
        handler=add_to_dataset,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="get_dataset_stats",
        description="Get dataset composition stats: count of goldens, failures, corrections, and top tags",
        parameters={"type": "object", "properties": {}},
        handler=get_dataset_stats,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="run_quick_eval",
        description="Run a quick evaluation against correction pairs in the dataset to measure response quality. Optionally pass prompt_name + prompt_version to test a candidate prompt before activating it.",
        parameters={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category filter for dataset entries (default: all)",
                },
                "prompt_name": {
                    "type": "string",
                    "description": "Optional: name of the prompt to evaluate (e.g. 'classifier')",
                },
                "prompt_version": {
                    "type": "integer",
                    "description": "Required when prompt_name is set: version number to evaluate",
                },
            },
        },
        handler=run_quick_eval,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="get_latency_stats",
        description="Return p50/p95/p99 latency for each pipeline span (classify_intent, embed, execute_tool_loop, guardrails, etc.)",
        parameters={
            "type": "object",
            "properties": {
                "span_name": {
                    "type": "string",
                    "description": "Span name to filter (default 'all' = all frequent spans)",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (default 7)",
                },
            },
        },
        handler=get_latency_stats,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="get_search_stats",
        description="Return distribution of semantic search modes (semantic vs fallback) to help calibrate memory_similarity_threshold",
        parameters={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (default 7)",
                },
            },
        },
        handler=get_search_stats,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="get_dashboard_stats",
        description="Get a comprehensive performance dashboard: failure trend over time and score distribution per guardrail check",
        parameters={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back for the trend (default 30)",
                },
            },
        },
        handler=get_dashboard_stats,
        skill_name=_SKILL_NAME,
    )

    registry.register_tool(
        name="propose_prompt_change",
        description="Propose a modification to a system prompt based on a diagnosed failure pattern. Saves a draft for human review via /approve-prompt.",
        parameters={
            "type": "object",
            "properties": {
                "prompt_name": {
                    "type": "string",
                    "description": "Name of the prompt to modify (e.g. 'system_prompt')",
                },
                "diagnosis": {
                    "type": "string",
                    "description": "Description of the recurring problem identified",
                },
                "proposed_change": {
                    "type": "string",
                    "description": "Specific change to make to address the problem",
                },
            },
            "required": ["prompt_name", "diagnosis", "proposed_change"],
        },
        handler=propose_prompt_change,
        skill_name=_SKILL_NAME,
    )
