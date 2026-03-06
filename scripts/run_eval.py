#!/usr/bin/env python
"""Offline eval benchmark — runs LLM-as-judge against the eval dataset without starting FastAPI.

Usage:
    python scripts/run_eval.py [options]

Options:
    --db PATH           Path to SQLite database (default: data/localforge.db)
    --ollama URL        Ollama base URL (default: http://localhost:11434)
    --model MODEL       Ollama model (default: qwen3:8b)
    --entry-type TYPE   Filter by entry type: correction | golden | failure | all (default: all)
    --limit N           Max entries to evaluate (default: 20)
    --threshold FLOAT   Accuracy threshold for exit code 0/1 (default: 0.7)

Exit codes:
    0   accuracy >= threshold
    1   accuracy < threshold (useful for CI)
    2   no evaluatable entries found
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path when running from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.database.db import init_db
from app.database.repository import Repository
from app.llm.client import OllamaClient
from app.models import ChatMessage


def _build_judge_prompt(input_text: str, expected: str, actual: str) -> str:
    """Binary LLM-as-judge prompt — shared with eval_tools.py run_quick_eval."""
    return (
        f"Question: {input_text[:300]}\n"
        f"Expected answer: {expected[:300]}\n"
        f"Actual answer: {actual[:300]}\n\n"
        "Does the actual answer correctly and completely answer the question? "
        "Reply ONLY 'yes' or 'no'."
    )


async def _run_eval(
    db_path: str,
    ollama_url: str,
    model: str,
    entry_type: str | None,
    limit: int,
    threshold: float,
) -> int:
    """Core evaluation loop. Returns process exit code."""
    # Init DB (minimal — no sqlite-vec needed for eval)
    conn, _ = await init_db(db_path)
    repository = Repository(conn)

    async with httpx.AsyncClient(timeout=60.0) as http:
        client = OllamaClient(http_client=http, base_url=ollama_url, model=model)

        # Fetch entries that have expected_output (needed for LLM-as-judge)
        fetch_type = None if entry_type == "all" else entry_type
        entries = await repository.get_dataset_entries(entry_type=fetch_type, limit=limit)
        evaluatable = [e for e in entries if e.get("expected_output")]

        if not evaluatable:
            print(
                f"No evaluatable entries found (entry_type={entry_type or 'all'}, "
                f"limit={limit}). Use add_to_dataset() or add_correction_pair() first."
            )
            return 2

        print(f"Evaluating {len(evaluatable)} entries (model={model}, threshold={threshold:.0%})\n")

        results: list[dict] = []
        for entry in evaluatable:
            entry_id = entry["id"]
            try:
                # Step 1: generate model's actual response
                actual_resp = await client.chat(
                    [ChatMessage(role="user", content=entry["input_text"])]
                )
                actual = str(actual_resp).strip() if actual_resp else ""

                # Step 2: LLM-as-judge
                judge_prompt = _build_judge_prompt(
                    entry["input_text"], entry["expected_output"], actual
                )
                judge_resp = await client.chat(
                    [ChatMessage(role="user", content=judge_prompt)],
                    think=False,
                )
                passed = str(judge_resp).strip().lower().startswith("yes")
                results.append(
                    {
                        "id": entry_id,
                        "type": entry["entry_type"],
                        "passed": passed,
                        "input_preview": entry["input_text"][:60].replace("\n", " "),
                    }
                )
            except Exception as exc:
                print(f"  [ERROR] entry #{entry_id}: {exc}")
                results.append(
                    {
                        "id": entry_id,
                        "type": entry.get("entry_type", "?"),
                        "passed": False,
                        "input_preview": entry.get("input_text", "")[:60].replace("\n", " "),
                        "error": str(exc),
                    }
                )

        await conn.close()

    # --- Print results table ---
    col_w = [8, 12, 8, 62]
    header = f"{'entry_id':<{col_w[0]}} {'type':<{col_w[1]}} {'passed':<{col_w[2]}} input (preview)"
    sep = "-" * (sum(col_w) + 3)
    print(header)
    print(sep)
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        print(
            f"{r['id']:<{col_w[0]}} {r['type']:<{col_w[1]}} {icon:<{col_w[2]}} "
            f"{r['input_preview']!r}"
        )

    print()

    # --- Summary ---
    correct = sum(1 for r in results if r["passed"])
    total = len(results)
    accuracy = correct / total if total else 0.0
    print(f"Summary: {correct}/{total} correct ({accuracy:.1%})")

    # Break down by entry_type
    types = sorted({r["type"] for r in results})
    if len(types) > 1:
        for t in types:
            t_results = [r for r in results if r["type"] == t]
            t_correct = sum(1 for r in t_results if r["passed"])
            print(f"  - {t}: {t_correct}/{len(t_results)} ({t_correct / len(t_results):.1%})")

    print()
    if accuracy >= threshold:
        print(f"✅ PASS — accuracy {accuracy:.1%} >= threshold {threshold:.1%}")
        return 0
    else:
        print(f"❌ FAIL — accuracy {accuracy:.1%} < threshold {threshold:.1%}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline LLM-as-judge eval benchmark for LocalForge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default="data/localforge.db", help="Path to SQLite database")
    parser.add_argument("--ollama", default="http://localhost:11434", help="Ollama base URL")
    parser.add_argument("--model", default="qwen3:8b", help="Ollama model name")
    parser.add_argument(
        "--entry-type",
        default="all",
        choices=["all", "correction", "golden", "failure"],
        help="Filter dataset by entry type",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max entries to evaluate")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Accuracy threshold for exit code 0 (default: 0.7)",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(
        _run_eval(
            db_path=args.db,
            ollama_url=args.ollama,
            model=args.model,
            entry_type=args.entry_type if args.entry_type != "all" else None,
            limit=args.limit,
            threshold=args.threshold,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
