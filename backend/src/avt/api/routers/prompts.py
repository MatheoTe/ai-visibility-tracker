from __future__ import annotations

import base64
import csv
import datetime as dt
import hashlib
import io
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from avt.analysis.normalize import normalize
from avt.analysis.pipeline import ANALYSIS_VERSION
from avt.api.deps import SessionDep, WorkspaceDep
from avt.db.models import Analysis, Brand, BrandMention, Prompt
from avt.db.models import Response as ResponseRow
from avt.domain import BrandRole
from avt.schemas.api import (
    CursorPage,
    Page,
    PromptImportResult,
    PromptIn,
    PromptOut,
    PromptResponseOut,
    PromptUpdate,
    ResponseMention,
    ResponseSource,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/prompts", tags=["prompts"])

MAX_IMPORT_ROWS = 10_000


def prompt_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


@router.get("")
async def list_prompts(
    workspace: WorkspaceDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[PromptOut]:
    """Prompts with 7-day visibility of the primary brand (two grouped queries per
    page, never one per prompt)."""
    conditions = [Prompt.workspace_id == workspace.id]
    if q:
        conditions.append(Prompt.text.ilike(f"%{q}%"))
    if active is not None:
        conditions.append(Prompt.is_active.is_(active))
    where = and_(*conditions)
    total = (await session.execute(select(func.count()).select_from(Prompt).where(where))).scalar_one()
    prompts = list(
        (
            await session.execute(
                select(Prompt)
                .where(where)
                .order_by(Prompt.created_at.desc(), Prompt.id)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
    )
    ids = [p.id for p in prompts]
    since = dt.date.today() - dt.timedelta(days=7)
    responses: dict[uuid.UUID, int] = {}
    mentions: dict[uuid.UUID, int] = {}
    if ids:
        responses = {
            pid: int(n)
            for pid, n in (
                await session.execute(
                    select(ResponseRow.prompt_id, func.count())
                    .where(ResponseRow.prompt_id.in_(ids), ResponseRow.day >= since)
                    .group_by(ResponseRow.prompt_id)
                )
            )
        }
        mentions = {
            pid: int(n)
            for pid, n in (
                await session.execute(
                    select(BrandMention.prompt_id, func.count())
                    .join(Brand, Brand.id == BrandMention.brand_id)
                    .where(
                        BrandMention.prompt_id.in_(ids),
                        BrandMention.day >= since,
                        BrandMention.analysis_version == ANALYSIS_VERSION,
                        Brand.role == BrandRole.PRIMARY,
                    )
                    .group_by(BrandMention.prompt_id)
                )
            )
        }
    items = []
    for p in prompts:
        out = PromptOut.model_validate(p)
        n = responses.get(p.id, 0)
        out.responses_7d = n
        out.mention_rate_7d = round(mentions.get(p.id, 0) / n, 4) if n else None
        items.append(out)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_prompt(body: PromptIn, workspace: WorkspaceDep, session: SessionDep) -> PromptOut:
    prompt = Prompt(
        workspace_id=workspace.id, text=body.text.strip(), text_hash=prompt_hash(body.text), tags=body.tags
    )
    session.add(prompt)
    try:
        await session.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "this prompt already exists") from exc
    return PromptOut.model_validate(prompt)


@router.post("/import")
async def import_prompts(
    file: UploadFile, workspace: WorkspaceDep, session: SessionDep
) -> PromptImportResult:
    """Bulk import from CSV. Uses a ``prompt`` column if present (else the first
    column) and an optional ``tags`` column separated by ``;``. Duplicates of
    existing prompts are skipped, not errors."""
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(raw))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return PromptImportResult(created=0, skipped_duplicates=0, errors=["file is empty"])
    header = [h.strip().lower() for h in rows[0]]
    text_col, tags_col, start = 0, None, 0
    if "prompt" in header or "text" in header:
        text_col = header.index("prompt") if "prompt" in header else header.index("text")
        tags_col = header.index("tags") if "tags" in header else None
        start = 1
    errors: list[str] = []
    values: dict[str, dict[str, Any]] = {}
    for line_no, row in enumerate(rows[start : start + MAX_IMPORT_ROWS], start=start + 1):
        text = row[text_col].strip() if text_col < len(row) else ""
        if not 3 <= len(text) <= 2000:
            errors.append(f"line {line_no}: prompt must be 3-2000 characters")
            continue
        tags = []
        if tags_col is not None and tags_col < len(row):
            tags = [t.strip() for t in row[tags_col].split(";") if t.strip()]
        values.setdefault(
            prompt_hash(text),
            {"workspace_id": workspace.id, "text": text, "text_hash": prompt_hash(text), "tags": tags},
        )
    if len(rows) - start > MAX_IMPORT_ROWS:
        errors.append(f"only the first {MAX_IMPORT_ROWS} rows were imported")
    created = 0
    if values:
        result = await session.execute(
            insert(Prompt)
            .values(list(values.values()))
            .on_conflict_do_nothing(index_elements=["workspace_id", "text_hash"])
            .returning(Prompt.id)
        )
        created = len(result.scalars().all())
        await session.commit()
    valid_rows = len(rows) - start - len([e for e in errors if e.startswith("line")])
    return PromptImportResult(
        created=created,
        skipped_duplicates=max(0, min(valid_rows, MAX_IMPORT_ROWS) - created),
        errors=errors[:50],
    )


async def _get(session: SessionDep, workspace_id: uuid.UUID, prompt_id: uuid.UUID) -> Prompt:
    prompt = (
        await session.execute(
            select(Prompt).where(Prompt.id == prompt_id, Prompt.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if prompt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "prompt not found")
    return prompt


@router.get("/{prompt_id}")
async def get_prompt(prompt_id: uuid.UUID, workspace: WorkspaceDep, session: SessionDep) -> PromptOut:
    return PromptOut.model_validate(await _get(session, workspace.id, prompt_id))


@router.patch("/{prompt_id}")
async def update_prompt(
    prompt_id: uuid.UUID, body: PromptUpdate, workspace: WorkspaceDep, session: SessionDep
) -> PromptOut:
    prompt = await _get(session, workspace.id, prompt_id)
    data = body.model_dump(exclude_unset=True)
    if "text" in data and data["text"] is not None:
        prompt.text = data["text"].strip()
        prompt.text_hash = prompt_hash(prompt.text)
    if data.get("tags") is not None:
        prompt.tags = data["tags"]
    if data.get("is_active") is not None:
        prompt.is_active = data["is_active"]
    try:
        await session.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "this prompt already exists") from exc
    return PromptOut.model_validate(prompt)


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(prompt_id: uuid.UUID, workspace: WorkspaceDep, session: SessionDep) -> Response:
    await _get(session, workspace.id, prompt_id)
    await session.execute(delete(Prompt).where(Prompt.id == prompt_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- drill-down


def _encode_cursor(day: dt.date, rid: int) -> str:
    return base64.urlsafe_b64encode(f"{day.isoformat()}|{rid}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[dt.date, int]:
    try:
        day, rid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return dt.date.fromisoformat(day), int(rid)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc


@router.get("/{prompt_id}/responses")
async def prompt_responses(
    prompt_id: uuid.UUID,
    workspace: WorkspaceDep,
    session: SessionDep,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    model: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorPage[PromptResponseOut]:
    """Every answer to this prompt, newest first, with mentions/sources from the
    current analysis version. Keyset-paginated on (day, id)."""
    await _get(session, workspace.id, prompt_id)
    conditions = [ResponseRow.prompt_id == prompt_id, ResponseRow.workspace_id == workspace.id]
    if date_from:
        conditions.append(ResponseRow.day >= date_from)
    if date_to:
        conditions.append(ResponseRow.day <= date_to)
    if model:
        conditions.append(ResponseRow.model_key == model)
    if cursor:
        c_day, c_id = _decode_cursor(cursor)
        conditions.append(or_(ResponseRow.day < c_day, and_(ResponseRow.day == c_day, ResponseRow.id < c_id)))
    rows = (
        await session.execute(
            select(ResponseRow, Analysis)
            .outerjoin(
                Analysis,
                and_(Analysis.response_id == ResponseRow.id, Analysis.version == ANALYSIS_VERSION),
            )
            .where(*conditions)
            .order_by(ResponseRow.day.desc(), ResponseRow.id.desc())
            .limit(limit + 1)
        )
    ).all()
    items: list[PromptResponseOut] = []
    for resp, analysis in rows[:limit]:
        detail: dict[str, Any] = analysis.detail if analysis else {}
        items.append(
            PromptResponseOut(
                id=resp.id,
                run_id=resp.run_id,
                day=resp.day,
                model_key=resp.model_key,
                model_name=resp.model_name,
                text=resp.text,
                latency_ms=resp.latency_ms,
                cost_usd=resp.cost_usd,
                analyzed=analysis is not None,
                overall_sentiment=analysis.overall_sentiment if analysis else None,
                mentions=[ResponseMention.model_validate(m) for m in detail.get("mentions", [])],
                sources=[ResponseSource.model_validate(s) for s in detail.get("sources", [])],
            )
        )
    next_cursor = _encode_cursor(rows[limit - 1][0].day, rows[limit - 1][0].id) if len(rows) > limit else None
    return CursorPage(items=items, next_cursor=next_cursor)
