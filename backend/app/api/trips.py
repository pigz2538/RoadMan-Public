import asyncio
import json
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..db import get_session
from ..domain.models import (
    ClarificationAnswer,
    JobCreate,
    PlanningSnapshot,
    PlanPatch,
    PreflightIssue,
    PreflightRequest,
    PreflightResponse,
    Trip,
    TripCreate,
    TripStatus,
    TripUpdate,
)
from ..core.config import get_settings
from ..planning.llm import (
    OllamaRequirementExtractor,
    OllamaRequirementValidator,
    deterministic_extract,
)
from ..planning.editing import (
    CandidatePatchRequest,
    DeleteActivityPatchRequest,
    EditIntentRequest,
    MapPointPatchRequest,
    create_candidate_patch,
    create_delete_activity_patch,
    create_map_point_patch,
    decide_candidate_patch,
    interpret_edit_intent,
    recompute_and_verify_patch,
    rollback_patch,
)
from ..planning.poi_enrichment import enrich_tourism_candidates
from ..repositories import JobRepository, TripRepository
from ..services.job_queue import enqueue_job
from ..services.exports import ReportAgent
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
    extracted = dict(payload.previous_extracted)
    if not extracted:
        settings = get_settings()
        fast_extracted = deterministic_extract(payload.raw_text, date.today())
        # Let the Requirement Agent interpret semantic details (for example
        # relationship-based party size) whenever it is configured. The
        # deterministic parser remains the offline fallback only.
        if settings.enable_llm_requirement_extraction and settings.ollama_api_key:
            extracted = await OllamaRequirementExtractor(settings).extract(
                payload.raw_text,
                date.today(),
            )
        else:
            extracted = fast_extracted
    for key, value in payload.answers.items():
        value = value.strip()
        if not value:
            continue
        _, _, field = key.partition(":")
        if field in {"origin_name", "destination_name"}:
            extracted[field] = value
        elif field in {"start_date", "end_date"}:
            parsed = _safe_date(value)
            if parsed:
                extracted[field] = parsed.isoformat()
        elif key.startswith("CROSS_SEA_MODE_REQUIRED:"):
            extracted["preferences"] = list(
                dict.fromkeys([*extracted.get("preferences", []), value])
            )

    def answered(code: str, field: str | None = None) -> bool:
        key = f"{code}:{field or ''}"
        return bool(payload.answers.get(key, "").strip())

    issues: list[PreflightIssue] = []
    labels = {
        "origin_name": "请补充从哪里出发。",
        "destination_name": "请补充主要目的地。",
        "start_date": "请补充明确的出发日期。",
        "end_date": "请补充明确的返回日期。",
    }
    for field, message in labels.items():
        if not extracted.get(field):
            issues.append(
                PreflightIssue(
                    code="MISSING_FIELD",
                    field=field,
                    message=message,
                    answer_type="date" if field in {"start_date", "end_date"} else "text",
                )
            )

    start_value = _safe_date(extracted.get("start_date"))
    end_value = _safe_date(extracted.get("end_date"))
    if start_value and end_value and end_value < start_value:
        issues.append(
            PreflightIssue(
                code="INVALID_DATE_ORDER",
                field="end_date",
                severity="error",
                message="返回日期早于出发日期，请重新确认日期顺序。",
                answer_type="date",
            )
        )
    if end_value and end_value < date.today():
        issues.append(
            PreflightIssue(
                code="TRIP_IN_PAST",
                field="end_date",
                severity="error",
                message="整个行程已经处于过去，请提供今天或未来的日期。",
                answer_type="date",
            )
        )
    if not answered("PAST_RETURN_TIME", "end_date") and "昨天" in payload.raw_text and any(
        keyword in payload.raw_text for keyword in ("回", "返", "到达", "抵达")
    ):
        issues.append(
            PreflightIssue(
                code="PAST_RETURN_TIME",
                field="end_date",
                severity="error",
                message="需求中出现“昨天返回/抵达”，与当前时间矛盾，请修正。",
                answer_type="date",
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
    if cross_sea and not explicit_cross_sea_mode and not answered(
        "CROSS_SEA_MODE_REQUIRED",
        "preferences",
    ):
        issues.append(
            PreflightIssue(
                code="CROSS_SEA_MODE_REQUIRED",
                field="preferences",
                message="行程涉及跨海，请确认采用轮渡、飞机还是明确可通车的跨海大桥。",
                answer_type="choice",
                options=["轮渡", "飞机", "跨海大桥"],
            )
        )

    window = _explicit_travel_window_minutes(payload.raw_text)
    different_places = extracted.get("origin_name") != extracted.get("destination_name")
    if (
        window is not None
        and different_places
        and not answered("IMPOSSIBLE_TIME_WINDOW", "time_window")
    ):
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
                    answer_type="time",
                )
            )

    deduped = list({(item.code, item.message): item for item in issues}.values())
    semantic_checked = payload.semantic_checked
    if not deduped and not semantic_checked:
        clarified_text = "；".join(
            [payload.raw_text, *[
                f"用户确认：{value.strip()}"
                for value in payload.answers.values()
                if value.strip()
            ]]
        )
        semantic_issues = await OllamaRequirementValidator(get_settings()).validate(
            clarified_text,
            extracted,
        )

        def already_resolved_semantic_issue(item: dict[str, object]) -> bool:
            message = str(item.get("message") or "")
            date_resolved = any(
                key.startswith((
                    "INVALID_DATE_ORDER:",
                    "TRIP_IN_PAST:",
                    "PAST_RETURN_TIME:",
                ))
                and value.strip()
                for key, value in payload.answers.items()
            )
            if date_resolved and any(
                keyword in message for keyword in ("日期", "昨天", "返回", "出发日")
            ):
                return True
            if answered("CROSS_SEA_MODE_REQUIRED", "preferences") and any(
                keyword in message for keyword in ("跨海", "轮渡", "渡轮", "飞机", "海岛")
            ):
                return True
            if answered("IMPOSSIBLE_TIME_WINDOW", "time_window") and any(
                keyword in message for keyword in ("时间", "抵达", "到达", "车程", "按时")
            ):
                return True
            return False

        deduped = [
            PreflightIssue.model_validate(item)
            for item in semantic_issues
            if not already_resolved_semantic_issue(item)
            if not answered(str(item.get("code")), str(item.get("field") or "preferences"))
        ]
        semantic_checked = True
    summary = {
        "origin_name": extracted.get("origin_name"),
        "destination_name": extracted.get("destination_name"),
        "start_date": extracted.get("start_date"),
        "end_date": extracted.get("end_date"),
        "departure_time": extracted.get("departure_time"),
        "return_time": extracted.get("return_time"),
        # Keep an unknown party size visible as unknown until the user or the
        # Requirement Agent supplies it; do not present the runtime fallback
        # of one traveler as if it came from the user's request.
        "travelers": extracted.get("travelers"),
        "preferences": extracted.get("preferences", []),
        "clarifications": [
            value.strip() for value in payload.answers.values() if value.strip()
        ],
    }
    confirmation_required = not deduped and not payload.confirmed
    return PreflightResponse(
        ready=not deduped and payload.confirmed,
        confirmation_required=confirmation_required,
        semantic_checked=semantic_checked,
        issues=deduped,
        extracted=extracted,
        summary=summary,
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


@router.get("/{trip_id}/recommendations")
async def get_trip_recommendations(
    trip_id: str,
    category: str = Query(default="attractions", pattern="^(attractions|hotels|meals)$"),
    repo: TripRepository = Depends(get_repo),
) -> dict[str, object]:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state = await repo.load_planning_state(trip_id) or {}
    candidates = state.get("tourism_candidates", {})
    return {
        "trip_id": trip_id,
        "category": category,
        "items": candidates.get(category, []),
    }


@router.post("/{trip_id}/patches/preview", response_model=PlanPatch)
async def preview_candidate_patch(
    trip_id: str,
    payload: CandidatePatchRequest,
    repo: TripRepository = Depends(get_repo),
) -> PlanPatch:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state, markdown = await repo.get_planning_snapshot(trip_id)
    state = state or {}
    patch = create_candidate_patch(trip, state, payload)
    await repo.save_planning_result(trip, state, markdown)
    return patch


@router.post("/{trip_id}/patches/preview-map-point", response_model=PlanPatch)
async def preview_map_point_patch(
    trip_id: str,
    payload: MapPointPatchRequest,
    repo: TripRepository = Depends(get_repo),
) -> PlanPatch:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state, markdown = await repo.get_planning_snapshot(trip_id)
    state = state or {}
    patch = create_map_point_patch(trip, state, payload)
    settings = get_settings()
    if settings.enable_poi_web_enrichment:
        category_candidates = state.get("tourism_candidates", {}).get(payload.category, [])
        picked = next(
            (item for item in category_candidates if item.get("candidate_id") == patch.proposed_value.get("candidate_id")),
            None,
        )
        if picked:
            await enrich_tourism_candidates(
                {payload.category: [picked]},
                max_attractions=1,
                max_other=1,
                timeout_seconds=settings.poi_web_timeout_seconds,
            )
            patch.proposed_value["candidate"] = picked
            state.setdefault("plan_patches", {})[patch.id] = patch.model_dump(mode="json")
    await repo.save_planning_result(trip, state, markdown)
    return patch


@router.post("/{trip_id}/editing/interpret")
async def interpret_trip_edit(
    trip_id: str,
    payload: EditIntentRequest,
    repo: TripRepository = Depends(get_repo),
) -> dict[str, object]:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state, markdown = await repo.get_planning_snapshot(trip_id)
    state = state or {}
    message, patch, global_replan_required = interpret_edit_intent(
        trip,
        state,
        payload,
    )
    if patch:
        await repo.save_planning_result(trip, state, markdown)
    return {
        "message": message,
        "patch": patch,
        "global_replan_required": global_replan_required,
    }


@router.post("/{trip_id}/patches/preview-delete", response_model=PlanPatch)
async def preview_delete_activity_patch(
    trip_id: str,
    payload: DeleteActivityPatchRequest,
    repo: TripRepository = Depends(get_repo),
) -> PlanPatch:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state, markdown = await repo.get_planning_snapshot(trip_id)
    state = state or {}
    patch = create_delete_activity_patch(trip, state, payload)
    await repo.save_planning_result(trip, state, markdown)
    return patch


@router.get("/{trip_id}/patches/{patch_id}", response_model=PlanPatch)
async def get_candidate_patch(
    trip_id: str,
    patch_id: str,
    repo: TripRepository = Depends(get_repo),
) -> PlanPatch:
    if not await repo.get(trip_id):
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state = await repo.load_planning_state(trip_id) or {}
    raw_patch = state.get("plan_patches", {}).get(patch_id)
    if not raw_patch:
        raise AppError("PATCH_NOT_FOUND", "修改预览不存在或已失效", 404)
    return PlanPatch.model_validate(raw_patch)


@router.post("/{trip_id}/patches/{patch_id}/apply")
async def apply_candidate_patch(
    trip_id: str,
    patch_id: str,
    repo: TripRepository = Depends(get_repo),
    registry: SkillRegistry = Depends(get_skill_registry),
) -> dict[str, object]:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state, markdown = await repo.get_planning_snapshot(trip_id)
    state = state or {}
    backup = trip.model_dump(mode="json")
    patch, trip = decide_candidate_patch(trip, state, patch_id, apply=True)
    await recompute_and_verify_patch(trip, state, patch, registry)
    state.setdefault("patch_backups", {})[patch.id] = backup
    await repo.save_planning_result(trip, state, markdown)
    # Return the canonical row after the commit. This prevents a follow-up
    # recommendation request from hydrating an older in-memory snapshot when
    # users delete one POI and immediately add another one.
    persisted = await repo.get(trip_id)
    return {"patch": patch, "trip": persisted or trip}


@router.post("/{trip_id}/patches/{patch_id}/reject", response_model=PlanPatch)
async def reject_candidate_patch(
    trip_id: str,
    patch_id: str,
    repo: TripRepository = Depends(get_repo),
) -> PlanPatch:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state, markdown = await repo.get_planning_snapshot(trip_id)
    state = state or {}
    patch, _ = decide_candidate_patch(trip, state, patch_id, apply=False)
    await repo.save_planning_result(trip, state, markdown)
    return patch


@router.post("/{trip_id}/patches/{patch_id}/rollback")
async def rollback_candidate_patch(
    trip_id: str,
    patch_id: str,
    repo: TripRepository = Depends(get_repo),
) -> dict[str, object]:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    state, markdown = await repo.get_planning_snapshot(trip_id)
    state = state or {}
    patch, restored = rollback_patch(trip, state, patch_id)
    await repo.save_planning_result(restored, state, markdown)
    return {"patch": patch, "trip": restored}


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
    if trip.status != TripStatus.completed:
        raise AppError("PLANNING_NOT_COMPLETED", "规划完成后才可导出行程安排", 409)
    _, markdown = await repo.get_planning_snapshot(trip_id)
    if not markdown:
        raise AppError("ROADBOOK_NOT_READY", "行程安排尚未生成", 409)
    return PlainTextResponse(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="roadman-{trip_id}.md"'
            )
        },
    )


async def _export_snapshot(trip_id: str, repo: TripRepository, kind: str) -> Response:
    trip = await repo.get(trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404, {"trip_id": trip_id})
    if trip.status != TripStatus.completed:
        raise AppError("PLANNING_NOT_COMPLETED", "规划完成后才可导出行程安排", 409)
    _, markdown = await repo.get_planning_snapshot(trip_id)
    if not markdown and not trip.days:
        raise AppError("ROADBOOK_NOT_READY", "行程安排尚未生成", 409)
    formats = {
        "pdf": ("application/pdf", "pdf"),
        "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
        "png": ("image/png", "png"),
        "html": ("text/html; charset=utf-8", "html"),
    }
    media_type, extension = formats[kind]
    return Response(
        ReportAgent().render(trip, markdown or "", kind),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="roadman-{trip_id}.{extension}"'},
    )


@router.get("/{trip_id}/roadbook.pdf")
async def get_roadbook_pdf(trip_id: str, repo: TripRepository = Depends(get_repo)) -> Response:
    return await _export_snapshot(trip_id, repo, "pdf")


@router.get("/{trip_id}/roadbook.pptx")
async def get_roadbook_pptx(trip_id: str, repo: TripRepository = Depends(get_repo)) -> Response:
    return await _export_snapshot(trip_id, repo, "pptx")


@router.get("/{trip_id}/roadbook.png")
async def get_roadbook_image(trip_id: str, repo: TripRepository = Depends(get_repo)) -> Response:
    return await _export_snapshot(trip_id, repo, "png")


@router.get("/{trip_id}/roadbook.html")
async def get_roadbook_html(trip_id: str, repo: TripRepository = Depends(get_repo)) -> Response:
    return await _export_snapshot(trip_id, repo, "html")


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
