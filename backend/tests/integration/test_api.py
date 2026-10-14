"""API contract: auth, tenant isolation, validation, ops endpoints, re-analysis."""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
from sqlalchemy import delete, func, select

from avt.analysis.pipeline import ANALYSIS_VERSION
from avt.analysis.sentiment import LexiconSentimentAnalyzer
from avt.db.base import Database
from avt.db.models import Analysis, DailyBrandStats
from avt.services.analyzer import AnalysisService
from avt.services.brand_cache import BrandCache
from tests.integration.conftest import WorkerRunner, register


async def test_requires_auth(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_login_and_duplicate_registration(client: httpx.AsyncClient) -> None:
    await register(client, "a@example.com")
    r = await client.post(
        "/api/v1/auth/register", json={"email": "A@example.com", "password": "another-pass"}
    )
    assert r.status_code == 409
    bad = await client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong-pass"})
    assert bad.status_code == 401
    ok = await client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "correct-horse-1"}
    )
    assert ok.status_code == 200
    assert ok.json()["token_type"] == "bearer"


async def test_workspaces_are_isolated(client: httpx.AsyncClient) -> None:
    alice, ws_a = await register(client, "alice@example.com")
    bob, _ = await register(client, "bob@example.com")
    r = await client.post(f"/api/v1/workspaces/{ws_a}/brands", json={"name": "Lumora"}, headers=alice)
    assert r.status_code == 201
    for path in ("", "/brands", "/prompts", "/runs", "/dashboard/overview"):
        r = await client.get(f"/api/v1/workspaces/{ws_a}{path}", headers=bob)
        assert r.status_code == 404, path  # not 403: workspace ids are not probeable
    r = await client.get(f"/api/v1/workspaces/{uuid.uuid4()}/brands", headers=alice)
    assert r.status_code == 404


async def test_brand_and_prompt_constraints(client: httpx.AsyncClient) -> None:
    h, ws = await register(client)
    base = f"/api/v1/workspaces/{ws}"
    assert (
        await client.post(f"{base}/brands", json={"name": "Lumora", "role": "primary"}, headers=h)
    ).status_code == 201
    dup = await client.post(f"{base}/brands", json={"name": "lumora"}, headers=h)
    assert dup.status_code == 409
    second_primary = await client.post(f"{base}/brands", json={"name": "Other", "role": "primary"}, headers=h)
    assert second_primary.status_code == 409
    bad_color = await client.post(f"{base}/brands", json={"name": "X", "color": "red"}, headers=h)
    assert bad_color.status_code == 422

    assert (await client.post(f"{base}/prompts", json={"text": "Best CRM?"}, headers=h)).status_code == 201
    same = await client.post(f"{base}/prompts", json={"text": "  best   crm?  "}, headers=h)
    assert same.status_code == 409  # normalised duplicate
    no_prompts_run = await client.post(f"{base}/runs", json={"models": ["nope"]}, headers=h)
    assert no_prompts_run.status_code == 422  # invalid model key


async def test_run_requires_prompts(client: httpx.AsyncClient) -> None:
    h, ws = await register(client)
    r = await client.post(f"/api/v1/workspaces/{ws}/runs", headers=h)
    assert r.status_code == 409


async def test_ops_endpoints(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "postgres": "ok", "redis": "ok"}
    metrics = await client.get("/metrics")
    assert "avt_runs_in_flight" in metrics.text
    spec = (await client.get("/api/v1/openapi.json")).json()
    assert "/api/v1/workspaces/{workspace_id}/dashboard/overview" in spec["paths"]
    assert (await client.get("/healthz")).headers["x-request-id"]


async def test_reanalysis_is_idempotent_and_rebuilds_rollups(
    client: httpx.AsyncClient, run_worker: WorkerRunner, db: Database
) -> None:
    h, ws = await register(client)
    base = f"/api/v1/workspaces/{ws}"
    await client.post(f"{base}/brands", json={"name": "Lumora", "role": "primary"}, headers=h)
    await client.post(f"{base}/prompts", json={"text": "best CRM for small businesses"}, headers=h)
    await client.post(f"{base}/runs", headers=h)
    await run_worker()

    async with db.session() as s:
        before = (await s.execute(select(func.count()).select_from(Analysis))).scalar_one()
        await s.execute(delete(DailyBrandStats))  # simulate lost rollups
        await s.commit()

    service = AnalysisService(db, BrandCache(), LexiconSentimentAnalyzer())
    today = dt.datetime.now(dt.UTC).date()
    done = await service.reanalyze_range(uuid.UUID(ws), today - dt.timedelta(days=1), today)
    assert done == before
    async with db.session() as s:
        after = (await s.execute(select(func.count()).select_from(Analysis))).scalar_one()
        versions = set((await s.execute(select(Analysis.version))).scalars())
        rollups = (await s.execute(select(func.count()).select_from(DailyBrandStats))).scalar_one()
    assert after == before  # same version: nothing duplicated
    assert versions == {ANALYSIS_VERSION}
    assert rollups > 0
