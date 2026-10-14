"""A full run through the real API, queue, workers, Postgres and Redis (fake providers)."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import func, select

from avt.config import Settings
from avt.db.base import Database
from avt.db.models import Analysis, Response, Run, RunTask, Workspace
from avt.domain import RunTrigger
from avt.scheduler.main import schedule_due_runs
from avt.services.runs import create_run, dispatch_run
from avt.worker.main import make_enqueue
from tests.integration.conftest import WorkerRunner, register

MODELS = ["anthropic:claude-sonnet-5-5", "openai:gpt-5-mini", "perplexity:sonar"]
CSV = (
    "prompt,tags\n"
    "best CRM for small businesses,crm;smb\n"
    "Which CRM has the best free plan?,crm;pricing\n"
    "top sales pipeline tools for startups,pipeline\n"
    "best CRM for small businesses,duplicate\n"
    "x,too short\n"
    "CRM with the best email automation,email\n"
)


async def _setup_workspace(client: httpx.AsyncClient) -> tuple[dict[str, str], str]:
    headers, ws = await register(client)
    base = f"/api/v1/workspaces/{ws}"
    r = await client.patch(base, json={"models": MODELS}, headers=headers)
    assert r.status_code == 200, r.text
    for body in (
        {"name": "Lumora CRM", "role": "primary", "aliases": ["Lumora"], "domains": ["lumoracrm.com"]},
        {"name": "Pipewise"},
        {"name": "Clientra"},
    ):
        r = await client.post(f"{base}/brands", json=body, headers=headers)
        assert r.status_code == 201, r.text
    r = await client.post(
        f"{base}/prompts/import", files={"file": ("prompts.csv", CSV, "text/csv")}, headers=headers
    )
    assert r.json() == {
        "created": 4,
        "skipped_duplicates": 1,
        "errors": ["line 6: prompt must be 3-2000 characters"],
    }
    return headers, ws


async def test_full_run(client: httpx.AsyncClient, run_worker: WorkerRunner, db: Database) -> None:
    headers, ws = await _setup_workspace(client)
    base = f"/api/v1/workspaces/{ws}"

    r = await client.post(f"{base}/runs", headers=headers)
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    assert r.json()["total_tasks"] == 4 * 3

    # Inject faults so retries happen for real; the run must still complete.
    await run_worker(fake_error_rate=0.25)

    run = (await client.get(f"{base}/runs/{run_id}", headers=headers)).json()
    assert run["status"] == "completed", run
    assert run["counts"] == {"succeeded": 12}
    assert run["analyzed"] == 12
    assert run["percent"] == 100.0
    assert float(run["cost_usd"]) > 0
    assert run["input_tokens"] > 0
    assert {m["model_key"] for m in run["by_model"]} == set(MODELS)

    async with db.session() as s:
        retried = (await s.execute(select(func.count()).where(RunTask.attempts > 1))).scalar_one()
        assert retried > 0, "fault injection should have forced retries"
        assert (await s.execute(select(func.count()).select_from(Response))).scalar_one() == 12

    overview = (await client.get(f"{base}/dashboard/overview", headers=headers)).json()
    assert overview["responses"] == 12
    assert [b["name"] for b in overview["brands"]] == ["Lumora CRM", "Clientra", "Pipewise"]
    sov = sum(k["share_of_voice"] for k in overview["kpis"])
    assert 0.99 <= sov <= 1.01 or sov == 0  # SoV is a share over tracked brands
    assert {m["model_key"] for m in overview["by_model"]} == set(MODELS)

    sources = (await client.get(f"{base}/dashboard/sources", headers=headers)).json()
    assert sources["total"] > 0
    assert sources["items"][0]["citations"] >= sources["items"][-1]["citations"]

    prompts = (await client.get(f"{base}/prompts", headers=headers)).json()
    assert prompts["total"] == 4
    assert all(p["responses_7d"] == 3 for p in prompts["items"])
    pid = prompts["items"][0]["id"]
    page = (await client.get(f"{base}/prompts/{pid}/responses?limit=2", headers=headers)).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"]
    rest = (
        await client.get(
            f"{base}/prompts/{pid}/responses?limit=2&cursor={page['next_cursor']}", headers=headers
        )
    ).json()
    assert len(rest["items"]) == 1
    assert rest["next_cursor"] is None
    item = page["items"][0]
    assert item["analyzed"]
    assert item["text"]
    for mention in item["mentions"]:
        span = mention["spans"][0]
        assert item["text"][span["start"] : span["end"]]  # highlight offsets are valid


async def test_dead_letter_then_retry_failed(client: httpx.AsyncClient, run_worker: WorkerRunner) -> None:
    headers, ws = await _setup_workspace(client)
    base = f"/api/v1/workspaces/{ws}"
    run_id = (await client.post(f"{base}/runs", json={"models": [MODELS[0]]}, headers=headers)).json()["id"]

    await run_worker(fake_error_rate=1.0, max_task_attempts=2)
    run = (await client.get(f"{base}/runs/{run_id}", headers=headers)).json()
    assert run["status"] == "failed"
    assert run["counts"] == {"dead": 4}
    dead = (await client.get(f"{base}/runs/{run_id}/tasks?status=dead", headers=headers)).json()
    assert len(dead["items"]) == 4
    assert all("fake" in t["last_error"] for t in dead["items"])
    assert all(t["attempts"] == 2 for t in dead["items"])

    r = await client.post(f"{base}/runs/{run_id}/retry-failed", headers=headers)
    assert r.json() == {"requeued": 4}
    await run_worker(fake_error_rate=0.0)
    run = (await client.get(f"{base}/runs/{run_id}", headers=headers)).json()
    assert run["status"] == "completed"
    assert run["counts"] == {"succeeded": 4}


async def test_retry_budget_dead_letters_early(
    client: httpx.AsyncClient, run_worker: WorkerRunner, db: Database
) -> None:
    headers, ws = await _setup_workspace(client)
    base = f"/api/v1/workspaces/{ws}"
    run_id = (await client.post(f"{base}/runs", headers=headers)).json()["id"]
    # 12 tasks, every call fails, 5 attempts each would be 48 retries; the budget allows 3.
    await run_worker(fake_error_rate=1.0, retry_budget_floor=3, retry_budget_ratio=0.0)
    async with db.session() as s:
        attempts = (
            await s.execute(select(func.sum(RunTask.attempts)).where(RunTask.run_id == run_id))
        ).scalar_one()
    assert attempts == 12 + 3


async def test_idempotent_dispatch_and_execution(
    client: httpx.AsyncClient, run_worker: WorkerRunner, db: Database, settings: Settings
) -> None:

    headers, ws = await _setup_workspace(client)
    base = f"/api/v1/workspaces/{ws}"
    run_id = (await client.post(f"{base}/runs", headers=headers)).json()["id"]

    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    enqueue = make_enqueue(pool)
    # Re-dispatch the run and enqueue every task again while the originals are still queued.
    async with db.session() as s:
        task_ids = list((await s.execute(select(RunTask.id).where(RunTask.run_id == run_id))).scalars())
        await dispatch_run(s, uuid.UUID(run_id), enqueue)
    await asyncio.gather(*(enqueue("execute_task", t) for t in task_ids))
    await run_worker()
    # ...and once more after they finished: a late duplicate delivery.
    await asyncio.gather(*(enqueue("execute_task", t) for t in task_ids))
    await run_worker()
    await pool.aclose()

    async with db.session() as s:
        assert (await s.execute(select(func.count()).select_from(Response))).scalar_one() == 12
        assert (await s.execute(select(func.count()).select_from(Analysis))).scalar_one() == 12
        assert (await s.execute(select(func.max(RunTask.attempts)))).scalar_one() == 1
        assert (await s.execute(select(Run.status))).scalar_one() == "completed"


async def test_scheduler_never_double_fires(client: httpx.AsyncClient, db: Database) -> None:
    _, ws = await _setup_workspace(client)
    enqueued: list[tuple[str, int | str]] = []

    async def enqueue(name: str, arg: int | str) -> None:
        enqueued.append((name, arg))

    now = dt.datetime(2026, 10, 14, 23, 0, tzinfo=dt.UTC)
    # Five scheduler replicas ticking at the same instant.
    results = await asyncio.gather(*(schedule_due_runs(db, enqueue, now) for _ in range(5)))
    assert sum(len(r) for r in results) == 1
    assert len(enqueued) == 1
    async with db.session() as s:
        workspace = await s.get(Workspace, uuid.UUID(ws))
        assert workspace is not None
        again = await create_run(s, workspace, trigger=RunTrigger.SCHEDULED, scheduled_for=now.date())
        assert again is None
        runs = (await s.execute(select(func.count()).select_from(Run))).scalar_one()
    assert runs == 1


async def test_scheduler_respects_run_hour(client: httpx.AsyncClient, db: Database) -> None:
    await _setup_workspace(client)

    async def enqueue(name: str, arg: int | str) -> None:
        return None

    early = dt.datetime(2026, 10, 14, 0, 30, tzinfo=dt.UTC)  # default run hour is 06:00 UTC
    assert await schedule_due_runs(db, enqueue, early) == []
    later = early.replace(hour=7)
    assert len(await schedule_due_runs(db, enqueue, later)) == 1
