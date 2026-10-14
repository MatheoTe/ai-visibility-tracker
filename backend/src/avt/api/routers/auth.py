from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from avt.api.deps import SessionDep, SettingsDep, UserDep
from avt.api.security import create_access_token, hash_password, verify_password
from avt.db.models import Membership, User, Workspace
from avt.providers.registry import default_model_keys
from avt.schemas.api import LoginIn, MeOut, RegisterIn, TokenOut, WorkspaceOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _token(user: User, settings: SettingsDep) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(
            user.id, settings.jwt_secret.get_secret_value(), settings.jwt_ttl_minutes
        ),
        expires_in=settings.jwt_ttl_minutes * 60,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, session: SessionDep, settings: SettingsDep) -> TokenOut:
    """Create a user and their first workspace."""
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    workspace = Workspace(
        name=body.workspace_name,
        models=default_model_keys(settings),
        schedule_enabled=True,
        daily_run_hour_utc=settings.default_daily_run_hour_utc,
    )
    session.add_all([user, workspace])
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from exc
    session.add(Membership(user_id=user.id, workspace_id=workspace.id, role="owner"))
    await session.commit()
    return _token(user, settings)


@router.post("/login")
async def login(body: LoginIn, session: SessionDep, settings: SettingsDep) -> TokenOut:
    user = (
        await session.execute(select(User).where(func.lower(User.email) == body.email.lower()))
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return _token(user, settings)


@router.get("/me")
async def me(user: UserDep, session: SessionDep) -> MeOut:
    workspaces = (
        await session.execute(
            select(Workspace)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id)
            .order_by(Workspace.created_at)
        )
    ).scalars()
    return MeOut(
        id=user.id, email=user.email, workspaces=[WorkspaceOut.model_validate(w) for w in workspaces]
    )
