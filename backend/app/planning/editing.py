from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from ..core.errors import AppError
from ..domain.models import Activity, DayItemRef, MoneyRange, PlanPatch, Trip
from ..skills.registry import SkillRegistry
from .exclusions import (
    clear_exclusion_for_candidate,
    normalize_exclusion_name,
    remember_exclusion,
)
from .tourism import activity_checks


CATEGORY_ACTIVITY_TYPE = {
    "attractions": "attraction",
    "hotels": "hotel",
    "meals": "meal",
}

ACTIVITY_CATEGORY = {
    "attraction": "attractions",
    "hotel": "hotels",
    "meal": "meals",
}


class CandidatePatchRequest(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    category: Literal["attractions", "hotels", "meals"]
    day_id: str
    operation: Literal["add", "replace"] = "add"
    target_activity_id: str | None = None
    duration_minutes: int | None = Field(default=None, ge=30, le=240)


class DeleteActivityPatchRequest(BaseModel):
    day_id: str
    activity_id: str


class EditIntentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    current_day_id: str | None = None
    current_target_id: str | None = None


class MapPointPatchRequest(BaseModel):
    day_id: str
    category: Literal["attractions", "hotels", "meals"] = "attractions"
    name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


def interpret_edit_intent(
    trip: Trip,
    state: dict[str, Any],
    request: EditIntentRequest,
    *,
    agent_intent: dict[str, Any] | None = None,
) -> tuple[str, PlanPatch | None, bool]:
    """Build a patch from a validated Agent intent.

    User language is interpreted by OllamaTripEditAgent. This layer only
    resolves IDs and exact values from the returned structure; it never
    classifies the raw message with keyword lists or name substrings.
    """
    if not agent_intent:
        raise AppError(
            "EDIT_AGENT_REQUIRED",
            "行程语义修改需要规划 Agent 返回结构化意图，请检查模型配置后重试",
            503,
        )

    intent = str(agent_intent.get("intent") or "unknown").strip().lower()
    reply = str(agent_intent.get("reply") or "").strip()
    if intent == "replan":
        return reply or "这项修改会影响日期、路线或整体节奏，需要重新生成整份行程。", None, True
    if intent == "adjust":
        return reply or "我会重新平衡当天的停留时间和用餐窗口，确认后重新规划。", None, True
    if intent == "question":
        return reply or "我可以调整景点、餐饮、住宿和交通，请告诉我希望改变哪一段。", None, False
    if intent not in {"add", "delete", "replace"}:
        return reply or "我没有识别出可执行的行程修改，请在卡片中选择目标后再描述修改。", None, False

    day = _resolve_edit_day(trip, request, agent_intent)
    if day is None:
        raise AppError(
            "EDIT_DAY_REQUIRED",
            "请先选择要修改的日期或阶段卡片，Agent 才能安全应用修改",
            422,
        )

    target = _resolve_edit_target(day, request, agent_intent)
    selected_stage = _resolve_edit_stage(day, request, agent_intent)

    if intent == "delete":
        if target is None:
            raise AppError(
                "EDIT_TARGET_REQUIRED",
                "请先在阶段卡片中选择要删除的景点、酒店或餐饮安排",
                422,
            )
        patch = create_delete_activity_patch(
            trip,
            state,
            DeleteActivityPatchRequest(day_id=day.id, activity_id=target.id),
        )
        # Keep the user-facing verb stable even when the language model uses
        # the synonymous “移除”; clients and audit logs consistently describe
        # this operation as 删除.
        normalized_reply = reply.replace("移除", "删除") if reply else ""
        return normalized_reply or f"已生成删除“{target.place.name}”的修改预览。", patch, False

    category = agent_intent.get("category")
    if category not in {"attractions", "hotels", "meals"}:
        if intent == "replace" and target is not None:
            category = {
                "attraction": "attractions",
                "hotel": "hotels",
                "meal": "meals",
            }.get(target.type)
    if category not in {"attractions", "hotels", "meals"}:
        return reply or "请让 Agent 明确这次修改是景点、住宿还是餐饮。", None, False

    candidate = _find_candidate_exact(state, category, agent_intent)
    if candidate is None and intent == "add":
        candidate = _candidate_for_add(state, category, selected_stage)
    if candidate is None:
        return reply or "没有找到与 Agent 意图完全对应的候选项，请先刷新候选推荐或在地图上选点。", None, False

    candidates = state.setdefault("tourism_candidates", {}).setdefault(category, [])
    if not any(item.get("candidate_id") == candidate.get("candidate_id") for item in candidates):
        candidates.append(candidate)

    if intent == "replace":
        if target is None:
            raise AppError(
                "EDIT_TARGET_REQUIRED",
                "请先在阶段卡片中选择要替换的现有安排",
                422,
            )
        patch = create_candidate_patch(
            trip,
            state,
            CandidatePatchRequest(
                candidate_id=str(candidate["candidate_id"]),
                category=category,
                day_id=day.id,
                operation="replace",
                target_activity_id=target.id,
                duration_minutes=_intent_duration(agent_intent),
            ),
        )
        return reply or f"已生成将“{target.place.name}”替换为“{candidate.get('place', {}).get('name', '候选项')}”的预览。", patch, False

    patch = create_candidate_patch(
        trip,
        state,
        CandidatePatchRequest(
            candidate_id=str(candidate["candidate_id"]),
            category=category,
            day_id=day.id,
            operation="add",
            duration_minutes=_intent_duration(agent_intent),
        ),
    )
    context = (
        f"“{selected_stage.origin.name} → {selected_stage.destination.name}”阶段"
        if selected_stage
        else f"第 {day.day_index} 天"
    )
    return reply or f"已生成在{context}加入“{candidate.get('place', {}).get('name', '候选项')}”的修改预览。", patch, False


def _resolve_edit_day(
    trip: Trip,
    request: EditIntentRequest,
    intent: dict[str, Any],
):
    requested_day_id = str(intent.get("day_id") or request.current_day_id or "").strip()
    if requested_day_id:
        day = next((item for item in trip.days if item.id == requested_day_id), None)
        if day is not None:
            return day

    target_ids = {
        str(value).strip()
        for value in (
            request.current_target_id,
            intent.get("target_stage_id"),
            intent.get("target_activity_id"),
        )
        if value
    }
    if target_ids:
        for day in trip.days:
            if any(stage.id in target_ids for stage in day.stages):
                return day
            if any(activity.id in target_ids for activity in day.activities):
                return day

    day_index = intent.get("day_index")
    try:
        day_index = int(day_index) if day_index is not None else None
    except (TypeError, ValueError):
        day_index = None
    if day_index is not None:
        return next((item for item in trip.days if item.day_index == day_index), None)
    return trip.days[0] if len(trip.days) == 1 else None


def _resolve_edit_target(day: Any, request: EditIntentRequest, intent: dict[str, Any]):
    target_id = str(
        intent.get("target_activity_id")
        or request.current_target_id
        or ""
    ).strip()
    if target_id:
        return next((item for item in day.activities if item.id == target_id), None)

    target_name = _normalize_label(intent.get("target_name"))
    if not target_name:
        return None
    return next(
        (
            item for item in day.activities
            if _normalize_label(item.place.name) == target_name
        ),
        None,
    )


def _resolve_edit_stage(day: Any, request: EditIntentRequest, intent: dict[str, Any]):
    stage_id = str(
        intent.get("target_stage_id")
        or request.current_target_id
        or ""
    ).strip()
    if stage_id:
        return next((item for item in day.stages if item.id == stage_id), None)
    return None


def _find_candidate_exact(
    state: dict[str, Any],
    category: str,
    intent: dict[str, Any],
) -> dict[str, Any] | None:
    candidates = state.get("tourism_candidates", {}).get(category, [])
    if not isinstance(candidates, list):
        return None
    candidate_id = str(intent.get("candidate_id") or "").strip()
    if candidate_id:
        return next(
            (
                item for item in candidates
                if isinstance(item, dict) and str(item.get("candidate_id") or "") == candidate_id
            ),
            None,
        )
    candidate_name = _normalize_label(intent.get("candidate_name"))
    if not candidate_name:
        return None
    return next(
        (
            item for item in candidates
            if isinstance(item, dict)
            and _normalize_label((item.get("place") or {}).get("name")) == candidate_name
        ),
        None,
    )


def _normalize_label(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _intent_duration(intent: dict[str, Any]) -> int | None:
    raw = intent.get("duration_minutes")
    if raw is None:
        delta = intent.get("duration_delta_minutes")
        if delta is None:
            return None
        try:
            raw = 90 + int(delta)
        except (TypeError, ValueError):
            return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(30, min(240, value))


def _candidate_for_add(
    state: dict[str, Any],
    category: str,
    selected_stage: Any | None,
) -> dict[str, Any] | None:
    if category == "meals":
        service_groups = state.get("service_pois", {})
        stage_services = service_groups.get(selected_stage.id, {}) if selected_stage else {}
        places = list(stage_services.get("meal", []))
        if not places:
            places = [
                place
                for group in service_groups.values()
                for place in group.get("meal", [])
            ]
        if places:
            place = places[0]
            source_id = place.get("source_id") or place.get("id") or place.get("name")
            return {
                "candidate_id": f"meals:route-service:{source_id}",
                "place": place,
                "provider": "沿途服务 Agent",
                "source_records": [],
                "recommendation_reasons": ["靠近所选移动阶段", "适合途中用餐和休息"],
                "score": 90,
            }
    candidates = state.get("tourism_candidates", {}).get(category, [])
    return candidates[0] if candidates else None


def create_map_point_patch(
    trip: Trip,
    state: dict[str, Any],
    request: MapPointPatchRequest,
) -> PlanPatch:
    candidate_id = f"{request.category}:map:{request.longitude:.6f},{request.latitude:.6f}"
    candidate = {
        "candidate_id": candidate_id,
        "place": {
            "name": request.name,
            "address": request.address,
            "coordinates": {
                "longitude": request.longitude,
                "latitude": request.latitude,
            },
            "source_id": candidate_id,
        },
        "provider": "高德地图选点",
        "source_records": [{
            "provider": "高德地图",
            "title": f"地图选点：{request.name}",
            "url": f"https://www.amap.com/search?query={quote(request.name)}",
        }],
        "recommendation_reasons": ["用户在地图上主动选择", "应用前需确认修改预览"],
        "score": 100,
    }
    candidates = state.setdefault("tourism_candidates", {}).setdefault(request.category, [])
    candidates[:] = [item for item in candidates if item.get("candidate_id") != candidate_id]
    candidates.insert(0, candidate)
    return create_candidate_patch(
        trip,
        state,
        CandidatePatchRequest(
            candidate_id=candidate_id,
            category=request.category,
            day_id=request.day_id,
            operation="add",
        ),
    )


def create_candidate_patch(
    trip: Trip,
    state: dict[str, Any],
    request: CandidatePatchRequest,
) -> PlanPatch:
    day = next((item for item in trip.days if item.id == request.day_id), None)
    if not day:
        raise AppError("DAY_NOT_FOUND", "未找到要修改的行程日期", 404)
    candidates = state.get("tourism_candidates", {}).get(request.category, [])
    candidate = next(
        (item for item in candidates if item.get("candidate_id") == request.candidate_id),
        None,
    )
    if not candidate:
        raise AppError("CANDIDATE_NOT_FOUND", "备选方案已失效，请刷新后重试", 404)

    if request.operation == "add" and candidate.get("seasonal_excluded"):
        raise AppError(
            "SEASONAL_CANDIDATE_UNSUITABLE",
            str(candidate.get("seasonal_warning") or "该候选与出行日期不匹配，已保留为备选"),
            409,
        )

    target = None
    if request.operation == "replace":
        if not request.target_activity_id:
            raise AppError("PATCH_TARGET_REQUIRED", "替换操作需要先选择现有安排", 422)
        target = next(
            (item for item in day.activities if item.id == request.target_activity_id),
            None,
        )
        if not target:
            raise AppError("ACTIVITY_NOT_FOUND", "未找到要替换的行程安排", 404)
        if target.locked or target.required:
            raise AppError("ACTIVITY_LOCKED", "该安排已锁定，不能直接替换", 409)

    price = _candidate_price(candidate)
    patch = PlanPatch(
        trip_id=trip.id,
        target_type="activity",
        target_id=target.id if target else "new",
        operation=request.operation,
        original_value=target.model_dump(mode="json") if target else {},
        proposed_value={
            "candidate_id": request.candidate_id,
            "category": request.category,
            "day_id": request.day_id,
            "candidate": candidate,
            "duration_minutes": request.duration_minutes,
        },
        impact_scope=[
            request.day_id,
            *[stage.id for stage in day.stages],
        ],
        time_delta_minutes=0 if target else (request.duration_minutes or _duration_for(request.category)),
        cost_delta=price,
        requires_replan=True,
    )
    state.setdefault("plan_patches", {})[patch.id] = patch.model_dump(mode="json")
    return patch


def create_delete_activity_patch(
    trip: Trip,
    state: dict[str, Any],
    request: DeleteActivityPatchRequest,
) -> PlanPatch:
    day = next((item for item in trip.days if item.id == request.day_id), None)
    if not day:
        raise AppError("DAY_NOT_FOUND", "未找到要修改的行程日期", 404)
    target = next(
        (item for item in day.activities if item.id == request.activity_id),
        None,
    )
    if not target:
        raise AppError("ACTIVITY_NOT_FOUND", "未找到要删除的行程安排", 404)
    if target.locked or target.required:
        raise AppError("ACTIVITY_LOCKED", "该安排已锁定，不能直接删除", 409)
    patch = PlanPatch(
        trip_id=trip.id,
        target_type="activity",
        target_id=target.id,
        operation="delete",
        original_value=target.model_dump(mode="json"),
        proposed_value={"day_id": request.day_id},
        impact_scope=[
            request.day_id,
            *[stage.id for stage in day.stages],
            *[
                activity.id
                for activity in day.activities
                if activity.planned_start >= target.planned_end
            ],
        ],
        time_delta_minutes=-target.duration_minutes,
        requires_replan=True,
    )
    state.setdefault("plan_patches", {})[patch.id] = patch.model_dump(mode="json")
    return patch


def decide_candidate_patch(
    trip: Trip,
    state: dict[str, Any],
    patch_id: str,
    *,
    apply: bool,
) -> tuple[PlanPatch, Trip]:
    raw_patch = state.get("plan_patches", {}).get(patch_id)
    if not raw_patch:
        raise AppError("PATCH_NOT_FOUND", "修改预览不存在或已失效", 404)
    patch = PlanPatch.model_validate(raw_patch)
    if patch.status != "preview":
        raise AppError("PATCH_ALREADY_DECIDED", "该修改预览已经处理", 409)
    if not apply:
        patch.status = "rejected"
        state["plan_patches"][patch.id] = patch.model_dump(mode="json")
        return patch, trip

    day_id = str(patch.proposed_value["day_id"])
    day = next((item for item in trip.days if item.id == day_id), None)
    if not day:
        raise AppError("DAY_NOT_FOUND", "未找到要修改的行程日期", 404)

    if patch.operation == "delete":
        target = next(
            (item for item in day.activities if item.id == patch.target_id),
            None,
        )
        if not target:
            raise AppError("ACTIVITY_NOT_FOUND", "原安排已被修改，请重新预览", 409)
        category = ACTIVITY_CATEGORY.get(target.type, target.type)
        candidate = _candidate_for_activity(state, target, category)
        # A deletion is a durable user constraint.  Provider searches may
        # return this place again, but a replan must keep it out until the
        # user explicitly adds it back.
        remember_exclusion(
            state,
            target,
            category=category,
            candidate=candidate,
        )
        day.activities = [item for item in day.activities if item.id != target.id]
        day.items = [item for item in day.items if item.id != target.id]
        _shift_following_activities(day, target.planned_end, target.duration_minutes)
    elif patch.operation == "replace":
        category = str(patch.proposed_value["category"])
        candidate = dict(patch.proposed_value["candidate"])
        index = next(
            (index for index, item in enumerate(day.activities) if item.id == patch.target_id),
            None,
        )
        if index is None:
            raise AppError("ACTIVITY_NOT_FOUND", "原安排已被修改，请重新预览", 409)
        original = day.activities[index]
        replacement = _activity_from_candidate(
            candidate,
            category,
            day.id,
            original.planned_start,
            original.planned_end,
            sequence=original.sequence,
        )
        replacement.id = original.id
        day.activities[index] = replacement
    else:
        category = str(patch.proposed_value["category"])
        candidate = dict(patch.proposed_value["candidate"])
        duration = int(patch.proposed_value.get("duration_minutes") or _duration_for(category))
        try:
            start, end = _find_available_slot(day, duration)
        except AppError as error:
            if error.code != "NO_AVAILABLE_TIME_SLOT" or not patch.requires_replan:
                raise
            # An edit is a request for the planning agent to reflow the day,
            # not a promise that the current snapshot already has room. Keep
            # the accepted item in a temporary evening slot so the user can
            # confirm the change; the queued replan will place it legally.
            tzinfo = day.activities[0].planned_start.tzinfo if day.activities else None
            start = datetime.combine(day.date, time(21, 0), tzinfo=tzinfo)
            end = start + timedelta(minutes=duration)
        activity = _activity_from_candidate(
            candidate,
            category,
            day.id,
            start,
            end,
            sequence=len(day.items),
        )
        day.activities.append(activity)
        day.items.append(DayItemRef(type="activity", id=activity.id))

    if patch.operation in {"add", "replace"}:
        # Explicitly adding/replacing a removed place restores it.  This is
        # done only after the preview is applied, so rejecting a preview keeps
        # the original exclusion intact.
        clear_exclusion_for_candidate(state, candidate, category=category)

    _normalize_day(day)
    patch.status = "applied"
    state["plan_patches"][patch.id] = patch.model_dump(mode="json")
    return patch, trip


def _candidate_for_activity(
    state: dict[str, Any],
    activity: Activity,
    category: str,
) -> dict[str, Any] | None:
    target_name = normalize_exclusion_name(activity.place.name)
    for candidate in (state.get("tourism_candidates", {}).get(category, []) or []):
        place = candidate.get("place") or {}
        if normalize_exclusion_name(place.get("name")) == target_name:
            return candidate
    return None


async def recompute_and_verify_patch(
    trip: Trip,
    state: dict[str, Any],
    patch: PlanPatch,
    registry: SkillRegistry,
) -> list[dict[str, Any]]:
    from .graph import _movement_stage, _route, _verify_route_closure
    from .tourism import verify_tourism_plan

    day = next(
        (item for item in trip.days if item.id == patch.proposed_value.get("day_id")),
        None,
    )
    if not day:
        return []
    changed_stages = []
    if patch.operation == "replace":
        old_place = dict(patch.original_value.get("place") or {})
        new_place = dict(
            patch.proposed_value.get("candidate", {}).get("place") or {}
        )
        if (
            old_place.get("name")
            and new_place.get("coordinates")
        ):
            for index, stage in enumerate(day.stages):
                origin_matches = stage.origin.name == old_place["name"]
                destination_matches = stage.destination.name == old_place["name"]
                if not origin_matches and not destination_matches:
                    continue
                origin = (
                    new_place if origin_matches else stage.origin.model_dump(mode="json")
                )
                destination = (
                    new_place
                    if destination_matches
                    else stage.destination.model_dump(mode="json")
                )
                if not origin.get("coordinates") or not destination.get("coordinates"):
                    continue
                result = await _route(
                    registry,
                    origin,
                    destination,
                    trip.id,
                    preferred_mode=stage.mode,
                    fallback_modes=["walking", "riding", "transit", "driving"],
                )
                if not result.get("success"):
                    raise AppError(
                        "PATCH_ROUTE_UNAVAILABLE",
                        "替换地点后无法形成可导航路线，修改未应用",
                        409,
                    )
                updated = _movement_stage(
                    day_id=day.id,
                    sequence=stage.sequence,
                    title=stage.title,
                    origin=origin,
                    destination=destination,
                    route=result,
                    start_at=stage.planned_start,
                )
                updated.id = stage.id
                updated.status = stage.status
                updated.weather_summary = stage.weather_summary
                updated.weather_samples = stage.weather_samples
                updated.risk_level = stage.risk_level
                updated.risk_tags = stage.risk_tags
                day.stages[index] = updated
                changed_stages.append(updated.id)
    _normalize_day(day)
    day.total_distance_km = round(sum(item.distance_km for item in day.stages), 2)
    day.total_drive_minutes = sum(
        item.duration_minutes for item in day.stages if item.mode == "driving"
    )
    day.total_walk_minutes = sum(
        item.duration_minutes for item in day.stages if item.mode == "walking"
    )
    day_dicts = [item.model_dump(mode="json") for item in trip.days]
    issues = [
        *verify_tourism_plan(day_dicts, state.get("tourism_candidates", {})),
        *_verify_route_closure(day_dicts),
    ]
    blockers = [item for item in issues if item.get("severity") == "blocker"]
    if blockers:
        # The confirmed edit is already persisted, but its stage chain still
        # needs a full planning pass.  Keep the edit visible and expose a
        # replan marker instead of rejecting a valid user action because the
        # old route is temporarily stale.
        if patch.requires_replan:
            state["route_replan_required"] = True
            state["last_applied_patch_id"] = patch.id
            state["verification_result"] = {
                "passed": False,
                "issues": blockers,
                "scope": patch.impact_scope,
                "recomputed_stage_ids": changed_stages,
            }
            return issues
        raise AppError(
            "PATCH_VERIFICATION_FAILED",
            "修改后的行程未通过校验，正式行程未保存",
            409,
            blockers,
        )
    state["verification_result"] = {
        "passed": True,
        "issues": issues,
        "scope": patch.impact_scope,
        "recomputed_stage_ids": changed_stages,
    }
    return issues


def rollback_patch(
    trip: Trip,
    state: dict[str, Any],
    patch_id: str,
) -> tuple[PlanPatch, Trip]:
    raw_patch = state.get("plan_patches", {}).get(patch_id)
    raw_backup = state.get("patch_backups", {}).get(patch_id)
    if not raw_patch or not raw_backup:
        raise AppError("PATCH_ROLLBACK_UNAVAILABLE", "该修改没有可恢复的版本", 404)
    patch = PlanPatch.model_validate(raw_patch)
    if patch.status != "applied":
        raise AppError("PATCH_ROLLBACK_UNAVAILABLE", "只有已应用的修改可以撤销", 409)
    restored = Trip.model_validate(raw_backup)
    patch.status = "rolled_back"
    state["plan_patches"][patch.id] = patch.model_dump(mode="json")
    return patch, restored


def _activity_from_candidate(
    candidate: dict[str, Any],
    category: str,
    day_id: str,
    start: datetime,
    end: datetime,
    *,
    sequence: int,
) -> Activity:
    checks = activity_checks(candidate, CATEGORY_ACTIVITY_TYPE[category], start_at=start, end_at=end)
    return Activity(
        day_id=day_id,
        sequence=sequence,
        type=CATEGORY_ACTIVITY_TYPE[category],
        place=candidate["place"],
        planned_start=start,
        planned_end=end,
        duration_minutes=max(0, int((end - start).total_seconds() // 60)),
        ticket_or_price=_candidate_price(candidate),
        opening_hours=candidate.get("opening_hours"),
        **checks,
        source_records=candidate.get("source_records", []),
        user_note="由地图选点或 Agent 备选方案加入",
        description=candidate.get("description"),
        image_url=candidate.get("image_url"),
        detail_url=candidate.get("detail_url"),
    )


def _candidate_price(candidate: dict[str, Any]) -> MoneyRange | None:
    raw = candidate.get("ticket_or_price")
    if raw:
        return MoneyRange.model_validate(raw)
    minimum = candidate.get("price_min_cny")
    maximum = candidate.get("price_max_cny")
    if minimum is None and maximum is None:
        return None
    minimum = float(minimum or maximum or 0)
    maximum = float(maximum or minimum)
    return MoneyRange(
        minimum=minimum,
        maximum=maximum,
        estimated=bool(candidate.get("price_estimated", True)),
    )


def _duration_for(category: str) -> int:
    return {"attractions": 90, "hotels": 30, "meals": 60}[category]


def _find_available_slot(day: Any, duration_minutes: int) -> tuple[datetime, datetime]:
    """Find a gap, then flex non-essential stops before rejecting an edit."""
    try:
        return _find_strict_available_slot(day, duration_minutes)
    except AppError as error:
        if error.code != "NO_AVAILABLE_TIME_SLOT":
            raise
    rebalanced = _rebalance_for_insert(day, duration_minutes)
    if rebalanced is not None:
        return rebalanced
    raise AppError(
        "NO_AVAILABLE_TIME_SLOT",
        "当天没有可调整的空闲时间；可以缩短景点停留、删除安排或选择其他日期",
        409,
    )


def _find_strict_available_slot(day: Any, duration_minutes: int) -> tuple[datetime, datetime]:
    intervals = sorted(
        [
            (item.planned_start, item.planned_end)
            for item in [*day.stages, *day.activities]
        ],
        key=lambda item: item[0],
    )
    tzinfo = intervals[0][0].tzinfo if intervals else None
    cursor = datetime.combine(day.date, time(9, 0), tzinfo=tzinfo)
    limit = datetime.combine(day.date, time(21, 0), tzinfo=tzinfo)
    duration = timedelta(minutes=duration_minutes)
    for start, end in intervals:
        if cursor + duration <= start:
            return cursor, cursor + duration
        if end > cursor:
            cursor = end
    if cursor + duration <= limit:
        return cursor, cursor + duration
    raise AppError(
        "NO_AVAILABLE_TIME_SLOT",
        "当天没有足够的空闲时间，请先移除安排或选择其他日期",
        409,
    )


def _rebalance_for_insert(day: Any, duration_minutes: int) -> tuple[datetime, datetime] | None:
    """Reflow flexible activities around fixed intervals and return a slot.

    Attractions and explicit rest blocks can move or shorten within comfort
    limits.  Stages, meals, hotels, locked and required activities stay fixed.
    """
    all_activities = list(day.activities)
    flexible = [
        item
        for item in all_activities
        if item.type in {"attraction", "rest"}
        and not item.locked
        and not item.required
    ]
    if not flexible:
        return None
    original_times = {
        item.id: (item.planned_start, item.planned_end, item.duration_minutes)
        for item in flexible
    }
    fixed = [
        (item.planned_start, item.planned_end)
        for item in day.stages
    ] + [
        (item.planned_start, item.planned_end)
        for item in all_activities
        if item not in flexible
    ]
    fixed.sort(key=lambda value: value[0])
    tzinfo = fixed[0][0].tzinfo if fixed else flexible[0].planned_start.tzinfo
    day_start = datetime.combine(day.date, time(8, 0), tzinfo=tzinfo)
    day_end = datetime.combine(day.date, time(22, 0), tzinfo=tzinfo)
    windows: list[tuple[datetime, datetime]] = []
    cursor = day_start
    for start, end in fixed:
        if end <= day_start or start >= day_end:
            continue
        start = max(start, day_start)
        end = min(end, day_end)
        if cursor < start:
            windows.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < day_end:
        windows.append((cursor, day_end))
    if not windows:
        return None

    def minimum_for(item: Any) -> int:
        return 45 if item.type == "attraction" else 30

    capacity = sum(int((end - start).total_seconds() // 60) for start, end in windows)
    durations = {
        item.id: max(minimum_for(item), int(item.duration_minutes))
        for item in flexible
    }
    requested = max(30, int(duration_minutes))
    reduction_needed = max(0, sum(durations.values()) + requested - capacity)
    for item in sorted(flexible, key=lambda value: durations[value.id], reverse=True):
        if reduction_needed <= 0:
            break
        reducible = max(0, durations[item.id] - minimum_for(item))
        reduction = min(reducible, reduction_needed)
        durations[item.id] -= reduction
        reduction_needed -= reduction
    if reduction_needed > 0:
        return None

    preferred = datetime.combine(day.date, time(15, 0), tzinfo=tzinfo)
    queue: list[tuple[datetime, Any | None]] = [
        *[(item.planned_start, item) for item in sorted(flexible, key=lambda value: value.planned_start)],
        (preferred, None),
    ]
    queue.sort(key=lambda pair: pair[0])
    queue_index = 0
    candidate_slot: tuple[datetime, datetime] | None = None
    for window_start, window_end in windows:
        cursor = window_start
        while queue_index < len(queue):
            _, item = queue[queue_index]
            duration = requested if item is None else durations[item.id]
            end = cursor + timedelta(minutes=duration)
            if end > window_end:
                break
            if item is None:
                candidate_slot = (cursor, end)
            else:
                item.planned_start = cursor
                item.planned_end = end
                item.duration_minutes = duration
            cursor = end
            queue_index += 1
        if queue_index >= len(queue):
            break
    if queue_index < len(queue) or candidate_slot is None:
        for item in flexible:
            item.planned_start, item.planned_end, item.duration_minutes = original_times[item.id]
        return None
    return candidate_slot


def _normalize_day(day: Any) -> None:
    day.stages.sort(key=lambda item: item.planned_start)
    day.activities.sort(key=lambda item: item.planned_start)
    ordered = sorted(
        [
            ("stage", item.id, item.planned_start)
            for item in day.stages
        ]
        + [
            ("activity", item.id, item.planned_start)
            for item in day.activities
        ],
        key=lambda item: item[2],
    )
    day.items = [
        DayItemRef(type=item_type, id=item_id)
        for item_type, item_id, _ in ordered
    ]
    sequence_by_id = {item.id: index for index, item in enumerate(day.items)}
    for item in [*day.stages, *day.activities]:
        item.sequence = sequence_by_id[item.id]


def _shift_following_activities(
    day: Any,
    deleted_end: datetime,
    delta_minutes: int,
) -> None:
    fixed = sorted(
        [(item.planned_start, item.planned_end) for item in day.stages],
        key=lambda item: item[0],
    )
    previous_end: datetime | None = None
    delta = timedelta(minutes=delta_minutes)
    for activity in sorted(day.activities, key=lambda item: item.planned_start):
        if activity.planned_start < deleted_end:
            previous_end = max(previous_end or activity.planned_end, activity.planned_end)
            continue
        duration = timedelta(minutes=activity.duration_minutes)
        start = activity.planned_start - delta
        if previous_end and start < previous_end:
            start = previous_end
        moved = True
        while moved:
            moved = False
            for fixed_start, fixed_end in fixed:
                if start < fixed_end and start + duration > fixed_start:
                    start = fixed_end
                    moved = True
        activity.planned_start = start
        activity.planned_end = start + duration
        previous_end = activity.planned_end
