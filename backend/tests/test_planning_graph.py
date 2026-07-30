from datetime import date, datetime, time, timedelta
from typing import Any

import pytest

from app.core.config import Settings
from app.db import SessionLocal, create_tables
from app.domain.models import SkillResult, TripCreate, TripRequest
from app.planning.graph import build_planning_graph
from app.planning.runner import run_planning
from app.repositories import TripRepository
from app.skills.base import SkillAdapter, SkillContext
from app.skills.registry import SkillRegistry


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
        duration = 210 if mode == "driving" else 25
        return SkillResult(
            success=True,
            provider="fake-amap",
            data={
                "requested_mode": mode,
                "selected_mode": mode,
                "fallback_used": False,
                "distance_km": 254.2,
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


def fake_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(FakeGeocodeAdapter())
    registry.register(FakeRouteAdapter())
    registry.register(FakePoiAdapter())
    registry.register(FakeWeatherAdapter())
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
            "trip_request": {"raw_text": "周六从武汉去庐山，两天一夜"},
            "clarification_round": 0,
        }
    )
    assert result["missing_fields"] == []
    assert result["verification_result"]["passed"] is True
    assert len(result["day_plans"]) == 2
    stages = [stage for day in result["day_plans"] for stage in day["stages"]]
    assert len(stages) == 4
    assert {stage["mode"] for stage in stages} >= {"driving", "transit", "walking"}
    assert all(stage["weather_summary"].startswith("预计抵达") for stage in stages)
    assert "武汉—庐山自驾路书" in result["plan_markdown"]
    assert "travelers=1" in result["trip_request"]["defaults_applied"]


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
            "trip_request": {"raw_text": "周六从武汉去庐山，五天四夜，喜欢公共交通和步行"},
            "clarification_round": 0,
        }
    )
    stages = [stage for day in result["day_plans"] for stage in day["stages"]]
    assert len(result["day_plans"]) == 5
    assert len(stages) == 8
    assert {"driving", "transit", "walking", "riding"} <= {
        stage["mode"] for stage in stages
    }
    assert all(stage["route_segments"][0]["coordinates"] for stage in stages)
    assert all(stage["weather_samples"] for stage in stages)


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
                request=TripRequest(raw_text="周六从武汉去庐山，两天一夜"),
            )
        )
    result = await run_planning(trip.id, registry=fake_registry())
    assert result["status"] == "completed"
    assert result["plan_markdown"].startswith("# 武汉—庐山")
    async with SessionLocal() as session:
        saved = await TripRepository(session).get(trip.id)
        assert saved is not None
        assert len(saved.days) == 2
