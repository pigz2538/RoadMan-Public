from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..db import TripRow, TripVersionRow
from ..domain.models import Trip, TripVersion, TripVersionCreate


class TripVersionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        trip: Trip,
        payload: TripVersionCreate,
        state_json: str | None,
        plan_markdown: str | None,
    ) -> TripVersion:
        version = TripVersion(
            trip_id=trip.id,
            name=payload.name,
            note=payload.note,
        )
        self.session.add(
            TripVersionRow(
                id=version.id,
                trip_id=trip.id,
                name=version.name,
                note=version.note,
                trip_document=trip.model_dump_json(),
                state_json=state_json,
                plan_markdown=plan_markdown,
                created_at=version.created_at,
            )
        )
        await self.session.commit()
        return version

    async def list(self, trip_id: str) -> list[TripVersion]:
        rows = (
            await self.session.scalars(
                select(TripVersionRow)
                .where(TripVersionRow.trip_id == trip_id)
                .order_by(TripVersionRow.created_at.desc())
            )
        ).all()
        return [
            TripVersion(
                id=row.id,
                trip_id=row.trip_id,
                name=row.name,
                note=row.note,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def restore(self, trip_id: str, version_id: str) -> Trip:
        version = await self.session.get(TripVersionRow, version_id)
        if not version or version.trip_id != trip_id:
            raise AppError("TRIP_VERSION_NOT_FOUND", "行程版本不存在", 404)
        row = await self.session.get(TripRow, trip_id)
        if not row:
            raise AppError("TRIP_NOT_FOUND", "行程不存在", 404)
        trip = Trip.model_validate_json(version.trip_document)
        row.title = trip.title
        row.status = trip.status.value
        row.document = trip.model_dump_json()
        row.state_json = version.state_json
        row.plan_markdown = version.plan_markdown
        row.updated_at = trip.updated_at
        await self.session.commit()
        return trip
