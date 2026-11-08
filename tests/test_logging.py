import logging
import os
import socket
import sqlite3
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from receiver.config import Settings
from receiver.logging_setup import BACKUP_COUNT, LOG_FILE, MAX_BYTES
from receiver.main import create_app

KEY = "test-key-that-must-stay-secret"
HEADERS = {"X-Station-Key": KEY}


def batch(seqs=(1, 2)):
    return {
        "node_id": "balcony-01",
        "oldest_buffered_seq": min(seqs),
        "readings": [
            {"seq": s, "ts": 1762538400, "ts_synced": True, "metrics": {"lux": 1.0}} for s in seqs
        ],
    }


@pytest.fixture
def settings(tmp_path):
    return Settings(api_key=KEY, db_path=tmp_path / "station.db", log_dir=tmp_path / "logs")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), raise_server_exceptions=False)


def log_text(settings):
    return (settings.log_dir / LOG_FILE).read_text(encoding="utf-8")


def test_batch_is_logged(client, settings):
    client.post("/api/v1/readings", json=batch(), headers=HEADERS)
    client.post("/api/v1/readings", json=batch(), headers=HEADERS)
    text = log_text(settings)
    assert "node='balcony-01' stored=2 duplicates=0 ack_seq=2 client=testclient" in text
    assert "node='balcony-01' stored=0 duplicates=2 ack_seq=2 client=testclient" in text


def test_rejected_key_is_logged(client, settings):
    client.post("/api/v1/readings", json=batch(), headers={"X-Station-Key": "wrong"})
    assert "status=401 client=testclient path=/api/v1/readings" in log_text(settings)


def test_invalid_body_is_logged(client, settings):
    client.post("/api/v1/readings", json={"node_id": "x"}, headers=HEADERS)
    assert "invalid request: client=testclient path=/api/v1/readings" in log_text(settings)


def post_via(settings, peer, forwarded_for):
    """Send a batch from `peer`, as nginx would, with an X-Forwarded-For header."""
    client = TestClient(create_app(settings), client=(peer, 50000))
    headers = {**HEADERS, "X-Forwarded-For": forwarded_for}
    assert client.post("/api/v1/readings", json=batch(), headers=headers).status_code == 200
    return log_text(settings)


@pytest.mark.parametrize(
    "allow, peer, logged",
    [
        # A trusted proxy, by network or exact address: log the real client.
        ("172.18.0.0/16", "172.18.0.1", "203.0.113.9"),
        ("127.0.0.1", "127.0.0.1", "203.0.113.9"),
        # Anyone else sending the header can't fake their address.
        ("172.18.0.0/16", "198.51.100.7", "198.51.100.7"),
        ("127.0.0.1", "198.51.100.7", "198.51.100.7"),
        # An empty setting trusts no one.
        ("", "127.0.0.1", "127.0.0.1"),
    ],
)
def test_forwarded_client_ip_only_from_trusted_proxies(tmp_path, allow, peer, logged):
    settings = Settings(
        api_key=KEY,
        db_path=tmp_path / "station.db",
        log_dir=tmp_path / "logs",
        forwarded_allow_ips=allow,
    )
    text = post_via(settings, peer, "203.0.113.9")
    assert f"ack_seq=2 client={logged}\n" in text


def test_unhandled_error_is_logged_with_traceback(client, settings):
    conn = sqlite3.connect(settings.db_path)
    conn.execute("DROP TABLE metrics")
    conn.commit()
    conn.close()
    response = client.post("/api/v1/readings", json=batch(), headers=HEADERS)
    assert response.status_code == 500
    text = log_text(settings)
    assert "unhandled error" in text
    assert "Traceback" in text


def test_health_failure_is_logged(client, settings):
    conn = sqlite3.connect(settings.db_path)
    conn.execute("DROP TABLE metrics")
    conn.execute("DROP TABLE readings")
    conn.commit()
    conn.close()
    assert client.get("/api/v1/health").status_code == 503
    assert "health check could not read the database" in log_text(settings)


def test_formatter_redacts_key_and_query_values(client, settings):
    logger = logging.getLogger("receiver")
    logger.info("value %s", KEY)
    logger.info("GET /api/v1/latest?node_id=a&key=some-other-value HTTP/1.1")
    text = log_text(settings)
    assert KEY not in text
    assert "some-other-value" not in text
    assert text.count("[redacted]") == 2


def test_log_rotates_at_five_files_of_one_megabyte(client, settings):
    logger = logging.getLogger("receiver")
    for _ in range(800):
        logger.info("x" * 10_000)
    files = list(settings.log_dir.iterdir())
    assert len(files) == BACKUP_COUNT + 1
    assert all(path.stat().st_size <= MAX_BYTES for path in files)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_real_server_keeps_key_out_of_all_output(tmp_path):
    port = free_port()
    env = {
        **os.environ,
        "STATION_HOST": "127.0.0.1",
        "STATION_PORT": str(port),
        "STATION_API_KEY": KEY,
        "STATION_DB_PATH": str(tmp_path / "station.db"),
        "STATION_LOG_DIR": str(tmp_path / "logs"),
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "receiver"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}/api/v1"
    try:
        for _ in range(100):
            try:
                assert httpx.get(f"{base}/health", timeout=1).status_code == 200
                break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail("server did not start")
        assert httpx.post(f"{base}/readings", json=batch(), headers=HEADERS).status_code == 200
        latest = httpx.get(f"{base}/latest", params={"node_id": "balcony-01"}, headers=HEADERS)
        assert latest.status_code == 200
        # A key in the URL is no longer accepted, so a stray one is just refused.
        assert httpx.get(f"{base}/latest", params={"node_id": "x", "key": KEY}).status_code == 401
    finally:
        server.terminate()
        output, _ = server.communicate(timeout=10)
    text = (tmp_path / "logs" / LOG_FILE).read_text(encoding="utf-8")
    assert "stored=2" in text
    assert KEY not in text
    assert "key=" not in text
    assert KEY.encode() not in output
