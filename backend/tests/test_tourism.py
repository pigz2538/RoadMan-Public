import pytest

from app.planning.recommendations import rank_tourism_candidates
from app.planning.tourism import review_daily_schedule, schedule_tourism_activities, verify_tourism_plan
from app.skills.base import SkillContext
from app.skills.flyai import FlyAIHotelAdapter, FlyAIPoiAdapter, _parse_price


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

    assert [item["type"] for item in scheduled[0]["activities"]] == [
        "attraction",
        "hotel",
    ]
    assert scheduled[0]["activities"][0]["ticket_or_price"]["minimum"] == 80
    hotel = scheduled[0]["activities"][1]
    assert hotel["required"] is True
    assert hotel["planned_end"].startswith("2026-08-03")
    assert hotel["source_records"][0]["provider"] == "高德地图"
    assert verify_tourism_plan(scheduled, candidates) == []


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


@pytest.mark.asyncio
async def test_flyai_poi_adapter_degrades_when_cli_is_missing(monkeypatch):
    monkeypatch.setattr("app.skills.flyai.shutil.which", lambda _: None)
    result = await FlyAIPoiAdapter().execute(
        {"city_name": "北京"},
        SkillContext(),
    )
    assert result.success is False
    assert result.error_code == "SKILL_NOT_CONFIGURED"
