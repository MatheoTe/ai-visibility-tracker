"""Dashboard read models, computed from the daily rollup tables only.

Each query is bounded by (workspaces x days x models x brands) - a 90-day view of
a workspace with 4 models and 10 brands reads at most 3,600 small rows no matter
how many raw responses sit behind them.

Metric definitions (per day, or over a range, for brand *b*):

* mention rate    = responses mentioning b / responses
* share of voice  = responses mentioning b / sum over tracked brands of responses mentioning them
* avg position    = mean rank of b among brands mentioned in the same answer (1 = first)
* sentiment       = mean sentiment score of b's mentions (-1 .. 1)
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from avt.analysis.urls import registrable_domain
from avt.db.models import Brand, DailyBrandStats, DailyModelStats, DailySourceStats
from avt.schemas.api import (
    BrandKpi,
    BrandRef,
    ModelBreakdown,
    OverviewOut,
    Page,
    SeriesPoint,
    SourceRow,
)


@dataclass(slots=True)
class _Acc:
    mentioned: int = 0
    position_sum: int = 0
    sentiment_sum: float = 0.0
    top1: int = 0

    def add(self, row: DailyBrandStats) -> None:
        self.mentioned += row.responses_with_mention
        self.position_sum += row.position_sum
        self.sentiment_sum += row.sentiment_sum
        self.top1 += row.top1_count


def _ratio(num: float, den: float) -> float:
    return round(num / den, 4) if den else 0.0


def _mean(total: float, n: int) -> float | None:
    return round(total / n, 4) if n else None


async def _brand_rows(
    session: AsyncSession, workspace_id: uuid.UUID, start: dt.date, end: dt.date, model: str | None
) -> list[DailyBrandStats]:
    q = select(DailyBrandStats).where(
        DailyBrandStats.workspace_id == workspace_id, DailyBrandStats.day.between(start, end)
    )
    if model:
        q = q.where(DailyBrandStats.model_key == model)
    return list((await session.execute(q)).scalars())


async def _model_rows(
    session: AsyncSession, workspace_id: uuid.UUID, start: dt.date, end: dt.date, model: str | None
) -> list[DailyModelStats]:
    q = select(DailyModelStats).where(
        DailyModelStats.workspace_id == workspace_id, DailyModelStats.day.between(start, end)
    )
    if model:
        q = q.where(DailyModelStats.model_key == model)
    return list((await session.execute(q)).scalars())


def _kpis(
    brand_ids: list[uuid.UUID], rows: list[DailyBrandStats], responses: int
) -> dict[uuid.UUID, BrandKpi]:
    acc: dict[uuid.UUID, _Acc] = defaultdict(_Acc)
    for r in rows:
        acc[r.brand_id].add(r)
    all_mentions = sum(a.mentioned for a in acc.values())
    return {
        b: BrandKpi(
            brand_id=b,
            share_of_voice=_ratio(acc[b].mentioned, all_mentions),
            mention_rate=_ratio(acc[b].mentioned, responses),
            avg_position=_mean(acc[b].position_sum, acc[b].mentioned),
            sentiment=_mean(acc[b].sentiment_sum, acc[b].mentioned),
            top1_rate=_ratio(acc[b].top1, responses),
        )
        for b in brand_ids
    }


async def overview(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    date_from: dt.date,
    date_to: dt.date,
    model: str | None = None,
) -> OverviewOut:
    brands = list(
        (
            await session.execute(
                select(Brand)
                .where(Brand.workspace_id == workspace_id)
                .order_by(Brand.role.desc(), Brand.name)
            )
        ).scalars()
    )
    brand_ids = [b.id for b in brands]
    span = (date_to - date_from).days + 1
    prev_from = date_from - dt.timedelta(days=span)  # previous period of equal length, for deltas

    brand_rows = await _brand_rows(session, workspace_id, prev_from, date_to, model)
    model_rows = await _model_rows(session, workspace_id, prev_from, date_to, model)
    current = [r for r in brand_rows if r.day >= date_from]
    previous = [r for r in brand_rows if r.day < date_from]
    cur_models = [m for m in model_rows if m.day >= date_from]
    responses = sum(m.responses for m in cur_models)
    prev_responses = sum(m.responses for m in model_rows if m.day < date_from)

    kpis = _kpis(brand_ids, current, responses)
    if prev_responses:
        prev = _kpis(brand_ids, previous, prev_responses)
        for b, k in kpis.items():
            k.share_of_voice_delta = round(k.share_of_voice - prev[b].share_of_voice, 4)
            k.mention_rate_delta = round(k.mention_rate - prev[b].mention_rate, 4)

    # Daily series (summed over models unless filtered).
    per_day_responses: dict[dt.date, int] = defaultdict(int)
    for m in cur_models:
        per_day_responses[m.day] += m.responses
    per_day: dict[tuple[dt.date, uuid.UUID], _Acc] = defaultdict(_Acc)
    for r in current:
        per_day[(r.day, r.brand_id)].add(r)
    day_mentions: dict[dt.date, int] = defaultdict(int)
    for (day, _), a in per_day.items():
        day_mentions[day] += a.mentioned
    series = [
        SeriesPoint(
            day=day,
            brand_id=b,
            share_of_voice=_ratio(per_day[(day, b)].mentioned, day_mentions[day]),
            mention_rate=_ratio(per_day[(day, b)].mentioned, per_day_responses[day]),
            avg_position=_mean(per_day[(day, b)].position_sum, per_day[(day, b)].mentioned),
            sentiment=_mean(per_day[(day, b)].sentiment_sum, per_day[(day, b)].mentioned),
        )
        for day in sorted(per_day_responses)
        for b in brand_ids
    ]

    # Per model, over the whole range.
    per_model_responses: dict[str, int] = defaultdict(int)
    for m in cur_models:
        per_model_responses[m.model_key] += m.responses
    per_model: dict[tuple[str, uuid.UUID], _Acc] = defaultdict(_Acc)
    for r in current:
        per_model[(r.model_key, r.brand_id)].add(r)
    by_model = [
        ModelBreakdown(
            model_key=mk,
            brand_id=b,
            responses=n,
            mention_rate=_ratio(per_model[(mk, b)].mentioned, n),
            avg_position=_mean(per_model[(mk, b)].position_sum, per_model[(mk, b)].mentioned),
            sentiment=_mean(per_model[(mk, b)].sentiment_sum, per_model[(mk, b)].mentioned),
        )
        for mk, n in sorted(per_model_responses.items())
        for b in brand_ids
    ]

    return OverviewOut(
        date_from=date_from,
        date_to=date_to,
        responses=responses,
        cost_usd=sum((m.cost_usd for m in cur_models), Decimal(0)),
        brands=[BrandRef(id=b.id, name=b.name, role=b.role, color=b.color) for b in brands],
        kpis=[kpis[b] for b in brand_ids],
        series=series,
        by_model=by_model,
    )


async def sources(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    date_from: dt.date,
    date_to: dt.date,
    *,
    limit: int,
    offset: int,
) -> Page[SourceRow]:
    where = (DailySourceStats.workspace_id == workspace_id, DailySourceStats.day.between(date_from, date_to))
    totals = (
        await session.execute(
            select(
                func.count(func.distinct(DailySourceStats.domain)),
                func.coalesce(func.sum(DailySourceStats.citations), 0),
            ).where(*where)
        )
    ).one()
    citations = func.sum(DailySourceStats.citations).label("citations")
    rows = await session.execute(
        select(DailySourceStats.domain, citations, func.sum(DailySourceStats.responses_citing))
        .where(*where)
        .group_by(DailySourceStats.domain)
        .order_by(citations.desc(), DailySourceStats.domain)
        .limit(limit)
        .offset(offset)
    )
    brand_domains = {
        registrable_domain(d)
        for ds in (
            await session.execute(select(Brand.domains).where(Brand.workspace_id == workspace_id))
        ).scalars()
        for d in ds
    }
    total_citations = int(totals[1])
    return Page(
        items=[
            SourceRow(
                domain=domain,
                citations=int(n),
                responses_citing=int(rc),
                citation_share=_ratio(int(n), total_citations),
                is_brand_domain=domain in brand_domains,
            )
            for domain, n, rc in rows
        ],
        total=int(totals[0]),
        limit=limit,
        offset=offset,
    )
