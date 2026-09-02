from __future__ import annotations

from math import asin, ceil, cos, radians, sin, sqrt
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
            if item.get("user_required"):
                score += 40
                reasons.append("用户明确指定，必须纳入行程")
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
                item["research_area"] = recommendation.get("area") or None
                suggested_minutes = _number(recommendation.get("suggested_minutes")) or 90
                item["suggested_minutes"] = max(45, min(240, int(suggested_minutes)))
                visit_scale = str(recommendation.get("visit_scale") or "").strip().casefold()
                if visit_scale in {"major", "compact", "small", "quick", "short", "indoor", "outdoor"}:
                    item["visit_scale"] = visit_scale
                item["best_time"] = recommendation.get("best_time") or "any"
                item["must_see"] = priority >= 60
                reasons.insert(0, "目的地研究智能体标记为代表性推荐")
            item["score"] = round(max(0, min(100, score)), 1)
            item["distance_km"] = round(distance_km, 2) if distance_km is not None else None
            item["recommendation_reasons"] = reasons[:3] or ["按数据完整度排序"]
        items.sort(
            key=lambda item: (
                -1 if item.get("user_required") else 0,
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
                -1 if item.get("user_required") else 0,
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


def plan_attraction_coverage(
    candidates: list[dict[str, Any]],
    day_count: int,
) -> dict[str, Any]:
    """Assign researched highlights to balanced geographic day clusters.

    The planner should not treat a hotel as the destination's sightseeing
    boundary.  This pass groups source-backed highlights by the research
    Agent's area label when available, otherwise by a small coordinate grid,
    then assigns those groups to days with a bounded daily capacity.  It is a
    generic destination operation: no city names or attraction dictionaries
    are embedded here.
    """
    day_count = max(1, int(day_count or 1))
    priority_items = [
        item
        for item in candidates
        if item.get("place", {}).get("name")
        and not item.get("seasonal_excluded")
        and item.get("agent_suitability") is not False
        and not item.get("excluded_from_itinerary")
        and _research_priority(item) > 0
    ]
    if not priority_items:
        return {
            "priority_count": 0,
            "cluster_count": 0,
            "scheduled_capacity": 0,
            "deferred_count": 0,
        }

    daily_capacity = max(2, min(4, ceil(len(priority_items) / day_count)))
    clusters: dict[str, list[dict[str, Any]]] = {}
    for item in priority_items:
        item["coverage_cluster"] = _coverage_cluster_key(item)
        clusters.setdefault(item["coverage_cluster"], []).append(item)
    for items in clusters.values():
        items.sort(
            key=lambda candidate: (
                -_research_priority(candidate),
                -(_number(candidate.get("score")) or 0),
                str(candidate.get("place", {}).get("name") or ""),
            )
        )

    day_loads = [0] * day_count
    assigned = 0
    ordered_clusters = sorted(
        clusters.values(),
        key=lambda items: (
            -sum(_research_priority(item) for item in items),
            -len(items),
            str(items[0].get("coverage_cluster") or ""),
        ),
    )
    for cluster in ordered_clusters:
        # Keep a geographic cluster together where possible.  A very large
        # source-backed cluster is split into daily-sized chunks instead of
        # forcing an exhausting all-day loop.
        for offset in range(0, len(cluster), daily_capacity):
            chunk = cluster[offset : offset + daily_capacity]
            eligible = [
                index
                for index, load in enumerate(day_loads)
                if load + len(chunk) <= daily_capacity
            ]
            day_index = min(eligible or range(day_count), key=lambda index: day_loads[index])
            for item in chunk:
                item["coverage_day_index"] = day_index + 1
            day_loads[day_index] += len(chunk)
            assigned += len(chunk)
    return {
        "priority_count": len(priority_items),
        "cluster_count": len(clusters),
        "scheduled_capacity": assigned,
        "deferred_count": max(0, len(priority_items) - assigned),
        "daily_capacity": daily_capacity,
    }


_RESEARCH_NAME_SEPARATORS = re.compile(r"[\s\u00b7•\-—–_/|（）()【】\[\]，,。；;:：]+")

# Map providers deliberately return a broad ``景点`` search result.  The
# result may still be a nearby pharmacy, KTV, school, mall or other business.
# These are not destination-specific name rules; they are generic category
# evidence used to keep obvious search noise out of the executable attraction
# list.  A source-backed destination highlight or an explicitly requested
# place is allowed through because the traveller may intentionally visit it.
_HARD_NON_ATTRACTION_EVIDENCE_RE = re.compile(
    r"(?:ktv|karaoke|nightclub|bar|club|娱乐中心|歌厅|酒吧|夜店|休闲娱乐|"
    r"药店|药房|医院|诊所|pharmacy|clinic|hospital|medical|"
    r"装修|建材|门窗|筹建|售票处|service facility|"
    r"道路|街道|高速公路|公路|地名地址信息|交通设施|"
    r"road|street|highway|route|transportation|"
    r"旅行社|旅游服务|旅游公司|旅游咨询|旅行服务|票务代理|导游服务|"
    r"公司企业|有限公司|有限责任公司|营业部|门市部|"
    r"travel agency|tour operator|tourism service|company|corporation)",
    re.IGNORECASE,
)


# A map result named after a road facility is not an attraction even when the
# provider omitted category metadata. Keep this evidence generic (no city or
# destination names) so scenic searches cannot schedule a toll gate, service
# area or station as a three-hour visit.
_NON_ATTRACTION_LOCATION_RE = re.compile(
    r"(?:\u6536\u8d39\u7ad9|\u670d\u52a1\u533a|\u9ad8\u901f\u516c\u8def\u51fa\u53e3|\u673a\u573a|\u706b\u8f66\u7ad9|\u5730\u94c1\u7ad9|\u6c7d\u8f66\u7ad9)",
    re.IGNORECASE,
)

# AMap's ``type`` is a semicolon-delimited hierarchy, while FlyAI/OSM use
# several different field names for the same concept.  Keep the entity
# vocabulary in one place and apply it to every provider instead of relying
# on a destination-specific blacklist.  A road suffix is only a signal when
# there is no stronger scenic name (``锦里古街`` remains eligible), and an
# explicit provider road category always wins over a scenic-looking prefix
# (``青城山路`` must not become a three-hour visit).
_ROAD_CATEGORY_RE = re.compile(
    r"(?:道路|街道|高速公路|公路|路段|地名地址信息|交通设施|"
    r"road|street|highway|route|way|transportation|roadway)",
    re.IGNORECASE,
)
_BUSINESS_CATEGORY_RE = re.compile(
    r"(?:旅行社|旅游服务|旅游公司|旅游咨询|旅行服务|票务代理|导游服务|"
    r"公司企业|企业|有限公司|有限责任公司|营业部|门市部|"
    r"travel agency|tour operator|tourism service|business|company|corporation)",
    re.IGNORECASE,
)
_ATTRACTIVE_CATEGORY_RE = re.compile(
    r"(?:风景名胜|景点|景区|旅游景区|公园|博物馆|纪念馆|展览馆|文化馆|"
    r"故居|古镇|古城|长城|城墙|寺|庙|塔|园林|植物园|动物园|湿地|"
    r"森林公园|国家公园|湖泊|自然保护区|地质公园|大峡谷|瀑布|遗址|陵园|"
    r"tourism|attraction|park|museum|monument|historic|natural|viewpoint|"
    r"national park|nature reserve)",
    re.IGNORECASE,
)
_ATTRACTIVE_NAME_RE = re.compile(
    r"(?:风景区|景区|旅游区|度假区|公园|博物馆|纪念馆|展览馆|"
    r"故居|古镇|古城|长城|城墙|古街|老街|步行街|巷子|寺|庙|塔|"
    r"园林|植物园|动物园|湿地|森林公园|国家公园|湖|岛|山|"
    r"大峡谷|瀑布|遗址|陵园|宫|殿|祠|坊)$",
    re.IGNORECASE,
)
_BUSINESS_NAME_RE = re.compile(
    r"(?:旅行社|旅游服务|旅游公司|旅游咨询|旅行服务|票务代理|导游服务|"
    r"旅游门市|旅游营业部|有限公司|有限责任公司|营业部|门市部|"
    r"咨询中心|服务中心|服务部|工作室|俱乐部|代理|"
    r"travel agency|tour operator|tourism service|company|corporation)$",
    re.IGNORECASE,
)
_STANDALONE_TRAVEL_NAME_RE = re.compile(
    r"(?:旅游|旅行|旅游攻略|景点推荐)$",
    re.IGNORECASE,
)
_ROAD_NAME_RE = re.compile(
    r"(?:路|道路|街道|大道|大街|公路|高速|高速公路|环路|路段|路口|"
    r"立交|隧道|桥|桥梁|铁路|地铁线|公交线|匝道|出口)$",
    re.IGNORECASE,
)


def _flatten_entity_text(value: Any) -> str:
    """Flatten provider category values without stringifying whole records."""
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, dict):
        # Provider payloads frequently wrap a label as {name/type/value}.
        return " ".join(
            _flatten_entity_text(value.get(key))
            for key in ("name", "type", "label", "value", "text", "category", "kind")
            if value.get(key) not in (None, "", [], {})
        )
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_entity_text(item) for item in value)
    return str(value)


def _candidate_entity_evidence(candidate: dict[str, Any]) -> tuple[str, str]:
    """Return name and provider classification evidence for one candidate."""
    place = candidate.get("place") or {}
    name = str(place.get("name") or "").strip()
    fields = (
        "categories",
        "kinds",
        "type",
        "typecode",
        "type_code",
        "category",
        "category_name",
        "poi_type",
        "industry",
        "tags",
        "raw_type",
    )
    evidence = " ".join(
        _flatten_entity_text(candidate.get(field))
        for field in fields
        if candidate.get(field) not in (None, "", [], {})
    )
    # A few adapters keep the original provider response under one of these
    # keys. Read only classification fields, never arbitrary descriptions or
    # user text, so a review note cannot accidentally classify a POI.
    for container_key in ("provider_payload", "raw", "raw_item"):
        container = candidate.get(container_key)
        if isinstance(container, dict):
            evidence = " ".join(
                part
                for part in (
                    evidence,
                    *(
                        _flatten_entity_text(container.get(field))
                        for field in fields
                        if container.get(field) not in (None, "", [], {})
                    ),
                )
                if part
            )
    return name, evidence


def classify_candidate_entity(candidate: dict[str, Any]) -> dict[str, str]:
    """Classify a provider candidate without interpreting traveller intent.

    The planner receives heterogeneous POI records.  This classifier is a
    conservative integrity gate: it rejects only high-confidence roads,
    travel agencies and business/service records, while leaving ambiguous
    names for the ranking and suitability Agents.  It intentionally has no
    destination or landmark catalogue, so the same logic works for every
    city, language and provider.
    """
    name, evidence = _candidate_entity_evidence(candidate)
    combined = f"{evidence} {name}".strip()
    has_attraction_category = bool(_ATTRACTIVE_CATEGORY_RE.search(evidence))
    has_road_category = bool(_ROAD_CATEGORY_RE.search(evidence))
    has_business_category = bool(_BUSINESS_CATEGORY_RE.search(evidence))
    # Apply the hard service test to the returned name as well.  Some travel
    # search adapters omit categories and append annotations such as
    # ``（旅行社名称）`` to the title; that annotation is provider evidence,
    # not a user requirement.
    has_hard_service = bool(_HARD_NON_ATTRACTION_EVIDENCE_RE.search(combined))
    has_location_service = bool(_NON_ATTRACTION_LOCATION_RE.search(combined))
    has_business_name = bool(_BUSINESS_NAME_RE.search(name))
    has_standalone_travel_name = bool(_STANDALONE_TRAVEL_NAME_RE.search(name))
    has_road_name = bool(_ROAD_NAME_RE.search(name))
    has_attractive_name = bool(_ATTRACTIVE_NAME_RE.search(name))

    if has_hard_service or has_location_service:
        return {
            "entity_class": "service_or_facility",
            "confidence": "high",
            "reason": "来源类别或地点名称明确指向服务设施，而非可游览景点",
        }
    if has_road_category or (has_road_name and not has_attractive_name):
        return {
            "entity_class": "road_or_transport",
            "confidence": "high",
            "reason": "来源类别或地点名称明确指向道路、路段或交通设施",
        }
    if has_business_category or has_business_name or has_standalone_travel_name:
        # A company can manage a scenic area, but the company itself is not a
        # visitable POI. ``旅游区/旅游景区`` is intentionally not matched by
        # the standalone/business suffix checks, so genuine scenic areas pass.
        return {
            "entity_class": "business_or_travel_agency",
            "confidence": "high",
            "reason": "来源类别或地点名称明确指向旅行社、企业或商业服务",
        }
    if has_attraction_category or has_attractive_name:
        return {
            "entity_class": "attraction",
            "confidence": "medium" if has_attractive_name and not has_attraction_category else "high",
            "reason": "来源类别或地点名称包含可游览景点证据",
        }
    return {
        "entity_class": "unknown",
        "confidence": "low",
        "reason": "暂未获得足够实体类别证据，交由排序与适配智能体复核",
    }


def apply_candidate_type_guard(
    candidates: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Mark obvious non-attraction provider hits as unsuitable.

    This is a data-quality check after model review, not an intent parser. It
    uses provider category metadata plus unmistakable returned-name suffixes.
    Research-backed ambiguous landmarks and user-selected places are
    preserved; high-confidence roads, agencies and service facilities are
    marked for quarantine with an explainable reason.
    """
    for item in candidates.get("attractions", []):
        if not isinstance(item, dict):
            continue
        classification = classify_candidate_entity(item)
        item["entity_class"] = classification["entity_class"]
        item["entity_confidence"] = classification["confidence"]
        item["entity_reason"] = classification["reason"]
        # Explicit user choices are retained for a later human confirmation
        # step, but still carry the classification so the UI can explain why
        # a provider called a requested item a road or a business.
        if item.get("user_required") or item.get("user_confirmed"):
            item["entity_user_override"] = True
            continue
        if classification["entity_class"] not in {
            "service_or_facility",
            "road_or_transport",
            "business_or_travel_agency",
        }:
            continue
        item["agent_suitability"] = False
        item["suitability_confidence"] = "high"
        item["suitability_reason"] = (
            f"{classification['reason']}，已保留为备选而不纳入景点排程"
        )
        item["category_guarded"] = True
        item["excluded_from_itinerary"] = True
    return candidates


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


def _research_priority(candidate: dict[str, Any]) -> float:
    try:
        return float(candidate.get("destination_research_priority") or 0)
    except (TypeError, ValueError):
        return 0.0


def _coverage_cluster_key(candidate: dict[str, Any]) -> str:
    area = str(candidate.get("research_area") or "").strip()
    if area:
        return f"area:{_normalise_research_name(area)}"
    coordinates = (candidate.get("place") or {}).get("coordinates") or {}
    try:
        # About 3–5 km at common Chinese city latitudes; enough to keep
        # neighboring landmarks in one visit block without hotel anchoring.
        longitude = round(float(coordinates["longitude"]) / 0.04)
        latitude = round(float(coordinates["latitude"]) / 0.04)
        return f"grid:{longitude}:{latitude}"
    except (KeyError, TypeError, ValueError):
        return "unknown"


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
