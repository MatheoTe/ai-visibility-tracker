"""Per-process cache of each workspace's compiled brand detector.

Brand lists change rarely but are needed for every one of thousands of tasks;
caching the compiled regexes for a few seconds removes a query and a compile per
task while keeping edits visible almost immediately.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from avt.analysis.detection import BrandDetector, BrandSpec
from avt.db.models import Brand
from avt.domain import BrandRole


@dataclass(frozen=True, slots=True)
class WorkspaceBrands:
    specs: tuple[BrandSpec, ...]
    detector: BrandDetector

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.specs)


class BrandCache:
    def __init__(self, ttl_seconds: float = 15.0) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[uuid.UUID, tuple[float, WorkspaceBrands]] = {}

    async def get(self, session: AsyncSession, workspace_id: uuid.UUID) -> WorkspaceBrands:
        now = time.monotonic()
        hit = self._entries.get(workspace_id)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        rows = (
            await session.execute(
                select(Brand).where(Brand.workspace_id == workspace_id).order_by(Brand.name)
            )
        ).scalars()
        specs = tuple(
            BrandSpec(id=b.id, name=b.name, aliases=tuple(b.aliases), is_primary=b.role == BrandRole.PRIMARY)
            for b in rows
        )
        entry = WorkspaceBrands(specs=specs, detector=BrandDetector(specs))
        self._entries[workspace_id] = (now, entry)
        return entry

    def invalidate(self, workspace_id: uuid.UUID) -> None:
        self._entries.pop(workspace_id, None)
