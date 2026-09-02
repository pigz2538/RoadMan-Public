from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.api.trips import get_trip_risks, get_trip_services
from app.db import SessionLocal, create_tables
from app.domain.models import SkillResult, TripCreate, TripRequest, VehicleProfile
from app.planning.graph import (
    _ensure_coordinates,
    _merge_extracted_place,
    _current_weather_sample,
    _destination_focus_radius,
    _is_local_destination_anchor,
    _estimated_driving_arrival_date,
    _movement_stage,
    _scheduled_route_result,
    _train_route,
    _train_route_result,
    _return_stage_start,
    _return_deadline_issue,
    _verify_comfort_timeline,
    build_planning_graph,
)
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


def test_comfort_validator_rejects_unrequested_night_driving():
    issues = _verify_comfort_timeline(
        [
            {
                "date": "2026-09-04",
                "stages": [
                    {
                        "title": "\u57ce\u5e02\u51fa\u53d1",
                        "mode": "driving",
                        "planned_start": "2026-09-04T04:00:00+08:00",
                        "planned_end": "2026-09-04T05:00:00+08:00",
                    }
                ],
                "activities": [],
            }
        ],
        {"raw_text": "周五晚上出发，情侣出游，舒适为主"},
    )
    assert any(item["code"] == "COMFORT_NIGHT_DRIVING" for item in issues)


def test_comfort_validator_allows_explicit_night_travel_request():
    assert not _verify_comfort_timeline(
        [
            {
                "date": "2026-09-04",
                "stages": [
                    {
                        "title": "\u57ce\u5e02\u51fa\u53d1",
                        "mode": "driving",
                        "planned_start": "2026-09-04T04:00:00+08:00",
                        "planned_end": "2026-09-04T05:00:00+08:00",
                    }
                ],
                "activities": [],
            }
        ],
        {"raw_text": "凌晨四点出发，必须夜间开车"},
    )


def test_comfort_validator_relaxes_requested_evening_departure_in_steps():
    day = {
        "day_index": 1,
        "date": "2026-09-04",
        "stages": [
            {
                "title": "\u57ce\u5e02\u51fa\u53d1 \u00b7 \u7b2c 2/2 \u6bb5",
                "mode": "driving",
                "planned_start": "2026-09-04T21:20:00+08:00",
                "planned_end": "2026-09-04T23:27:00+08:00",
            }
        ],
        "activities": [],
    }
    request = {
        "start_date": "2026-09-04",
        "raw_text": "\u5468\u4e94\u665a\u4e0a\u4ece\u6b66\u6c49\u51fa\u53d1\uff0c\u53bb\u5408\u80a5\uff0c\u60c5\u4fa3\u51fa\u6e38\u8212\u9002\u4e3a\u4e3b",
    }
    assert _verify_comfort_timeline([day], request)
    assert _verify_comfort_timeline([day], request, relaxation_level=1)
    assert not _verify_comfort_timeline([day], request, relaxation_level=2)


def test_comfort_validator_allows_short_cross_midnight_outbound_after_final_relaxation():
    day = {
        "day_index": 1,
        "date": "2026-09-04",
        "stages": [
            {
                "title": "\u57ce\u5e02\u51fa\u53d1 \u00b7 \u7b2c 2/2 \u6bb5",
                "mode": "driving",
                "planned_start": "2026-09-04T21:20:00+08:00",
                "planned_end": "2026-09-05T00:20:00+08:00",
            }
        ],
        "activities": [],
    }
    request = {
        "start_date": "2026-09-04",
        "raw_text": "\u5468\u4e94\u665a\u4e0a\u4ece\u6b66\u6c49\u51fa\u53d1\u53bb\u5408\u80a5\uff0c\u8212\u9002\u51fa\u6e38",
    }
    assert not _verify_comfort_timeline([day], request, relaxation_level=3)


def test_return_deadline_allows_small_drift_and_half_day_grace():
    deadline = datetime(2026, 8, 9, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert _return_deadline_issue(
        deadline + timedelta(minutes=3), deadline
    ) is None

    warning = _return_deadline_issue(
        deadline + timedelta(hours=3), deadline
    )
    assert warning is not None
    assert warning["code"] == "RETURN_WINDOW_FLEXIBLE"
    assert warning["severity"] == "warning"

    blocker = _return_deadline_issue(
        deadline + timedelta(hours=12, minutes=1), deadline
    )
    assert blocker is not None
    assert blocker["code"] == "RETURN_DEADLINE_UNACHIEVABLE"
    assert blocker["severity"] == "blocker"


def test_fresh_semantic_destination_replaces_stale_parent_without_losing_same_anchor_metadata():
    stale = {
        "name": "河南",
        "coordinates": {"longitude": 113.6, "latitude": 34.7},
        "source_id": "old-geocode",
    }
    replaced = _merge_extracted_place(stale, "郑州", "city")
    assert replaced == {"name": "郑州", "destination_scope": "city"}

    same = _merge_extracted_place(stale, "河南省", "province")
    assert same["coordinates"] == stale["coordinates"]
    assert same["name"] == "河南省"


def test_scheduled_routes_reject_missing_real_service_numbers():
    origin = {"name": "武汉", "coordinates": {"longitude": 114.3, "latitude": 30.6}}
    destination = {"name": "成都", "coordinates": {"longitude": 104.1, "latitude": 30.7}}
    schedule = {
        "departure_at": "2026-09-02T08:00:00+08:00",
        "arrival_at": "2026-09-02T10:00:00+08:00",
        "departure_airport": "武汉天河国际机场",
        "arrival_airport": "成都天府国际机场",
    }

    assert _scheduled_route_result(schedule, origin, destination, [], mode="flight")["error_code"] == "FLIGHT_SERVICE_NUMBER_MISSING"
    assert _train_route_result(schedule, origin, destination, [])["error_code"] == "TRAIN_SERVICE_NUMBER_MISSING"


@pytest.mark.asyncio
async def test_train_route_warns_when_requested_morning_has_no_service(monkeypatch):
    class FakeTrainAdapter(SkillAdapter):
        def __init__(self, name: str):
            self.name = name

        async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
            day = payload["dep_date"]
            return SkillResult(
                success=True,
                provider=self.name,
                data={
                    "items": [
                        {
                            "train_number": "G9001",
                            "departure_station": "武汉站",
                            "arrival_station": "合肥南站",
                            "departure_at": f"{day}T13:10:00",
                            "arrival_at": f"{day}T15:00:00",
                        }
                    ]
                },
            )

        async def health_check(self) -> dict[str, Any]:
            return {"status": "ready"}

    registry = SkillRegistry()
    for adapter_name in ("flyai.train", "mcp12306.train", "freeapi.train"):
        registry.register(FakeTrainAdapter(adapter_name))

    async def no_terminal_lookup(registry, route, origin, destination, trip_id):
        return route

    monkeypatch.setattr("app.planning.graph._attach_scheduled_terminals", no_terminal_lookup)
    route, warnings = await _train_route(
        registry,
        {"name": "武汉", "coordinates": {"longitude": 114.3, "latitude": 30.6}},
        {"name": "合肥", "coordinates": {"longitude": 117.2, "latitude": 31.8}},
        "trip_train_period",
        travel_date=date(2026, 9, 4),
        requested_departure=datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        departure_period="morning",
    )

    assert route["success"] is True
    assert any(item["code"] == "TRAIN_DEPARTURE_PERIOD_UNAVAILABLE" for item in warnings)
    assert route["data"]["scheduled_departure_at"].startswith("2026-09-04T13:10")


def test_long_outbound_drive_arrival_date_respects_daytime_and_daily_budget():
    start = datetime(2026, 8, 24, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    # 1,450 minutes of driving at a 9-hour daily budget must occupy three
    # calendar days (08:00--17:00, 08:00--17:00, then the remaining leg).
    assert _estimated_driving_arrival_date(start, 1450, 9 * 60) == date(2026, 8, 26)

    # A route that ends inside the first daytime window remains on the
    # departure date; no extra local-day suppression is needed.
    assert _estimated_driving_arrival_date(start, 240, 9 * 60) == date(2026, 8, 24)


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


def test_structural_calendar_resolves_abbreviated_english_weekdays():
    extracted = extract_structural_constraints(
        "Next Thu after work fly to Hangzhou, Sunday night return",
        date(2026, 8, 29),
    )

    assert extracted == {
        "start_date": "2026-09-03",
        "end_date": "2026-09-06",
    }


def test_structural_constraints_preserve_explicit_cross_sea_and_zero_window():
    extracted = extract_structural_constraints(
        "2099-08-02从上海出发跨海去普陀山，下午3点出发到下午3点抵达",
        date(2026, 8, 3),
    )

    assert extracted["cross_sea_required"] is True
    assert extracted["departure_time"] == "15:00"
    assert extracted["return_time"] == "15:00"
    assert extracted["time_window_minutes"] == 0


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


def test_explicit_location_fallback_handles_transport_between_origin_and_destination():
    text = "这周日到下周三想和对象从武汉坐飞机去成都玩三天，一定要去麓湖CPI"
    assert extract_explicit_location_constraints(text) == {
        "origin_name": "武汉",
        "destination_name": "成都",
    }
    assert extract_structural_constraints(text, date(2026, 8, 19)) == {
        "start_date": "2026-08-23",
        "end_date": "2026-08-26",
    }


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
            FakeClient.prompt = kwargs["json"]["messages"][0]["content"]
            return FakeResponse()

    monkeypatch.setattr("app.planning.llm.httpx.AsyncClient", FakeClient)
    extractor = OllamaRequirementExtractor(
        Settings(
            ollama_api_key="test-key",
            ollama_api_url="https://test.example/v1/chat/completions",
            ollama_model="test-model",
            enable_llm_requirement_extraction=True,
        )
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
    extractor = OllamaRequirementExtractor(
        Settings(
            ollama_api_key="test-key",
            ollama_api_url="https://test.example/v1/chat/completions",
            ollama_model="test-model",
        )
    )

    extracted = await extractor.extract(
        "情侣出游，从武汉到庐山，同行 4 人",
        date(2026, 8, 1),
    )

    # The structured Agent response is authoritative; raw text is not scanned
    # for a numeric keyword override.
    assert extracted["travelers"] == 2


@pytest.mark.asyncio
async def test_requirement_agent_repairs_serialized_multi_destination_without_geocoding_it(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return {"response": self.payload}

    class FakeClient:
        responses = [
            '{"origin_name":"武汉","destination_name":"[\\"西藏\\", \\"新疆\\"]",'
            '"destination_names":[],"destination_scope":"multi_destination"}',
            '{"origin_name":"武汉","destination_name":"西藏",'
            '"destination_names":["西藏","新疆"],"destination_scope":"multi_destination",'
            '"travel_intents":["自然风光"]}',
        ]

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse(self.responses.pop(0))

    monkeypatch.setattr("app.planning.llm.httpx.AsyncClient", FakeClient)
    extracted = await OllamaRequirementExtractor(
        Settings(
            ollama_api_key="test-key",
            ollama_api_url="https://test.example/v1/chat/completions",
            ollama_model="test-model",
            enable_llm_requirement_extraction=True,
        )
    ).extract("我从武汉出发去西藏和新疆看自然风光", date(2026, 8, 1))

    assert extracted["origin_name"] == "武汉"
    assert extracted["destination_name"] == "西藏"
    assert extracted["destination_names"] == ["西藏", "新疆"]
    assert extracted["destination_scope"] == "multi_destination"
    assert extracted["_intent_status"] == "ok"


@pytest.mark.asyncio
async def test_requirement_agent_without_cloud_does_not_guess_locations():
    extracted = await OllamaRequirementExtractor(
        Settings(ollama_api_key="", enable_llm_requirement_extraction=True)
    ).extract("我说武汉到新疆", date(2026, 8, 1))

    assert extracted["_intent_status"] == "unavailable"
    assert "origin_name" not in extracted
    assert "destination_name" not in extracted


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


def test_intercity_stage_exposes_real_service_details_and_transit_legs():
    stage = _movement_stage(
        day_id="day_1",
        sequence=0,
        title="公共交通前往景点",
        origin={"name": "武汉站", "coordinates": {"longitude": 114.4, "latitude": 30.6}},
        destination={"name": "博物馆", "coordinates": {"longitude": 114.3, "latitude": 30.5}},
        route={
            "data": {
                "selected_mode": "transit",
                "distance_km": 5.2,
                "duration_minutes": 30,
                "geometry": [
                    {"longitude": 114.4, "latitude": 30.6},
                    {"longitude": 114.3, "latitude": 30.5},
                ],
                "transit_legs": [{
                    "mode": "subway", "line_name": "2号线",
                    "departure_stop": "汉口站", "arrival_stop": "江汉路站",
                    "fare_cny": 3,
                }],
                "fare_cny": 3,
                "price": "¥ 623",
                "service_number": "G344",
                "service_operator": "铁路",
                "seat_class": "二等座",
            },
            "sources": [],
        },
        start_at=datetime(2026, 8, 1, 9, 0),
    )
    assert stage.transit_type == "subway"
    assert stage.transit_legs[0].line_name == "2号线"
    assert stage.transit_legs[0].arrival_stop == "江汉路站"
    assert stage.transit_fare_cny == 3
    assert stage.service_number == "G344"
    assert stage.service_price is not None
    assert stage.service_price.minimum == 623
    assert stage.service_seat_class == "二等座"
    assert stage.traffic_summary


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


@pytest.mark.asyncio
async def test_city_destination_is_not_replaced_by_nearby_restaurant():
    """A city name must remain a city, even when a nearby POI shares its name.

    Regression for the Wuhan -> Beijing request that became the local POI
    ``北京片皮烤鸭``: the short-name ambiguity fallback used to search around
    Wuhan for every result more than 250 km away, then overwrite the
    destination with the closest matching restaurant.
    """

    class CityRegistry:
        def __init__(self):
            self.calls: list[str] = []

        async def execute(self, name, payload, _):
            self.calls.append(name)
            if name == "amap.geocode":
                return SkillResult(
                    success=True,
                    provider="fake-amap",
                    data={
                        "formatted_address": "北京市",
                        "location": "116.407387,39.904179",
                        "province": "北京市",
                        "city": "北京市",
                        "district": [],
                        "level": "市",
                    },
                )
            return SkillResult(
                success=True,
                provider="fake-amap",
                data={
                    "items": [
                        {
                            "name": "北京片皮烤鸭",
                            "location": "114.288664,30.582774",
                            "address": "武汉市江汉三路",
                            "city": "武汉市",
                            "type": "餐饮服务",
                        }
                    ]
                },
            )

    registry = CityRegistry()
    origin = {
        "name": "武汉",
        "city": "武汉市",
        "coordinates": {"longitude": 114.3055, "latitude": 30.5928},
    }
    destination = await _ensure_coordinates(
        registry,
        {"name": "北京"},
        "trip",
        nearby=origin,
    )

    assert destination["name"] == "北京"
    assert destination["city"] == "北京市"
    assert destination["coordinates"] == {"longitude": 116.407387, "latitude": 39.904179}
    assert registry.calls == ["amap.geocode"]


@pytest.mark.asyncio
async def test_region_scope_never_uses_nearby_poi_disambiguation():
    class RegionRegistry:
        def __init__(self):
            self.calls = []

        async def execute(self, name, payload, _):
            self.calls.append(name)
            if name == "amap.geocode":
                return SkillResult(
                    success=True,
                    provider="fake-amap",
                    data={
                        "formatted_address": "新疆维吾尔自治区",
                        "location": "87.6177,43.7928",
                        "province": "新疆维吾尔自治区",
                        "city": "乌鲁木齐市",
                        "district": "天山区",
                        "level": "兴趣点",
                    },
                )
            raise AssertionError(f"unexpected nearby lookup: {name}")

    registry = RegionRegistry()
    destination = await _ensure_coordinates(
        registry,
        {"name": "新疆", "destination_scope": "province"},
        "trip",
        nearby={"coordinates": {"longitude": 114.3055, "latitude": 30.5928}},
    )

    assert destination["name"] == "新疆"
    assert registry.calls == ["amap.geocode"]


def test_return_time_is_scheduled_as_arrival_deadline():
    start = datetime(2026, 8, 9, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    route = {"data": {"duration_minutes": 711}}

    scheduled = _return_stage_start(
        date(2026, 8, 9),
        "20:00",
        start,
        route,
    )

    assert scheduled == datetime(2026, 8, 9, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert scheduled + timedelta(minutes=711) == datetime(
        2026,
        8,
        9,
        21,
        21,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )


def test_driving_return_deadline_never_starts_before_daylight():
    """An early arrival target must not create a midnight road departure."""
    scheduled = _return_stage_start(
        date(2026, 9, 6),
        "08:00",
        datetime(2026, 9, 6, 0, 44, tzinfo=ZoneInfo("Asia/Shanghai")),
        {
            "data": {
                "selected_mode": "driving",
                "duration_minutes": 342,
            }
        },
    )

    assert scheduled == datetime(
        2026,
        9,
        6,
        7,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )


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


class FakeFlightAdapter(SkillAdapter):
    name = "flyai.flight"

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        dep_date = payload["dep_date"]
        return SkillResult(
            success=True,
            provider="fake-flyai",
            data={
                "items": [
                    {
                        "departure_at": f"{dep_date}T09:00:00",
                        "arrival_at": f"{dep_date}T11:00:00",
                        "duration_minutes": 120,
                        "flight_number": "RM100",
                        "departure_airport": "天河机场",
                        "arrival_airport": "九江机场",
                        "price": "¥500",
                        "detail_url": "https://example.test/flight",
                    }
                ]
            },
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready"}


class FakePoiAdapter(SkillAdapter):
    name = "amap.poi"

    async def execute(self, payload: dict[str, Any], __: SkillContext) -> SkillResult:
        scenic_items = [
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
        ]
        if payload.get("keywords") == "景点":
            scenic_items.extend(
                [
                    {
                        "id": "poi-road",
                        "name": "山景大道",
                        "location": "115.965000,29.565000",
                        "city": "九江",
                        "type": "地名地址信息;道路;主干道",
                        "typecode": "190301",
                    },
                    {
                        "id": "poi-agency",
                        "name": "自然假期旅行社",
                        "location": "115.955000,29.575000",
                        "city": "九江",
                        "type": "生活服务;旅行社;旅行社网点",
                        "typecode": "070000",
                    },
                ]
            )
        return SkillResult(
            success=True,
            provider="fake-amap",
            data={
                "count": len(scenic_items),
                "items": scenic_items,
            },
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready"}


class FakeFlyAIPoiAdapter(SkillAdapter):
    name = "flyai.poi"

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        is_meal = payload.get("keyword") == "餐厅"
        name = "FlyAI 餐厅" if is_meal else "FlyAI 景点"
        items = [
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
        if not is_meal:
            items.append(
                {
                    "id": "travel-agency-card",
                    "name": "名山旅游（旅行社名称）",
                    "address": "九江测试地址",
                    "longitude": 115.935,
                    "latitude": 29.605,
                    "categories": "生活服务;旅行社",
                }
            )
        return SkillResult(
            success=True,
            provider="fake-flyai",
            data={"items": items},
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


def test_current_weather_snapshot_uses_today_for_far_future_plans():
    now = datetime(2026, 8, 7, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    sample = _current_weather_sample(
        {
            "current": {
                "time": "2026-08-07T10:00",
                "temperature_2m": 31,
                "weather_code": 2,
                "wind_speed_10m": 8,
            },
            "hourly_samples": [
                {
                    "sampled_at": "2026-08-07T10:00",
                    "temperature_c": 30,
                    "precipitation_probability": 20,
                    "visibility_m": 12000,
                }
            ],
        },
        now,
    )

    assert sample == {
        "sampled_at": "2026-08-07T10:00",
        "temperature_c": 30,
        "precipitation_probability": 20,
        "weather_code": 2,
        "visibility_m": 12000,
        "wind_speed_kmh": 8,
    }


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
    assert "driving" in {stage["mode"] for stage in stages}
    assert not ({"transit", "riding"} & {stage["mode"] for stage in stages})
    assert all(
        stage["weather_summary"].startswith(("预计抵达", "预报天气参考"))
        for stage in stages
    )
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
async def test_return_deadline_trims_last_day_before_long_drive():
    graph = build_planning_graph(
        fake_registry(),
        Settings(
            load_local_skill_credentials=False,
            enable_llm_requirement_extraction=False,
        ),
    )
    result = await graph.ainvoke(
        {
            "trip_id": "trip_return_deadline",
            "raw_input": "8月8日从武汉自驾去庐山，8月9日20点前返回武汉",
            "trip_request": {
                "raw_text": "8月8日从武汉自驾去庐山，8月9日20点前返回武汉",
                "origin": {"name": "武汉"},
                "destination": {"name": "庐山"},
                "start_date": "2026-08-08",
                "end_date": "2026-08-09",
                "departure_time": "08:00:00",
                "return_time": "20:00:00",
                "transport_modes": ["driving"],
                "max_days": 2,
            },
            "clarification_round": 0,
        }
    )

    assert result["verification_result"]["passed"] is True
    final_stage = result["day_plans"][-1]["stages"][-1]
    assert final_stage["title"].startswith("返程")
    arrival = datetime.fromisoformat(final_stage["planned_end"])
    requested = datetime(2026, 8, 9, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert arrival <= requested + timedelta(hours=12)
    assert any(
        issue["code"] == "RETURN_WINDOW_FLEXIBLE"
        for issue in result["verification_result"]["issues"]
    )
    assert all(
        stage["planned_start"][:10] == stage["planned_end"][:10]
        for day in result["day_plans"]
        for stage in day["stages"]
        if stage["mode"] == "driving"
    )


@pytest.mark.asyncio
async def test_tourism_discovery_keeps_flyai_meal_and_hotel_candidates():
    progress_events = []

    async def collect_progress(trip_id, node, label, progress, event, tool):
        progress_events.append((node, label, event, tool))

    graph = build_planning_graph(
        fake_registry(with_flyai=True),
        Settings(load_local_skill_credentials=False, enable_llm_requirement_extraction=False),
        progress_callback=collect_progress,
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
    attraction_names = {
        item["place"]["name"] for item in candidates["attractions"]
    }
    assert "山景大道" not in attraction_names
    assert "自然假期旅行社" not in attraction_names
    assert "名山旅游（旅行社名称）" not in attraction_names
    excluded_names = {
        item["name"]
        for item in result["destination_research"].get("candidate_entity_exclusions", [])
    }
    assert {"山景大道", "自然假期旅行社", "名山旅游（旅行社名称）"} <= excluded_names
    assert all(
        activity.get("place", {}).get("name") not in excluded_names
        for day in result["day_plans"]
        for activity in day.get("activities", [])
    )
    selected_hotel_names = {
        activity["place"]["name"]
        for day in result["day_plans"]
        for activity in day["activities"]
        if activity.get("type") == "hotel" and activity.get("place", {}).get("name")
    }
    assert selected_hotel_names
    # The hotel chosen by the tourism scheduler is also the base used by the
    # movement planner.  Every day therefore has an explicit hotel endpoint;
    # no day can silently teleport from a city centroid or a terminal.
    for day in result["day_plans"]:
        day_endpoints = {
            endpoint["name"]
            for stage in day["stages"]
            for endpoint in (stage.get("origin", {}), stage.get("destination", {}))
            if endpoint.get("name")
        }
        assert day_endpoints & selected_hotel_names
    assert {
        "flyai_poi_attractions",
        "flyai_poi_meals",
        "flyai_hotels",
    } <= {event[0] for event in progress_events}
    assert any(
        event[0] == "flyai_hotels" and event[2] == "tool_completed"
        for event in progress_events
    )


def test_destination_focus_is_semantic_for_scenic_poi_but_broad_for_city():
    scenic = {
        "name": "仙岛湖",
        "city": "阳新县",
        "destination_scope": "poi",
    }
    city = {
        "name": "成都",
        "city": "成都市",
        "destination_scope": "city",
    }
    assert _is_local_destination_anchor(scenic) is True
    assert _destination_focus_radius(scenic) == 50.0
    assert _is_local_destination_anchor(city) is False
    assert _destination_focus_radius(city) is None
    assert _destination_focus_radius(city, explicit_local=True) == 35.0


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
                "transport_modes": ["transit", "walking", "riding"],
            },
            "clarification_round": 0,
        }
    )
    stages = [stage for day in result["day_plans"] for stage in day["stages"]]
    assert len(result["day_plans"]) == 5
    assert len(stages) >= 13
    assert {"transit", "walking", "riding"} <= {
        stage["mode"] for stage in stages
    }
    assert "driving" not in {stage["mode"] for stage in stages}
    assert all(stage["route_segments"][0]["coordinates"] for stage in stages)
    assert all(stage["weather_samples"] for stage in stages)
    assert result["verification_result"]["passed"] is True
    assert stages[0]["origin"]["name"] == stages[-1]["destination"]["name"]
    # A public-transport-only plan must not manufacture vehicle charging,
    # fueling or parking stops merely to satisfy the self-drive service set.
    assert not {
        category
        for stage_services in result["service_pois"].values()
        for category in ("charging", "fueling", "parking")
        if category in stage_services
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
    assert events[-2].payload.progress == 99
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
async def test_explicit_flight_uses_schedule_adapter_instead_of_default_driving():
    registry = fake_registry()
    registry.register(FakeFlightAdapter())
    graph = build_planning_graph(
        registry,
        Settings(
            load_local_skill_credentials=False,
            enable_llm_requirement_extraction=False,
        ),
    )
    result = await graph.ainvoke(
        {
            "trip_id": "trip_flight_mode",
            "raw_input": "周六从武汉去庐山，明确乘飞机，两天返回",
            "trip_request": {
                "raw_text": "周六从武汉去庐山，明确乘飞机，两天返回",
                "origin": {"name": "武汉"},
                "destination": {"name": "庐山"},
                "start_date": "2026-08-08",
                "end_date": "2026-08-09",
                "transport_modes": ["flight"],
            },
            "clarification_round": 0,
        }
    )
    assert result["verification_result"]["passed"] is True
    intercity = [
        stage
        for day in result["day_plans"]
        for stage in day["stages"]
        if stage["title"] in {"城市出发", "返程"}
    ]
    assert intercity
    assert {stage["mode"] for stage in intercity} == {"flight"}
    assert all(stage["traffic_summary"] for stage in intercity)
    local_modes = {
        stage["mode"]
        for day in result["day_plans"]
        for stage in day["stages"]
        if stage["mode"] != "flight"
    }
    assert "driving" not in local_modes
    assert "riding" not in local_modes
    outbound_stage = next(stage for stage in intercity if stage["title"] == "城市出发")
    return_stage = next(stage for stage in intercity if stage["title"] == "返程")
    assert outbound_stage["origin"]["name"] == "天河机场"
    assert outbound_stage["destination"]["name"] == "九江机场"
    # Even when the morning return flight leaves no sightseeing window, the
    # hotel-to-terminal connector keeps the movement chain continuous.
    assert any(
        stage["title"] == "返程接驳"
        and stage["destination"]["name"] == return_stage["origin"]["name"]
        for day in result["day_plans"]
        for stage in day["stages"]
    )


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
