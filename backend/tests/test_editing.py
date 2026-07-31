from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.models import (
    Activity,
    DayPlan,
    MovementStage,
    RouteSegment,
    SkillResult,
    Trip,
    TripRequest,
)
from app.planning.editing import (
    CandidatePatchRequest,
    create_candidate_patch,
    decide_candidate_patch,
    recompute_and_verify_patch,
)
from app.skills.base import SkillAdapter, SkillContext
from app.skills.registry import SkillRegistry


class RouteAdapter(SkillAdapter):
    name = "amap.route"
    category = "route"

    async def validate_input(self, payload):
        return payload

    async def execute(self, payload, _context):
        origin = payload["origin"]
        destination = payload["destination"]
        return SkillResult(
            success=True,
            provider="test-amap",
            data={
                "selected_mode": payload["preferred_mode"],
                "duration_minutes": 18,
                "distance_km": 7.5,
                "geometry": [
                    {
                        "longitude": origin["longitude"],
                        "latitude": origin["latitude"],
                    },
                    {
                        "longitude": destination["longitude"],
                        "latitude": destination["latitude"],
                    },
                ],
                "steps": [{"road": "测试道路"}],
                "tolls_cny": 0,
            },
        )

    async def health_check(self):
        return {"status": "ready"}


def stage(stage_id, sequence, origin, destination, start):
    return MovementStage(
        id=stage_id,
        day_id="day_1",
        sequence=sequence,
        title="景点接驳",
        mode="driving",
        origin=origin,
        destination=destination,
        route_segments=[
            RouteSegment(
                coordinates=[origin["coordinates"], destination["coordinates"]],
                distance_km=10,
                duration_minutes=20,
            )
        ],
        planned_start=start,
        planned_end=start + timedelta(minutes=20),
        distance_km=10,
        duration_minutes=20,
    )


@pytest.mark.asyncio
async def test_replacing_attraction_recomputes_both_adjacent_routes():
    start = {"name": "武汉", "coordinates": {"longitude": 114.3, "latitude": 30.5}}
    old = {"name": "旧景点", "coordinates": {"longitude": 114.4, "latitude": 30.6}}
    new = {"name": "新景点", "coordinates": {"longitude": 114.5, "latitude": 30.7}}
    base = datetime(2026, 8, 2, 9, tzinfo=timezone.utc)
    activity = Activity(
        id="activity_old",
        day_id="day_1",
        sequence=1,
        type="attraction",
        place=old,
        planned_start=base + timedelta(minutes=30),
        planned_end=base + timedelta(minutes=90),
        duration_minutes=60,
    )
    trip = Trip(
        title="局部重算",
        request=TripRequest(raw_text="测试"),
        days=[
            DayPlan(
                id="day_1",
                day_index=1,
                date=date(2026, 8, 2),
                title="测试日",
                stages=[
                    stage("stage_out", 0, start, old, base),
                    stage("stage_back", 2, old, start, base + timedelta(hours=2)),
                ],
                activities=[activity],
            )
        ],
    )
    state = {
        "tourism_candidates": {
            "attractions": [{
                "candidate_id": "candidate_new",
                "place": new,
                "score": 90,
                "rank": 1,
            }]
        }
    }
    patch = create_candidate_patch(
        trip,
        state,
        CandidatePatchRequest(
            candidate_id="candidate_new",
            category="attractions",
            day_id="day_1",
            operation="replace",
            target_activity_id="activity_old",
        ),
    )
    patch, trip = decide_candidate_patch(trip, state, patch.id, apply=True)
    registry = SkillRegistry()
    registry.register(RouteAdapter())

    issues = await recompute_and_verify_patch(trip, state, patch, registry)

    assert issues == []
    assert trip.days[0].activities[0].place.name == "新景点"
    assert trip.days[0].stages[0].destination.name == "新景点"
    assert trip.days[0].stages[1].origin.name == "新景点"
    assert trip.days[0].stages[0].duration_minutes == 18
    assert state["verification_result"]["recomputed_stage_ids"] == [
        "stage_out",
        "stage_back",
    ]
