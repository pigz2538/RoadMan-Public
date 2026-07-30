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
        return SkillResult(
            success=True,
            provider="fake-amap",
            data={
                "requested_mode": "driving",
                "selected_mode": "driving",
                "fallback_used": False,
                "distance_km": 254.2,
                "duration_minutes": 210,
                "tolls_cny": 95,
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
            },
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready"}


def fake_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(FakeGeocodeAdapter())
    registry.register(FakeRouteAdapter())
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
    assert "武汉—庐山自驾路书" in result["plan_markdown"]
    assert "travelers=1" in result["trip_request"]["defaults_applied"]


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
