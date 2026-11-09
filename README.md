# Balcony Station Receiver

Stores readings sent over HTTPS by the balcony ESP32 sensor node, and runs on a
VPS behind nginx.

> **Moving to a VPS.** The plan changed on 2026-11-07. The Windows install scripts
> are gone, and the steps below cover running it locally for development. Milestone
> 9 rewrites this README for the VPS. See the [build plan](docs/BUILD_PLAN.md).

- Build spec: [docs/build-spec.html](docs/build-spec.html)
- Milestones and decisions: [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md)

## First-time setup

Run these in PowerShell from the project folder. `.env` holds the API key and
stays out of git.

```powershell
uv sync
uv run pytest
```

If `.env` is missing, copy `.env.example` to `.env` and set a key (see
[Rotate the API key](#rotate-the-api-key)).

## Run it

In a terminal, where Ctrl+C stops it:

```powershell
uv run python -m receiver
```

Always start it this way rather than calling `uvicorn` directly. This entry point
turns off uvicorn's access log and its own proxy-header handling, which the app
does itself from `STATION_FORWARDED_ALLOW_IPS`.

The API docs pages (`/docs`, `/redoc`, `/openapi.json`) are turned off. The API is
described in the [spec](docs/build-spec.html).

## View the latest reading

The API key goes in the `X-Station-Key` header only. A key in the URL is refused,
because nginx logs full URLs.

```powershell
curl.exe -H "X-Station-Key: YOUR_KEY" "http://localhost:8765/api/v1/latest?node_id=balcony-01"
```

## Export to CSV

Writes one row per reading and one column per metric, plus `*_f` columns for
temperatures. Dates are local days, and both ends are included.

```powershell
uv run python scripts/export_csv.py --from 2026-01-01 --to 2026-01-31 --out january.csv
```

## Daily summary

Prints min/max temperatures, hours below 45°F, DLI, the sun window and data
coverage for each day.

```powershell
uv run python scripts/daily_summary.py --from 2026-01-01
```

## See the dashboard in action

The signal simulator starts its own receiver on `127.0.0.1:8766` with a scratch
database (`data/simulator.db`), so your real readings are never touched:

```powershell
uv run python scripts/simulate.py
```

It opens the dashboard in your browser, fills in three days of history (with a
frost night and a 3-hour outage), then plays the last day forward, one reading
every 2 seconds. At readings 12, 24, 36 and 48 it injects a sensor glitch, an
unknown node, a wrong clock and a `seq` jump, so each warning appears on the
page. Ctrl+C stops it. Useful flags: `--tick` (seconds per reading),
`--history-days`, `--no-anomalies`, `--port 0` (any free port).

The real receiver serves the same page at `/dashboard`. On the VPS, nginx's login
protects it.

## Test without the node

With the server running, this pushes a simulated day into the **real** database,
so point `STATION_DB_PATH` at a scratch file first if you want to keep it clean.

```powershell
uv run python scripts/fake_node.py --gap 6 --resend
```

`--url` sets the receiver's address (default `http://127.0.0.1:8765`). The public
address hides `/latest`, so there use a throwaway `--node` and `--first-seq 1`.

## Rotate the API key

1. Generate a new key:

   ```powershell
   uv run python -c "import secrets; print(secrets.token_hex(16))"
   ```

2. Replace `STATION_API_KEY` in `.env`.
3. Restart the receiver.
4. Update the key on the ESP32. Until then, the node gets `401` and keeps buffering.

## Logs and data

- `logs/receiver.log` holds each batch and every error. It rotates at 1 MB and
  keeps 5 files. API keys are redacted.
- `data/station.db` holds the readings. Back up this file (and any
  `station.db-wal` beside it) with the receiver stopped. Milestone 8 adds a backup
  script that works while it runs.

## Known limitations

- If the node's `seq` ever restarts, for example after a reflash, new readings are
  treated as duplicates, acknowledged and lost. Keep `seq` across reflashes.
- Readings with `ts_synced=false` count in summaries at the time the node reported.
