import pytest

from receiver.anomalies import (
    MAX_CLOCK_AHEAD_SECONDS,
    MAX_CLOCK_BACKWARDS_SECONDS,
    MAX_SEQ_JUMP,
    METRIC_RANGES,
    find_warnings,
)

KNOWN = ("balcony-01",)
T0 = 1_762_538_400


def reading(seq, ts=None, received_at=None, node_id="balcony-01", **metrics):
    ts = T0 + seq * 600 if ts is None else ts
    return {
        "node_id": node_id,
        "seq": seq,
        "ts": ts,
        "received_at": ts if received_at is None else received_at,
        "metrics": metrics or {"air_temp_c": 12.0, "lux": 100.0},
    }


def kinds(readings):
    return [w["kind"] for w in find_warnings(readings, KNOWN)]


def test_normal_readings_have_no_warnings():
    assert kinds([reading(s) for s in range(1, 50)]) == []


def test_readings_buffered_for_days_are_not_flagged():
    # A node offline for three days sends old readings late, in order.
    late = T0 + 3 * 86400
    assert kinds([reading(s, received_at=late) for s in range(1, 50)]) == []


@pytest.mark.parametrize(
    "name, value",
    [("air_temp_c", 85.0), ("air_temp_c", -41), ("air_rh_pct", 101), ("lux", -1),
     ("battery_v", 7.5), ("tray_temp_c", 61), ("pellet_moisture_raw", 5000), ("rssi_dbm", 3)],
)
def test_out_of_range_values(name, value):
    warnings = find_warnings([reading(1, **{name: value})], KNOWN)
    assert [w["kind"] for w in warnings] == ["out_of_range"]
    assert name in warnings[0]["message"]


@pytest.mark.parametrize("name", list(METRIC_RANGES))
def test_range_limits_are_inclusive(name):
    low, high = METRIC_RANGES[name]
    assert kinds([reading(1, **{name: low}), reading(2, **{name: high})]) == []


def test_text_and_unknown_metrics_are_not_range_checked():
    assert kinds([reading(1, power_source="battery", solution_ec_ms=99999.0)]) == []


def test_seq_jump():
    assert kinds([reading(1), reading(1 + MAX_SEQ_JUMP)]) == []
    warnings = find_warnings([reading(1), reading(2), reading(3 + MAX_SEQ_JUMP)], KNOWN)
    assert [w["kind"] for w in warnings] == ["seq_jump"]
    assert warnings[0]["seq"] == 3 + MAX_SEQ_JUMP


def test_seq_jump_is_per_node():
    readings = [reading(1), reading(5000, node_id="balcony-02")]
    assert "seq_jump" not in kinds(readings)


def test_clock_ahead_of_server():
    assert kinds([reading(1, ts=T0 + MAX_CLOCK_AHEAD_SECONDS, received_at=T0)]) == []
    warnings = find_warnings([reading(1, ts=T0 + 2 * 86400, received_at=T0)], KNOWN)
    assert [w["kind"] for w in warnings] == ["clock_ahead"]
    assert "2.0 days ahead" in warnings[0]["message"]


def test_clock_running_backwards():
    ok = [reading(1, ts=T0), reading(2, ts=T0 - MAX_CLOCK_BACKWARDS_SECONDS)]
    assert kinds(ok) == []
    warnings = find_warnings([reading(1, ts=T0), reading(2, ts=T0 - 3 * 86400)], KNOWN)
    assert [w["kind"] for w in warnings] == ["clock_backwards"]
    assert warnings[0]["seq"] == 2


def test_unknown_node_is_reported_once():
    readings = [reading(s, node_id="intruder") for s in range(1, 5)]
    assert kinds(readings) == ["unknown_node"]


def test_order_of_input_does_not_matter():
    readings = [reading(3 + MAX_SEQ_JUMP), reading(1), reading(2)]
    assert kinds(readings) == ["seq_jump"]
