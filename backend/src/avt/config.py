"""Runtime configuration, loaded from environment variables (prefix ``AVT_``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PrivateAttr, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderLimits(BaseSettings):
    """Default per-provider throughput limits. Overridable per provider via env."""

    model_config = SettingsConfigDict(extra="ignore")

    requests_per_minute: int = 600
    tokens_per_minute: int = 400_000
    max_concurrency: int = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AVT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+asyncpg://avt:avt@localhost:5432/avt"
    database_pool_size: int = 20
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: SecretStr = SecretStr("dev-only-change-me-dev-only-change-me")
    jwt_ttl_minutes: int = 60 * 24
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Provider credentials. Any provider without a key is skipped; when *no* key is
    # configured the deterministic fake provider serves every model so the whole
    # system (tests, CI, demo) runs offline.
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    perplexity_api_key: SecretStr | None = None
    force_fake_providers: bool = False
    enable_web_search: bool = False

    anthropic_answer_model: str = "claude-sonnet-5-5"
    anthropic_analysis_model: str = "claude-haiku-4-5-20251001"
    openai_model: str = "gpt-5-mini"
    gemini_model: str = "gemini-2.5-flash"
    perplexity_model: str = "sonar"
    max_output_tokens: int = 1024
    provider_timeout_seconds: float = 60.0

    # Fake provider behaviour (latency simulation + fault injection).
    fake_latency_ms_min: int = 40
    fake_latency_ms_max: int = 250
    fake_error_rate: float = 0.03

    # Execution policy.
    max_task_attempts: int = 5
    retry_budget_ratio: float = 0.25
    retry_budget_floor: int = 20
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    breaker_failure_ratio: float = 0.5
    breaker_min_requests: int = 20
    breaker_window_seconds: int = 30
    breaker_cooldown_seconds: int = 20
    dispatch_batch_size: int = 500
    worker_max_jobs: int = 64
    analysis_max_jobs: int = 48  # analysis is mostly DB round trips; concurrency pays off
    worker_metrics_port: int = 9100  # 0 disables the worker metrics server

    # Scheduler.
    scheduler_tick_seconds: int = 60
    default_daily_run_hour_utc: int = 6

    # Analysis.
    use_llm_analyzer: bool = True

    _limits: dict[str, ProviderLimits] = PrivateAttr(default_factory=dict)

    def limits_for(self, provider: str) -> ProviderLimits:
        """Limits for a provider, read once from ``AVT_LIMITS_<PROVIDER>_*`` env vars
        (e.g. ``AVT_LIMITS_OPENAI_REQUESTS_PER_MINUTE=5000``)."""
        limits = self._limits.get(provider)
        if limits is None:
            limits = self._limits[provider] = ProviderLimits(_env_prefix=f"AVT_LIMITS_{provider.upper()}_")
        return limits

    def override_limits(self, provider: str, limits: ProviderLimits) -> None:
        self._limits[provider] = limits


@lru_cache
def get_settings() -> Settings:
    return Settings()
