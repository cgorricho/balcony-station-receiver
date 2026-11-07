# Build Plan

This tracks the milestones from Section 9 of the spec.

- Local copy of the spec: [build-spec.html](build-spec.html)
- Published spec: <https://claude.ai/artifact/1hhDzc43MpVKkbjUnsz3Sg>

The spec is the source of truth. This file records status and decisions it leaves open.

## Milestones

| # | Milestone | Status |
|---|---|---|
| 1 | Scaffold: folder, git, `uv` env, dependencies, `.env` with a random key | Done |
| 2 | Ingest: `POST /readings` plus storage, with tests | Done |
| 3 | Other endpoints: `/time`, `/health`, `/latest`, with tests | Done |
| 4 | Derived values and scripts: DLI within 1%, fake node, CSV, daily summary | Done |
| 5 | Logging: rotating file log (5 × 1 MB), batches and errors, no API key | Done |
| 6 | Windows integration: firewall, scheduled task, DHCP instructions | Scripts done; installation pending |
| 7 | README: start/stop, latest, export, summary, key rotation, uninstall | Done |

**Done when (spec):** `fake_node.py` pushes 24 hours of data, including one resend and
one 6-hour gap, the database holds no duplicates, and `daily_summary.py` prints a
correct table. This passes against a real server process, and `tests/test_scripts.py`
repeats it automatically.

## Remaining steps for milestone 6

These change Windows or the router, so Carlos runs them. Details are in the README.

- [ ] Set the network to Private. On 2026-11-07 the laptop was on wired `Ethernet 3`, profile Public, with WiFi disconnected (`scripts/show_network.ps1`)
- [ ] Add the firewall rule (`scripts/install_firewall.ps1`, Administrator)
- [ ] Register the task (`scripts/install_task.ps1`)
- [ ] Reserve the laptop's IP address on the router
- [ ] Reboot, log in, and confirm the server came back
- [ ] Open `/api/v1/health` from a phone on the same WiFi

## Decisions

| Decision | Why |
|---|---|
| Added required `oldest_buffered_seq`; `ack_seq` is the unbroken run from it | "Highest seq in the batch" could delete unsent readings, and a lost reading could otherwise stall acks |
| `/latest` picks the highest `seq`, not the newest `ts` | The node's clock may be unsynced |
| `/health` uses the laptop's `received_at` | A wrong node clock can't distort it |
| Error responses never echo the input | The default echo can fail on `NaN` or invalid unicode |
| Undecodable bodies return `422`, not FastAPI's `400` | The contract says malformed bodies return `422` |
| Added the `tzdata` dependency | Windows has no timezone database for `zoneinfo` |
| Task runs `pythonw -m receiver`, so `run_hidden.vbs` is not needed | The spec allows either; this has fewer moving parts |
| `python -m receiver` is the only supported launch | It turns off the access log, which would record `?key=` |
| Log redacts the key and `?key=` values as well | A traceback must not leak the key either |
| Task allows running on battery | Windows tasks refuse to run on battery by default |
| `show_network.ps1` follows the default route, not the `Wi-Fi` adapter name | The laptop may be wired |

## Known gaps

- `seq` reset: a restarted count is treated as duplicates and lost. The proposed fix is a
  `boot_id` sent by the node and stored in the key. It is not implemented, and is
  cheapest to add before the firmware is written.
- `--gap` in `fake_node.py` simulates the laptop being away, so no data is missing.
  The 30-minute cap for real data loss is covered by a unit test.
- Readings with `ts_synced=false` count in summaries at the time the node reported.
