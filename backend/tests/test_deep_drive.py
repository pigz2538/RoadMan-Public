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
    estimates = [item["energy_estimate"] for item in driving_stages]
    assert all(
        estimates[index]["remaining_percent"]
        == estimates[index + 1]["starting_percent"]
        for index in range(len(estimates) - 1)
    )
    assert sum(item["replenished_amount"] is not None for item in estimates) == 1
    assert verify_deep_drive_plan(enriched, _vehicle(), 120) == []


def test_measured_charge_energy_updates_soc_without_fixed_reset():
    stage = _stage()
    stage["distance_km"] = 100
    stage["duration_minutes"] = 60
    stage["elevation_gain_m"] = 0
    stage["planned_end"] = datetime(2026, 8, 1, 10, 0, tzinfo=SHANGHAI).isoformat()
    stage["route_segments"][0].update(distance_km=100, duration_minutes=60)
    plans = [{"id": "day_1", "day_index": 1, "date": "2026-08-01", "title": "第 1 天", "stages": [stage], "activities": [], "items": []}]
    charger = {
        **_place("实测补能站", 115.15),
        "actual_energy_added_kwh": 16,
        "charging_power_kw": 80,
        "charging_minutes": 15,
    }
    vehicle = {
        **_vehicle(),
        "current_energy_percent": 60,
        "battery_kwh": 80,
        "consumption_per_100km": 20,
        "safe_energy_reserve_percent": 50,
        "max_charge_kw": 120,
    }

    enriched, _ = enrich_deep_drive_plan(plans, vehicle, {"stage_long": {"charging": [charger]}}, 120)
    estimate = enriched[0]["stages"][0]["energy_estimate"]

    assert estimate["calculation_basis"] == "measured_energy"
    assert estimate["estimated"] is False
    assert estimate["replenished_amount"] == 16.0
    assert estimate["after_replenishment_percent"] != 80.0
    assert estimate["remaining_percent"] == 55.0


def test_charger_power_and_duration_drive_replenishment_estimate():
    stage = _stage()
    stage["distance_km"] = 100
    stage["duration_minutes"] = 60
    stage["elevation_gain_m"] = 0
    stage["planned_end"] = datetime(2026, 8, 1, 10, 0, tzinfo=SHANGHAI).isoformat()
    stage["route_segments"][0].update(distance_km=100, duration_minutes=60)
    plans = [{"id": "day_1", "day_index": 1, "date": "2026-08-01", "title": "第 1 天", "stages": [stage], "activities": [], "items": []}]
    charger = {
        **_place("50kW 充电站", 115.15),
        "charging_power_kw": 50,
        "planned_charge_minutes": 30,
    }
    vehicle = {
        **_vehicle(),
        "current_energy_percent": 50,
        "battery_kwh": 80,
        "consumption_per_100km": 20,
        "safe_energy_reserve_percent": 45,
        "max_charge_kw": 120,
    }

    enriched, _ = enrich_deep_drive_plan(plans, vehicle, {"stage_long": {"charging": [charger]}}, 120)
    estimate = enriched[0]["stages"][0]["energy_estimate"]

    assert estimate["calculation_basis"] == "charger_power"
    assert estimate["charging_power_kw"] == 50.0
    assert estimate["replenishment_minutes"] == 30
    assert estimate["replenished_amount"] == 22.5
    assert estimate["remaining_percent"] == 53.1


def test_missing_energy_provider_degrades_to_an_estimated_route_stop():
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

    assert not any(item["code"] == "ENERGY_UNSAFE" for item in issues)
    assert not any(item["code"] == "CONTINUOUS_DRIVE" for item in issues)
    assert any(item["type"] == "charging" for item in enriched[0]["activities"])
    assert any(
        item["type"] in {"rest", "charging", "fueling"}
        for item in enriched[0]["activities"]
    )
    assert any(
        item["code"] == "ENERGY_STOP_ESTIMATED"
        for item in enriched[0]["stages"][0]["warnings"]
    )


def test_repair_clears_stale_energy_unavailable_after_route_stop_is_available():
    """A failed repair must not keep blocking after the fallback is inserted."""
    stage = _stage()
    stage["id"] = "城市出发（跨天第1段） · 第 1/5 段"
    stage["title"] = stage["id"]
    stage["warnings"] = [
        {
            "code": "ENERGY_STOP_UNAVAILABLE",
            "message": "预计能量不足且沿途补能点查询失败，请人工确认",
            "severity": "error",
            "estimated": True,
        }
    ]
    plans = [
        {
            "id": "day_1",
            "day_index": 1,
            "date": "2026-08-24",
            "title": "第 1 天",
            "stages": [stage],
            "activities": [],
            "items": [],
        }
    ]

    enriched, _ = enrich_deep_drive_plan(plans, _vehicle(), {}, 120)
    stage_after_repair = enriched[0]["stages"][0]
    warning_codes = {item["code"] for item in stage_after_repair["warnings"]}
    issues = verify_deep_drive_plan(enriched, _vehicle(), 120)

    assert "ENERGY_STOP_UNAVAILABLE" not in warning_codes
    assert "ENERGY_STOP_ESTIMATED" in warning_codes
    assert not any(item["code"] == "ENERGY_UNSAFE" for item in issues)
    assert any(item["type"] == "charging" for item in enriched[0]["activities"])


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
    assert any("当前天气数据暂不可用，已按基础风险继续规划" in item["description"] for item in issues)


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


def test_tied_service_points_do_not_crash_long_drive_split():
    """Several POIs can snap to the same route vertex; keep splitting stable."""
    stage = _stage()
    stage["duration_minutes"] = 360
    stage["planned_end"] = datetime(2026, 8, 1, 15, 0, tzinfo=SHANGHAI).isoformat()
    stage["route_segments"][0]["duration_minutes"] = 360
    stage["route_segments"][0]["coordinates"] = [
        {"longitude": 114.3 + index * 0.3, "latitude": 30.5 - index * 0.15}
        for index in range(7)
    ]
    tied_rest = [
        _place("服务区 A", 115.2),
        _place("服务区 B", 115.2),
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
    safe_vehicle = {**_vehicle(), "current_energy_percent": 100}

    enriched, _ = enrich_deep_drive_plan(
        plans,
        safe_vehicle,
        {"stage_long": {"rest": tied_rest}},
        120,
    )

    assert len(enriched[0]["stages"]) == 3


def test_long_city_departure_recovers_from_missing_geometry_and_inserts_rest_points():
    """A sparse provider polyline must not leave an oversized city departure."""
    stage = _stage()
    stage["duration_minutes"] = 360
    stage["planned_end"] = datetime(2026, 8, 1, 15, 0, tzinfo=SHANGHAI).isoformat()
    stage["route_segments"][0]["duration_minutes"] = 360
    stage["route_segments"][0]["coordinates"] = []
    vehicle = {
        **_vehicle(),
        "height_m": 1.7,
        "mountain_ready": True,
    }
    plans = [
        {
            "id": "day_1",
            "day_index": 1,
            "date": "2026-08-01",
            "title": "第一天",
            "stages": [stage],
            "activities": [],
            "items": [],
        }
    ]

    enriched, _ = enrich_deep_drive_plan(plans, vehicle, {}, 120)
    stages = enriched[0]["stages"]

    assert len(stages) == 3
    assert all(item["duration_minutes"] <= 120 for item in stages)
    assert len(
        [
            item
            for item in enriched[0]["activities"]
            if item["type"] in {"rest", "charging", "fueling"}
        ]
    ) >= 2
    assert not any(
        item["code"] == "CONTINUOUS_DRIVE"
        for item in verify_deep_drive_plan(enriched, vehicle, 120)
    )


def test_cross_day_city_departure_with_sparse_geometry_is_split_before_verification():
    """The Wuhan→Xinjiang-shaped long leg is safe even without provider points."""
    stage = _stage()
    stage["duration_minutes"] = 1800
    stage["planned_start"] = datetime(2026, 8, 1, 8, 0, tzinfo=SHANGHAI).isoformat()
    stage["planned_end"] = datetime(2026, 8, 2, 14, 0, tzinfo=SHANGHAI).isoformat()
    stage["route_segments"][0]["duration_minutes"] = 1800
    stage["route_segments"][0]["coordinates"] = []
    vehicle = {
        **_vehicle(),
        "height_m": 1.7,
        "mountain_ready": True,
    }
    plans = [
        {
            "id": f"day_{index}",
            "day_index": index,
            "date": f"2026-08-{index:02d}",
            "title": f"第 {index} 天",
            "stages": [stage] if index == 1 else [],
            "activities": [],
            "items": [],
        }
        for index in range(1, 7)
    ]

    enriched, _ = enrich_deep_drive_plan(plans, vehicle, {}, 120)
    stages = [item for day in enriched for item in day["stages"]]

    assert len(stages) >= 8
    assert all(item["duration_minutes"] <= 120 for item in stages)
    assert any(item["day_id"] == "day_2" for item in stages)
    assert not any(
        item["code"] == "CONTINUOUS_DRIVE"
        for item in verify_deep_drive_plan(enriched, vehicle, 120)
    )


def test_cross_day_drive_is_distributed_to_calendar_days_with_overnight_hotel():
    """A 18-hour intercity drive cannot remain on day one as one giant leg."""
    stage = _stage()
    stage["duration_minutes"] = 1080
    stage["planned_start"] = datetime(2026, 8, 1, 8, 0, tzinfo=SHANGHAI).isoformat()
    stage["planned_end"] = datetime(2026, 8, 2, 2, 0, tzinfo=SHANGHAI).isoformat()
    stage["route_segments"][0]["duration_minutes"] = 1080
    stage["route_segments"][0]["coordinates"] = [
        {"longitude": 114.3 + index * 0.25, "latitude": 30.5 - index * 0.1}
        for index in range(9)
    ]
    plans = [
        {
            "id": "day_1",
            "day_index": 1,
            "date": "2026-08-01",
            "title": "第一天",
            "stages": [stage],
            "activities": [],
            "items": [],
        },
        {
            "id": "day_2",
            "day_index": 2,
            "date": "2026-08-02",
            "title": "第二天",
            "stages": [],
            "activities": [],
            "items": [],
        },
    ]
    services = {
        "stage_long": {
            "overnight_hotel": [_place("沿途舒适酒店", 115.4)],
            "charging": [_place("沿途充电站", 115.0)],
            "rest": [_place("沿途服务区", 115.2)],
            "meal": [_place("沿途服务区餐厅", 115.2)],
        }
    }
    safe_vehicle = {**_vehicle(), "current_energy_percent": 100, "height_m": 1.7, "mountain_ready": True}

    enriched, _ = enrich_deep_drive_plan(plans, safe_vehicle, services, 120)
    stages = [stage for day in enriched for stage in day["stages"]]
    overnight = [
        activity
        for day in enriched
        for activity in day["activities"]
        if activity["type"] == "hotel"
    ]

    assert len(stages) >= 10  # calendar split plus 120-minute safety segments
    assert all(
        datetime.fromisoformat(item["planned_start"]).date()
        == datetime.fromisoformat(item["planned_end"]).date()
        for item in stages
    )
    assert any(item["day_id"] == "day_2" for item in stages)
    assert overnight
    assert any("过夜" in item["user_note"] for item in overnight)
    assert all(item["duration_minutes"] <= 120 for item in stages)


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
