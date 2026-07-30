from __future__ import annotations

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

            graph = build_planning_graph(
                skill_registry,
                settings,
                progress_callback=_job_aware_progress(job_id),
            )
            result = await graph.ainvoke(state)
            request = TripRequest.model_validate(result["trip_request"])
            trip.request = request
            if request.origin and request.destination and request.start_date and request.end_date:
                day_count = max(1, (request.end_date - request.start_date).days + 1)
                trip.title = f"{request.origin.name}—{request.destination.name}{day_count}天自驾路书"
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


def _job_aware_progress(job_id: str | None):
    async def callback(
        trip_id: str,
        node: str,
        label: str,
        progress: int,
        event: str,
        tool: str | None,
    ) -> None:
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
    await sse_manager.publish(
        SSEEvent(
            event="planning_failed",
            trip_id=trip_id,
            node=None,
            label="规划执行失败",
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
