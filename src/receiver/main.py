"""FastAPI app. Start it with: python -m receiver"""

import logging
import secrets
import sqlite3
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import db
from .dashboard import create_router
from .config import Settings, load_settings
from .logging_setup import configure_logging
from .models import (
    MAX_TEXT,
    HealthResponse,
    IngestResponse,
    LatestResponse,
    ReadingBatch,
    TimeResponse,
)

logger = logging.getLogger("receiver")


def _safe(value: object) -> str:
    return str(value).encode("utf-8", "replace").decode("utf-8")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # The default handler echoes the input, which fails to render for NaN or
    # invalid unicode. Report only where and why the body was rejected.
    detail = [
        {
            "loc": [_safe(part) for part in error["loc"]],
            "msg": _safe(error["msg"]),
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    logger.warning(
        "invalid request: client=%s path=%s errors=%d first=%r",
        _client_ip(request),
        request.url.path,
        len(detail),
        detail[0] if detail else None,
    )
    return JSONResponse(status_code=422, content={"detail": detail})


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # FastAPI answers an undecodable body with 400; the API contract says 422.
    status_code = 422 if exc.status_code == 400 else exc.status_code
    logger.warning(
        "request failed: status=%d client=%s path=%s",
        status_code,
        _client_ip(request),
        request.url.path,
    )
    return JSONResponse(
        status_code=status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled error: client=%s path=%s",
        _client_ip(request),
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(status_code=500, content={"detail": "internal error"})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings)
    db.init_db(settings.db_path)

    # No /docs, /redoc or /openapi.json: the service is public, and its API is
    # documented in the spec.
    app = FastAPI(
        title="Balcony Station Receiver",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    if settings.forwarded_allow_ips:
        # Behind nginx every request comes from the proxy. Take the client IP from
        # X-Forwarded-For, but only when the proxy is one we trust.
        trusted = [ip.strip() for ip in settings.forwarded_allow_ips.split(",") if ip.strip()]
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)

    def check_key(supplied: str | None) -> None:
        supplied_bytes = (supplied or "").encode("utf-8", "replace")
        if not secrets.compare_digest(supplied_bytes, settings.api_key.encode("utf-8")):
            raise HTTPException(status_code=401, detail="invalid API key")

    def require_key(x_station_key: str | None = Header(default=None)) -> None:
        check_key(x_station_key)

    @app.post(
        "/api/v1/readings",
        response_model=IngestResponse,
        dependencies=[Depends(require_key)],
    )
    def post_readings(batch: ReadingBatch, request: Request) -> IngestResponse:
        stored, duplicates, ack_seq = db.store_batch(
            settings.db_path, batch, received_at=int(time.time())
        )
        logger.info(
            "batch: node=%r stored=%d duplicates=%d ack_seq=%s client=%s",
            batch.node_id,
            stored,
            duplicates,
            ack_seq,
            _client_ip(request),
        )
        return IngestResponse(
            stored=stored,
            duplicates=duplicates,
            ack_seq=ack_seq,
            server_ts=int(time.time()),
        )

    @app.get("/api/v1/time", response_model=TimeResponse)
    def get_time() -> TimeResponse:
        return TimeResponse(server_ts=int(time.time()))

    @app.get("/api/v1/health", response_model=HealthResponse)
    def get_health() -> HealthResponse | JSONResponse:
        try:
            last_reading_at, nodes = db.health_info(settings.db_path)
        except sqlite3.Error:
            logger.exception("health check could not read the database")
            unhealthy = HealthResponse(status="error", db="error", last_reading_at=None, nodes=[])
            return JSONResponse(status_code=503, content=unhealthy.model_dump())
        return HealthResponse(
            status="ok", db="ok", last_reading_at=last_reading_at, nodes=nodes
        )

    @app.get(
        "/api/v1/latest",
        response_model=LatestResponse,
        dependencies=[Depends(require_key)],
    )
    def get_latest(node_id: str = Query(min_length=1, max_length=MAX_TEXT)) -> LatestResponse:
        latest = db.latest_reading(settings.db_path, node_id)
        if latest is None:
            raise HTTPException(status_code=404, detail="no readings for this node")
        return LatestResponse(**latest)

    app.include_router(create_router(settings))

    return app
