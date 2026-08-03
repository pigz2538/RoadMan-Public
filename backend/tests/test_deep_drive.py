from datetime import datetime
from zoneinfo import ZoneInfo

from app.planning.deep_drive import enrich_deep_drive_plan, verify_deep_drive_plan


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _stage() -> dict:
    return {
        "id": "stage_long",
        "day_id": "day_1",
        "sequence": 0,
        "title": "山路长途驾驶",
        "mode": "driving",
        "origin": {"name": "武汉", "coordinates": {"longitude": 114.3, "latitude": 30.5}},
        "destination": {"name": "庐山", "coordinates": {"longitude": 116.0, "latitude": 29.5}},
        "waypoints": [],
        "route_segments": [
            {
                "coordinates": [
                    {"longitude": 114.3, "latitude": 30.5},
                    {"longitude": 116.0, "latitude": 29.5},
                ],
                "distance_km": 300,
                "duration_minutes": 240,
            }
        ],
        "planned_start": datetime(2026, 8, 1, 9, 0, tzinfo=SHANGHAI).isoformat(),
        "planned_end": datetime(2026, 8, 1, 13, 0, tzinfo=SHANGHAI).isoformat(),
        "distance_km": 300,
        "duration_minutes": 240,
        "elevation_gain_m": 420,
        "weather_samples": [
            {
                "place": {"name": "庐山"},
                "sampled_at": datetime(2026, 8, 1, 13, 0, tzinfo=SHANGHAI).isoformat(),
                "temperature_c": 39,
                "precipitation_probability": 80,
                "visibility_m": 1200,
                "wind_speed_kmh": 45,
            }
        ],
        "warnings": [],
    }


def _place(name: str, longitude: float) -> dict:
    return {
        "id": name,
        "name": name,
        "coordinates": {"longitude": longitude, "latitude": 30.0},
    }


def _vehicle() -> dict:
    return {
        "power_type": "electric",
        "current_energy_percent": 25,
        "battery_kwh": 82,
        "consumption_per_100km": 18,
        "safe_energy_reserve_percent": 15,
        "height_m": 2.3,
        "mountain_ready": False,
    }


def test_vehicle_weather_and_schedule_agents_insert_required_stops_and_risks():
    plans = [
        {
            "id": "day_1",
            "day_index": 1,
            "date": "2026-08-01",
            "title": "第 1 天",
            "stages": [_stage()],
            "activities": [],
            "items": [],
        }
    ]
    services = {
        "stage_long": {
            "charging": [_place("高速充电站", 115.1)],
            "rest": [_place("服务区", 115.0)],
            "meal": [_place("服务区餐厅", 115.2)],
        }
    }

    enriched, warnings = enrich_deep_drive_plan(plans, _vehicle(), services, 120)
    driving_stages = enriched[0]["stages"]
    stage = driving_stages[0]

    assert stage["energy_estimate"]["unit"] == "kWh"
    assert stage["energy_estimate"]["estimated"] is True
    assert len(driving_stages) >= 2
    assert all(item["duration_minutes"] <= 120 for item in driving_stages)
    assert {"charging", "meal"} <= {
        item["type"] for item in enriched[0]["activities"]
    }
    assert {"补能", "连续驾驶", "强降水", "低能见度", "大风", "极端温度", "限高"} <= set(
        stage["risk_tags"]
    )
    assert stage["risk_level"] == "high"
    assert any(item["code"] == "ENERGY_STOP_SCHEDULED" for item in warnings)
    assert verify_deep_drive_plan(enriched, _vehicle(), 120) == []


def test_verification_blocks_when_required_energy_stop_is_unavailable():
    plans = [
        {
            "id": "day_1",
            "day_index": 1,
            "date": "2026-08-01",
            "title": "第 1 天",
            "stages": [_stage()],
            "activities": [],
            "items": [],
        }
    ]

    enriched, _ = enrich_deep_drive_plan(plans, _vehicle(), {}, 120)
    issues = verify_deep_drive_plan(enriched, _vehicle(), 120)

    assert any(item["code"] == "ENERGY_UNSAFE" and item["severity"] == "blocker" for item in issues)
    assert not any(item["code"] == "CONTINUOUS_DRIVE" for item in issues)
    assert any(item["type"] == "rest" for item in enriched[0]["activities"])


def test_noncritical_service_and_weather_failures_degrade_without_blocking():
    stage = _stage()
    stage["distance_km"] = 20
    stage["duration_minutes"] = 30
    stage["planned_end"] = datetime(2026, 8, 1, 9, 30, tzinfo=SHANGHAI).isoformat()
    stage["weather_samples"] = []
    plans = [
        {
            "id": "day_1",
            "day_index": 1,
            "date": "2026-08-01",
            "title": "第 1 天",
            "stages": [stage],
            "activities": [],
            "items": [],
        }
    ]
    safe_vehicle = {
        **_vehicle(),
        "current_energy_percent": 100,
        "height_m": 1.7,
        "mountain_ready": True,
    }

    enriched, _ = enrich_deep_drive_plan(plans, safe_vehicle, {}, 120)
    issues = verify_deep_drive_plan(enriched, safe_vehicle, 120)

    assert enriched[0]["stages"][0]["risk_level"] == "moderate"
    assert any(item["code"] == "WEATHER_DATA_DEGRADED" for item in enriched[0]["stages"][0]["warnings"])
    assert issues and all(item["severity"] == "warning" for item in issues)


def test_long_drive_is_split_into_rest_segments_and_day_has_three_meals():
    stage = _stage()
    stage["duration_minutes"] = 360
    stage["planned_end"] = datetime(2026, 8, 1, 15, 0, tzinfo=SHANGHAI).isoformat()
    stage["route_segments"][0]["duration_minutes"] = 360
    stage["route_segments"][0]["coordinates"] = [
        {"longitude": 114.3 + index * 0.3, "latitude": 30.5 - index * 0.15}
        for index in range(7)
    ]
    plans = [
        {
            "id": "day_1",
            "day_index": 1,
            "date": "2026-08-01",
            "title": "第 1 天",
            "stages": [stage],
            "activities": [],
            "items": [],
        }
    ]
    services = {
        "stage_long": {
            "charging": [_place("中途充电站", 114.9)],
            "rest": [_place("第一服务区", 114.6), _place("第二服务区", 115.5)],
            "meal": [_place("服务区餐厅", 115.2)],
        }
    }

    enriched, _ = enrich_deep_drive_plan(plans, _vehicle(), services, 120)
    stages = enriched[0]["stages"]
    activities = enriched[0]["activities"]

    assert len(stages) == 3
    assert all(item["duration_minutes"] <= 120 for item in stages)
    assert all(
        stages[index]["destination"]["name"] == stages[index + 1]["origin"]["name"]
        for index in range(len(stages) - 1)
    )
    assert len([item for item in activities if item["type"] == "meal"]) == 3
    assert len(
        [item for item in activities if item["type"] in {"rest", "charging", "fueling"}]
    ) >= 2
    stop_keys = [
        (item["type"], item["place"]["name"])
        for item in activities
        if item["type"] in {"rest", "charging", "fueling"}
    ]
    assert len(stop_keys) == len(set(stop_keys))
    assert not any(
        item["severity"] == "blocker"
        for item in verify_deep_drive_plan(enriched, _vehicle(), 120)
    )


def test_unreasonable_walking_stage_is_blocked():
    stage = _stage()
    stage["mode"] = "walking"
    stage["duration_minutes"] = 720
    stage["distance_km"] = 55
    stage["planned_end"] = datetime(2026, 8, 1, 21, 0, tzinfo=SHANGHAI).isoformat()
    plans = [
        {
            "id": "day_1",
            "day_index": 1,
            "date": "2026-08-01",
            "title": "第 1 天",
            "stages": [stage],
            "activities": [
                {
                    "type": "meal",
                    "planned_start": datetime(2026, 8, 1, hour, 0, tzinfo=SHANGHAI).isoformat(),
                }
                for hour in (8, 12, 18)
            ],
        }
    ]

    issues = verify_deep_drive_plan(plans, _vehicle(), 120)

    assert any(item["code"] == "WALKING_STAGE_TOO_LONG" for item in issues)
