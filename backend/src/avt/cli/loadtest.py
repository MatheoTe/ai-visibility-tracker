"""Load test: one run of N prompts x M models through the real queue and workers.

    uv run avt-loadtest --prompts 2000 --workers 4
    uv run avt-loadtest --prompts 2000 --workers 4 --rpm 600      # provider-limited

The script creates a throwaway workspace, spawns ``--workers`` arq worker
processes (fake providers, configurable latency / fault rate / rate limits),
triggers a run, polls progress from Postgres and prints a summary. It measures
the pipeline - fan-out, dispatch, gating, persistence, analysis, rollups - not
any vendor's API.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import itertools
import os
import signal
import subprocess
import sys
import time

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from avt.analysis.pipeline import ANALYSIS_VERSION
from avt.api.routers.prompts import prompt_hash
from avt.cli.seed import BRANDS
from avt.config import get_settings
from avt.db.base import Database
from avt.db.models import (
    Brand,
    DailyBrandStats,
    DailyModelStats,
    DailySourceStats,
    Prompt,
    Run,
    RunTask,
    Workspace,
)
from avt.domain import RunStatus, RunTrigger
from avt.services.runs import create_run, run_progress
from avt.worker.main import make_enqueue

SUBJECTS = ["CRM", "sales pipeline tool", "lead management software", "customer database", "sales CRM"]
AUDIENCES = [
    "small businesses",
    "startups",
    "real estate agents",
    "agencies",
    "nonprofits",
    "e-commerce brands",
    "B2B SaaS",
    "consultants",
    "field sales teams",
    "recruiters",
    "law firms",
    "clinics",
    "freelancers",
    "manufacturers",
    "insurance brokers",
    "schools",
    "restaurants",
    "logistics companies",
    "fintechs",
    "marketing teams",
]
FRAMES = [
    "best {s} for {a}",
    "which {s} do {a} use?",
    "top {s} options for {a} in 2026",
    "affordable {s} for {a}",
    "easiest {s} to set up for {a}",
    "{s} with the best automation for {a}",
    "{s} with the best free plan for {a}",
    "most reliable {s} for {a}",
    "{s} recommendations for {a}",
    "what is the best {s} for {a} on a budget",
]


def generate_prompts(n: int) -> list[str]:
    combos = itertools.product(FRAMES, SUBJECTS, AUDIENCES)
    out = [f.format(s=s, a=a) for f, s, a in combos]
    if n > len(out):
        out += [f"{p} (variant {i})" for i, p in enumerate(out * (n // len(out) + 1))]
    return out[:n]


def spawn_workers(
    count: int, settings_path: str, env: dict[str, str], log_path: str | None
) -> list[subprocess.Popen[bytes]]:
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL  # noqa: SIM115 - owned by the children
    return [
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-m", "arq", settings_path],
            env={**os.environ, **env},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        for _ in range(count)
    ]


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    db = Database(settings)
    models = args.models.split(",")
    env = {
        "AVT_FORCE_FAKE_PROVIDERS": "true",
        "AVT_FAKE_LATENCY_MS_MIN": str(args.latency_min),
        "AVT_FAKE_LATENCY_MS_MAX": str(args.latency_max),
        "AVT_FAKE_ERROR_RATE": str(args.error_rate),
        "AVT_WORKER_MAX_JOBS": str(args.max_jobs),
        "AVT_LOG_LEVEL": args.log_level,
        "AVT_WORKER_METRICS_PORT": "0",
        "AVT_USE_LLM_ANALYZER": "false",
    }
    for provider in {m.split(":")[0] for m in models}:
        p = provider.upper()
        env[f"AVT_LIMITS_{p}_REQUESTS_PER_MINUTE"] = str(args.rpm)
        env[f"AVT_LIMITS_{p}_TOKENS_PER_MINUTE"] = str(args.rpm * 2000)
        env[f"AVT_LIMITS_{p}_MAX_CONCURRENCY"] = str(args.concurrency)

    async with db.session() as session:
        workspace = Workspace(
            name=f"loadtest {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M}",
            models=models,
            schedule_enabled=False,
            daily_run_hour_utc=0,
        )
        session.add(workspace)
        await session.flush()
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
        prompts = generate_prompts(args.prompts)
        for chunk in range(0, len(prompts), 1000):
            await session.execute(
                insert(Prompt),
                [
                    {
                        "workspace_id": workspace.id,
                        "text": p,
                        "text_hash": prompt_hash(p),
                        "tags": ["loadtest"],
                    }
                    for p in prompts[chunk : chunk + 1000]
                ],
            )
        await session.commit()
        workspace_id = workspace.id

    workers = spawn_workers(args.workers, "avt.worker.main.WorkerSettings", env, args.worker_log)
    workers += spawn_workers(
        args.analysis_workers, "avt.worker.main.AnalysisWorkerSettings", env, args.worker_log
    )
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    print(
        f"task workers={args.workers} (max_jobs={args.max_jobs}) analysis workers={args.analysis_workers}"
        f" prompts={len(prompts)} models={len(models)}"
    )
    try:
        await asyncio.sleep(2)  # let workers connect
        t0 = time.perf_counter()
        async with db.session() as session:
            ws = await session.get(Workspace, workspace_id)
            assert ws is not None
            run = await create_run(session, ws, trigger=RunTrigger.MANUAL)
        assert run is not None
        fan_out = time.perf_counter() - t0
        await make_enqueue(pool)("dispatch_run", str(run.id))
        print(f"run {run.id}: {run.total_tasks} tasks, fan-out {fan_out * 1000:.0f} ms")

        status = RunStatus.PENDING
        while True:
            await asyncio.sleep(args.poll)
            async with db.session() as session:
                progress = await run_progress(session, run.id, ANALYSIS_VERSION)
                status = RunStatus(
                    (await session.execute(select(Run.status).where(Run.id == run.id))).scalar_one()
                )
            elapsed = time.perf_counter() - t0
            print(
                f"  t={elapsed:6.1f}s  done={progress.done}/{progress.total}  analyzed={progress.analyzed}"
                f"  rate={progress.done / elapsed:6.1f} tasks/s  status={status.value}",
                flush=True,
            )
            if status.is_terminal or elapsed > args.timeout:
                break
        wall = time.perf_counter() - t0

        async with db.session() as session:
            attempts, dead = (
                await session.execute(
                    select(func.sum(RunTask.attempts), func.count().filter(RunTask.status == "dead")).where(
                        RunTask.run_id == run.id
                    )
                )
            ).one()
            latencies = [
                float(x)
                for x in (
                    await session.execute(
                        text(
                            "SELECT extract(epoch FROM t.finished_at - r.created_at) "
                            "FROM run_tasks t JOIN runs r ON r.id = t.run_id "
                            "WHERE t.run_id = :id AND t.finished_at IS NOT NULL"
                        ),
                        {"id": run.id},
                    )
                ).scalars()
            ]
            final = await session.get(Run, run.id)
            assert final is not None

        total = run.total_tasks
        print("\n| metric | value |\n|---|---|")
        rows = [
            ("tasks (prompts x models)", f"{total:,}"),
            ("task workers x max_jobs", f"{args.workers} x {args.max_jobs}"),
            ("analysis workers", f"{args.analysis_workers}"),
            ("fake latency per call", f"{args.latency_min}-{args.latency_max} ms"),
            ("injected fault rate", f"{args.error_rate:.0%}"),
            ("rate limit per provider", f"{args.rpm:,} req/min"),
            ("fan-out (INSERT ... SELECT)", f"{fan_out * 1000:.0f} ms"),
            ("wall time to completed + analysed + rolled up", f"{wall:.1f} s"),
            ("throughput", f"{total / wall:.1f} tasks/s ({total / wall * 60:,.0f}/min)"),
            ("task completion time p50 / p95", f"{pct(latencies, 0.5):.1f} s / {pct(latencies, 0.95):.1f} s"),
            ("retries (attempts - tasks)", f"{int(attempts or 0) - total:,}"),
            ("dead-lettered", f"{dead:,}"),
            ("final status", final.status),
            ("estimated cost if the calls were real", f"${final.cost_usd:.2f}"),
        ]
        for k, v in rows:
            print(f"| {k} | {v} |")
    finally:
        for w in workers:
            w.send_signal(signal.SIGTERM)
        for w in workers:
            try:
                w.wait(timeout=20)
            except subprocess.TimeoutExpired:
                w.kill()
        await pool.aclose()
        if not args.keep:
            async with db.session() as session:
                await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
                for model in (DailyModelStats, DailyBrandStats, DailySourceStats):
                    await session.execute(delete(model).where(model.workspace_id == workspace_id))
                await session.commit()
        await db.dispose()


def run() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--prompts", type=int, default=2000)
    parser.add_argument(
        "--models", default="anthropic:claude-sonnet-5-5,openai:gpt-5-mini,gemini:gemini-2.5-flash"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--analysis-workers", type=int, default=2)
    parser.add_argument("--max-jobs", type=int, default=64)
    parser.add_argument("--concurrency", type=int, default=256, help="per-provider in-flight cap")
    parser.add_argument("--rpm", type=int, default=1_000_000, help="per-provider requests/minute")
    parser.add_argument("--latency-min", type=int, default=40)
    parser.add_argument("--latency-max", type=int, default=250)
    parser.add_argument("--error-rate", type=float, default=0.02)
    parser.add_argument("--poll", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--keep", action="store_true", help="keep the load-test workspace")
    parser.add_argument("--worker-log", default=None, help="append worker logs to this file")
    parser.add_argument("--log-level", default="WARNING", help="worker log level")
    asyncio.run(main(parser.parse_args()))


if __name__ == "__main__":
    run()
