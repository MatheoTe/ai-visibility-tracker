# AI Visibility Tracker

[![Live demo](https://img.shields.io/badge/Live_demo-open_the_dashboard-2a78d6?style=for-the-badge)](https://ai-visibility-tracker.vercel.app)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/MatheoTe/ai-visibility-tracker)

**When someone asks an AI assistant "what's the best CRM for a small business?", is your brand in the answer?**

AI Visibility Tracker asks that question — and every other question your buyers ask — to Claude, GPT, Gemini and Perplexity every day, then measures how often your brand is mentioned, in what position, with what tone, and which sources the answers cite. It is the AI-search equivalent of rank tracking: share of voice against your competitors, over time, per model.

It runs end to end **with zero API keys** (a deterministic fake provider stands in for every model), so `docker compose up` gives you a working product with 30 days of demo history.

![Dashboard](docs/screenshots/dashboard-light.png)

| Prompt drill-down with highlighted mentions | Sources leaderboard | Run monitor |
|---|---|---|
| ![Drill-down](docs/screenshots/prompt-drilldown.png) | ![Sources](docs/screenshots/sources.png) | ![Run](docs/screenshots/run-detail.png) |

Dark mode: [`docs/screenshots/dashboard-dark.png`](docs/screenshots/dashboard-dark.png). Screenshots are produced by [`frontend/scripts/screenshots.mjs`](frontend/scripts/screenshots.mjs) against the seeded demo.

> **GIF to record:** sign in as the demo user → Overview (switch metric tabs, filter to one model) → Prompts → open "best CRM for small businesses" → Runs → **Run now** → watch the progress bar fill and per-model cost update live → back to Overview.

## Live demo

Two ways to try it without installing anything:

- **[Live demo](https://ai-visibility-tracker.vercel.app)** — the dashboard as a static site, read-only. Every page (overview, prompts and the per-answer drill-down, brands, sources, runs and the run monitor) is served from a JSON snapshot of the seeded demo workspace, so there is no backend behind it: actions that would write (Run now, add a prompt, delete a brand, …) explain that they are disabled. Dates are shifted so the 30 days of history always end today. All data is fictional.
- **[Codespaces](https://codespaces.new/MatheoTe/ai-visibility-tracker)** — the full system in your browser: Postgres, Redis, API, workers, scheduler and dashboard from the same `docker-compose.yml`, on the fake provider, no secrets. The first start builds the images (a few minutes); then the Dashboard port opens and you sign in as `demo@avt.dev` / `demo-password`. Here **Run now** really fans out tasks to the workers and you can watch the run fill in.

<details>
<summary>How the static demo is built</summary>

- `NEXT_PUBLIC_DEMO_MODE=1` swaps the API client for [`lib/api/demo.ts`](frontend/src/lib/api/demo.ts), which answers the same `/api/v1/...` paths from `frontend/public/demo/` (search, model and status filters, offset and cursor pagination reproduced in memory; aggregates served as the backend computed them). Responses still go through the zod contract schemas, and a Vitest test parses the committed snapshot against them.
- Regenerate the snapshot from a seeded backend: `API_URL=http://localhost:8000 node frontend/scripts/export-demo-snapshot.mjs`.
- Vercel: project root directory `frontend/`, framework Next.js ([`frontend/vercel.json`](frontend/vercel.json)). Demo mode turns on by itself on Vercel builds; set `NEXT_PUBLIC_DEMO_MODE=0` and `NEXT_PUBLIC_API_URL` there to point a deployment at a real API instead.

</details>

---

## What it does

- **Workspaces** hold a primary brand, its competitors (with aliases and domains), and a list of prompts — typed in or imported from CSV.
- **Runs** ask every active prompt to every configured model (`prompts × models` tasks), in parallel, within per-provider rate limits. A scheduler starts one run per workspace per day; you can also run on demand.
- **Analysis** turns each raw answer into structure: which tracked brands are mentioned (exact + fuzzy matching with aliases), their **position** (order of first mention), **sentiment** per brand (Claude Haiku with a JSON schema, or an offline lexicon), and every **cited source** (inline links, bare URLs and provider citations, normalised to registrable domains).
- **Dashboard**: share of voice, mention rate, average position and sentiment — KPIs with deltas vs the previous period, daily trends per brand, mention rate per model, a competitive snapshot table, a sources leaderboard, a per-prompt drill-down with mentions highlighted in each answer, and a live run monitor with a dead-letter view and **retry failed**.
- **Cost tracking**: tokens and estimated USD per task, per run and per model.

## Architecture

```mermaid
flowchart LR
    UI[Next.js dashboard] -->|REST + JWT| API[FastAPI]
    API --> PG[(Postgres)]
    API -->|enqueue dispatch_run| RQ[(Redis<br/>arq queues · token buckets<br/>breaker · semaphores)]
    SCH[Scheduler<br/>daily runs + sweeper] -->|create run · enqueue| RQ
    SCH --> PG

    subgraph exec [Execution stage — I/O bound]
      W1[task worker] & W2[task worker]
    end
    subgraph analysis [Analysis stage — CPU bound]
      A1[analysis worker]
    end

    RQ -->|tasks queue| exec
    exec -->|gates: breaker → concurrency → token bucket| RQ
    exec -->|one call per task| P{{ModelProvider}}
    P --> C[Anthropic<br/>claude-sonnet-5-5]
    P --> O[OpenAI]
    P --> G[Gemini]
    P --> X[Perplexity]
    P --> F[Fake — seeded, offline]
    exec -->|raw response| PG
    exec -->|enqueue analyze_response| RQ
    RQ -->|analysis queue| analysis
    analysis -->|detection · URLs · sentiment<br/>Haiku JSON schema| PG
    analysis -->|finalize_run → daily rollups| PG
```

### A run, step by step

```mermaid
sequenceDiagram
    autonumber
    participant U as User / Scheduler
    participant API as FastAPI
    participant DB as Postgres
    participant Q as Redis (arq)
    participant W as Task worker
    participant P as Provider
    participant A as Analysis worker

    U->>API: POST /runs
    API->>DB: INSERT run + INSERT…SELECT tasks (prompts × models)<br/>UNIQUE(run_id, prompt_id, model_key)
    API->>Q: enqueue dispatch_run (job id = run)
    API-->>U: 202 Accepted
    Q->>W: dispatch_run
    W->>DB: keyset-page pending tasks (500/batch) → queued
    W->>Q: enqueue execute_task × N (job id = task)
    loop every task
        Q->>W: execute_task
        W->>Q: breaker? concurrency slot? token bucket (req + tokens)?
        alt any gate says "not now"
            W-->>Q: Retry(defer = wait time) — no slot held
        else admitted
            W->>DB: claim: UPDATE … SET status='running' WHERE status IN (pending, queued)
            W->>P: generate(prompt)
            alt success
                W->>DB: INSERT response ON CONFLICT (task_id) DO NOTHING; task → succeeded
                W->>Q: enqueue analyze_response
            else 429 / 5xx / timeout
                W->>Q: breaker.record_failure; spend retry budget
                W-->>Q: Retry(defer = backoff with full jitter, ≥ Retry-After)
            else permanent error or budget exhausted
                W->>DB: task → dead (dead-letter)
            end
        end
    end
    Q->>A: analyze_response
    A->>DB: INSERT analysis (response_id, version) + mentions + sources
    A->>Q: finalize_run (one per run per 2 s window)
    Q->>A: finalize_run
    A->>DB: all tasks terminal & analysed? → run completed / partial / failed,<br/>rebuild daily rollups for the touched days
```

## Design decisions & trade-offs

**Queue: arq (Redis).** The workload is overwhelmingly *waiting on LLM APIs*, so an asyncio-native queue lets one process keep 64 calls in flight without threads. arq gives deferred jobs, `Retry(defer=…)` and job-id de-duplication — exactly the three primitives the design leans on — in a few hundred lines with no broker beyond the Redis we already need for rate limiting. Celery's prefork model fits CPU work, not thousands of concurrent HTTP waits; Dramatiq is solid but its asyncio support is a middleware on a thread-based core. The costs: arq is small and in maintenance mode, and it pins `redis-py < 6` — both acceptable for a queue this thin, and the services never import arq (they take an `enqueue` callable), so swapping it is a contained change.

**Two stages, two queues.** Execution is I/O-bound; analysis is CPU-bound (regex, fuzzy matching) plus DB writes. They run on separate arq queues and worker pools, so each scales on its own axis and a burst of CPU-bound analysis never sits on the event loop that is dispatching tasks and waiting on providers.

**Rate limiting is a gate, not a sleep.** Each provider has a Redis token bucket that enforces *requests/min and tokens/min together* in one Lua script, on the Redis server clock (worker clock skew can't over-admit). Tokens are debited from an estimate and reconciled with real usage afterwards — under-estimates become debt that throttles the next callers. A leased Redis semaphore caps concurrent calls per provider (a crashed worker's slot expires with its lease). When any gate says "not now", the job is re-scheduled for exactly when it would be admitted (`Retry(defer=…)`), so throttled tasks never hold a worker slot or a DB connection.

**Retries: bounded twice.** Retryable failures (429, 5xx, timeouts) back off exponentially with **full jitter**, never earlier than the provider's `Retry-After`. Attempts are capped per task (`run_tasks.attempts`, not arq's counter, which would also count throttling deferrals), and a **per-run retry budget** (default 25% of tasks) caps total amplification — a degraded provider can't turn a 6,000-task run into 30,000 calls. Exhausted tasks are **dead-lettered**: visible in the run monitor with their last error, and re-queued with one click (`POST /runs/{id}/retry-failed`, which also resets the budget).

**Circuit breaker on failure *ratio*.** Per provider, shared through Redis: it opens when ≥50% of calls in a 30 s sliding window fail (with a minimum volume), rejects calls for a cooldown, then lets exactly one half-open probe through (`SET NX`). It started life count-based ("8 failures in 30 s"): the load test showed that at ~300 calls/s a *healthy* provider with a 2% error rate trips that every few seconds, freezing runs. Switching to a ratio took the same load test from 48 to 180 tasks/s.

**Idempotency, in layers.** The task table's `UNIQUE(run_id, prompt_id, model_key)` *is* the idempotency key — fan-out is `INSERT … SELECT … ON CONFLICT DO NOTHING`, so re-creating or re-dispatching a run never duplicates work. Job ids (`execute_task:{id}`) de-duplicate enqueues. Execution begins with an atomic claim (`UPDATE … WHERE status IN ('pending','queued') RETURNING`), with stale-lease takeover if a worker died mid-call. Responses are `UNIQUE(task_id)` and analyses `UNIQUE(response_id, version)`, both inserted `ON CONFLICT DO NOTHING`. The integration suite enqueues every task twice while queued and again after completion and asserts exactly one response and one analysis each.

**Scheduler can't double-fire.** "One scheduled run per workspace per day" is a partial unique index — `(workspace_id, scheduled_for) WHERE trigger = 'scheduled'` — so any number of scheduler replicas can race (a test runs five concurrently) and Postgres guarantees one winner. Each tick also sweeps work committed but never enqueued (crash between commit and enqueue, Redis flush) — safe because dispatch is idempotent — and closes runs whose last finalize job was lost.

**Analysis is versioned; raw data is immutable.** Raw responses and parsed analyses are separate tables, keyed `(response_id, analysis_version)`. Changing the detector bumps `ANALYSIS_VERSION`; `POST /workspaces/{id}/reanalyze` re-processes a date range and rebuilds rollups — no provider calls, and the old version stays for comparison. This was exercised for real: the drill-down showed `https://www.clientra.com/pricing` counting as a Clientra *mention*; v2 masks URLs before matching, and re-analysing the 3,000-response demo history took **5.8 s** on one analysis worker.

**Deterministic detection, LLM only for tone.** Brand detection is regex + RapidFuzz: free, fast (a few milliseconds per answer), reproducible, and offsets map back to the original text for highlighting. Fuzzy matching only applies to aliases of 5+ characters, skips inflections ("pipewiser") and text exact matches already claimed. Sentiment — which actually needs language understanding — goes to Claude Haiku with `output_config.format` JSON schema, re-validated with Pydantic, and **degrades to the lexicon analyzer on any failure**, so a sentiment outage never blocks mention/position/source data.

**Time series built for the dashboard's queries.** Fact tables (`brand_mentions`, `cited_sources`) are narrow and denormalised with `workspace_id`, `day` and `model_key` so aggregation never joins. Rollups (`daily_model_stats`, `daily_brand_stats`, `daily_source_stats`) are rebuilt per *(workspace, day)* inside one transaction when a run closes — instead of a materialized view, whose `REFRESH` recomputes every tenant's entire history. A 90-day dashboard reads at most `days × models × brands` small rows regardless of how many answers sit behind them. Every list endpoint is paginated (offset for user-managed lists, keyset for tasks and responses), and list views use grouped queries, never per-row lookups.

**Runs finish without a hot lock.** The "is this run done?" check used to run after every analysis; near the end of a run it scans more each time (quadratic tail) and serialises on the run row. Now each completion enqueues `finalize_run` keyed by *(run, 2-second window)* and deferred to the window's end; arq de-duplicates, so a run is checked at most once per window, and the window of the last commit always runs after it.

**Thin providers.** Each provider maps one request to one API call and classifies errors (`RateLimited`, `Transient`, `Permanent`); SDK-level retries are disabled so the policy above is the only one. Anthropic uses the official SDK with low effort (answers like the consumer chat) and the server-side refusal fallback; optional web search (`AVT_ENABLE_WEB_SEARCH`) makes Claude, GPT and Gemini return citations. Perplexity returns citations natively.

**Typed end to end.** Python is `mypy --strict` (tests included). The frontend's zod schemas are checked at compile time against TypeScript types generated from the backend's OpenAPI spec (`satisfies z.ZodType<…>`), and parsed at runtime; CI fails if the committed spec or generated types drift from the code. That check caught a real mismatch while building the prompts page.

### Scaling to 100k prompts

At 100k prompts × 4 models = 400k calls/day, vendor rate limits — not this system — set the pace; the work is to stay inside them efficiently and keep the database cheap.

- **Workers scale horizontally** — all coordination (buckets, semaphores, breaker, budgets, job de-duplication) already lives in Redis and Postgres. Scale task workers with in-flight calls, analysis workers with CPU.
- **Spread runs over the day.** One daily run per workspace at a configurable hour; at this size, shard prompts across hours (e.g. `hash(prompt_id) % 24`) so the token buckets drain smoothly instead of absorbing one spike.
- **Use batch APIs for scheduled runs.** Anthropic's and OpenAI's batch endpoints cost ~50% less and have separate limits; scheduled runs don't need sub-minute latency. The `ModelProvider` interface would grow a `submit_batch`/`poll_batch` pair.
- **Partition by month.** `responses`, `analyses` and the fact tables partition by `day` (monthly), so retention is `DROP PARTITION` and old months move to cheaper storage; queries are already day-bounded and prune partitions.
- **Rollups stay incremental**; past ~10M facts/day, move the fact tables to a columnar store (ClickHouse/Timescale) and keep Postgres for orchestration.
- **Dispatch** is keyset-paginated in batches of 500; at 400k tasks, pipeline the enqueues and shard dispatch per model so the first answers land within seconds.

## Load test (real numbers)

`uv run avt-loadtest` creates a workspace with 2,000 generated prompts, spawns worker processes, starts a run against **3 models** (6,000 tasks) on the fake provider (40–250 ms simulated latency, 2% injected 429/503s), and measures until the run is completed, analysed and rolled up. It measures the pipeline — fan-out, dispatch, gating, persistence, analysis — not any vendor's API.

Measured on a MacBook (Apple Silicon, 10 cores) with Postgres 17 and Redis on the same machine, 4 task workers × 64 concurrent jobs, 2 analysis workers:

| scenario | tasks | wall time (completed + analysed + rolled up) | throughput | task p50 / p95 | retries | dead |
|---|---|---|---|---|---|---|
| unthrottled (limits 1M req/min) | 6,000 | **28.7 s** | **209 tasks/s** (12.5k/min) | 11.7 s / 21.7 s | 118 | 0 |
| rate-limited, 300 req/min per provider | 1,500 | 45.3 s | 33 tasks/s | 4.3 s / 37.9 s | 30 | 0 |

- Fan-out of 6,000 tasks (one `INSERT … SELECT`): **~110 ms**. Execution alone peaks at ~350 tasks/s; the analysis tail is the remainder.
- The rate-limited run matches the bucket math: 3 × 300 requests admitted as the initial burst, then 15/s across three providers → 600 remaining tasks in ~40 s.
- The 118 retries are the 2% injected faults; every one recovered (0 dead-lettered). Estimated cost of the same run against the real models: $7.22.
- History of this table: an early run did **48 tasks/s**, freezing for ~20 s at a time. The load test drove two fixes, both in the commit log with before/after numbers: the ratio-based circuit breaker (48 → 180 tasks/s) and coalesced run finalisation plus higher analysis concurrency (→ 209–226 tasks/s across runs).

```bash
uv run avt-loadtest --prompts 2000 --workers 4 --analysis-workers 2       # unthrottled
uv run avt-loadtest --prompts 500 --workers 4 --rpm 300                   # provider-limited
```

## Running it

### Everything in Docker

```bash
docker compose up --build
# http://localhost:3000   — demo@avt.dev / demo-password
# http://localhost:8000/docs — OpenAPI
```

Or open it in [Codespaces](https://codespaces.new/MatheoTe/ai-visibility-tracker): [`.devcontainer/`](.devcontainer) runs this same compose file, with the dashboard proxying `/api/v1` to the API so only one forwarded port is needed.

Compose runs Postgres, Redis, migrations, the seed (fictional CRM market, 5 brands, 25 prompts, 30 days of history — idempotent), the API, two task workers, an analysis worker, the scheduler and the frontend. To query real models, copy `.env.example` to `.env` and add any subset of `AVT_ANTHROPIC_API_KEY`, `AVT_OPENAI_API_KEY`, `AVT_GEMINI_API_KEY`, `AVT_PERPLEXITY_API_KEY`; providers without a key keep using the fake.

### Locally

```bash
docker compose up -d postgres redis

cd backend
uv sync
uv run alembic upgrade head
uv run avt-seed                                        # demo workspace + 30 days of history
uv run uvicorn avt.api.main:app --reload               # API on :8000
uv run arq avt.worker.main.WorkerSettings              # task worker (run several)
uv run arq avt.worker.main.AnalysisWorkerSettings      # analysis worker
uv run avt-scheduler                                   # daily runs + sweeper

cd ../frontend
npm install
npm run dev                                            # http://localhost:3000
```

Configuration is environment-only (`AVT_*`, see [`.env.example`](.env.example) and [`config.py`](backend/src/avt/config.py)). Per-provider limits: `AVT_LIMITS_<PROVIDER>_REQUESTS_PER_MINUTE`, `…_TOKENS_PER_MINUTE`, `…_MAX_CONCURRENCY`.

### Observability

- Structured JSON logs (structlog) carrying `request_id`, `run_id`, `task_id`, `provider` through API, workers and scheduler — follow one run with `jq 'select(.run_id=="…")'`.
- `/healthz` (liveness) and `/readyz` (Postgres + Redis).
- Prometheus metrics on the API's `/metrics` and on each worker (`AVT_WORKER_METRICS_PORT`, default 9100): provider calls by outcome, latency histogram, tokens, cost, throttling by reason (rate limit / concurrency / circuit open), breaker trips, task transitions, analyses, runs finalised and in flight.

## Testing

```bash
# backend — unit tests need nothing; integration tests need Postgres + Redis
cd backend
uv run ruff check src tests alembic && uv run mypy
AVT_TEST_DATABASE_URL=postgresql+asyncpg://avt:avt@localhost:5432/avt_test \
AVT_TEST_REDIS_URL=redis://localhost:6379/15 \
uv run pytest --cov=avt

# frontend
cd frontend
npm run typecheck && npm run lint && npm test && npm run build
npm run test:e2e        # starts API, both worker pools and the built app; needs Postgres + Redis
```

- **Backend: 67 tests, 77% line coverage.** Unit (53): brand detection (aliases, separators, word boundaries, overlaps, fuzzy typos vs inflections, URLs), position ranking, URL normalisation and extraction, sentiment and fallback, token bucket (requests, tokens, reconciliation), concurrency leases, circuit breaker (ratio, half-open probe, late results), backoff and jitter, retry budget, fake-provider determinism. Integration (14, real Postgres + Redis + a real arq worker): a full fault-injected run through the API to the dashboard, dead-letter → retry-failed, the retry budget, duplicate deliveries, scheduler races, tenant isolation, validation, ops endpoints, re-analysis, and dashboard KPIs checked against raw rows.
- **Frontend: 31 Vitest tests** (formatting, highlight segmentation, series pivots, color assignment, schema contracts, components, the live-demo backend and its committed snapshot) and **1 Playwright E2E**: sign up → add brand and competitors → add prompts → run → watch it complete → dashboard → drill-down with highlighted mentions. Its first run caught a real bug (the first brand defaulted to *competitor*).
- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): ruff, ruff format, mypy, `alembic check` (migrations match models), pytest with Postgres and Redis services, OpenAPI and generated-types drift checks, typecheck, lint, Vitest, build, and the Playwright E2E.

## Project structure

```
backend/
  src/avt/
    api/            FastAPI app, JWT auth, routers (auth, workspaces, brands, prompts, runs, dashboard, ops)
    providers/      ModelProvider protocol, Anthropic/OpenAI/Gemini/Perplexity, seeded fake, pricing
    resilience/     token bucket, concurrency leases, circuit breaker, backoff + retry budget (Redis/Lua)
    analysis/       normalisation, brand detection, URL extraction, sentiment, versioned pipeline
    services/       run lifecycle, task executor, analysis service, rollups, dashboard read models
    worker/         arq WorkerSettings (execution) and AnalysisWorkerSettings
    scheduler/      daily runs + sweeper
    cli/            seed, load test, OpenAPI export
    db/             SQLAlchemy 2 models
  alembic/          migrations
  tests/unit, tests/integration
frontend/
  src/app/          App Router pages (dashboard, prompts, brands, sources, runs, login)
  src/components/   charts (Recharts), UI primitives, highlighted answers, run monitor
  src/lib/api/      generated OpenAPI types, zod schemas, client, React Query hooks
  e2e/              Playwright
docker-compose.yml  full stack
```

## Roadmap

- Batch-API execution path for scheduled runs (~50% cheaper).
- Prompt sets and tags as dashboard filters; per-tag share of voice.
- Alerts (email/Slack) when share of voice or position moves beyond a threshold.
- Answer-level diffs between days for the same prompt and model.
- Case-sensitive / context-required aliases for brand names that are common words.
- Team invites and roles beyond owner (the membership model already supports them).
- Monthly partitioning of response and fact tables (see scaling notes).

## License

[MIT](LICENSE)

---

Built by **Matheo Telesca** · [LinkedIn](https://www.linkedin.com/in/matheo-telesca-084a20209/)
