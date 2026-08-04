from datetime import date

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
