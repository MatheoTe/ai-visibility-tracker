"""Integration fixtures: real Postgres + Redis, real arq worker (burst mode), fake providers.

Point them at disposable services::

    AVT_TEST_DATABASE_URL=postgresql+asyncpg://avt:avt@localhost:5432/avt_test
    AVT_TEST_REDIS_URL=redis://localhost:6379/15

Tests are skipped when the variables are not set.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from arq.connections import RedisSettings
from arq.worker import Worker
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from avt.api.main import create_app
from avt.config import Settings
from avt.db.base import Base, Database
from avt.worker.main import AnalysisWorkerSettings, WorkerSettings, shutdown, startup

DB_URL = os.environ.get("AVT_TEST_DATABASE_URL")
REDIS_URL = os.environ.get("AVT_TEST_REDIS_URL")

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if DB_URL and REDIS_URL:
        return
    skip = pytest.mark.skip(reason="AVT_TEST_DATABASE_URL / AVT_TEST_REDIS_URL not set")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "database_url": DB_URL,
        "redis_url": REDIS_URL,
        "force_fake_providers": True,
        "fake_latency_ms_min": 0,
        "fake_latency_ms_max": 5,
        "fake_error_rate": 0.0,
        "backoff_base_seconds": 0.05,
        "backoff_max_seconds": 0.2,
        "breaker_min_requests": 1000,
        "worker_metrics_port": 0,
        "worker_max_jobs": 50,
        "log_json": False,
        "log_level": "WARNING",
        "jwt_secret": "test-secret-test-secret-test-secret",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return make_settings()


@pytest.fixture(scope="session", autouse=True)
async def _schema() -> AsyncIterator[None]:
    if not (DB_URL and REDIS_URL):
        yield
        return
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini"))
    cfg.attributes["database_url"] = DB_URL
    # Alembic's env.py runs its own event loop, so run it in a worker thread.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield


@pytest.fixture(autouse=True)
async def _clean(settings: Settings) -> AsyncIterator[None]:
    if not (DB_URL and REDIS_URL):
        yield
        return
    engine = create_async_engine(DB_URL)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    redis = Redis.from_url(settings.redis_url)
    await redis.flushdb()
    await redis.aclose()
    yield


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings)
    yield database
    await database.dispose()


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(settings.redis_url)
    yield client
    await client.aclose()


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


WorkerRunner = Callable[..., Awaitable[None]]


@pytest.fixture
def run_worker(settings: Settings) -> WorkerRunner:
    """Run real arq workers (execution + analysis) in burst mode until both queues,
    including deferred jobs, are empty."""

    async def _one(
        stage: type[WorkerSettings] | type[AnalysisWorkerSettings], worker_settings: Settings
    ) -> int:
        worker = Worker(
            functions=stage.functions,
            queue_name=stage.queue_name,
            on_startup=startup,
            on_shutdown=shutdown,
            redis_settings=RedisSettings.from_dsn(worker_settings.redis_url),
            burst=True,
            poll_delay=0.02,
            max_jobs=worker_settings.worker_max_jobs,
            keep_result=0,
            handle_signals=False,
            ctx={"settings": worker_settings},
            log_results=False,
        )
        try:
            await worker.main()
        finally:
            await worker.close()
        return worker.jobs_complete + worker.jobs_retried + worker.jobs_failed

    async def _run(**overrides: Any) -> None:
        worker_settings = make_settings(**overrides) if overrides else settings
        # Stages feed each other (execution -> analysis -> finalisation), so alternate
        # until a full pass over both queues finds nothing to do.
        while True:
            ran = await _one(WorkerSettings, worker_settings)
            ran += await _one(AnalysisWorkerSettings, worker_settings)
            if ran == 0:
                return

    return _run


async def register(client: httpx.AsyncClient, email: str = "owner@example.com") -> tuple[dict[str, str], str]:
    """Register a user; return (auth headers, workspace id)."""
    r = await client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"})
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return headers, me["workspaces"][0]["id"]
