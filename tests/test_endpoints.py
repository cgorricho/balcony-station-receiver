import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from receiver.config import Settings
from receiver.main import create_app

KEY = "test-key"
HEADERS = {"X-Station-Key": KEY}
LATEST = "/api/v1/latest"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "station.db"


@pytest.fixture
def client(db_path):
    app = create_app(Settings(api_key=KEY, db_path=db_path, log_dir=db_path.parent))
    return TestClient(app, raise_server_exceptions=False)


def post(client, seqs, node_id="balcony-01", metrics=None):
    response = client.post(
        "/api/v1/readings",
        headers=HEADERS,
        json={
            "node_id": node_id,
            "fw_version": "0.1.0",
            "oldest_buffered_seq": min(seqs),
            "readings": [
                {
                    "seq": seq,
                    "ts": 1762538400 + seq,
                    "ts_synced": True,
                    "metrics": metrics or {"lux": float(seq), "power_source": "battery"},
                }
                for seq in seqs
            ],
        },
    )
    assert response.status_code == 200


def test_time_needs_no_key(client):
    before = int(time.time())
    response = client.get("/api/v1/time")
    assert response.status_code == 200
    assert set(response.json()) == {"server_ts"}
    assert before <= response.json()["server_ts"] <= int(time.time())


def test_health_on_empty_database(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "db": "ok",
        "last_reading_at": None,
        "nodes": [],
    }


def test_health_reports_readings_and_nodes(client):
    before = int(time.time())
    post(client, [1], node_id="balcony-02")
    post(client, [1], node_id="balcony-01")
    body = client.get("/api/v1/health").json()
    assert body["nodes"] == ["balcony-01", "balcony-02"]
    assert before <= body["last_reading_at"] <= int(time.time())


def test_health_reports_database_failure(client, db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE metrics")
    conn.execute("DROP TABLE readings")
    conn.commit()
    conn.close()
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "db": "error",
        "last_reading_at": None,
        "nodes": [],
    }


def test_latest_with_header(client):
    post(client, [1, 2, 3])
    response = client.get(LATEST, params={"node_id": "balcony-01"}, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["seq"] == 3
    assert body["metrics"] == {"lux": 3.0, "power_source": "battery"}
    assert body["ts"] == 1762538403
    assert body["ts_synced"] is True
    assert body["fw_version"] == "0.1.0"


def test_latest_with_query_key(client):
    post(client, [1])
    response = client.get(LATEST, params={"node_id": "balcony-01", "key": KEY})
    assert response.status_code == 200


def test_latest_uses_highest_seq_even_if_sent_out_of_order(client):
    post(client, [5])
    post(client, [4])
    body = client.get(LATEST, params={"node_id": "balcony-01"}, headers=HEADERS).json()
    assert body["seq"] == 5


def test_latest_keeps_nodes_separate(client):
    post(client, [9], node_id="balcony-02")
    post(client, [1], node_id="balcony-01")
    body = client.get(LATEST, params={"node_id": "balcony-01"}, headers=HEADERS).json()
    assert body["seq"] == 1


def test_latest_returns_new_metric_names(client):
    post(client, [1], metrics={"solution_ec_ms": 1.8})
    body = client.get(LATEST, params={"node_id": "balcony-01"}, headers=HEADERS).json()
    assert body["metrics"] == {"solution_ec_ms": 1.8}


@pytest.mark.parametrize(
    "params, headers",
    [
        ({"node_id": "balcony-01"}, {}),
        ({"node_id": "balcony-01", "key": "wrong"}, {}),
        ({"node_id": "balcony-01"}, {"X-Station-Key": "wrong"}),
    ],
)
def test_latest_rejects_bad_key(client, params, headers):
    post(client, [1])
    assert client.get(LATEST, params=params, headers=headers).status_code == 401


def test_latest_unknown_node_is_404(client):
    response = client.get(LATEST, params={"node_id": "nobody"}, headers=HEADERS)
    assert response.status_code == 404


def test_latest_without_node_id_is_422(client):
    assert client.get(LATEST, headers=HEADERS).status_code == 422
