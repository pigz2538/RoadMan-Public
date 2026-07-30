from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from typing import Any

from ..domain.models import Activity, DayItemRef, EnergyEstimate, PlaceRef, PlanWarning

MOUNTAIN_WORDS = ("山", "岭", "坡", "峡", "峰")


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current_percent = float(vehicle.get("current_energy_percent") or 80)
    reserve = float(vehicle.get("safe_energy_reserve_percent") or 15)
    power_type = vehicle.get("power_type", "electric")
    warnings: list[dict[str, Any]] = []

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
                    if stop_place:
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
                                "ENERGY_STOP_SCHEDULED",
                                f"预计低于 {reserve:.0f}% 安全余量，已插入"
                                f"{'充电' if power_type == 'electric' else '加油'}点",
                                "warning",
                                True,
                            )
                        )
                        risk_tags.append("补能")
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

        day["activities"] = activities
        stage_refs = [DayItemRef(type="stage", id=item["id"]).model_dump() for item in day["stages"]]
        activity_refs = [
            DayItemRef(type="activity", id=item["id"]).model_dump() for item in activities
        ]
        day["items"] = [*stage_refs, *activity_refs]
    return plans, _dedupe_warnings(warnings)


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
            codes = {item["code"] for item in stage.get("warnings", [])}
            if "ENERGY_STOP_UNAVAILABLE" in codes:
                issues.append(_issue("ENERGY_UNSAFE", "blocker", f"{stage['title']} 未找到必要补能点"))
            if (
                stage["mode"] == "driving"
                and stage["duration_minutes"] > max_continuous_drive_minutes
                and "REST_STOP_SCHEDULED" not in codes
            ):
                issues.append(_issue("CONTINUOUS_DRIVE", "blocker", f"{stage['title']} 缺少驾驶休息"))
            if not stage.get("weather_samples"):
                issues.append(_issue("WEATHER_DEGRADED", "warning", f"{stage['title']} 无逐时天气"))
    return _dedupe_issues(issues)


def _energy_use(stage: dict[str, Any], vehicle: dict[str, Any]) -> tuple[float, str, float]:
    distance = float(stage["distance_km"])
    mountain = any(
        word in f"{stage['origin']['name']}{stage['destination']['name']}"
        for word in MOUNTAIN_WORDS
    )
    factor = 1.12 if mountain else 1.0
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
        warnings.append(_warning("WEATHER_DATA_DEGRADED", "逐时天气不可用，临近出发需复核", "warning", True))
        tags.append("天气待复核")
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
    mountain = any(
        word in f"{stage['origin']['name']}{stage['destination']['name']}"
        for word in MOUNTAIN_WORDS
    )
    if mountain and not vehicle.get("mountain_ready", True):
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
