import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from receiver.config import Settings
from receiver.main import create_app

KEY = "test-key"
HEADERS = {"X-Station-Key": KEY}
URL = "/api/v1/readings"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "station.db"


@pytest.fixture
def client(db_path):
    app = create_app(Settings(api_key=KEY, db_path=db_path, log_dir=db_path.parent))
    return TestClient(app, raise_server_exceptions=False)


def reading(seq, **metrics):
    return {
        "seq": seq,
        "ts": 1762538400 + seq * 600,
        "ts_synced": True,
        "metrics": metrics or {"air_temp_c": 18.4, "power_source": "battery"},
    }


def batch(seqs, oldest=None, node_id="balcony-01"):
    seqs = list(seqs)
    return {
        "node_id": node_id,
        "fw_version": "0.1.0",
        "oldest_buffered_seq": min(seqs) if oldest is None else oldest,
        "readings": [reading(s) for s in seqs],
    }


def post(client, body):
    return client.post(URL, json=body, headers=HEADERS)


def rows(db_path, sql, *args):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def count(db_path, table="readings"):
    return rows(db_path, f"SELECT COUNT(*) FROM {table}")[0][0]


def test_valid_batch_is_stored(client, db_path):
    response = post(client, batch([1, 2, 3]))
    assert response.status_code == 200
    body = response.json()
    assert (body["stored"], body["duplicates"], body["ack_seq"]) == (3, 0, 3)
    assert isinstance(body["server_ts"], int)
    assert count(db_path) == 3
    assert count(db_path, "metrics") == 6


def test_values_and_received_at_are_stored(client, db_path):
    post(client, batch([7]))
    row = rows(db_path, "SELECT ts, ts_synced, received_at, fw_version FROM readings")[0]
    assert row[0] == 1762538400 + 7 * 600
    assert row[1] == 1
    assert row[2] > 0
    assert row[3] == "0.1.0"
    metrics = {
        name: (num, txt)
        for name, num, txt in rows(db_path, "SELECT name, value_num, value_txt FROM metrics")
    }
    assert metrics == {"air_temp_c": (18.4, None), "power_source": (None, "battery")}


def test_unsynced_reading_is_stored(client, db_path):
    body = batch([1])
    body["readings"][0]["ts_synced"] = False
    assert post(client, body).status_code == 200
    assert rows(db_path, "SELECT ts_synced FROM readings") == [(0,)]


def test_resent_batch_adds_no_rows(client, db_path):
    post(client, batch([1, 2, 3]))
    body = post(client, batch([1, 2, 3])).json()
    assert (body["stored"], body["duplicates"], body["ack_seq"]) == (0, 3, 3)
    assert count(db_path) == 3
    assert count(db_path, "metrics") == 6


def test_duplicate_seq_inside_one_batch(client, db_path):
    body = post(client, batch([1, 1, 2])).json()
    assert (body["stored"], body["duplicates"]) == (2, 1)
    assert count(db_path) == 2


def test_out_of_order_seqs(client):
    body = post(client, batch([3, 1, 2])).json()
    assert (body["stored"], body["ack_seq"]) == (3, 3)


def test_nodes_are_separate(client, db_path):
    post(client, batch([1, 2], node_id="balcony-01"))
    body = post(client, batch([1, 2], node_id="balcony-02")).json()
    assert body["stored"] == 2
    assert count(db_path) == 4


def test_ack_stops_before_gap_then_advances_when_filled(client):
    assert post(client, batch([1, 2, 4, 5])).json()["ack_seq"] == 2
    assert post(client, batch([3])).json()["ack_seq"] == 5


def test_ack_advances_when_oldest_moves_past_gap(client):
    assert post(client, batch([1, 2, 4, 5])).json()["ack_seq"] == 2
    assert post(client, batch([4, 5, 6])).json()["ack_seq"] == 6


def test_ack_is_null_when_oldest_is_not_stored(client):
    assert post(client, batch([5, 6], oldest=3)).json()["ack_seq"] is None


def test_oldest_above_batch_is_rejected(client, db_path):
    assert post(client, batch([3, 4], oldest=4)).status_code == 422
    assert count(db_path) == 0


def test_unknown_metric_names_are_stored(client, db_path):
    body = batch([1])
    body["readings"][0]["metrics"] = {"solution_ec_ms": 1.8, "pump_state": "on", "count": 3}
    assert post(client, body).status_code == 200
    names = {name for (name,) in rows(db_path, "SELECT name FROM metrics")}
    assert names == {"solution_ec_ms", "pump_state", "count"}


def test_batch_of_500_is_accepted(client, db_path):
    assert post(client, batch(range(1, 501))).json()["ack_seq"] == 500
    assert count(db_path) == 500


def test_batch_over_500_is_rejected(client, db_path):
    assert post(client, batch(range(1, 502))).status_code == 422
    assert count(db_path) == 0


@pytest.mark.parametrize("headers", [{}, {"X-Station-Key": "wrong"}])
def test_bad_key_is_401(client, db_path, headers):
    assert client.post(URL, json=batch([1]), headers=headers).status_code == 401
    assert count(db_path) == 0


def test_bad_key_with_bad_body_is_401(client):
    response = client.post(URL, content=b"nope", headers={"X-Station-Key": "wrong"})
    assert response.status_code == 401


def with_changes(**fields):
    return {**batch([1]), **fields}


def with_reading(**fields):
    return {**batch([1]), "readings": [{**reading(1), **fields}]}


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        with_changes(readings=[]),
        with_changes(node_id=""),
        with_changes(node_id="x" * 65),
        with_changes(oldest_buffered_seq=-1),
        {k: v for k, v in batch([1]).items() if k != "oldest_buffered_seq"},
        with_reading(seq="1"),
        with_reading(seq=-1),
        with_reading(seq=2**63),
        with_reading(ts=1.5),
        with_reading(ts_synced="yes"),
        with_reading(metrics=[]),
        with_reading(metrics={"a": None}),
        with_reading(metrics={"a": True}),
        with_reading(metrics={"a": [1]}),
        with_reading(metrics={"a": 2**70}),
        with_reading(metrics={"a": "x" * 65}),
        with_reading(metrics={"": 1}),
    ],
)
def test_malformed_body_is_422(client, db_path, body):
    assert post(client, body).status_code == 422
    assert count(db_path) == 0


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json",
        b'{"node_id":',
        b"\xff\xfe\x00",
        json.dumps(batch([1])).replace("18.4", "NaN").encode(),
        json.dumps(batch([1])).replace("18.4", "1e999").encode(),
        json.dumps(batch([1])).replace("battery", "\\ud800").encode(),
        json.dumps(batch([1])).replace("power_source", "\\ud800").encode(),
        json.dumps(batch([1])).replace("balcony-01", "\\ud800").encode(),
    ],
)
def test_garbage_bytes_are_422_and_server_survives(client, db_path, raw):
    headers = {**HEADERS, "Content-Type": "application/json"}
    assert client.post(URL, content=raw, headers=headers).status_code == 422
    assert count(db_path) == 0
    assert post(client, batch([1])).status_code == 200


def test_failed_batch_leaves_nothing_stored(client, db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON metrics WHEN NEW.seq = 3 "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END"
    )
    conn.commit()
    conn.close()
    assert post(client, batch([1, 2, 3])).status_code == 500
    assert count(db_path) == 0
    assert count(db_path, "metrics") == 0


def test_wal_mode_is_enabled(client, db_path):
    assert rows(db_path, "PRAGMA journal_mode") == [("wal",)]
