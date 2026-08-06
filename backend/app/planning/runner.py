from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from ..core.config import get_settings
from ..db import JobRow, SessionLocal
from ..domain.models import (
    DayPlan,
    PlanWarning,
    SSEEvent,
    SourceRecord,
    TripRequest,
    TripStatus,
)
from ..repositories import TripRepository, VehicleRepository
from ..services.registry_factory import build_skill_registry
from ..services.sse import sse_manager
from ..skills.registry import SkillRegistry
from .graph import build_planning_graph


_progress_floor: dict[str, int] = {}


async def run_planning(
    trip_id: str,
    clarification_answer: str | None = None,
    registry: SkillRegistry | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    owns_registry = registry is None
    skill_registry = registry or build_skill_registry(settings)
    try:
        async with SessionLocal() as session:
            repo = TripRepository(session)
            trip = await repo.get(trip_id)
            if not trip:
                return {"error": {"code": "TRIP_NOT_FOUND", "trip_id": trip_id}}
            saved = await repo.load_planning_state(trip_id) or {}
            state: dict[str, Any] = {
                **saved,
                "trip_id": trip.id,
                "raw_input": trip.request.raw_text,
                "selected_vehicle_id": trip.selected_vehicle_id,
                "trip_request": saved.get(
                    "trip_request",
                    trip.request.model_dump(mode="json"),
                ),
                "clarification_round": saved.get("clarification_round", 0),
                "clarification_answers": saved.get("clarification_answers", []),
                "repair_attempted": False,
            }
            if trip.selected_vehicle_id:
                vehicle = await VehicleRepository(session).get(trip.selected_vehicle_id)
                state["vehicle_profile"] = (
                    vehicle.model_dump(mode="json") if vehicle else None
                )
            if clarification_answer:
                state = _apply_clarification(state, clarification_answer)
            trip.status = TripStatus.planning
            await repo.save(trip)
            await sse_manager.publish(
                SSEEvent(
                    event="planning_started",
                    trip_id=trip_id,
                    node="load_context",
                    label="规划任务已启动",
                    progress=1,
                )
            )
            _progress_floor[trip_id] = 1

            graph = build_planning_graph(
                skill_registry,
                settings,
                progress_callback=_job_aware_progress(job_id),
            )
            result = dict(state)
            async for update in graph.astream(state, stream_mode="updates"):
                if not isinstance(update, dict):
                    continue
                updated_nodes: list[str] = []
                for node_name, values in update.items():
                    if isinstance(values, dict):
                        result.update(values)
                        updated_nodes.append(str(node_name))
                if updated_nodes:
                    node_name = updated_nodes[-1]
                    await _persist_partial_result(repo, trip, result, node_name)
                    await _publish_progress(
                        trip_id,
                        node_name,
                        _partial_update_label(node_name, result),
                        int(result.get("progress", {}).get("value") or 1),
                        "plan_updated",
                        None,
                    )
            request = TripRequest.model_validate(result["trip_request"])
            trip.request = request
            if request.origin and request.destination and request.start_date and request.end_date:
                day_count = max(1, (request.end_date - request.start_date).days + 1)
                stage_modes = {
                    stage.get("mode")
                    for day in result.get("day_plans", [])
                    for stage in day.get("stages", [])
                }
                mode_label = (
                    "飞机" if "flight" in stage_modes
                    else "轮船" if "ferry" in stage_modes
                    else "火车" if "train" in stage_modes
                    else "自驾"
                )
                trip.title = f"{request.origin.name}—{request.destination.name}{day_count}天{mode_label}行程"
            trip.origin = request.origin
            trip.destination = request.destination
            trip.start_date = request.start_date
            trip.end_date = request.end_date
            trip.days = [DayPlan.model_validate(item) for item in result.get("day_plans", [])]
            trip.sources = [
                SourceRecord.model_validate(item)
                for item in result.get("sources", [])
            ]
            trip.warnings = [
                PlanWarning.model_validate(item)
                for item in result.get("warnings", [])
            ]
            if result.get("missing_fields"):
                trip.status = TripStatus.clarification_required
            elif result.get("verification_result", {}).get("passed"):
                trip.status = TripStatus.completed
            else:
                trip.status = TripStatus.failed
            await repo.save_planning_result(
                trip,
                _json_safe_state(result),
                result.get("plan_markdown"),
                result.get("messages", []),
            )
            if trip.status == TripStatus.completed:
                result["progress"] = {
                    "node": "persist_trip",
                    "value": 100,
                    "label": "规划完成",
                }
                await repo.save_planning_result(
                    trip,
                    _json_safe_state(result),
                    result.get("plan_markdown"),
                    result.get("messages", []),
                )
                await _publish_progress(
                    trip_id,
                    "persist_trip",
                    "规划完成",
                    100,
                    "planning_completed",
                    None,
                )
            elif trip.status == TripStatus.failed:
                await _publish_progress(
                    trip_id,
                    "persist_trip",
                    "规划校验未通过",
                    100,
                    "planning_failed",
                    None,
                )
            return {
                "trip_id": trip.id,
                "status": trip.status.value,
                "missing_fields": result.get("missing_fields", []),
                "clarification_round": result.get("clarification_round", 0),
                "clarification_question": result.get("clarification_question"),
                "defaults_applied": request.defaults_applied,
                "progress": result.get("progress", {}),
                "verification_result": result.get("verification_result"),
                "plan_markdown": result.get("plan_markdown"),
            }
    except PlanningCancelled:
        await pause_planning(trip_id)
        return {"trip_id": trip_id, "status": TripStatus.paused.value, "cancelled": True}
    except Exception as exc:
        await _mark_failed(trip_id, exc)
        raise
    finally:
        if owns_registry:
            await skill_registry.close()


async def _publish_progress(
    trip_id: str,
    node: str,
    label: str,
    progress: int,
    event: str,
    tool: str | None,
) -> None:
    # Partial persistence publishes its own snapshot event in addition to the
    # graph callback. Keep the public stream monotonic even when a repair node
    # returns the previous snapshot value after emitting a heartbeat.
    progress = max(_progress_floor.get(trip_id, 0), progress)
    _progress_floor[trip_id] = progress
    await sse_manager.publish(
        SSEEvent(
            event=event,
            trip_id=trip_id,
            node=node,
            tool=tool,
            label=label,
            progress=progress,
        )
    )
    if event in {"planning_completed", "planning_failed", "planning_paused"}:
        _progress_floor.pop(trip_id, None)


def _job_aware_progress(job_id: str | None):
    last_progress = 0

    async def callback(
        trip_id: str,
        node: str,
        label: str,
        progress: int,
        event: str,
        tool: str | None,
    ) -> None:
        nonlocal last_progress
        progress = max(last_progress, progress)
        last_progress = progress
        if job_id:
            async with SessionLocal() as session:
                row = await session.get(JobRow, job_id)
                if row and row.cancel_requested:
                    raise PlanningCancelled
                if row:
                    row.progress = progress
                    await session.commit()
        await _publish_progress(trip_id, node, label, progress, event, tool)

    return callback


def _apply_clarification(state: dict[str, Any], answer: str) -> dict[str, Any]:
    request = dict(state.get("trip_request", {}))
    missing = state.get("missing_fields", [])
    field = missing[0] if missing else None
    state["raw_input"] = f"{state['raw_input']}；用户补充：{answer}"
    if field in {"origin", "destination"}:
        request[field] = {"name": answer.strip()}
    elif field in {"start_date", "end_date"}:
        try:
            request[field] = date.fromisoformat(answer.strip()).isoformat()
        except ValueError:
            pass
    state["trip_request"] = request
    state["clarification_answers"] = [
        *state.get("clarification_answers", []),
        {"field": field, "answer": answer},
    ]
    state["messages"] = [
        *state.get("messages", []),
        {"role": "user", "type": "clarification_answer", "content": answer},
    ]
    return state


def _json_safe_state(state: dict[str, Any]) -> dict[str, Any]:
    return state


async def _mark_failed(trip_id: str, exc: Exception) -> None:
    async with SessionLocal() as session:
        repo = TripRepository(session)
        trip = await repo.get(trip_id)
        if trip:
            trip.status = TripStatus.failed
            await repo.save(trip)
    _progress_floor.pop(trip_id, None)
    await sse_manager.publish(
        SSEEvent(
            event="planning_failed",
            trip_id=trip_id,
            node=None,
            label="规划失败",
            progress=100,
        )
    )


async def pause_planning(trip_id: str) -> None:
    async with SessionLocal() as session:
        repo = TripRepository(session)
        trip = await repo.get(trip_id)
        if trip:
            trip.status = TripStatus.paused
            await repo.save(trip)
    _progress_floor.pop(trip_id, None)
    await sse_manager.publish(
        SSEEvent(
            event="planning_paused",
            trip_id=trip_id,
            node=None,
            label="规划任务已取消并暂停",
            progress=100,
        )
    )


class PlanningCancelled(Exception):
    pass


async def _persist_partial_result(
    repo: TripRepository,
    trip: Any,
    result: dict[str, Any],
    node: str,
) -> None:
    """Persist every usable graph update so the detail page can grow in real time."""
    request_data = result.get("trip_request")
    if isinstance(request_data, dict):
        try:
            request = TripRequest.model_validate(request_data)
            trip.request = request
            trip.origin = request.origin
            trip.destination = request.destination
            trip.start_date = request.start_date
            trip.end_date = request.end_date
            if request.origin and request.destination and request.start_date and request.end_date:
                day_count = max(1, (request.end_date - request.start_date).days + 1)
                stage_modes = {
                    stage.get("mode")
                    for day in result.get("day_plans", [])
                    for stage in day.get("stages", [])
                }
                mode_label = (
                    "飞机" if "flight" in stage_modes
                    else "轮船" if "ferry" in stage_modes
                    else "火车" if "train" in stage_modes
                    else "自驾"
                )
                trip.title = f"{request.origin.name}—{request.destination.name}{day_count}天{mode_label}行程"
        except (TypeError, ValueError):
            pass

    raw_days = result.get("day_plans")
    if isinstance(raw_days, list) and raw_days:
        try:
            full_days = [DayPlan.model_validate(item) for item in raw_days]
            revealed = False
            if node in {"build_stages", "enrich_deep_drive", "repair_plan"}:
                revealed = await _reveal_stages(repo, trip, result, raw_days)
            elif node in {"schedule_tourism", "review_daily_schedule"}:
                revealed = await _reveal_activities(repo, trip, result, raw_days, node)
            if not revealed:
                trip.days = full_days
        except (TypeError, ValueError):
            pass

    raw_sources = result.get("sources")
    if isinstance(raw_sources, list):
        trip.sources = _validate_items(SourceRecord, raw_sources)
    raw_warnings = result.get("warnings")
    if isinstance(raw_warnings, list):
        trip.warnings = _validate_items(PlanWarning, raw_warnings)

    trip.status = TripStatus.planning
    await repo.save_planning_result(
        trip,
        _json_safe_state(result),
        result.get("plan_markdown"),
        result.get("messages", []),
    )


async def _reveal_stages(
    repo: TripRepository,
    trip: Any,
    result: dict[str, Any],
    raw_days: list[dict[str, Any]],
) -> bool:
    current_count = sum(len(day.stages) for day in trip.days)
    target_count = sum(len(day.get("stages", [])) for day in raw_days)
    if target_count <= current_count:
        return False
    for visible_count in range(max(1, current_count + 1), target_count + 1):
        trip.days = _partial_days(raw_days, visible_count, None)
        await repo.save_planning_result(
            trip,
            _json_safe_state(result),
            result.get("plan_markdown"),
            result.get("messages", []),
        )
        await _publish_progress(
            trip.id,
            "build_stages",
            f"Agent 已加入第 {visible_count}/{target_count} 个行程阶段",
            int(result.get("progress", {}).get("value") or 1),
            "plan_updated",
            None,
        )
    return True


async def _reveal_activities(
    repo: TripRepository,
    trip: Any,
    result: dict[str, Any],
    raw_days: list[dict[str, Any]],
    node: str = "schedule_tourism",
) -> bool:
    current_count = sum(len(day.activities) for day in trip.days)
    target_count = sum(len(day.get("activities", [])) for day in raw_days)
    if target_count <= current_count:
        return False
    stage_count = sum(len(day.get("stages", [])) for day in raw_days)
    for visible_count in range(max(1, current_count + 1), target_count + 1):
        trip.days = _partial_days(raw_days, stage_count, visible_count)
        await repo.save_planning_result(
            trip,
            _json_safe_state(result),
            result.get("plan_markdown"),
            result.get("messages", []),
        )
        visible_items = [item for day in trip.days for item in day.activities]
        activity = visible_items[visible_count - 1] if len(visible_items) >= visible_count else None
        label = f"Agent 已加入：{activity.place.name}" if activity else f"Agent 已加入第 {visible_count} 项停留安排"
        await _publish_progress(
            trip.id,
            node,
            label,
            int(result.get("progress", {}).get("value") or 1),
            "plan_updated",
            None,
        )
    return True


def _partial_days(
    raw_days: list[dict[str, Any]],
    visible_stages: int,
    visible_activities: int | None,
) -> list[DayPlan]:
    stage_remaining = visible_stages
    activity_remaining = visible_activities
    partial: list[DayPlan] = []
    for raw_day in raw_days:
        day = deepcopy(raw_day)
        stages = list(day.get("stages", []))
        activities = list(day.get("activities", []))
        day["stages"] = stages[:max(0, stage_remaining)]
        stage_remaining -= len(day["stages"])
        if activity_remaining is not None:
            day["activities"] = activities[:max(0, activity_remaining)]
            activity_remaining -= len(day["activities"])
        visible_ids = {
            item.get("id")
            for item in [*day.get("stages", []), *day.get("activities", [])]
        }
        day["items"] = [item for item in day.get("items", []) if item.get("id") in visible_ids]
        partial.append(DayPlan.model_validate(day))
    return partial


def _validate_items(model: Any, values: list[Any]) -> list[Any]:
    validated: list[Any] = []
    for value in values:
        try:
            validated.append(model.model_validate(value))
        except (TypeError, ValueError):
            continue
    return validated


def _partial_update_label(node: str, result: dict[str, Any]) -> str:
    day_plans = result.get("day_plans") or []
    stage_count = sum(len(day.get("stages", [])) for day in day_plans if isinstance(day, dict))
    activity_count = sum(len(day.get("activities", [])) for day in day_plans if isinstance(day, dict))
    labels = {
        "extract_requirements": "Agent 已理解并结构化旅行需求",
        "build_base_route": "Agent 已加入跨城主路线",
        "discover_tourism": "Agent 已完成多来源景点、餐饮与住宿候选整理",
        "build_local_routes": "Agent 正在补齐景点间的本地交通",
        "build_stages": f"已加入 {stage_count} 个行程阶段",
        "discover_services": "Agent 已检查沿途休息与补能设施",
        "schedule_tourism": f"已加入 {activity_count} 项景点、用餐与住宿安排",
        "review_daily_schedule": "每日复核 Agent 已检查上午、下午、晚间与三餐住宿",
        "sample_weather": "Agent 已按计划时间补充逐段天气",
        "enrich_deep_drive": "Agent 已补充休息、补能与安全余量",
        "verify_plan": "Agent 正在逐段核验时间、闭环与驾驶安全",
        "enrich_poi_details": "POI Agent 已完成景点详情与图片补充",
        "render_markdown": "报告 Agent 正在整理最终行程安排",
        "persist_trip": "报告 Agent 正在保存并核对行程安排",
        "generate_plan": "Agent 正在整理最终行程安排",
    }
    # Never expose internal graph node names (e.g. ``render markdown``) in
    # the user-facing progress stream. Unknown future nodes get a generic
    # localized label until they are added to the map above.
    return labels.get(node, "Agent 已完成当前规划步骤")
