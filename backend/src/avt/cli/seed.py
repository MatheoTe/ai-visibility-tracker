"""Seed a fictional demo workspace with ~30 days of history.

    uv run avt-seed                 # idempotent: skips if the demo user exists
    uv run avt-seed --days 45 --reset

Brands, prompts and the market are fictional. History is produced by the fake
provider through the real executor and analysis pipeline (see ``inline.py``), so
the dashboard shows exactly what a month of daily runs would have produced.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import time

from redis.asyncio import Redis
from sqlalchemy import delete, func, select

from avt.api.routers.prompts import prompt_hash
from avt.api.security import hash_password
from avt.cli.inline import InlineRunner
from avt.config import get_settings
from avt.db.base import Database
from avt.db.models import Brand, Membership, Prompt, Response, User, Workspace
from avt.domain import BrandRole, RunTrigger
from avt.logging import configure_logging
from avt.providers.registry import default_model_keys
from avt.services.runs import create_run

DEMO_EMAIL = "demo@avt.dev"
DEMO_PASSWORD = "demo-password"  # noqa: S105 - fictional demo account

BRANDS = [
    ("Lumora CRM", BrandRole.PRIMARY, ["Lumora"], ["lumoracrm.com"], "#6366f1"),
    ("Pipewise", BrandRole.COMPETITOR, [], ["pipewise.com"], "#f59e0b"),
    ("Clientra", BrandRole.COMPETITOR, [], ["clientra.com"], "#10b981"),
    ("HubNest", BrandRole.COMPETITOR, ["Hub Nest"], ["hubnest.com"], "#ef4444"),
    ("SalesKite", BrandRole.COMPETITOR, ["Sales Kite"], ["saleskite.com"], "#0ea5e9"),
]

PROMPTS = [
    ("best CRM for small businesses", ["smb"]),
    ("Which CRM has the best free plan?", ["pricing"]),
    ("top CRM tools for startups in 2026", ["startups"]),
    ("easiest CRM to set up for a 5-person sales team", ["smb", "onboarding"]),
    ("CRM with the best email automation", ["email"]),
    ("best CRM for real estate agents", ["vertical"]),
    ("best CRM for B2B SaaS companies", ["saas"]),
    ("affordable CRM alternatives to enterprise suites", ["pricing"]),
    ("which CRM integrates best with accounting software", ["integrations"]),
    ("best mobile CRM app for field sales", ["mobile"]),
    ("CRM with AI sales forecasting", ["ai"]),
    ("best CRM for agencies and consultants", ["vertical"]),
    ("what CRM do fast-growing startups use", ["startups"]),
    ("simple pipeline management tool for freelancers", ["smb"]),
    ("best CRM for customer support teams", ["support"]),
    ("most user-friendly CRM software", ["ux"]),
    ("CRM with the best reporting dashboards", ["reporting"]),
    ("best CRM for nonprofits", ["vertical"]),
    ("lead management software for small teams", ["leads"]),
    ("best CRM for e-commerce brands", ["vertical"]),
    ("which CRM has the best API for developers", ["integrations"]),
    ("CRM recommendations for a growing sales team", ["growth"]),
    ("best sales CRM with built-in calling", ["calling"]),
    ("GDPR-friendly CRM options in Europe", ["compliance"]),
    ("best CRM to replace spreadsheets", ["smb"]),
]


async def seed(days: int, reset: bool) -> None:
    settings = get_settings()
    configure_logging("WARNING", json=False)
    settings.force_fake_providers = True  # history is synthetic, never spend real tokens on it
    settings.fake_latency_ms_min = 0
    settings.fake_latency_ms_max = 0
    settings.fake_error_rate = 0.01
    db = Database(settings)
    redis = Redis.from_url(settings.redis_url)
    try:
        async with db.session() as session:
            existing = (
                await session.execute(select(User).where(func.lower(User.email) == DEMO_EMAIL))
            ).scalar_one_or_none()
            if existing and not reset:
                print(f"demo user already exists - log in with {DEMO_EMAIL} / {DEMO_PASSWORD}")
                return
            if existing:
                ws_ids = select(Membership.workspace_id).where(Membership.user_id == existing.id)
                await session.execute(delete(Workspace).where(Workspace.id.in_(ws_ids)))
                await session.execute(delete(User).where(User.id == existing.id))
                await session.commit()

            user = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD))
            workspace = Workspace(
                name="Lumora CRM (demo)",
                models=default_model_keys(settings),
                schedule_enabled=True,
                daily_run_hour_utc=settings.default_daily_run_hour_utc,
            )
            session.add_all([user, workspace])
            await session.flush()
            session.add(Membership(user_id=user.id, workspace_id=workspace.id, role="owner"))
            for name, role, aliases, domains, color in BRANDS:
                session.add(
                    Brand(
                        workspace_id=workspace.id,
                        name=name,
                        role=role,
                        aliases=aliases,
                        domains=domains,
                        color=color,
                    )
                )
            for text, tags in PROMPTS:
                session.add(
                    Prompt(workspace_id=workspace.id, text=text, text_hash=prompt_hash(text), tags=tags)
                )
            await session.commit()
            workspace_id = workspace.id

        runner = InlineRunner(settings, db, redis)
        today = dt.datetime.now(dt.UTC).date()
        started = time.perf_counter()
        for offset in range(days, 0, -1):
            day = today - dt.timedelta(days=offset)
            async with db.session() as session:
                ws = await session.get(Workspace, workspace_id)
                assert ws is not None
                run = await create_run(session, ws, trigger=RunTrigger.BACKFILL, scheduled_for=day)
            assert run is not None
            await runner.run(run.id)
            print(f"  {day}  {run.total_tasks} tasks", flush=True)
        await runner.aclose()
        async with db.session() as session:
            responses = (
                await session.execute(
                    select(func.count()).select_from(Response).where(Response.workspace_id == workspace_id)
                )
            ).scalar_one()
        print(
            f"seeded {days} days, {responses} responses in {time.perf_counter() - started:.1f}s\n"
            f"log in with {DEMO_EMAIL} / {DEMO_PASSWORD}"
        )
    finally:
        await redis.aclose()
        await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the fictional demo workspace.")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--reset", action="store_true", help="delete and recreate the demo workspace")
    args = parser.parse_args()
    asyncio.run(seed(args.days, args.reset))


if __name__ == "__main__":
    main()
