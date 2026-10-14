from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from avt.api.deps import SessionDep, WorkspaceDep
from avt.schemas.api import OverviewOut, Page, SourceRow
from avt.services import dashboard

router = APIRouter(prefix="/workspaces/{workspace_id}/dashboard", tags=["dashboard"])

MAX_RANGE_DAYS = 366


def _range(date_from: dt.date | None, date_to: dt.date | None) -> tuple[dt.date, dt.date]:
    end = date_to or dt.datetime.now(dt.UTC).date()
    start = date_from or end - dt.timedelta(days=29)
    if start > end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be <= date_to")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"range is limited to {MAX_RANGE_DAYS} days"
        )
    return start, end


@router.get("/overview")
async def get_overview(
    workspace: WorkspaceDep,
    session: SessionDep,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    model: str | None = None,
) -> OverviewOut:
    """Share of voice, mention rate, average position and sentiment for every tracked
    brand: KPIs (with deltas vs the previous period), a daily series, and a per-model
    breakdown. Defaults to the last 30 days."""
    start, end = _range(date_from, date_to)
    return await dashboard.overview(session, workspace.id, start, end, model)


@router.get("/sources")
async def get_sources(
    workspace: WorkspaceDep,
    session: SessionDep,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[SourceRow]:
    """Leaderboard of the domains AI answers cite most."""
    start, end = _range(date_from, date_to)
    return await dashboard.sources(session, workspace.id, start, end, limit=limit, offset=offset)
