from datetime import date, datetime, time, timedelta
from typing import Any

import pytest

from app.core.config import Settings
from app.api.trips import get_trip_risks, get_trip_services
from app.db import SessionLocal, create_tables
from app.domain.models import SkillResult, TripCreate, TripRequest, VehicleProfile
from app.planning.graph import _ensure_coordinates, _movement_stage, build_planning_graph
from app.planning.deep_drive import _ensure_daily_meals
from app.planning.llm import (
    OllamaRequirementExtractor,
    deterministic_extract,
    extract_explicit_location_constraints,
    extract_structural_constraints,
)
from app.planning.runner import run_planning
from app.repositories import TripRepository, VehicleRepository
from app.services.sse import sse_manager
from app.skills.base import SkillAdapter, SkillContext
from app.skills.registry import SkillRegistry


def test_offline_fallback_preserves_only_literal_calendar_constraints():
    extracted = deterministic_extract(
        "2026-08-02从武汉出发，2026-08-01返回",
        date(2026, 8, 3),
    )
    assert extracted == {
        "start_date": "2026-08-02",
        "end_date": "2026-08-01",
    }


def test_daily_meals_follow_a_late_departure_and_avoid_stage_overlap():
    day = {
        "id": "day_late",
        "date": "2026-08-11",
        "stages": [
            {
                "id": "stage_late",
                "planned_start": "2026-08-11T12:00:00+08:00",
                "planned_end": "2026-08-11T12:30:00+08:00",
                "origin": {"name": "南浔站"},
                "destination": {"name": "乌镇"},
            }
        ],
    }

    activities = _ensure_daily_meals(day, [], {})
    meals = {item["user_note"]: item for item in activities}

    assert len(meals) == 3
    assert meals["每日早餐安排"]["planned_start"].startswith("2026-08-11T09:00")
    assert meals["每日午餐安排"]["planned_start"].startswith("2026-08-11T13:00")


def test_requirement_agent_owns_relationship_and_destination_semantics():
    extracted = deterministic_extract(
        "情侣出游，从湖州南浔到乌镇及其周边，玩两天",
        date(2026, 8, 1),
    )
    assert extracted == {}


def test_deterministic_extract_reads_iso_dates_adjacent_to_chinese_text():
    extracted = deterministic_extract(
        "2026-08-02从上海出发，2026-08-01返回",
        date(2026, 8, 3),
    )

    assert extracted["start_date"] == "2026-08-02"
    assert extracted["end_date"] == "2026-08-01"


def test_offline_fallback_does_not_interpret_weekday_or_destination_language():
    extracted = deterministic_extract(
        "周一从武汉出发，周五返回武汉",
        date(2026, 8, 3),
    )
    assert extracted == {}


def test_structural_calendar_resolves_weekday_range_from_today():
    extracted = extract_structural_constraints(
        "周一早上从武汉出发，去九宫山，周五晚上八点回来，喜欢自然景观",
        date(2026, 8, 3),
    )

    assert extracted == {
        "start_date": "2026-08-03",
        "end_date": "2026-08-07",
    }


def test_explicit_location_fallback_reads_travel_grammar_without_place_keywords():
    extracted = extract_explicit_location_constraints(
        "周一早上从武汉出发，去九宫山看流星雨，周五晚上回来"
    )
    assert extracted == {"origin_name": "武汉", "destination_name": "九宫山"}
    assert extract_explicit_location_constraints(
        "从湖州南浔站出发，在乌镇及其周边转转"
    ) == {"origin_name": "湖州南浔站", "destination_name": "乌镇"}
    assert extract_explicit_location_constraints(
        "from Wuhan to Jiugongshan for stargazing"
    ) == {"origin_name": "Wuhan", "destination_name": "Jiugongshan"}


def test_structural_calendar_supports_relative_weekend_and_english_dates():
    today = date(2026, 8, 3)
    assert extract_structural_constraints("后天出发，前天回来", today) == {
        "start_date": "2026-08-05",
        "end_date": "2026-08-01",
    }
    assert extract_structural_constraints("this weekend出发", today) == {
        "start_date": "2026-08-08",
        "end_date": "2026-08-09",
    }
    assert extract_structural_constraints("周末周日回来", today) == {
        "start_date": "2026-08-08",
        "end_date": "2026-08-09",
    }
    assert extract_structural_constraints("next Sunday出发，Monday回来", today) == {
        "start_date": "2026-08-16",
        "end_date": "2026-08-17",
    }


@pytest.mark.asyncio
async def test_requirement_agent_decides_semantic_party_size(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": (
                    '{"origin_name":"湖州南浔","destination_name":"乌镇",'
                    '"travelers":2,"preferences":["目的地周边"]}'
                )
            }

    class FakeClient:
        prompt = ""

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, _url, **kwargs):
            FakeClient.prompt = kwargs["json"]["prompt"]
            return FakeResponse()

    monkeypatch.setattr("app.planning.llm.httpx.AsyncClient", FakeClient)
    extractor = OllamaRequirementExtractor(
        Settings(ollama_api_key="test-key", enable_llm_requirement_extraction=True)
    )

    extracted = await extractor.extract(
        "情侣出游，从湖州南浔到乌镇及其周边，玩两天",
        date(2026, 8, 1),
    )

    assert extracted["travelers"] == 2
    assert extracted["destination_name"] == "乌镇"
    assert "start_date" not in extracted
    assert "end_date" not in extracted
    assert "根据语义判断同行人数" in FakeClient.prompt


@pytest.mark.asyncio
async def test_requirement_agent_preserves_explicit_party_size(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"travelers":2}'}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.planning.llm.httpx.AsyncClient", FakeClient)
    extractor = OllamaRequirementExtractor(Settings(ollama_api_key="test-key"))

    extracted = await extractor.extract(
        "情侣出游，从武汉到庐山，同行 4 人",
        date(2026, 8, 1),
    )

    # The structured Agent response is authoritative; raw text is not scanned
    # for a numeric keyword override.
    assert extracted["travelers"] == 2


def test_non_driving_stage_exposes_total_elevation_gain():
    stage = _movement_stage(
        day_id="day_1",
        sequence=0,
        title="骑行游览接驳",
        origin={"name": "起点", "coordinates": {"longitude": 120.4, "latitude": 30.8}},
        destination={"name": "终点", "coordinates": {"longitude": 120.5, "latitude": 30.7}},
        route={
            "data": {
                "selected_mode": "riding",
                "distance_km": 8.2,
                "duration_minutes": 35,
                "geometry": [
                    {"longitude": 120.4, "latitude": 30.8},
                    {"longitude": 120.5, "latitude": 30.7},
                ],
                "elevation_gain_m": 186,
            },
            "sources": [],
        },
        start_at=datetime(2026, 8, 1, 9, 0),
    )

    assert stage.elevation_gain_m == 186
    assert stage.traffic_summary == "路线起伏：总爬升约 186 m"


@pytest.mark.asyncio
async def test_ambiguous_destination_is_corrected_by_nearby_poi():
    class AmbiguousRegistry:
        async def execute(self, name, payload, _):
            if name == "amap.geocode":
                return SkillResult(
                    success=True,
                    provider="fake-amap",
                    data={
                        "formatted_address": "陕西省榆林市佳县乌镇",
                        "location": "110.364601,37.936564",
                        "city": "榆林市",
                    },
                )
            return SkillResult(
                success=True,
                provider="fake-amap",
                data={
                    "items": [
                        {
                            "name": "乌镇风景区",
                            "location": "120.486173,30.748979",
                            "address": "石佛南路18号",
                            "city": "嘉兴市",
                        }
                    ]
                },
            )

    origin = await _ensure_coordinates(AmbiguousRegistry(), {"name": "湖州南浔"}, "trip")
    destination = await _ensure_coordinates(
        AmbiguousRegistry(), {"name": "乌镇"}, "trip", nearby=origin | {"coordinates": {"longitude": 120.418244, "latitude": 30.850835}}
    )

    assert destination["name"] == "乌镇风景区"
    assert destination["coordinates"]["longitude"] == 120.486173


class FakeGeocodeAdapter(SkillAdapter):
    name = "amap.geocode"

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        is_wuhan = "武汉" in payload["address"]
        location = "114.365248,30.537860" if is_wuhan else "115.983503,29.555963"
        return SkillResult(
            success=True,
            provider="fake-amap",
            data={
                "formatted_address": payload["address"],
                "location": location,
                "city": "武汉" if is_wuhan else "九江",
            },
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready"}


class FakeRouteAdapter(SkillAdapter):
    name = "amap.route"

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        mode = payload.get("preferred_mode", "driving")
        local = (
            abs(payload["origin"]["longitude"] - payload["destination"]["longitude"]) < 0.1
            and abs(payload["origin"]["latitude"] - payload["destination"]["latitude"]) < 0.1
        )
        distance = 1.8 if local else 254.2
        duration = 210 if mode == "driving" and not local else 25
        return SkillResult(
            success=True,
            provider="fake-amap",
            data={
                "requested_mode": mode,
                "selected_mode": mode,
                "fallback_used": False,
                "distance_km": distance,
                "duration_minutes": duration,
                "tolls_cny": 95 if mode == "driving" else 0,
                "geometry": [
                    {
                        "longitude": payload["origin"]["longitude"],
                        "latitude": payload["origin"]["latitude"],
                    },
                    {
                        "longitude": payload["destination"]["longitude"],
                        "latitude": payload["destination"]["latitude"],
                    },
                ],
                "steps": [],
                "transfers": [],
                "traffic_summary": "高德当前路况整体畅通" if mode == "driving" else None,
            },
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready"}


class FakePoiAdapter(SkillAdapter):
    name = "amap.poi"

    async def execute(self, _: dict[str, Any], __: SkillContext) -> SkillResult:
        return SkillResult(
            success=True,
            provider="fake-amap",
            data={
                "count": 4,
                "items": [
                    {"id": f"poi_{index}", "name": f"景点 {index}", "location": location, "city": "九江"}
                    for index, location in enumerate(
                        [
                            "115.970000,29.560000",
                            "115.960000,29.570000",
                            "115.950000,29.580000",
                            "115.940000,29.590000",
                        ],
                        start=1,
                    )
                ],
            },
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready"}


class FakeFlyAIPoiAdapter(SkillAdapter):
    name = "flyai.poi"

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        is_meal = payload.get("keyword") == "餐厅"
        name = "FlyAI 餐厅" if is_meal else "FlyAI 景点"
        return SkillResult(
            success=True,
            provider="fake-flyai",
            data={
                "items": [
                    {
                        "id": name,
                        "name": name,
                        "address": "九江测试地址",
                        "longitude": 115.93,
                        "latitude": 29.60,
                        "detail_url": "https://example.test/flyai",
                        "image_url": "https://example.test/flyai.jpg",
                    }
                ]
            },
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready"}


class FakeFlyAIHotelAdapter(SkillAdapter):
    name = "flyai.hotel"

    async def execute(self, _: dict[str, Any], __: SkillContext) -> SkillResult:
        return SkillResult(
            success=True,
            provider="fake-flyai",
            data={
                "items": [
                    {
                        "id": "flyai-hotel",
                        "name": "FlyAI 测试民宿",
                        "address": "九江测试地址",
                        "location": "115.93,29.60",
                        "longitude": 115.93,
                        "latitude": 29.60,
                        "price_min_cny": 320,
                        "price_max_cny": 420,
                        "price_estimated": False,
                        "detail_url": "https://example.test/flyai-hotel",
                    }
                ]
            },
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready"}


class FakeWeatherAdapter(SkillAdapter):
    name = "open_meteo.forecast"

    async def execute(self, _: dict[str, Any], __: SkillContext) -> SkillResult:
        samples = []
        for day_offset in range(16):
            for hour in range(24):
                sampled = datetime.combine(
                    date.today() + timedelta(days=day_offset),
                    time(hour, 0),
                )
                samples.append(
                    {
                        "sampled_at": sampled.isoformat(timespec="minutes"),
                        "temperature_c": 26,
                        "precipitation_probability": 20,
                    }
                )
        return SkillResult(
            success=True,
            provider="fake-weather",
            data={"hourly_samples": samples},
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready"}


def fake_registry(*, with_flyai: bool = False) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(FakeGeocodeAdapter())
    registry.register(FakeRouteAdapter())
    registry.register(FakePoiAdapter())
    registry.register(FakeWeatherAdapter())
    if with_flyai:
        registry.register(FakeFlyAIPoiAdapter())
        registry.register(FakeFlyAIHotelAdapter())
    return registry


@pytest.mark.asyncio
async def test_graph_builds_two_day_markdown_plan():
    graph = build_planning_graph(
        fake_registry(),
        Settings(
            load_local_skill_credentials=False,
            enable_llm_requirement_extraction=False,
        ),
    )
    result = await graph.ainvoke(
        {
            "trip_id": "trip_graph",
            "raw_input": "周六从武汉去庐山，两天一夜",
            "trip_request": {
                "raw_text": "周六从武汉去庐山，两天一夜",
                "origin": {"name": "武汉"},
                "destination": {"name": "庐山"},
                "start_date": "2026-08-08",
                "end_date": "2026-08-09",
                "max_days": 2,
            },
            "clarification_round": 0,
        }
    )
    assert result["missing_fields"] == []
    assert result["verification_result"]["passed"] is True
    assert len(result["day_plans"]) == 2
    stages = [stage for day in result["day_plans"] for stage in day["stages"]]
    assert len(stages) >= 7
    assert {stage["mode"] for stage in stages} >= {"driving", "transit", "walking"}
    assert all(stage["weather_summary"].startswith("预计抵达") for stage in stages)
    driving = [stage for stage in stages if stage["mode"] == "driving"]
    assert all(stage["energy_estimate"]["estimated"] for stage in driving)
    assert result["vehicle_profile"]["power_type"] == "electric"
    assert result["service_pois"]
    assert any(
        activity["type"] in {"rest", "charging"}
        for day in result["day_plans"]
        for activity in day["activities"]
    )
    assert "武汉—庐山自驾行程安排" in result["plan_markdown"]
    assert result["trip_request"].get("travelers") is None
    assert "travelers=1" not in result["trip_request"]["defaults_applied"]


@pytest.mark.asyncio
async def test_tourism_discovery_keeps_flyai_meal_and_hotel_candidates():
    graph = build_planning_graph(
        fake_registry(with_flyai=True),
        Settings(load_local_skill_credentials=False, enable_llm_requirement_extraction=False),
    )
    result = await graph.ainvoke(
        {
            "trip_id": "trip_flyai_sources",
            "raw_input": "周六从武汉去庐山，两天一夜",
            "trip_request": {
                "raw_text": "周六从武汉去庐山，两天一夜",
                "origin": {"name": "武汉"},
                "destination": {"name": "庐山"},
                "start_date": "2026-08-08",
                "end_date": "2026-08-09",
                "max_days": 2,
            },
            "clarification_round": 0,
        }
    )
    candidates = result["tourism_candidates"]
    assert any(item["provider"] == "fake-flyai" for item in candidates["meals"])
    assert any(item["provider"] == "fake-flyai" for item in candidates["hotels"])


@pytest.mark.asyncio
async def test_graph_builds_five_days_and_multiple_transport_modes():
    graph = build_planning_graph(
        fake_registry(),
        Settings(
            load_local_skill_credentials=False,
            enable_llm_requirement_extraction=False,
        ),
    )
    result = await graph.ainvoke(
        {
            "trip_id": "trip_five_days",
            "raw_input": "周六从武汉去庐山，五天四夜，喜欢公共交通和步行",
            "trip_request": {
                "raw_text": "周六从武汉去庐山，五天四夜，喜欢公共交通和步行",
                "origin": {"name": "武汉"},
                "destination": {"name": "庐山"},
                "start_date": "2026-08-08",
                "end_date": "2026-08-12",
                "max_days": 5,
                "preferences": ["公共交通", "步行", "骑行"],
            },
            "clarification_round": 0,
        }
    )
    stages = [stage for day in result["day_plans"] for stage in day["stages"]]
    assert len(result["day_plans"]) == 5
    assert len(stages) >= 13
    assert {"driving", "transit", "walking", "riding"} <= {
        stage["mode"] for stage in stages
    }
    assert all(stage["route_segments"][0]["coordinates"] for stage in stages)
    assert all(stage["weather_samples"] for stage in stages)
    assert result["verification_result"]["passed"] is True
    assert stages[0]["origin"]["name"] == stages[-1]["destination"]["name"]
    assert {"rest", "charging", "fueling", "parking", "meal", "hospital", "toilet"} <= {
        category
        for stage_services in result["service_pois"].values()
        for category in stage_services
    }


@pytest.mark.asyncio
async def test_graph_pauses_with_visible_clarification():
    graph = build_planning_graph(
        fake_registry(),
        Settings(
            load_local_skill_credentials=False,
            enable_llm_requirement_extraction=False,
        ),
    )
    result = await graph.ainvoke(
        {
            "trip_id": "trip_clarify",
            "raw_input": "周六想出去玩两天一夜",
            "trip_request": {"raw_text": "周六想出去玩两天一夜"},
            "clarification_round": 0,
        }
    )
    assert result["missing_fields"]
    assert result["clarification_question"]
    assert result["progress"]["paused"] is True


@pytest.mark.asyncio
async def test_runner_persists_state_markdown_and_trip_days():
    await create_tables()
    async with SessionLocal() as session:
        trip = await TripRepository(session).create(
            TripCreate(
                title="武汉—庐山",
                request=TripRequest(
                    raw_text="周六从武汉去庐山，两天一夜",
                    origin={"name": "武汉"},
                    destination={"name": "庐山"},
                    start_date=date(2026, 8, 8),
                    end_date=date(2026, 8, 9),
                    max_days=2,
                ),
            )
        )
    result = await run_planning(trip.id, registry=fake_registry())
    assert result["status"] == "completed"
    assert result["progress"]["value"] == 100
    events = await sse_manager.after(trip.id)
    progress_values = [item.payload.progress for item in events]
    assert progress_values == sorted(progress_values)
    assert events[-2].payload.progress == 96
    assert events[-1].payload.event == "planning_completed"
    assert events[-1].payload.progress == 100
    assert result["plan_markdown"].startswith("# 武汉—庐山")
    async with SessionLocal() as session:
        saved = await TripRepository(session).get(trip.id)
        assert saved is not None
        assert len(saved.days) == 2
        risks = await get_trip_risks(trip.id, TripRepository(session))
        services = await get_trip_services(trip.id, TripRepository(session))
        assert risks["summary"]["moderate"] >= 1
        assert services["services"]
        assert services["selected"]


@pytest.mark.asyncio
async def test_runner_uses_selected_vehicle_for_energy_and_charging_plan():
    await create_tables()
    vehicle = VehicleProfile(
        id="vehicle_low_battery_test",
        brand="RoadMan",
        series="Explorer",
        model="低电量纯电 SUV",
        power_type="electric",
        rated_range_km=560,
        current_energy_percent=25,
        battery_kwh=82,
        consumption_per_100km=18,
        max_charge_kw=180,
        mountain_ready=True,
    )
    async with SessionLocal() as session:
        existing = await VehicleRepository(session).get(vehicle.id)
        if not existing:
            await VehicleRepository(session).create(vehicle)
        trip = await TripRepository(session).create(
            TripCreate(
                title="低电量武汉—庐山",
                request=TripRequest(
                    raw_text="周六从武汉去庐山，两天一夜",
                    origin={"name": "武汉"},
                    destination={"name": "庐山"},
                    start_date=date(2026, 8, 8),
                    end_date=date(2026, 8, 9),
                    max_days=2,
                ),
                selected_vehicle_id=vehicle.id,
            )
        )

    result = await run_planning(trip.id, registry=fake_registry())
    assert result["status"] == "completed"
    async with SessionLocal() as session:
        saved = await TripRepository(session).get(trip.id)
        state = await TripRepository(session).load_planning_state(trip.id)
        assert saved is not None and state is not None
        assert state["vehicle_profile"]["id"] == vehicle.id
        driving = [
            stage for day in saved.days for stage in day.stages if stage.mode == "driving"
        ]
        assert all(stage.energy_estimate for stage in driving)
        assert any(
            activity.type == "charging"
            for day in saved.days
            for activity in day.activities
        )
