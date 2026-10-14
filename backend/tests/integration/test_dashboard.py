"""Dashboard math over two days of backfilled runs, checked against raw rows."""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import func, select

from avt.analysis.pipeline import ANALYSIS_VERSION
from avt.config import Settings
from avt.db.base import Database
from avt.db.models import Brand, BrandMention, Response, Workspace
from avt.domain import BrandRole, RunTrigger
from avt.services.runs import create_run
from avt.worker.main import make_enqueue
from tests.integration.conftest import WorkerRunner, register


async def test_overview_matches_raw_facts(
    client: httpx.AsyncClient, run_worker: WorkerRunner, db: Database, settings: Settings
) -> None:
    h, ws = await register(client)
    base = f"/api/v1/workspaces/{ws}"
    await client.patch(base, json={"models": ["openai:gpt-5-mini", "gemini:gemini-2.5-flash"]}, headers=h)
    for name, role in (("Lumora CRM", "primary"), ("Pipewise", "competitor"), ("Clientra", "competitor")):
        await client.post(f"{base}/brands", json={"name": name, "role": role}, headers=h)
    for text in ("best CRM for small businesses", "best CRM for startups", "cheapest CRM"):
        await client.post(f"{base}/prompts", json={"text": text}, headers=h)

    today = dt.datetime.now(dt.UTC).date()
    days = [today - dt.timedelta(days=1), today]
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    for day in days:
        async with db.session() as s:
            workspace = await s.get(Workspace, uuid.UUID(ws))
            assert workspace is not None
            run = await create_run(s, workspace, trigger=RunTrigger.BACKFILL, scheduled_for=day)
        assert run is not None
        await make_enqueue(pool)("dispatch_run", str(run.id))
    await pool.aclose()
    await run_worker()

    r = await client.get(f"{base}/dashboard/overview?date_from={today}&date_to={today}", headers=h)
    overview = r.json()
    assert overview["responses"] == 3 * 2

    async with db.session() as s:
        primary_id = (await s.execute(select(Brand.id).where(Brand.role == BrandRole.PRIMARY))).scalar_one()
        mentioned = (
            await s.execute(
                select(func.count())
                .select_from(BrandMention)
                .where(
                    BrandMention.brand_id == primary_id,
                    BrandMention.day == today,
                    BrandMention.analysis_version == ANALYSIS_VERSION,
                )
            )
        ).scalar_one()
        responses_today = (
            await s.execute(select(func.count()).select_from(Response).where(Response.day == today))
        ).scalar_one()

    kpi = next(k for k in overview["kpis"] if k["brand_id"] == str(primary_id))
    assert kpi["mention_rate"] == round(mentioned / responses_today, 4)
    assert kpi["share_of_voice_delta"] is not None  # yesterday exists as the previous period
    for point_day in {p["day"] for p in overview["series"]}:
        total = sum(p["share_of_voice"] for p in overview["series"] if p["day"] == point_day)
        assert total == 0 or abs(total - 1) < 0.01

    # The per-model filter only counts that model's answers.
    one = (
        await client.get(
            f"{base}/dashboard/overview?date_from={today}&date_to={today}&model=openai:gpt-5-mini", headers=h
        )
    ).json()
    assert one["responses"] == 3
    assert {m["model_key"] for m in one["by_model"]} == {"openai:gpt-5-mini"}

    bad = await client.get(f"{base}/dashboard/overview?date_from={today}&date_to={days[0]}", headers=h)
    assert bad.status_code == 422
