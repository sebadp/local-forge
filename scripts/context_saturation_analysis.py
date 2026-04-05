#!/usr/bin/env python
"""Context saturation analysis — correlates context fill rate with quality metrics.

Queries trace_scores for context_fill_rate and guardrail/judge scores,
groups by fill rate buckets, and identifies the inflection point where
quality degrades.

Usage:
    python scripts/context_saturation_analysis.py [--db PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.db import init_db

_BUCKETS = [
    (0.0, 0.5, "0-50%"),
    (0.5, 0.7, "50-70%"),
    (0.7, 0.8, "70-80%"),
    (0.8, 0.9, "80-90%"),
    (0.9, 1.01, "90-100%"),
]


async def _analyze(db_path: str) -> None:
    conn, _ = await init_db(db_path)

    # Get all traces with context_fill_rate score
    cursor = await conn.execute(
        "SELECT ts.trace_id, ts.value "
        "FROM trace_scores ts WHERE ts.name = 'context_fill_rate'"
    )
    fill_rates = {row[0]: row[1] for row in await cursor.fetchall()}

    if not fill_rates:
        print("No context_fill_rate scores found in trace_scores.")
        print("This metric is recorded during normal message processing.")
        await conn.close()
        return

    # Get guardrail pass rates per trace
    cursor = await conn.execute(
        "SELECT ts.trace_id, ts.name, ts.value "
        "FROM trace_scores ts WHERE ts.name LIKE 'guardrail_%'"
    )
    guardrail_scores: dict[str, list[float]] = {}
    for row in await cursor.fetchall():
        tid = row[0]
        guardrail_scores.setdefault(tid, []).append(row[2])

    # Get judge scores per trace
    cursor = await conn.execute(
        "SELECT ts.trace_id, ts.value "
        "FROM trace_scores ts WHERE ts.name = 'judge_score'"
    )
    judge_scores = {row[0]: row[1] for row in await cursor.fetchall()}

    await conn.close()

    # Bucket analysis
    print(f"\nContext Saturation Analysis ({len(fill_rates)} traces with fill rate)\n")
    print(f"{'Bucket':<12} {'Traces':>8} {'Avg Fill':>10} {'Guard Pass':>12} {'Judge Avg':>12} {'Failures':>10}")
    print("-" * 66)

    prev_quality = None
    inflection_bucket = None

    for lo, hi, label in _BUCKETS:
        bucket_traces = [tid for tid, fr in fill_rates.items() if lo <= fr < hi]
        if not bucket_traces:
            print(f"{label:<12} {'—':>8}")
            continue

        avg_fill = sum(fill_rates[t] for t in bucket_traces) / len(bucket_traces)

        # Guardrail pass rate for this bucket
        guard_rates = []
        for tid in bucket_traces:
            if tid in guardrail_scores:
                scores = guardrail_scores[tid]
                guard_rates.append(sum(scores) / len(scores))
        avg_guard = sum(guard_rates) / len(guard_rates) if guard_rates else None

        # Judge score for this bucket
        judge_vals = [judge_scores[t] for t in bucket_traces if t in judge_scores]
        avg_judge = sum(judge_vals) / len(judge_vals) if judge_vals else None

        # Failure count (guardrail pass < 1.0 or judge < 0.5)
        failures = sum(
            1 for t in bucket_traces
            if (t in guardrail_scores and sum(guardrail_scores[t]) / len(guardrail_scores[t]) < 1.0)
            or (t in judge_scores and judge_scores[t] < 0.5)
        )

        guard_str = f"{avg_guard:.0%}" if avg_guard is not None else "—"
        judge_str = f"{avg_judge:.2f}" if avg_judge is not None else "—"

        print(
            f"{label:<12} {len(bucket_traces):>8} {avg_fill:>10.1%} "
            f"{guard_str:>12} {judge_str:>12} {failures:>10}"
        )

        # Detect inflection
        quality = avg_guard if avg_guard is not None else avg_judge
        if quality is not None and prev_quality is not None:
            drop = prev_quality - quality
            if drop > 0.05 and inflection_bucket is None:
                inflection_bucket = label
        if quality is not None:
            prev_quality = quality

    print()
    if inflection_bucket:
        print(f"⚠️  Quality inflection detected at bucket: {inflection_bucket}")
        print("   Recommendation: Keep context fill rate below the lower bound of this bucket.")
    else:
        print("✅ No significant quality degradation detected across fill rate buckets.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Context saturation analysis.")
    parser.add_argument("--db", default="data/localforge.db", help="Path to SQLite database")
    args = parser.parse_args()
    asyncio.run(_analyze(args.db))


if __name__ == "__main__":
    main()
