"""FastAPI application factory: ``uvicorn avt.api.main:app``."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
import uvicorn
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from avt import __version__
from avt.api.routers import auth, brands, dashboard, health, prompts, runs, workspaces
from avt.config import Settings, get_settings
from avt.db.base import Database
from avt.logging import configure_logging, get_logger
from avt.resilience.backoff import RetryBudget

log = get_logger(__name__)

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db = Database(settings)
        app.state.pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        app.state.budget = RetryBudget(
            app.state.pool, ratio=settings.retry_budget_ratio, floor=settings.retry_budget_floor
        )
        log.info("api.started", version=__version__)
        yield
        await app.state.pool.aclose()
        await app.state.db.dispose()

    app = FastAPI(
        title="AI Visibility Tracker",
        version=__version__,
        description=(
            "Track how AI assistants (Claude, GPT, Gemini, Perplexity) mention your brand "
            "versus competitors: share of voice, position, sentiment and cited sources."
        ),
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        if request.url.path not in ("/healthz", "/metrics"):
            log.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        return response

    app.include_router(health.router)
    for router in (auth, workspaces, brands, prompts, runs, dashboard):
        app.include_router(router.router, prefix=API_PREFIX)
    return app


app = create_app()


def run() -> None:
    uvicorn.run("avt.api.main:app", host="0.0.0.0", port=8000, log_config=None)  # noqa: S104
