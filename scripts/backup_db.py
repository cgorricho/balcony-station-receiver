"""Consistent SQLite backup of the station database (spec 8).

Safe while the receiver is running: it uses SQLite's online backup API, then
checks the copy. Keeps the newest --keep daily copies and deletes older ones.

Standard library only and Python 3.10 compatible, so the VPS's own python3 can
run it from cron:

    python3 scripts/backup_db.py --db data/station.db --dest backups
"""

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"^station-\d{4}-\d{2}-\d{2}\.db$")


def backup(db: Path, dest: Path, keep: int, today: str | None = None) -> Path:
    if not db.is_file():
        raise SystemExit(f"Database not found: {db}")
    dest.mkdir(parents=True, exist_ok=True)
    day = today or datetime.now().strftime("%Y-%m-%d")
    target = dest / f"station-{day}.db"
    partial = dest / f".station-{day}.db.partial"
    partial.unlink(missing_ok=True)

    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(partial)
        try:
            source.backup(copy)
            copy.execute("PRAGMA journal_mode = DELETE")
            result = copy.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copy.close()
    finally:
        source.close()
    if result != "ok":
        partial.unlink(missing_ok=True)
        raise SystemExit(f"Backup failed its integrity check: {result}")
    partial.replace(target)

    # Retention touches only files this script named.
    copies = sorted(p for p in dest.iterdir() if p.is_file() and NAME.match(p.name))
    for old in copies[:-keep]:
        old.unlink()
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "station.db")
    parser.add_argument("--dest", type=Path, default=PROJECT_ROOT / "backups")
    parser.add_argument("--keep", type=int, default=14, help="daily copies to keep (default 14)")
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    target = backup(args.db, args.dest, args.keep)
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    finally:
        conn.close()
    print(f"Backed up {rows} readings to {target}")


if __name__ == "__main__":
    sys.exit(main())
