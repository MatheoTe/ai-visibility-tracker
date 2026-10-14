from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from avt.api.deps import SessionDep, WorkspaceDep
from avt.db.models import Brand
from avt.schemas.api import BrandIn, BrandOut, BrandUpdate, Page

router = APIRouter(prefix="/workspaces/{workspace_id}/brands", tags=["brands"])

_CONFLICT = "a brand with that name exists, or the workspace already has a primary brand"


@router.get("")
async def list_brands(
    workspace: WorkspaceDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[BrandOut]:
    where = Brand.workspace_id == workspace.id
    total = (await session.execute(select(func.count()).select_from(Brand).where(where))).scalar_one()
    rows = (
        await session.execute(
            # Primary brand first, then competitors alphabetically.
            select(Brand).where(where).order_by(Brand.role.desc(), Brand.name).limit(limit).offset(offset)
        )
    ).scalars()
    return Page(items=[BrandOut.model_validate(b) for b in rows], total=total, limit=limit, offset=offset)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_brand(body: BrandIn, workspace: WorkspaceDep, session: SessionDep) -> BrandOut:
    brand = Brand(workspace_id=workspace.id, **body.model_dump())
    session.add(brand)
    try:
        await session.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, _CONFLICT) from exc
    return BrandOut.model_validate(brand)


async def _get(session: SessionDep, workspace_id: uuid.UUID, brand_id: uuid.UUID) -> Brand:
    brand = (
        await session.execute(select(Brand).where(Brand.id == brand_id, Brand.workspace_id == workspace_id))
    ).scalar_one_or_none()
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    return brand


@router.patch("/{brand_id}")
async def update_brand(
    brand_id: uuid.UUID, body: BrandUpdate, workspace: WorkspaceDep, session: SessionDep
) -> BrandOut:
    brand = await _get(session, workspace.id, brand_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(brand, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, _CONFLICT) from exc
    return BrandOut.model_validate(brand)


@router.delete("/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand(brand_id: uuid.UUID, workspace: WorkspaceDep, session: SessionDep) -> Response:
    await _get(session, workspace.id, brand_id)
    await session.execute(delete(Brand).where(Brand.id == brand_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
