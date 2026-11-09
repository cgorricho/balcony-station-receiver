import time

import pytest
from fastapi.testclient import TestClient

from receiver.config import Settings
from receiver.dashboard import build_data
from receiver.main import create_app

KEY = "test-key"
HEADERS = {"X-Station-Key": KEY}
NOW = int(time.time())
STEP = 600


@pytest.fixture
def settings(tmp_path):
    return Settings(api_key=KEY, db_path=tmp_path / "station.db", log_dir=tmp_path)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), raise_server_exceptions=False)


def post(client, seqs, node_id="balcony-01", ts=None, metrics=None, synced=True):
    readings = [
        {
            "seq": seq,
            "ts": (ts if ts is not None else NOW - 86400 + seq * STEP),
            "ts_synced": synced,
            "metrics": metrics or {
                "air_temp_c": 10.0,
                "tray_temp_c": 12.0,
                "lux": 1000.0,
                "battery_v": 4.7,
                "power_source": "battery",
            },
        }
        for seq in seqs
    ]
    body = {"node_id": node_id, "oldest_buffered_seq": min(seqs), "readings": readings}
    response = client.post("/api/v1/readings", json=body, headers=HEADERS)
    assert response.status_code == 200, response.text


def data(client, **params):
    response = client.get("/dashboard/data", params=params)
    assert response.status_code == 200
    return response.json()


def test_page_and_assets_are_served_with_security_headers(client):
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert '<script src="/dashboard/plotly.min.js">' in page.text
    for path, kind in [("/dashboard/app.js", "javascript"), ("/dashboard/app.css", "css"),
                       ("/dashboard/plotly.min.js", "javascript")]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert kind in response.headers["content-type"]
    assert "plotly.js" in client.get("/dashboard/plotly.min.js").text[:200]


def test_page_has_no_inline_script(client):
    # The CSP allows only scripts from this server, so inline code would break.
    text = client.get("/dashboard").text
    assert "<script>" not in text
    assert "onclick" not in text.lower()


def test_empty_database(client):
    body = data(client)
    assert body["node"] == "balcony-01"
    assert body["nodes"] == ["balcony-01"]
    assert body["latest"] is None
    assert body["series"] == []
    assert body["summary"] == []
    assert body["warnings"] == []


def test_series_latest_and_summary(client):
    post(client, range(1, 13))
    body = data(client)
    names = [s["name"] for s in body["series"]]
    assert names == ["air_temp_c", "tray_temp_c", "lux", "battery_v"]
    air = body["series"][0]
    assert len(air["x"]) == 12
    assert air["y"][0] == 50.0  # 10 °C shown as °F
    assert body["latest"]["seq"] == 12
    assert body["latest"]["metrics"]["power_source"] == "battery"
    assert body["latest"]["age_seconds"] >= 0
    assert body["summary"] and body["summary"][0]["air_min_f"] == 50.0
    assert body["gaps"] == []
    assert body["warnings"] == []


def test_gaps_longer_than_30_minutes_are_marked(client):
    post(client, [1, 2, 3, 10, 11])  # 7 steps of 10 min = 70 min between seq 3 and 10
    body = data(client)
    gaps = body["gaps"]
    assert len(gaps) == 1
    assert gaps[0]["hours"] == pytest.approx(70 / 60, abs=0.05)
    # An empty point after seq 3 breaks the line across the gap.
    air = body["series"][0]
    assert air["y"] == [50.0, 50.0, 50.0, None, 50.0, 50.0]
    assert len(air["x"]) == 6


def test_text_and_missing_metrics(client):
    post(client, [1], metrics={"power_source": "usb"})
    post(client, [2], metrics={"lux": 5.0, "solution_ec_ms": 1.2})
    body = data(client)
    assert [s["name"] for s in body["series"]] == ["lux"]
    assert body["series"][0]["y"] == [None, 5.0]
    assert body["latest"]["metrics"] == {"lux": 5.0, "solution_ec_ms": 1.2}


def points(body):
    return [y for y in body["series"][0]["y"] if y is not None]


def test_window_follows_days(client):
    post(client, [1], ts=NOW - 5 * 86400)
    post(client, [2], ts=NOW - 60)
    assert len(points(data(client, days=7))) == 2
    assert len(points(data(client, days=1))) == 1


def test_old_data_is_still_shown(client):
    # The window ends at the node's newest reading, not at today.
    post(client, [1, 2], ts=NOW - 200 * 86400)
    assert len(data(client)["series"][0]["x"]) == 2


def test_each_warning_kind_is_reported(client):
    post(client, range(1, 6))
    post(client, [6], metrics={"air_temp_c": 85.0})
    post(client, [7], ts=NOW - 3 * 86400, synced=False)
    post(client, [5007], ts=NOW - 60)
    post(client, [1], node_id="intruder-99")
    kinds = sorted(w["kind"] for w in data(client)["warnings"])
    assert kinds == ["clock_backwards", "out_of_range", "seq_jump", "unknown_node"]


def test_clock_far_ahead_is_reported_but_does_not_move_the_window(client):
    post(client, range(1, 4))
    post(client, [4], ts=NOW + 30 * 86400)
    body = data(client)
    assert [w["kind"] for w in body["warnings"]] == ["clock_ahead"]
    assert len(body["series"][0]["x"]) == 3


def test_unknown_node_can_be_selected(client):
    post(client, [1], node_id="intruder-99")
    body = data(client, node="intruder-99")
    assert body["node"] == "intruder-99"
    assert body["nodes"] == ["balcony-01", "intruder-99"]
    assert [w["kind"] for w in body["warnings"]] == ["unknown_node"]


def test_unknown_selected_node_with_no_data(client):
    body = data(client, node="nobody")
    assert body["latest"] is None
    assert body["series"] == []


@pytest.mark.parametrize("params", [{"days": 0}, {"days": 32}, {"node": "x" * 65}, {"days": "a"}])
def test_bad_parameters_are_422(client, params):
    assert client.get("/dashboard/data", params=params).status_code == 422


def test_dashboard_needs_no_api_key_because_nginx_guards_it(client):
    assert client.get("/dashboard/data").status_code == 200


def test_build_data_uses_known_nodes_order(settings):
    custom = Settings(**{**settings.__dict__, "known_nodes": ("balcony-02", "balcony-01")})
    client = TestClient(create_app(custom))
    post(client, [1], node_id="balcony-01")
    post(client, [1], node_id="balcony-02")
    assert build_data(custom, None, 7)["node"] == "balcony-02"
