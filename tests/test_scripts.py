"""End-to-end: fake node over real HTTP, then the export and summary scripts."""

import csv
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from receiver.config import Settings
from receiver.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KEY = "test-key"
DAY = "2026-01-15"
READINGS = 144
METRICS_PER_READING = 8


def run_script(name, *args):
    env = {**os.environ, "STATION_API_KEY": KEY, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / name), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    return tmp_path_factory.mktemp("station") / "station.db"


@pytest.fixture(scope="module")
def server(db_path):
    app = create_app(Settings(api_key=KEY, db_path=db_path, log_dir=db_path.parent))
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    while not instance.started:
        time.sleep(0.05)
    port = instance.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    instance.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def fake_run(server):
    result = run_script(
        "fake_node.py", "--url", server, "--key", KEY, "--start", DAY, "--gap", 6, "--resend"
    )
    assert result.returncode == 0, result.stderr
    return result


def scalar(db_path, sql):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_fake_node_delivers_everything_once(fake_run, db_path):
    assert f"readings={READINGS}" in fake_run.stdout
    assert f"stored={READINGS}" in fake_run.stdout
    assert "duplicates=6" in fake_run.stdout
    assert "buffered=0" in fake_run.stdout
    assert scalar(db_path, "SELECT COUNT(*) FROM readings") == READINGS
    assert scalar(db_path, "SELECT COUNT(DISTINCT seq) FROM readings") == READINGS
    assert scalar(db_path, "SELECT MIN(seq) FROM readings") == 1
    assert scalar(db_path, "SELECT MAX(seq) FROM readings") == READINGS
    assert scalar(db_path, "SELECT COUNT(*) FROM metrics") == READINGS * METRICS_PER_READING


def test_rerun_continues_seq_instead_of_colliding(server, fake_run):
    result = run_script(
        "fake_node.py", "--url", server, "--key", KEY, "--node", "other", "--hours", 1
    )
    assert "stored=6" in result.stdout
    again = run_script(
        "fake_node.py", "--url", server, "--key", KEY, "--node", "other", "--hours", 1
    )
    assert "stored=6" in again.stdout
    assert "duplicates=0" in again.stdout


def test_export_csv(fake_run, db_path, tmp_path):
    out = tmp_path / "export.csv"
    result = run_script(
        "export_csv.py", "--db", db_path, "--node", "balcony-01", "--out", out, "--from", DAY,
        "--to", DAY,
    )
    assert result.returncode == 0, result.stderr
    with out.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == READINGS
    first = rows[0]
    assert first["time_local"] == f"{DAY}T00:00:00-05:00"
    assert first["power_source"] == "battery"
    for row in rows:
        assert float(row["air_temp_f"]) == pytest.approx(
            float(row["air_temp_c"]) * 9 / 5 + 32, abs=0.01
        )
        assert float(row["tray_temp_f"]) == pytest.approx(
            float(row["tray_temp_c"]) * 9 / 5 + 32, abs=0.01
        )


def test_export_date_filter_excludes_other_days(fake_run, db_path):
    result = run_script("export_csv.py", "--db", db_path, "--node", "balcony-01", "--to", "2026-01-14")
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip().splitlines()) == 1


def test_daily_summary_table_is_correct(fake_run, db_path):
    result = run_script("daily_summary.py", "--db", db_path, "--node", "balcony-01")
    assert result.returncode == 0, result.stderr
    line = next(line for line in result.stdout.splitlines() if line.strip().startswith(DAY))
    cells = re.split(r"\s+", line.strip())
    _, air_min, air_max, tray_min, tray_max, cold, dli, sun_start, sun_end, coverage = cells

    conn = sqlite3.connect(db_path)
    air = [
        v * 9 / 5 + 32
        for (v,) in conn.execute(
            "SELECT value_num FROM metrics WHERE node_id = 'balcony-01' AND name = 'air_temp_c'"
        )
    ]
    conn.close()
    assert float(air_min) == pytest.approx(min(air), abs=0.05)
    assert float(air_max) == pytest.approx(max(air), abs=0.05)
    assert float(tray_min) > float(air_min)
    assert float(tray_max) > float(air_max)
    assert 8 < float(cold) < 12
    # The fake light curve is a half sine: 60,000 lux peak over 12.5 hours.
    assert float(dli) == pytest.approx(2 * 60_000 / 54 * 12.5 * 3600 / 3.14159 / 1e6, rel=0.02)
    assert "07:" in sun_start or "08:" in sun_start
    assert "16:" in sun_end or "17:" in sun_end
    assert float(coverage) == pytest.approx(143 / 144 * 100, abs=0.1)


def test_scripts_report_a_missing_database(tmp_path):
    for script in ("daily_summary.py", "export_csv.py"):
        result = run_script(script, "--db", tmp_path / "missing.db")
        assert result.returncode == 1
        assert "Database not found" in result.stderr
    assert not (tmp_path / "missing.db").exists()


def test_first_seq_skips_the_latest_lookup(server, fake_run, db_path):
    # Over the public address /latest is hidden, so the run must not depend on it.
    result = run_script(
        "fake_node.py", "--url", server + "/", "--key", KEY, "--node", "throwaway",
        "--hours", 1, "--first-seq", 500,
    )
    assert result.returncode == 0, result.stderr
    assert "stored=6" in result.stdout
    seqs = scalar(db_path, "SELECT GROUP_CONCAT(seq) FROM readings WHERE node_id = 'throwaway'")
    assert sorted(map(int, seqs.split(","))) == list(range(500, 506))


@pytest.mark.parametrize("url", ["127.0.0.1:8765", "ftp://example.com"])
def test_fake_node_rejects_a_url_without_http(url):
    result = run_script("fake_node.py", "--url", url, "--key", KEY)
    assert result.returncode == 2
    assert "must start with http:// or https://" in result.stderr


def test_simulator_fills_history_and_injects_each_warning(tmp_path):
    db = tmp_path / "simulator.db"
    result = run_script(
        "simulate.py", "--port", 0, "--tick", 0, "--ticks", 50, "--no-browser", "--db", db,
    )
    assert result.returncode == 0, result.stderr
    assert "frost night" in result.stdout
    assert (
        "Warnings: clock_backwards=1, out_of_range=1, seq_jump=1, unknown_node=1" in result.stdout
    )
    # 3 days of history at 10-minute steps, minus the 3-hour outage, plus 50 live readings.
    readings = scalar(db, "SELECT COUNT(*) FROM readings WHERE node_id = 'balcony-01'")
    assert 3 * 144 - 18 - 2 <= readings - 50 <= 3 * 144 - 18 + 2
    frost = scalar(db, "SELECT MIN(value_num) FROM metrics WHERE name = 'air_temp_c'")
    assert frost < 0


def test_simulator_without_anomalies_has_no_warnings(tmp_path):
    result = run_script(
        "simulate.py", "--port", 0, "--tick", 0, "--ticks", 50, "--no-browser",
        "--no-anomalies", "--db", tmp_path / "simulator.db",
    )
    assert result.returncode == 0, result.stderr
    assert "Warnings: none" in result.stdout


def test_simulator_leaves_the_real_database_alone(tmp_path):
    result = run_script(
        "simulate.py", "--port", 0, "--tick", 0, "--ticks", 1, "--no-browser",
        "--db", tmp_path / "simulator.db",
    )
    assert result.returncode == 0, result.stderr
    assert str(tmp_path) in result.stdout
    assert "data" + os.sep + "station.db" not in result.stdout
