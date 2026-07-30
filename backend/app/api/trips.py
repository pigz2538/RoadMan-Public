import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..db import get_session
from ..domain.models import (
    ClarificationAnswer,
    JobCreate,
    PlanningSnapshot,
    Trip,
    TripCreate,
    TripStatus,
    TripUpdate,
)
from ..repositories import JobRepository, TripRepository
from ..services.job_queue import enqueue_job
from ..services.sse import sse_manager

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


@router.post(
    "/{trip_id}/planning/start",
    response_model=PlanningSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_planning(
    trip_id: str,
    session: AsyncSession = Depends(get_session),
) -> PlanningSnapshot:
    repo = TripRepository(session)
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    trip.status = TripStatus.planning
    await repo.save(trip)
    job = await JobRepository(session).create(
        JobCreate(kind="planning", trip_id=trip_id, payload={"trip_id": trip_id})
    )
    if not await enqueue_job(job):
        raise AppError("JOB_QUEUE_UNAVAILABLE", "规划任务队列不可用", 503)
    return PlanningSnapshot(
        trip_id=trip_id,
        status=trip.status,
        progress={"node": "queued", "value": 0},
        job_id=job.id,
    )


@router.get("/{trip_id}/planning", response_model=PlanningSnapshot)
async def get_planning_snapshot(
    trip_id: str,
    repo: TripRepository = Depends(get_repo),
) -> PlanningSnapshot:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state, markdown = await repo.get_planning_snapshot(trip_id)
    state = state or {}
    return PlanningSnapshot(
        trip_id=trip_id,
        status=trip.status,
        missing_fields=state.get("missing_fields", []),
        clarification_round=state.get("clarification_round", 0),
        clarification_question=state.get("clarification_question"),
        defaults_applied=state.get("trip_request", {}).get("defaults_applied", []),
        progress=state.get("progress", {}),
        verification_result=state.get("verification_result"),
        plan_markdown=markdown,
    )


@router.post(
    "/{trip_id}/planning/clarifications",
    response_model=PlanningSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def answer_clarification(
    trip_id: str,
    payload: ClarificationAnswer,
    session: AsyncSession = Depends(get_session),
) -> PlanningSnapshot:
    repo = TripRepository(session)
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    if trip.status != TripStatus.clarification_required:
        raise AppError("CLARIFICATION_NOT_REQUIRED", "当前行程不需要补充信息", 409)
    job = await JobRepository(session).create(
        JobCreate(
            kind="planning",
            trip_id=trip_id,
            payload={"trip_id": trip_id, "clarification_answer": payload.answer},
        )
    )
    if not await enqueue_job(job):
        raise AppError("JOB_QUEUE_UNAVAILABLE", "规划任务队列不可用", 503)
    trip.status = TripStatus.planning
    await repo.save(trip)
    return PlanningSnapshot(
        trip_id=trip_id,
        status=trip.status,
        progress={"node": "queued", "value": 0},
        job_id=job.id,
    )


@router.get("/{trip_id}/roadbook", response_class=PlainTextResponse)
async def get_roadbook(
    trip_id: str,
    repo: TripRepository = Depends(get_repo),
) -> PlainTextResponse:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    _, markdown = await repo.get_planning_snapshot(trip_id)
    if not markdown:
        raise AppError("ROADBOOK_NOT_READY", "路书尚未生成", 409)
    return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")


@router.get("/{trip_id}/risks")
async def get_trip_risks(
    trip_id: str,
    repo: TripRepository = Depends(get_repo),
) -> dict:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    stages = [
        {
            "day_id": day.id,
            "stage_id": stage.id,
            "title": stage.title,
            "risk_level": stage.risk_level,
            "risk_tags": stage.risk_tags,
            "warnings": [item.model_dump(mode="json") for item in stage.warnings],
        }
        for day in trip.days
        for stage in day.stages
        if stage.risk_level != "low" or stage.warnings
    ]
    return {
        "trip_id": trip_id,
        "summary": {
            "high": sum(item["risk_level"] == "high" for item in stages),
            "moderate": sum(item["risk_level"] == "moderate" for item in stages),
        },
        "stages": stages,
        "warnings": [item.model_dump(mode="json") for item in trip.warnings],
    }


@router.get("/{trip_id}/services")
async def get_trip_services(
    trip_id: str,
    repo: TripRepository = Depends(get_repo),
) -> dict:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state, _ = await repo.get_planning_snapshot(trip_id)
    return {
        "trip_id": trip_id,
        "services": (state or {}).get("service_pois", {}),
        "selected": [
            item.model_dump(mode="json")
            for day in trip.days
            for item in day.activities
            if item.type in {"rest", "charging", "fueling", "parking", "service", "meal"}
        ],
    }


@router.get("/{trip_id}/planning/events")
async def planning_events(
    trip_id: str,
    repo: TripRepository = Depends(get_repo),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    if not await repo.get(trip_id) and trip_id != "trip_wuhan_lushan_demo":
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    try:
        cursor = int(last_event_id or 0)
    except ValueError:
        raise AppError("INVALID_LAST_EVENT_ID", "Last-Event-ID 必须为整数", 400)
    await sse_manager.seed_planning_demo(trip_id)

    async def event_stream():
        for stored in await sse_manager.after(trip_id, cursor):
            payload = stored.payload
            yield (
                f"id: {stored.id}\n"
                f"event: {payload.event}\n"
                f"data: {json.dumps(payload.model_dump(mode='json'), ensure_ascii=False)}\n\n"
            )
            await asyncio.sleep(0.05)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
