from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..db import TripRow, get_session
from ..domain.models import Trip, TripVersion, TripVersionCreate
from ..repositories import TripRepository
from ..repositories.versions import TripVersionRepository

router = APIRouter(prefix="/api/v1/trips/{trip_id}/versions", tags=["versions"])


@router.post("", response_model=TripVersion, status_code=status.HTTP_201_CREATED)
async def create_version(
    trip_id: str,
    payload: TripVersionCreate,
    session: AsyncSession = Depends(get_session),
) -> TripVersion:
    trip_repo = TripRepository(session)
    trip = await trip_repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404)
    row = await session.get(TripRow, trip_id)
    assert row is not None
    return await TripVersionRepository(session).create(
        trip,
        payload,
        row.state_json,
        row.plan_markdown,
    )


@router.get("", response_model=list[TripVersion])
async def list_versions(
    trip_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[TripVersion]:
    if not await TripRepository(session).get(trip_id):
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404)
    return await TripVersionRepository(session).list(trip_id)


@router.post("/{version_id}/restore", response_model=Trip)
async def restore_version(
    trip_id: str,
    version_id: str,
    session: AsyncSession = Depends(get_session),
) -> Trip:
    return await TripVersionRepository(session).restore(trip_id, version_id)
