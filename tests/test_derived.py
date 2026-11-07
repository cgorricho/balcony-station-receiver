import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from receiver.derived import c_to_f, daily_summaries, ppfd_umol

TZ = ZoneInfo("America/New_York")
TEN_MINUTES = 600


def epoch(*args):
    return int(datetime(*args, tzinfo=TZ).timestamp())


def readings(start, count, interval=TEN_MINUTES, **metrics):
    return [
        {"seq": i, "ts": start + i * interval, "metrics": dict(metrics)} for i in range(count)
    ]


def only_day(data, **kwargs):
    summaries = daily_summaries(data, **kwargs)
    assert len(summaries) == 1
    return summaries[0]


def test_c_to_f():
    assert c_to_f(0) == 32
    assert c_to_f(100) == 212
    assert c_to_f(-40) == -40


def test_ppfd_factor_is_configurable():
    assert ppfd_umol(5400) == 100
    assert ppfd_umol(5000, lux_per_ppfd=50) == 100


def test_dli_for_constant_light():
    # 1000 umol/m2/s for 12 hours: 1000 * 43200 / 1e6 = 43.2 mol/m2/day.
    data = readings(epoch(2026, 1, 15, 6), 73, lux=54_000.0)
    assert only_day(data).dli == pytest.approx(43.2)


def test_dli_matches_analytic_daylight_curve_within_one_percent():
    # A half-sine day integrates to 2 * peak * length / pi.
    start = epoch(2026, 1, 15, 0)
    length = 12 * 3600
    sunrise = start + 6 * 3600
    peak_lux = 54_000.0
    data = []
    for i in range(144):
        ts = start + i * TEN_MINUTES
        phase = (ts - sunrise) / length
        lux = peak_lux * math.sin(math.pi * phase) if 0 < phase < 1 else 0.0
        data.append({"seq": i, "ts": ts, "metrics": {"lux": lux}})
    expected = 2 * 1000 * length / math.pi / 1_000_000
    assert only_day(data).dli == pytest.approx(expected, rel=0.01)


def test_dli_factor_is_used():
    data = readings(epoch(2026, 1, 15, 6), 7, lux=27_000.0)
    assert only_day(data, lux_per_ppfd=27).dli == pytest.approx(1000 * 3600 / 1e6)


def test_long_gap_is_capped_at_30_minutes():
    start = epoch(2026, 1, 15, 8)
    data = [
        {"seq": 1, "ts": start, "metrics": {"lux": 54_000.0}},
        {"seq": 2, "ts": start + 6 * 3600, "metrics": {"lux": 54_000.0}},
    ]
    summary = only_day(data)
    assert summary.dli == pytest.approx(1000 * 1800 / 1e6)
    assert summary.coverage_pct == pytest.approx(1800 / 86400 * 100)


def test_unordered_readings_are_sorted():
    data = readings(epoch(2026, 1, 15, 6), 7, lux=54_000.0)
    assert only_day(list(reversed(data))).dli == pytest.approx(only_day(data).dli)


def test_full_day_coverage_needs_a_reading_after_midnight():
    data = readings(epoch(2026, 1, 15, 0), 145, lux=0.0)
    summaries = daily_summaries(data)
    assert summaries[0].coverage_pct == pytest.approx(100)


def test_interval_crossing_midnight_is_split_between_days():
    start = epoch(2026, 1, 15, 23, 50)
    data = [
        {"seq": 1, "ts": start, "metrics": {"lux": 54_000.0}},
        {"seq": 2, "ts": start + 1200, "metrics": {"lux": 54_000.0}},
    ]
    first, second = daily_summaries(data)
    assert first.day == date(2026, 1, 15)
    assert second.day == date(2026, 1, 16)
    assert first.dli == pytest.approx(0.6)
    assert second.dli == pytest.approx(0.6)


@pytest.mark.parametrize(
    "day, hours",
    [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25), (date(2026, 1, 15), 24)],
)
def test_daylight_saving_days_use_their_real_length(day, hours):
    start = epoch(day.year, day.month, day.day, 0)
    data = readings(start, hours * 6 + 1, lux=0.0)
    assert daily_summaries(data)[0].coverage_pct == pytest.approx(100)


def test_temperatures_are_reported_in_fahrenheit():
    start = epoch(2026, 1, 15, 12)
    data = [
        {"seq": 1, "ts": start, "metrics": {"air_temp_c": 0.0, "tray_temp_c": 10.0}},
        {"seq": 2, "ts": start + 600, "metrics": {"air_temp_c": 20.0, "tray_temp_c": 30.0}},
    ]
    summary = only_day(data)
    assert (summary.air_min_f, summary.air_max_f) == (32, 68)
    assert (summary.tray_min_f, summary.tray_max_f) == (50, 86)


def test_zero_fahrenheit_is_not_treated_as_missing():
    start = epoch(2026, 1, 15, 12)
    zero_f = -160 / 9
    assert c_to_f(zero_f) == 0
    data = [
        {"seq": 1, "ts": start, "metrics": {"air_temp_c": zero_f, "tray_temp_c": -40.0}},
        {"seq": 2, "ts": start + 600, "metrics": {"air_temp_c": 10.0, "tray_temp_c": zero_f}},
        {"seq": 3, "ts": start + 1200, "metrics": {"air_temp_c": 5.0, "tray_temp_c": -30.0}},
    ]
    summary = only_day(data)
    assert summary.air_min_f == 0
    assert summary.air_max_f == 50
    assert summary.tray_max_f == 0


def test_hours_below_45f():
    start = epoch(2026, 1, 15, 2)
    cold_then_warm = readings(start, 7, air_temp_c=5.0)
    cold_then_warm[-1]["metrics"]["air_temp_c"] = 10.0
    assert only_day(cold_then_warm).hours_below_45f == pytest.approx(1)


def test_sun_window_uses_threshold():
    start = epoch(2026, 1, 15, 9)
    values = [10_000.0, 25_000.0, 15_000.0, 30_000.0, 5_000.0]
    data = [
        {"seq": i, "ts": start + i * 3600, "metrics": {"lux": lux}} for i, lux in enumerate(values)
    ]
    summary = only_day(data)
    assert summary.sun_start.hour == 10
    assert summary.sun_end.hour == 12
    assert only_day(data, sun_threshold_lux=28_000).sun_start.hour == 12


def test_no_sun_leaves_window_empty():
    summary = only_day(readings(epoch(2026, 1, 15, 9), 3, lux=100.0))
    assert summary.sun_start is None
    assert summary.sun_end is None


def test_text_and_missing_metrics_are_ignored():
    data = readings(epoch(2026, 1, 15, 9), 3, lux="broken")
    summary = only_day(data)
    assert summary.dli == 0
    assert summary.air_min_f is None


def test_no_readings_gives_no_days():
    assert daily_summaries([]) == []
