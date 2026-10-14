from __future__ import annotations

from fastapi import APIRouter, status

from avt.api.deps import SessionDep, SettingsDep, UserDep, WorkspaceDep
from avt.db.models import Membership, Workspace
from avt.providers.registry import default_model_keys
from avt.schemas.api import WorkspaceCreate, WorkspaceOut, WorkspaceUpdate

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate, user: UserDep, session: SessionDep, settings: SettingsDep
) -> WorkspaceOut:
    workspace = Workspace(
        name=body.name,
        models=body.models or default_model_keys(settings),
        schedule_enabled=True,
        daily_run_hour_utc=settings.default_daily_run_hour_utc,
    )
    session.add(workspace)
    await session.flush()
    session.add(Membership(user_id=user.id, workspace_id=workspace.id, role="owner"))
    await session.commit()
    return WorkspaceOut.model_validate(workspace)


@router.get("/{workspace_id}")
async def get_workspace(workspace: WorkspaceDep) -> WorkspaceOut:
    return WorkspaceOut.model_validate(workspace)


@router.patch("/{workspace_id}")
async def update_workspace(
    body: WorkspaceUpdate, workspace: WorkspaceDep, session: SessionDep
) -> WorkspaceOut:
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(workspace, field, value)
    await session.commit()
    return WorkspaceOut.model_validate(workspace)
