#!/bin/sh
# docker compose for this project, from anywhere, as the current account.
#   ./deploy/compose.sh up -d --build
#   ./deploy/compose.sh ps
# Sets STATION_UID/STATION_GID so files in data/ and logs/ belong to you.
set -eu
cd "$(dirname "$0")/.."
STATION_UID="$(id -u)" STATION_GID="$(id -g)" \
    exec docker compose -f deploy/docker-compose.yml "$@"
