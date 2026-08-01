from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def schedule_tourism_activities(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Attach executable attraction and overnight hotel activities to day plans."""
    hotels = candidates.get("hotels", [])
    attraction_sources = {
        item["place"]["name"]: item
        for item in candidates.get("attractions", [])
        if item.get("place", {}).get("name")
    }

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
        activities = list(day.get("activities", []))
        stages = sorted(day.get("stages", []), key=lambda item: item.get("sequence", 0))
        _reschedule_meals(day, activities, stages)
        existing_names = {
            item.get("place", {}).get("name")
            for item in activities
            if item.get("place")
        }
        for stage_index, stage in enumerate(stages):
            candidate = attraction_sources.get(stage.get("destination", {}).get("name"))
            if not candidate or candidate["place"]["name"] in existing_names:
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
                duration_minutes=90,
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
            existing_names.add(candidate["place"]["name"])

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
        # attractions per day.
        target_attractions = min(4, max(2, len(stages) + 1))
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
                key=lambda item: (-float(item.get("score", 0)), item.get("place", {}).get("name", "")),
            )
            for candidate in ranked_candidates:
                if len(scheduled_attractions) >= target_attractions:
                    break
                place = candidate.get("place") or {}
                name = place.get("name")
                if not name or name in existing_names:
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
                    duration_minutes=75,
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
                scheduled_attractions.append(activities[-1])
                existing_names.add(name)

        if (
            day_index < len(day_plans) - 1
            and hotels
            and not any(item.get("type") == "hotel" for item in activities)
        ):
            hotel = hotels[day_index % len(hotels)]
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
