"""ORM models.

Layout, from write-heavy to read-heavy:

* ``runs`` / ``run_tasks``      - orchestration state (one task per prompt x model).
* ``responses``                 - raw model output, written exactly once per task.
* ``analyses``                  - parsed output, one row per (response, analysis_version),
                                  so analysis can be re-run without touching raw data.
* ``brand_mentions`` /
  ``cited_sources``             - narrow fact tables, denormalised with ``workspace_id``,
                                  ``day`` and ``model_key`` so aggregation never joins.
* ``daily_*_stats``             - incrementally rebuilt daily rollups the dashboard reads.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from avt.db.base import Base
from avt.domain import BrandRole, RunStatus, RunTrigger, TaskStatus


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sa_text("gen_random_uuid()"))


def _created_at() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _check_in(column: str, values: list[str]) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"{column}_valid")


# --------------------------------------------------------------------------- tenancy


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (Index("uq_users_email_lower", func.lower(email), unique=True),)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    models: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    daily_run_hour_utc: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=6)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (CheckConstraint("daily_run_hour_utc BETWEEN 0 AND 23", name="run_hour_range"),)


class Membership(Base):
    __tablename__ = "memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, default="owner")
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (_check_in("role", ["owner", "member"]),)


# --------------------------------------------------------------------------- catalog


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default=BrandRole.COMPETITOR)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sa_text("'{}'"))
    domains: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sa_text("'{}'"))
    color: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _check_in("role", [r.value for r in BrandRole]),
        Index("uq_brands_workspace_name", "workspace_id", func.lower(name), unique=True),
        # At most one primary ("own") brand per workspace.
        Index(
            "uq_brands_one_primary",
            "workspace_id",
            unique=True,
            postgresql_where=sa_text("role = 'primary'"),
        ),
    )


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sa_text("'{}'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("workspace_id", "text_hash"),
        Index("ix_prompts_workspace_created", "workspace_id", "created_at"),
    )


# --------------------------------------------------------------------------- orchestration


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=RunStatus.PENDING)
    scheduled_for: Mapped[dt.date | None] = mapped_column(Date)
    models: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    created_at: Mapped[dt.datetime] = _created_at()
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _check_in("trigger", [t.value for t in RunTrigger]),
        _check_in("status", [s.value for s in RunStatus]),
        Index("ix_runs_workspace_created", "workspace_id", "created_at"),
        # Double-fire guard for the scheduler: one scheduled run per workspace per day,
        # enforced by the database rather than by the scheduler process.
        Index(
            "uq_runs_scheduled_once_per_day",
            "workspace_id",
            "scheduled_for",
            unique=True,
            postgresql_where=sa_text("trigger = 'scheduled'"),
        ),
    )


class RunTask(Base):
    __tablename__ = "run_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    prompt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_key: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=TaskStatus.PENDING)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # The idempotency key: a retry, a duplicate enqueue or a crashed worker can
        # never create a second task (and therefore a second result) for this triple.
        UniqueConstraint("run_id", "prompt_id", "model_key", name="uq_run_tasks_idempotency"),
        _check_in("status", [s.value for s in TaskStatus]),
        Index("ix_run_tasks_run_status", "run_id", "status"),
    )

    @property
    def idempotency_key(self) -> str:
        return f"{self.run_id}:{self.prompt_id}:{self.model_key}"


# --------------------------------------------------------------------------- results


class Response(Base):
    __tablename__ = "responses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("run_tasks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    prompt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_key: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    provider_citations: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'[]'::jsonb")
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    day: Mapped[dt.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        Index("ix_responses_workspace_day", "workspace_id", "day"),
        Index("ix_responses_prompt_day", "prompt_id", "day"),
    )


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("responses.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    analyzer: Mapped[str] = mapped_column(Text, nullable=False)
    overall_sentiment: Mapped[str] = mapped_column(Text, nullable=False)
    primary_mentioned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    primary_position: Mapped[int | None] = mapped_column(SmallInteger)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (UniqueConstraint("response_id", "version"),)


class BrandMention(Base):
    __tablename__ = "brand_mentions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False)
    analysis_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    brand_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    response_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    prompt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_key: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[dt.date] = mapped_column(Date, nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mention_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    first_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    sentiment: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment_score: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "brand_id"),
        Index("ix_brand_mentions_ws_day", "workspace_id", "analysis_version", "day"),
    )


class CitedSource(Base):
    __tablename__ = "cited_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False)
    analysis_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    response_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    model_key: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[dt.date] = mapped_column(Date, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("analysis_id", "url"),
        Index("ix_cited_sources_ws_day", "workspace_id", "analysis_version", "day"),
        _check_in("origin", ["inline", "provider"]),
    )


# --------------------------------------------------------------------------- rollups


class DailyModelStats(Base):
    """Responses per (workspace, day, model) - the denominator for mention rate."""

    __tablename__ = "daily_model_stats"

    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    model_key: Mapped[str] = mapped_column(Text, primary_key=True)
    responses: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)


class DailyBrandStats(Base):
    __tablename__ = "daily_brand_stats"

    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    model_key: Mapped[str] = mapped_column(Text, primary_key=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), primary_key=True)
    responses_with_mention: Mapped[int] = mapped_column(Integer, nullable=False)
    mention_total: Mapped[int] = mapped_column(Integer, nullable=False)
    position_sum: Mapped[int] = mapped_column(Integer, nullable=False)
    top1_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sentiment_sum: Mapped[float] = mapped_column(Float, nullable=False)
    positive_count: Mapped[int] = mapped_column(Integer, nullable=False)
    negative_count: Mapped[int] = mapped_column(Integer, nullable=False)


class DailySourceStats(Base):
    __tablename__ = "daily_source_stats"

    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    citations: Mapped[int] = mapped_column(Integer, nullable=False)
    responses_citing: Mapped[int] = mapped_column(Integer, nullable=False)
