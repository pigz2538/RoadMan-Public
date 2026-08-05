from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
import re
from typing import Any


def rank_tourism_candidates(
    candidates: dict[str, list[dict[str, Any]]],
    destination: dict[str, Any],
    preferences: list[str],
    destination_research: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    research_recommendations = _research_recommendations(destination_research)
    center = destination.get("coordinates") or {}
    for category, items in candidates.items():
        for item in items:
            place = item.get("place") or {}
            coordinates = place.get("coordinates") or {}
            distance_km = _distance_km(center, coordinates)
            rating = _number(item.get("rating"))
            price = item.get("ticket_or_price") or {}
            price_mid = (
                (_number(price.get("minimum")) + _number(price.get("maximum"))) / 2
                if price and _number(price.get("minimum")) is not None
                and _number(price.get("maximum")) is not None
                else None
            )
            score = 55.0
            reasons: list[str] = []
            if rating is not None:
                score += min(20, rating * 4)
                reasons.append(f"评分 {rating:g}")
            if distance_km is not None:
                score += max(-18, 16 - distance_km * 0.8)
                reasons.append(f"距目的地约 {distance_km:.1f} km")
            if price_mid is not None:
                score += max(-10, 8 - price_mid / 80)
                reasons.append(f"价格约 ¥{price_mid:.0f}")
            if item.get("provider") in {"FlyAI / 飞猪", "OpenTripMap"}:
                score += 3
                reasons.append("含外部旅行来源")
            recommendation = _match_research_recommendation(
                item.get("research_hint_name") or item.get("place", {}).get("name"),
                research_recommendations.get(category, []),
            )
            if recommendation:
                # A source-backed city highlight must outrank an obscure POI
                # merely because it happens to be close to the hotel.  Keep
                # the Agent's importance visible so the itinerary and the
                # local-route selector can preserve city-wide coverage.
                importance = float(recommendation.get("importance") or 0)
                priority = max(1.0, min(100.0, importance))
                score += 24 + priority * 0.22
                item["destination_research_priority"] = round(priority, 1)
                item["destination_research_name"] = recommendation["name"]
                item["destination_research_reason"] = recommendation.get("reason")
                item["must_see"] = priority >= 60
                reasons.insert(0, "目的地研究 Agent 标记为代表性推荐")
            item["score"] = round(max(0, min(100, score)), 1)
            item["distance_km"] = round(distance_km, 2) if distance_km is not None else None
            item["recommendation_reasons"] = reasons[:3] or ["按数据完整度排序"]
        items.sort(
            key=lambda item: (
                -float(item.get("destination_research_priority") or 0),
                -float(item.get("score") or 0),
                item["place"]["name"],
            )
        )
        for index, item in enumerate(items):
            item["rank"] = index + 1
            item["backup"] = index > 0
            item["candidate_id"] = (
                f"{category}:{item.get('provider', 'unknown')}:"
                f"{item['place'].get('source_id') or item['place'].get('id') or index}"
            )
    return candidates


def apply_agent_ranking(
    candidates: dict[str, list[dict[str, Any]]],
    decisions: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Apply validated Agent scores without allowing unknown IDs to mutate data."""
    by_id = {
        str(decision.get("candidate_id")): decision
        for decision in decisions
        if decision.get("candidate_id")
    }
    for category, items in candidates.items():
        for item in items:
            decision = by_id.get(str(item.get("candidate_id")))
            if not decision:
                continue
            item["agent_score"] = decision["score"]
            item["agent_reason"] = decision["reason"]
            item["score"] = decision["score"]
            if decision.get("seasonal_fit") is not None:
                item["seasonal_fit"] = decision["seasonal_fit"]
            if decision.get("seasonal_reason"):
                item["agent_seasonal_reason"] = decision["seasonal_reason"]
            item["recommendation_reasons"] = [decision["reason"]]
        items.sort(
            key=lambda item: (
                -float(item.get("destination_research_priority") or 0),
                -(item.get("agent_score") if item.get("agent_score") is not None else item.get("score", 0)),
                -item.get("score", 0),
                item["place"]["name"],
            )
        )
        for index, item in enumerate(items):
            item["rank"] = index + 1
            item["backup"] = index > 0
    return candidates


_RESEARCH_NAME_SEPARATORS = re.compile(r"[\s\u00b7•\-—–_/|（）()【】\[\]，,。；;:：]+")


def _normalise_research_name(value: Any) -> str:
    """Normalize names only for source-backed POI identity matching.

    This is not an intent/requirement keyword parser.  It is deliberately
    limited to punctuation, whitespace and case so a researched name such as
    “南京大学” can match a provider label like “南京大学鼓楼校区”.
    """
    return _RESEARCH_NAME_SEPARATORS.sub("", str(value or "")).casefold()


def _research_recommendations(
    destination_research: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"attractions": [], "meals": []}
    for item in (destination_research or {}).get("agent_recommendations", []):
        if not isinstance(item, dict):
            continue
        category = item.get("category")
        name = str(item.get("name") or "").strip()
        if category not in result or not name:
            continue
        result[category].append(item)
    return result


def _match_research_recommendation(
    candidate_name: Any,
    recommendations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidate_key = _normalise_research_name(candidate_name)
    if len(candidate_key) < 3:
        return None
    matches: list[dict[str, Any]] = []
    for recommendation in recommendations:
        recommendation_key = _normalise_research_name(recommendation.get("name"))
        if len(recommendation_key) < 3:
            continue
        if candidate_key == recommendation_key or candidate_key in recommendation_key or recommendation_key in candidate_key:
            matches.append(recommendation)
    return max(matches, key=lambda item: float(item.get("importance") or 0), default=None)


def apply_agent_suitability(
    candidates: dict[str, list[dict[str, Any]]],
    decisions: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Attach per-candidate condition checks without deleting alternatives."""
    by_id = {
        str(decision.get("candidate_id")): decision
        for decision in decisions
        if decision.get("candidate_id")
    }
    for items in candidates.values():
        for item in items:
            decision = by_id.get(str(item.get("candidate_id")))
            if not decision:
                continue
            item["agent_suitability"] = bool(decision["suitable"])
            item["suitability_confidence"] = decision.get("confidence", "low")
            item["suitability_reason"] = decision.get("reason")
            item["weather_fit_reason"] = decision.get("weather_reason")
            item["terrain_fit_reason"] = decision.get("terrain_reason")
            item["personal_fit_reason"] = decision.get("personal_reason")
    return candidates


def _distance_km(
    first: dict[str, Any],
    second: dict[str, Any],
) -> float | None:
    try:
        lon1, lat1 = float(first["longitude"]), float(first["latitude"])
        lon2, lat2 = float(second["longitude"]), float(second["latitude"])
    except (KeyError, TypeError, ValueError):
        return None
    lon_delta = radians(lon2 - lon1)
    lat_delta = radians(lat2 - lat1)
    value = (
        sin(lat_delta / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(lon_delta / 2) ** 2
    )
    return 6371.0088 * 2 * asin(sqrt(value))


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
