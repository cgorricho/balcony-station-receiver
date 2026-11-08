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
| 6 | Production hardening: no `?key=`, no `/docs`, forwarded IPs, `fake_node.py --url`, remove Windows scripts | Next | Claude |
| 7 | Dashboard at `/dashboard` | Not started | Claude |
| 8 | Packaging: Dockerfile, compose, nginx template, backups | Not started | Claude |
| 9 | README rewritten for the VPS | Not started | Claude |
| 10 | Server readiness | Not started | Carlos and Claude |
| 11 | Deploy | Not started | Carlos and Claude |
| 12 | End-to-end over the real HTTPS address | Not started | Claude |

The earlier Windows integration milestone is retired. Its scripts stay in the repo until
milestone 6 removes them.

## Milestone 10: server readiness

- [ ] Confirm who else can log in to the VPS
- [ ] Check `ufw` rules (needs `sudo`)
- [ ] Check from outside which Docker-published ports are reachable, and close any that shouldn't be
- [ ] Add the DNS record for `station.carlosgorrichoai.one`

## Milestone 11: deploy

Carlos runs the steps that need `sudo`.

- [ ] Clone the repo and create `.env` on the VPS with a new API key
- [ ] Start the container
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
| `python -m receiver` is the only supported launch | It turns off the access log |
| Log redacts the key as well | A traceback must not leak it |

## Known gaps

- `seq` reset: a restarted count is treated as duplicates and lost, until the `boot_id` decision above.
- `--gap` in `fake_node.py` simulates the server being unreachable, so no data is missing. The
  30-minute cap for real data loss is covered by a unit test.
- Readings with `ts_synced=false` count in summaries at the time the node reported.
- The code has only been run on Windows so far. Milestone 8 runs it on Linux.
- Crash recovery is untested. Milestone 8 covers `restart: unless-stopped`.
