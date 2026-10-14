"""FastAPI dependencies: settings, DB session, queue, auth and workspace access."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import jwt
from arq.connections import ArqRedis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from avt.api.security import decode_access_token
from avt.config import Settings
from avt.db.base import Database
from avt.db.models import Membership, User, Workspace
from avt.services.runs import EnqueueFn
from avt.worker.main import make_enqueue

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


def get_enqueue(request: Request) -> EnqueueFn:
    pool: ArqRedis = request.app.state.pool
    return make_enqueue(pool)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
EnqueueDep = Annotated[EnqueueFn, Depends(get_enqueue)]


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthorized
    try:
        user_id = decode_access_token(credentials.credentials, settings.jwt_secret.get_secret_value())
    except (jwt.PyJWTError, ValueError) as exc:
        raise unauthorized from exc
    user = await session.get(User, user_id)
    if user is None:
        raise unauthorized
    return user


UserDep = Annotated[User, Depends(get_current_user)]


async def get_workspace(workspace_id: uuid.UUID, user: UserDep, session: SessionDep) -> Workspace:
    """Resolve a workspace the caller is a member of. Non-members get 404, not 403,
    so workspace ids cannot be probed."""
    workspace = (
        await session.execute(
            select(Workspace)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Workspace.id == workspace_id, Membership.user_id == user.id)
        )
    ).scalar_one_or_none()
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not found")
    return workspace


WorkspaceDep = Annotated[Workspace, Depends(get_workspace)]
