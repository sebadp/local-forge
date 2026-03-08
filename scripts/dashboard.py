#!/usr/bin/env python
"""Offline HTML dashboard for LocalForge metrics.

Generates a self-contained HTML report with summary cards, guardrail pass rates,
failure trend chart (Chart.js), latency percentiles, dataset composition, and
recent failures — all from the local SQLite database without starting FastAPI.

Usage:
    python scripts/dashboard.py [options]

Options:
    --db PATH       Path to SQLite database (default: data/localforge.db)
    --output PATH   Output HTML file (default: reports/dashboard.html)
    --days N        Number of days to cover (default: 30)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path when running from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.db import init_db
from app.database.repository import Repository


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate LocalForge metrics dashboard")
    parser.add_argument("--db", default="data/localforge.db", help="SQLite database path")
    parser.add_argument("--output", default="reports/dashboard.html", help="Output HTML file path")
    parser.add_argument("--days", type=int, default=30, help="Number of days to cover")
    # For CLI compatibility with run_eval.py — unused
    parser.add_argument("--ollama", default="http://localhost:11434", help=argparse.SUPPRESS)
    return parser.parse_args()


async def _fetch_all_data(db_path: str, days: int) -> dict:
    """Fetch all dashboard data from SQLite in one async session."""
    conn, _ = await init_db(db_path)
    repo = Repository(conn)
    try:
        data = {
            "summary": await repo.get_eval_summary(days),
            "trend": await repo.get_failure_trend(days),
            "scores": await repo.get_score_distribution(),
            "dataset": await repo.get_dataset_stats(),
            "latencies": await repo.get_latency_percentiles(None, days),
            "failures": await repo.get_failed_traces(limit=20),
        }
    finally:
        await conn.close()
    return data


def _safe_pct(a: int, b: int) -> float:
    return round(a / b * 100, 1) if b else 0.0


def _render_html(data: dict, days: int, langfuse_host: str | None) -> str:  # noqa: C901
    """Render a self-contained HTML dashboard from the fetched data."""
    summary = data["summary"]
    trend = data["trend"]
    scores = data["scores"]
    dataset = data["dataset"]
    latencies = data["latencies"]
    failures = data["failures"]

    total_traces = summary.get("total_traces", 0)
    completed = summary.get("completed_traces", 0)
    failed_traces = summary.get("failed_traces", 0)
    pass_rate = _safe_pct(completed, total_traces)
    dataset_total = dataset.get("total", 0)

    # ---- Summary cards ----
    cards_html = f"""
    <div class="cards">
        <div class="card">
            <div class="card-value">{total_traces}</div>
            <div class="card-label">Total traces ({days}d)</div>
        </div>
        <div class="card {"card-ok" if pass_rate >= 90 else "card-warn" if pass_rate >= 70 else "card-fail"}">
            <div class="card-value">{pass_rate:.1f}%</div>
            <div class="card-label">Pass rate</div>
        </div>
        <div class="card {"card-fail" if failed_traces > 0 else "card-ok"}">
            <div class="card-value">{failed_traces}</div>
            <div class="card-label">Failed traces</div>
        </div>
        <div class="card">
            <div class="card-value">{dataset_total}</div>
            <div class="card-label">Dataset entries</div>
        </div>
    </div>"""

    # ---- Guardrail pass rates ----
    if scores:
        guardrail_rows = "".join(
            f"<tr><td>{s['check']}</td>"
            f"<td>{_safe_pct(s['count'] - s['failures'], s['count']):.1f}%</td>"
            f"<td>{s['count']}</td>"
            f"<td class='{'fail' if s['failures'] > 0 else ''}'>{s['failures']}</td></tr>"
            for s in sorted(scores, key=lambda x: x["avg_score"])
        )
        guardrails_html = f"""
    <h2>Guardrail Pass Rates</h2>
    <table>
        <thead><tr><th>Check</th><th>Pass Rate</th><th>Total</th><th>Failures</th></tr></thead>
        <tbody>{guardrail_rows}</tbody>
    </table>"""
    else:
        guardrails_html = "<h2>Guardrail Pass Rates</h2><p class='empty'>No score data yet.</p>"

    # ---- Failure trend (Chart.js) ----
    if trend:
        trend_sorted = sorted(trend, key=lambda x: x["day"])
        chart_labels = json.dumps([r["day"] for r in trend_sorted])
        chart_totals = json.dumps([r["total"] for r in trend_sorted])
        chart_failures = json.dumps([r["failed"] for r in trend_sorted])

        trend_table_rows = "".join(
            f"<tr><td>{r['day']}</td><td>{r['total']}</td>"
            f"<td>{r['failed']}</td>"
            f"<td>{_safe_pct(r['failed'], r['total']):.1f}%</td></tr>"
            for r in reversed(trend_sorted)
        )
        trend_html = f"""
    <h2>Failure Trend</h2>
    <canvas id="trendChart" height="80"></canvas>
    <script>
    (function() {{
        var ctx = document.getElementById('trendChart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {chart_labels},
                datasets: [
                    {{
                        label: 'Total traces',
                        data: {chart_totals},
                        borderColor: '#4a90d9',
                        backgroundColor: 'rgba(74,144,217,0.1)',
                        fill: true,
                        tension: 0.3,
                    }},
                    {{
                        label: 'Failures',
                        data: {chart_failures},
                        borderColor: '#e05252',
                        backgroundColor: 'rgba(224,82,82,0.1)',
                        fill: true,
                        tension: 0.3,
                    }}
                ]
            }},
            options: {{
                responsive: true,
                plugins: {{ legend: {{ position: 'top' }} }},
                scales: {{ y: {{ beginAtZero: true }} }}
            }}
        }});
    }})();
    </script>
    <table>
        <thead><tr><th>Day</th><th>Total</th><th>Failures</th><th>Failure %</th></tr></thead>
        <tbody>{trend_table_rows}</tbody>
    </table>"""
    else:
        trend_html = "<h2>Failure Trend</h2><p class='empty'>No trace data yet.</p>"

    # ---- Latency percentiles ----
    if latencies:
        lat_rows = "".join(
            f"<tr><td><code>{s['span']}</code></td>"
            f"<td>{s['p50']:.0f}</td><td>{s['p95']:.0f}</td>"
            f"<td>{s['p99']:.0f}</td><td>{s['max']:.0f}</td><td>{s['n']}</td></tr>"
            for s in latencies
        )
        latency_html = f"""
    <h2>Latency Percentiles (ms, last {days}d)</h2>
    <table>
        <thead>
            <tr><th>Span</th><th>p50</th><th>p95</th><th>p99</th><th>max</th><th>n</th></tr>
        </thead>
        <tbody>{lat_rows}</tbody>
    </table>"""
    else:
        latency_html = f"<h2>Latency Percentiles</h2><p class='empty'>No span data for the last {days} days.</p>"

    # ---- Dataset composition ----
    golden_count = dataset.get("golden", 0)
    failure_count = dataset.get("failure", 0)
    correction_count = dataset.get("correction", 0)
    dataset_html = f"""
    <h2>Dataset Composition</h2>
    <table>
        <thead><tr><th>Type</th><th>Count</th><th>%</th></tr></thead>
        <tbody>
            <tr><td>Golden</td><td>{golden_count}</td><td>{_safe_pct(golden_count, dataset_total):.1f}%</td></tr>
            <tr><td>Failure</td><td>{failure_count}</td><td>{_safe_pct(failure_count, dataset_total):.1f}%</td></tr>
            <tr><td>Correction</td><td>{correction_count}</td><td>{_safe_pct(correction_count, dataset_total):.1f}%</td></tr>
        </tbody>
    </table>"""

    # ---- Recent failures ----
    def _trace_link(trace_id: str) -> str:
        if langfuse_host:
            url = f"{langfuse_host.rstrip('/')}/trace/{trace_id}"
            return f'<a href="{url}" target="_blank">{trace_id[:12]}…</a>'
        return f"{trace_id[:12]}…"

    if failures:
        failure_rows = "".join(
            f"<tr>"
            f"<td>{_trace_link(t['id'])}</td>"
            f"<td>{(t['started_at'] or '')[:16]}</td>"
            f"<td>{t['min_score']:.2f}</td>"
            f"<td>{((t['input_text'] or '')[:100]).replace('<', '&lt;').replace('>', '&gt;')}</td>"
            f"</tr>"
            for t in failures
        )
        failures_html = f"""
    <h2>Recent Failures (min score &lt; 0.5)</h2>
    <table>
        <thead><tr><th>Trace ID</th><th>Time</th><th>Min Score</th><th>Input Preview</th></tr></thead>
        <tbody>{failure_rows}</tbody>
    </table>"""
    else:
        failures_html = "<h2>Recent Failures</h2><p class='empty'>No failures found.</p>"

    # ---- Full HTML ----
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LocalForge Dashboard — last {days} days</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; color: #333; }}
  h1 {{ color: #1a1a2e; }}
  h2 {{ color: #16213e; border-bottom: 2px solid #4a90d9; padding-bottom: 4px; margin-top: 32px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
  .card {{ background: #fff; border-radius: 8px; padding: 20px 28px; min-width: 140px;
           box-shadow: 0 2px 6px rgba(0,0,0,0.08); text-align: center; }}
  .card-value {{ font-size: 2.2rem; font-weight: bold; color: #1a1a2e; }}
  .card-label {{ font-size: 0.85rem; color: #888; margin-top: 4px; }}
  .card-ok .card-value {{ color: #27ae60; }}
  .card-warn .card-value {{ color: #e67e22; }}
  .card-fail .card-value {{ color: #e05252; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff;
           box-shadow: 0 2px 6px rgba(0,0,0,0.08); border-radius: 6px; overflow: hidden; }}
  th {{ background: #16213e; color: #fff; padding: 10px 12px; text-align: left; font-size: 0.9rem; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #eee; font-size: 0.9rem; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f0f4ff; }}
  td.fail {{ color: #e05252; font-weight: bold; }}
  code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 0.85em; }}
  a {{ color: #4a90d9; }}
  .empty {{ color: #999; font-style: italic; }}
  canvas {{ background: #fff; border-radius: 8px; padding: 16px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.08); margin-bottom: 16px; }}
  .footer {{ margin-top: 40px; color: #aaa; font-size: 0.8rem; text-align: center; }}
</style>
</head>
<body>
<h1>LocalForge Dashboard — last {days} days</h1>
{cards_html}
{guardrails_html}
{trend_html}
{latency_html}
{dataset_html}
{failures_html}
<div class="footer">Generated by scripts/dashboard.py · LocalForge</div>
</body>
</html>"""


def main() -> None:
    args = _parse_args()
    data = asyncio.run(_fetch_all_data(args.db, args.days))
    langfuse_host = os.getenv("LANGFUSE_HOST")
    html = _render_html(data, args.days, langfuse_host)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"Dashboard generated: {output_path.resolve()}")


if __name__ == "__main__":
    main()
