# Balcony Station Receiver

Stores readings that the balcony ESP32 sensor node sends over HTTPS, and shows them
on a dashboard. It runs in Docker on the VPS, behind nginx, at
`https://station.carlosgorrichoai.one`.

- Build spec: [docs/build-spec.html](docs/build-spec.html)
- Milestones and decisions: [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md)

```
ESP32 node ──HTTPS──▶ nginx (certificate, rate limits, dashboard login)
                        │
                        ▼  127.0.0.1:8765
                      receiver container ──▶ data/station.db
```

Public paths: `POST /api/v1/readings` and `GET /api/v1/time` for the node, and
`/dashboard` behind a login. Everything else returns `404` from outside the VPS.

On the VPS, run every command below from the project folder,
`~/apps/balcony-station-receiver`. Commands that start with `sudo` change the
shared server, so check them before running them.

## Contents

- [Deploy](#deploy)
- [Update](#update)
- [Start, stop and status](#start-stop-and-status)
- [View the dashboard](#view-the-dashboard)
- [View the latest reading](#view-the-latest-reading)
- [Export to CSV](#export-to-csv)
- [Daily summary](#daily-summary)
- [Rotate the API key](#rotate-the-api-key)
- [Backups](#backups)
- [Restore a backup](#restore-a-backup)
- [Logs](#logs)
- [Remove the site](#remove-the-site)
- [Develop on the laptop](#develop-on-the-laptop)
- [Known limitations](#known-limitations)

## Deploy

Do this once. Before starting, finish the server checks in the
[build plan](docs/BUILD_PLAN.md) (milestone 10), including the DNS record for
`station.carlosgorrichoai.one`.

### 1. Get the code and create `.env`

```bash
git clone https://github.com/cgorricho/balcony-station-receiver.git ~/apps/balcony-station-receiver
```

```bash
cd ~/apps/balcony-station-receiver && cp .env.example .env && chmod 600 .env && mkdir -p data logs backups
```

Generate an API key:

```bash
python3 -c "import secrets; print(secrets.token_hex(16))"
```

Edit `.env`: put the key in `STATION_API_KEY`, and list your node IDs in
`STATION_KNOWN_NODES`. Leave the host, port, paths and
`STATION_FORWARDED_ALLOW_IPS` as they are, because the compose file sets them for
the container. `.env` never goes into git.

### 2. Check that the Docker network range is free

The container's network uses `172.30.87.0/24`. List the ranges already in use:

```bash
docker network inspect $(docker network ls -q) --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'
```

If `172.30.87.0/24` appears, or overlaps a range shown, pick a free `/24`, and
change both the subnet and the gateway in
[deploy/docker-compose.yml](deploy/docker-compose.yml), and
`STATION_FORWARDED_ALLOW_IPS` there to the new gateway.

### 3. Start the container

```bash
./deploy/compose.sh up -d --build
```

`deploy/compose.sh` runs `docker compose` with this project's compose file, as
your account, so files in `data/` and `logs/` belong to you. Check it's healthy:

```bash
./deploy/compose.sh ps
```

```bash
curl -s http://127.0.0.1:8765/api/v1/health
```

### 4. Install the nginx site (sudo)

This adds a new site and changes no other.

```bash
sudo cp deploy/nginx-station.conf /etc/nginx/sites-available/station.carlosgorrichoai.one
```

```bash
sudo ln -s /etc/nginx/sites-available/station.carlosgorrichoai.one /etc/nginx/sites-enabled/
```

Test the whole configuration before reloading, because other live sites share
this nginx. Reload only if the test passes:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Get the certificate (sudo)

```bash
sudo certbot --nginx -d station.carlosgorrichoai.one
```

When asked, choose to redirect HTTP to HTTPS. certbot edits the installed site
file to add HTTPS.

### 6. Create the dashboard login (sudo)

Do this after the certificate, so the password never crosses plain HTTP. The VPS
has no `htpasswd` tool, so this uses `openssl`, which asks for the password twice
and keeps it out of your shell history. Running it again replaces the login.

```bash
printf 'carlos:%s\n' "$(openssl passwd -6)" | sudo tee /etc/nginx/station.htpasswd >/dev/null
```

```bash
sudo chown root:www-data /etc/nginx/station.htpasswd && sudo chmod 640 /etc/nginx/station.htpasswd
```

### 7. Check from outside

From the laptop or a phone on mobile data:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://station.carlosgorrichoai.one/api/v1/time
```

That should print `200`. These should print `404`, `404` and `401`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://station.carlosgorrichoai.one/api/v1/health
```

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://station.carlosgorrichoai.one/docs
```

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://station.carlosgorrichoai.one/dashboard
```

Then open the dashboard and log in. Last, check that the receiver logs real client
addresses, not `172.30.87.1`. Send one request that fails, then look at the log's
last line:

```bash
curl -s -X POST https://station.carlosgorrichoai.one/api/v1/readings
```

```bash
tail -n 1 logs/receiver.log
```

The `client=` value should be your own public address. If it's `172.30.87.1`, the
trusted proxy address is wrong; see the build plan.

### 8. Add the nightly backup

See [Backups](#backups).

## Update

```bash
git pull
```

```bash
./deploy/compose.sh up -d --build
```

The readings stay in `data/`. If `git pull` changed `deploy/nginx-station.conf`,
install it again, then have certbot add HTTPS back, because the copy replaces
certbot's edits:

```bash
sudo cp deploy/nginx-station.conf /etc/nginx/sites-available/station.carlosgorrichoai.one
```

```bash
sudo certbot install --nginx --cert-name station.carlosgorrichoai.one
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## Start, stop and status

The container starts again by itself after a crash or a reboot. While it's
stopped, the node keeps its readings and sends them later.

| Task | Command |
|---|---|
| Status and health | `./deploy/compose.sh ps` |
| Stop | `./deploy/compose.sh stop` |
| Start | `./deploy/compose.sh start` |
| Restart | `./deploy/compose.sh restart` |
| Container output | `./deploy/compose.sh logs --tail 50` |

## View the dashboard

Open `https://station.carlosgorrichoai.one/dashboard` and log in. It shows the
latest reading, warnings for readings that look wrong, charts and a daily
summary, and refreshes every minute.

You can add these to the address:

| Option | Meaning |
|---|---|
| `?days=14` | Days shown, 1 to 31 (default 7) |
| `?node=balcony-02` | Which node (default: the first known node with data) |
| `?refresh=30` | Seconds between refreshes, at least 5 (default 60) |

For example, `/dashboard?days=14&refresh=30`.

## View the latest reading

This works on the VPS only. The key is read from `.env`, so it stays out of your
shell history. It goes in a header; a key in the URL is refused.

```bash
curl -s -H "X-Station-Key: $(grep '^STATION_API_KEY=' .env | cut -d= -f2-)" "http://127.0.0.1:8765/api/v1/latest?node_id=balcony-01"
```

## Export to CSV

One row per reading, one column per metric, plus `*_f` columns for temperatures.
Dates are local days, and both ends are included. The script runs inside the
container and the CSV is written on the VPS:

```bash
./deploy/compose.sh exec -T receiver python scripts/export_csv.py --from 2026-01-01 --to 2026-01-31 > january.csv
```

Leave out `--from` and `--to` to export everything; add `--node` for one node. To
copy it to the laptop, run this on the laptop, replacing `VPS` with your SSH host:

```bash
scp VPS:apps/balcony-station-receiver/january.csv .
```

## Daily summary

Min/max temperatures, hours below 45°F, DLI, the sun window and data coverage for
each day:

```bash
./deploy/compose.sh exec receiver python scripts/daily_summary.py --from 2026-01-01
```

## Rotate the API key

Do this if the key may have leaked, or if a node is lost or stolen.

1. Generate a new key:

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(16))"
   ```

2. Replace `STATION_API_KEY` in `.env`.
3. Recreate the container so it reads the new key:

   ```bash
   ./deploy/compose.sh up -d --force-recreate
   ```

4. Put the new key on the node. Until then, the node gets `401` and keeps
   buffering, so nothing is lost.

## Backups

`scripts/backup_db.py` copies the database safely while the receiver runs, checks
the copy, and keeps the 14 newest daily copies in `backups/`. It uses the VPS's own
`python3`.

Run it once by hand:

```bash
python3 scripts/backup_db.py
```

Then add it to your crontab (`crontab -e`) to run nightly at 03:15:

```
15 3 * * * cd $HOME/apps/balcony-station-receiver && python3 scripts/backup_db.py >> logs/backup.log 2>&1
```

A copy on the VPS doesn't survive losing the server, so keep one somewhere else
too. Where is still an open decision in the build plan. Until then, copy the
newest one to the laptop now and then (on the laptop, replacing `VPS` with your
SSH host):

```bash
scp "VPS:apps/balcony-station-receiver/backups/station-*.db" .
```

## Restore a backup

1. Stop the receiver. The node keeps buffering while it's down.

   ```bash
   ./deploy/compose.sh stop
   ```

2. Move the current database aside (keep it until you're sure):

   ```bash
   mkdir -p data/replaced && mv data/station.db* data/replaced/
   ```

3. Copy the backup in, replacing the date with the one you want:

   ```bash
   cp backups/station-2026-01-31.db data/station.db
   ```

4. Start it and check:

   ```bash
   ./deploy/compose.sh start
   ```

   ```bash
   curl -s http://127.0.0.1:8765/api/v1/health
   ```

Readings received after the backup was taken are missing from it. If the node
still holds them in its buffer, it sends them again.

## Logs

| Log | Where |
|---|---|
| Receiver: each batch and every error, API key redacted | `logs/receiver.log` (rotates at 1 MB, keeps 5) |
| nginx: requests and errors for this site | `/var/log/nginx/station.access.log`, `/var/log/nginx/station.error.log` |
| Container output | `./deploy/compose.sh logs` (capped at 15 MB) |
| Backups | `logs/backup.log` |

## Remove the site

This takes the site down for good. Back up `data/` first if you might want the
readings.

1. Take the site out of nginx (sudo):

   ```bash
   sudo rm /etc/nginx/sites-enabled/station.carlosgorrichoai.one /etc/nginx/sites-available/station.carlosgorrichoai.one /etc/nginx/station.htpasswd
   ```

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

2. Delete the certificate (sudo):

   ```bash
   sudo certbot delete --cert-name station.carlosgorrichoai.one
   ```

3. Remove the container, its network and its image:

   ```bash
   ./deploy/compose.sh down --rmi all
   ```

4. Remove the backup line from your crontab (`crontab -e`).
5. Remove the DNS record for `station.carlosgorrichoai.one`.
6. When you no longer need the readings, delete `~/apps/balcony-station-receiver`.

## Develop on the laptop

### Setup and tests

From the project folder (`.env` holds a local key and stays out of git; copy it
from `.env.example` if it's missing):

```bash
uv sync
```

```bash
uv run pytest
```

### Run the receiver

```bash
uv run python -m receiver
```

Always start it this way, not with `uvicorn` directly: this turns off uvicorn's
access log and its proxy-header handling, which the app does itself. The API docs
pages (`/docs`, `/redoc`, `/openapi.json`) are off; the API is described in the
[spec](docs/build-spec.html).

### See the dashboard in action

The signal simulator starts its own receiver on `127.0.0.1:8766` with a scratch
database (`data/simulator.db`), so real readings are never touched:

```bash
uv run python scripts/simulate.py
```

It opens the dashboard, fills in three days of history (with a frost night and a
3-hour outage), then plays the last day forward, one reading every 2 seconds. At
readings 12, 24, 36 and 48 it injects a sensor glitch, an unknown node, a wrong
clock and a `seq` jump, so each warning appears. Ctrl+C stops it. Useful flags:
`--tick` (seconds per reading), `--history-days`, `--no-anomalies`, `--port 0`
(any free port).

### Send fake readings

`fake_node.py` behaves like the node: batches, resends, gaps, and waiting when
rate limited. With the receiver running, this pushes a simulated day into the
database `.env` points at:

```bash
uv run python scripts/fake_node.py --gap 6 --resend
```

`--url` sets the receiver's address (default `http://127.0.0.1:8765`). The public
address hides `/latest`, so there use a throwaway `--node` and `--first-seq 1`,
and remove the test data afterwards.

### Check the deployment files

With Docker running, this builds the image, then runs the real compose file and
the nginx site on a scratch folder. It checks the container hardening, the public
paths, an upload through nginx, the logs and a backup, then removes everything. It
never touches `data/` or the VPS.

```bash
docker compose -f deploy/docker-compose.yml build
```

```bash
uv run python deploy/local_stack_test.py
```

## Known limitations

- If the node's `seq` ever restarts, for example after a reflash, new readings are
  treated as duplicates, acknowledged and lost. Keep `seq` across reflashes.
- Anyone with the API key can send readings with `seq` numbers the node hasn't
  used yet, so the node's real readings are later dropped as duplicates. The
  dashboard's warnings show this, and rotating the key stops it.
- Readings with `ts_synced=false` count in summaries at the time the node reported.
