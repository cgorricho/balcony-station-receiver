"""Print a per-day table: temperatures, cold hours, DLI, sun window, coverage.

Example:
    uv run python scripts/daily_summary.py --from 2026-01-01
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from receiver import db
from receiver.config import load_settings
from receiver.derived import DaySummary, daily_summaries

HEADERS = [
    "Date",
    "Air min F",
    "Air max F",
    "Tray min F",
    "Tray max F",
    "Hrs <45F",
    "DLI",
    "Sun start",
    "Sun end",
    "Coverage %",
]


def number(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def clock(value: datetime | None) -> str:
    return "-" if value is None else value.strftime("%H:%M")


def to_cells(summary: DaySummary) -> list[str]:
    return [
        summary.day.isoformat(),
        number(summary.air_min_f),
        number(summary.air_max_f),
        number(summary.tray_min_f),
        number(summary.tray_max_f),
        number(summary.hours_below_45f),
        number(summary.dli),
        clock(summary.sun_start),
        clock(summary.sun_end),
        number(summary.coverage_pct),
    ]


def format_table(summaries: list[DaySummary]) -> str:
    rows = [HEADERS] + [to_cells(summary) for summary in summaries]
    widths = [max(len(row[i]) for row in rows) for i in range(len(HEADERS))]
    lines = ["  ".join(cell.rjust(width) for cell, width in zip(row, widths)) for row in rows]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="first local day")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, help="last local day")
    parser.add_argument("--node", help="only this node")
    parser.add_argument("--db", type=Path, help="database file (default: from .env)")
    args = parser.parse_args(argv)

    settings = load_settings()
    db_path = args.db or settings.db_path
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    # Every reading is loaded so intervals crossing the --from boundary still count.
    by_node: dict[str, list[dict]] = {}
    for reading in db.fetch_readings(db_path, node_id=args.node):
        by_node.setdefault(reading["node_id"], []).append(reading)
    if not by_node:
        print("No readings found.")
        return 0

    for node_id, readings in sorted(by_node.items()):
        summaries = [
            summary
            for summary in daily_summaries(
                readings, settings.tz, settings.lux_per_ppfd, settings.sun_threshold_lux
            )
            if (not args.start or summary.day >= args.start)
            and (not args.end or summary.day <= args.end)
        ]
        print(f"Node {node_id}")
        print(format_table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
