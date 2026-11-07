"""Simulate the ESP32 node: buffer readings and send them in batches.

Like the real node, it drops readings only once the receiver acknowledges them.
With --gap, it keeps buffering while the laptop is away, then catches up.

Example:
    uv run python scripts/fake_node.py --start 2026-01-15 --gap 6 --resend
"""

import argparse
import math
import random
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from receiver.config import load_settings
from receiver.derived import day_bounds
from receiver.models import MAX_BATCH

LUX_PEAK = 60_000.0
SUNRISE_HOUR = 6.5
SUNSET_HOUR = 19.0


def batch_size(value: str) -> int:
    size = int(value)
    if not 1 <= size <= MAX_BATCH:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_BATCH}")
    return size


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1:8765", help="host or host:port")
    parser.add_argument("--key", help="API key (default: from .env)")
    parser.add_argument("--node", default="balcony-01")
    parser.add_argument("--batch", type=batch_size, default=6, help="readings per POST")
    parser.add_argument("--resend", action="store_true", help="repeat the last batch at the end")
    parser.add_argument("--gap", type=float, default=0, help="hours the laptop is away, mid-run")
    parser.add_argument("--hours", type=float, default=24, help="simulated hours")
    parser.add_argument("--interval", type=int, default=600, help="seconds between readings")
    parser.add_argument("--start", type=date.fromisoformat, help="first local day, YYYY-MM-DD")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


def make_metrics(ts: int, tz: ZoneInfo, rng: random.Random, elapsed_fraction: float) -> dict:
    moment = datetime.fromtimestamp(ts, tz)
    hour = moment.hour + moment.minute / 60
    daylight = (hour - SUNRISE_HOUR) / (SUNSET_HOUR - SUNRISE_HOUR)
    lux = 0.0
    if 0 < daylight < 1:
        lux = LUX_PEAK * math.sin(math.pi * daylight) * rng.uniform(0.95, 1.05)
    air = 9 + 7 * math.sin(math.pi * (hour - 9) / 12) + rng.uniform(-0.3, 0.3)
    return {
        "air_temp_c": round(air, 2),
        "air_rh_pct": round(55 - 2 * (air - 9) + rng.uniform(-2, 2), 1),
        "lux": round(lux, 1),
        "tray_temp_c": round(air + 1.5 + lux / 20_000 + rng.uniform(-0.2, 0.2), 2),
        "pellet_moisture_raw": 1830 + rng.randint(-15, 15),
        "battery_v": round(4.8 - 0.02 * elapsed_fraction, 3),
        "power_source": "battery",
        "rssi_dbm": -61 + rng.randint(-5, 5),
    }


class Sender:
    def __init__(self, client: httpx.Client, url: str, node_id: str):
        self.client = client
        self.url = url
        self.node_id = node_id
        self.buffer: list[dict] = []
        self.last_body: dict | None = None
        self.posts = 0
        self.stored = 0
        self.duplicates = 0

    def post(self, body: dict) -> dict:
        response = self.client.post(self.url, json=body)
        response.raise_for_status()
        result = response.json()
        self.posts += 1
        self.stored += result["stored"]
        self.duplicates += result["duplicates"]
        return result

    def send_oldest(self, count: int) -> None:
        body = {
            "node_id": self.node_id,
            "fw_version": "fake-0.1.0",
            "oldest_buffered_seq": self.buffer[0]["seq"],
            "readings": self.buffer[:count],
        }
        ack_seq = self.post(body)["ack_seq"]
        if ack_seq is None or ack_seq < self.buffer[0]["seq"]:
            raise RuntimeError(f"receiver did not acknowledge the batch: ack_seq={ack_seq}")
        self.last_body = body
        self.buffer = [r for r in self.buffer if r["seq"] > ack_seq]


def first_position(client: httpx.Client, base: str, args, tz: ZoneInfo) -> tuple[int, int]:
    """Continue after the node's latest reading, so reruns don't reuse seqs."""
    response = client.get(f"{base}/latest", params={"node_id": args.node})
    latest = None
    if response.status_code != 404:
        response.raise_for_status()
        latest = response.json()
    seq = latest["seq"] + 1 if latest else 1
    if args.start:
        return seq, day_bounds(args.start, tz)[0]
    if latest:
        return seq, latest["ts"] + args.interval
    yesterday = datetime.now(tz).date() - timedelta(days=1)
    return seq, int(datetime.combine(yesterday, time.min, tz).timestamp())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings() if not args.key else None
    key = args.key or settings.api_key
    tz = ZoneInfo(settings.tz if settings else "America/New_York")
    rng = random.Random(args.seed)
    base = f"http://{args.host}/api/v1"
    total = int(args.hours * 3600 / args.interval)
    gap_start = total // 2
    gap_end = gap_start + int(args.gap * 3600 / args.interval)

    with httpx.Client(headers={"X-Station-Key": key}, timeout=30) as client:
        first_seq, first_ts = first_position(client, base, args, tz)
        sender = Sender(client, f"{base}/readings", args.node)
        for index in range(total):
            ts = first_ts + index * args.interval
            sender.buffer.append(
                {
                    "seq": first_seq + index,
                    "ts": ts,
                    "ts_synced": True,
                    "metrics": make_metrics(ts, tz, rng, index / max(total, 1)),
                }
            )
            if gap_start <= index < gap_end:
                continue
            while len(sender.buffer) >= args.batch:
                sender.send_oldest(args.batch)
        while sender.buffer:
            sender.send_oldest(args.batch)
        if args.resend and sender.last_body:
            sender.post(sender.last_body)

    print(
        f"readings={total} posts={sender.posts} stored={sender.stored} "
        f"duplicates={sender.duplicates} buffered={len(sender.buffer)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
