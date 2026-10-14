"""Prometheus metrics. The API serves them on ``/metrics``; each worker process
exposes its own registry on ``AVT_WORKER_METRICS_PORT`` (default 9100)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

PROVIDER_REQUESTS = Counter(
    "avt_provider_requests_total",
    "Calls made to model providers, by outcome.",
    ["provider", "outcome"],  # outcome: ok | rate_limited | transient | permanent
)
PROVIDER_LATENCY = Histogram(
    "avt_provider_latency_seconds",
    "Latency of successful provider calls.",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 60),
)
TOKENS = Counter("avt_tokens_total", "Tokens consumed.", ["provider", "direction"])
COST_USD = Counter("avt_cost_usd_total", "Estimated spend in USD.", ["provider"])
TASKS = Counter("avt_tasks_total", "Task state transitions.", ["status"])
THROTTLED = Counter(
    "avt_throttled_total",
    "Times a task was re-scheduled before calling a provider.",
    ["provider", "reason"],  # reason: rate_limit | concurrency | circuit_open
)
BREAKER_TRIPS = Counter("avt_breaker_trips_total", "Circuit breaker openings.", ["provider"])
ANALYSES = Counter("avt_analyses_total", "Analyses written.", ["analyzer"])
RUNS_FINALIZED = Counter("avt_runs_finalized_total", "Runs that reached a terminal status.", ["status"])
RUNS_IN_FLIGHT = Gauge("avt_runs_in_flight", "Runs currently pending or running (sampled by the API).")
