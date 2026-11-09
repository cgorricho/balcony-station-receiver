# Balcony Station receiver. Build from the repository root:
#   docker compose -f deploy/docker-compose.yml build

FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.14-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.14-slim-trixie
# A fixed non-root user. Compose may run the container as another non-root
# UID so files in the mounted folders belong to the VPS account.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin station
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY src ./src
COPY scripts ./scripts
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATION_HOST=0.0.0.0 \
    STATION_PORT=8765 \
    STATION_DB_PATH=/app/data/station.db \
    STATION_LOG_DIR=/app/logs
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=4)"]
CMD ["python", "-m", "receiver"]
