"""Values computed on output: PPFD, DLI, sun window, daily summaries."""

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_LUX_PER_PPFD = 54.0
DEFAULT_SUN_THRESHOLD_LUX = 20_000.0
MAX_INTERVAL_SECONDS = 30 * 60
COLD_THRESHOLD_F = 45.0


def c_to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def ppfd_umol(lux: float, lux_per_ppfd: float = DEFAULT_LUX_PER_PPFD) -> float:
    return lux / lux_per_ppfd


def day_bounds(day: date, tz: ZoneInfo) -> tuple[int, int]:
    """Return epoch seconds for the start and end of a local calendar day."""
    start = datetime.combine(day, time.min, tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tz)
    return int(start.timestamp()), int(end.timestamp())


def local_date(ts: int, tz: ZoneInfo) -> date:
    return datetime.fromtimestamp(ts, tz).date()


def _split_by_day(start: int, end: int, tz: ZoneInfo) -> Iterator[tuple[date, int]]:
    while start < end:
        day = local_date(start, tz)
        segment_end = min(end, day_bounds(day, tz)[1])
        yield day, segment_end - start
        start = segment_end


def _number(metrics: Mapping[str, Any], name: str) -> float | None:
    value = metrics.get(name)
    return value if isinstance(value, int | float) else None


def _widen(low: float | None, high: float | None, value: float) -> tuple[float, float]:
    if low is None or high is None:
        return value, value
    return min(low, value), max(high, value)


@dataclass
class DaySummary:
    day: date
    air_min_f: float | None = None
    air_max_f: float | None = None
    tray_min_f: float | None = None
    tray_max_f: float | None = None
    hours_below_45f: float = 0.0
    dli: float = 0.0
    sun_start: datetime | None = None
    sun_end: datetime | None = None
    coverage_pct: float = 0.0


def daily_summaries(
    readings: Iterable[Mapping[str, Any]],
    tz_name: str = "America/New_York",
    lux_per_ppfd: float = DEFAULT_LUX_PER_PPFD,
    sun_threshold_lux: float = DEFAULT_SUN_THRESHOLD_LUX,
) -> list[DaySummary]:
    """Summarize readings per local calendar day.

    Each reading stands for the time until the next one, capped at 30 minutes
    so long data gaps don't inflate totals. An interval that crosses midnight
    is split between the two days. The last reading has no next reading, so it
    adds no time.
    """
    tz = ZoneInfo(tz_name)
    ordered = sorted(readings, key=lambda r: (r["ts"], r["seq"]))
    days: dict[date, DaySummary] = {}
    covered: defaultdict[date, int] = defaultdict(int)

    def summary_for(day: date) -> DaySummary:
        return days.setdefault(day, DaySummary(day=day))

    for index, reading in enumerate(ordered):
        ts = reading["ts"]
        metrics = reading["metrics"]
        lux = _number(metrics, "lux")
        air_c = _number(metrics, "air_temp_c")
        tray_c = _number(metrics, "tray_temp_c")
        air_f = None if air_c is None else c_to_f(air_c)
        tray_f = None if tray_c is None else c_to_f(tray_c)

        summary = summary_for(local_date(ts, tz))
        if air_f is not None:
            summary.air_min_f, summary.air_max_f = _widen(
                summary.air_min_f, summary.air_max_f, air_f
            )
        if tray_f is not None:
            summary.tray_min_f, summary.tray_max_f = _widen(
                summary.tray_min_f, summary.tray_max_f, tray_f
            )
        if lux is not None and lux > sun_threshold_lux:
            moment = datetime.fromtimestamp(ts, tz)
            summary.sun_start = summary.sun_start or moment
            summary.sun_end = moment

        if index + 1 == len(ordered):
            continue
        interval = min(ordered[index + 1]["ts"] - ts, MAX_INTERVAL_SECONDS)
        for day, seconds in _split_by_day(ts, ts + interval, tz):
            target = summary_for(day)
            covered[day] += seconds
            if lux is not None:
                target.dli += ppfd_umol(lux, lux_per_ppfd) * seconds / 1_000_000
            if air_f is not None and air_f < COLD_THRESHOLD_F:
                target.hours_below_45f += seconds / 3600

    for day, summary in days.items():
        start, end = day_bounds(day, tz)
        summary.coverage_pct = covered[day] / (end - start) * 100
    return [days[day] for day in sorted(days)]
