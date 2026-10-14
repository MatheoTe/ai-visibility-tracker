"""Request / response models of the public REST API (they define the OpenAPI spec)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from avt.domain import BrandRole, ModelKey


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    """Offset pagination - used for user-managed lists (brands, prompts, runs)."""

    items: list[T]
    total: int
    limit: int
    offset: int


class CursorPage[T](BaseModel):
    """Keyset pagination - used for high-volume, append-only lists (tasks, responses)."""

    items: list[T]
    next_cursor: str | None


# --------------------------------------------------------------------------- auth


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str = Field(default="My workspace", min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - not a secret
    expires_in: int


class WorkspaceOut(_Out):
    id: uuid.UUID
    name: str
    models: list[str]
    schedule_enabled: bool
    daily_run_hour_utc: int
    created_at: dt.datetime


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    workspaces: list[WorkspaceOut]


# --------------------------------------------------------------------------- workspace


def _validate_models(models: list[str] | None) -> list[str] | None:
    if models is None:
        return None
    if not models:
        raise ValueError("at least one model is required")
    return [str(ModelKey.parse(m)) for m in dict.fromkeys(models)]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    models: list[str] | None = None

    _models = field_validator("models")(_validate_models)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    models: list[str] | None = None
    schedule_enabled: bool | None = None
    daily_run_hour_utc: int | None = Field(default=None, ge=0, le=23)

    _models = field_validator("models")(_validate_models)


# --------------------------------------------------------------------------- brands


def _clean_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: BrandRole = BrandRole.COMPETITOR
    aliases: list[str] = Field(default_factory=list, max_length=20)
    domains: list[str] = Field(default_factory=list, max_length=20)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")

    _lists = field_validator("aliases", "domains")(_clean_list)


class BrandUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: BrandRole | None = None
    aliases: list[str] | None = Field(default=None, max_length=20)
    domains: list[str] | None = Field(default=None, max_length=20)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class BrandOut(_Out):
    id: uuid.UUID
    name: str
    role: BrandRole
    aliases: list[str]
    domains: list[str]
    color: str | None
    created_at: dt.datetime


# --------------------------------------------------------------------------- prompts


class PromptIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    _tags = field_validator("tags")(_clean_list)


class PromptUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=3, max_length=2000)
    tags: list[str] | None = None
    is_active: bool | None = None


class PromptOut(_Out):
    id: uuid.UUID
    text: str
    tags: list[str]
    is_active: bool
    created_at: dt.datetime
    responses_7d: int = 0
    mention_rate_7d: float | None = None


class PromptImportResult(BaseModel):
    created: int
    skipped_duplicates: int
    errors: list[str]


# --------------------------------------------------------------------------- runs


class RunCreate(BaseModel):
    models: list[str] | None = None

    _models = field_validator("models")(_validate_models)


class RunOut(_Out):
    id: uuid.UUID
    trigger: str
    status: str
    scheduled_for: dt.date | None
    models: list[str]
    total_tasks: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class ProviderCost(BaseModel):
    model_key: str
    responses: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class RunDetailOut(RunOut):
    counts: dict[str, int]
    analyzed: int
    percent: float
    by_model: list[ProviderCost]


class TaskOut(BaseModel):
    id: int
    prompt_id: uuid.UUID
    prompt_text: str
    model_key: str
    status: str
    attempts: int
    last_error: str | None
    updated_at: dt.datetime


class RetryFailedOut(BaseModel):
    requeued: int


class ReanalyzeIn(BaseModel):
    date_from: dt.date
    date_to: dt.date


# --------------------------------------------------------------------------- dashboard


class BrandRef(BaseModel):
    id: uuid.UUID
    name: str
    role: BrandRole
    color: str | None


class BrandKpi(BaseModel):
    brand_id: uuid.UUID
    share_of_voice: float
    mention_rate: float
    avg_position: float | None
    sentiment: float | None
    top1_rate: float
    share_of_voice_delta: float | None = None
    mention_rate_delta: float | None = None


class SeriesPoint(BaseModel):
    day: dt.date
    brand_id: uuid.UUID
    share_of_voice: float
    mention_rate: float
    avg_position: float | None
    sentiment: float | None


class ModelBreakdown(BaseModel):
    model_key: str
    brand_id: uuid.UUID
    responses: int
    mention_rate: float
    avg_position: float | None
    sentiment: float | None


class OverviewOut(BaseModel):
    date_from: dt.date
    date_to: dt.date
    responses: int
    cost_usd: Decimal
    brands: list[BrandRef]
    kpis: list[BrandKpi]
    series: list[SeriesPoint]
    by_model: list[ModelBreakdown]


class SourceRow(BaseModel):
    domain: str
    citations: int
    responses_citing: int
    citation_share: float
    is_brand_domain: bool


class MentionSpan(BaseModel):
    start: int
    end: int
    kind: str


class ResponseMention(BaseModel):
    brand_id: uuid.UUID
    brand: str
    position: int
    sentiment: str
    score: float
    spans: list[MentionSpan]


class ResponseSource(BaseModel):
    url: str
    domain: str
    rank: int
    origin: str


class PromptResponseOut(BaseModel):
    id: int
    run_id: uuid.UUID
    day: dt.date
    model_key: str
    model_name: str
    text: str
    latency_ms: int
    cost_usd: Decimal
    analyzed: bool
    overall_sentiment: str | None
    mentions: list[ResponseMention]
    sources: list[ResponseSource]
