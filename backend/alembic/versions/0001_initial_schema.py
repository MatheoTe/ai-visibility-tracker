"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-14 09:18:06.617071
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_model_stats",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("responses", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "day", "model_key", name=op.f("pk_daily_model_stats")),
    )
    op.create_table(
        "daily_source_stats",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("citations", sa.Integer(), nullable=False),
        sa.Column("responses_citing", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "day", "domain", name=op.f("pk_daily_source_stats")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index("uq_users_email_lower", "users", [sa.literal_column("lower(email)")], unique=True)
    op.create_table(
        "workspaces",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("models", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("schedule_enabled", sa.Boolean(), nullable=False),
        sa.Column("daily_run_hour_utc", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("daily_run_hour_utc BETWEEN 0 AND 23", name=op.f("ck_workspaces_run_hour_range")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_table(
        "brands",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("domains", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('primary', 'competitor')", name=op.f("ck_brands_role_valid")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_brands_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brands")),
    )
    op.create_index(
        "uq_brands_one_primary",
        "brands",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("role = 'primary'"),
    )
    op.create_index(
        "uq_brands_workspace_name", "brands", ["workspace_id", sa.literal_column("lower(name)")], unique=True
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'member')", name=op.f("ck_memberships_role_valid")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_memberships_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "workspace_id", name=op.f("pk_memberships")),
    )
    op.create_index(op.f("ix_memberships_workspace_id"), "memberships", ["workspace_id"], unique=False)
    op.create_table(
        "prompts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_prompts_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompts")),
        sa.UniqueConstraint("workspace_id", "text_hash", name=op.f("uq_prompts_workspace_id")),
    )
    op.create_index("ix_prompts_workspace_created", "prompts", ["workspace_id", "created_at"], unique=False)
    op.create_table(
        "runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.Date(), nullable=True),
        sa.Column("models", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("total_tasks", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'partial', 'completed', 'failed')",
            name=op.f("ck_runs_status_valid"),
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'backfill')", name=op.f("ck_runs_trigger_valid")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_runs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index("ix_runs_workspace_created", "runs", ["workspace_id", "created_at"], unique=False)
    op.create_index(
        "uq_runs_scheduled_once_per_day",
        "runs",
        ["workspace_id", "scheduled_for"],
        unique=True,
        postgresql_where=sa.text("trigger = 'scheduled'"),
    )
    op.create_table(
        "daily_brand_stats",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=False),
        sa.Column("responses_with_mention", sa.Integer(), nullable=False),
        sa.Column("mention_total", sa.Integer(), nullable=False),
        sa.Column("position_sum", sa.Integer(), nullable=False),
        sa.Column("top1_count", sa.Integer(), nullable=False),
        sa.Column("sentiment_sum", sa.Float(), nullable=False),
        sa.Column("positive_count", sa.Integer(), nullable=False),
        sa.Column("negative_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brands.id"], name=op.f("fk_daily_brand_stats_brand_id_brands"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id", "day", "model_key", "brand_id", name=op.f("pk_daily_brand_stats")
        ),
    )
    op.create_table(
        "run_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("prompt_id", sa.UUID(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'succeeded', 'dead')",
            name=op.f("ck_run_tasks_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompts.id"], name=op.f("fk_run_tasks_prompt_id_prompts"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_tasks_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_tasks")),
        sa.UniqueConstraint("run_id", "prompt_id", "model_key", name="uq_run_tasks_idempotency"),
    )
    op.create_index(op.f("ix_run_tasks_prompt_id"), "run_tasks", ["prompt_id"], unique=False)
    op.create_index("ix_run_tasks_run_status", "run_tasks", ["run_id", "status"], unique=False)
    op.create_table(
        "responses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("prompt_id", sa.UUID(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "provider_citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["run_tasks.id"], name=op.f("fk_responses_task_id_run_tasks"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_responses")),
        sa.UniqueConstraint("task_id", name=op.f("uq_responses_task_id")),
    )
    op.create_index("ix_responses_prompt_day", "responses", ["prompt_id", "day"], unique=False)
    op.create_index(op.f("ix_responses_run_id"), "responses", ["run_id"], unique=False)
    op.create_index("ix_responses_workspace_day", "responses", ["workspace_id", "day"], unique=False)
    op.create_table(
        "analyses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("response_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("analyzer", sa.Text(), nullable=False),
        sa.Column("overall_sentiment", sa.Text(), nullable=False),
        sa.Column("primary_mentioned", sa.Boolean(), nullable=False),
        sa.Column("primary_position", sa.SmallInteger(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["response_id"],
            ["responses.id"],
            name=op.f("fk_analyses_response_id_responses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyses")),
        sa.UniqueConstraint("response_id", "version", name=op.f("uq_analyses_response_id")),
    )
    op.create_table(
        "brand_mentions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("analysis_version", sa.SmallInteger(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=False),
        sa.Column("response_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_id", sa.UUID(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("mention_count", sa.SmallInteger(), nullable=False),
        sa.Column("first_offset", sa.Integer(), nullable=False),
        sa.Column("sentiment", sa.Text(), nullable=False),
        sa.Column("sentiment_score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["analyses.id"],
            name=op.f("fk_brand_mentions_analysis_id_analyses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brands.id"], name=op.f("fk_brand_mentions_brand_id_brands"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand_mentions")),
        sa.UniqueConstraint("analysis_id", "brand_id", name=op.f("uq_brand_mentions_analysis_id")),
    )
    op.create_index(op.f("ix_brand_mentions_brand_id"), "brand_mentions", ["brand_id"], unique=False)
    op.create_index("ix_brand_mentions_prompt_day", "brand_mentions", ["prompt_id", "day"], unique=False)
    op.create_index(op.f("ix_brand_mentions_response_id"), "brand_mentions", ["response_id"], unique=False)
    op.create_index(
        "ix_brand_mentions_ws_day",
        "brand_mentions",
        ["workspace_id", "analysis_version", "day"],
        unique=False,
    )
    op.create_table(
        "cited_sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("analysis_version", sa.SmallInteger(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("response_id", sa.BigInteger(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.CheckConstraint("origin IN ('inline', 'provider')", name=op.f("ck_cited_sources_origin_valid")),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["analyses.id"],
            name=op.f("fk_cited_sources_analysis_id_analyses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cited_sources")),
        sa.UniqueConstraint("analysis_id", "url", name=op.f("uq_cited_sources_analysis_id")),
    )
    op.create_index(op.f("ix_cited_sources_response_id"), "cited_sources", ["response_id"], unique=False)
    op.create_index(
        "ix_cited_sources_ws_day", "cited_sources", ["workspace_id", "analysis_version", "day"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_cited_sources_ws_day", table_name="cited_sources")
    op.drop_index(op.f("ix_cited_sources_response_id"), table_name="cited_sources")
    op.drop_table("cited_sources")
    op.drop_index("ix_brand_mentions_ws_day", table_name="brand_mentions")
    op.drop_index(op.f("ix_brand_mentions_response_id"), table_name="brand_mentions")
    op.drop_index("ix_brand_mentions_prompt_day", table_name="brand_mentions")
    op.drop_index(op.f("ix_brand_mentions_brand_id"), table_name="brand_mentions")
    op.drop_table("brand_mentions")
    op.drop_table("analyses")
    op.drop_index("ix_responses_workspace_day", table_name="responses")
    op.drop_index(op.f("ix_responses_run_id"), table_name="responses")
    op.drop_index("ix_responses_prompt_day", table_name="responses")
    op.drop_table("responses")
    op.drop_index("ix_run_tasks_run_status", table_name="run_tasks")
    op.drop_index(op.f("ix_run_tasks_prompt_id"), table_name="run_tasks")
    op.drop_table("run_tasks")
    op.drop_table("daily_brand_stats")
    op.drop_index(
        "uq_runs_scheduled_once_per_day", table_name="runs", postgresql_where=sa.text("trigger = 'scheduled'")
    )
    op.drop_index("ix_runs_workspace_created", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_prompts_workspace_created", table_name="prompts")
    op.drop_table("prompts")
    op.drop_index(op.f("ix_memberships_workspace_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index("uq_brands_workspace_name", table_name="brands")
    op.drop_index("uq_brands_one_primary", table_name="brands", postgresql_where=sa.text("role = 'primary'"))
    op.drop_table("brands")
    op.drop_table("workspaces")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
    op.drop_table("daily_source_stats")
    op.drop_table("daily_model_stats")
