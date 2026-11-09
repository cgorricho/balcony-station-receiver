"""Local check of the deployment files (milestone 8). Needs Docker.

Runs the real docker-compose.yml with the built image, plus an nginx container
using nginx-station.conf, all on a scratch folder. Then checks the container
hardening, the public paths, a fake node's upload, the logs and a backup.
Everything is removed at the end.

    docker compose -f deploy/docker-compose.yml build
    uv run python deploy/local_stack_test.py

The real nginx proxies to 127.0.0.1:8765 on the VPS; here the nginx container
reaches the receiver by name instead, and is published on 127.0.0.1:18080.
"""

import base64
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
PROJECT = "station-smoke"
NGINX = "http://127.0.0.1:18080"
USER, PASSWORD = "carlos", secrets.token_urlsafe(12)
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{f'  ({detail})' if detail and not ok else ''}")
    if not ok:
        failures.append(name)


def run(*args: str, check_rc: bool = True, **kw) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if check_rc and result.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def request(path: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None,
            auth: bool = False) -> tuple[int, str]:
    headers = dict(headers or {})
    if auth:
        token = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(NGINX + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="station-smoke-"))
    key = secrets.token_hex(16)
    (scratch / "deploy").mkdir()
    (scratch / "data").mkdir()
    (scratch / "logs").mkdir()
    (scratch / ".env").write_bytes(
        f"STATION_API_KEY={key}\nSTATION_TZ=America/New_York\nSTATION_KNOWN_NODES=smoke-test\n".encode()
    )
    # nginx container: the site template, pointed at the receiver by name.
    site = (DEPLOY / "nginx-station.conf").read_text(encoding="utf-8")
    site = site.replace("server 127.0.0.1:8765;", "server receiver:8765;")
    (scratch / "deploy" / "station.conf").write_bytes(site.encode())
    (scratch / "deploy" / "station.htpasswd").write_bytes(f"{USER}:{{PLAIN}}{PASSWORD}\n".encode())
    override = {
        "services": {
            "receiver": {
                "pull_policy": "never",
                # nginx here reaches the receiver from its own address on the
                # network, not from the gateway as on the VPS.
                "environment": {"STATION_FORWARDED_ALLOW_IPS": "172.30.87.0/24"},
            },
            "nginx": {
                "image": "nginx:1.28-alpine",
                "ports": ["127.0.0.1:18080:80"],
                "volumes": [
                    f"{(scratch / 'deploy' / 'station.conf').as_posix()}:/etc/nginx/conf.d/default.conf:ro",
                    f"{(scratch / 'deploy' / 'station.htpasswd').as_posix()}:/etc/nginx/station.htpasswd:ro",
                ],
                "depends_on": ["receiver"],
                "networks": ["station"],
            },
        }
    }
    (scratch / "deploy" / "override.yml").write_bytes(json.dumps(override, indent=2).encode())
    compose = [
        "docker", "compose", "-p", PROJECT, "--project-directory", str(scratch / "deploy"),
        "-f", str(DEPLOY / "docker-compose.yml"), "-f", str(scratch / "deploy" / "override.yml"),
    ]

    try:
        print("Starting the stack")
        run(*compose, "up", "-d", "--no-build")
        for _ in range(60):
            state = run(*compose, "ps", "--format", "{{.Service}} {{.Health}}").stdout
            if "receiver healthy" in state:
                break
            time.sleep(1)
        check("receiver container reports healthy", "receiver healthy" in state, state.strip())

        print("Container hardening")
        exec_ = [*compose, "exec", "-T", "receiver"]
        uid = run(*exec_, "id", "-u").stdout.strip()
        check("runs as a non-root user", uid not in ("", "0"), uid)
        write = run(*exec_, "sh", "-c", "touch /app/x", check_rc=False)
        check("root filesystem is read-only", write.returncode != 0)
        tmp = run(*exec_, "sh", "-c", "touch /tmp/x && rm /tmp/x", check_rc=False)
        check("/tmp is writable", tmp.returncode == 0, tmp.stderr)
        caps = run(*exec_, "sh", "-c", "grep CapEff /proc/self/status").stdout.split()[-1]
        check("all capabilities dropped", int(caps, 16) == 0, caps)
        nnp = run(*exec_, "sh", "-c", "grep NoNewPrivs /proc/self/status").stdout.split()[-1]
        check("no-new-privileges set", nnp == "1", nnp)
        ports = run("docker", "port", f"{PROJECT}-receiver-1").stdout.strip()
        check("port published on 127.0.0.1 only", ports == "8765/tcp -> 127.0.0.1:8765", ports)

        print("Public paths through nginx")
        for path in ["/docs", "/redoc", "/openapi.json", "/api/v1/health", "/api/v1/latest", "/", "/.env"]:
            status, _ = request(path)
            check(f"{path} is 404", status == 404, str(status))
        check("/dashboard needs the login", request("/dashboard")[0] == 401)
        check("/dashboard/data needs the login", request("/dashboard/data")[0] == 401)
        check("/dashboard with the login is 200", request("/dashboard", auth=True)[0] == 200)
        check("/dashboard/data with the login is 200", request("/dashboard/data", auth=True)[0] == 200)
        check("POST /dashboard/data is refused", request("/dashboard/data", "POST", b"{}", auth=True)[0] == 403)
        check("GET /api/v1/time is 200", request("/api/v1/time")[0] == 200)
        check("POST /api/v1/time is refused", request("/api/v1/time", "POST", b"")[0] == 403)
        check("GET /api/v1/readings is refused", request("/api/v1/readings")[0] == 403)
        status, _ = request("/api/v1/readings", "POST", b"{}", {"Content-Type": "application/json"})
        check("readings without the key is 401", status == 401, str(status))
        big = b"x" * (1024 * 1024 + 1)
        status, _ = request("/api/v1/readings", "POST", big, {"X-Station-Key": key})
        check("a body over 1 MB is 413", status == 413, str(status))

        print("Fake node: 24 h with a resend and a 6 h gap")
        node = run(
            sys.executable, str(ROOT / "scripts" / "fake_node.py"), "--url", NGINX, "--key", key,
            "--node", "smoke-test", "--hours", "24", "--gap", "6", "--resend", "--first-seq", "1",
            check_rc=False, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        check("fake node finished", node.returncode == 0, node.stderr[-400:])
        summary = run(*exec_, "python", "scripts/daily_summary.py", "--node", "smoke-test", check_rc=False)
        check("daily_summary runs in the container", summary.returncode == 0 and "Coverage" in summary.stdout,
              summary.stderr[-300:] or summary.stdout[-300:])
        status, body = request("/dashboard/data?node=smoke-test&days=7", auth=True)
        points = sum(y is not None for y in json.loads(body)["series"][0]["y"]) if status == 200 else 0
        check("dashboard shows the uploaded readings", points > 100, f"{points} points")

        # Before the rate-limit burst, which would turn this into a 429.
        # A refused request is logged with the client address nginx saw.
        for _ in range(10):  # the fake node's upload may have used up the burst
            spoof = request("/api/v1/readings", "POST", b"{}",
                            {"Content-Type": "application/json", "X-Forwarded-For": "6.6.6.6"})[0]
            if spoof != 429:
                break
            time.sleep(3)
        time.sleep(0.5)
        app_logs = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (scratch / "logs").glob("*"))
        last = [line for line in app_logs.splitlines() if "status=401" in line][-1:]
        check("a forged X-Forwarded-For is not logged", spoof == 401 and last and "6.6.6.6" not in app_logs,
              " | ".join(line for line in app_logs.splitlines() if "6.6.6.6" in line)[:300])
        print(f"       logged as: {last[0].split('client=')[1].split()[0] if last else '?'}")

        print("Rate limit")
        codes = [request("/api/v1/time")[0] for _ in range(40)]
        check("bursts beyond the limit get 429", 429 in codes, str(sorted(set(codes))))

        print("Logs")
        time.sleep(1)
        app_logs = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (scratch / "logs").glob("*"))
        nginx_logs = run(*compose, "logs", "nginx").stdout + run(*compose, "logs", "receiver").stdout
        check("receiver wrote its log to the mounted folder", "batch: node='smoke-test'" in app_logs)
        check("API key in no log", key not in app_logs and key not in nginx_logs)
        check("password in no log", PASSWORD not in app_logs and PASSWORD not in nginx_logs)

        print("Backup and restore")
        db = scratch / "data" / "station.db"
        live = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        expected = live.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        live.close()
        backup = run(sys.executable, str(ROOT / "scripts" / "backup_db.py"), "--db", str(db),
                     "--dest", str(scratch / "backups"), check_rc=False)
        check("backup while the receiver runs", backup.returncode == 0, backup.stderr)
        copy = next((scratch / "backups").glob("station-*.db"), None)
        if copy:
            restored = sqlite3.connect(copy)
            rows = restored.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
            dupes = restored.execute(
                "SELECT COUNT(*) FROM (SELECT node_id, seq FROM readings GROUP BY 1, 2 HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            restored.close()
            check("restored copy holds the expected rows, no duplicates", rows == expected > 0 and dupes == 0,
                  f"{rows} vs {expected}, {dupes} dupes")

        print("Restart keeps the data")
        run(*compose, "restart", "receiver")
        time.sleep(3)
        status, body = request("/dashboard/data?node=smoke-test", auth=True)
        check("data survives a restart", status == 200 and json.loads(body)["latest"] is not None, str(status))
    finally:
        print("Removing the stack")
        run(*compose, "down", "--volumes", "--remove-orphans", check_rc=False)
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n{'All checks passed' if not failures else f'{len(failures)} failed: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
