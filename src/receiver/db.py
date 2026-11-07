"""SQLite storage. Long format, so new sensors need no schema changes."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from .models import ReadingBatch

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
  node_id      TEXT    NOT NULL,
  seq          INTEGER NOT NULL,
  ts           INTEGER NOT NULL,
  ts_synced    INTEGER NOT NULL,
  received_at  INTEGER NOT NULL,
  fw_version   TEXT,
  PRIMARY KEY (node_id, seq)
);

CREATE TABLE IF NOT EXISTS metrics (
  node_id   TEXT    NOT NULL,
  seq       INTEGER NOT NULL,
  name      TEXT    NOT NULL,
  value_num REAL,
  value_txt TEXT,
  PRIMARY KEY (node_id, seq, name),
  FOREIGN KEY (node_id, seq) REFERENCES readings(node_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    # Autocommit mode: transactions are opened explicitly in store_batch.
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def health_info(db_path: Path) -> tuple[int | None, list[str]]:
    """Return (last received_at, node ids) across all stored readings."""
    with closing(connect(db_path)) as conn:
        last_reading_at = conn.execute("SELECT MAX(received_at) FROM readings").fetchone()[0]
        nodes = conn.execute("SELECT DISTINCT node_id FROM readings ORDER BY node_id").fetchall()
    return last_reading_at, [node_id for (node_id,) in nodes]


def latest_reading(db_path: Path, node_id: str) -> dict | None:
    """Return the node's highest-seq reading, or None if it has no readings.

    Uses seq rather than ts because the node's clock may be unsynced.
    """
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT seq, ts, ts_synced, received_at, fw_version FROM readings "
            "WHERE node_id = ? ORDER BY seq DESC LIMIT 1",
            (node_id,),
        ).fetchone()
        if row is None:
            return None
        seq, ts, ts_synced, received_at, fw_version = row
        metrics = conn.execute(
            "SELECT name, value_num, value_txt FROM metrics "
            "WHERE node_id = ? AND seq = ? ORDER BY name",
            (node_id, seq),
        ).fetchall()
    return {
        "node_id": node_id,
        "seq": seq,
        "ts": ts,
        "ts_synced": bool(ts_synced),
        "received_at": received_at,
        "fw_version": fw_version,
        "metrics": {
            name: value_txt if value_txt is not None else value_num
            for name, value_num, value_txt in metrics
        },
    }


def fetch_readings(
    db_path: Path,
    node_id: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[dict]:
    """Return readings with their metrics, ordered by node and time.

    start_ts is inclusive and end_ts is exclusive.
    """
    query = (
        "SELECT r.node_id, r.seq, r.ts, r.ts_synced, r.received_at, r.fw_version, "
        "m.name, m.value_num, m.value_txt "
        "FROM readings r LEFT JOIN metrics m ON m.node_id = r.node_id AND m.seq = r.seq "
        "WHERE (:node IS NULL OR r.node_id = :node) "
        "AND (:start IS NULL OR r.ts >= :start) "
        "AND (:end IS NULL OR r.ts < :end) "
        "ORDER BY r.node_id, r.ts, r.seq, m.name"
    )
    params = {"node": node_id, "start": start_ts, "end": end_ts}
    readings: dict[tuple[str, int], dict] = {}
    with closing(connect(db_path)) as conn:
        for row in conn.execute(query, params):
            node, seq, ts, ts_synced, received_at, fw_version, name, value_num, value_txt = row
            reading = readings.setdefault(
                (node, seq),
                {
                    "node_id": node,
                    "seq": seq,
                    "ts": ts,
                    "ts_synced": bool(ts_synced),
                    "received_at": received_at,
                    "fw_version": fw_version,
                    "metrics": {},
                },
            )
            if name is not None:
                reading["metrics"][name] = value_txt if value_txt is not None else value_num
    return list(readings.values())


def compute_ack_seq(conn: sqlite3.Connection, node_id: str, oldest: int) -> int | None:
    """Return the end of the unbroken stored run starting at oldest, or None."""
    rows = conn.execute(
        "SELECT seq FROM readings WHERE node_id = ? AND seq >= ? ORDER BY seq",
        (node_id, oldest),
    )
    ack = None
    expected = oldest
    for (seq,) in rows:
        if seq != expected:
            break
        ack = seq
        expected += 1
    return ack


def store_batch(
    db_path: Path, batch: ReadingBatch, received_at: int
) -> tuple[int, int, int | None]:
    """Store a batch in one transaction. Returns (stored, duplicates, ack_seq)."""
    stored = 0
    with transaction(db_path) as conn:
        for reading in batch.readings:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO readings "
                "(node_id, seq, ts, ts_synced, received_at, fw_version) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    batch.node_id,
                    reading.seq,
                    reading.ts,
                    int(reading.ts_synced),
                    received_at,
                    batch.fw_version,
                ),
            )
            if cursor.rowcount == 0:
                continue
            stored += 1
            conn.executemany(
                "INSERT OR IGNORE INTO metrics (node_id, seq, name, value_num, value_txt) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        batch.node_id,
                        reading.seq,
                        name,
                        None if isinstance(value, str) else value,
                        value if isinstance(value, str) else None,
                    )
                    for name, value in reading.metrics.items()
                ],
            )
        ack_seq = compute_ack_seq(conn, batch.node_id, batch.oldest_buffered_seq)
    return stored, len(batch.readings) - stored, ack_seq
