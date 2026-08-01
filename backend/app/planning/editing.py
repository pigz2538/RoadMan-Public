from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from ..core.errors import AppError
from ..domain.models import Activity, DayItemRef, MoneyRange, PlanPatch, Trip
from ..skills.registry import SkillRegistry


CATEGORY_ACTIVITY_TYPE = {
    "attractions": "attraction",
    "hotels": "hotel",
    "meals": "meal",
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
) -> tuple[str, PlanPatch | None, bool]:
    message = request.message.strip()
    if any(word in message for word in ("改日期", "换日期", "提前一天", "推迟一天")):
        return (
            "修改行程日期会影响全部路线、天气和住宿，需要重新生成整份行程安排。",
            None,
            True,
        )
    day = next((item for item in trip.days if item.id == request.current_day_id), None)
    if not day and request.current_target_id:
        day = next(
            (
                item for item in trip.days
                if any(stage.id == request.current_target_id for stage in item.stages)
                or any(activity.id == request.current_target_id for activity in item.activities)
            ),
            None,
        )
    if not day:
        day = _infer_day_from_message(trip, message)
    if not day:
        raise AppError("EDIT_DAY_REQUIRED", "请说明要修改第几天，或在地图、阶段卡上选择一段行程", 422)
    target = next(
        (item for item in day.activities if item.id == request.current_target_id),
        None,
    )
    if not target:
        target = next(
            (item for item in day.activities if item.place.name and item.place.name in message),
            None,
        )
    selected_stage = next(
        (item for item in day.stages if item.id == request.current_target_id),
        None,
    ) or _infer_stage_from_message(day, message)

    mentioned_candidate = _candidate_mentioned(state, message)
    add_category = mentioned_candidate[0] if mentioned_candidate else _infer_add_category(message)
    if add_category and not any(word in message for word in ("替换", "换成", "改成", "删除", "移除", "不要")):
        candidate = mentioned_candidate[1] if mentioned_candidate else _candidate_for_add(state, add_category, selected_stage)
        if not candidate:
            label = {"meals": "餐饮", "hotels": "住宿", "attractions": "景点"}[add_category]
            return f"我理解您想在第 {day.day_index} 天加入{label}，但当前候选不足，请先刷新附近推荐。", None, False
        candidates = state.setdefault("tourism_candidates", {}).setdefault(add_category, [])
        if not any(item.get("candidate_id") == candidate["candidate_id"] for item in candidates):
            candidates.append(candidate)
        patch = create_candidate_patch(
            trip,
            state,
            CandidatePatchRequest(
                candidate_id=candidate["candidate_id"],
                category=add_category,
                day_id=day.id,
                operation="add",
                duration_minutes=_requested_duration(add_category, message),
            ),
        )
        context = (
            f"“{selected_stage.origin.name} → {selected_stage.destination.name}”阶段附近"
            if selected_stage else f"第 {day.day_index} 天"
        )
        return f"已理解为在{context}加入“{candidate['place']['name']}”，请确认修改预览。", patch, False

    if any(word in message for word in ("删除", "删掉", "移除", "去掉", "不要", "取消这个", "取消安排")):
        if not target:
            raise AppError("EDIT_TARGET_REQUIRED", "请说出要删除的安排名称，或先点选该景点、酒店或餐饮", 422)
        patch = create_delete_activity_patch(
            trip,
            state,
            DeleteActivityPatchRequest(day_id=day.id, activity_id=target.id),
        )
        return f"已生成删除“{target.place.name}”的修改预览。", patch, False

    if any(word in message for word in ("替换", "换成", "改成")):
        if not target:
            raise AppError("EDIT_TARGET_REQUIRED", "请说出要替换的安排名称，或先点选该景点、酒店或餐饮", 422)
        category = {
            "attraction": "attractions",
            "hotel": "hotels",
            "meal": "meals",
        }.get(target.type)
        if not category:
            raise AppError("EDIT_TYPE_UNSUPPORTED", "当前类型暂不支持候选替换", 422)
        candidates = state.get("tourism_candidates", {}).get(category, [])
        candidate = next(
            (
                item for item in candidates
                if item.get("place", {}).get("name")
                and item["place"]["name"] in message
            ),
            None,
        )
        if not candidate:
            return (
                f"请在“{target.place.name}”所属分类的备选列表中选择替换目标。",
                None,
                False,
            )
        patch = create_candidate_patch(
            trip,
            state,
            CandidatePatchRequest(
                candidate_id=candidate["candidate_id"],
                category=category,
                day_id=day.id,
                operation="replace",
                target_activity_id=target.id,
            ),
        )
        return f"已生成替换“{target.place.name}”的修改预览。", patch, False

    return (
        "您可以直接说“在返程服务区吃午饭”“第 2 天加一家酒店”，也可以点选阶段或活动后要求删除、替换。",
        None,
        False,
    )


def _infer_day_from_message(trip: Trip, message: str):
    for day in trip.days:
        if f"第{day.day_index}天" in message.replace(" ", ""):
            return day
        names = [
            *[stage.origin.name for stage in day.stages],
            *[stage.destination.name for stage in day.stages],
            *[activity.place.name for activity in day.activities],
        ]
        if any(name and name in message for name in names):
            return day
    return trip.days[0] if len(trip.days) == 1 else None


def _infer_stage_from_message(day: Any, message: str):
    scored = []
    for stage in day.stages:
        score = sum(
            1
            for value in (stage.title, stage.origin.name, stage.destination.name)
            if value and value in message
        )
        if "返程" in message and "返程" in stage.title:
            score += 2
        if score:
            scored.append((score, stage))
    if scored:
        return max(scored, key=lambda item: item[0])[1]
    if any(word in message for word in ("返程", "服务区", "沿途", "途中")):
        return next(
            (stage for stage in reversed(day.stages) if stage.mode == "driving"),
            day.stages[-1] if day.stages else None,
        )
    return None


def _infer_add_category(message: str) -> str | None:
    if any(word in message for word in ("吃饭", "吃个饭", "餐厅", "午饭", "午餐", "晚饭", "晚餐", "早餐", "用餐", "餐饮")):
        return "meals"
    if any(word in message for word in ("住宿", "住一晚", "酒店", "宾馆", "民宿")):
        return "hotels"
    if any(word in message for word in ("景点", "增加景点", "加个景点", "安排景点", "游览", "逛一逛", "玩一会", "多逛")):
        return "attractions"
    return None


def _requested_duration(category: str, message: str) -> int | None:
    """Allow natural-language edits to trade visit depth for itinerary capacity."""
    if any(word in message for word in ("多逛", "深度", "多停留", "慢慢看")):
        return 120 if category == "attractions" else 90
    if any(word in message for word in ("顺路", "快速", "少逛", "短停")):
        return 60 if category == "attractions" else 45
    if any(word in message for word in ("再加", "多加", "多安排", "增加几个", "增加一些")):
        return 60 if category == "attractions" else 45
    return None


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


def _candidate_mentioned(
    state: dict[str, Any],
    message: str,
) -> tuple[str, dict[str, Any]] | None:
    if not any(word in message for word in ("添加", "加入", "安排", "想去", "去一下")):
        return None
    for category, candidates in state.get("tourism_candidates", {}).items():
        if category not in CATEGORY_ACTIVITY_TYPE:
            continue
        match = next(
            (
                item for item in candidates
                if item.get("place", {}).get("name")
                and item["place"]["name"] in message
            ),
            None,
        )
        if match:
            return category, match
    return None


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
        start, end = _find_available_slot(day, duration)
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

    _normalize_day(day)
    patch.status = "applied"
    state["plan_patches"][patch.id] = patch.model_dump(mode="json")
    return patch, trip


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
        source_records=candidate.get("source_records", []),
        user_note="由地图选点或 Agent 备选方案加入",
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
