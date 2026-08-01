from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any


def rank_tourism_candidates(
    candidates: dict[str, list[dict[str, Any]]],
    destination: dict[str, Any],
    preferences: list[str],
) -> dict[str, list[dict[str, Any]]]:
    center = destination.get("coordinates") or {}
    preference_text = " ".join(preferences).lower()
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
            name_text = f"{place.get('name', '')} {item.get('categories', '')}".lower()
            nature_preferred = any(token in preference_text for token in ("自然", "风景", "山水"))
            nature_match = any(
                token in name_text
                for token in ("山", "湖", "公园", "瀑布", "峡", "峰", "景区", "自然")
            )
            family_match = "亲子" in preference_text and any(
                token in name_text for token in ("公园", "乐园", "动物", "博物馆")
            )
            if (nature_preferred and nature_match) or family_match:
                score += 10
                reasons.append("符合旅行偏好")
            if item.get("provider") in {"FlyAI / 飞猪", "OpenTripMap"}:
                score += 3
                reasons.append("含外部旅行来源")
            item["score"] = round(max(0, min(100, score)), 1)
            item["distance_km"] = round(distance_km, 2) if distance_km is not None else None
            item["recommendation_reasons"] = reasons[:3] or ["按数据完整度排序"]
        items.sort(key=lambda item: (-item.get("score", 0), item["place"]["name"]))
        for index, item in enumerate(items):
            item["rank"] = index + 1
            item["backup"] = index > 0
            item["candidate_id"] = (
                f"{category}:{item.get('provider', 'unknown')}:"
                f"{item['place'].get('source_id') or item['place'].get('id') or index}"
            )
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
