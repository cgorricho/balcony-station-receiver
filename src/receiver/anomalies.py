"""Flags readings that look wrong, so tampering or a faulty sensor shows up.

Warnings only: nothing is deleted or rejected. All thresholds live here.
"""

from collections.abc import Iterable, Mapping
from typing import Any

# Physically plausible ranges per metric, inclusive. Metrics not listed are not checked.
METRIC_RANGES: dict[str, tuple[float, float]] = {
    "air_temp_c": (-40.0, 60.0),
    "tray_temp_c": (-40.0, 60.0),
    "air_rh_pct": (0.0, 100.0),
    "lux": (0.0, 200_000.0),
    "battery_v": (0.0, 6.0),
    "pellet_moisture_raw": (0.0, 4095.0),
    "rssi_dbm": (-120.0, 0.0),
}
MAX_SEQ_JUMP = 1_000
# A node can't have measured something after the server received it. A little
# drift is allowed.
MAX_CLOCK_AHEAD_SECONDS = 3600
# Readings buffered during an outage arrive late but keep their order, so only
# time running backwards between consecutive seqs points at a wrong clock.
MAX_CLOCK_BACKWARDS_SECONDS = 3600


def _warning(reading: Mapping[str, Any], kind: str, message: str) -> dict:
    return {
        "node_id": reading["node_id"],
        "seq": reading["seq"],
        "ts": reading["ts"],
        "received_at": reading["received_at"],
        "kind": kind,
        "message": message,
    }


def _duration(seconds: int) -> str:
    hours = seconds / 3600
    return f"{hours:.0f} hours" if hours < 48 else f"{hours / 24:.1f} days"


def find_warnings(
    readings: Iterable[Mapping[str, Any]], known_nodes: Iterable[str]
) -> list[dict]:
    """Return one warning per problem found, ordered by node and seq.

    Seq jumps compare consecutive stored readings of the same node, so the
    first reading of a window is not compared with anything before it.
    """
    known = set(known_nodes)
    ordered = sorted(readings, key=lambda r: (r["node_id"], r["seq"]))
    found: list[dict] = []
    reported_unknown: set[str] = set()
    previous: Mapping[str, Any] | None = None

    for reading in ordered:
        node = reading["node_id"]
        if node not in known and node not in reported_unknown:
            reported_unknown.add(node)
            found.append(_warning(reading, "unknown_node", f"Readings from unknown node {node!r}"))

        for name, (low, high) in METRIC_RANGES.items():
            value = reading["metrics"].get(name)
            if isinstance(value, int | float) and not low <= value <= high:
                found.append(
                    _warning(
                        reading,
                        "out_of_range",
                        f"{name} = {value:g} is outside {low:g} to {high:g}",
                    )
                )

        ahead = reading["ts"] - reading["received_at"]
        if ahead > MAX_CLOCK_AHEAD_SECONDS:
            found.append(
                _warning(
                    reading,
                    "clock_ahead",
                    f"Node clock is {_duration(ahead)} ahead of the server",
                )
            )

        if previous is not None and previous["node_id"] == node:
            backwards = previous["ts"] - reading["ts"]
            if backwards > MAX_CLOCK_BACKWARDS_SECONDS:
                found.append(
                    _warning(
                        reading,
                        "clock_backwards",
                        f"Node time went back {_duration(backwards)} from seq {previous['seq']}",
                    )
                )
            jump = reading["seq"] - previous["seq"]
            if jump > MAX_SEQ_JUMP:
                found.append(
                    _warning(
                        reading,
                        "seq_jump",
                        f"seq jumped by {jump} (from {previous['seq']} to {reading['seq']})",
                    )
                )
        previous = reading

    return found
