#!/usr/bin/env python
"""Baseline performance snapshot for Plan 36 optimization.

Captures current latency metrics from SQLite tracing data before any
Plan 36 changes so we can measure the impact of each optimization.

Usage:
    python scripts/baseline.py [--db PATH] [--days N] [--output PATH]

Output:
    - Formatted report printed to stdout
    - JSON snapshot saved to reports/baseline_plan36_<timestamp>.json
      (load the JSON later to compare against post-optimization)

Metrics captured:
    - End-to-end message latency (p50/p95/p99)
    - Phase AB total (context build + save_message)
    - Phase A: embedding latency (from phase_ab span metadata)
    - Phase B: parallel DB searches latency (from phase_ab span metadata)
    - classify_intent latency
    - tool_loop latency
    - guardrails latency
    - delivery latency
    - Semantic search mode distribution
    - Trace volume (n messages captured)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.db import init_db
from app.database.repository import Repository, _compute_percentiles


# ---------------------------------------------------------------------------
# Extra queries not yet in Repository
# ---------------------------------------------------------------------------

async def _get_phase_ab_sub_timings(conn, days: int) -> dict:
    """Extract embed_ms and searches_ms from phase_ab span metadata."""
    cursor = await conn.execute(
        """
        SELECT
            json_extract(metadata, '$.embed_ms')    AS embed_ms,
            json_extract(metadata, '$.searches_ms') AS searches_ms
        FROM trace_spans
        WHERE name = 'phase_ab'
          AND started_at >= datetime('now', ? || ' days')
          AND json_extract(metadata, '$.embed_ms') IS NOT NULL
        ORDER BY embed_ms ASC
        """,
        (f"-{days}",),
    )
    rows = await cursor.fetchall()
    if not rows:
        return {}

    embed_values   = sorted(r[0] for r in rows if r[0] is not None)
    search_values  = sorted(r[1] for r in rows if r[1] is not None)

    result: dict = {}
    if embed_values:
        result["phase_a_embed"] = _compute_percentiles("phase_a_embed", embed_values)
    if search_values:
        result["phase_b_searches"] = _compute_percentiles("phase_b_searches", search_values)
    return result


async def _get_trace_volume(conn, days: int) -> dict:
    """Count total traces and message types."""
    cursor = await conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN message_type = 'text'  THEN 1 ELSE 0 END) AS text_msgs,
            SUM(CASE WHEN message_type = 'audio' THEN 1 ELSE 0 END) AS audio_msgs,
            SUM(CASE WHEN message_type = 'image' THEN 1 ELSE 0 END) AS image_msgs,
            SUM(CASE WHEN status = 'completed'   THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN status = 'failed'      THEN 1 ELSE 0 END) AS failed
        FROM traces
        WHERE started_at >= datetime('now', ? || ' days')
        """,
        (f"-{days}",),
    )
    row = await cursor.fetchone()
    if not row or not row[0]:
        return {"total": 0}
    return {
        "total":      row[0],
        "text":       row[1] or 0,
        "audio":      row[2] or 0,
        "image":      row[3] or 0,
        "completed":  row[4] or 0,
        "failed":     row[5] or 0,
    }


async def _get_tool_loop_detail(conn, days: int) -> dict:
    """Count traces that used tools (have at least one tool_loop span)."""
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT trace_id) AS with_tools
        FROM trace_spans
        WHERE name = 'tool_loop'
          AND started_at >= datetime('now', ? || ' days')
        """,
        (f"-{days}",),
    )
    row = await cursor.fetchone()
    return {"traces_with_tools": row[0] if row else 0}


# ---------------------------------------------------------------------------
# Main fetch
# ---------------------------------------------------------------------------

async def _fetch_baseline(db_path: str, days: int) -> dict:
    conn, _ = await init_db(db_path)
    repo = Repository(conn)
    try:
        # Core latency percentiles
        e2e        = await repo.get_e2e_latency_percentiles(days=days)
        spans      = await repo.get_latency_percentiles(None, days=days)
        sub_timing = await _get_phase_ab_sub_timings(conn, days)
        search     = await repo.get_search_hit_rate(days=days)
        volume     = await _get_trace_volume(conn, days)
        tool_detail = await _get_tool_loop_detail(conn, days)
    finally:
        await conn.close()

    # Index spans by name for easy lookup
    spans_by_name = {s["span"]: s for s in spans}

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "days":        days,
        "db_path":     db_path,
        "volume":      {**volume, **tool_detail},
        "latency": {
            "end_to_end":       e2e[0]                          if e2e else None,
            "phase_ab":         spans_by_name.get("phase_ab"),
            "phase_a_embed":    sub_timing.get("phase_a_embed"),
            "phase_b_searches": sub_timing.get("phase_b_searches"),
            "classify_intent":  spans_by_name.get("llm:classify_intent"),
            "tool_loop":        spans_by_name.get("tool_loop"),
            "guardrails":       spans_by_name.get("guardrails"),
            "delivery":         spans_by_name.get("delivery"),
        },
        "search_modes": search,
        "all_spans":    spans,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_span(label: str, s: dict | None, indent: str = "  ") -> str:
    if not s:
        return f"{indent}{label}: — (no data)"
    return (
        f"{indent}{label}: "
        f"p50={s['p50']:.0f}ms  p95={s['p95']:.0f}ms  "
        f"p99={s['p99']:.0f}ms  max={s['max']:.0f}ms  (n={s['n']})"
    )


def _print_report(data: dict) -> None:
    lat    = data["latency"]
    vol    = data["volume"]
    search = data["search_modes"]
    days   = data["days"]
    ts     = data["captured_at"][:19].replace("T", " ")

    sep = "─" * 60

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║        BASELINE — Plan 36 Performance Snapshot          ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"  Captured : {ts} UTC")
    print(f"  Window   : last {days} days")
    print(f"  DB       : {data['db_path']}")
    print()

    # Volume
    print(f"  {sep}")
    print("  TRACE VOLUME")
    print(f"  {sep}")
    if vol.get("total", 0) == 0:
        print("  No traces found in this window. Run the app and retry.")
    else:
        print(f"  Total messages      : {vol['total']}")
        print(f"    text              : {vol.get('text', 0)}")
        print(f"    audio             : {vol.get('audio', 0)}")
        print(f"    image             : {vol.get('image', 0)}")
        print(f"  Completed           : {vol.get('completed', 0)}")
        print(f"  Failed              : {vol.get('failed', 0)}")
        print(f"  With tool calls     : {vol.get('traces_with_tools', 0)}")
    print()

    # End-to-end latency
    print(f"  {sep}")
    print("  END-TO-END LATENCY (full message round-trip)")
    print(f"  {sep}")
    print(_fmt_span("end_to_end", lat["end_to_end"]))
    print()

    # Phase breakdown
    print(f"  {sep}")
    print("  PHASE BREAKDOWN (critical path)")
    print(f"  {sep}")
    print(_fmt_span("phase_ab   (total A+B)", lat["phase_ab"]))
    print(_fmt_span("  phase_a  (embed query)", lat["phase_a_embed"]))
    print(_fmt_span("  phase_b  (DB searches)", lat["phase_b_searches"]))
    print(_fmt_span("classify_intent        ", lat["classify_intent"]))
    print(_fmt_span("tool_loop              ", lat["tool_loop"]))
    print(_fmt_span("guardrails             ", lat["guardrails"]))
    print(_fmt_span("delivery               ", lat["delivery"]))
    print()

    # All spans
    if data["all_spans"]:
        print(f"  {sep}")
        print("  ALL SPANS (sorted by frequency)")
        print(f"  {sep}")
        for s in data["all_spans"]:
            print(_fmt_span(f"{s['span']:<30}", s))
        print()

    # Search modes
    print(f"  {sep}")
    print("  SEMANTIC SEARCH MODES")
    print(f"  {sep}")
    if not search:
        total_txt = (
            "  No data yet — search_stats will populate after the next messages are processed.\n"
            "  (Requires tracing_enabled=True and messages processed after baseline deploy.)"
        )
        print(total_txt)
    else:
        total_n = sum(s["n"] for s in search)
        for s in search:
            pct = s["n"] / total_n * 100 if total_n else 0
            print(
                f"  {s['mode']:<22}: {s['n']:>4} requests ({pct:5.1f}%)  "
                f"retrieved={s['avg_retrieved']:.1f}  passed_threshold={s['avg_passed']:.1f}"
            )
    print()

    # Plan 36 targets
    print(f"  {sep}")
    print("  PLAN 36 TARGETS (for comparison after optimization)")
    print(f"  {sep}")
    e2e = lat["end_to_end"]
    if e2e:
        p50_simple = e2e["p50"]
        target_simple = 1500
        target_tools  = 3500
        print(f"  Metric                    Baseline    Target")
        print(f"  end_to_end p50 (all)   : {p50_simple:>8.0f}ms  < {target_simple}ms (simple)")
        if lat["tool_loop"]:
            p50_tools = lat["tool_loop"]["p50"]
            print(f"  tool_loop p50          : {p50_tools:>8.0f}ms  < {target_tools - 1000:.0f}ms")
        if lat["phase_a_embed"]:
            print(f"  phase_a embed p50      : {lat['phase_a_embed']['p50']:>8.0f}ms  (track improvement)")
        if lat["phase_b_searches"]:
            print(f"  phase_b searches p50   : {lat['phase_b_searches']['p50']:>8.0f}ms  (track improvement)")
    else:
        print("  No latency data yet — run more messages then re-run this script.")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture performance baseline before Plan 36 optimizations"
    )
    parser.add_argument("--db",     default="data/localforge.db", help="SQLite database path")
    parser.add_argument("--days",   type=int, default=7,           help="Lookback window in days (default: 7)")
    parser.add_argument("--output", default=None,                  help="Override JSON output path")
    return parser.parse_args()


async def main() -> None:
    args   = _parse_args()
    data   = await _fetch_baseline(args.db, args.days)

    _print_report(data)

    # Save JSON snapshot
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    ts_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else reports_dir / f"baseline_plan36_{ts_slug}.json"
    out_path.write_text(json.dumps(data, indent=2, default=str))
    print(f"  Snapshot saved → {out_path}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
