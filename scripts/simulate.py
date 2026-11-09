"""Signal simulator: watch the dashboard in action without the hardware.

Starts its own receiver on 127.0.0.1 with a scratch database (data/simulator.db),
so your real readings are never touched. It then:

1. fills in a few days of history, including a frost night and a 3-hour outage;
2. plays the last day forward, one reading every --tick seconds, so you can watch
   the charts fill in; once it reaches the present, it continues in real time;
3. injects each kind of warning along the way (sensor glitch, unknown node,
   wrong clock, seq jump), unless --no-anomalies.

Example:
    uv run python scripts/simulate.py
Stop it with Ctrl+C.
"""

import argparse
import math
import random
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import uvicorn

from receiver.config import PROJECT_ROOT, Settings
from receiver.dashboard import build_data
from receiver.main import create_app

NODE = "balcony-01"
INTRUDER = "intruder-99"
FW = "sim-0.1.0"
DAY = 86400


class Weather:
    """A smooth, repeatable model of the balcony, driven by Unix time."""

    def __init__(self, seed: int, frost_night: int | None):
        rng = random.Random(seed)
        self.phases = [rng.uniform(0, 2 * math.pi) for _ in range(6)]
        self.frost_night = frost_night
        self.rng = random.Random(seed + 1)
        self.moisture = 1900.0
        self.last_ts: int | None = None

    def clouds(self, ts: int) -> float:
        """Cloud cover factor in 0.25..1, changing over hours and days."""
        p = self.phases
        wave = (
            0.5 * math.sin(ts / 9_000 + p[0])
            + 0.3 * math.sin(ts / 31_000 + p[1])
            + 0.4 * math.sin(ts / 170_000 + p[2])
        )
        return max(0.25, min(1.0, 0.75 + 0.35 * wave))

    def metrics(self, ts: int, local_hour: float, start_ts: int) -> dict:
        p = self.phases
        daylight = (local_hour - 6.5) / 12.5
        sun = math.sin(math.pi * daylight) if 0 < daylight < 1 else 0.0
        lux = 65_000 * sun * self.clouds(ts) * self.rng.uniform(0.97, 1.03)

        base = 9 + 3 * math.sin(ts / 260_000 + p[3])
        air = base + 6 * math.sin(math.pi * (local_hour - 9) / 12) + self.rng.uniform(-0.25, 0.25)
        if self.frost_night is not None:
            # A clear, cold night: a dip of up to 11 C centred on the night's 5 a.m.
            hours_from_dip = (ts - self.frost_night) / 3600
            air -= 11 * math.exp(-((hours_from_dip / 4) ** 2))

        if self.last_ts is not None:
            hours = (ts - self.last_ts) / 3600
            self.moisture -= hours * (6 + lux / 15_000)
            if self.moisture < 1450:
                self.moisture = 2450  # watered
        self.last_ts = ts

        return {
            "air_temp_c": round(air, 2),
            "air_rh_pct": round(max(20, min(98, 62 - 2.2 * (air - 9) + self.rng.uniform(-2, 2))), 1),
            "lux": round(lux, 1),
            "tray_temp_c": round(air + 1.2 + lux / 18_000 + self.rng.uniform(-0.15, 0.15), 2),
            "pellet_moisture_raw": int(self.moisture + self.rng.uniform(-10, 10)),
            "battery_v": round(4.82 - 0.012 * (ts - start_ts) / DAY, 3),
            "power_source": "battery",
            "rssi_dbm": -60 + self.rng.randint(-6, 6),
        }


def local_hour(ts: int, tz: ZoneInfo) -> float:
    moment = datetime.fromtimestamp(ts, tz)
    return moment.hour + moment.minute / 60 + moment.second / 3600


def local_5am_before(ts: int, tz: ZoneInfo) -> int:
    day = datetime.fromtimestamp(ts, tz).date()
    moment = datetime.combine(day, dtime(5), tz)
    if moment.timestamp() > ts:
        moment = datetime.combine(day - timedelta(days=1), dtime(5), tz)
    return int(moment.timestamp())


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8766, help="0 picks a free port")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "simulator.db")
    parser.add_argument("--keep", action="store_true", help="keep the previous simulator database")
    parser.add_argument("--history-days", type=float, default=3, help="days filled in at once")
    parser.add_argument("--live-hours", type=float, default=24, help="hours played forward")
    parser.add_argument("--step", type=int, default=600, help="seconds between readings")
    parser.add_argument("--tick", type=float, default=2, help="real seconds per live reading")
    parser.add_argument("--ticks", type=int, default=0, help="stop after N live readings (0 = run on)")
    parser.add_argument("--refresh", type=int, default=5, help="dashboard refresh, seconds")
    parser.add_argument("--no-anomalies", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    if args.step < 60 or args.tick < 0 or args.history_days < 0 or args.live_hours <= 0:
        parser.error("--step must be at least 60, and the other durations positive")
    return args


def start_server(settings: Settings, port: int) -> tuple[uvicorn.Server, threading.Thread, int]:
    config = uvicorn.Config(
        create_app(settings), host="127.0.0.1", port=port, log_level="warning",
        access_log=False, log_config=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        if not thread.is_alive() or time.time() > deadline:
            raise SystemExit(f"Could not start the receiver on port {port}. Is it in use? Try --port 0.")
        time.sleep(0.05)
    return server, thread, server.servers[0].sockets[0].getsockname()[1]


class Node:
    def __init__(self, client: httpx.Client, url: str):
        self.client = client
        self.url = url
        self.seq = 1

    def send(self, readings: list[dict], node_id: str = NODE) -> dict:
        body = {
            "node_id": node_id,
            "fw_version": FW,
            "oldest_buffered_seq": min(r["seq"] for r in readings),
            "readings": readings,
        }
        response = self.client.post(self.url, json=body)
        response.raise_for_status()
        return response.json()

    def reading(self, ts: int, metrics: dict, synced: bool = True) -> dict:
        reading = {"seq": self.seq, "ts": ts, "ts_synced": synced, "metrics": metrics}
        self.seq += 1
        return reading


def main(argv=None) -> int:
    args = parse_args(argv)
    db_path = args.db.resolve()
    if not args.keep:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    key = secrets.token_hex(16)
    settings = Settings(
        api_key=key, db_path=db_path, log_dir=db_path.parent / "simulator-logs",
        host="127.0.0.1", known_nodes=(NODE,),
    )
    tz = ZoneInfo(settings.tz)
    server, thread, port = start_server(settings, args.port)
    base = f"http://127.0.0.1:{port}"
    dashboard = f"{base}/dashboard?refresh={args.refresh}"
    print(f"Receiver running on {base} with scratch database {db_path}")
    print(f"Dashboard: {dashboard}")

    now = int(time.time())
    live_start = now - int(args.live_hours * 3600)
    history_start = live_start - int(args.history_days * DAY)
    start = history_start - history_start % args.step
    frost = local_5am_before(start + int(args.history_days * DAY * 0.6), tz) if args.history_days >= 1 else None
    weather = Weather(args.seed, frost)
    outage = (start + int((live_start - start) * 0.35), start + int((live_start - start) * 0.35) + 3 * 3600)

    try:
        with httpx.Client(headers={"X-Station-Key": key}, timeout=30) as client:
            node = Node(client, f"{base}/api/v1/readings")

            # 1. History, sent in batches like a node catching up.
            batch: list[dict] = []
            ts = start
            while ts < live_start:
                if not outage[0] <= ts < outage[1] or args.history_days < 1:
                    batch.append(node.reading(ts, weather.metrics(ts, local_hour(ts, tz), start)))
                if len(batch) == 200:
                    node.send(batch)
                    batch = []
                ts += args.step
            if batch:
                node.send(batch)
            print(f"History: {node.seq - 1} readings from the last {args.history_days:g} days "
                  "(with a frost night and a 3-hour outage)")
            if not args.no_browser:
                webbrowser.open(dashboard)

            # 2. Live: one reading per tick until the present, then in real time.
            anomalies = {} if args.no_anomalies else {
                12: "glitch", 24: "intruder", 36: "clock", 48: "jump",
            }
            tick = 0
            print(f"Live: one reading every {args.tick:g} s"
                  + ("" if args.no_anomalies else "; warnings injected at readings 12, 24, 36 and 48"))
            while args.ticks == 0 or tick < args.ticks:
                tick += 1
                if ts > time.time():
                    time.sleep(max(0, ts - time.time()))
                metrics = weather.metrics(ts, local_hour(ts, tz), start)
                kind = anomalies.get(tick)
                synced = True
                send_ts = ts
                if kind == "glitch":
                    metrics["air_temp_c"] = 85.0
                    print(f"  [{tick}] sensor glitch: air temperature 85 C")
                elif kind == "intruder":
                    stranger = {"seq": 1, "ts": ts, "ts_synced": True, "metrics": dict(metrics)}
                    node.send([stranger], node_id=INTRUDER)
                    print(f"  [{tick}] unknown node {INTRUDER!r} sent a reading")
                elif kind == "clock":
                    send_ts = ts - 3 * DAY
                    synced = False
                    print(f"  [{tick}] node clock 3 days behind")
                elif kind == "jump":
                    node.seq += 5000
                    print(f"  [{tick}] seq jumped by 5000")
                result = node.send([node.reading(send_ts, metrics, synced)])
                if tick % 12 == 0 and not kind:
                    print(f"  [{tick}] seq {result['ack_seq']} acknowledged")
                ts += args.step
                if args.tick and ts <= time.time():
                    time.sleep(args.tick)
    except KeyboardInterrupt:
        print("Stopping.")
    finally:
        data = build_data(settings, NODE, 7)
        kinds: dict[str, int] = {}
        for warning in data["warnings"]:
            kinds[warning["kind"]] = kinds.get(warning["kind"], 0) + 1
        print("Warnings: " + (", ".join(f"{k}={v}" for k, v in sorted(kinds.items())) or "none"))
        server.should_exit = True
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
