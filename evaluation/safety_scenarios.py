"""Reproducible safety/degradation scenarios using the production planner."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from app.planning.deep_drive import enrich_deep_drive_plan, verify_deep_drive_plan


SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_OUTPUT = Path(__file__).with_name("results") / "safety-scenarios-baseline.json"


def _place(name: str, longitude: float, **facts: Any) -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "coordinates": {"longitude": longitude, "latitude": 30.0},
        **facts,
    }


def _stage(*, distance_km: float, duration_minutes: int, weather: str) -> dict[str, Any]:
    start = datetime(2026, 9, 7, 8, 0, tzinfo=SHANGHAI)
    samples: list[dict[str, Any]] = []
    if weather == "normal":
        samples = [{
            "place": {"name": "终点"}, "sampled_at": start.isoformat(),
            "temperature_c": 24, "precipitation_probability": 10,
            "visibility_m": 10000, "wind_speed_kmh": 8,
        }]
    elif weather == "severe":
        samples = [{
            "place": {"name": "终点"}, "sampled_at": start.isoformat(),
            "temperature_c": 39, "precipitation_probability": 85,
            "visibility_m": 900, "wind_speed_kmh": 48,
        }]
    coordinates = [
        {"longitude": 114.3 + 1.7 * index / 20, "latitude": 30.0}
        for index in range(21)
    ]
    return {
        "id": "stage_safety",
        "day_id": "day_1",
        "sequence": 0,
        "title": "场景路线",
        "mode": "driving",
        "origin": _place("起点", 114.3),
        "destination": _place("终点", 116.0),
        "waypoints": [],
        "route_segments": [{
            "coordinates": coordinates,
            "distance_km": distance_km,
            "duration_minutes": duration_minutes,
        }],
        "planned_start": start.isoformat(),
        "planned_end": (start + timedelta(minutes=duration_minutes)).isoformat(),
        "distance_km": distance_km,
        "duration_minutes": duration_minutes,
        "elevation_gain_m": 0,
        "weather_samples": samples,
        "warnings": [],
    }


def _plan(stage: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "id": "day_1", "day_index": 1, "date": "2026-09-07", "title": "第 1 天",
        "stages": [stage], "activities": [], "items": [],
    }]


def _vehicle(current_soc: float = 80) -> dict[str, Any]:
    return {
        "power_type": "electric", "current_energy_percent": current_soc,
        "battery_kwh": 82, "consumption_per_100km": 18, "max_charge_kw": 120,
        "safe_energy_reserve_percent": 15, "mountain_ready": True, "height_m": 1.7,
    }


def _execute_case(case_id: str) -> dict[str, Any]:
    if case_id == "low-soc-long-distance":
        plans = _plan(_stage(distance_km=220, duration_minutes=180, weather="normal"))
        vehicle = _vehicle(45)
        services = {"stage_safety": {
            "charging": [_place("沿途快充站", 115.15, charging_power_kw=120)],
            "rest": [_place("驾驶休息区", 115.15)],
        }}
        expected_codes = {"ENERGY_STOP_SCHEDULED", "REST_STOP_SCHEDULED"}
        expected_route_executable = True
    elif case_id == "adverse-weather":
        plans = _plan(_stage(distance_km=40, duration_minutes=45, weather="severe"))
        vehicle = _vehicle()
        services = {}
        expected_codes = {"HEAVY_PRECIPITATION", "LOW_VISIBILITY", "HIGH_WIND", "EXTREME_TEMPERATURE"}
        expected_route_executable = True
    elif case_id == "energy-facilities-insufficient":
        plans = _plan(_stage(distance_km=300, duration_minutes=240, weather="normal"))
        vehicle = _vehicle(25)
        services = {}
        expected_codes = {"ENERGY_STOP_ESTIMATED", "CHARGING_POWER_ESTIMATED"}
        expected_route_executable = False
    elif case_id == "vehicle-information-missing":
        plans = _plan(_stage(distance_km=40, duration_minutes=45, weather="normal"))
        vehicle = {}
        services = {}
        expected_codes = {"VEHICLE_DATA_MISSING"}
        expected_route_executable = True
    elif case_id == "external-services-error":
        plans = _plan(_stage(distance_km=40, duration_minutes=45, weather="missing"))
        vehicle = _vehicle()
        services = {}
        expected_codes = {"WEATHER_DATA_DEGRADED", "WEATHER_DEGRADED"}
        expected_route_executable = True
    else:
        raise KeyError(case_id)

    started_at = monotonic()
    enriched, _ = enrich_deep_drive_plan(deepcopy(plans), vehicle, services, 120)
    issues = verify_deep_drive_plan(enriched, vehicle, 120)
    warning_codes = {
        warning.get("code")
        for day in enriched
        for stage in day.get("stages", [])
        for warning in stage.get("warnings", [])
    }
    issue_codes = {issue.get("code") for issue in issues}
    observed_codes = warning_codes | issue_codes
    blockers = [issue for issue in issues if issue.get("severity") == "blocker"]
    provisional_energy = "ENERGY_STOP_ESTIMATED" in observed_codes
    route_executable = not blockers and not provisional_energy
    estimates = [
        stage.get("energy_estimate") or {}
        for day in enriched
        for stage in day.get("stages", [])
        if stage.get("mode") == "driving"
    ]
    soc_continuous = all(
        estimates[index].get("remaining_percent") == estimates[index + 1].get("starting_percent")
        for index in range(len(estimates) - 1)
    )
    passed = (
        expected_codes <= observed_codes
        and route_executable == expected_route_executable
        and not blockers
        and soc_continuous
    )
    return {
        "id": case_id,
        "passed": passed,
        "expected_codes": sorted(expected_codes),
        "observed_codes": sorted(str(code) for code in observed_codes if code),
        "route_executable": route_executable,
        "route_executability_basis": (
            "verified" if route_executable else "补能设施仅有估算位置，需真实服务确认"
        ),
        "soc_continuous": soc_continuous,
        "blocker_codes": [item.get("code") for item in blockers],
        "latency_ms": round((monotonic() - started_at) * 1000, 3),
    }


def evaluate_all() -> dict[str, Any]:
    case_ids = [
        "low-soc-long-distance",
        "adverse-weather",
        "energy-facilities-insufficient",
        "vehicle-information-missing",
        "external-services-error",
    ]
    cases = [_execute_case(case_id) for case_id in case_ids]
    return {
        "dataset_id": "roadman-safety-scenarios-v1",
        "sample_count": len(cases),
        "task_completion_rate": round(sum(item["passed"] for item in cases) / len(cases), 4),
        "route_executability_rate": round(sum(item["route_executable"] for item in cases) / len(cases), 4),
        "average_latency_ms": round(mean(item["latency_ms"] for item in cases), 3),
        "passed": all(item["passed"] for item in cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate_all()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[safety-eval] {sum(item['passed'] for item in result['cases'])}/{result['sample_count']} passed; "
        f"route_executability={result['route_executability_rate']:.0%}"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
