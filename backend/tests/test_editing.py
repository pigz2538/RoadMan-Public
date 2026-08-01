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
    DeleteActivityPatchRequest,
    EditIntentRequest,
    MapPointPatchRequest,
    create_candidate_patch,
    create_delete_activity_patch,
    create_map_point_patch,
    decide_candidate_patch,
    interpret_edit_intent,
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


def test_semantic_edit_adds_meal_near_selected_stage_without_activity_selection():
    origin = {"name": "庐山", "coordinates": {"longitude": 115.98, "latitude": 29.57}}
    destination = {"name": "阳新服务区", "coordinates": {"longitude": 115.21, "latitude": 29.89}}
    base = datetime(2026, 8, 2, 10, tzinfo=timezone.utc)
    selected = stage("stage_return", 0, origin, destination, base)
    selected.title = "返程高速阶段"
    trip = Trip(
        title="语义编辑",
        request=TripRequest(raw_text="测试"),
        days=[DayPlan(id="day_2", day_index=2, date=date(2026, 8, 2), title="返程", stages=[selected])],
    )
    meal = {
        "id": "meal_service",
        "name": "阳新服务区餐厅",
        "coordinates": {"longitude": 115.21, "latitude": 29.89},
    }
    state = {"tourism_candidates": {"meals": []}, "service_pois": {"stage_return": {"meal": [meal]}}}

    message, patch, global_replan = interpret_edit_intent(
        trip,
        state,
        EditIntentRequest(
            message="从庐山返程到服务区吃个饭",
            current_day_id="day_2",
            current_target_id="stage_return",
        ),
    )

    assert global_replan is False
    assert patch is not None and patch.operation == "add"
    assert patch.proposed_value["candidate"]["place"]["name"] == "阳新服务区餐厅"
    assert "请确认修改预览" in message


def test_map_point_creates_preview_without_mutating_trip():
    trip = Trip(
        title="地图选点",
        request=TripRequest(raw_text="测试"),
        days=[DayPlan(id="day_1", day_index=1, date=date(2026, 8, 2), title="第 1 天")],
    )
    state = {}

    patch = create_map_point_patch(
        trip,
        state,
        MapPointPatchRequest(
            day_id="day_1",
            category="attractions",
            name="地图选择的观景台",
            address="庐山风景区",
            longitude=115.982,
            latitude=29.57,
        ),
    )

    assert trip.days[0].activities == []
    assert patch.status == "preview"
    assert patch.proposed_value["candidate"]["place"]["name"] == "地图选择的观景台"
    assert state["plan_patches"][patch.id]["operation"] == "add"


def test_semantic_add_prefers_named_candidate_over_first_candidate():
    trip = Trip(
        title="语义添加",
        request=TripRequest(raw_text="测试"),
        days=[DayPlan(id="day_1", day_index=1, date=date(2026, 8, 2), title="第 1 天")],
    )
    state = {
        "tourism_candidates": {
            "attractions": [
                {"candidate_id": "first", "place": {"name": "第一个景点"}},
                {"candidate_id": "target", "place": {"name": "花径公园"}},
            ]
        }
    }

    _, patch, _ = interpret_edit_intent(
        trip,
        state,
        EditIntentRequest(message="第 1 天添加花径公园", current_day_id="day_1"),
    )

    assert patch is not None
    assert patch.proposed_value["candidate_id"] == "target"


def test_semantic_meal_request_can_use_route_service_without_selected_stage():
    origin = {"name": "武汉", "coordinates": {"longitude": 114.3, "latitude": 30.5}}
    destination = {"name": "庐山", "coordinates": {"longitude": 115.98, "latitude": 29.57}}
    trip = Trip(
        title="服务区用餐",
        request=TripRequest(raw_text="测试"),
        days=[
            DayPlan(
                id="day_1",
                day_index=1,
                date=date(2026, 8, 2),
                title="第 1 天",
                stages=[stage("stage_drive", 0, origin, destination, datetime(2026, 8, 2, 8, tzinfo=timezone.utc))],
            )
        ],
    )
    state = {
        "tourism_candidates": {"meals": []},
        "service_pois": {"stage_drive": {"meal": [{"name": "阳新服务区餐厅", "coordinates": {"longitude": 115.2, "latitude": 29.8}}]}},
    }

    message, patch, _ = interpret_edit_intent(
        trip,
        state,
        EditIntentRequest(message="在返程服务区安排午饭", current_day_id="day_1"),
    )

    assert patch is not None
    assert patch.proposed_value["candidate"]["place"]["name"] == "阳新服务区餐厅"
    assert "修改预览" in message


def test_semantic_duration_can_shrink_for_more_attractions():
    trip = Trip(
        title="弹性停留",
        request=TripRequest(raw_text="测试"),
        days=[DayPlan(id="day_1", day_index=1, date=date(2026, 8, 2), title="第 1 天")],
    )
    state = {
        "tourism_candidates": {
            "attractions": [{"candidate_id": "a", "place": {"name": "新景点"}}],
        }
    }

    _, patch, _ = interpret_edit_intent(
        trip,
        state,
        EditIntentRequest(message="第1天多加一个景点，顺路短停", current_day_id="day_1"),
    )

    assert patch is not None
    assert patch.proposed_value["duration_minutes"] == 60
    assert patch.time_delta_minutes == 60


def test_delete_then_add_keeps_the_new_activity_in_canonical_day_state():
    origin = {"name": "Origin", "coordinates": {"longitude": 114.3, "latitude": 30.5}}
    destination = {"name": "Destination", "coordinates": {"longitude": 114.4, "latitude": 30.6}}
    start = datetime(2026, 8, 2, 8, tzinfo=timezone.utc)
    old = Activity(
        id="activity_old",
        day_id="day_1",
        sequence=1,
        type="attraction",
        place={"name": "Old attraction"},
        planned_start=start + timedelta(minutes=35),
        planned_end=start + timedelta(minutes=95),
        duration_minutes=60,
    )
    trip = Trip(
        title="edit regression",
        request=TripRequest(raw_text="test"),
        days=[DayPlan(
            id="day_1",
            day_index=1,
            date=date(2026, 8, 2),
            title="Day 1",
            stages=[stage("stage_1", 0, origin, destination, start)],
            activities=[old],
        )],
    )
    state = {"tourism_candidates": {"attractions": [{
        "candidate_id": "candidate_new",
        "place": {"name": "New attraction", "coordinates": destination["coordinates"]},
        "rank": 1,
        "score": 90,
    }]}}
    delete_patch = create_delete_activity_patch(
        trip,
        state,
        DeleteActivityPatchRequest(day_id="day_1", activity_id="activity_old"),
    )
    decide_candidate_patch(trip, state, delete_patch.id, apply=True)
    add_patch = create_candidate_patch(
        trip,
        state,
        CandidatePatchRequest(
            candidate_id="candidate_new",
            category="attractions",
            day_id="day_1",
            operation="add",
        ),
    )
    decide_candidate_patch(trip, state, add_patch.id, apply=True)
    assert [item.place.name for item in trip.days[0].activities] == ["New attraction"]
    assert [item.id for item in trip.days[0].items] == ["stage_1", trip.days[0].activities[0].id]
