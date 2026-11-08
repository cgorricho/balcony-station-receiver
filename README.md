# Balcony Station Receiver

Stores readings sent over home WiFi by the balcony ESP32 sensor node. It runs on
native Windows and nothing leaves the LAN.

> **Moving to a VPS.** The plan changed on 2026-11-07. The steps below describe the
> Windows setup and will be replaced in milestone 9. See the
> [build plan](docs/BUILD_PLAN.md).

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

## Windows integration

1. Check the network and get the details for your router:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\show_network.ps1
   ```

   The laptop can be wired or on WiFi, as long as it's on the same network as the
   node. The network can stay Public.

2. Add the firewall rule, from an **Administrator** PowerShell:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install_firewall.ps1
   ```

   This opens port 8765 to `10.0.0.0/24` only. Add `-Subnet` if your home network
   uses a different range.

3. Register auto-start. Use an Administrator PowerShell if Windows denies access:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
   ```

4. Reserve the laptop's IP address on your router, using the details from step 1.
5. Reboot and log in, then open `http://<laptop-ip>:8765/api/v1/health` on a phone
   on the same WiFi.

Add `-DryRun` to either install script to see what it would do without changing
anything.

The receiver starts when you log in, not when the laptop boots. While it's
stopped, the node keeps buffering and catches up later.

## Start and stop

If the scheduled task is installed:

```powershell
Start-ScheduledTask -TaskName "Balcony Station Receiver"
Stop-ScheduledTask -TaskName "Balcony Station Receiver"
```

To run it in a terminal instead, where Ctrl+C stops it:

```powershell
uv run python -m receiver
```

Always start it this way rather than calling `uvicorn` directly. This entry point
turns off the access log, which would otherwise record `?key=` values.

## View the latest reading

In a browser, with your key from `.env`:

```text
http://localhost:8765/api/v1/latest?node_id=balcony-01&key=YOUR_KEY
```

The key then stays in your browser history. The header avoids that:

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

## Test without the node

With the server running, this pushes a simulated day into the **real** database,
so point `STATION_DB_PATH` at a scratch file first if you want to keep it clean.

```powershell
uv run python scripts/fake_node.py --gap 6 --resend
```

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
  `station.db-wal` beside it) with the receiver stopped.

## Uninstall

Remove the task, then the firewall rule from an **Administrator** PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Uninstall
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_firewall.ps1 -Uninstall
```

Then delete the project folder. That also deletes your readings in `data/`.

## Known limitations

- If the node's `seq` ever restarts, for example after a reflash, new readings are
  treated as duplicates, acknowledged and lost. Keep `seq` across reflashes.
- Readings with `ts_synced=false` count in summaries at the time the node reported.
