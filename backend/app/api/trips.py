import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..db import get_session
from ..domain.models import SSEEvent, Trip, TripCreate, TripStatus, TripUpdate
from ..repositories import TripRepository

router = APIRouter(prefix="/api/v1/trips", tags=["trips"])
MOCK_TRIP_PATH = Path(__file__).resolve().parents[3] / "shared" / "examples" / "wuhan-lushan-trip.json"


def get_repo(session: AsyncSession = Depends(get_session)) -> TripRepository:
    return TripRepository(session)


@router.post("", response_model=Trip, status_code=status.HTTP_201_CREATED)
async def create_trip(payload: TripCreate, repo: TripRepository = Depends(get_repo)) -> Trip:
    return await repo.create(payload)


@router.get("", response_model=list[Trip])
async def list_trips(repo: TripRepository = Depends(get_repo)) -> list[Trip]:
    return await repo.list()


@router.get("/mock/wuhan-lushan", response_model=Trip)
async def get_wuhan_lushan_mock() -> Trip:
    return Trip.model_validate_json(MOCK_TRIP_PATH.read_text(encoding="utf-8"))


@router.get("/{trip_id}", response_model=Trip)
async def get_trip(trip_id: str, repo: TripRepository = Depends(get_repo)) -> Trip:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    return trip


@router.patch("/{trip_id}", response_model=Trip)
async def update_trip(
    trip_id: str,
    payload: TripUpdate,
    repo: TripRepository = Depends(get_repo),
) -> Trip:
    trip = await repo.update(trip_id, payload)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    return trip


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(trip_id: str, repo: TripRepository = Depends(get_repo)) -> Response:
    if not await repo.delete(trip_id):
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{trip_id}/planning/start", response_model=Trip)
async def start_mock_planning(
    trip_id: str,
    repo: TripRepository = Depends(get_repo),
) -> Trip:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    trip.status = TripStatus.planning
    return await repo.save(trip)


@router.get("/{trip_id}/planning/events")
async def planning_events(trip_id: str, repo: TripRepository = Depends(get_repo)) -> StreamingResponse:
    if not await repo.get(trip_id):
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})

    async def event_stream():
        events = [
            ("planning_started", "正在建立行程上下文", 5, "load_context", None),
            ("node_started", "正在识别出发地与目的地", 20, "extract_trip_request", None),
            ("tool_started", "正在查询武汉—庐山路线", 42, "build_base_route", "amap.driving"),
            ("tool_completed", "Mock 路线已返回", 68, "build_base_route", "amap.driving"),
            ("progress", "正在拆分天和阶段", 84, "build_stages", None),
            ("planning_completed", "路书 Mock 已生成", 100, "persist_trip", None),
        ]
        for event, label, progress, node, tool in events:
            payload = SSEEvent(
                event=event,
                trip_id=trip_id,
                node=node,
                tool=tool,
                label=label,
                progress=progress,
            )
            yield f"event: {event}\ndata: {json.dumps(payload.model_dump(mode='json'), ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
