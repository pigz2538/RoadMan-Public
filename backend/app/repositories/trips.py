from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..db import FileRow, JobRow, SkillCallRow, TripRow, TripVersionRow
from ..domain.models import Trip, TripCreate, TripUpdate


class TripRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, payload: TripCreate) -> Trip:
        now = datetime.now(timezone.utc)
        trip = Trip(
            title=payload.title,
            request=payload.request,
            selected_vehicle_id=payload.selected_vehicle_id,
            origin=payload.request.origin,
            destination=payload.request.destination,
            start_date=payload.request.start_date,
            end_date=payload.request.end_date,
            created_at=now,
            updated_at=now,
        )
        self.session.add(
            TripRow(
                id=trip.id,
                title=trip.title,
                status=trip.status.value,
                document=trip.model_dump_json(),
                created_at=trip.created_at,
                updated_at=trip.updated_at,
            )
        )
        await self.session.commit()
        return trip

    async def list(self) -> list[Trip]:
        rows = (await self.session.scalars(select(TripRow).order_by(TripRow.updated_at.desc()))).all()
        return [Trip.model_validate_json(row.document) for row in rows]

    async def get(self, trip_id: str) -> Trip | None:
        row = await self.session.get(TripRow, trip_id)
        return Trip.model_validate_json(row.document) if row else None

    async def update(self, trip_id: str, payload: TripUpdate) -> Trip | None:
        row = await self.session.get(TripRow, trip_id)
        if not row:
            return None
        trip = Trip.model_validate_json(row.document)
        trip = trip.model_copy(
            update={
                **payload.model_dump(exclude_none=True),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        row.title = trip.title
        row.status = trip.status.value
        row.updated_at = trip.updated_at
        row.document = trip.model_dump_json()
        await self.session.commit()
        return trip

    async def save(self, trip: Trip) -> Trip:
        row = await self.session.get(TripRow, trip.id)
        if not row:
            raise KeyError(trip.id)
        trip.updated_at = datetime.now(timezone.utc)
        row.title = trip.title
        row.status = trip.status.value
        row.updated_at = trip.updated_at
        row.document = trip.model_dump_json()
        await self.session.commit()
        return trip

    async def load_planning_state(self, trip_id: str) -> dict[str, Any] | None:
        row = await self.session.get(TripRow, trip_id)
        if not row or not row.state_json:
            return None
        return json.loads(row.state_json)

    async def save_planning_result(
        self,
        trip: Trip,
        state: dict[str, Any],
        plan_markdown: str | None,
        messages: list[dict[str, Any]] | None = None,
    ) -> Trip:
        row = await self.session.get(TripRow, trip.id)
        if not row:
            raise KeyError(trip.id)
        trip.updated_at = datetime.now(timezone.utc)
        row.title = trip.title
        row.status = trip.status.value
        row.updated_at = trip.updated_at
        row.document = trip.model_dump_json()
        row.state_json = json.dumps(state, ensure_ascii=False, default=str)
        row.plan_markdown = plan_markdown
        if messages is not None:
            row.messages_json = json.dumps(messages, ensure_ascii=False, default=str)
        await self.session.commit()
        return trip

    async def get_planning_snapshot(self, trip_id: str) -> tuple[dict[str, Any] | None, str | None]:
        row = await self.session.get(TripRow, trip_id)
        if not row:
            return None, None
        return json.loads(row.state_json) if row.state_json else None, row.plan_markdown

    async def delete(self, trip_id: str) -> bool:
        row = await self.session.get(TripRow, trip_id)
        if not row:
            return False
        file_rows = list(
            (await self.session.scalars(select(FileRow).where(FileRow.trip_id == trip_id))).all()
        )
        for model in (TripVersionRow, JobRow, SkillCallRow, FileRow):
            await self.session.execute(delete(model).where(model.trip_id == trip_id))
        await self.session.delete(row)
        await self.session.commit()

        upload_root = Path(get_settings().upload_dir).resolve()
        for file_row in file_rows:
            candidate = Path(file_row.storage_path).resolve()
            if candidate != upload_root and upload_root in candidate.parents:
                candidate.unlink(missing_ok=True)
        return True
