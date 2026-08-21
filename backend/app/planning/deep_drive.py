from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timedelta
from math import ceil
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
    current_percent = float(vehicle.get("current_energy_percent") or 80)
    reserve = float(vehicle.get("safe_energy_reserve_percent") or 15)
    power_type = vehicle.get("power_type", "electric")
    warnings: list[dict[str, Any]] = []

    # Split before energy/rest enrichment.  This makes the calendar date an
    # invariant for every persisted movement stage and gives the later
    # continuous-driving splitter a normal, day-sized stage to work with.
    _split_cross_day_driving_stages(
        plans,
        service_pois,
        max_daily_drive_minutes=max_daily_drive_minutes,
    )

    for day in plans:
        activities: list[dict[str, Any]] = list(day.get("activities", []))
        for stage in day.get("stages", []):
            stage_warnings = [
                PlanWarning.model_validate(item).model_dump(mode="json")
                for item in stage.get("warnings", [])
            ]
            risk_tags: list[str] = list(stage.get("risk_tags", []))
            if stage["mode"] == "driving":
                amount, unit, used_percent = _energy_use(stage, vehicle)
                projected = max(0.0, current_percent - used_percent)
                stage["energy_estimate"] = EnergyEstimate(
                    amount=round(amount, 1),
                    unit=unit,
                    remaining_percent=round(projected, 1),
                    estimated=True,
                ).model_dump(mode="json")

                needs_energy = projected < reserve
                needs_rest = stage["duration_minutes"] > max_continuous_drive_minutes
                stop_minutes = 0
                stop_place: dict[str, Any] | None = None
                stop_type = "charging" if power_type == "electric" else "fueling"
                stage_services = service_pois.get(stage["id"], {})
                if needs_energy:
                    stop_place = _first_place(stage_services.get(stop_type))
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
                        stop_minutes = 30 if power_type == "electric" else 15
                        stage["waypoints"].append(stop_place)
                        current_percent = 80.0
                        projected = max(
                            reserve,
                            current_percent - used_percent / 2,
                        )
                        stage["energy_estimate"]["remaining_percent"] = round(projected, 1)
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
    return plans, _dedupe_warnings(warnings)


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
            owner_key = str(owner_day.get("date"))
            staged_by_date.setdefault(owner_key, []).append(stage)
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
            )
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
) -> dict[str, Any]:
    for category in ("overnight_hotel", "hotel", "rest"):
        place = _first_place(stage_services.get(category))
        if place and place.get("coordinates"):
            return place
    return {
        "id": f"route_overnight_{stage.get('id', 'stage')}_{number}",
        "name": f"沿途住宿点 {number}（需预订）",
        "address": "按实时导航在路线附近选择可入住酒店",
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
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not vehicle:
        issues.append(_issue("VEHICLE_DATA_MISSING", "warning", "车辆数据缺失，能耗只能降级估算"))
    for day in plans:
        for stage in day.get("stages", []):
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
                        f"{stage['title']} 当前天气数据暂不可用，已按基础风险继续规划",
                    )
                )
            start = datetime.fromisoformat(stage["planned_start"])
            end = datetime.fromisoformat(stage["planned_end"])
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
        meals = [item for item in day.get("activities", []) if item.get("type") == "meal"]
        if len(meals) < 3:
            issues.append(
                _issue(
                    "DAILY_MEALS_INCOMPLETE",
                    "blocker",
                    f"第 {day.get('day_index')} 天未完整安排早餐、午餐和晚餐",
                )
            )
    return _dedupe_issues(issues)


def _energy_use(stage: dict[str, Any], vehicle: dict[str, Any]) -> tuple[float, str, float]:
    distance = float(stage["distance_km"])
    # Route difficulty comes from the elevation skill, not from guessing from
    # Chinese place-name characters such as “山” or “峰”.
    elevation_gain = float(stage.get("elevation_gain_m") or 0)
    factor = 1.12 if elevation_gain >= ELEVATED_ROUTE_THRESHOLD_M else 1.0
    consumption = float(
        vehicle.get("consumption_per_100km")
        or (18 if vehicle.get("power_type") == "electric" else 7.5)
    )
    amount = distance * consumption / 100 * factor
    if vehicle.get("power_type") == "electric":
        capacity = float(vehicle.get("battery_kwh") or 75)
        return amount, "kWh", amount / capacity * 100
    rated_range = float(vehicle.get("rated_range_km") or 650)
    return amount, "L", distance / rated_range * 100 * factor


def _apply_weather_risk(
    stage: dict[str, Any],
    warnings: list[dict[str, Any]],
    tags: list[str],
) -> None:
    samples = stage.get("weather_samples", [])
    if not samples:
        warnings.append(_warning("WEATHER_DATA_DEGRADED", "当前天气数据暂不可用，已按基础风险继续规划", "warning", True))
        tags.append("天气数据不足")
        return
    sample = samples[0]
    precipitation = sample.get("precipitation_probability")
    visibility = sample.get("visibility_m")
    wind = sample.get("wind_speed_kmh")
    temperature = sample.get("temperature_c")
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
            remaining = [item for item in candidates if item[1].get("name") not in selected_names]
            selected.extend(
                _select_break_places(stage, remaining, part_count - 1 - len(selected))
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
            segment = deepcopy(stage["route_segments"][0])
            segment["coordinates"] = geometry[
                boundaries[index] : boundaries[index + 1] + 1
            ]
            segment["distance_km"] = round(distance, 2)
            segment["duration_minutes"] = duration
            piece["route_segments"] = [segment]
            if piece.get("energy_estimate"):
                piece["energy_estimate"]["amount"] = round(
                    float(piece["energy_estimate"]["amount"]) / part_count,
                    1,
                )
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
            if index < part_count - 1:
                kind, place = selected[index]
                break_minutes = 30 if kind == "charging" else 20
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
            "name": f"沿途计划休息点 {number}",
            "address": "按实时导航选择附近服务区或可安全停车区域",
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
                    "name": f"沿途计划休息点 {number}",
                    "address": "按实时导航选择附近服务区或可安全停车区域",
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


def _first_route_segment(stage: dict[str, Any]) -> dict[str, Any]:
    segments = stage.get("route_segments")
    if not isinstance(segments, list) or not segments or not isinstance(segments[0], dict):
        stage["route_segments"] = [{}]
        return stage["route_segments"][0]
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
    return {
        "id": f"route_estimated_{stop_type}_{stage.get('id', 'stage')}",
        "name": f"沿途估算{label}（需确认）",
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
    geometry = stage.get("route_segments", [{}])[0].get("coordinates", [])
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
