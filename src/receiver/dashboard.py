"""Read-only dashboard at /dashboard (spec 6.3).

The app has no user accounts: nginx's login protects everything under
/dashboard, so this router must never be published except through nginx.
"""

import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import plotly
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

from . import db
from .anomalies import MAX_CLOCK_AHEAD_SECONDS, find_warnings
from .config import Settings
from .derived import MAX_INTERVAL_SECONDS, c_to_f, daily_summaries
from .models import MAX_TEXT

STATIC = Path(__file__).parent / "static"
PLOTLY_JS = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
DEFAULT_DAYS = 7
MAX_DAYS = 31
# A node clock this far ahead of the server doesn't move the chart window.
MAX_FUTURE_SECONDS = 24 * 3600

# Charted metrics: (name, label, unit, convert). Temperatures are shown in °F.
SERIES = [
    ("air_temp_c", "Air", "°F", c_to_f),
    ("tray_temp_c", "Tray", "°F", c_to_f),
    ("lux", "Light", "lux", None),
    ("pellet_moisture_raw", "Pellet moisture", "raw", None),
    ("battery_v", "Battery", "V", None),
]

# Plotly injects inline styles, so style-src needs 'unsafe-inline'. Scripts don't.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _local(ts: int, tz: ZoneInfo) -> str:
    """Station-local time without an offset, so charts show balcony time."""
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S")


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def build_data(settings: Settings, node_id: str | None, days: int, now: int | None = None) -> dict:
    """Everything the page shows, as JSON-ready data."""
    tz = ZoneInfo(settings.tz)
    now = int(time.time()) if now is None else now
    _, nodes = db.health_info(settings.db_path)
    nodes_all = sorted(set(nodes) | set(settings.known_nodes))
    if node_id is None:
        # A known node with data, else any node with data, else the first known node.
        fallback = nodes[0] if nodes else next(iter(settings.known_nodes), None)
        node_id = next((n for n in settings.known_nodes if n in nodes), fallback)

    data: dict = {
        "generated_at": _local(now, tz),
        "tz": settings.tz,
        "nodes": nodes_all,
        "node": node_id,
        "days": days,
        "latest": None,
        "window": None,
        "series": [],
        "gaps": [],
        "summary": [],
        "warnings": [],
    }

    # Unknown nodes are flagged even when another node is selected.
    for other in nodes:
        if other in settings.known_nodes or other == node_id:
            continue
        newest = db.latest_reading(settings.db_path, other)
        if newest is not None:
            data["warnings"].extend(
                _format(w, tz)
                for w in find_warnings([newest], settings.known_nodes)
                if w["kind"] == "unknown_node"
            )

    if node_id is None:
        return data
    latest = db.latest_reading(settings.db_path, node_id)
    if latest is None:
        return data

    # The window ends at the node's newest timestamp, but a clock far in the
    # future can't drag it away from the real data.
    end = db.newest_ts(settings.db_path, node_id, now + MAX_FUTURE_SECONDS) or now
    start = end - days * 86400
    readings = db.fetch_readings(settings.db_path, node_id, start, end + 1)

    data["window"] = {"start": _local(start, tz), "end": _local(end, tz)}
    data["latest"] = {
        **latest,
        "ts_local": _local(latest["ts"], tz),
        "received_local": _local(latest["received_at"], tz),
        "age_seconds": now - latest["received_at"],
    }

    # A gap gets an empty point one second after its last reading, so the
    # chart lines break there instead of bridging it.
    gap_after = {
        index
        for index, (before, after) in enumerate(zip(readings, readings[1:]))
        if after["ts"] - before["ts"] > MAX_INTERVAL_SECONDS
    }
    times = []
    for index, reading in enumerate(readings):
        times.append(_local(reading["ts"], tz))
        if index in gap_after:
            times.append(_local(reading["ts"] + 1, tz))
    for name, label, unit, convert in SERIES:
        values = []
        for index, reading in enumerate(readings):
            value = reading["metrics"].get(name)
            if isinstance(value, int | float):
                values.append(_round(convert(value) if convert else value))
            else:
                values.append(None)
            if index in gap_after:
                values.append(None)
        if any(v is not None for v in values):
            data["series"].append(
                {"name": name, "label": label, "unit": unit, "x": times, "y": values}
            )

    for before, after in zip(readings, readings[1:]):
        if after["ts"] - before["ts"] > MAX_INTERVAL_SECONDS:
            data["gaps"].append(
                {
                    "start": _local(before["ts"], tz),
                    "end": _local(after["ts"], tz),
                    "hours": round((after["ts"] - before["ts"]) / 3600, 1),
                }
            )

    for day in daily_summaries(readings, settings.tz, settings.lux_per_ppfd, settings.sun_threshold_lux):
        data["summary"].append(
            {
                "day": day.day.isoformat(),
                "air_min_f": _round(day.air_min_f, 1),
                "air_max_f": _round(day.air_max_f, 1),
                "tray_min_f": _round(day.tray_min_f, 1),
                "tray_max_f": _round(day.tray_max_f, 1),
                "hours_below_45f": round(day.hours_below_45f, 1),
                "dli": round(day.dli, 2),
                "sun_start": day.sun_start.strftime("%H:%M") if day.sun_start else None,
                "sun_end": day.sun_end.strftime("%H:%M") if day.sun_end else None,
                "coverage_pct": round(day.coverage_pct, 1),
            }
        )

    # Warnings cover the window, plus any reading whose clock is so far ahead
    # that it falls outside it. Those are checked alone, so they can't look
    # like a seq jump.
    found = find_warnings(readings, settings.known_nodes)
    in_window = {r["seq"] for r in readings}
    outside = [
        seq
        for seq in db.ahead_seqs(settings.db_path, node_id, MAX_CLOCK_AHEAD_SECONDS)
        if seq not in in_window
    ]
    for reading in db.fetch_by_seq(settings.db_path, node_id, outside[:50]):
        found.extend(w for w in find_warnings([reading], settings.known_nodes) if w["kind"] != "unknown_node")
    data["warnings"].extend(_format(w, tz) for w in found)
    data["warnings"].sort(key=lambda w: w["received_at"], reverse=True)
    return data


def _format(warning: dict, tz: ZoneInfo) -> dict:
    return {**warning, "received_local": _local(warning["received_at"], tz)}


def create_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/dashboard")

    def static(name: str, media_type: str) -> FileResponse:
        return FileResponse(STATIC / name, media_type=media_type, headers=SECURITY_HEADERS)

    @router.get("", include_in_schema=False)
    def page() -> FileResponse:
        return static("dashboard.html", "text/html; charset=utf-8")

    @router.get("/app.js", include_in_schema=False)
    def app_js() -> FileResponse:
        return static("dashboard.js", "text/javascript; charset=utf-8")

    @router.get("/app.css", include_in_schema=False)
    def app_css() -> FileResponse:
        return static("dashboard.css", "text/css; charset=utf-8")

    @router.get("/plotly.min.js", include_in_schema=False)
    def plotly_js() -> FileResponse:
        headers = {**SECURITY_HEADERS, "Cache-Control": "public, max-age=86400"}
        return FileResponse(PLOTLY_JS, media_type="text/javascript; charset=utf-8", headers=headers)

    @router.get("/data", include_in_schema=False)
    def page_data(
        node: str | None = Query(default=None, min_length=1, max_length=MAX_TEXT),
        days: int = Query(default=DEFAULT_DAYS, ge=1, le=MAX_DAYS),
    ) -> JSONResponse:
        return JSONResponse(build_data(settings, node, days), headers=SECURITY_HEADERS)

    return router
