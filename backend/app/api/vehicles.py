from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..db import get_session
from ..domain.models import VehicleProfile, VehicleUpdate
from ..repositories import VehicleRepository

router = APIRouter(prefix="/api/v1/vehicles", tags=["vehicles"])


def get_repo(session: AsyncSession = Depends(get_session)) -> VehicleRepository:
    return VehicleRepository(session)


@router.post("", response_model=VehicleProfile, status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    payload: VehicleProfile,
    repo: VehicleRepository = Depends(get_repo),
) -> VehicleProfile:
    return await repo.create(payload)


@router.get("", response_model=list[VehicleProfile])
async def list_vehicles(repo: VehicleRepository = Depends(get_repo)) -> list[VehicleProfile]:
    return await repo.list()


@router.get("/{vehicle_id}", response_model=VehicleProfile)
async def get_vehicle(
    vehicle_id: str,
    repo: VehicleRepository = Depends(get_repo),
) -> VehicleProfile:
    vehicle = await repo.get(vehicle_id)
    if not vehicle:
        raise AppError("VEHICLE_NOT_FOUND", "车辆不存在", 404, {"vehicle_id": vehicle_id})
    return vehicle


@router.patch("/{vehicle_id}", response_model=VehicleProfile)
async def update_vehicle(
    vehicle_id: str,
    payload: VehicleUpdate,
    repo: VehicleRepository = Depends(get_repo),
) -> VehicleProfile:
    vehicle = await repo.update(vehicle_id, payload)
    if not vehicle:
        raise AppError("VEHICLE_NOT_FOUND", "车辆不存在", 404, {"vehicle_id": vehicle_id})
    return vehicle


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vehicle(
    vehicle_id: str,
    repo: VehicleRepository = Depends(get_repo),
) -> Response:
    if not await repo.delete(vehicle_id):
        raise AppError("VEHICLE_NOT_FOUND", "车辆不存在", 404, {"vehicle_id": vehicle_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
