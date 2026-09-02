from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timedelta
from math import ceil, isfinite
from typing import Any

from ..domain.models import Activity, DayItemRef, EnergyEstimate, PlaceRef, PlanWarning

ELEVATED_ROUTE_THRESHOLD_M = 300.0
# A long intercity drive is a calendar-spanning activity, not one oversized
# stage.  Keep a conservative daily driving budget so the planner can reserve
# a real overnight stop before the continuous-drive splitter adds shorter
# service/rest breaks.
DEFAULT_DAILY_DRIVING_MINUTES = 9 * 60
DRIVING_DAY_END_HOUR = 20
DRIVING_NEXT_DAY_START_HOUR = 8


def default_vehicle() -> dict[str, Any]:
    return {
        "id": "vehicle_estimated_default",
        "brand": "RoadMan",
        "series": "Explorer",
        "model": "纯电 SUV（估算）",
        "power_type": "electric",
        "rated_range_km": 560,
        "current_energy_percent": 80,
        "battery_kwh": 82,
        "consumption_per_100km": 18,
        "max_charge_kw": 180,
        "height_m": 1.68,
        "mountain_ready": True,
        "unpaved_ready": False,
        "safe_energy_reserve_percent": 15,
        "estimated": True,
    }


def enrich_deep_drive_plan(
    plans: list[dict[str, Any]],
    vehicle: dict[str, Any],
    service_pois: dict[str, dict[str, list[dict[str, Any]]]],
    max_continuous_drive_minutes: int,
    max_daily_drive_minutes: int = DEFAULT_DAILY_DRIVING_MINUTES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_current_percent = vehicle.get("current_energy_percent")
    parsed_current_percent = _finite_number(raw_current_percent)
    parsed_reserve = _finite_number(vehicle.get("safe_energy_reserve_percent"))
    current_percent = min(100.0, max(0.0, parsed_current_percent if parsed_current_percent is not None else 80.0))
    reserve = min(50.0, max(0.0, parsed_reserve if parsed_reserve is not None else 15.0))
    vehicle_energy_data_normalized = (
        parsed_current_percent is None
        or parsed_current_percent != current_percent
        or parsed_reserve is None
        or parsed_reserve != reserve
    )
    power_type = vehicle.get("power_type", "electric")
    warnings: list[dict[str, Any]] = []
    used_energy_stop_keys: set[str] = set()

    # Split before energy/rest enrichment.  This makes the calendar date an
    # invariant for every persisted movement stage and gives the later
    # continuous-driving splitter a normal, day-sized stage to work with.
    # Reserve part of the 08:00–20:00 daily window for charging/fuelling,
    # meals and rest. Using the full nine hours as wheel-turning time made
    # those required stops push the final piece past midnight and overlap the
    # next calendar day's stage.
    effective_daily_drive_minutes = max(
        180,
        int(max_daily_drive_minutes or DEFAULT_DAILY_DRIVING_MINUTES) - 80,
    )
    _split_cross_day_driving_stages(
        plans,
        service_pois,
        max_daily_drive_minutes=effective_daily_drive_minutes,
    )

    for day in plans:
        # Split a calendar-day highway leg into continuous-driving pieces
        # before computing SOC. A nine-hour leg can consume more than one
        # full battery even though each two-hour piece is perfectly feasible
        # with en-route charging. Computing one EnergyEstimate for the raw leg
        # both overstated a single-stage percentage and prevented multiple
        # charging boundaries from being represented.
        pre_split_stages, planned_break_activities = _split_long_driving_stages(
            day.get("stages", []),
            service_pois,
            max_continuous_drive_minutes,
            power_type,
        )
        day["stages"] = pre_split_stages
        activities: list[dict[str, Any]] = [
            *list(day.get("activities", [])),
            *planned_break_activities,
        ]
        previous_stage_end: datetime | None = None
        for stage in day.get("stages", []):
            stage_start = datetime.fromisoformat(stage["planned_start"])
            stage_end = datetime.fromisoformat(stage["planned_end"])
            if previous_stage_end is not None and stage_start < previous_stage_end:
                shift = previous_stage_end - stage_start
                stage_start += shift
                stage_end += shift
                stage["planned_start"] = stage_start.isoformat()
                stage["planned_end"] = stage_end.isoformat()
            stage_warnings = [
                PlanWarning.model_validate(item).model_dump(mode="json")
                for item in stage.get("warnings", [])
            ]
            if stage.get("mode") == "driving" and vehicle_energy_data_normalized:
                stage_warnings.append(
                    _warning(
                        "VEHICLE_ENERGY_DATA_NORMALIZED",
                        "车辆电量或安全余量字段异常，已限制到安全范围并使用保守默认值",
                        "warning",
                        True,
                    )
                )
            risk_tags: list[str] = list(stage.get("risk_tags", []))
            if stage["mode"] == "driving":
                amount, unit, used_percent = _energy_use(stage, vehicle)
                starting_percent = current_percent
                projected = max(0.0, current_percent - used_percent)
                stage["energy_estimate"] = EnergyEstimate(
                    amount=round(amount, 1),
                    unit=unit,
                    starting_percent=round(starting_percent, 1),
                    consumed_percent=round(used_percent, 1),
                    remaining_percent=round(projected, 1),
                    calculation_basis="consumption_model",
                    estimated=True,
                ).model_dump(mode="json")

                needs_energy = projected < reserve
                needs_rest = stage["duration_minutes"] > max_continuous_drive_minutes
                stop_minutes = 0
                stop_place: dict[str, Any] | None = None
                stop_type = "charging" if power_type == "electric" else "fueling"
                stage_services = service_pois.get(stage["id"], {})
                if needs_energy:
                    stop_place = next(
                        (
                            item
                            for item in (stage_services.get(stop_type) or [])
                            if _service_place_key(item) not in used_energy_stop_keys
                        ),
                        None,
                    )
                    energy_stop_estimated = False
                    if not stop_place:
                        # A provider outage or a sparse corridor must not make
                        # an otherwise schedulable cross-city trip fail after
                        # four identical repair passes.  Insert a clearly
                        # labelled route-derived candidate and keep the
                        # uncertainty visible as a warning; the traveller can
                        # confirm the actual charger/fuel station before
                        # departure.
                        stop_place = _route_derived_stop_place(stage, stop_type)
                        energy_stop_estimated = stop_place is not None
                        if stop_place:
                            stage_services.setdefault(stop_type, []).insert(0, stop_place)
                            service_pois.setdefault(stage["id"], stage_services)
                    if stop_place:
                        used_energy_stop_keys.add(_service_place_key(stop_place))
                        # A repair pass may receive the stage snapshot from a
                        # previous failed attempt.  In that case the old
                        # provider error can still be present even though a
                        # real or route-derived stop is now available.  Keep
                        # only the current resolution state; otherwise the
                        # verifier would keep emitting ENERGY_UNSAFE forever.
                        stage_warnings = [
                            item
                            for item in stage_warnings
                            if item.get("code") != "ENERGY_STOP_UNAVAILABLE"
                        ]
                        stage["waypoints"].append(stop_place)
                        replenishment = _calculate_replenishment(
                            stage,
                            vehicle,
                            stop_place,
                            starting_percent=starting_percent,
                            consumed_percent=used_percent,
                            reserve_percent=reserve,
                        )
                        stop_minutes = replenishment["replenishment_minutes"]
                        projected = replenishment["remaining_percent"]
                        stage["energy_estimate"].update(replenishment)
                        if replenishment["calculation_basis"] == "conservative_fallback":
                            stage_warnings.append(
                                _warning(
                                    "CHARGING_POWER_ESTIMATED",
                                    "充电站功率未返回，已按车辆与公共快充保守功率估算补能时长",
                                    "warning",
                                    True,
                                )
                            )
                        stage_warnings.append(
                            _warning(
                                "ENERGY_STOP_ESTIMATED"
                                if energy_stop_estimated
                                else "ENERGY_STOP_SCHEDULED",
                                (
                                    f"预计低于 {reserve:.0f}% 安全余量，沿途补能点暂未返回，"
                                    "已插入路线估算点；出发前请确认实际可用性"
                                    if energy_stop_estimated
                                    else f"预计低于 {reserve:.0f}% 安全余量，已插入"
                                    f"{'充电' if power_type == 'electric' else '加油'}点"
                                ),
                                "warning",
                                True,
                            )
                        )
                        risk_tags.append("补能待确认" if energy_stop_estimated else "补能")
                    else:
                        stage_warnings.append(
                            _warning(
                                "ENERGY_STOP_UNAVAILABLE",
                                "预计能量不足且沿途补能点查询失败，请人工确认",
                                "error",
                                True,
                            )
                        )
                        risk_tags.append("续航")
                if needs_rest:
                    rest_count = max(
                        1,
                        ceil(stage["duration_minutes"] / max_continuous_drive_minutes) - 1,
                    )
                    if stop_place:
                        stop_minutes = max(stop_minutes, 20 * rest_count)
                        note = "补能与驾驶休息合并"
                    else:
                        stop_place = _first_place(stage_services.get("rest"))
                        note = "连续驾驶达到上限，安排强制休息"
                        if stop_place:
                            stage["waypoints"].append(stop_place)
                            stop_minutes = 20 * rest_count
                    if stop_place:
                        stage_warnings.append(
                            _warning(
                                "REST_STOP_SCHEDULED",
                                f"连续驾驶超过 {max_continuous_drive_minutes} 分钟，"
                                f"已安排 {rest_count} 次休息",
                                "warning",
                                True,
                            )
                        )
                        risk_tags.append("连续驾驶")
                else:
                    note = ""

                if stop_place and (needs_energy or needs_rest):
                    activity = _activity(
                        day["id"],
                        len(activities),
                        stop_type if needs_energy else "rest",
                        stop_place,
                        _midpoint_time(stage),
                        stop_minutes,
                        note or "沿途补能",
                        required=True,
                    )
                    activities.append(activity)

                if _crosses_lunch(stage):
                    meal_place = _first_place(stage_services.get("meal"))
                    if meal_place:
                        activities.append(
                            _activity(
                                day["id"],
                                len(activities),
                                "meal",
                                meal_place,
                                datetime.fromisoformat(stage["planned_start"]).replace(
                                    hour=12,
                                    minute=0,
                                ),
                                45,
                                "午餐与途中休整",
                                required=True,
                            )
                        )
                        if all(
                            waypoint.get("name") != meal_place.get("name")
                            for waypoint in stage["waypoints"]
                        ):
                            stage["waypoints"].append(meal_place)
                        stop_minutes += 45

                # Movement time and elapsed time are intentionally distinct:
                # route_segments retain pure wheel-turning minutes for the
                # continuous-driving guard, while the stage includes planned
                # charging/rest/meal dwell so arrival times stay honest.
                stage["planned_stop_minutes"] = stop_minutes
                stage["duration_minutes"] += stop_minutes
                stage["planned_end"] = (
                    datetime.fromisoformat(stage["planned_end"])
                    + timedelta(minutes=stop_minutes)
                ).isoformat()
                current_percent = projected
                _apply_vehicle_restrictions(stage, vehicle, stage_warnings, risk_tags)

            _apply_weather_risk(stage, stage_warnings, risk_tags)
            _apply_night_risk(stage, stage_warnings, risk_tags)
            stage["warnings"] = _dedupe_warnings(stage_warnings)
            stage["risk_tags"] = list(dict.fromkeys(risk_tags))
            stage["risk_level"] = _risk_level(stage["warnings"])
            if stage["risk_level"] != "low":
                warnings.extend(stage["warnings"])
            previous_stage_end = datetime.fromisoformat(stage["planned_end"])

        expanded_stages, break_activities = _split_long_driving_stages(
            day.get("stages", []),
            service_pois,
            max_continuous_drive_minutes,
            power_type,
        )
        day["stages"] = expanded_stages
        if break_activities:
            replacement_keys = {
                (item.get("type"), item.get("place", {}).get("name"))
                for item in break_activities
            }
            activities = [
                item
                for item in activities
                if not (
                    item.get("type") in {"rest", "charging", "fueling"}
                    and (item.get("type"), item.get("place", {}).get("name"))
                    in replacement_keys
                )
            ]
        activities = _dedupe_activities([*activities, *break_activities])
        activities = _ensure_daily_meals(day, activities, service_pois)
        day["activities"] = activities
        stage_refs = [
            DayItemRef(type="stage", id=item["id"]).model_dump()
            for item in day.get("stages", [])
        ]
        activity_refs = [
            DayItemRef(type="activity", id=item["id"]).model_dump() for item in activities
        ]
        day["items"] = [*stage_refs, *activity_refs]
    # Energy/rest dwell can push a later piece into the next calendar day.
    # Re-home the item after all dwell calculations so the day column, stage
    # timestamp and map route cannot disagree (the verifier also calls this
    # helper before checking a repaired snapshot).
    normalize_plan_calendar(plans)
    return plans, _dedupe_warnings(warnings)


def normalize_plan_calendar(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep stages and activities in the day matching their timestamps.

    A repair pass may extend a driving/charging block past midnight.  Merely
    changing the timestamp leaves it attached to the previous ``DayPlan`` and
    produces a false route jump (or an invalid cross-day driving stage). Move
    items to an existing target day whenever possible; if the requested trip
    window has no such day, leave the item in place so verification can report
    the overflow honestly instead of silently truncating it.
    """
    if not plans:
        return plans
    day_by_date = {
        str(day.get("date")): day
        for day in plans
        if day.get("date")
    }
    # A stage can move more than one day after a long dwell. Repeat until no
    # in-window item is left under the wrong owner, bounded for malformed data.
    for _ in range(max(1, len(plans) + 1)):
        moved = False
        for owner in list(plans):
            owner_key = str(owner.get("date"))
            for collection_name in ("stages", "activities"):
                collection = list(owner.get(collection_name) or [])
                kept: list[dict[str, Any]] = []
                for item in collection:
                    parsed = _parse_datetime(item.get("planned_start"))
                    target_key = parsed.date().isoformat() if parsed else owner_key
                    target = day_by_date.get(target_key)
                    if target is not None and target is not owner:
                        item["day_id"] = target.get("id") or f"day_{target.get('day_index', 1)}"
                        target.setdefault(collection_name, []).append(item)
                        moved = True
                    else:
                        kept.append(item)
                owner[collection_name] = kept
        if not moved:
            break

    for day in plans:
        stages = sorted(
            day.get("stages") or [],
            key=lambda item: (str(item.get("planned_start") or ""), item.get("sequence", 0)),
        )
        activities = sorted(
            day.get("activities") or [],
            key=lambda item: (str(item.get("planned_start") or ""), item.get("sequence", 0)),
        )
        day["stages"] = stages
        day["activities"] = activities
        for sequence, item in enumerate(stages):
            item["sequence"] = sequence
        for sequence, item in enumerate(activities):
            item["sequence"] = sequence
        day["items"] = [
            *[{"type": "stage", "id": item["id"]} for item in stages],
            *[{"type": "activity", "id": item["id"]} for item in activities],
        ]
        day["total_distance_km"] = round(
            sum(float(item.get("distance_km") or 0) for item in stages),
            2,
        )
        day["total_drive_minutes"] = sum(
            int(item.get("duration_minutes") or 0)
            for item in stages
            if item.get("mode") == "driving"
        )
        day["total_walk_minutes"] = sum(
            int(item.get("duration_minutes") or 0)
            for item in stages
            if item.get("mode") == "walking"
        )
    return plans


def _split_cross_day_driving_stages(
    plans: list[dict[str, Any]],
    service_pois: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    max_daily_drive_minutes: int = DEFAULT_DAILY_DRIVING_MINUTES,
) -> None:
    """Distribute a long driving leg across calendar days with an overnight.

    Route providers return one geometry and one duration for an intercity leg.
    The old pipeline put that object into the departure day unchanged, so a
    Wuhan→Xinjiang leg could render as ``08:00–次日20:00`` on day one.  This
    pass keeps the provider geometry, cuts it at safe driving windows, and
    moves each piece to the matching day plan.  A hotel/rest activity bridges
    the gap to the next morning; if no hotel POI was returned, it is explicitly
    marked as a route-derived stop that still needs booking confirmation.
    """
    if not plans:
        return
    max_daily = max(60, int(max_daily_drive_minutes or DEFAULT_DAILY_DRIVING_MINUTES))
    day_by_date = {
        str(day.get("date")): day
        for day in plans
        if day.get("date")
    }
    original: list[tuple[dict[str, Any], dict[str, Any]]] = [
        (day, stage)
        for day in plans
        for stage in list(day.get("stages", []))
    ]
    staged_by_date: dict[str, list[dict[str, Any]]] = {
        key: [] for key in day_by_date
    }
    activities_by_date: dict[str, list[dict[str, Any]]] = {
        key: [] for key in day_by_date
    }

    for owner_day, stage in original:
        start = _parse_datetime(stage.get("planned_start"))
        end = _parse_datetime(stage.get("planned_end"))
        movement_duration = int(
            (stage.get("route_segments") or [{}])[0].get(
                "duration_minutes",
                stage.get("duration_minutes", 0),
            )
            or 0
        )
        if (
            stage.get("mode") != "driving"
            or start is None
            or end is None
            or movement_duration <= 0
            or (end.date() == start.date() and movement_duration <= max_daily)
        ):
            # The repair loop can call this pass again after a previous split.
            # In that case a stage may still sit in its original list while
            # its timestamp already belongs to another calendar day. Re-home
            # it by the timestamp whenever that day exists; otherwise keep the
            # original owner so the verifier can report an honest overflow.
            owner_key = str(owner_day.get("date"))
            stage_date_key = start.date().isoformat()
            target_key = stage_date_key if stage_date_key in day_by_date else owner_key
            target_day = day_by_date.get(target_key)
            if target_day:
                stage["day_id"] = target_day.get("id") or f"day_{target_day.get('day_index', 1)}"
            staged_by_date.setdefault(target_key, []).append(stage)
            continue

        pieces, overnight_activities, piece_services = _split_driving_stage(
            stage,
            service_pois.get(stage.get("id"), {}),
            max_daily_minutes=max_daily,
        )
        if not pieces:
            owner_key = str(owner_day.get("date"))
            staged_by_date.setdefault(owner_key, []).append(stage)
            continue
        for piece in pieces:
            piece_key = str(piece["planned_start"][:10])
            target_day = day_by_date.get(piece_key) or owner_day
            target_key = str(target_day.get("date"))
            piece["day_id"] = target_day.get("id") or f"day_{target_day.get('day_index', 1)}"
            staged_by_date.setdefault(target_key, []).append(piece)
            if piece.get("_service_pois") is not None:
                piece_services[str(piece["id"])] = piece.pop("_service_pois")
        for activity in overnight_activities:
            activity_key = str(activity["planned_start"][:10])
            target_day = day_by_date.get(activity_key) or owner_day
            target_key = str(target_day.get("date"))
            activity["day_id"] = target_day.get("id") or f"day_{target_day.get('day_index', 1)}"
            activities_by_date.setdefault(target_key, []).append(activity)
        # The later 120-minute splitter works on the newly created piece IDs.
        # Reuse the original corridor service search for every piece instead
        # of silently losing charging/rest candidates after the calendar cut.
        service_pois.update(piece_services)

    # Preserve existing activities and normalize each day's ordering.  The
    # overlap repair later in the graph can move optional local activities
    # behind an arrival, while the generated hotel block remains required.
    for day in plans:
        key = str(day.get("date"))
        day["stages"] = sorted(
            staged_by_date.get(key, []),
            key=lambda item: (str(item.get("planned_start", "")), item.get("sequence", 0)),
        )
        day["activities"] = [
            *list(day.get("activities", [])),
            *activities_by_date.get(key, []),
        ]
        for sequence, stage in enumerate(day["stages"]):
            stage["sequence"] = sequence
        for sequence, activity in enumerate(
            sorted(day["activities"], key=lambda item: str(item.get("planned_start", "")))
        ):
            activity["sequence"] = sequence
        day["items"] = [
            *[{"type": "stage", "id": item["id"]} for item in day["stages"]],
            *[{"type": "activity", "id": item["id"]} for item in day["activities"]],
        ]


def _split_driving_stage(
    stage: dict[str, Any],
    stage_services: dict[str, list[dict[str, Any]]],
    *,
    max_daily_minutes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    start = _parse_datetime(stage.get("planned_start"))
    if start is None:
        return [], [], {}
    source_segment = _first_route_segment(stage)
    segment = deepcopy(source_segment)
    geometry = _stage_geometry(stage)
    if len(geometry) < 2:
        return [], [], {}

    total_duration = int(segment.get("duration_minutes") or stage.get("duration_minutes") or 0)
    total_distance = float(segment.get("distance_km") or stage.get("distance_km") or 0)
    if total_duration <= 0:
        return [], [], {}
    remaining = total_duration
    elapsed = 0
    cursor = start
    driven_today = 0
    pieces: list[dict[str, Any]] = []
    overnight_activities: list[dict[str, Any]] = []
    piece_services: dict[str, list[dict[str, Any]]] = {}
    part_number = 1
    overnight_number = 1
    used_overnight_keys: set[str] = set()

    while remaining > 0:
        day_end = cursor.replace(
            hour=DRIVING_DAY_END_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
        if cursor >= day_end or driven_today >= max_daily_minutes:
            cursor = (cursor + timedelta(days=1)).replace(
                hour=DRIVING_NEXT_DAY_START_HOUR,
                minute=0,
                second=0,
                microsecond=0,
            )
            driven_today = 0
            continue
        window_minutes = int((day_end - cursor).total_seconds() // 60)
        available = min(max_daily_minutes - driven_today, window_minutes)
        if available <= 0:
            continue
        duration = min(remaining, available)
        start_fraction = elapsed / total_duration
        end_fraction = (elapsed + duration) / total_duration
        piece_geometry = _slice_route_geometry(geometry, start_fraction, end_fraction)
        piece_end = cursor + timedelta(minutes=duration)
        is_last = duration >= remaining
        stop_place: dict[str, Any] | None = None
        if not is_last:
            stop_place = _overnight_stop_place(
                stage,
                stage_services,
                piece_geometry[-1],
                overnight_number,
                used_overnight_keys,
            )
            used_overnight_keys.add(_service_place_key(stop_place))
            overnight_number += 1
        piece = deepcopy(stage)
        piece["id"] = f"{stage.get('id', 'driving')}_calendar_{part_number}"
        piece["title"] = f"{stage.get('title', '长途驾驶')}（跨天第{part_number}段）"
        piece["origin"] = stage.get("origin") if not pieces else (pieces[-1].get("destination") or stop_place)
        piece["destination"] = stage.get("destination") if is_last else stop_place
        piece["waypoints"] = []
        piece["distance_km"] = round(total_distance * duration / total_duration, 2)
        piece["duration_minutes"] = duration
        piece["planned_start"] = cursor.isoformat()
        piece["planned_end"] = piece_end.isoformat()
        piece_segment = deepcopy(segment)
        piece_segment["coordinates"] = piece_geometry
        piece_segment["distance_km"] = piece["distance_km"]
        piece_segment["duration_minutes"] = duration
        piece["route_segments"] = [piece_segment]
        piece.pop("energy_estimate", None)
        piece["warnings"] = list(piece.get("warnings", []))
        piece["risk_tags"] = list(piece.get("risk_tags", []))
        if not is_last:
            piece["warnings"].append(
                _warning(
                    "OVERNIGHT_STOP_SCHEDULED",
                    "长途驾驶已拆分，下一段安排次日出发并预留过夜住宿",
                    "warning",
                    True,
                )
            )
            piece["risk_tags"].append("跨天驾驶")
        piece["_service_pois"] = stage_services
        pieces.append(piece)
        remaining -= duration
        elapsed += duration
        driven_today += duration
        part_number += 1
        if not is_last:
            next_start = (piece_end + timedelta(days=1)).replace(
                hour=DRIVING_NEXT_DAY_START_HOUR,
                minute=0,
                second=0,
                microsecond=0,
            )
            overnight_activities.append(
                _activity(
                    str(stage.get("day_id") or "day_1"),
                    len(overnight_activities),
                    "hotel",
                    stop_place or stage.get("destination", {}),
                    piece_end,
                    max(60, int((next_start - piece_end).total_seconds() // 60)),
                    "长途驾驶过夜休息（住宿需提前预订）",
                    required=True,
                )
            )
            cursor = next_start
            driven_today = 0
    return pieces, overnight_activities, piece_services


def _parse_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _slice_route_geometry(
    geometry: list[dict[str, Any]],
    start_fraction: float,
    end_fraction: float,
) -> list[dict[str, float]]:
    """Slice a polyline by normalized distance along its point index."""
    count = len(geometry)
    if count < 2:
        return geometry
    start_position = max(0.0, min(1.0, start_fraction)) * (count - 1)
    end_position = max(0.0, min(1.0, end_fraction)) * (count - 1)

    def at(position: float) -> dict[str, float]:
        left = min(count - 2, max(0, int(position)))
        fraction = position - left
        first = geometry[left]
        second = geometry[left + 1]
        return {
            "longitude": float(first["longitude"])
            + (float(second["longitude"]) - float(first["longitude"])) * fraction,
            "latitude": float(first["latitude"])
            + (float(second["latitude"]) - float(first["latitude"])) * fraction,
        }

    result = [at(start_position)]
    for index in range(int(start_position) + 1, int(end_position) + 1):
        if index < count - 1:
            result.append(
                {
                    "longitude": float(geometry[index]["longitude"]),
                    "latitude": float(geometry[index]["latitude"]),
                }
            )
    result.append(at(end_position))
    deduped: list[dict[str, float]] = []
    for point in result:
        if not deduped or point != deduped[-1]:
            deduped.append(point)
    return deduped


def _overnight_stop_place(
    stage: dict[str, Any],
    stage_services: dict[str, list[dict[str, Any]]],
    coordinate: dict[str, Any],
    number: int,
    used_keys: set[str] | None = None,
) -> dict[str, Any]:
    provider_candidates: list[dict[str, Any]] = []
    for category in ("overnight_hotel", "hotel", "rest"):
        provider_candidates.extend(
            place
            for place in (stage_services.get(category) or [])
            if place.get("coordinates")
            and _service_place_key(place) not in (used_keys or set())
        )
    if provider_candidates:
        place = min(
            provider_candidates,
            key=lambda item: (
                float(item["coordinates"]["longitude"]) - float(coordinate["longitude"])
            ) ** 2
            + (
                float(item["coordinates"]["latitude"]) - float(coordinate["latitude"])
            ) ** 2,
        )
        # A provider can return one terminal-area hotel for an entire
        # thousand-kilometre corridor. Use it only near the actual daily cut;
        # otherwise keep a route-derived booking placeholder at the correct
        # coordinate instead of teleporting every overnight to one property.
        squared_distance = (
            float(place["coordinates"]["longitude"]) - float(coordinate["longitude"])
        ) ** 2 + (
            float(place["coordinates"]["latitude"]) - float(coordinate["latitude"])
        ) ** 2
        if squared_distance <= 0.25:
            return place
    return {
        "id": f"route_overnight_{stage.get('id', 'stage')}_{number}",
        "name": "沿途服务区附近可入住酒店（需预订）",
        "address": "路线分段后的过夜位置；请出发前通过导航确认具体酒店、房态与取消政策",
        "city": (stage.get("origin") or {}).get("city")
        or (stage.get("destination") or {}).get("city"),
        "coordinates": {
            "longitude": float(coordinate["longitude"]),
            "latitude": float(coordinate["latitude"]),
        },
        "source_id": f"route-derived-overnight:{stage.get('id', 'stage')}:{number}",
    }


def verify_deep_drive_plan(
    plans: list[dict[str, Any]],
    vehicle: dict[str, Any] | None,
    max_continuous_drive_minutes: int,
    *,
    relaxation_level: int = 0,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not vehicle:
        issues.append(_issue("VEHICLE_DATA_MISSING", "warning", "车辆数据缺失，能耗只能降级估算"))
    for day in plans:
        stages = sorted(
            day.get("stages", []),
            key=lambda item: str(item.get("planned_start") or ""),
        )
        previous_stage: dict[str, Any] | None = None
        for stage in stages:
            route_segment = _first_route_segment(stage)
            codes = {item["code"] for item in stage.get("warnings", [])}
            movement_minutes = int(
                route_segment.get(
                    "duration_minutes",
                    stage.get("duration_minutes", 0),
                )
            )
            if "ENERGY_STOP_UNAVAILABLE" in codes:
                issues.append(_issue("ENERGY_UNSAFE", "blocker", f"{stage['title']} 未找到必要补能点"))
            if (
                stage["mode"] == "driving"
                and movement_minutes > max_continuous_drive_minutes
                and "REST_STOP_SCHEDULED" not in codes
            ):
                issues.append(_issue("CONTINUOUS_DRIVE", "blocker", f"{stage['title']} 缺少驾驶休息"))
            if not stage.get("weather_samples"):
                # Forecast gaps are expected for far-future dates or when a
                # provider is temporarily degraded.  Keep the itinerary
                # executable and surface this as a review reminder; weather
                # data alone must never turn an otherwise valid plan into a
                # failed verification.
                issues.append(
                    _issue(
                        "WEATHER_DEGRADED",
                        "warning",
                        f"{stage['title']} 预报天气暂不可用，已按基础风险继续规划",
                    )
                )
            start = datetime.fromisoformat(stage["planned_start"])
            end = datetime.fromisoformat(stage["planned_end"])
            if (
                stage.get("mode") == "driving"
                and day.get("date")
                and (start.date().isoformat() != str(day["date"]) or end.date() != start.date())
            ):
                issues.append(
                    _issue(
                        "DRIVING_STAGE_CALENDAR_MISMATCH",
                        "blocker",
                        f"{stage['title']} 已跨出所属日期，需减少当日驾驶或提前安排过夜",
                    )
                )
            if previous_stage is not None:
                previous_end = datetime.fromisoformat(previous_stage["planned_end"])
                if start < previous_end:
                    issues.append(
                        _issue(
                            "STAGE_OVERLAP",
                            "blocker",
                            f"{stage['title']} 与前一移动阶段时间重叠",
                        )
                    )
            elapsed_minutes = int((end - start).total_seconds() / 60)
            if end <= start or abs(elapsed_minutes - stage["duration_minutes"]) > 5:
                issues.append(
                    _issue(
                        "STAGE_TIME_INCONSISTENT",
                        "blocker",
                        f"{stage['title']} 的起止时间与阶段时长不一致",
                    )
                )
            if stage["mode"] == "walking" and (
                movement_minutes > 60 or stage["distance_km"] > 4
            ):
                issues.append(
                    _issue(
                        "WALKING_STAGE_TOO_LONG",
                        "blocker",
                        f"{stage['title']} 步行 {stage['duration_minutes']} 分钟，必须改用公共交通或拆段",
                    )
                )
            if stage["mode"] == "riding" and (
                movement_minutes > 90 or stage["distance_km"] > 18
            ):
                issues.append(
                    _issue(
                        "RIDING_STAGE_TOO_LONG",
                        "blocker",
                        f"{stage['title']} 骑行 {movement_minutes} 分钟，必须改用公共交通或拆段",
                    )
                )
            if stage["mode"] == "riding" and (
                stage["duration_minutes"] > 240 or stage["distance_km"] > 60
            ):
                issues.append(
                    _issue(
                        "RIDING_STAGE_TOO_LONG",
                        "blocker",
                        f"{stage['title']} 骑行距离或时长超出单阶段上限",
                    )
                )
            previous_stage = stage
        meals = [item for item in day.get("activities", []) if item.get("type") == "meal"]
        if len(meals) < 3:
            # The first pass is strict so the repair loop gets a chance to
            # fill a missing slot.  On retries, a genuine transport day may
            # use food on the train/plane, at a station/airport, or at a
            # service stop; do not fail the whole itinerary when that is the
            # only remaining gap.
            meal_severity = "warning" if int(relaxation_level or 0) >= 1 else "blocker"
            issues.append(
                _issue(
                    "DAILY_MEALS_INCOMPLETE",
                    meal_severity,
                    f"第 {day.get('day_index')} 天未完整安排早餐、午餐和晚餐",
                )
            )
    if int(relaxation_level or 0) >= 1 and any(
        item.get("code") == "DAILY_MEALS_INCOMPLETE" and item.get("severity") == "warning"
        for item in issues
    ):
        issues.append(
            _issue(
                "MEAL_FALLBACK_ALLOWED",
                "warning",
                "\u9910\u5385\u65e0\u53ef\u7528\u65f6\uff0c\u53ef\u5728\u8f66\u4e0a\u3001\u706b\u8f66/\u98de\u673a\u4e0a\u3001\u673a\u573a/\u8f66\u7ad9\u6216\u670d\u52a1\u533a\u7528\u4fbf\u643a\u9910\uff08\u5982\u6ce1\u9762\uff09\uff0c\u4e0d\u518d\u963b\u65ad\u884c\u7a0b\u3002",
            )
        )
    return _dedupe_issues(issues)


def _energy_use(stage: dict[str, Any], vehicle: dict[str, Any]) -> tuple[float, str, float]:
    distance = float(stage["distance_km"])
    # Route difficulty comes from the elevation skill, not from guessing from
    # Chinese place-name characters such as “山” or “峰”.
    elevation_gain = _finite_number(stage.get("elevation_gain_m")) or 0.0
    factor = 1.12 if elevation_gain >= ELEVATED_ROUTE_THRESHOLD_M else 1.0
    consumption = _positive_number(vehicle.get("consumption_per_100km")) or (
        18.0 if vehicle.get("power_type") == "electric" else 7.5
    )
    amount = distance * consumption / 100 * factor
    if vehicle.get("power_type") == "electric":
        capacity = _positive_number(vehicle.get("battery_kwh")) or 75.0
        return amount, "kWh", amount / capacity * 100
    rated_range = _positive_number(vehicle.get("rated_range_km")) or 650.0
    return amount, "L", distance / rated_range * 100 * factor


def _calculate_replenishment(
    stage: dict[str, Any],
    vehicle: dict[str, Any],
    stop_place: dict[str, Any],
    *,
    starting_percent: float,
    consumed_percent: float,
    reserve_percent: float,
) -> dict[str, Any]:
    """Calculate a continuous energy balance around one en-route stop.

    Provider-reported delivered energy wins. Otherwise EV replenishment is
    derived from station/vehicle power and duration; when the provider omits
    power, a deliberately conservative public-fast-charge value is used and
    exposed through ``calculation_basis``. No branch resets SOC to a fixed
    percentage.
    """
    stop_fraction = _energy_stop_fraction(stage, stop_place)
    consumed_before = consumed_percent * stop_fraction
    consumed_after = max(0.0, consumed_percent - consumed_before)
    before = max(0.0, starting_percent - consumed_before)
    power_type = vehicle.get("power_type", "electric")

    if power_type == "electric":
        capacity = _positive_number(vehicle.get("battery_kwh")) or 75.0
        reported_energy = _first_positive_number(
            stop_place,
            "actual_energy_added_kwh",
            "delivered_energy_kwh",
            "energy_added_kwh",
        )
        reported_power = _first_positive_number(
            stop_place,
            "charging_power_kw",
            "charger_power_kw",
            "power_kw",
            "max_power_kw",
        )
        vehicle_power = _positive_number(vehicle.get("max_charge_kw"))
        reported_minutes = _first_positive_number(
            stop_place,
            "charging_minutes",
            "planned_charge_minutes",
            "duration_minutes",
        )
        target_after_stop = min(
            90.0,
            max(before, reserve_percent + consumed_after + 5.0),
        )
        room_kwh = max(0.0, capacity * (100.0 - before) / 100.0)

        if reported_energy is not None:
            added = min(reported_energy, room_kwh)
            effective_power = (
                min(reported_power, vehicle_power)
                if reported_power is not None and vehicle_power is not None
                else reported_power or vehicle_power
            )
            minutes = int(round(reported_minutes or 0))
            if not minutes and effective_power:
                minutes = max(1, ceil(added / (effective_power * 0.9) * 60))
            elif not minutes:
                minutes = 30
            basis = "measured_energy"
            estimated = False
        else:
            if reported_power is not None:
                effective_power = min(reported_power, vehicle_power or reported_power)
                basis = "charger_power"
            else:
                effective_power = min(vehicle_power or 60.0, 60.0)
                basis = "conservative_fallback"
            required_kwh = max(0.0, capacity * (target_after_stop - before) / 100.0)
            if reported_minutes is not None:
                minutes = max(1, int(round(reported_minutes)))
                added = min(room_kwh, effective_power * minutes / 60.0 * 0.9)
            else:
                minutes = max(1, min(90, ceil(required_kwh / (effective_power * 0.9) * 60)))
                added = min(room_kwh, effective_power * minutes / 60.0 * 0.9)
            estimated = True

        after = min(100.0, before + added / capacity * 100.0)
        remaining = max(0.0, after - consumed_after)
        return {
            "before_replenishment_percent": round(before, 1),
            "replenished_amount": round(added, 1),
            "replenished_unit": "kWh",
            "replenishment_minutes": minutes,
            "charging_power_kw": round(effective_power, 1) if effective_power else None,
            "after_replenishment_percent": round(after, 1),
            "remaining_percent": round(remaining, 1),
            "calculation_basis": basis,
            "estimated": estimated,
        }

    consumption_l_per_100km = _positive_number(vehicle.get("consumption_per_100km")) or 7.5
    rated_range = _positive_number(vehicle.get("rated_range_km")) or 650.0
    tank_liters = max(1.0, rated_range * consumption_l_per_100km / 100.0)
    reported_liters = _first_positive_number(
        stop_place,
        "actual_fuel_added_liters",
        "fuel_added_liters",
    )
    desired_after = min(100.0, max(before, reserve_percent + consumed_after + 10.0))
    added_liters = min(
        reported_liters if reported_liters is not None else tank_liters * (desired_after - before) / 100.0,
        tank_liters * (100.0 - before) / 100.0,
    )
    after = min(100.0, before + added_liters / tank_liters * 100.0)
    return {
        "before_replenishment_percent": round(before, 1),
        "replenished_amount": round(added_liters, 1),
        "replenished_unit": "L",
        "replenishment_minutes": int(round(_first_positive_number(stop_place, "fueling_minutes") or 15)),
        "charging_power_kw": None,
        "after_replenishment_percent": round(after, 1),
        "remaining_percent": round(max(0.0, after - consumed_after), 1),
        "calculation_basis": "measured_energy" if reported_liters is not None else "fuel_service_estimate",
        "estimated": reported_liters is None,
    }


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _first_positive_number(item: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if (number := _positive_number(item.get(key))) is not None:
            return number
    return None


def _energy_stop_fraction(stage: dict[str, Any], stop_place: dict[str, Any]) -> float:
    geometry = _stage_geometry(stage, minimum_points=21)
    coordinates = _coordinate_pair(stop_place.get("coordinates"))
    if not geometry or coordinates is None:
        return 0.5
    index = _nearest_geometry_index(geometry, coordinates)
    return max(0.05, min(0.95, index / max(1, len(geometry) - 1)))


def _apply_weather_risk(
    stage: dict[str, Any],
    warnings: list[dict[str, Any]],
    tags: list[str],
) -> None:
    samples = stage.get("weather_samples", [])
    if not samples:
        warnings.append(_warning("WEATHER_DATA_DEGRADED", "预报天气暂不可用，已按基础风险继续规划", "warning", True))
        tags.append("天气数据不足")
        return
    sample = samples[0]
    raw_values = {
        "precipitation": sample.get("precipitation_probability"),
        "visibility": sample.get("visibility_m"),
        "wind": sample.get("wind_speed_kmh"),
        "temperature": sample.get("temperature_c"),
    }
    parsed_values = {key: _finite_number(value) for key, value in raw_values.items()}
    if any(value is not None and parsed_values[key] is None for key, value in raw_values.items()):
        warnings.append(
            _warning(
                "WEATHER_DATA_PARTIAL",
                "部分天气字段格式异常，已忽略异常字段并按可用信息继续规划",
                "warning",
                True,
            )
        )
        tags.append("天气数据部分可用")
    precipitation = parsed_values["precipitation"]
    visibility = parsed_values["visibility"]
    wind = parsed_values["wind"]
    temperature = parsed_values["temperature"]
    if precipitation is not None and precipitation >= 60:
        warnings.append(_warning("HEAVY_PRECIPITATION", f"预计降水概率 {precipitation:.0f}%", "error"))
        tags.append("强降水")
    if visibility is not None and visibility < 2000:
        warnings.append(_warning("LOW_VISIBILITY", f"预计能见度仅 {visibility / 1000:.1f} km", "error"))
        tags.append("低能见度")
    if wind is not None and wind >= 40:
        warnings.append(_warning("HIGH_WIND", f"预计阵风/风速约 {wind:.0f} km/h", "warning"))
        tags.append("大风")
    if temperature is not None and (temperature <= 0 or temperature >= 38):
        warnings.append(_warning("EXTREME_TEMPERATURE", f"预计气温 {temperature:.0f}°C", "warning"))
        tags.append("极端温度")


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _apply_night_risk(
    stage: dict[str, Any],
    warnings: list[dict[str, Any]],
    tags: list[str],
) -> None:
    start = datetime.fromisoformat(stage["planned_start"])
    end = datetime.fromisoformat(stage["planned_end"])
    if start.hour < 6 or end.hour >= 21:
        warnings.append(_warning("NIGHT_DRIVING", "阶段包含夜间驾驶/移动，建议调整时间", "warning", True))
        tags.append("夜间")


def _apply_vehicle_restrictions(
    stage: dict[str, Any],
    vehicle: dict[str, Any],
    warnings: list[dict[str, Any]],
    tags: list[str],
) -> None:
    elevation_gain = float(stage.get("elevation_gain_m") or 0)
    if elevation_gain >= ELEVATED_ROUTE_THRESHOLD_M and not vehicle.get("mountain_ready", True):
        warnings.append(_warning("MOUNTAIN_CAPABILITY", "车辆未标记为适合山路", "error"))
        tags.append("山路适配")
    if float(vehicle.get("height_m") or 0) >= 2.2:
        warnings.append(_warning("VEHICLE_HEIGHT_REVIEW", "车高达到 2.2 m，需复核限高", "warning", True))
        tags.append("限高")


def _activity(
    day_id: str,
    sequence: int,
    activity_type: str,
    place: dict[str, Any],
    start: datetime,
    duration: int,
    note: str,
    required: bool,
) -> dict[str, Any]:
    return Activity(
        day_id=day_id,
        sequence=sequence,
        type=activity_type,
        place=PlaceRef.model_validate(place),
        planned_start=start,
        planned_end=start + timedelta(minutes=duration),
        duration_minutes=duration,
        required=required,
        user_note=note,
    ).model_dump(mode="json")


def _midpoint_time(stage: dict[str, Any]) -> datetime:
    start = datetime.fromisoformat(stage["planned_start"])
    end = datetime.fromisoformat(stage["planned_end"])
    return start + (end - start) / 2


def _crosses_lunch(stage: dict[str, Any]) -> bool:
    start = datetime.fromisoformat(stage["planned_start"])
    end = datetime.fromisoformat(stage["planned_end"])
    return start.hour < 13 and (end.hour > 11 or (end.hour == 11 and end.minute >= 30))


def _split_long_driving_stages(
    stages: list[dict[str, Any]],
    service_pois: dict[str, dict[str, list[dict[str, Any]]]],
    max_minutes: int,
    power_type: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expanded: list[dict[str, Any]] = []
    breaks: list[dict[str, Any]] = []
    max_minutes = max(1, int(max_minutes or 120))
    for stage in stages:
        route_segment = _first_route_segment(stage)
        movement_duration = int(
            route_segment.get(
                "duration_minutes",
                stage.get("duration_minutes", 0),
            )
        )
        if stage.get("mode") != "driving" or movement_duration <= max_minutes:
            expanded.append(stage)
            continue
        part_count = ceil(movement_duration / max_minutes)
        # A route provider may return a simplified/empty polyline for a long
        # intercity leg.  Always recover the endpoints from the stage before
        # deciding that the leg cannot be split; otherwise the verifier sees
        # the original oversized “城市出发” stage and reports a false missing
        # rest stop.
        geometry = _stage_geometry(stage, minimum_points=part_count + 1)
        if len(geometry) < 2:
            expanded.append(stage)
            continue
        services = service_pois.get(stage["id"], {})
        candidates: list[tuple[str, dict[str, Any]]] = []
        energy_kind = "charging" if power_type == "electric" else "fueling"
        for kind in (energy_kind, "rest"):
            for place in services.get(kind, []):
                if place.get("coordinates"):
                    candidates.append((kind, place))
        warning_codes = {item.get("code") for item in stage.get("warnings", [])}
        energy_candidates = [item for item in candidates if item[0] == energy_kind]
        selected = (
            _select_break_places(stage, energy_candidates, 1)
            if warning_codes.intersection(
                {"ENERGY_STOP_SCHEDULED", "ENERGY_STOP_ESTIMATED"}
            )
            and energy_candidates
            else []
        )
        if len(selected) < part_count - 1:
            selected_names = {item[1].get("name") for item in selected}
            remaining = [
                item for item in candidates if item[1].get("name") not in selected_names
            ]
            rest_candidates = [item for item in remaining if item[0] == "rest"]
            selected.extend(
                _select_break_places(
                    stage,
                    rest_candidates,
                    part_count - 1 - len(selected),
                )
            )
            if len(selected) < part_count - 1:
                selected_names = {item[1].get("name") for item in selected}
                other_candidates = [
                    item
                    for item in remaining
                    if item[0] != "rest" and item[1].get("name") not in selected_names
                ]
                selected.extend(
                    _select_break_places(
                        stage,
                        other_candidates,
                        part_count - 1 - len(selected),
                    )
                )
        if len(selected) < part_count - 1:
            selected = _fill_planned_rest_points(
                stage,
                selected,
                part_count - 1,
            )

        # ``_fill_planned_rest_points`` intentionally de-duplicates POIs that
        # snap to the same route vertex.  Keep a final guard here as well: a
        # duplicate index would create non-increasing boundaries and used to
        # silently return the unsplit stage.
        if len(selected) != part_count - 1:
            selected = _fill_planned_rest_points(stage, [], part_count - 1)
        if len(selected) != part_count - 1:
            expanded.append(stage)
            continue

        split_indexes = [
            _nearest_geometry_index(geometry, place["coordinates"])
            for _, place in selected
        ]
        boundaries = [0, *split_indexes, len(geometry) - 1]
        if (
            len(set(split_indexes)) != len(split_indexes)
            or any(second <= first for first, second in zip(boundaries, boundaries[1:]))
        ):
            expanded.append(stage)
            continue

        cursor = datetime.fromisoformat(stage["planned_start"])
        remaining_duration = movement_duration
        remaining_distance = float(stage["distance_km"])
        target_duration = ceil(movement_duration / part_count)
        base_energy = deepcopy(stage.get("energy_estimate") or {})
        split_energy_percent = float(
            base_energy.get("starting_percent")
            if base_energy.get("starting_percent") is not None
            else 0.0
        )
        total_energy_amount = float(base_energy.get("amount") or 0.0)
        total_consumed_percent = float(base_energy.get("consumed_percent") or 0.0)
        replenished_percent = max(
            0.0,
            float(base_energy.get("after_replenishment_percent") or 0.0)
            - float(base_energy.get("before_replenishment_percent") or 0.0),
        )
        replenishment_applied = False
        for index in range(part_count):
            fraction = (boundaries[index + 1] - boundaries[index]) / (len(geometry) - 1)
            if index == part_count - 1:
                duration = remaining_duration
                distance = remaining_distance
            else:
                duration = min(target_duration, remaining_duration - (part_count - index - 1))
                distance = max(0.01, round(stage["distance_km"] * fraction, 2))
                remaining_duration -= duration
                remaining_distance -= distance
            piece = deepcopy(stage)
            piece["id"] = f"{stage['id']}_part_{index + 1}"
            piece["title"] = f"{stage['title']} · 第 {index + 1}/{part_count} 段"
            piece["origin"] = stage["origin"] if index == 0 else selected[index - 1][1]
            piece["destination"] = (
                stage["destination"] if index == part_count - 1 else selected[index][1]
            )
            piece["waypoints"] = []
            piece["distance_km"] = round(distance, 2)
            piece["duration_minutes"] = duration
            piece["planned_start"] = cursor.isoformat()
            piece_end = cursor + timedelta(minutes=duration)
            piece["planned_end"] = piece_end.isoformat()
            segment = deepcopy(_first_route_segment(stage))
            segment.setdefault("distance_km", distance)
            segment.setdefault("duration_minutes", duration)
            segment["coordinates"] = geometry[
                boundaries[index] : boundaries[index + 1] + 1
            ]
            segment["distance_km"] = round(distance, 2)
            segment["duration_minutes"] = duration
            piece["route_segments"] = [segment]
            if piece.get("energy_estimate"):
                distance_fraction = (
                    float(distance) / float(stage["distance_km"])
                    if float(stage["distance_km"]) > 0
                    else 1.0 / part_count
                )
                piece_consumed = total_consumed_percent * distance_fraction
                piece_starting = split_energy_percent
                before_stop = max(0.0, piece_starting - piece_consumed)
                is_energy_boundary = (
                    index < part_count - 1
                    and selected[index][0] in {"charging", "fueling"}
                    and replenished_percent > 0
                    and not replenishment_applied
                )
                piece_energy = {
                    **base_energy,
                    "amount": round(total_energy_amount * distance_fraction, 1),
                    "starting_percent": round(piece_starting, 1),
                    "consumed_percent": round(piece_consumed, 1),
                    "before_replenishment_percent": None,
                    "replenished_amount": None,
                    "replenished_unit": None,
                    "replenishment_minutes": None,
                    "charging_power_kw": None,
                    "after_replenishment_percent": None,
                    "remaining_percent": round(before_stop, 1),
                    "calculation_basis": "consumption_model",
                    "estimated": True,
                }
                if is_energy_boundary:
                    after_stop = min(100.0, before_stop + replenished_percent)
                    piece_energy.update(
                        {
                            "before_replenishment_percent": round(before_stop, 1),
                            "replenished_amount": base_energy.get("replenished_amount"),
                            "replenished_unit": base_energy.get("replenished_unit"),
                            "replenishment_minutes": base_energy.get("replenishment_minutes"),
                            "charging_power_kw": base_energy.get("charging_power_kw"),
                            "after_replenishment_percent": round(after_stop, 1),
                            "remaining_percent": round(after_stop, 1),
                            "calculation_basis": base_energy.get(
                                "calculation_basis", "consumption_model"
                            ),
                            "estimated": bool(base_energy.get("estimated", True)),
                        }
                    )
                    split_energy_percent = after_stop
                    replenishment_applied = True
                else:
                    split_energy_percent = before_stop
                piece["energy_estimate"] = piece_energy
            if index < part_count - 1:
                piece_warnings = list(piece.get("warnings", []))
                piece_warnings.append(
                    _warning(
                        "REST_STOP_SCHEDULED",
                        "已按路线插入驾驶休息/补能停靠点",
                        "warning",
                        True,
                    )
                )
                piece["warnings"] = _dedupe_warnings(piece_warnings)
                piece["risk_tags"] = list(
                    dict.fromkeys([*piece.get("risk_tags", []), "连续驾驶"])
                )
            expanded.append(piece)
            # The energy pass runs on these new piece IDs. Preserve the
            # original corridor candidates so every piece can select a nearby
            # charge/fuel/rest point or transparently derive a fallback.
            service_pois[piece["id"]] = services
            if index < part_count - 1:
                kind, place = selected[index]
                energy_minutes = (
                    (piece.get("energy_estimate") or {}).get("replenishment_minutes")
                    if kind in {"charging", "fueling"}
                    else None
                )
                break_minutes = int(energy_minutes or (30 if kind == "charging" else 20))
                breaks.append(
                    _activity(
                        stage["day_id"],
                        len(breaks),
                        kind,
                        place,
                        piece_end,
                        break_minutes,
                        "长途驾驶分段休息与补能",
                        required=True,
                    )
                )
                cursor = piece_end + timedelta(minutes=break_minutes)
    for sequence, piece in enumerate(expanded):
        piece["sequence"] = sequence
    return expanded, breaks


def _fill_planned_rest_points(
    stage: dict[str, Any],
    selected: list[tuple[str, dict[str, Any]]],
    count: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Use route-bound safety break nodes when providers return too few facilities."""
    if count <= 0:
        return []
    geometry = _stage_geometry(stage, minimum_points=count + 2)
    if len(geometry) < 2:
        return selected[:count]

    # Keep at most one facility per route vertex.  AMap often returns several
    # nearby service POIs with identical coordinates; counting all of them
    # made the old implementation stop before creating synthetic breaks.
    indexed_by_route_index: dict[int, tuple[str, dict[str, Any]]] = {}
    used_names: set[str] = set()
    for kind, place in selected:
        coordinates = _coordinate_pair(place.get("coordinates"))
        if not coordinates:
            continue
        index = _nearest_geometry_index(geometry, coordinates)
        if not 0 < index < len(geometry) - 1:
            continue
        name = str(place.get("name") or "")
        if index in indexed_by_route_index or (name and name in used_names):
            continue
        indexed_by_route_index[index] = (kind, place)
        if name:
            used_names.add(name)

    used_indexes = set(indexed_by_route_index)
    targets = [
        round((len(geometry) - 1) * number / (count + 1))
        for number in range(1, count + 1)
    ]
    for number, target in enumerate(targets, start=1):
        if len(indexed_by_route_index) >= count:
            break
        target = _nearest_free_interior_index(target, len(geometry), used_indexes)
        if target is None:
            continue
        point = geometry[target]
        place = {
            "id": f"rest_{stage.get('id', 'stage')}_{number}",
            "name": _derived_rest_place_name(stage, number, count),
            "address": _derived_rest_place_address(stage, number, count),
            "city": stage.get("origin", {}).get("city") or stage.get("destination", {}).get("city"),
            "coordinates": {
                "longitude": float(point["longitude"]),
                "latitude": float(point["latitude"]),
            },
            "source_id": f"route-derived:{stage.get('id', 'stage')}:{number}",
        }
        indexed_by_route_index[target] = ("rest", place)
        used_indexes.add(target)

    # If a provider gave an unusually short/duplicated geometry, fill any
    # remaining interior vertices deterministically after densification.
    if len(indexed_by_route_index) < count:
        for target in range(1, len(geometry) - 1):
            if len(indexed_by_route_index) >= count:
                break
            if target in used_indexes:
                continue
            point = geometry[target]
            number = len(indexed_by_route_index) + 1
            indexed_by_route_index[target] = (
                "rest",
                {
                    "id": f"rest_{stage.get('id', 'stage')}_{number}",
                    "name": _derived_rest_place_name(stage, number, count),
                    "address": _derived_rest_place_address(stage, number, count),
                    "city": stage.get("origin", {}).get("city") or stage.get("destination", {}).get("city"),
                    "coordinates": {
                        "longitude": float(point["longitude"]),
                        "latitude": float(point["latitude"]),
                    },
                    "source_id": f"route-derived:{stage.get('id', 'stage')}:{number}",
                },
            )
            used_indexes.add(target)

    # Multiple service POIs can project onto the same route geometry point.
    # Sort only by the numeric route index; comparing the nested place dicts
    # on a tie raises ``TypeError: '<' not supported between instances of
    # 'dict' and 'dict'`` and aborts otherwise valid long-distance plans.
    ordered = sorted(indexed_by_route_index.items(), key=lambda item: item[0])
    return [item for _, item in ordered[:count]]


def _derived_rest_place_name(stage: dict[str, Any], number: int, count: int) -> str:
    """Give a generated safety stop a useful corridor label.

    Provider-returned service areas keep their real names. Only when a
    provider has no named result do we derive an honest, route-bound label;
    this avoids the opaque ``休息地点1234`` placeholders while making clear
    that the exact service-area name still needs navigation confirmation.
    """
    segment = _first_route_segment(stage)
    road = str(segment.get("road_name") or "").strip()
    road = road.split(" / ", 1)[0].strip()
    origin_city = str((stage.get("origin") or {}).get("city") or "").strip()
    destination_city = str((stage.get("destination") or {}).get("city") or "").strip()
    if number <= 1:
        position = "前段"
    elif number >= max(1, count):
        position = "后段"
    else:
        position = "中段"
    if road:
        return f"{road}沿线{position}服务区候选（需确认具体名称）"
    corridor = "至".join(item for item in (origin_city, destination_city) if item)
    if corridor:
        return f"{corridor}沿途{position}服务区候选（需确认具体名称）"
    return f"沿途{position}服务区候选（需确认具体名称）"


def _derived_rest_place_address(stage: dict[str, Any], number: int, count: int) -> str:
    """Explain why a generated stop has no provider address yet."""
    segment = _first_route_segment(stage)
    road = str(segment.get("road_name") or "").strip()
    context = road.split(" / ", 1)[0].strip() if road else "路线估算位置"
    return f"{context}的路线估算停靠位置；请出发前通过导航确认服务区名称、营业与可停车状态"


def _first_route_segment(stage: dict[str, Any]) -> dict[str, Any]:
    segments = stage.get("route_segments")
    if not isinstance(segments, list) or not segments or not isinstance(segments[0], dict):
        # Keep the stage schema valid: an empty list is allowed, while a
        # placeholder ``{}`` would later fail RouteSegment validation.  Callers
        # use the returned ephemeral dict only for best-effort duration/
        # geometry recovery.
        stage["route_segments"] = []
        return {}
    return segments[0]


def _coordinate_pair(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    longitude = value.get("longitude", value.get("lng"))
    latitude = value.get("latitude", value.get("lat"))
    try:
        return {"longitude": float(longitude), "latitude": float(latitude)}
    except (TypeError, ValueError):
        return None


def _stage_geometry(
    stage: dict[str, Any],
    *,
    minimum_points: int = 2,
) -> list[dict[str, float]]:
    """Return a valid route polyline, recovering endpoints when needed."""
    segment = _first_route_segment(stage)
    geometry = [
        coordinates
        for point in (segment.get("coordinates") or [])
        if (coordinates := _coordinate_pair(point)) is not None
    ]
    if len(geometry) < 2:
        origin = _coordinate_pair((stage.get("origin") or {}).get("coordinates"))
        destination = _coordinate_pair((stage.get("destination") or {}).get("coordinates"))
        if origin and destination:
            geometry = [origin, destination]
        elif origin:
            geometry = [origin, dict(origin)]
        elif destination:
            geometry = [dict(destination), destination]
    if len(geometry) < 2:
        return []
    geometry = _densify_geometry(geometry, max(2, minimum_points))
    segment["coordinates"] = geometry
    return geometry


def _nearest_free_interior_index(
    target: int,
    geometry_length: int,
    used_indexes: set[int],
) -> int | None:
    if geometry_length <= 2:
        return None
    interior = range(1, geometry_length - 1)
    choices = [index for index in interior if index not in used_indexes]
    if not choices:
        return None
    return min(choices, key=lambda index: (abs(index - target), index))


def _route_derived_stop_place(
    stage: dict[str, Any],
    stop_type: str,
) -> dict[str, Any] | None:
    """Create an estimated energy stop when the corridor search is empty."""
    geometry = _stage_geometry(stage, minimum_points=3)
    if len(geometry) < 2:
        return None
    index = max(1, min(len(geometry) - 2, round((len(geometry) - 1) / 2)))
    point = geometry[index]
    label = "充电点" if stop_type == "charging" else "加油点"
    stage_id = str(stage.get("id") or "stage")
    part_label = stage_id.rsplit("_part_", 1)[-1] if "_part_" in stage_id else ""
    display_label = (
        f"沿途估算{label}（第{part_label}段，需确认）"
        if part_label
        else f"沿途估算{label}（需确认）"
    )
    return {
        "id": f"route_estimated_{stop_type}_{stage.get('id', 'stage')}",
        "name": display_label,
        "address": "路线估算位置；出发前请通过导航确认实际营业与可用状态",
        "city": (stage.get("origin") or {}).get("city")
        or (stage.get("destination") or {}).get("city"),
        "coordinates": {
            "longitude": float(point["longitude"]),
            "latitude": float(point["latitude"]),
        },
        "source_id": f"route-derived-energy:{stage.get('id', 'stage')}:{stop_type}",
        "estimated": True,
    }


def _densify_geometry(
    geometry: list[dict[str, Any]],
    minimum_points: int,
) -> list[dict[str, float]]:
    if len(geometry) >= minimum_points or len(geometry) < 2:
        return geometry
    # Provider geometries are often simplified to only two or three points
    # for a long leg.  Interpolate by point index for any short polyline so
    # each safety break can snap to a distinct route vertex.
    positions = [
        index * (len(geometry) - 1) / (minimum_points - 1)
        for index in range(minimum_points)
    ]
    dense: list[dict[str, float]] = []
    for position in positions:
        left = min(len(geometry) - 2, max(0, int(position)))
        fraction = position - left
        first = geometry[left]
        second = geometry[left + 1]
        dense.append(
            {
                "longitude": float(first["longitude"])
                + (float(second["longitude"]) - float(first["longitude"])) * fraction,
                "latitude": float(first["latitude"])
                + (float(second["latitude"]) - float(first["latitude"])) * fraction,
            }
        )
    return dense


def _select_break_places(
    stage: dict[str, Any],
    candidates: list[tuple[str, dict[str, Any]]],
    count: int,
) -> list[tuple[str, dict[str, Any]]]:
    geometry = _stage_geometry(stage, minimum_points=3)
    if len(geometry) < 3:
        return []
    indexed = [
        (_nearest_geometry_index(geometry, place["coordinates"]), kind, place)
        for kind, place in candidates
    ]
    selected: list[tuple[int, str, dict[str, Any]]] = []
    used: set[str] = set()
    used_indexes: set[int] = set()
    for number in range(1, count + 1):
        target = round((len(geometry) - 1) * number / (count + 1))
        choices = [
            item
            for item in indexed
            if (
                item[2].get("name") not in used
                and item[0] not in used_indexes
                and 0 < item[0] < len(geometry) - 1
            )
        ]
        if not choices:
            break
        choice = min(choices, key=lambda item: abs(item[0] - target))
        selected.append(choice)
        used.add(choice[2].get("name", ""))
        used_indexes.add(choice[0])
    # Do not let equal geometry indexes fall through to tuple comparison of
    # the nested POI dictionaries.  AMap commonly returns several nearby
    # service points that map to the same sampled route vertex.
    ordered = sorted(selected, key=lambda item: item[0])
    return [(kind, place) for _, kind, place in ordered]


def _nearest_geometry_index(
    geometry: list[dict[str, Any]],
    coordinates: dict[str, Any],
) -> int:
    return min(
        range(len(geometry)),
        key=lambda index: (
            (geometry[index]["longitude"] - coordinates["longitude"]) ** 2
            + (geometry[index]["latitude"] - coordinates["latitude"]) ** 2
        ),
    )


def _ensure_daily_meals(
    day: dict[str, Any],
    activities: list[dict[str, Any]],
    service_pois: dict[str, dict[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    stages = day.get("stages", [])
    if not stages:
        return activities
    meal_places = [
        place
        for stage_services in service_pois.values()
        for place in stage_services.get("meal", [])
        if place.get("coordinates")
    ]
    base_place = stages[0]["origin"]
    destination_place = stages[-1]["destination"]
    first_start = datetime.fromisoformat(stages[0]["planned_start"])
    day_date = first_start
    breakfast_hour = 9 if first_start.hour >= 11 else 7
    lunch_hour = 13 if first_start.hour >= 11 else 12
    meal_slots = [
        ("早餐", time(hour=breakfast_hour, minute=0 if breakfast_hour == 9 else 30), time(6, 0), time(11, 0)),
        ("午餐", time(hour=lunch_hour), time(11, 0), time(15, 0)),
        ("晚餐", time(18, 30), time(17, 0), time(22, 0)),
    ]
    stage_ranges = [
        (
            datetime.fromisoformat(stage["planned_start"]),
            datetime.fromisoformat(stage["planned_end"]),
        )
        for stage in stages
    ]
    occupied = [
        *stage_ranges,
        *[
            (datetime.fromisoformat(item["planned_start"]), datetime.fromisoformat(item["planned_end"]))
            for item in activities
            if item.get("planned_start") and item.get("planned_end")
        ],
    ]
    for slot_index, (label, preferred_time, window_start_time, window_end_time) in enumerate(meal_slots):
        if any(
            datetime.fromisoformat(item["planned_start"]).hour
            in (
                range(6, 10) if label == "早餐"
                else range(11, 15) if label == "午餐"
                else range(17, 22)
            )
            for item in activities
            if item.get("type") == "meal"
        ):
            continue
        if label == "早餐":
            place = deepcopy(base_place)
        elif label == "晚餐":
            place = deepcopy(destination_place)
        elif meal_places:
            place = deepcopy(meal_places[slot_index % len(meal_places)])
        else:
            place = deepcopy(base_place)
        if label != "午餐" or not meal_places:
            place["name"] = f"{place['name']}附近{label}"
        preferred = day_date.replace(hour=preferred_time.hour, minute=preferred_time.minute)
        window_start = day_date.replace(hour=window_start_time.hour, minute=window_start_time.minute)
        window_end = day_date.replace(hour=window_end_time.hour, minute=window_end_time.minute)
        start = _find_meal_slot(preferred, window_start, window_end, occupied)
        if start is None:
            continue
        activities.append(
            _activity(
                day["id"],
                len(activities),
                "meal",
                place,
                start,
                45,
                f"每日{label}安排",
                required=True,
            )
        )
        occupied.append((start, start + timedelta(minutes=45)))
    return _dedupe_activities(activities)


def _find_meal_slot(
    preferred: datetime,
    window_start: datetime,
    window_end: datetime,
    occupied: list[tuple[datetime, datetime]],
) -> datetime | None:
    duration = timedelta(minutes=45)
    candidates = [
        preferred + timedelta(minutes=15 * offset)
        for offset in range(-12, 13)
    ]
    for candidate in sorted(candidates, key=lambda value: abs((value - preferred).total_seconds())):
        if candidate < window_start or candidate + duration > window_end:
            continue
        if all(candidate + duration <= start or candidate >= end for start, end in occupied):
            return candidate
    return None


def _dedupe_activities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        key = (
            item["type"],
            item["place"]["name"],
            item["planned_start"][:16],
        )
        unique[key] = item
    result = sorted(unique.values(), key=lambda item: item["planned_start"])
    for sequence, item in enumerate(result):
        item["sequence"] = sequence
    return result


def _first_place(items: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    return items[0] if items else None


def _service_place_key(place: dict[str, Any]) -> str:
    coordinates = place.get("coordinates") or {}
    return "|".join(
        [
            str(place.get("id") or place.get("source_id") or place.get("name") or ""),
            str(coordinates.get("longitude") or ""),
            str(coordinates.get("latitude") or ""),
        ]
    )


def _warning(
    code: str,
    message: str,
    severity: str,
    estimated: bool = False,
) -> dict[str, Any]:
    return PlanWarning(
        code=code,
        message=message,
        severity=severity,
        estimated=estimated,
    ).model_dump(mode="json")


def _risk_level(warnings: list[dict[str, Any]]) -> str:
    if any(item["severity"] == "error" for item in warnings):
        return "high"
    if warnings:
        return "moderate"
    return "low"


def _dedupe_warnings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({(item["code"], item["message"]): item for item in items}.values())


def _issue(code: str, severity: str, description: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "description": description}


def _dedupe_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({(item["code"], item["description"]): item for item in items}.values())
