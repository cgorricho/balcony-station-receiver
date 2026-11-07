"""Export readings to a wide CSV: one row per reading, one column per metric.

Example:
    uv run python scripts/export_csv.py --from 2026-01-01 --to 2026-01-31 --out jan.csv
"""

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

from receiver import db
from receiver.config import load_settings
from receiver.derived import c_to_f, day_bounds

BASE_COLUMNS = [
    "node_id",
    "seq",
    "ts",
    "time_local",
    "ts_synced",
    "received_at",
    "fw_version",
]


def fahrenheit_name(metric: str) -> str | None:
    return f"{metric[:-2]}_f" if metric.endswith("_temp_c") else None


def metric_columns(readings: list[dict]) -> list[str]:
    columns = []
    for metric in sorted({name for r in readings for name in r["metrics"]}):
        columns.append(metric)
        if fahrenheit := fahrenheit_name(metric):
            columns.append(fahrenheit)
    return columns


def to_row(reading: dict, tz: ZoneInfo) -> dict:
    row = {name: reading[name] for name in BASE_COLUMNS if name != "time_local"}
    row["ts_synced"] = int(reading["ts_synced"])
    row["time_local"] = datetime.fromtimestamp(reading["ts"], tz).isoformat()
    for metric, value in reading["metrics"].items():
        row[metric] = value
        fahrenheit = fahrenheit_name(metric)
        if fahrenheit and isinstance(value, int | float):
            row[fahrenheit] = round(c_to_f(value), 2)
    return row


def write_csv(readings: list[dict], tz: ZoneInfo, stream: TextIO) -> None:
    writer = csv.DictWriter(stream, fieldnames=BASE_COLUMNS + metric_columns(readings))
    writer.writeheader()
    for reading in readings:
        writer.writerow(to_row(reading, tz))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="first local day")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, help="last local day")
    parser.add_argument("--node", help="only this node")
    parser.add_argument("--db", type=Path, help="database file (default: from .env)")
    parser.add_argument("--out", type=Path, help="output file (default: stdout)")
    args = parser.parse_args(argv)

    settings = load_settings()
    db_path = args.db or settings.db_path
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1
    tz = ZoneInfo(settings.tz)
    readings = db.fetch_readings(
        db_path,
        node_id=args.node,
        start_ts=day_bounds(args.start, tz)[0] if args.start else None,
        end_ts=day_bounds(args.end, tz)[1] if args.end else None,
    )
    if args.out:
        with args.out.open("w", newline="", encoding="utf-8") as stream:
            write_csv(readings, tz, stream)
        print(f"Wrote {len(readings)} readings to {args.out}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8", newline="")
        write_csv(readings, tz, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
