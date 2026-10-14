"""Daily rollups the dashboard reads.

Why tables rebuilt per (workspace, day) instead of a materialized view:
``REFRESH MATERIALIZED VIEW`` recomputes *everything* for *every* tenant, so its
cost grows with total history. A run only ever touches one or two days of one
workspace, so we delete-and-reinsert exactly those slices inside one transaction.
Readers never see a half-built day, the cost of a refresh is bounded by the size
of a day, and re-analysis can rebuild any date range on demand.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from avt.db.models import DailyBrandStats, DailyModelStats, DailySourceStats

_MODEL_STATS = text(
    """
    INSERT INTO daily_model_stats (
        workspace_id, day, model_key, responses, input_tokens, output_tokens, cost_usd
    )
    SELECT workspace_id, day, model_key, count(*), sum(input_tokens), sum(output_tokens), sum(cost_usd)
    FROM responses
    WHERE workspace_id = :ws AND day = ANY(:days)
    GROUP BY workspace_id, day, model_key
    """
)

_BRAND_STATS = text(
    """
    INSERT INTO daily_brand_stats (
        workspace_id, day, model_key, brand_id, responses_with_mention, mention_total,
        position_sum, top1_count, sentiment_sum, positive_count, negative_count
    )
    SELECT workspace_id, day, model_key, brand_id,
           count(*), sum(mention_count), sum(position),
           count(*) FILTER (WHERE position = 1),
           sum(sentiment_score),
           count(*) FILTER (WHERE sentiment = 'positive'),
           count(*) FILTER (WHERE sentiment = 'negative')
    FROM brand_mentions
    WHERE workspace_id = :ws AND analysis_version = :version AND day = ANY(:days)
    GROUP BY workspace_id, day, model_key, brand_id
    """
)

_SOURCE_STATS = text(
    """
    INSERT INTO daily_source_stats (workspace_id, day, domain, citations, responses_citing)
    SELECT workspace_id, day, domain, count(*), count(DISTINCT response_id)
    FROM cited_sources
    WHERE workspace_id = :ws AND analysis_version = :version AND day = ANY(:days)
    GROUP BY workspace_id, day, domain
    """
)


async def rebuild_daily_stats(
    session: AsyncSession, workspace_id: uuid.UUID, days: Sequence[dt.date], version: int
) -> None:
    if not days:
        return
    day_list = sorted(set(days))
    # Serialise concurrent rebuilds of the same workspace (two runs finishing at once).
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"rollup:{workspace_id}"})
    for model in (DailyModelStats, DailyBrandStats, DailySourceStats):
        await session.execute(
            delete(model).where(model.workspace_id == workspace_id, model.day.in_(day_list))
        )
    params = {"ws": workspace_id, "days": day_list, "version": version}
    await session.execute(_MODEL_STATS, {"ws": workspace_id, "days": day_list})
    await session.execute(_BRAND_STATS, params)
    await session.execute(_SOURCE_STATS, params)
    await session.commit()
