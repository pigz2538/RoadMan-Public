import pytest

from app.planning.tourism import schedule_tourism_activities, verify_tourism_plan
from app.skills.base import SkillContext
from app.skills.flyai import FlyAIHotelAdapter, _parse_price


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
