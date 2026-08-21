import json

import pytest

from app.planning.recommendations import plan_attraction_coverage, rank_tourism_candidates
from app.planning.tourism import (
    activity_checks,
    review_daily_schedule,
    schedule_tourism_activities,
    select_primary_hotel,
    verify_tourism_plan,
)
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


def test_tourism_scheduler_preserves_explicit_required_attraction():
    days = [
        {
            "id": "day_required",
            "date": "2026-08-23",
            "items": [],
            "activities": [],
            "stages": [
                {
                    "id": "stage_required",
                    "title": "公共交通前往景点",
                    "destination": {"name": "麓湖CPI", "city": "成都市"},
                    "planned_start": "2026-08-23T14:00:00+08:00",
                    "planned_end": "2026-08-23T14:30:00+08:00",
                }
            ],
        }
    ]
    candidates = {
        "attractions": [
            {
                "place": {
                    "name": "麓湖CPI",
                    "city": "成都市",
                    "coordinates": {"longitude": 104.04, "latitude": 30.46},
                },
                "user_required": True,
                "source_records": [],
            }
        ],
        "hotels": [],
        "meals": [],
    }

    scheduled = schedule_tourism_activities(days, candidates)
    attraction = next(
        item for item in scheduled[0]["activities"] if item["type"] == "attraction"
    )
    assert attraction["place"]["name"] == "麓湖CPI"
    assert attraction["required"] is True


def test_primary_hotel_prefers_city_base_over_airport_or_station_property():
    destination = {
        "name": "成都",
        "city": "成都市",
        "coordinates": {"longitude": 104.0663, "latitude": 30.5730},
    }
    hotels = [
        {
            "place": {
                "name": "如家旗下-成都双流国际机场酒店",
                "coordinates": {"longitude": 103.9819, "latitude": 30.5793},
            },
            "score": 99,
        },
        {
            "place": {
                "name": "维也纳国际酒店（宽窄巷子店）",
                "coordinates": {"longitude": 104.0547, "latitude": 30.6690},
            },
            "score": 80,
        },
    ]
    attractions = [
        {
            "place": {
                "name": "成都武侯祠",
                "coordinates": {"longitude": 104.0480, "latitude": 30.6461},
            },
            "destination_research_priority": 95,
        },
        {
            "place": {
                "name": "成都大熊猫繁育研究基地",
                "coordinates": {"longitude": 104.1379, "latitude": 30.7409},
            },
            "destination_research_priority": 100,
        },
        {
            "place": {
                "name": "麓湖CPI",
                "coordinates": {"longitude": 104.0422, "latitude": 30.4617},
            },
            "destination_research_priority": 100,
        },
        # A single out-of-town recommendation must not pull the base hotel
        # away from the city highlights.
        {
            "place": {
                "name": "都江堰景区",
                "coordinates": {"longitude": 103.6105, "latitude": 31.0034},
            },
            "destination_research_priority": 100,
        },
    ]

    selected = select_primary_hotel(hotels, destination, attractions, {"麓湖CPI"})

    assert selected is not None
    assert "机场" not in selected["place"]["name"]
    assert selected["place"]["name"] == "维也纳国际酒店（宽窄巷子店）"


def test_tourism_scheduler_never_places_destination_attraction_before_late_intercity_arrival():
    days = [
        {
            "id": "day_1",
            "date": "2026-08-07",
            "items": [],
            "activities": [],
            "stages": [
                {
                    "id": "outbound",
                    "title": "城市出发",
                    "mode": "train",
                    "sequence": 0,
                    "origin": {"name": "武汉"},
                    "destination": {"name": "北京", "city": "北京市"},
                    "planned_start": "2026-08-07T16:30:00+08:00",
                    "planned_end": "2026-08-08T00:45:00+08:00",
                }
            ],
        },
        {
            "id": "day_2",
            "date": "2026-08-08",
            "items": [],
            "activities": [],
            "stages": [
                {
                    "id": "local",
                    "title": "公共交通前往景点",
                    "mode": "transit",
                    "sequence": 0,
                    "origin": {"name": "北京"},
                    "destination": {"name": "天安门广场", "city": "北京市"},
                    "planned_start": "2026-08-08T09:30:00+08:00",
                    "planned_end": "2026-08-08T10:00:00+08:00",
                }
            ],
        },
    ]
    candidates = {
        "attractions": [
            {
                "place": {"name": "天安门广场", "city": "北京市"},
                "source_records": [{"provider": "高德地图", "title": "地点详情"}],
                "destination_research_priority": 90,
            },
            {
                "place": {"name": "故宫博物院", "city": "北京市"},
                "source_records": [{"provider": "高德地图", "title": "地点详情"}],
                "destination_research_priority": 80,
            },
        ],
        "hotels": [],
        "meals": [],
    }

    scheduled = schedule_tourism_activities(days, candidates)
    day_one_attractions = [item for item in scheduled[0]["activities"] if item["type"] == "attraction"]
    assert day_one_attractions == []
    day_two_attractions = [item for item in scheduled[1]["activities"] if item["type"] == "attraction"]
    assert day_two_attractions
    assert all(item["planned_start"].startswith("2026-08-08") for item in day_two_attractions)
    assert all(int(item["planned_start"][11:13]) >= 7 for item in day_two_attractions)


def test_tourism_scheduler_never_places_attraction_after_return_departure():
    days = [
        {
            "id": "day_1",
            "date": "2026-08-09",
            "items": [],
            "activities": [],
            "stages": [
                {
                    "id": "local",
                    "title": "步行游览接驳",
                    "mode": "walking",
                    "sequence": 0,
                    "origin": {"name": "北京"},
                    "destination": {"name": "天安门广场", "city": "北京市"},
                    "planned_start": "2026-08-09T09:00:00+08:00",
                    "planned_end": "2026-08-09T09:30:00+08:00",
                },
                {
                    "id": "return",
                    "title": "返程",
                    "mode": "train",
                    "sequence": 1,
                    "origin": {"name": "北京"},
                    "destination": {"name": "武汉", "city": "武汉市"},
                    "planned_start": "2026-08-09T12:15:00+08:00",
                    "planned_end": "2026-08-09T17:25:00+08:00",
                },
            ],
        }
    ]
    candidates = {
        "attractions": [
            {
                "place": {"name": "天安门广场", "city": "北京市"},
                "source_records": [{"provider": "高德地图", "title": "地点详情"}],
                "destination_research_priority": 90,
            },
            {
                "place": {"name": "故宫博物院", "city": "北京市"},
                "source_records": [{"provider": "FlyAI", "title": "地点详情"}],
                "destination_research_priority": 80,
            },
            {
                "place": {"name": "景山公园", "city": "北京市"},
                "source_records": [{"provider": "OpenStreetMap", "title": "地点详情"}],
                "destination_research_priority": 70,
            },
        ],
        "hotels": [],
        "meals": [],
    }

    scheduled = schedule_tourism_activities(days, candidates)
    attractions = [item for item in scheduled[0]["activities"] if item["type"] == "attraction"]
    assert all(item["planned_start"] < "2026-08-09T12:15:00+08:00" for item in attractions)


def test_activity_checks_expose_reservation_and_risk_evidence():
    checks = activity_checks(
        {
            "ticket_name": "故宫门票",
            "ticket_date": "2026-08-08",
            "source_records": [{"provider": "FlyAI / 飞猪", "title": "门票"}],
        },
        "attraction",
    )
    assert checks["reservation_status"] == "recommended"
    assert "预约" in checks["reservation_note"]
    assert "营业/开放时间待确认" in checks["risk_tags"]


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


def test_tourism_scheduler_keeps_meals_inside_long_transfer_stage():
    days = [{
        "id": "day_transfer",
        "day_index": 1,
        "date": "2026-08-02",
        "title": "第 1 天",
        "items": [],
        "activities": [],
        "stages": [{
            "id": "stage_train",
            "sequence": 0,
            "title": "城市出发",
            "mode": "train",
            "origin": {"name": "武汉"},
            "destination": {"name": "北京"},
            "planned_start": "2026-08-02T08:00:00+08:00",
            "planned_end": "2026-08-02T22:00:00+08:00",
        }],
    }]

    scheduled = schedule_tourism_activities(
        days,
        {"attractions": [], "hotels": [], "meals": []},
    )
    meals = [item for item in scheduled[0]["activities"] if item["type"] == "meal"]

    assert len(meals) == 3
    assert sum(item["in_transit"] for item in meals) == 2
    assert verify_tourism_plan(scheduled, {"attractions": [], "hotels": [], "meals": []}) == []


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
    assert result.data["items"][0]["service_number"] == "CA123"
    assert result.data["items"][0]["service_status"] == "confirmed"
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


def test_tourism_scheduler_keeps_three_meals_on_long_drive_day():
    """A long driving day must not stop the planning loop at meal review."""
    day = {
        "id": "day_long_drive",
        "title": "第 1 天",
        "date": "2026-08-24",
        "items": [],
        "activities": [
            {
                "id": "breakfast_existing",
                "day_id": "day_long_drive",
                "sequence": 0,
                "type": "meal",
                "place": {"name": "出发地附近早餐", "city": "武汉"},
                "planned_start": "2026-08-24T07:15:00+08:00",
                "planned_end": "2026-08-24T08:00:00+08:00",
                "duration_minutes": 45,
            },
            {
                "id": "rest_1",
                "day_id": "day_long_drive",
                "sequence": 1,
                "type": "rest",
                "place": {"name": "服务区休息", "city": "孝感"},
                "planned_start": "2026-08-24T09:48:00+08:00",
                "planned_end": "2026-08-24T10:08:00+08:00",
            },
            {
                "id": "rest_2",
                "day_id": "day_long_drive",
                "sequence": 2,
                "type": "rest",
                "place": {"name": "服务区休息", "city": "信阳"},
                "planned_start": "2026-08-24T11:56:00+08:00",
                "planned_end": "2026-08-24T12:16:00+08:00",
            },
            {
                "id": "charge_1",
                "day_id": "day_long_drive",
                "sequence": 3,
                "type": "charging",
                "place": {"name": "服务区充电", "city": "驻马店"},
                "planned_start": "2026-08-24T14:04:00+08:00",
                "planned_end": "2026-08-24T14:34:00+08:00",
            },
            {
                "id": "rest_3",
                "day_id": "day_long_drive",
                "sequence": 4,
                "type": "rest",
                "place": {"name": "服务区休息", "city": "漯河"},
                "planned_start": "2026-08-24T16:22:00+08:00",
                "planned_end": "2026-08-24T16:42:00+08:00",
            },
            {
                "id": "hotel_existing",
                "day_id": "day_long_drive",
                "sequence": 5,
                "type": "hotel",
                "place": {"name": "哈尔滨舒适酒店", "city": "哈尔滨"},
                "planned_start": "2026-08-24T18:30:00+08:00",
                "planned_end": "2026-08-25T09:30:00+08:00",
            },
        ],
        "stages": [
            {
                "id": "drive_1",
                "sequence": 0,
                "mode": "driving",
                "origin": {"name": "武汉"},
                "destination": {"name": "孝感"},
                "planned_start": "2026-08-24T08:00:00+08:00",
                "planned_end": "2026-08-24T09:48:00+08:00",
            },
            {
                "id": "drive_2",
                "sequence": 1,
                "mode": "driving",
                "origin": {"name": "孝感"},
                "destination": {"name": "信阳"},
                "planned_start": "2026-08-24T10:08:00+08:00",
                "planned_end": "2026-08-24T11:56:00+08:00",
            },
            {
                "id": "drive_3",
                "sequence": 2,
                "mode": "driving",
                "origin": {"name": "信阳"},
                "destination": {"name": "驻马店"},
                "planned_start": "2026-08-24T12:16:00+08:00",
                "planned_end": "2026-08-24T14:04:00+08:00",
            },
            {
                "id": "drive_4",
                "sequence": 3,
                "mode": "driving",
                "origin": {"name": "驻马店"},
                "destination": {"name": "漯河"},
                "planned_start": "2026-08-24T14:34:00+08:00",
                "planned_end": "2026-08-24T16:22:00+08:00",
            },
            {
                "id": "drive_5",
                "sequence": 4,
                "mode": "driving",
                "origin": {"name": "漯河"},
                "destination": {"name": "许昌"},
                "planned_start": "2026-08-24T16:42:00+08:00",
                "planned_end": "2026-08-24T18:30:00+08:00",
            },
        ],
    }

    scheduled = schedule_tourism_activities(
        [day],
        {"attractions": [], "hotels": [], "meals": []},
    )
    meals = [item for item in scheduled[0]["activities"] if item["type"] == "meal"]

    assert len(meals) == 3
    lunch = next(item for item in meals if "午餐" in (item.get("user_note") or ""))
    dinner = next(item for item in meals if "晚餐" in (item.get("user_note") or ""))
    assert lunch["in_transit"] is True
    assert dinner["in_transit"] is True
    assert lunch["planned_start"].startswith("2026-08-24T11:")
    assert dinner["planned_start"].startswith(("2026-08-24T17:", "2026-08-24T18:"))
    assert not any(
        issue["code"] == "DAILY_MEALS_INCOMPLETE"
        for issue in verify_tourism_plan(scheduled, {"attractions": [], "hotels": [], "meals": []})
    )
