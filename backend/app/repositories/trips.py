from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import TripRow
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

    async def delete(self, trip_id: str) -> bool:
        row = await self.session.get(TripRow, trip_id)
        if not row:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True
