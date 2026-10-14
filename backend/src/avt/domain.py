"""Domain enums and value objects shared by every layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BrandRole(StrEnum):
    PRIMARY = "primary"
    COMPETITOR = "competitor"


class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    BACKFILL = "backfill"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.PARTIAL, RunStatus.COMPLETED, RunStatus.FAILED)


class TaskStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEAD = "dead"  # dead-lettered: retries or retry budget exhausted

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.SUCCEEDED, TaskStatus.DEAD)


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class ModelKey:
    """``provider:model`` identifier, e.g. ``anthropic:claude-sonnet-5-5``."""

    provider: str
    model: str

    @classmethod
    def parse(cls, raw: str) -> ModelKey:
        provider, sep, model = raw.partition(":")
        if not sep or not provider or not model:
            raise ValueError(f"invalid model key {raw!r}, expected 'provider:model'")
        return cls(provider=provider, model=model)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"
