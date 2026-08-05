from __future__ import annotations

from datetime import datetime, time, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

_INCOMFORTABLE_LODGING_RE = re.compile(
    r"(?:青旅|青年旅舍|青年旅社|旅舍|背包客栈|hostel|backpacker)",
    re.IGNORECASE,
)


def _comfortable_hotel(candidate: dict[str, Any]) -> bool:
    """Exclude hostels from the default comfortable overnight plan."""
    place = candidate.get("place") or {}
    text = " ".join(str(place.get(key) or "") for key in ("name", "address"))
    return not _INCOMFORTABLE_LODGING_RE.search(text)


def _candidate_quality(candidate: dict[str, Any]) -> tuple[float, float, str]:
    try:
        score = float(candidate.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        rating = float(candidate.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    return (-score, -rating, str((candidate.get("place") or {}).get("name") or ""))


def _attraction_priority(candidate: dict[str, Any]) -> float:
    try:
        return float(candidate.get("destination_research_priority") or 0)
    except (TypeError, ValueError):
        return 0.0


def _attraction_sort_key(candidate: dict[str, Any]) -> tuple[float, float, float, str]:
    """Keep source-backed city highlights ahead of nearby generic POIs."""
    try:
        score = float(candidate.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        rating = float(candidate.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    return (
        -_attraction_priority(candidate),
        -score,
        -rating,
        str((candidate.get("place") or {}).get("name") or ""),
    )


def _suggested_duration(candidate: dict[str, Any], default: int) -> int:
    try:
        return max(45, min(240, int(candidate.get("suggested_minutes") or default)))
    except (TypeError, ValueError):
        return default


def _attraction_name_key(value: Any) -> str:
    return re.sub(r"[\s\u00b7•\-—–_/|（）()【】\[\]，,。；;:：]+", "", str(value or "")).casefold()


def _place_distance_km(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float | None:
    if not left or not right:
        return None
    first = left.get("coordinates") or {}
    second = right.get("coordinates") or {}
    try:
        from math import asin, cos, radians, sin, sqrt

        lon1, lat1 = float(first["longitude"]), float(first["latitude"])
        lon2, lat2 = float(second["longitude"]), float(second["latitude"])
        dlon, dlat = radians(lon2 - lon1), radians(lat2 - lat1)
        value = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 6371.0088 * 2 * asin(sqrt(value))
    except (KeyError, TypeError, ValueError):
        return None


def _hotel_for_day(
    hotels: list[dict[str, Any]],
    anchor: dict[str, Any] | None,
    previous: list[tuple[dict[str, Any], dict[str, Any] | None]],
) -> dict[str, Any] | None:
    """Reuse one hotel while consecutive days stay in the same area."""
    pool = [item for item in hotels if _comfortable_hotel(item)]
    if not pool:
        return None
    for selected, _selected_anchor in reversed(previous):
        selected_place = selected.get("place") or {}
        same_city = bool(
            anchor
            and selected_place.get("city")
            and str(selected_place.get("city")).casefold() == str(anchor.get("city") or "").casefold()
        )
        close = _place_distance_km(selected_place, anchor)
        if same_city or (close is not None and close <= 20):
            return selected
    def distance_key(item: dict[str, Any]) -> tuple[float, tuple[float, float, str]]:
        distance = _place_distance_km(item.get("place"), anchor)
        return (distance if distance is not None else 9999, _candidate_quality(item))

    return min(pool, key=distance_key)


def schedule_tourism_activities(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Attach executable attraction and overnight hotel activities to day plans."""
    hotels = [item for item in candidates.get("hotels", []) if _comfortable_hotel(item)]
    attraction_sources = {
        item["place"]["name"]: item
        for item in candidates.get("attractions", [])
        if item.get("place", {}).get("name")
        and not item.get("seasonal_excluded")
    }
    seasonal_excluded_names = {
        item.get("place", {}).get("name")
        for item in candidates.get("attractions", [])
        if item.get("seasonal_excluded") and item.get("place", {}).get("name")
    }
    used_attraction_names: set[str] = set()
    used_meal_names = {
        item.get("place", {}).get("name")
        for day in day_plans
        for item in day.get("activities", [])
        if item.get("type") == "meal" and item.get("place", {}).get("name")
    }
    selected_hotels: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

    for day_index, day in enumerate(day_plans):
        # Some agent-produced day dictionaries omit the optional model id.
        # Materialize a stable id before creating activity ids so one malformed
        # day cannot abort the entire planning job.
        day.setdefault("id", f"day_{day.get('day_index', day_index + 1)}")
        # ``title`` is required by the persisted DayPlan model, but is not
        # semantically important to the scheduling pass.  Agents may omit it
        # while returning an otherwise usable day; give it a localized,
        # deterministic fallback instead of failing the whole planning job at
        # the final persistence step.
        day.setdefault("title", f"第 {day.get('day_index', day_index + 1)} 天")
        activities: list[dict[str, Any]] = []
        for activity in list(day.get("activities", [])):
            name = activity.get("place", {}).get("name")
            if activity.get("type") == "hotel" and not _comfortable_hotel(
                {"place": activity.get("place") or {}}
            ):
                # A previous snapshot may contain a hostel from before the
                # comfort policy was enabled. Remove it so this pass can pick
                # a valid replacement.
                continue
            if activity.get("type") == "attraction" and name:
                if name in seasonal_excluded_names:
                    # A re-run of the review pass can encounter a stale
                    # activity created before seasonal filtering. Remove it
                    # from the formal plan and leave the candidate visible as
                    # a backup recommendation.
                    continue
                if name in used_attraction_names:
                    # Agent candidates can repeat the destination attraction
                    # on every day. Keep the first occurrence and let the
                    # ranked pool fill later days with different places.
                    continue
                used_attraction_names.add(name)
            activities.append(activity)
        stages = sorted(day.get("stages", []), key=lambda item: item.get("sequence", 0))
        _ensure_meals(
            day,
            activities,
            stages,
            candidates.get("meals", []),
            used_names=used_meal_names,
        )
        _reschedule_meals(day, activities, stages)
        existing_hotel = next(
            (item for item in activities if item.get("type") == "hotel"),
            None,
        )
        if existing_hotel:
            selected_hotels.append(
                (
                    {"place": existing_hotel.get("place") or {}},
                    next(
                        (
                            stage.get("destination")
                            for stage in reversed(stages)
                            if stage.get("destination")
                        ),
                        None,
                    ),
                )
            )
        existing_names = {
            item.get("place", {}).get("name")
            for item in activities
            if item.get("place")
        }
        for stage_index, stage in enumerate(stages):
            candidate = attraction_sources.get(stage.get("destination", {}).get("name"))
            if (
                not candidate
                or candidate["place"]["name"] in existing_names
                or candidate["place"]["name"] in used_attraction_names
            ):
                continue
            start_at = datetime.fromisoformat(stage["planned_end"])
            next_start = (
                datetime.fromisoformat(stages[stage_index + 1]["planned_start"])
                if stage_index + 1 < len(stages)
                else None
            )
            slot_end = (
                next_start - timedelta(minutes=15)
                if next_start
                else start_at + timedelta(minutes=120)
            )
            slot = _closest_free_slot(
                start_at,
                slot_end,
                preferred=start_at,
                duration_minutes=_suggested_duration(candidate, 90),
                occupied=_occupied_ranges(activities),
                minimum_minutes=45,
            )
            if not slot:
                continue
            activity_start, duration = slot
            activities.append(
                _activity(
                    day=day,
                    sequence=len(activities),
                    activity_type="attraction",
                    place=candidate["place"],
                    start_at=activity_start,
                    duration_minutes=duration,
                    sources=candidate.get("source_records", []),
                    opening_text="开放时间以景区当天公告为准",
                    ticket_or_price=candidate.get("ticket_or_price"),
                    user_note=(
                        candidate.get("agent_reason")
                        or " · ".join(candidate.get("recommendation_reasons", []))
                        or "由 POI Agent 综合来源、距离与偏好选入"
                    ),
                    description=candidate.get("description"),
                    image_url=candidate.get("image_url"),
                    detail_url=candidate.get("detail_url"),
                )
            )
            candidate["coverage_scheduled"] = True
            existing_names.add(candidate["place"]["name"])
            used_attraction_names.add(candidate["place"]["name"])

        # Use remaining safe gaps for a second/third attraction when the day
        # has enough slack.  The old implementation only attached the POI
        # whose name exactly matched a stage destination, which left most of
        # the Agent's ranked candidates unused.
        scheduled_attractions = [
            item for item in activities if item.get("type") == "attraction"
        ]
        # A multi-day stay should not collapse into one transfer plus a
        # single attraction. Keep enough breathing room for a comfortable
        # morning/afternoon/evening plan while never exceeding four curated
        # attractions per day.  When the destination research Agent has
        # identified a city-wide must-see set, distribute the remaining
        # highlights over the remaining days instead of letting the hotel
        # area's distance score consume every slot.
        remaining_priority = [
            item
            for item in candidates.get("attractions", [])
            if _attraction_priority(item) > 0
            and item.get("place", {}).get("name") not in used_attraction_names
        ]
        remaining_days = max(1, len(day_plans) - day_index)
        priority_target = (
            (len(remaining_priority) + remaining_days - 1) // remaining_days
            if remaining_priority
            else 0
        )
        target_attractions = min(
            4,
            max(2, len(stages) + 1, priority_target),
        )
        if len(scheduled_attractions) < target_attractions:
            stage_ranges = [
                (
                    datetime.fromisoformat(stage["planned_start"]),
                    datetime.fromisoformat(stage["planned_end"]),
                )
                for stage in stages
            ]
            occupied = [*stage_ranges, *_occupied_ranges(activities)]
            ranked_candidates = sorted(
                candidates.get("attractions", []),
                key=lambda item: (
                    0
                    if item.get("coverage_day_index") == day_index + 1
                    else 1,
                    *_attraction_sort_key(item),
                ),
            )
            for candidate in ranked_candidates:
                if len(scheduled_attractions) >= target_attractions:
                    break
                place = candidate.get("place") or {}
                name = place.get("name")
                if (
                    not name
                    or candidate.get("seasonal_excluded")
                    or name in existing_names
                    or name in used_attraction_names
                ):
                    continue
                slot = _closest_free_slot(
                    datetime.combine(
                        datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date(),
                        time(9, 0),
                        tzinfo=SHANGHAI,
                    ),
                    datetime.combine(
                        datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date(),
                        time(21, 0),
                        tzinfo=SHANGHAI,
                    ),
                    preferred=datetime.combine(
                        datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date(),
                        time(15, 0),
                        tzinfo=SHANGHAI,
                    ),
                    duration_minutes=_suggested_duration(candidate, 75),
                    occupied=occupied,
                    minimum_minutes=45,
                )
                if not slot:
                    continue
                activity_start, duration = slot
                activities.append(
                    _activity(
                        day=day,
                        sequence=len(activities),
                        activity_type="attraction",
                        place=place,
                        start_at=activity_start,
                        duration_minutes=duration,
                        sources=candidate.get("source_records", []),
                        opening_text="开放时间以景区当天公告为准",
                        ticket_or_price=candidate.get("ticket_or_price"),
                        user_note=(
                            candidate.get("agent_reason")
                            or " · ".join(candidate.get("recommendation_reasons", []))
                            or "由 POI Agent 综合来源、距离与偏好选入"
                        ),
                        description=candidate.get("description"),
                        image_url=candidate.get("image_url"),
                        detail_url=candidate.get("detail_url"),
                    )
                )
                occupied.append((activity_start, activity_start + timedelta(minutes=duration)))
                candidate["coverage_scheduled"] = True
                scheduled_attractions.append(activities[-1])
                existing_names.add(name)
                used_attraction_names.add(name)

        if (
            day_index < len(day_plans) - 1
            and hotels
            and not any(item.get("type") == "hotel" for item in activities)
        ):
            hotel_anchor = next(
                (
                    stage.get("destination")
                    for stage in reversed(stages)
                    if stage.get("destination")
                ),
                None,
            )
            hotel = _hotel_for_day(hotels, hotel_anchor, selected_hotels)
            if hotel is None:
                continue
            selected_hotels.append((hotel, hotel_anchor))
            day_date = datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date()
            last_end = max(
                (
                    datetime.fromisoformat(stage["planned_end"])
                    for stage in stages
                ),
                default=datetime.combine(day_date, time(18, 0), tzinfo=SHANGHAI),
            )
            latest_activity_end = max(
                (
                    datetime.fromisoformat(activity["planned_end"])
                    for activity in activities
                    if datetime.fromisoformat(activity["planned_end"]).date()
                    == day_date
                ),
                default=last_end,
            )
            check_in = max(
                datetime.combine(day_date, time(19, 0), tzinfo=SHANGHAI),
                last_end + timedelta(minutes=30),
                latest_activity_end + timedelta(minutes=15),
            )
            check_out = datetime.combine(
                day_date + timedelta(days=1),
                time(7, 30),
                tzinfo=SHANGHAI,
            )
            if check_out <= check_in:
                check_out = check_in + timedelta(hours=8)
            activities.append(
                _activity(
                    day=day,
                    sequence=len(activities),
                    activity_type="hotel",
                    place=hotel["place"],
                    start_at=check_in,
                    duration_minutes=int((check_out - check_in).total_seconds() // 60),
                    sources=hotel.get("source_records", []),
                    opening_text="入住时间与房态以酒店实时信息为准",
                    required=True,
                    ticket_or_price=hotel.get("ticket_or_price"),
                    user_note=(
                        " · ".join(hotel.get("recommendation_reasons", []))
                        or "由住宿 Agent 综合位置、时间和来源选入"
                    ),
                    image_url=hotel.get("image_url"),
                    detail_url=hotel.get("detail_url"),
                )
            )

        activities.sort(key=lambda item: item["planned_start"])
        for sequence, activity in enumerate(activities):
            activity["sequence"] = sequence
        day["activities"] = activities
        day["items"] = [
            *[{"type": "stage", "id": stage["id"]} for stage in stages],
            *[{"type": "activity", "id": item["id"]} for item in activities],
        ]
    return day_plans


def _ensure_meals(
    day: dict[str, Any],
    activities: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    used_names: set[str | None],
) -> None:
    """Guarantee visible breakfast, lunch and dinner slots for every day.

    Meal candidates are optional enrichment.  When a provider returns no
    restaurant, keep the time block with a clearly labelled nearby-food
    placeholder instead of leaving the afternoon/evening empty or inventing a
    route to a restaurant.
    """
    definitions = [
        ("早餐", time(6, 0), time(9, 30), time(7, 30)),
        ("午餐", time(11, 0), time(15, 0), time(12, 0)),
        ("晚餐", time(17, 0), time(22, 0), time(18, 30)),
    ]
    day_date = datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date()
    existing_meals = [item for item in activities if item.get("type") == "meal"]
    occupied = [
        *[
            (
                datetime.fromisoformat(stage["planned_start"]),
                datetime.fromisoformat(stage["planned_end"]),
            )
            for stage in stages
            if stage.get("planned_start") and stage.get("planned_end")
        ],
        *_occupied_ranges([item for item in activities if item.get("type") != "meal"]),
        *_occupied_ranges(existing_meals),
    ]
    available = sorted(
        (item for item in candidates if item.get("place", {}).get("name")),
        key=_candidate_quality,
    )
    candidate_cursor = 0
    for meal_index, (label, window_start, window_end, preferred_time) in enumerate(definitions):
        if meal_index < len(existing_meals):
            used_names.add(existing_meals[meal_index].get("place", {}).get("name"))
            continue
        candidate = next(
            (
                item
                for item in available[candidate_cursor:] + available[:candidate_cursor]
                if item.get("place", {}).get("name") not in used_names
            ),
            None,
        )
        reused_candidate = False
        if candidate is None and available:
            # Small destinations may return fewer restaurants than meal
            # slots. Reuse the best sourced option instead of leaving a meal
            # blank, and expose the reuse in the activity note.
            candidate = available[candidate_cursor % len(available)]
            reused_candidate = True
        if reused_candidate and candidate:
            candidate["user_note"] = (
                f"{candidate.get('user_note') or ''}；候选池不足，已轮换复用有来源餐厅"
            )
        if candidate:
            candidate_cursor = (available.index(candidate) + 1) % len(available)
            place = dict(candidate["place"])
            used_names.add(place.get("name"))
        else:
            anchor = next(
                (
                    stage.get("destination")
                    for stage in reversed(stages)
                    if stage.get("destination")
                ),
                next((stage.get("origin") for stage in stages if stage.get("origin")), {}),
            )
            place = {
                "name": f"{anchor.get('name', '目的地')}附近{label}",
                "city": anchor.get("city"),
                "coordinates": anchor.get("coordinates"),
            }
        slot = _closest_free_slot(
            datetime.combine(day_date, window_start, tzinfo=SHANGHAI),
            datetime.combine(day_date, window_end, tzinfo=SHANGHAI),
            preferred=datetime.combine(day_date, preferred_time, tzinfo=SHANGHAI),
            duration_minutes=45,
            occupied=occupied,
            minimum_minutes=30,
        )
        if not slot:
            # A full-day transfer may leave no mathematically free slot. Do
            # not fabricate an overlapping restaurant; the verifier will
            # surface the constrained day for adjustment.
            continue
        start_at, duration = slot
        activity = _activity(
            day=day,
            sequence=len(activities),
            activity_type="meal",
            place=place,
            start_at=start_at,
            duration_minutes=duration,
            sources=candidate.get("source_records", []) if candidate else [],
            opening_text="营业时间与排队情况以当天为准",
            ticket_or_price=candidate.get("ticket_or_price") if candidate else None,
            user_note=(
                f"{label}；{candidate.get('user_note') or '出发前确认餐厅营业状态'}"
                if candidate
                else f"{label}；未返回可靠餐饮候选，可在附近灵活选择"
            ),
            description=candidate.get("description") if candidate else None,
            image_url=candidate.get("image_url") if candidate else None,
            detail_url=candidate.get("detail_url") if candidate else None,
        )
        activities.append(activity)
        occupied.append((start_at, start_at + timedelta(minutes=duration)))


def review_daily_schedule(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run a second pass over every day and phase after the first schedule.

    The first pass is intentionally conservative while route/weather data is
    still being assembled.  This review pass is idempotent and can safely add
    a missed attraction or overnight stay once all stage times are known.
    It returns non-blocking review notes for days that still have a large free
    window, so the UI can explain that the window is intentional instead of
    looking like an unfinished itinerary.
    """
    reviewed = schedule_tourism_activities(day_plans, candidates)
    notes: list[dict[str, Any]] = []
    for day in reviewed:
        stages = day.get("stages", [])
        if not stages:
            continue
        activities = day.get("activities", [])
        meals = [item for item in activities if item.get("type") == "meal"]
        if len(meals) < 3:
            notes.append(
                {
                    "code": "DAILY_MEAL_REVIEW",
                    "severity": "warning",
                    "message": f"{day.get('date')} 的三餐仍需临近出发复核",
                }
            )
        last_stage_end = max(
            datetime.fromisoformat(stage["planned_end"]) for stage in stages
        )
        later_activities = [
            item
            for item in activities
            if item.get("planned_start")
            and datetime.fromisoformat(item["planned_start"]) >= last_stage_end
            and item.get("type") in {"attraction", "meal", "hotel", "rest"}
        ]
        if last_stage_end.hour < 17 and not later_activities:
            # Keep the afternoon/evening visible in the itinerary even when
            # no additional POI can be safely placed. This is an explicit,
            # adjustable free-time block rather than an invented attraction.
            day_date = last_stage_end.date()
            rest_start = max(
                last_stage_end + timedelta(minutes=30),
                datetime.combine(day_date, time(14, 0), tzinfo=SHANGHAI),
            )
            rest_end = datetime.combine(day_date, time(17, 30), tzinfo=SHANGHAI)
            if rest_start < rest_end:
                destination = next(
                    (
                        stage.get("destination")
                        for stage in reversed(stages)
                        if stage.get("destination")
                    ),
                    {"name": "目的地周边"},
                )
                activities.append(
                    _activity(
                        day=day,
                        sequence=len(activities),
                        activity_type="rest",
                        place=destination,
                        start_at=rest_start,
                        duration_minutes=int((rest_end - rest_start).total_seconds() // 60),
                        sources=[],
                        opening_text="可按体力与天气灵活调整",
                        user_note="自由活动 / 休息时段，可按体力调整；晚餐仍按当天窗口安排",
                    )
                )
                activities.sort(key=lambda item: item["planned_start"])
                for sequence, activity in enumerate(activities):
                    activity["sequence"] = sequence
                day["activities"] = activities
                day["items"] = [
                    *[{"type": "stage", "id": stage["id"]} for stage in stages],
                    *[{"type": "activity", "id": item["id"]} for item in activities],
                ]
            notes.append(
                {
                    "code": "DAILY_FREE_WINDOW",
                    "severity": "info",
                    "message": f"{day.get('date')} 到达后保留了下午/晚间自由时间",
                }
            )
    return reviewed, notes


def _reschedule_meals(
    day: dict[str, Any],
    activities: list[dict[str, Any]],
    stages: list[dict[str, Any]],
) -> None:
    meals = sorted(
        (item for item in activities if item.get("type") == "meal"),
        key=lambda item: item["planned_start"],
    )
    if not meals:
        return
    day_date = datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date()
    definitions = [
        (time(6, 0), time(9, 30), time(7, 30)),
        (time(11, 0), time(15, 0), time(12, 0)),
        (time(17, 0), time(22, 0), time(18, 30)),
    ]
    stage_ranges = [
        (
            datetime.fromisoformat(stage["planned_start"]),
            datetime.fromisoformat(stage["planned_end"]),
        )
        for stage in stages
    ]
    fixed_activity_ranges = _occupied_ranges(
        [item for item in activities if item.get("type") != "meal"]
    )
    scheduled_meals: list[tuple[datetime, datetime]] = []
    for meal, (window_start, window_end, preferred_time) in zip(
        meals,
        definitions,
        strict=False,
    ):
        duration = max(30, int(meal.get("duration_minutes") or 45))
        slot = _closest_free_slot(
            datetime.combine(day_date, window_start, tzinfo=SHANGHAI),
            datetime.combine(day_date, window_end, tzinfo=SHANGHAI),
            preferred=datetime.combine(day_date, preferred_time, tzinfo=SHANGHAI),
            duration_minutes=duration,
            occupied=[*stage_ranges, *fixed_activity_ranges, *scheduled_meals],
            minimum_minutes=duration,
        )
        if not slot:
            continue
        start_at, actual_duration = slot
        end_at = start_at + timedelta(minutes=actual_duration)
        meal["planned_start"] = start_at.isoformat()
        meal["planned_end"] = end_at.isoformat()
        meal["duration_minutes"] = actual_duration
        scheduled_meals.append((start_at, end_at))


def _occupied_ranges(
    activities: list[dict[str, Any]],
) -> list[tuple[datetime, datetime]]:
    return [
        (
            datetime.fromisoformat(item["planned_start"]),
            datetime.fromisoformat(item["planned_end"]),
        )
        for item in activities
        if item.get("planned_start") and item.get("planned_end")
    ]


def _closest_free_slot(
    window_start: datetime,
    window_end: datetime,
    *,
    preferred: datetime,
    duration_minutes: int,
    occupied: list[tuple[datetime, datetime]],
    minimum_minutes: int,
) -> tuple[datetime, int] | None:
    gaps: list[tuple[datetime, datetime]] = []
    cursor = window_start
    for start_at, end_at in sorted(occupied):
        if end_at <= window_start or start_at >= window_end:
            continue
        clipped_start = max(start_at, window_start)
        clipped_end = min(end_at, window_end)
        if clipped_start > cursor:
            gaps.append((cursor, clipped_start))
        cursor = max(cursor, clipped_end)
    if cursor < window_end:
        gaps.append((cursor, window_end))

    options: list[tuple[float, datetime, int]] = []
    for gap_start, gap_end in gaps:
        available = int((gap_end - gap_start).total_seconds() // 60)
        actual_duration = min(duration_minutes, available)
        if actual_duration < minimum_minutes:
            continue
        latest_start = gap_end - timedelta(minutes=actual_duration)
        candidate_start = min(max(preferred, gap_start), latest_start)
        options.append(
            (
                abs((candidate_start - preferred).total_seconds()),
                candidate_start,
                actual_duration,
            )
        )
    if not options:
        return None
    _, start_at, actual_duration = min(options, key=lambda item: item[0])
    return start_at, actual_duration


def verify_tourism_plan(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    scheduled_attraction_names = {
        _attraction_name_key(activity.get("place", {}).get("name"))
        for day in day_plans
        for activity in day.get("activities", [])
        if activity.get("type") == "attraction"
    }
    uncovered_highlights = [
        item.get("place", {}).get("name")
        for item in candidates.get("attractions", [])
        if item.get("must_see")
        and item.get("place", {}).get("name")
        and _attraction_name_key(item["place"]["name"]) not in scheduled_attraction_names
        and not item.get("seasonal_excluded")
    ]
    if uncovered_highlights:
        issues.append(
            {
                "code": "DESTINATION_HIGHLIGHTS_UNCOVERED",
                "severity": "warning",
                "description": (
                    "目的地研究标记的代表性景点未全部进入当前可执行时间窗："
                    + "、".join(uncovered_highlights[:8])
                    + "；可延长天数、减少停留或在编辑面板中加入。"
                ),
            }
        )
    if candidates.get("hotels"):
        for day in day_plans[:-1]:
            if not any(
                activity.get("type") == "hotel"
                for activity in day.get("activities", [])
            ):
                issues.append(
                    {
                        "code": "OVERNIGHT_HOTEL_MISSING",
                        "severity": "blocker",
                        "description": f"{day['date']} 未安排过夜住宿",
                    }
                )
    for day in day_plans:
        ranges: list[tuple[datetime, datetime, str]] = [
            (
                datetime.fromisoformat(stage["planned_start"]),
                datetime.fromisoformat(stage["planned_end"]),
                f"移动阶段“{stage.get('title', stage.get('id', '未命名'))}”",
            )
            for stage in day.get("stages", [])
        ]
        for activity in day.get("activities", []):
            start_at = datetime.fromisoformat(activity["planned_start"])
            end_at = datetime.fromisoformat(activity["planned_end"])
            if end_at <= start_at:
                issues.append(
                    {
                        "code": "ACTIVITY_TIME_INCONSISTENT",
                        "severity": "blocker",
                        "description": (
                            f"{activity['place']['name']} 的结束时间不晚于开始时间"
                        ),
                    }
                )
            if activity.get("type") in {"meal", "attraction", "hotel"}:
                ranges.append((start_at, end_at, activity["place"]["name"]))
        for index, (start_at, end_at, name) in enumerate(sorted(ranges)):
            for other_start, _, other_name in sorted(ranges)[index + 1 :]:
                if other_start >= end_at:
                    break
                issues.append(
                    {
                        "code": "ACTIVITY_TIME_OVERLAP",
                        "severity": "blocker",
                        "description": f"{name} 与 {other_name} 的活动时间重叠",
                    }
                )
    return issues


def _activity(
    *,
    day: dict[str, Any],
    sequence: int,
    activity_type: str,
    place: dict[str, Any],
    start_at: datetime,
    duration_minutes: int,
    sources: list[dict[str, Any]],
    opening_text: str,
    required: bool = False,
    ticket_or_price: dict[str, Any] | None = None,
    user_note: str | None = None,
    description: str | None = None,
    image_url: str | None = None,
    detail_url: str | None = None,
) -> dict[str, Any]:
    end_at = start_at + timedelta(minutes=duration_minutes)
    return {
        "id": f"activity_{day['id']}_{activity_type}_{sequence}",
        "day_id": day["id"],
        "sequence": sequence,
        "type": activity_type,
        "place": place,
        "planned_start": start_at.isoformat(),
        "planned_end": end_at.isoformat(),
        "duration_minutes": duration_minutes,
        "locked": False,
        "required": required,
        "backup": False,
        "ticket_or_price": ticket_or_price,
        "opening_hours": {"text": opening_text, "confirmed": False},
        "source_records": sources,
        "user_note": user_note,
        "description": description,
        "image_url": image_url,
        "detail_url": detail_url,
        "warnings": [],
    }
