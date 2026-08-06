import json

import pytest

from app.planning.recommendations import plan_attraction_coverage, rank_tourism_candidates
from app.planning.tourism import review_daily_schedule, schedule_tourism_activities, verify_tourism_plan
from app.skills.base import SkillContext
from app.skills.flyai import (
    FlyAIFerryAdapter,
    FlyAIFlightAdapter,
    FlyAIHotelAdapter,
    FlyAIKeywordSearchAdapter,
    FlyAIPoiAdapter,
    FlyAISemanticSearchAdapter,
    _flyai_process_env,
    _parse_price,
)


def test_tourism_scheduler_adds_attraction_and_overnight_hotel():
    days = [
        {
            "id": "day_1",
            "date": "2026-08-02",
            "items": [],
            "activities": [],
            "stages": [
                {
                    "id": "stage_1",
                    "sequence": 0,
                    "destination": {"name": "庐山风景区"},
                    "planned_start": "2026-08-02T09:00:00+08:00",
                    "planned_end": "2026-08-02T10:00:00+08:00",
                },
                {
                    "id": "stage_2",
                    "sequence": 1,
                    "destination": {"name": "牯岭镇"},
                    "planned_start": "2026-08-02T12:00:00+08:00",
                    "planned_end": "2026-08-02T12:30:00+08:00",
                },
            ],
        },
        {
            "id": "day_2",
            "date": "2026-08-03",
            "items": [],
            "activities": [],
            "stages": [],
        },
    ]
    source = [
        {
            "provider": "高德地图",
            "title": "POI 2.0 API",
            "retrieved_at": "2026-07-30T00:00:00Z",
        }
    ]
    candidates = {
        "attractions": [
            {
                "place": {
                    "name": "庐山风景区",
                    "city": "九江市",
                    "coordinates": {"longitude": 115.98, "latitude": 29.55},
                },
                "source_records": source,
                "ticket_or_price": {
                    "currency": "CNY",
                    "minimum": 80,
                    "maximum": 100,
                    "estimated": True,
                },
            }
        ],
        "hotels": [
            {
                "place": {
                    "name": "牯岭镇山景酒店",
                    "city": "九江市",
                    "coordinates": {"longitude": 115.97, "latitude": 29.56},
                },
                "source_records": source,
            }
        ],
        "meals": [],
    }

    scheduled = schedule_tourism_activities(days, candidates)

    first_day_types = [item["type"] for item in scheduled[0]["activities"]]
    assert first_day_types.count("meal") == 3
    assert "attraction" in first_day_types
    assert "hotel" in first_day_types
    attraction = next(item for item in scheduled[0]["activities"] if item["type"] == "attraction")
    assert attraction["ticket_or_price"]["minimum"] == 80
    hotel = next(item for item in scheduled[0]["activities"] if item["type"] == "hotel")
    assert hotel["required"] is True
    assert hotel["planned_end"].startswith("2026-08-03")
    assert hotel["source_records"][0]["provider"] == "高德地图"
    assert verify_tourism_plan(scheduled, candidates) == []


def test_tourism_scheduler_reuses_comfortable_hotel_in_same_city_and_filters_hostel():
    days = [
        {
            "id": "day_1",
            "date": "2026-08-02",
            "items": [],
            "activities": [],
            "stages": [{
                "id": "stage_1",
                "sequence": 0,
                "origin": {"name": "武汉", "city": "武汉市"},
                "destination": {
                    "name": "乌镇西栅",
                    "city": "桐乡市",
                    "coordinates": {"longitude": 120.48, "latitude": 30.74},
                },
                "planned_start": "2026-08-02T08:00:00+08:00",
                "planned_end": "2026-08-02T10:00:00+08:00",
            }],
        },
        {
            "id": "day_2",
            "date": "2026-08-03",
            "items": [],
            "activities": [],
            "stages": [{
                "id": "stage_2",
                "sequence": 0,
                "origin": {
                    "name": "乌镇西栅",
                    "city": "桐乡市",
                    "coordinates": {"longitude": 120.48, "latitude": 30.74},
                },
                "destination": {
                    "name": "乌镇东栅",
                    "city": "桐乡市",
                    "coordinates": {"longitude": 120.49, "latitude": 30.75},
                },
                "planned_start": "2026-08-03T09:00:00+08:00",
                "planned_end": "2026-08-03T10:00:00+08:00",
            }],
        },
        {
            "id": "day_3",
            "date": "2026-08-04",
            "items": [],
            "activities": [],
            "stages": [],
        },
    ]
    candidates = {
        "attractions": [],
        "meals": [],
        "hotels": [
            {"place": {"name": "乌镇青年旅舍", "city": "桐乡市"}, "rating": 5},
            {
                "place": {
                    "name": "乌镇云水舒适酒店",
                    "city": "桐乡市",
                    "coordinates": {"longitude": 120.48, "latitude": 30.74},
                },
                "rating": 4.7,
            },
            {
                "place": {
                    "name": "乌镇另一家舒适酒店",
                    "city": "桐乡市",
                    "coordinates": {"longitude": 120.50, "latitude": 30.76},
                },
                "rating": 4.9,
            },
        ],
    }

    scheduled = schedule_tourism_activities(days, candidates)
    hotel_names = [
        next(item["place"]["name"] for item in day["activities"] if item["type"] == "hotel")
        for day in scheduled[:2]
    ]
    assert hotel_names == ["乌镇云水舒适酒店", "乌镇云水舒适酒店"]
    assert all("旅舍" not in name and "青年" not in name for name in hotel_names)


def test_tourism_scheduler_rotates_high_quality_meals_across_days():
    days = [
        {"id": f"day_{index}", "date": f"2026-08-{index + 1:02d}", "items": [], "activities": [], "stages": []}
        for index in range(2)
    ]
    candidates = {
        "attractions": [],
        "hotels": [],
        "meals": [
            {
                "place": {"name": f"地方名店{index}", "city": "乌镇"},
                "rating": 4.0 + index / 10,
                "source_records": [{"provider": "FlyAI / 飞猪"}],
            }
            for index in range(6)
        ],
    }

    scheduled = schedule_tourism_activities(days, candidates)
    meal_names = [
        item["place"]["name"]
        for day in scheduled
        for item in day["activities"]
        if item["type"] == "meal"
    ]
    assert len(meal_names) == 6
    assert len(set(meal_names)) == 6


def test_tourism_verifier_blocks_missing_hotel_when_candidates_exist():
    issues = verify_tourism_plan(
        [
            {"date": "2026-08-02", "activities": []},
            {"date": "2026-08-03", "activities": []},
        ],
        {"hotels": [{"place": {"name": "候选酒店"}}]},
    )

    assert issues[0]["code"] == "OVERNIGHT_HOTEL_MISSING"
    assert issues[0]["severity"] == "blocker"


def test_daily_review_is_idempotent_and_does_not_duplicate_hotels():
    days = [
        {
            "id": "day_review_1",
            "date": "2026-08-02",
            "items": [],
            "activities": [],
            "stages": [
                {
                    "id": "stage_review_1",
                    "sequence": 0,
                    "origin": {"name": "乌镇"},
                    "destination": {"name": "西栅景区"},
                    "planned_start": "2026-08-02T12:00:00+08:00",
                    "planned_end": "2026-08-02T13:00:00+08:00",
                }
            ],
        },
        {"id": "day_review_2", "date": "2026-08-03", "items": [], "activities": [], "stages": []},
    ]
    candidates = {
        "attractions": [
            {"place": {"name": "西栅景区"}},
            {"place": {"name": "乌镇老药铺"}},
        ],
        "hotels": [{"place": {"name": "乌镇民宿"}}],
        "meals": [],
    }
    first, _ = review_daily_schedule(days, candidates)
    second, _ = review_daily_schedule(first, candidates)
    assert sum(item["type"] == "hotel" for item in second[0]["activities"]) == 1
    assert sum(item["type"] == "attraction" for item in second[0]["activities"]) >= 2


def test_tourism_scheduler_materializes_missing_day_id():
    days = [{
        "date": "2026-08-02",
        "items": [],
        "activities": [],
        "stages": [],
    }]

    scheduled = schedule_tourism_activities(days, {"attractions": [], "hotels": [], "meals": []})

    assert scheduled[0]["id"] == "day_1"
    assert scheduled[0]["title"] == "第 1 天"


def test_tourism_scheduler_fills_meals_without_routes_and_rotates_attractions():
    days = [
        {"id": "day_1", "date": "2026-08-02", "items": [], "activities": [], "stages": []},
        {"id": "day_2", "date": "2026-08-03", "items": [], "activities": [], "stages": []},
    ]
    candidates = {
        "attractions": [
            {"place": {"name": "古镇博物馆"}},
            {"place": {"name": "水乡花园"}},
            {"place": {"name": "运河夜景"}},
        ],
        "hotels": [],
        "meals": [],
    }

    scheduled = schedule_tourism_activities(days, candidates)

    for day in scheduled:
        meals = [item for item in day["activities"] if item["type"] == "meal"]
        assert len(meals) == 3
        assert all(item["place"]["name"] for item in meals)
    first_attractions = {
        item["place"]["name"]
        for item in scheduled[0]["activities"]
        if item["type"] == "attraction"
    }
    second_attractions = {
        item["place"]["name"]
        for item in scheduled[1]["activities"]
        if item["type"] == "attraction"
    }
    assert first_attractions.isdisjoint(second_attractions)


def test_tourism_scheduler_removes_repeated_agent_attraction_activities():
    repeated = lambda day_id, date_value: {
        "id": day_id,
        "date": date_value,
        "items": [],
        "stages": [],
        "activities": [{
            "id": f"activity_{day_id}",
            "type": "attraction",
            "place": {"name": "乌镇西栅景区"},
            "planned_start": f"{date_value}T10:00:00+08:00",
            "planned_end": f"{date_value}T11:00:00+08:00",
            "duration_minutes": 60,
        }],
    }
    days = [repeated("day_1", "2026-08-02"), repeated("day_2", "2026-08-03")]

    scheduled = schedule_tourism_activities(
        days,
        {"attractions": [], "hotels": [], "meals": []},
    )

    assert sum(
        item["place"]["name"] == "乌镇西栅景区"
        for day in scheduled
        for item in day["activities"]
        if item["type"] == "attraction"
    ) == 1


@pytest.mark.asyncio
async def test_flyai_hotel_adapter_degrades_when_cli_is_missing(monkeypatch):
    monkeypatch.setattr("app.skills.flyai.shutil.which", lambda _: None)
    result = await FlyAIHotelAdapter().execute(
        {
            "destination": "九江",
            "poi_name": "庐山",
            "check_in_date": "2026-08-08",
            "check_out_date": "2026-08-10",
        },
        SkillContext(),
    )

    assert result.success is False
    assert result.error_code == "SKILL_NOT_CONFIGURED"


def test_flyai_masked_price_is_a_range_not_a_fake_exact_amount():
    assert _parse_price("¥3xx") == (300.0, 399.0, True)
    assert _parse_price("¥618") == (618.0, 618.0, False)


def test_flyai_cli_receives_node_proxy_switch(monkeypatch):
    monkeypatch.delenv("NODE_USE_ENV_PROXY", raising=False)
    assert _flyai_process_env()["NODE_USE_ENV_PROXY"] == "1"
    monkeypatch.setenv("NODE_USE_ENV_PROXY", "0")
    assert _flyai_process_env()["NODE_USE_ENV_PROXY"] == "0"


def test_tourism_candidates_are_ranked_by_rating_distance_and_preference():
    candidates = {
        "attractions": [
            {
                "place": {
                    "name": "远方商场",
                    "coordinates": {"longitude": 116.8, "latitude": 39.9},
                    "source_id": "far",
                },
                "rating": 2,
                "provider": "高德地图",
            },
            {
                "place": {
                    "name": "自然湖公园",
                    "coordinates": {"longitude": 116.401, "latitude": 39.901},
                    "source_id": "near",
                },
                "rating": 4.8,
                "provider": "OpenTripMap",
            },
        ],
        "hotels": [],
        "meals": [],
    }
    ranked = rank_tourism_candidates(
        candidates,
        {"coordinates": {"longitude": 116.4, "latitude": 39.9}},
        ["喜欢自然风景"],
    )
    first = ranked["attractions"][0]
    assert first["place"]["name"] == "自然湖公园"
    assert first["rank"] == 1
    assert first["backup"] is False
    assert ranked["attractions"][1]["backup"] is True
    assert first["recommendation_reasons"]


def test_destination_research_highlight_outranks_nearby_generic_poi():
    candidates = {
        "attractions": [
            {
                "place": {
                    "name": "酒店旁小公园",
                    "coordinates": {"longitude": 118.80, "latitude": 32.05},
                    "source_id": "near",
                },
                "rating": 4.9,
                "provider": "高德地图",
            },
            {
                "place": {
                    "name": "秦淮河风光带",
                    "coordinates": {"longitude": 118.78, "latitude": 32.00},
                    "source_id": "qinhuai",
                },
                "rating": 4.5,
                "provider": "高德地图",
            },
        ],
        "hotels": [],
        "meals": [],
    }
    ranked = rank_tourism_candidates(
        candidates,
        {"coordinates": {"longitude": 118.80, "latitude": 32.05}},
        [],
        destination_research={
            "agent_recommendations": [
                {
                    "name": "秦淮河",
                    "category": "attractions",
                    "importance": 98,
                    "reason": "目的地研究来源列为南京代表性城市景观",
                }
            ]
        },
    )

    assert ranked["attractions"][0]["place"]["name"] == "秦淮河风光带"
    assert ranked["attractions"][0]["must_see"] is True
    assert ranked["attractions"][0]["destination_research_priority"] == 98


def test_scheduler_distributes_city_highlights_across_days():
    days = [
        {"id": "day_1", "date": "2026-08-11", "items": [], "activities": [], "stages": []},
        {"id": "day_2", "date": "2026-08-12", "items": [], "activities": [], "stages": []},
    ]
    names = ["秦淮河", "明城墙", "大报恩寺", "中山陵", "明孝陵", "雨花台"]
    candidates = {
        "attractions": [
            {
                "place": {"name": name, "coordinates": {"longitude": 118.7 + index * 0.01, "latitude": 32.0}},
                "score": 90 - index,
                "destination_research_priority": 100 - index,
                "source_records": [],
            }
            for index, name in enumerate(names)
        ],
        "hotels": [],
        "meals": [],
    }

    scheduled = schedule_tourism_activities(days, candidates)
    selected = [
        item["place"]["name"]
        for day in scheduled
        for item in day["activities"]
        if item["type"] == "attraction"
    ]
    assert set(selected) == set(names)
    assert len({name for name in selected[:3]}) == 3
    assert len(set(selected[3:])) == 3


def test_attraction_coverage_groups_researched_places_without_city_specific_rules():
    candidates = [
        {
            "place": {
                "name": f"代表地标{index}",
                "coordinates": {
                    "longitude": 120.10 + (index // 2) * 0.06,
                    "latitude": 30.10 + (index // 2) * 0.02,
                },
            },
            "destination_research_priority": 100 - index,
            "score": 90 - index,
            "research_area": f"片区{index // 2}",
        }
        for index in range(6)
    ]

    summary = plan_attraction_coverage(candidates, 3)

    assert summary["priority_count"] == 6
    assert summary["cluster_count"] == 3
    assert summary["deferred_count"] == 0
    assert {item["coverage_day_index"] for item in candidates} == {1, 2, 3}
    assert all(item["coverage_cluster"].startswith("area:") for item in candidates)


def test_verifier_reports_researched_highlight_coverage_gap_without_blocking_route():
    issues = verify_tourism_plan(
        [{"date": "2026-08-11", "activities": [], "stages": []}],
        {
            "attractions": [
                {
                    "place": {"name": "城市代表地标"},
                    "must_see": True,
                }
            ],
            "hotels": [],
        },
    )

    gap = next(item for item in issues if item["code"] == "DESTINATION_HIGHLIGHTS_UNCOVERED")
    assert gap["severity"] == "warning"
    assert "城市代表地标" in gap["description"]


@pytest.mark.asyncio
async def test_flyai_poi_adapter_degrades_when_cli_is_missing(monkeypatch):
    monkeypatch.setattr("app.skills.flyai.shutil.which", lambda _: None)
    result = await FlyAIPoiAdapter().execute(
        {"city_name": "北京"},
        SkillContext(),
    )
    assert result.success is False
    assert result.error_code == "SKILL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_flyai_destination_search_adapters_execute_cli_and_keep_sources(monkeypatch):
    calls = []

    class FakeProcess:
        async def communicate(self):
            return (
                json.dumps(
                    {
                        "data": {
                            "itemList": [{
                                "info": {
                                    "title": "夫子庙",
                                    "description": "秦淮名胜",
                                    "jumpUrl": "https://example.test/poi",
                                    "picUrl": "https://example.test/poi.jpg",
                                    "rate": "4.8",
                                }
                            }]
                        }
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
                b"",
            )

    monkeypatch.setattr("app.skills.flyai.shutil.which", lambda _: "flyai")

    async def fake_create(*args, **kwargs):
        calls.append(args)
        return FakeProcess()

    monkeypatch.setattr("app.skills.flyai.asyncio.create_subprocess_exec", fake_create)
    context = SkillContext(trip_id="trip-research", metadata={"purpose": "destination_research"})
    keyword = await FlyAIKeywordSearchAdapter().execute({"query": "南京必去景点"}, context)
    semantic = await FlyAISemanticSearchAdapter().execute({"query": "南京代表性美食"}, context)

    assert keyword.success and semantic.success
    assert keyword.data["items"][0]["title"] == "夫子庙"
    assert keyword.data["items"][0]["image_url"]
    assert keyword.sources[0].provider == "FlyAI / 飞猪"
    assert calls[0][1:3] == ("keyword-search", "--query")
    assert calls[1][1:3] == ("ai-search", "--query")


@pytest.mark.asyncio
async def test_flyai_flight_adapter_normalizes_schedule(monkeypatch):
    class FakeProcess:
        async def communicate(self):
            return (
                json.dumps({
                    "data": {
                        "itemList": [{
                            "adultPrice": "¥680",
                            "jumpUrl": "https://example.test/flight",
                            "journeys": [{
                                "segments": [{
                                    "depDateTime": "2026-08-14 18:10:00",
                                    "arrDateTime": "2026-08-14 20:25:00",
                                    "depStationName": "天河机场",
                                    "arrStationName": "首都机场",
                                    "marketingTransportNo": "CA123",
                                    "marketingTransportName": "中国国航",
                                    "duration": "135分钟",
                                }],
                                "totalDuration": "135分钟",
                            }],
                        }],
                    }
                }).encode("utf-8"),
                b"",
            )

    calls = []
    monkeypatch.setattr("app.skills.flyai.shutil.which", lambda _: "flyai")

    async def fake_create(*args, **kwargs):
        calls.append(args)
        return FakeProcess()

    monkeypatch.setattr("app.skills.flyai.asyncio.create_subprocess_exec", fake_create)
    result = await FlyAIFlightAdapter().execute(
        {
            "origin": "武汉",
            "destination": "北京",
            "dep_date": "2026-08-14",
        },
        SkillContext(),
    )
    assert result.success
    assert result.data["items"][0]["flight_number"] == "CA123"
    assert result.data["items"][0]["duration_minutes"] == 135
    assert calls[0][1:3] == ("search-flight", "--origin")


@pytest.mark.asyncio
async def test_flyai_ferry_adapter_marks_semantic_schedule_estimated(monkeypatch):
    class FakeProcess:
        async def communicate(self):
            return (
                json.dumps({
                    "data": {
                        "itemList": [{
                            "info": {
                                "title": "舟山—普陀山客运轮渡",
                                "description": "08:30 开船，约 2 小时",
                                "jumpUrl": "https://example.test/ferry",
                            }
                        }]
                    }
                }).encode("utf-8"),
                b"",
            )

    monkeypatch.setattr("app.skills.flyai.shutil.which", lambda _: "flyai")
    async def fake_create(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr("app.skills.flyai.asyncio.create_subprocess_exec", fake_create)
    result = await FlyAIFerryAdapter().execute(
        {
            "origin": "舟山",
            "destination": "普陀山",
            "dep_date": "2026-08-14",
        },
        SkillContext(),
    )
    assert result.success
    assert result.data["estimated_schedule"] is True
    assert result.data["items"][0]["departure_at"].endswith("08:30:00")
    assert result.data["items"][0]["estimated"] is True
