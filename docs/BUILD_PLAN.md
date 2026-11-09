# Build Plan

This tracks the milestones from Section 9 of the spec.

- Local copy of the spec: [build-spec.html](build-spec.html)
- Published spec: <https://claude.ai/artifact/1hhDzc43MpVKkbjUnsz3Sg>

The spec is the source of truth. This file records status and decisions it leaves open.

**Plan changed on 2026-11-07:** the receiver moves from the Windows laptop to the VPS.
Readings now travel over HTTPS, and the spec adds a dashboard. The hardware build is
postponed about two weeks, but milestones 6 to 9 don't depend on it.

## Milestones

| # | Milestone | Status | Who |
|---|---|---|---|
| 1 | Scaffold | Done | Claude |
| 2 | Ingest: `POST /readings` plus storage | Done | Claude |
| 3 | Other endpoints: `/time`, `/health`, `/latest` | Done | Claude |
| 4 | Derived values and scripts | Done | Claude |
| 5 | Logging: rotating file log with the API key redacted | Done | Claude |
| 6 | Production hardening: no `?key=`, no `/docs`, forwarded IPs, `fake_node.py --url`, remove Windows scripts | Done | Claude |
| 7 | Dashboard at `/dashboard`, with warnings for readings that look wrong, plus the `simulate.py` signal simulator | Done | Claude |
| 8 | Packaging: Dockerfile, compose, nginx template, backups; container runs non-root and read-only | Done | Claude |
| 9 | README rewritten for the VPS | Next | Claude |
| 10 | Server readiness | Not started | Carlos and Claude |
| 11 | Deploy | Not started | Carlos and Claude |
| 12 | End-to-end over the real HTTPS address | Not started | Claude |

The earlier Windows integration milestone is retired. Milestone 6 removed its scripts; they
remain in git history.

## Milestone 10: server readiness

- [ ] Confirm who else can log in to the VPS
- [ ] Check `ufw` rules (needs `sudo`)
- [ ] Check from outside which Docker-published ports are reachable, and close any that shouldn't be
- [ ] Add the DNS record for `station.carlosgorrichoai.one`

## Milestone 11: deploy

Carlos runs the steps that need `sudo`.

- [ ] Clone the repo and create `.env` on the VPS with a new API key
- [ ] Check that `172.30.87.0/24` is free (`docker network inspect` on the existing networks), then start the container with `STATION_UID`/`STATION_GID` set
- [ ] Install the nginx site and test with `nginx -t` before reloading
- [ ] Issue the certificate with certbot
- [ ] Create the dashboard login
- [ ] Add the nightly backup, with one copy off the VPS
- [ ] Open the dashboard from a phone on mobile data

## Open decisions

| Decision | Options | Needed by |
|---|---|---|
| `seq` reset protection | Add `boot_id` sent by the node and stored in the key, or accept the risk. Recommended: add it | Before firmware starts |
| Where the off-VPS backup goes | Not chosen | Milestone 11 |
| ESP32 flash encryption | Turn it on so a stolen node doesn't easily give up its key, or leave it off so reflashing stays easy | Before the node is deployed |

## Security

Added 2026-11-08 (spec version 7). The receiver's protection depends on the firmware too.

| Item | Where | Status |
|---|---|---|
| Firmware checks the server certificate against built-in Let's Encrypt roots (ISRG Root X1 and X2) and the hostname, and never turns checking off | Spec 5.5, firmware project | Required, before firmware starts |
| Container runs as a non-root user, read-only except `data/`, `logs/` and `/tmp`, no Linux capabilities, `no-new-privileges` | Spec section 8, milestone 8 | Done, checked by `deploy/local_stack_test.py` |
| Dashboard warns about readings that look wrong: out-of-range values, `seq` jumps, node clock ahead of the server or running backwards, unknown `node_id` | Spec 6.3, milestone 7 | Done |
| ESP32 flash encryption | Spec 5.5 | Open decision above |

If a node is lost or stolen, rotate the API key.

## Decisions

| Decision | Why |
|---|---|
| Run on the VPS, not the laptop | It's always on, and it avoids company IT limits, firewall changes and router setup on the laptop |
| Docker container bound to `127.0.0.1`, behind the existing nginx | Same pattern as the other sites on the server, and the project needs newer Python than the server has |
| One subdomain, `station.carlosgorrichoai.one` | Keeps this site separate from the others |
| Only `POST /readings` and `GET /time` are public, plus the dashboard behind a login | Keeps exposure small |
| Removed `?key=` | nginx logs full URLs, and the app's redaction doesn't cover nginx |
| Dashboard is a Plotly page in the receiver, not Grafana | One service to run and update |
| Node uploads about hourly | HTTPS costs more battery, and the node already buffers |
| Added required `oldest_buffered_seq`; `ack_seq` is the unbroken run from it | "Highest seq in the batch" could delete unsent readings, and a lost reading could otherwise stall acks |
| `/latest` picks the highest `seq`, not the newest `ts` | The node's clock may be unsynced |
| `/health` uses the server's `received_at` | A wrong node clock can't distort it |
| Error responses never echo the input | The default echo can fail on `NaN` or invalid unicode |
| Undecodable bodies return `422`, not FastAPI's `400` | The contract says malformed bodies return `422` |
| Added the `tzdata` dependency | The slim container image has no timezone database, like Windows |
| `python -m receiver` is the only supported launch | It turns off uvicorn's access log and proxy-header handling |
| Forwarded client IPs handled in the app, trusted only from `STATION_FORWARDED_ALLOW_IPS` (default `127.0.0.1`, empty trusts no one) | Testable without a real proxy, and an outside sender can't fake its address in the log |
| `fake_node.py --first-seq` skips the `/latest` lookup | The public address hides `/latest`, and milestone 12 runs through it |
| Clock warnings flag a node clock ahead of the server, or time running backwards between seqs, not "`ts` more than a day from `received_at`" | The original rule flagged every reading a node buffered during an outage and sent late |
| Plotly's JavaScript comes from the `plotly` package (7.1, plotly.js 4.1.1) and is served by the app | The spec rules out a third-party CDN, and PyPI is the project's normal dependency source |
| Dashboard JS and CSS are separate files under a strict Content-Security-Policy, and data is inserted as text only | A stored metric name or node ID can't run as script in the browser |
| Dashboard has no API key; nginx's login guards all of `/dashboard` | Spec 6.3. Locally it's only on `127.0.0.1` |
| Chart window ends at the node's newest reading (ignoring clocks more than a day ahead), not at today | Old data still shows, and a bad clock can't drag the window away |
| `simulate.py` runs its own receiver on `127.0.0.1:8766` with `data/simulator.db` | It can never write to the real database |
| Log redacts the key as well | A traceback must not leak it |
| Image: `python:3.14-slim-trixie` with `uv` 0.12.19, dependencies from `uv.lock`; source, scripts and the venv only | Same Python and locked versions as development; `.dockerignore` keeps `.env`, `data/` and `logs/` out |
| Compose sets the container's host, port, paths and trusted proxy, overriding `.env` | The container layout can't be misconfigured from `.env` |
| Fixed Docker network `172.30.87.0/24`; the app trusts forwarded IPs from its gateway `172.30.87.1` only | nginx on the host reaches the published port through that gateway. A fixed address is checkable; a clash fails loudly at `docker compose up` |
| Container runs as `STATION_UID:STATION_GID` (default 1000), not the image's own UID 10001 | Files in `data/` and `logs/` belong to Carlos's account, so his cron backup can read them |
| Memory, CPU, process and Docker log limits | A fault here can't starve the other sites on the VPS |
| nginx: 30 requests a minute per address, burst 20, on the node's paths; 120 a minute on the dashboard; other methods `403`; everything else `404` | Spec 5. Zone names start with `station_` so they can't clash with other sites |
| nginx replaces `X-Forwarded-For` with the connecting address instead of appending | A client can't put a forged address in the receiver's log |
| Dashboard password file is created after the certificate | The password never crosses plain HTTP |
| The node waits and resends on `429` (spec 5.1); `fake_node.py` does the same | A long catch-up after an outage must not lose its place |
| `backup_db.py` is standard library only and Python 3.10 compatible, and checks each copy's integrity | The VPS's own `python3` runs it from cron, outside the container |

## Known gaps

- `seq` reset: a restarted count is treated as duplicates and lost, until the `boot_id` decision above.
- Anyone holding the API key can send readings with `seq` numbers the node hasn't used yet, so its real readings are later dropped as duplicates. The dashboard warnings make this visible, and rotating the key stops it. `boot_id` alone doesn't prevent it.
- `--gap` in `fake_node.py` simulates the server being unreachable, so no data is missing. The
  30-minute cap for real data loss is covered by a unit test.
- Readings with `ts_synced=false` count in summaries at the time the node reported.
- `deploy/local_stack_test.py` runs on Docker Desktop, where nginx is a container on the receiver's network. On the VPS, nginx runs on the host, so the trusted gateway address is first confirmed in milestone 11 (the receiver's log should show real client addresses, not `172.30.87.1`).
- A restart keeps the data (checked). A crash mid-write relies on SQLite's journal and isn't tested separately.
- Time running backwards is only checked within the chart window, so it's missed if the earlier reading is outside it.
- An out-of-range value stays in the charts and daily summary, so one glitch can stretch the temperature axis. The warning explains it.
