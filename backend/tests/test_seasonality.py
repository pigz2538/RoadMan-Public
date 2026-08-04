from datetime import date

from app.planning.recommendations import apply_agent_suitability
from app.planning.seasonality import apply_seasonal_guard, assess_candidate_season


def _candidate(name: str, **extra):
    return {"candidate_id": name, "place": {"name": name}, **extra}


def test_august_rejects_obvious_cherry_blossom_and_ski_candidates():
    assert assess_candidate_season(_candidate("大幕山樱花"), date(2026, 8, 11))["seasonal_fit"] is False
    assert assess_candidate_season(_candidate("九宫山滑雪场"), date(2026, 8, 11))["seasonal_fit"] is False


def test_warm_season_keeps_rafting_and_unknown_attractions():
    assert assess_candidate_season(_candidate("九宫山漂流"), date(2026, 8, 11))["seasonal_fit"] is True
    assert assess_candidate_season(_candidate("城市博物馆"), date(2026, 8, 11))["seasonal_fit"] is None


def test_guard_keeps_unsuitable_items_visible_as_backups():
    candidates, report = apply_seasonal_guard(
        {"attractions": [_candidate("赏枫步道"), _candidate("湖畔公园")]},
        date(2026, 8, 11),
    )
    assert candidates["attractions"][0]["seasonal_excluded"] is False
    excluded = next(item for item in candidates["attractions"] if item["place"]["name"] == "赏枫步道")
    assert excluded["seasonal_excluded"] is True
    assert report[0]["name"] == "赏枫步道"


def test_confident_agent_can_override_broad_fallback_with_provider_context():
    candidates = {"attractions": [_candidate("室内滑雪馆", description="全年室内恒温雪场")]}
    candidates = apply_agent_suitability(
        candidates,
        [{
            "candidate_id": "室内滑雪馆",
            "suitable": True,
            "confidence": "high",
            "reason": "Agent 根据室内全年运营和恒温说明判断可用",
        }],
    )
    candidates, report = apply_seasonal_guard(candidates, date(2026, 8, 11))
    assert candidates["attractions"][0]["seasonal_excluded"] is False
    assert report == []
