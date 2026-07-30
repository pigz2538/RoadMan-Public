import asyncio
import json
import re
from datetime import date
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
    PreflightIssue,
    PreflightRequest,
    PreflightResponse,
    Trip,
    TripCreate,
    TripStatus,
    TripUpdate,
)
from ..core.config import get_settings
from ..planning.llm import OllamaRequirementExtractor
from ..repositories import JobRepository, TripRepository
from ..services.job_queue import enqueue_job
from ..services.sse import sse_manager
from ..skills.base import SkillContext
from ..skills.registry import SkillRegistry

router = APIRouter(prefix="/api/v1/trips", tags=["trips"])
MOCK_TRIP_PATH = Path(__file__).resolve().parents[3] / "shared" / "examples" / "wuhan-lushan-trip.json"


def _safe_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _explicit_travel_window_minutes(raw_text: str) -> int | None:
    match = re.search(
        r"(早上|上午|中午|下午|晚上)?\s*(\d{1,2})\s*[点时]"
        r".{0,20}?(?:到|至)"
        r"(早上|上午|中午|下午|晚上)?\s*(\d{1,2})\s*[点时]",
        raw_text,
    )
    if match:
        clock_values = [
            (match.group(1), match.group(2)),
            (match.group(3), match.group(4)),
        ]
    else:
        clock_values = re.findall(
            r"(早上|上午|中午|下午|晚上)?\s*(\d{1,2})\s*[点时]",
            raw_text,
        )
        if (
            len(clock_values) < 2
            or not any(word in raw_text for word in ("出发", "启程"))
            or not any(word in raw_text for word in ("到", "抵达", "到达"))
        ):
            return None
        clock_values = clock_values[:2]

    def hour(period: str | None, value: str) -> int:
        parsed = int(value) % 24
        if period in {"下午", "晚上"} and parsed < 12:
            parsed += 12
        if period == "中午" and parsed < 11:
            parsed += 12
        return parsed

    start_hour = hour(*clock_values[0])
    end_hour = hour(*clock_values[1])
    if end_hour < start_hour:
        end_hour += 24
    return (end_hour - start_hour) * 60


def get_repo(session: AsyncSession = Depends(get_session)) -> TripRepository:
    return TripRepository(session)


def get_skill_registry() -> SkillRegistry:
    from ..main import registry

    return registry


async def _estimate_driving_minutes(
    registry: SkillRegistry,
    origin_name: str,
    destination_name: str,
) -> int | None:
    coordinates: list[dict[str, float]] = []
    for place_name in (origin_name, destination_name):
        result = await registry.execute(
            "amap.geocode",
            {"address": place_name},
            SkillContext(metadata={"purpose": "trip_preflight"}),
        )
        if not result.success or not isinstance(result.data, dict):
            return None
        location = str(result.data.get("location") or "")
        try:
            longitude, latitude = location.split(",", 1)
            coordinates.append(
                {"longitude": float(longitude), "latitude": float(latitude)}
            )
        except (TypeError, ValueError):
            return None

    route = await registry.execute(
        "amap.route",
        {
            "origin": coordinates[0],
            "destination": coordinates[1],
            "preferred_mode": "driving",
            "allowed_fallback_modes": [],
        },
        SkillContext(metadata={"purpose": "trip_preflight"}),
    )
    if not route.success or not isinstance(route.data, dict):
        return None
    try:
        return int(route.data.get("duration_minutes") or 0) or None
    except (TypeError, ValueError):
        return None


@router.post("", response_model=Trip, status_code=status.HTTP_201_CREATED)
async def create_trip(payload: TripCreate, repo: TripRepository = Depends(get_repo)) -> Trip:
    return await repo.create(payload)


@router.get("", response_model=list[Trip])
async def list_trips(repo: TripRepository = Depends(get_repo)) -> list[Trip]:
    return await repo.list()


@router.post("/preflight", response_model=PreflightResponse)
async def preflight_trip(
    payload: PreflightRequest,
    registry: SkillRegistry = Depends(get_skill_registry),
) -> PreflightResponse:
    extracted = await OllamaRequirementExtractor(get_settings()).extract(
        payload.raw_text,
        date.today(),
    )
    issues: list[PreflightIssue] = []
    labels = {
        "origin_name": "请补充从哪里出发。",
        "destination_name": "请补充主要目的地。",
        "start_date": "请补充明确的出发日期。",
        "end_date": "请补充明确的返回日期。",
    }
    for field, message in labels.items():
        if not extracted.get(field):
            issues.append(PreflightIssue(code="MISSING_FIELD", field=field, message=message))

    start_value = _safe_date(extracted.get("start_date"))
    end_value = _safe_date(extracted.get("end_date"))
    if start_value and end_value and end_value < start_value:
        issues.append(
            PreflightIssue(
                code="INVALID_DATE_ORDER",
                field="end_date",
                severity="error",
                message="返回日期早于出发日期，请重新确认日期顺序。",
            )
        )
    if end_value and end_value < date.today():
        issues.append(
            PreflightIssue(
                code="TRIP_IN_PAST",
                field="end_date",
                severity="error",
                message="整个行程已经处于过去，请提供今天或未来的日期。",
            )
        )
    if "昨天" in payload.raw_text and any(
        keyword in payload.raw_text for keyword in ("回", "返", "到达", "抵达")
    ):
        issues.append(
            PreflightIssue(
                code="PAST_RETURN_TIME",
                field="end_date",
                severity="error",
                message="需求中出现“昨天返回/抵达”，与当前时间矛盾，请修正。",
            )
        )

    cross_sea = any(
        keyword in payload.raw_text
        for keyword in ("跨海", "海岛", "海南", "舟山", "涠洲岛", "台湾")
    )
    explicit_cross_sea_mode = any(
        keyword in payload.raw_text
        for keyword in ("轮渡", "渡轮", "坐船", "飞机", "跨海大桥")
    )
    if cross_sea and not explicit_cross_sea_mode:
        issues.append(
            PreflightIssue(
                code="CROSS_SEA_MODE_REQUIRED",
                field="preferences",
                message="行程涉及跨海，请确认采用轮渡、飞机还是明确可通车的跨海大桥。",
            )
        )

    window = _explicit_travel_window_minutes(payload.raw_text)
    different_places = extracted.get("origin_name") != extracted.get("destination_name")
    if window is not None and different_places:
        estimated_minutes = None
        if (
            window > 60
            and extracted.get("origin_name")
            and extracted.get("destination_name")
        ):
            estimated_minutes = await _estimate_driving_minutes(
                registry,
                str(extracted["origin_name"]),
                str(extracted["destination_name"]),
            )
        if window <= 60 or (estimated_minutes and window < estimated_minutes):
            estimate_text = (
                f"，高德当前估算驾车约需 {estimated_minutes} 分钟"
                if estimated_minutes
                else ""
            )
            issues.append(
                PreflightIssue(
                    code="IMPOSSIBLE_TIME_WINDOW",
                    field="time_window",
                    severity="error",
                    message=(
                        f"明确的移动时间窗口只有 {window} 分钟{estimate_text}，"
                        "无法按时完成，请放宽到达时间。"
                    ),
                )
            )

    deduped = list({(item.code, item.message): item for item in issues}.values())
    return PreflightResponse(
        ready=not deduped,
        issues=deduped,
        extracted=extracted,
    )


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
        raise AppError("ROADBOOK_NOT_READY", "行程安排尚未生成", 409)
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
    if trip_id == "trip_wuhan_lushan_demo":
        await sse_manager.seed_planning_demo(trip_id)

    async def event_stream():
        current_cursor = cursor
        terminal_events = {
            "planning_completed",
            "planning_failed",
            "planning_paused",
            "clarification_required",
        }
        while True:
            events = await sse_manager.after(trip_id, current_cursor)
            if not events:
                yield ": keep-alive\n\n"
                await asyncio.sleep(0.45)
                continue
            for stored in events:
                current_cursor = stored.id
                payload = stored.payload
                yield (
                    f"id: {stored.id}\n"
                    f"event: {payload.event}\n"
                    f"data: {json.dumps(payload.model_dump(mode='json'), ensure_ascii=False)}\n\n"
                )
                if payload.event in terminal_events:
                    return
                await asyncio.sleep(0.05)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
