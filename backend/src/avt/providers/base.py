"""The ``ModelProvider`` contract every LLM backend implements.

Providers are deliberately thin: they translate one request into one API call and
classify failures. Retries, backoff, rate limiting and circuit breaking live in the
executor so the policy is identical for every vendor (vendor SDK retries are
disabled for that reason).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    prompt: str
    model: str
    max_output_tokens: int = 1024
    day: dt.date = field(default_factory=dt.date.today)
    attempt: int = 1
    # Only consumed by the fake provider to simulate a market around the tracked
    # brands. Real providers never see it - they get the bare prompt.
    simulation_brands: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    model_name: str
    input_tokens: int
    output_tokens: int
    citations: list[str]
    latency_ms: int


class ProviderError(Exception):
    """Base class. ``retryable`` drives the executor's retry decision."""

    retryable: bool = False

    def __init__(
        self, message: str, *, status_code: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class RateLimitedError(ProviderError):
    """HTTP 429 or a provider-specific overload signal."""

    retryable = True


class TransientProviderError(ProviderError):
    """5xx, timeouts, connection resets."""

    retryable = True


class PermanentProviderError(ProviderError):
    """4xx other than 429: bad request, auth, content policy. Never retried."""

    retryable = False


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

    async def aclose(self) -> None: ...


def classify_http_status(status: int | None, message: str, retry_after: float | None = None) -> ProviderError:
    """Map an HTTP status to the right error class (shared by every provider)."""
    if status == 429:
        return RateLimitedError(message, status_code=status, retry_after=retry_after)
    if status is None or status >= 500 or status in (408, 409, 529):
        return TransientProviderError(message, status_code=status, retry_after=retry_after)
    return PermanentProviderError(message, status_code=status)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
