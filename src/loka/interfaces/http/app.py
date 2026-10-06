"""FastAPI application factory.

Controllers stay thin: routing, authentication and serialisation only. Any
heavy work is published to the broker and executed by a worker.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from loka.interfaces.http.auth import BearerTokenAuthenticator
from loka.interfaces.http.container import Container
from loka.interfaces.http.errors import register_error_handlers
from loka.interfaces.http.routers import properties as properties_router
from loka.shared.application.context import (
    new_id,
    reset_request_id,
    set_request_id,
)
from loka.shared.infrastructure import metrics
from loka.shared.infrastructure.config.settings import Settings, get_settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import configure_logging, get_logger
from loka.shared.infrastructure.redis_client import build_redis, redis_healthcheck

SERVICE_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(
        level=resolved.observability.log_level,
        json_output=resolved.observability.log_json,
    )
    logger = get_logger("http")

    database = Database(resolved.database, application_name=f"{resolved.service_name}-api")
    container = Container(
        settings=resolved,
        database=database,
        authenticator=BearerTokenAuthenticator(database),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await database.connect()
        redis = await build_redis(
            resolved.redis_url, max_connections=resolved.redis_max_connections
        )
        container.redis = redis
        logger.info(
            "api_started",
            environment=resolved.environment,
            version=SERVICE_VERSION,
        )
        try:
            yield
        finally:
            await database.dispose()
            await redis.aclose()
            logger.info("api_stopped")

    app = FastAPI(
        title="Loka API",
        version=SERVICE_VERSION,
        docs_url=None if resolved.is_production else "/docs",
        lifespan=lifespan,
    )
    app.state.container = container

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or new_id()
        token = set_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - started
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            metrics.http_requests_total.labels(
                method=request.method, route=route_path, status="500"
            ).inc()
            metrics.http_latency_seconds.labels(
                method=request.method, route=route_path
            ).observe(elapsed)
            logger.exception("request_failed", path=request.url.path)
            raise
        finally:
            reset_request_id(token)

        elapsed = time.perf_counter() - started
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        metrics.http_requests_total.labels(
            method=request.method, route=route_path, status=str(response.status_code)
        ).inc()
        metrics.http_latency_seconds.labels(
            method=request.method, route=route_path
        ).observe(elapsed)
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", error=str(exc))
        return JSONResponse(status_code=500, content={"code": "internal_error"})

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok", "version": SERVICE_VERSION}

    @app.get("/health/ready", include_in_schema=False)
    async def ready(request: Request) -> Response:
        checks = {"database": await database.healthcheck()}
        redis = request.app.state.container.redis
        checks["redis"] = (
            await redis_healthcheck(redis) if redis is not None else False
        )
        ready_state = all(checks.values())
        return JSONResponse(
            status_code=200 if ready_state else 503,
            content={"status": "ready" if ready_state else "degraded", "checks": checks},
        )

    register_error_handlers(app, logger=logger)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(content=metrics.render(), media_type="text/plain")

    app.include_router(properties_router.router)

    @app.get("/", include_in_schema=False)
    async def root() -> PlainTextResponse:
        return PlainTextResponse("loka - WhatsApp-first real estate platform")

    return app


def _redis_url(settings: Settings) -> str:
    import os

    return os.getenv("REDIS_URL", "redis://localhost:6379/0")