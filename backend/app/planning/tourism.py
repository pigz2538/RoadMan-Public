from __future__ import annotations

from datetime import datetime, time, timedelta
import re
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from .poi_enrichment import _sanitize_candidate_copy

SHANGHAI = ZoneInfo("Asia/Shanghai")

_INCOMFORTABLE_LODGING_RE = re.compile(
    r"(?:青旅|青年旅舍|青年旅社|青年公寓|学生公寓|旅舍|背包客栈|hostel|backpacker)",
    re.IGNORECASE,
)

# Keep the comfort filter semantic rather than tied to one provider's spelling.
# Search providers use several variants for dormitory/hostel-style properties;
# all of them must be excluded from the default comfortable base selection.
_DISALLOWED_COMFORT_LODGING_RE = re.compile(
    r"(?:青年旅店|青年旅馆|青年客栈|学生宿舍|宿舍型|床位房|胶囊旅馆|太空舱)",
    re.IGNORECASE,
)

# An airport/railway hotel is a useful fallback for a very early departure,
# but it is a poor default base for a multi-day city itinerary.  Previously a
# provider could return a property whose name contained ``双流国际机场`` or
# ``离堆公园高铁站`` and the distance scorer would happily make it the base for
# every day.  Keep the rule data-driven: explicitly confirmed hotels still win,
# and a transit hotel is used only when no ordinary lodging candidate exists.
_TRANSIT_FOCUSED_LODGING_RE = re.compile(
    r"(?:机场|航站楼|火车站|高铁站|动车站|铁路站|客运站|汽车站|长途站)",
    re.IGNORECASE,
)


def _hotel_text(candidate: dict[str, Any]) -> str:
    place = candidate.get("place") or {}
    return " ".join(str(place.get(key) or "") for key in ("name", "address"))


def _comfortable_hotel(
    candidate: dict[str, Any],
    *,
    allow_transit: bool = False,
) -> bool:
    """Return whether a lodging candidate is suitable as a trip base.

    Hostels are never selected by the comfort default.  Properties named after
    an airport/station are excluded from the normal city-base pool unless the
    traveller explicitly confirmed that property or there is no alternative.
    """
    text = _hotel_text(candidate)
    if _INCOMFORTABLE_LODGING_RE.search(text) or _DISALLOWED_COMFORT_LODGING_RE.search(text):
        return False
    if (
        not allow_transit
        and not candidate.get("user_confirmed")
        and not candidate.get("user_requested")
        and _TRANSIT_FOCUSED_LODGING_RE.search(text)
    ):
        return False
    return True


def _candidate_quality(candidate: dict[str, Any]) -> tuple[float, float, float, str]:
    try:
        score = float(candidate.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        rating = float(candidate.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    return (
        0.0 if candidate.get("user_confirmed") else 1.0,
        -score,
        -rating,
        str((candidate.get("place") or {}).get("name") or ""),
    )


def _attraction_priority(candidate: dict[str, Any]) -> float:
    try:
        return float(candidate.get("destination_research_priority") or 0)
    except (TypeError, ValueError):
        return 0.0


def _attraction_sort_key(candidate: dict[str, Any]) -> tuple[float, float, float, str]:
    """Keep source-backed city highlights ahead of nearby generic POIs."""
    try:
        score = float(candidate.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        rating = float(candidate.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    return (
        -_attraction_priority(candidate),
        -score,
        -rating,
        str((candidate.get("place") or {}).get("name") or ""),
    )


def _suggested_duration(candidate: dict[str, Any], default: int) -> int:
    """Return a comfortable visit window for one attraction.

    Research agents can provide a measured duration. In its absence, a
    recognized landmark receives a 3-hour baseline so the scheduler does not
    turn a full-day scenic visit into a string of one-hour photo stops. A
    compact/quick venue may opt out explicitly via ``visit_scale`` or
    ``compact_visit``; this keeps the rule semantic rather than a place-name
    table.
    """
    # A user-named/confirmed place is the traveller's primary reason for the
    # trip.  Even if a provider labels it ``compact``, never compress it into
    # a photo stop: the route builder must reserve a proper daytime block.
    user_priority = bool(candidate.get("user_required") or candidate.get("user_confirmed"))
    scale = str(
        candidate.get("visit_scale")
        or candidate.get("scale")
        or candidate.get("visit_size")
        or ""
    ).strip().casefold()
    compact = (not user_priority) and (
        bool(candidate.get("compact_visit")) or scale in {
        "compact",
        "small",
        "quick",
        "short",
        }
    )
    try:
        raw = int(candidate.get("suggested_minutes") or default)
    except (TypeError, ValueError):
        raw = int(default or 180)
    if compact:
        return max(45, min(150, raw))
    # Keep a research-provided four-hour visit, but lift underspecified or
    # suspiciously short defaults to a comfortable three-hour block.
    return max(180, min(240, raw))


def _attraction_is_schedulable(candidate: dict[str, Any] | None) -> bool:
    """Return whether a reviewed POI may become an executable stop.

    Suitability decisions deliberately do not delete provider results: the
    UI can still show them as alternatives and explain why they were rejected.
    Only an explicit user requirement/confirmation can override a negative
    suitability decision for the actual itinerary.
    """
    if not candidate:
        return False
    if candidate.get("seasonal_excluded") and not (
        candidate.get("user_required") or candidate.get("user_confirmed")
    ):
        return False
    if candidate.get("agent_suitability") is False and not (
        candidate.get("user_required") or candidate.get("user_confirmed")
    ):
        return False
    return True


def _comfortable_visit_minimum(candidate: dict[str, Any] | None) -> int:
    """Return the minimum useful scenic visit block for a candidate."""
    item = candidate or {}
    if item.get("user_required") or item.get("user_confirmed"):
        return 180
    scale = str(
        item.get("visit_scale")
        or item.get("scale")
        or item.get("visit_size")
        or ""
    ).strip().casefold()
    if item.get("compact_visit") or scale in {"compact", "small", "quick", "short"}:
        return 45
    return 180


def _stage_is_non_scenic(stage: dict[str, Any] | None) -> bool:
    """Return whether a stage is clearly a transfer/check-in endpoint."""
    if not isinstance(stage, dict):
        return True
    title = str(stage.get("title") or "")
    return any(
        token in title
        for token in (
            "住宿",
            "酒店",
            "入住",
            "返回",
            "返程",
            "城市出发",
            "机场",
            "火车",
            "高铁",
            "车站",
        )
    )


def _stage_can_host_attraction(stage: dict[str, Any] | None) -> bool:
    """Return whether a movement stage is an attraction-bound leg.

    Destination names are often embedded in hotel/store names (for example
    ``宽窄巷子店``).  Containment matching against every stage therefore used
    to attach a scenic activity to a hotel check-in leg.  Only legs explicitly
    describing an attraction visit may use fuzzy source matching; exact
    required-place matches are still handled by the requirement pass.
    """
    if not isinstance(stage, dict):
        return False
    if _stage_is_non_scenic(stage):
        return False
    title = str(stage.get("title") or "")
    return any(token in title for token in ("景点", "景区", "游览", "参观", "前往"))


def _shift_stage_chain(
    stages: list[dict[str, Any]],
    start_index: int,
    delta: timedelta,
) -> None:
    """Move a local stage and every following stage by one continuous delta.

    Route geometry and durations stay unchanged; only the calendar slot moves.
    Keeping the suffix together prevents the return-to-hotel leg from being
    pushed through a later airport/rail departure and creating a fake overlap.
    """
    if delta <= timedelta(0):
        return
    for stage in stages[start_index:]:
        try:
            start = datetime.fromisoformat(stage["planned_start"]) + delta
            end = datetime.fromisoformat(stage["planned_end"]) + delta
        except (KeyError, TypeError, ValueError):
            continue
        stage["planned_start"] = start.isoformat()
        stage["planned_end"] = end.isoformat()


def _ensure_anchor_visit_windows(
    stages: list[dict[str, Any]],
    *,
    day_date: date,
    return_cutoff: datetime | None,
    source_for_stage: Any,
) -> None:
    """Reserve a genuine scenic window before the return-to-base connector.

    A route builder can put the outbound and return connectors only a little
    over an hour apart.  That leaves a misleading one-hour ``景点停留`` card
    even though the request is explicitly centred on a named scenic anchor.
    When there is room in the same day, move the return suffix as a unit so
    the scheduler can place a three-hour visit.  If a terminal deadline leaves
    no room, keep the original timing and let the verifier explain the limit.
    """
    for index, stage in enumerate(stages):
        if index == 0 or "返回住宿或目的地核心区" not in str(stage.get("title") or ""):
            continue
        previous = stages[index - 1]
        candidate = source_for_stage(previous)
        if not candidate or not _attraction_is_schedulable(candidate):
            continue
        try:
            previous_end = datetime.fromisoformat(previous["planned_end"])
            return_start = datetime.fromisoformat(stage["planned_start"])
        except (KeyError, TypeError, ValueError):
            continue
        if previous_end.date() != day_date or return_start.date() != day_date:
            continue
        # The scheduler keeps a 15-minute handoff before the next movement
        # stage. Reserve that handoff in addition to the actual scenic visit
        # so a nominal three-hour window is not silently truncated to 165m.
        required_gap = timedelta(
            minutes=_comfortable_visit_minimum(candidate) + 15
        )
        missing = required_gap - (return_start - previous_end)
        if missing <= timedelta(0):
            continue
        latest_allowed = return_cutoff or datetime.combine(
            day_date,
            time(21, 30),
            tzinfo=return_start.tzinfo,
        )
        try:
            suffix_end = max(
                datetime.fromisoformat(item["planned_end"])
                for item in stages[index:]
            )
        except (KeyError, TypeError, ValueError):
            suffix_end = return_start
        available = latest_allowed - suffix_end
        if available <= timedelta(0):
            continue
        _shift_stage_chain(stages, index, min(missing, available))


def _ensure_scenic_visit_windows(
    stages: list[dict[str, Any]],
    *,
    day_date: date,
    return_cutoff: datetime | None,
    source_for_stage: Any,
) -> None:
    """Reserve a comfortable dwell for every researched scenic stage.

    Movement builders normally leave only a small hand-off gap between two
    legs.  If that gap is used verbatim, a major attraction becomes a
    misleading 45--90 minute photo stop.  Shift the following suffix as one
    unit so each eligible stop gets a real three-hour window (or its explicit
    compact-venue duration), while respecting the day boundary and a booked
    intercity departure.
    """
    for index, stage in enumerate(stages[:-1]):
        candidate = source_for_stage(stage)
        if not candidate or not _attraction_is_schedulable(candidate):
            continue
        try:
            visit_end = datetime.fromisoformat(stage["planned_end"])
            next_start = datetime.fromisoformat(stages[index + 1]["planned_start"])
        except (KeyError, TypeError, ValueError):
            continue
        if visit_end.date() != day_date or next_start.date() != day_date:
            continue
        required_gap = timedelta(minutes=_comfortable_visit_minimum(candidate) + 15)
        missing = required_gap - (next_start - visit_end)
        if missing <= timedelta(0):
            continue
        latest_allowed = return_cutoff or datetime.combine(
            day_date,
            time(21, 30),
            tzinfo=next_start.tzinfo,
        )
        try:
            suffix_end = max(
                datetime.fromisoformat(item["planned_end"])
                for item in stages[index + 1 :]
            )
        except (KeyError, TypeError, ValueError):
            suffix_end = next_start
        available = latest_allowed - suffix_end
        if available > timedelta(0):
            _shift_stage_chain(stages, index + 1, min(missing, available))


def activity_checks(
    candidate: dict[str, Any] | None,
    activity_type: str,
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> dict[str, Any]:
    """Return visible booking and safety checks for one itinerary item.

    Provider data is deliberately kept as evidence: a missing ticket/booking
    record is reported as ``unknown`` rather than silently treated as safe.
    The planner can therefore show the traveller what still needs checking
    without turning a provider outage into a false hard failure.
    """
    item = candidate or {}
    reservation = str(item.get("reservation_status") or "").strip().lower()
    if reservation not in {"required", "recommended", "not_required", "unknown"}:
        reservation = ""
    reservation_note = str(item.get("reservation_note") or "").strip() or None
    if not reservation:
        if activity_type == "hotel":
            reservation = "required"
            reservation_note = reservation_note or "住宿需要确认入住日期、房态、取消政策与实名要求"
        elif activity_type == "meal":
            reservation = "recommended"
            reservation_note = reservation_note or "热门餐厅建议提前预约，并在当天确认营业与排队情况"
        elif activity_type == "attraction":
            # A dated ticket or a ticket product is provider evidence that the
            # venue has an admission/booking flow, but it is not proof that
            # every visit requires a reservation.
            if item.get("ticket_date") or item.get("ticket_name"):
                reservation = "recommended"
                reservation_note = reservation_note or "已找到门票/票务信息，请核对预约、实名和入园时段"
            elif item.get("source_records"):
                reservation = "unknown"
                reservation_note = reservation_note or "已找到地点来源，未发现明确预约规则，请查看官方公告"
            else:
                reservation = "unknown"
                reservation_note = reservation_note or "暂无可验证预约信息，请出发前查看官方渠道"
        else:
            reservation = "unknown"
            reservation_note = reservation_note or "暂无预约核查结果"

    risk_tags: list[str] = []
    risk_note = str(item.get("risk_note") or "").strip() or None
    if item.get("seasonal_excluded") is True or item.get("agent_suitability") is False:
        risk_tags.append("时令/条件不适配")
        risk_note = risk_note or str(
            item.get("suitability_reason")
            or item.get("seasonal_warning")
            or "智能体复核认为当前日期或条件不适合"
        )
    if item.get("suitability_confidence") in {"low", None} and item.get("agent_suitability") is not True:
        risk_tags.append("适配性待确认")
    if not item.get("source_records"):
        risk_tags.append("来源不足")
        risk_note = risk_note or "没有可追溯的公开来源，建议出发前再次核验"
    if activity_type == "attraction" and reservation == "unknown":
        risk_tags.append("预约规则待核查")
        risk_note = risk_note or "请查看景区官方预约、实名和分时入园规则"
    opening = item.get("opening_hours")
    if not isinstance(opening, dict) or opening.get("confirmed") is not True:
        risk_tags.append("营业/开放时间待确认")
        risk_note = risk_note or "开放时间以出发日前官方公告为准"
    if start_at is not None:
        if activity_type == "attraction" and (start_at.hour < 7 or start_at.hour >= 22):
            risk_tags.append("非舒适时段")
            risk_note = risk_note or "景点已被排到夜间，规划器会将其移动到白天"
        if activity_type != "hotel" and end_at is not None and end_at.date() != start_at.date():
            risk_tags.append("跨日活动")
            risk_note = risk_note or "活动跨越日历日，需重新核对营业和交通"
    risk_tags = list(dict.fromkeys(risk_tags))
    if item.get("risk_level") in {"low", "moderate", "high"}:
        risk_level = item["risk_level"]
    elif any(tag in {"时令/条件不适配", "非舒适时段", "跨日活动"} for tag in risk_tags):
        risk_level = "high"
    elif risk_tags or reservation == "unknown":
        risk_level = "moderate"
    else:
        risk_level = "low"
    return {
        "reservation_status": reservation,
        "reservation_note": reservation_note,
        "risk_level": risk_level,
        "risk_tags": risk_tags,
        "risk_note": risk_note,
    }


def _attraction_name_key(value: Any) -> str:
    return re.sub(r"[\s\u00b7•\-—–_/|（）()【】\[\]，,。；;:：]+", "", str(value or "")).casefold()


def _place_distance_km(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float | None:
    if not left or not right:
        return None
    first = left.get("coordinates") or {}
    second = right.get("coordinates") or {}
    try:
        from math import asin, cos, radians, sin, sqrt

        lon1, lat1 = float(first["longitude"]), float(first["latitude"])
        lon2, lat2 = float(second["longitude"]), float(second["latitude"])
        dlon, dlat = radians(lon2 - lon1), radians(lat2 - lat1)
        value = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 6371.0088 * 2 * asin(sqrt(value))
    except (KeyError, TypeError, ValueError):
        return None


def _hotel_for_day(
    hotels: list[dict[str, Any]],
    anchor: dict[str, Any] | None,
    previous: list[tuple[dict[str, Any], dict[str, Any] | None]],
) -> dict[str, Any] | None:
    """Reuse one hotel while consecutive days stay in the same area."""
    pool = [item for item in hotels if _comfortable_hotel(item)]
    # A provider outage should not erase accommodation from the itinerary. If
    # every returned property is attached to a terminal, keep one as a
    # visibly sub-optimal fallback rather than reverting to a city-centre
    # coordinate with no hotel record at all.
    if not pool:
        pool = [item for item in hotels if _comfortable_hotel(item, allow_transit=True)]
    if not pool:
        return None
    for selected, _selected_anchor in reversed(previous):
        selected_place = selected.get("place") or {}
        same_city = bool(
            anchor
            and selected_place.get("city")
            and str(selected_place.get("city")).casefold() == str(anchor.get("city") or "").casefold()
        )
        close = _place_distance_km(selected_place, anchor)
        if same_city or (close is not None and close <= 20):
            return selected
    def distance_key(item: dict[str, Any]) -> tuple[float, float, tuple[float, float, float, str]]:
        distance = _place_distance_km(item.get("place"), anchor)
        return (
            0.0 if item.get("user_confirmed") else 1.0,
            distance if distance is not None else 9999,
            _candidate_quality(item),
        )

    return min(pool, key=distance_key)


def select_primary_hotel(
    hotels: list[dict[str, Any]],
    destination: dict[str, Any] | None,
    attractions: list[dict[str, Any]] | None = None,
    required_names: set[str] | None = None,
) -> dict[str, Any] | None:
    """Choose a comfortable base hotel for the whole destination stay.

    The route builder runs before the activity scheduler, so it needs the same
    hotel decision in order to start/end each local day at the booked property.
    Rank by the median distance to the destination and deduplicated researched
    highlights, then use provider quality as a tie-breaker. This deliberately
    avoids choosing an airport or railway-station hotel merely because the
    city geocode or one out-of-town recommendation happens to be there.
    """
    pool = [item for item in hotels if _comfortable_hotel(item)]
    if not pool:
        pool = [item for item in hotels if _comfortable_hotel(item, allow_transit=True)]
    if not pool:
        return None
    confirmed = [item for item in pool if item.get("user_confirmed")]
    if confirmed:
        pool = confirmed
    anchors: list[dict[str, Any]] = []
    if destination:
        anchors.append(destination)

    # Research providers often return several entrance/metro/parking records
    # for one landmark.  Deduplicate nearby variants before scoring, otherwise
    # one noisy POI cluster can pull the lodging base to the wrong edge of the
    # city. Required places are considered first, followed by the ranked city
    # highlights; the selection remains data-driven and does not contain a
    # city-specific radius or name catalogue.
    required_keys = {
        _attraction_name_key(value)
        for value in (required_names or set())
        if _attraction_name_key(value)
    }
    ordered_attractions = sorted(
        list(attractions or []),
        key=lambda item: (
            0 if _attraction_name_key((item.get("place") or {}).get("name")) in required_keys else 1,
            *_attraction_sort_key(item),
        ),
    )
    seen_names: set[str] = set()
    for candidate in ordered_attractions:
        if candidate.get("seasonal_excluded"):
            continue
        place = candidate.get("place") or {}
        if place.get("coordinates"):
            name_key = _attraction_name_key(place.get("name"))
            if name_key and name_key in seen_names:
                continue
            # Do not count a second entrance/metro point as a separate anchor
            # when it sits within roughly one kilometre of an existing point.
            if any(_place_distance_km(place, existing) is not None and _place_distance_km(place, existing) <= 1.0 for existing in anchors):
                continue
            anchors.append(place)
            if name_key:
                seen_names.add(name_key)
        if len(anchors) >= 13:
            break

    def key(item: dict[str, Any]) -> tuple[float, tuple[float, float, float, str]]:
        place = item.get("place") or {}
        distances = [
            distance
            for anchor in anchors
            if (distance := _place_distance_km(place, anchor)) is not None
        ]
        # A missing coordinate is kept as a last-resort candidate, never a
        # reason to drop accommodation altogether.
        # Median distance is deliberately used instead of a raw average. A
        # three-day Chengdu trip may contain one optional out-of-town result
        # (for example Dujiangyan); that result must not make an airport or
        # remote railway-station hotel look like the best city base.
        typical = float(median(distances)) if distances else 9999.0
        transit_penalty = 1000.0 if _TRANSIT_FOCUSED_LODGING_RE.search(_hotel_text(item)) else 0.0
        return typical + transit_penalty, _candidate_quality(item)

    selected = min(pool, key=key)
    selected_distance = _place_distance_km(selected.get("place"), destination)
    if (
        selected_distance is not None
        and selected_distance > 120
        and not selected.get("user_confirmed")
        and not selected.get("user_requested")
    ):
        # A remote provider hit is not a usable city base. Returning ``None``
        # lets the graph create a route-derived core-area lodging placeholder
        # at the actual gateway instead of forcing a 200+ km hotel transfer.
        return None
    return selected


def schedule_tourism_activities(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
    confirmed_additions: list[dict[str, Any]] | None = None,
    destination: dict[str, Any] | None = None,
    trip_request: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach executable attraction and overnight hotel activities to day plans."""
    # A confirmed edit is a user decision, not merely another provider hit.
    # Mark it before selecting the base hotel so a map-selected property wins
    # the same pass that builds movement stages. Otherwise the route can use
    # one hotel while the visible activity silently uses another (or a hotel
    # selected on the return day is dropped as a non-overnight item).
    for record in confirmed_additions or []:
        if not isinstance(record, dict):
            continue
        category = str(record.get("category") or "").strip()
        candidate = record.get("candidate")
        if category not in {"hotels", "meals", "attractions"} or not isinstance(candidate, dict):
            continue
        candidate["user_confirmed"] = True
        candidate["user_requested"] = True
        candidate_id = str(candidate.get("candidate_id") or "")
        name = str((candidate.get("place") or {}).get("name") or "").strip()
        for existing in candidates.setdefault(category, []):
            if (
                candidate_id
                and str(existing.get("candidate_id") or "") == candidate_id
            ) or (
                name
                and str((existing.get("place") or {}).get("name") or "").strip() == name
            ):
                existing.update(candidate)
                break
    hotels = [item for item in candidates.get("hotels", []) if _comfortable_hotel(item)]
    # A travel-platform result can be a valid hotel but still be hundreds of
    # kilometres from the selected gateway (common when the destination is a
    # province/region). Do not use that outlier as the daily base. The graph
    # passes the resolved destination so a local estimated hotel can be used
    # instead of creating a midnight arrival or a map jump.
    if destination and hotels:
        nearest = min(
            (
                _place_distance_km(item.get("place"), destination)
                for item in hotels
                if _place_distance_km(item.get("place"), destination) is not None
            ),
            default=None,
        )
        if nearest is not None and nearest > 120 and not any(
            item.get("user_confirmed") or item.get("user_requested") for item in hotels
        ):
            hotels = []
    if not hotels:
        # Keep accommodation visible even during provider degradation. This
        # is an estimated booking placeholder at the resolved destination
        # anchor, never an invented named property or a hostel.
        fallback_place = dict(destination or {})
        if not fallback_place.get("coordinates"):
            fallback_place = {}
            for day in day_plans:
                for stage in day.get("stages", []):
                    candidate_place = stage.get("destination") or stage.get("origin") or {}
                    if candidate_place.get("coordinates"):
                        fallback_place = dict(candidate_place)
                        break
                if fallback_place:
                    break
        if fallback_place.get("coordinates"):
            fallback_name = str(
                fallback_place.get("city") or fallback_place.get("name") or "目的地"
            ).strip()
            hotels = [
                {
                    "place": {
                        "id": "estimated-destination-hotel",
                        "name": f"{fallback_name}核心区住宿点（需预订）",
                        "city": fallback_place.get("city") or fallback_name,
                        "address": fallback_place.get("address"),
                        "coordinates": fallback_place.get("coordinates"),
                        "source_id": "estimated-destination-hotel",
                    },
                    "provider": "RoadMan planner",
                    "source_records": [],
                    "estimated": True,
                    "user_requested": False,
                }
            ]
    attraction_sources = {
        item["place"]["name"]: item
        for item in candidates.get("attractions", [])
        if item.get("place", {}).get("name")
        and _attraction_is_schedulable(item)
    }
    attraction_source_keys = {
        _attraction_name_key(name): item
        for name, item in attraction_sources.items()
        if _attraction_name_key(name)
    }

    def source_for_stage(stage: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve a route destination to its researched attraction record."""
        destination_name = (stage.get("destination") or {}).get("name")
        exact = attraction_sources.get(destination_name)
        # Exact destination equality is safe on a neutral/anchor transfer
        # (e.g. a requested scenic lake reached via ``目的地短途接驳``), while
        # hotel/airport/return stages remain explicitly excluded.
        if exact and (not _stage_is_non_scenic(stage) or exact.get("user_required")):
            return exact
        # Fuzzy containment is useful for provider suffixes such as
        # ``景区(东门)`` but unsafe for hotel/station names.  A hotel called
        # ``宽窄巷子店`` must not turn its check-in connector into a scenic
        # visit simply because it contains the attraction name.
        if not _stage_can_host_attraction(stage):
            return None
        destination_key = _attraction_name_key(destination_name)
        if not destination_key:
            return None
        direct = attraction_source_keys.get(destination_key)
        if direct:
            return direct
        # Providers append entrance/branch/campus suffixes. Prefer the
        # longest containment match so a short label such as “省博” cannot
        # accidentally claim an unrelated museum.
        matches = [
            (key, item)
            for key, item in attraction_source_keys.items()
            if min(len(key), len(destination_key)) >= 2
            and (key in destination_key or destination_key in key)
        ]
        return max(matches, key=lambda pair: len(pair[0]))[1] if matches else None
    seasonal_excluded_names = {
        item.get("place", {}).get("name")
        for item in candidates.get("attractions", [])
        if item.get("seasonal_excluded")
        and not item.get("user_required")
        and item.get("place", {}).get("name")
    }
    used_attraction_names: set[str] = set()
    used_meal_names = {
        item.get("place", {}).get("name")
        for day in day_plans
        for item in day.get("activities", [])
        if item.get("type") == "meal" and item.get("place", {}).get("name")
    }
    selected_hotels: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    local_anchor_request = bool(
        trip_request
        and (
            str(trip_request.get("destination_scope") or "").strip().lower() == "poi"
            or trip_request.get("stay_only_at_destination")
        )
    )

    for day_index, day in enumerate(day_plans):
        # Some agent-produced day dictionaries omit the optional model id.
        # Materialize a stable id before creating activity ids so one malformed
        # day cannot abort the entire planning job.
        day.setdefault("id", f"day_{day.get('day_index', day_index + 1)}")
        # ``title`` is required by the persisted DayPlan model, but is not
        # semantically important to the scheduling pass.  Agents may omit it
        # while returning an otherwise usable day; give it a localized,
        # deterministic fallback instead of failing the whole planning job at
        # the final persistence step.
        day.setdefault("title", f"第 {day.get('day_index', day_index + 1)} 天")
        day_date = datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date()
        activities: list[dict[str, Any]] = []
        for activity in list(day.get("activities", [])):
            # Replans start from the previous persisted snapshot. Clean the
            # activity itself before merging a refreshed candidate so an old
            # provider slogan cannot survive when the new search times out.
            if isinstance(activity, dict):
                _sanitize_candidate_copy(activity)
            name = activity.get("place", {}).get("name")
            if activity.get("type") == "hotel" and not _comfortable_hotel(
                {"place": activity.get("place") or {}}
            ):
                # A previous snapshot may contain a hostel from before the
                # comfort policy was enabled. Remove it so this pass can pick
                # a valid replacement.
                continue
            if activity.get("type") == "attraction" and name:
                source_candidate = attraction_sources.get(name)
                if source_candidate is None:
                    source_key = _attraction_name_key(name)
                    source_candidate = attraction_source_keys.get(source_key)
                if source_candidate is not None and not _attraction_is_schedulable(source_candidate):
                    # A stale model activity may have been created before the
                    # suitability pass. Keep the provider record in backups,
                    # but do not leave the rejected venue in the itinerary.
                    continue
                if name in seasonal_excluded_names:
                    # A re-run of the review pass can encounter a stale
                    # activity created before seasonal filtering. Remove it
                    # from the formal plan and leave the candidate visible as
                    # a backup recommendation.
                    continue
                if source_candidate is not None:
                    try:
                        existing_duration = int(
                            (
                                datetime.fromisoformat(activity["planned_end"])
                                - datetime.fromisoformat(activity["planned_start"])
                            ).total_seconds()
                            // 60
                        )
                    except (KeyError, TypeError, ValueError):
                        existing_duration = 0
                    minimum_duration = _comfortable_visit_minimum(source_candidate)
                    if existing_duration < minimum_duration:
                        # Never carry a stale 45--90 minute scenic block
                        # through a replan. The route-aware pass will reserve
                        # a full window; compact venues keep their explicit
                        # source-backed minimum.
                        continue
                if name in used_attraction_names:
                    # Agent candidates can repeat the destination attraction
                    # on every day. Keep the first occurrence and let the
                    # ranked pool fill later days with different places.
                    continue
                used_attraction_names.add(name)
                if local_anchor_request and not activity.get("required"):
                    try:
                        existing_duration = int(
                            (
                                datetime.fromisoformat(activity["planned_end"])
                                - datetime.fromisoformat(activity["planned_start"])
                            ).total_seconds()
                            // 60
                        )
                    except (KeyError, TypeError, ValueError):
                        existing_duration = 0
                    if existing_duration < _comfortable_visit_minimum(source_candidate):
                        # A stale model activity can survive between replans
                        # with a compressed one-hour window. Scenic-anchor
                        # trips require a useful block; let the route-aware
                        # scheduler place a fresh stop instead.
                        used_attraction_names.discard(name)
                        continue
            if activity.get("type") in {"attraction", "meal", "hotel"}:
                # Agent-produced activities can arrive without the structured
                # check fields.  Normalize them before any scheduling pass so
                # every visible item has an explicit reservation state and
                # risk explanation, even when its provider metadata is sparse.
                try:
                    existing_checks = activity_checks(
                        None,
                        str(activity.get("type")),
                        start_at=datetime.fromisoformat(activity["planned_start"]),
                        end_at=datetime.fromisoformat(activity["planned_end"]),
                    )
                except (KeyError, TypeError, ValueError):
                    existing_checks = activity_checks(None, str(activity.get("type")))
                for key, value in existing_checks.items():
                    activity.setdefault(key, value)
            activities.append(activity)
        stages = sorted(day.get("stages", []), key=lambda item: item.get("sequence", 0))
        # On a return day the final stage is the intercity leg home.  Local
        # attraction filling must stop before that departure; otherwise the
        # generic gap filler can put a destination POI *after* the traveller
        # has already returned to the origin city.
        return_cutoff = None
        if day_index == len(day_plans) - 1 and stages:
            final_stage = stages[-1]
            final_mode = final_stage.get("mode")
            final_title = str(final_stage.get("title") or "")
            # A local ``返回住宿或目的地核心区`` connector is still part of
            # the sightseeing day.  Treating every final driving stage as an
            # intercity return incorrectly made its departure the hard
            # cutoff, leaving no room for the named scenic visit.  Only
            # scheduled public/air/sea transport or an explicitly labelled
            # return-to-origin driving leg closes the day.
            is_return_stage = (
                final_mode in {"train", "flight", "ferry"}
                or "返程" in final_title
                or "回程" in final_title
                or "返回出发" in final_title
                or "return" in final_title.lower()
            )
            if not is_return_stage:
                final_stage = None
            try:
                if final_stage is not None:
                    return_cutoff = datetime.fromisoformat(final_stage["planned_start"])
            except (KeyError, TypeError, ValueError):
                return_cutoff = None
        # Reserve scenic dwell time before meals are inserted.  Named-anchor
        # trips use the stricter return-chain helper as well; the generic pass
        # keeps ordinary city itineraries from collapsing every attraction to
        # a one-hour transfer gap.
        _ensure_scenic_visit_windows(
            stages,
            day_date=day_date,
            return_cutoff=return_cutoff,
            source_for_stage=source_for_stage,
        )
        if local_anchor_request:
            _ensure_anchor_visit_windows(
                stages,
                day_date=day_date,
                return_cutoff=return_cutoff,
                source_for_stage=source_for_stage,
            )
        existing_hotel = next(
            (item for item in activities if item.get("type") == "hotel"),
            None,
        )
        if existing_hotel:
            selected_hotels.append(
                (
                    {"place": existing_hotel.get("place") or {}},
                    next(
                        (
                            stage.get("destination")
                            for stage in reversed(stages)
                            if stage.get("destination")
                        ),
                        None,
                    ),
                )
            )
        existing_names = {
            item.get("place", {}).get("name")
            for item in activities
            if item.get("place")
        }
        for stage_index, stage in enumerate(stages):
            candidate = source_for_stage(stage)
            if (
                not candidate
                or candidate["place"]["name"] in existing_names
                or candidate["place"]["name"] in used_attraction_names
            ):
                continue
            start_at = datetime.fromisoformat(stage["planned_end"])
            # A stage may finish after midnight (for example, a late train
            # into Beijing).  Do not attach a sightseeing stop to the previous
            # calendar day or place it at 01:00–05:00.  The next day's local
            # route pass will provide daytime sightseeing instead.
            if start_at.date() != day_date:
                continue
            daylight_start = datetime.combine(day_date, time(7, 0), tzinfo=SHANGHAI)
            daylight_end = datetime.combine(day_date, time(21, 0), tzinfo=SHANGHAI)
            if return_cutoff is not None:
                daylight_end = min(daylight_end, return_cutoff)
            start_at = max(start_at, daylight_start)
            next_start = (
                datetime.fromisoformat(stages[stage_index + 1]["planned_start"])
                if stage_index + 1 < len(stages)
                else None
            )
            slot_end = (
                next_start - timedelta(minutes=15)
                if next_start
                else start_at + timedelta(minutes=195)
            )
            slot_end = min(slot_end, daylight_end)
            if slot_end <= start_at:
                continue
            slot = _closest_free_slot(
                start_at,
                slot_end,
                preferred=start_at,
                duration_minutes=max(
                    _suggested_duration(candidate, 90),
                    _comfortable_visit_minimum(candidate),
                ),
                # Meals are re-slotted after scenic windows are reserved. Do
                # not let a stale breakfast/lunch snapshot shrink a required
                # attraction to a one-hour gap during a replan.
                occupied=_occupied_ranges(
                    [item for item in activities if item.get("type") != "meal"]
                ),
                minimum_minutes=45,
            )
            if not slot:
                continue
            activity_start, duration = slot
            if (
                local_anchor_request
                and
                duration < _comfortable_visit_minimum(candidate)
                and not candidate.get("user_required")
                and not candidate.get("user_confirmed")
            ):
                # The route may arrive at a candidate just before another
                # transfer. Do not display an implausible 40–90 minute scenic
                # stop merely because a gap exists; keep it in alternatives.
                continue
            if duration < _comfortable_visit_minimum(candidate):
                # Ordinary scenic stops should not be rendered as tiny photo
                # breaks merely because another activity consumed the gap.
                continue
            activities.append(
                _activity(
                    day=day,
                    sequence=len(activities),
                    activity_type="attraction",
                    candidate=candidate,
                    place=candidate["place"],
                    start_at=activity_start,
                    duration_minutes=duration,
                    sources=candidate.get("source_records", []),
                    opening_text="开放时间以景区当天公告为准",
                    required=bool(candidate.get("user_required")),
                    ticket_or_price=candidate.get("ticket_or_price"),
                    user_note=(
                        candidate.get("agent_reason")
                        or " · ".join(candidate.get("recommendation_reasons", []))
                        or "由候选排序智能体综合来源、距离与偏好选入"
                    ),
                    description=candidate.get("description"),
                    image_url=candidate.get("image_url"),
                    detail_url=candidate.get("detail_url"),
                )
            )
            candidate["coverage_scheduled"] = True
            existing_names.add(candidate["place"]["name"])
            used_attraction_names.add(candidate["place"]["name"])

        # A hard requirement must have an activity record, not only a
        # movement stage.  If a provider changed the stage label (or the
        # stage gap is too small for the normal ranked filler), reserve a
        # daytime slot on the requirement's assigned day before meals are
        # placed. This keeps “橘子洲/省博” visible and verifiable without
        # inventing a new place from a keyword list.
        required_for_day = [
            candidate
            for candidate in candidates.get("attractions", [])
            if candidate.get("user_required")
            and candidate.get("place", {}).get("name")
            and candidate.get("coverage_day_index") in {None, day_index + 1}
            and _attraction_name_key(candidate["place"]["name"])
            not in {
                _attraction_name_key(name) for name in used_attraction_names if name
            }
        ]
        if required_for_day:
            stage_ranges = [
                (
                    datetime.fromisoformat(stage["planned_start"]),
                    datetime.fromisoformat(stage["planned_end"]),
                )
                for stage in stages
                if stage.get("planned_start") and stage.get("planned_end")
            ]
            occupied_required = [*stage_ranges, *_occupied_ranges(activities)]
            for candidate in required_for_day:
                if not (candidate.get("place") or {}).get("coordinates"):
                    # Keep the unresolved marker for verification instead of
                    # constructing an activity that cannot be mapped.
                    continue
                candidate_key = _attraction_name_key(candidate["place"]["name"])
                matching_stages = [
                    stage
                    for stage in stages
                    if _attraction_name_key(
                        (stage.get("destination") or {}).get("name")
                    )
                    and _stage_can_host_attraction(stage)
                    and (
                        _attraction_name_key(
                            (stage.get("destination") or {}).get("name")
                        )
                        == candidate_key
                        or candidate_key
                        in _attraction_name_key(
                            (stage.get("destination") or {}).get("name")
                        )
                        or _attraction_name_key(
                            (stage.get("destination") or {}).get("name")
                        )
                        in candidate_key
                    )
                ]
                if matching_stages:
                    target_stage = matching_stages[0]
                    start_at = datetime.fromisoformat(target_stage["planned_end"])
                    next_starts = [
                        datetime.fromisoformat(stage["planned_start"])
                        for stage in stages
                        if stage is not target_stage and stage.get("planned_start")
                        and datetime.fromisoformat(stage["planned_start"]) > start_at
                    ]
                    window_end = min(
                        next_starts[0] if next_starts else start_at + timedelta(hours=3),
                        datetime.combine(day_date, time(21, 0), tzinfo=SHANGHAI),
                    )
                    preferred = start_at + timedelta(minutes=15)
                else:
                    start_at = datetime.combine(day_date, time(9, 0), tzinfo=SHANGHAI)
                    window_end = datetime.combine(day_date, time(21, 0), tzinfo=SHANGHAI)
                    preferred = datetime.combine(day_date, time(14, 0), tzinfo=SHANGHAI)
                slot = _closest_free_slot(
                    start_at,
                    window_end,
                    preferred=preferred,
                    duration_minutes=max(
                        _suggested_duration(candidate, 90),
                        _comfortable_visit_minimum(candidate),
                    ),
                    occupied=occupied_required,
                    minimum_minutes=30,
                )
                if not slot:
                    continue
                activity_start, duration = slot
                activities.append(
                    _activity(
                        day=day,
                        sequence=len(activities),
                        activity_type="attraction",
                        candidate=candidate,
                        place=candidate["place"],
                        start_at=activity_start,
                        duration_minutes=duration,
                        sources=candidate.get("source_records", []),
                        opening_text="开放时间以景区当日公告为准",
                        required=True,
                        ticket_or_price=candidate.get("ticket_or_price"),
                        user_note=(
                            candidate.get("agent_reason")
                            or "；".join(candidate.get("recommendation_reasons", []))
                            or "用户指定地点，已保留游览时段"
                        ),
                        description=candidate.get("description"),
                        image_url=candidate.get("image_url"),
                        detail_url=candidate.get("detail_url"),
                    )
                )
                candidate["coverage_scheduled"] = True
                existing_names.add(candidate["place"]["name"])
                used_attraction_names.add(candidate["place"]["name"])
                occupied_required.append(
                    (activity_start, activity_start + timedelta(minutes=duration))
                )

        # Reserve executable sightseeing windows before filling the day with
        # meals.  A flight/transfer day can otherwise have all free time
        # consumed by the three meal placeholders, leaving a named must-visit
        # stage with no activity record even though the route reaches it.
        _ensure_meals(
            day,
            activities,
            stages,
            candidates.get("meals", []),
            used_names=used_meal_names,
        )
        _reschedule_meals(day, activities, stages)

        # Use remaining safe gaps for a second/third attraction when the day
        # has enough slack.  The old implementation only attached the POI
        # whose name exactly matched a stage destination, which left most of
        # the Agent's ranked candidates unused.
        scheduled_attractions = [
            item for item in activities if item.get("type") == "attraction"
        ]
        if local_anchor_request and len(scheduled_attractions) > 2:
            # A short scenic-anchor trip is intentionally spacious. Keep the
            # strongest two source-backed stops per day and leave the rest as
            # visible alternatives instead of filling every gap with remote
            # or low-value POIs.
            ranked_existing = sorted(
                scheduled_attractions,
                key=lambda activity: (
                    0 if activity.get("required") else 1,
                    -_attraction_priority(attraction_sources.get((activity.get("place") or {}).get("name"), {})),
                    str((activity.get("place") or {}).get("name") or ""),
                ),
            )
            keep_ids = {id(item) for item in ranked_existing[:2]}
            activities = [
                item
                for item in activities
                if item.get("type") != "attraction" or id(item) in keep_ids
            ]
            used_attraction_names = {
                item.get("place", {}).get("name")
                for owner in day_plans[:day_index]
                for item in owner.get("activities", [])
                if item.get("type") == "attraction" and item.get("place", {}).get("name")
            }
            used_attraction_names.update(
                item.get("place", {}).get("name")
                for item in activities
                if item.get("type") == "attraction" and item.get("place", {}).get("name")
            )
            scheduled_attractions = [
                item for item in activities if item.get("type") == "attraction"
            ]
        # A multi-day stay should not collapse into one transfer plus a
        # single attraction. Keep enough breathing room for a comfortable
        # morning/afternoon/evening plan while never exceeding four curated
        # attractions per day.  When the destination research Agent has
        # identified a city-wide must-see set, distribute the remaining
        # highlights over the remaining days instead of letting the hotel
        # area's distance score consume every slot.
        remaining_priority = [
            item
            for item in candidates.get("attractions", [])
            if _attraction_priority(item) > 0
            and item.get("place", {}).get("name") not in used_attraction_names
        ]
        remaining_days = max(1, len(day_plans) - day_index)
        priority_target = (
            (len(remaining_priority) + remaining_days - 1) // remaining_days
            if remaining_priority
            else 0
        )
        required_count = sum(
            1
            for item in candidates.get("attractions", [])
            if item.get("user_required")
            and item.get("coverage_day_index") in {None, day_index + 1}
        )
        # A comfortable city day is intentionally centered on at most two
        # substantial attractions.  Explicit requirements are exempt from the
        # cap so they are never silently dropped; optional highlights remain
        # visible as ranked alternatives for later days.
        target_attractions = max(
            required_count,
            min(3, max(2, len(stages) + 1, priority_target)),
        )
        if local_anchor_request:
            target_attractions = min(2, target_attractions)
        travel_only_day = any(
            "跨天" in str(stage.get("title") or "")
            and stage.get("mode") == "driving"
            for stage in stages
        )
        if travel_only_day:
            # A calendar day occupied by an intercity driving piece still
            # needs meals, charging/rest and an overnight stop, but never a
            # destination attraction squeezed into an artificial gap.
            target_attractions = len(scheduled_attractions)
        if len(scheduled_attractions) < target_attractions:
            outbound_intercity_day = day_index == 0 and any(
                stage.get("title") == "城市出发"
                and stage.get("mode") in {"train", "flight", "ferry"}
                for stage in stages
            )
            if outbound_intercity_day:
                # The first calendar day is a departure day, not a destination
                # sightseeing day.  Never place Beijing attractions in the
                # morning before a Friday-afternoon train/flight or after a
                # cross-midnight arrival.
                scheduled_attractions = [
                    item for item in activities if item.get("type") == "attraction"
                ]
                target_attractions = len(scheduled_attractions)
            stage_ranges = [
                (
                    datetime.fromisoformat(stage["planned_start"]),
                    datetime.fromisoformat(stage["planned_end"]),
                )
                for stage in stages
            ]
            occupied = [
                *stage_ranges,
                *_occupied_ranges(
                    [item for item in activities if item.get("type") != "meal"]
                ),
            ]
            ranked_candidates = sorted(
                candidates.get("attractions", []),
                key=lambda item: (
                    0
                    if item.get("coverage_day_index") == day_index + 1
                    else 1,
                    *_attraction_sort_key(item),
                ),
            )
            for candidate in ranked_candidates:
                if len(scheduled_attractions) >= target_attractions:
                    break
                place = candidate.get("place") or {}
                name = place.get("name")
                if (
                    not name
                    or not _attraction_is_schedulable(candidate)
                    or name in existing_names
                    or name in used_attraction_names
                ):
                    continue
                activity_day_start = datetime.combine(
                    datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date(),
                    time(9, 0),
                    tzinfo=SHANGHAI,
                )
                activity_day_end = datetime.combine(
                    datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date(),
                    time(21, 0),
                    tzinfo=SHANGHAI,
                )
                if return_cutoff is not None:
                    activity_day_end = min(activity_day_end, return_cutoff)
                slot = _closest_free_slot(
                    activity_day_start,
                    activity_day_end,
                    preferred=datetime.combine(
                        datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date(),
                        time(15, 0),
                        tzinfo=SHANGHAI,
                    ),
                    duration_minutes=max(
                        _suggested_duration(candidate, 75),
                        _comfortable_visit_minimum(candidate),
                    ),
                    occupied=occupied,
                    minimum_minutes=45,
                )
                if not slot:
                    continue
                activity_start, duration = slot
                if (
                    local_anchor_request
                    and
                    duration < _comfortable_visit_minimum(candidate)
                    and not candidate.get("user_required")
                    and not candidate.get("user_confirmed")
                ):
                    # Do not manufacture a one-hour “visit” by truncating a
                    # three-hour scenic block between meals or transfers. The
                    # stop remains a ranked backup and the free window stays
                    # visible to the traveller.
                    continue
                if duration < _comfortable_visit_minimum(candidate):
                    continue
                activities.append(
                    _activity(
                        day=day,
                        sequence=len(activities),
                        activity_type="attraction",
                        candidate=candidate,
                        place=place,
                        start_at=activity_start,
                        duration_minutes=duration,
                        sources=candidate.get("source_records", []),
                        opening_text="开放时间以景区当天公告为准",
                        required=bool(candidate.get("user_required")),
                        ticket_or_price=candidate.get("ticket_or_price"),
                        user_note=(
                            candidate.get("agent_reason")
                            or " · ".join(candidate.get("recommendation_reasons", []))
                            or "由候选排序智能体综合来源、距离与偏好选入"
                        ),
                        description=candidate.get("description"),
                        image_url=candidate.get("image_url"),
                        detail_url=candidate.get("detail_url"),
                    )
                )
                occupied.append((activity_start, activity_start + timedelta(minutes=duration)))
                candidate["coverage_scheduled"] = True
                scheduled_attractions.append(activities[-1])
                existing_names.add(name)
                used_attraction_names.add(name)

        if (
            hotels
            # A one-day preview still needs a visible overnight option.  For
            # multi-day trips the final day is the return/departure day and
            # therefore does not create a new night after the traveller has
            # left the destination.
            and (day_index < len(day_plans) - 1 or len(day_plans) == 1)
            and not any(item.get("type") == "hotel" for item in activities)
        ):
            hotel_anchor = next(
                (
                    stage.get("destination")
                    for stage in reversed(stages)
                    if stage.get("destination")
                ),
                None,
            )
            hotel = _hotel_for_day(hotels, hotel_anchor, selected_hotels)
            if hotel is None:
                continue
            selected_hotels.append((hotel, hotel_anchor))
            last_end = max(
                (
                    datetime.fromisoformat(stage["planned_end"])
                    for stage in stages
                ),
                default=datetime.combine(day_date, time(18, 0), tzinfo=SHANGHAI),
            )
            latest_activity_end = max(
                (
                    datetime.fromisoformat(activity["planned_end"])
                    for activity in activities
                    if datetime.fromisoformat(activity["planned_end"]).date()
                    == day_date
                ),
                default=last_end,
            )
            check_in = max(
                datetime.combine(day_date, time(19, 0), tzinfo=SHANGHAI),
                last_end + timedelta(minutes=30),
                latest_activity_end + timedelta(minutes=15),
            )
            check_out = datetime.combine(
                day_date + timedelta(days=1),
                time(7, 30),
                tzinfo=SHANGHAI,
            )
            if check_out <= check_in:
                check_out = check_in + timedelta(hours=8)
            activities.append(
                _activity(
                    day=day,
                    sequence=len(activities),
                    activity_type="hotel",
                    candidate=hotel,
                    place=hotel["place"],
                    start_at=check_in,
                    duration_minutes=int((check_out - check_in).total_seconds() // 60),
                    sources=hotel.get("source_records", []),
                    opening_text="入住时间与房态以酒店实时信息为准",
                    required=True,
                    ticket_or_price=hotel.get("ticket_or_price"),
                    user_note=(
                        " · ".join(hotel.get("recommendation_reasons", []))
                        or "由住宿智能体综合位置、时间和来源选入"
                    ),
                    image_url=hotel.get("image_url"),
                    detail_url=hotel.get("detail_url"),
                )
            )

        activities.sort(key=lambda item: item["planned_start"])
        for sequence, activity in enumerate(activities):
            activity["sequence"] = sequence
        day["activities"] = activities
        day["items"] = [
            *[{"type": "stage", "id": stage["id"]} for stage in stages],
            *[{"type": "activity", "id": item["id"]} for item in activities],
        ]
    # A user-confirmed add/replace is stronger than a fresh provider ranking.
    # Keep the chosen item visible even when the agent had already produced a
    # generic meal/hotel slot before the candidate search completed.
    if confirmed_additions:
        _apply_confirmed_additions(day_plans, candidates, confirmed_additions)
    return day_plans


def _apply_confirmed_additions(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
    confirmed_additions: list[dict[str, Any]],
) -> None:
    """Make accepted edit candidates durable across a full replan.

    The normal schedule pass intentionally preserves existing meal slots.  A
    map-selected restaurant could therefore be present in the candidate pool
    but never replace the generic breakfast/lunch/dinner activity.  For an
    explicit user decision, replacing that slot is the least surprising and
    keeps the original time window and overlap guarantees intact.
    """
    category_to_type = {
        "attractions": "attraction",
        "meals": "meal",
        "hotels": "hotel",
    }
    for record in confirmed_additions:
        if not isinstance(record, dict):
            continue
        category = str(record.get("category") or "").strip()
        activity_type = category_to_type.get(category)
        candidate = record.get("candidate")
        if not activity_type or not isinstance(candidate, dict):
            continue
        place = candidate.get("place") or {}
        name = str(place.get("name") or "").strip()
        if not name:
            continue
        # Prefer the day selected in the edit preview.  If an older state does
        # not carry day_id, an already visible match is still marked as fixed.
        requested_day_id = str(record.get("day_id") or "").strip()
        target_days = [
            day for day in day_plans
            if not requested_day_id or str(day.get("id") or "") == requested_day_id
        ] or day_plans
        existing = next(
            (
                activity
                for day in day_plans
                for activity in day.get("activities", [])
                if activity.get("type") == activity_type
                and str((activity.get("place") or {}).get("name") or "").strip() == name
            ),
            None,
        )
        if existing is not None:
            existing["user_confirmed"] = True
            continue
        target_day = target_days[0] if target_days else None
        if target_day is None:
            continue
        if activity_type == "meal":
            meal = next(
                (
                    activity for activity in target_day.get("activities", [])
                    if activity.get("type") == "meal" and not activity.get("locked")
                ),
                None,
            ) or next(
                (
                    activity for activity in target_day.get("activities", [])
                    if activity.get("type") == "meal"
                ),
                None,
            )
            if meal is None:
                day_date = datetime.fromisoformat(
                    f"{target_day['date']}T00:00:00+08:00"
                ).date()
                slot = _closest_free_slot(
                    datetime.combine(day_date, time(11, 0), tzinfo=SHANGHAI),
                    datetime.combine(day_date, time(14, 30), tzinfo=SHANGHAI),
                    preferred=datetime.combine(day_date, time(12, 0), tzinfo=SHANGHAI),
                    duration_minutes=45,
                    occupied=[
                        *[
                            (
                                datetime.fromisoformat(stage["planned_start"]),
                                datetime.fromisoformat(stage["planned_end"]),
                            )
                            for stage in target_day.get("stages", [])
                            if stage.get("planned_start") and stage.get("planned_end")
                        ],
                        *_occupied_ranges(target_day.get("activities", [])),
                    ],
                    minimum_minutes=30,
                )
                if slot:
                    start_at, duration = slot
                    meal = _activity(
                        day=target_day,
                        sequence=len(target_day.get("activities", [])),
                        activity_type="meal",
                        candidate=candidate,
                        place=place,
                        start_at=start_at,
                        duration_minutes=duration,
                        sources=candidate.get("source_records", []),
                        opening_text="营业时间与排队情况以当天为准",
                        user_note=candidate.get("user_note") or "用户已确认的餐饮安排",
                    )
                    target_day.setdefault("activities", []).append(meal)
            if meal is not None:
                # Retain the legal schedule slot and replace only the
                # place/evidence fields with the explicit user choice.
                meal["place"] = dict(place)
                meal["user_confirmed"] = True
                meal["source_records"] = list(candidate.get("source_records") or [])
                for key in (
                    "description", "image_url", "detail_url", "ticket_or_price",
                    "parking_or_price", "parking_note", "opening_hours",
                    "reservation_status", "reservation_note",
                ):
                    if candidate.get(key) is not None:
                        meal[key] = candidate[key]
                meal["user_note"] = candidate.get("user_note") or "用户已确认的餐饮安排"
        elif activity_type == "hotel":
            hotel = next(
                (
                    activity for activity in target_day.get("activities", [])
                    if activity.get("type") == "hotel"
                ),
                None,
            ) or next(
                (
                    activity for day in day_plans
                    for activity in day.get("activities", [])
                    if activity.get("type") == "hotel"
                ),
                None,
            )
            if hotel is not None:
                # A hotel selected on the final/return day should still be
                # visible and should become the same base used on prior days.
                hotel["place"] = dict(place)
                hotel["user_confirmed"] = True
                hotel["required"] = True
                hotel["source_records"] = list(candidate.get("source_records") or [])
                for key in (
                    "description", "image_url", "detail_url", "opening_hours",
                    "reservation_status", "reservation_note",
                ):
                    if candidate.get(key) is not None:
                        hotel[key] = candidate[key]
                hotel["user_note"] = candidate.get("user_note") or "用户已确认的住宿安排"
            else:
                day_date = datetime.fromisoformat(
                    f"{target_day['date']}T00:00:00+08:00"
                ).date()
                slot = _closest_free_slot(
                    datetime.combine(day_date, time(17, 0), tzinfo=SHANGHAI),
                    datetime.combine(day_date, time(22, 30), tzinfo=SHANGHAI),
                    preferred=datetime.combine(day_date, time(19, 30), tzinfo=SHANGHAI),
                    duration_minutes=60,
                    occupied=[
                        *[
                            (
                                datetime.fromisoformat(stage["planned_start"]),
                                datetime.fromisoformat(stage["planned_end"]),
                            )
                            for stage in target_day.get("stages", [])
                            if stage.get("planned_start") and stage.get("planned_end")
                        ],
                        *_occupied_ranges(target_day.get("activities", [])),
                    ],
                    minimum_minutes=30,
                )
                if slot:
                    start_at, duration = slot
                    target_day.setdefault("activities", []).append(
                        _activity(
                            day=target_day,
                            sequence=len(target_day.get("activities", [])),
                            activity_type="hotel",
                            candidate=candidate,
                            place=place,
                            start_at=start_at,
                            duration_minutes=duration,
                            sources=candidate.get("source_records", []),
                            opening_text="入住时间与房态以酒店实时信息为准",
                            required=True,
                            user_note=candidate.get("user_note") or "用户已确认的住宿安排",
                        )
                    )

        # Keep item references deterministic after replacing/materializing a
        # confirmed activity.  This also makes the map and left timeline use
        # the same order immediately after a replan.
        target_day["activities"] = sorted(
            target_day.get("activities", []),
            key=lambda item: str(item.get("planned_start") or ""),
        )
        for sequence, activity in enumerate(target_day["activities"]):
            activity["sequence"] = sequence
        target_day["items"] = [
            *[{"type": "stage", "id": stage["id"]} for stage in target_day.get("stages", [])],
            *[{"type": "activity", "id": activity["id"]} for activity in target_day["activities"]],
        ]


def _meal_fallback_slot(
    day_date,
    window_start: time,
    window_end: time,
    preferred_time: time,
    stages: list[dict[str, Any]],
    activities: list[dict[str, Any]],
) -> tuple[datetime, int, str] | None:
    """Find a truthful meal slot when the normal free-gap search is full.

    Long intercity legs are intentionally split by rest, charging and service
    stops.  A meal can happen during one of those stops, or as a simple
    onboard/waypoint meal during a movement stage.  Returning the fallback
    kind lets the UI explain why no extra route segment was created.
    """
    window_start_at = datetime.combine(day_date, window_start, tzinfo=SHANGHAI)
    window_end_at = datetime.combine(day_date, window_end, tzinfo=SHANGHAI)
    preferred_at = datetime.combine(day_date, preferred_time, tzinfo=SHANGHAI)

    def overlap(
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[datetime, datetime] | None:
        clipped_start = max(start_at, window_start_at)
        clipped_end = min(end_at, window_end_at)
        if clipped_end <= clipped_start:
            return None
        return clipped_start, clipped_end

    def choose(
        ranges: list[tuple[datetime, datetime]],
        *,
        minimum_minutes: int,
        preferred_duration: int,
    ) -> tuple[datetime, int] | None:
        options: list[tuple[float, datetime, int]] = []
        for start_at, end_at in ranges:
            available = int((end_at - start_at).total_seconds() // 60)
            if available < minimum_minutes:
                continue
            duration = min(preferred_duration, available)
            latest_start = end_at - timedelta(minutes=duration)
            candidate_start = min(max(preferred_at, start_at), latest_start)
            options.append(
                (
                    abs((candidate_start - preferred_at).total_seconds()),
                    candidate_start,
                    duration,
                )
            )
        if not options:
            return None
        _, start_at, duration = min(options, key=lambda item: item[0])
        return start_at, duration

    # Prefer an explicitly planned rest/charging/fuelling/service break.  A
    # 15–20 minute stop is intentionally accepted: it is more honest and
    # useful than reporting that the day has no lunch at all.
    stop_ranges: list[tuple[datetime, datetime]] = []
    for activity in activities:
        if activity.get("type") not in {
            "rest",
            "charging",
            "fueling",
            "service",
            "break",
        }:
            continue
        try:
            start_at = datetime.fromisoformat(activity["planned_start"])
            end_at = datetime.fromisoformat(activity["planned_end"])
        except (KeyError, TypeError, ValueError):
            continue
        clipped = overlap(start_at, end_at)
        if clipped is not None:
            stop_ranges.append(clipped)
    selected = choose(stop_ranges, minimum_minutes=15, preferred_duration=30)
    if selected is not None:
        return (*selected, "stop")

    # If no break overlaps the meal window, place a short onboard/waypoint
    # meal inside a movement stage that does.  Include all modes produced by
    # the transport agents, not only the original driving/train set.
    movement_modes = {
        "driving",
        "car",
        "train",
        "flight",
        "ferry",
        "transit",
        "bus",
        "subway",
        "metro",
        "tram",
        "bike",
        "cycling",
        "walking",
    }
    movement_ranges: list[tuple[datetime, datetime]] = []
    for stage in stages:
        if str(stage.get("mode") or "").lower() not in movement_modes:
            continue
        try:
            start_at = datetime.fromisoformat(stage["planned_start"])
            end_at = datetime.fromisoformat(stage["planned_end"])
        except (KeyError, TypeError, ValueError):
            continue
        clipped = overlap(start_at, end_at)
        if clipped is not None:
            movement_ranges.append(clipped)
    selected = choose(movement_ranges, minimum_minutes=15, preferred_duration=30)
    if selected is not None:
        return (*selected, "movement")
    return None


def _ensure_meals(
    day: dict[str, Any],
    activities: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    used_names: set[str | None],
) -> None:
    """Guarantee visible breakfast, lunch and dinner slots for every day.

    Meal candidates are optional enrichment.  When a provider returns no
    restaurant, keep the time block with a clearly labelled nearby-food
    placeholder instead of leaving the afternoon/evening empty or inventing a
    route to a restaurant.
    """
    definitions = [
        ("早餐", time(6, 0), time(9, 30), time(7, 30)),
        ("午餐", time(11, 0), time(15, 0), time(12, 0)),
        ("晚餐", time(17, 0), time(22, 0), time(18, 30)),
    ]
    day_date = datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date()
    existing_meals = [item for item in activities if item.get("type") == "meal"]
    occupied = [
        *[
            (
                datetime.fromisoformat(stage["planned_start"]),
                datetime.fromisoformat(stage["planned_end"]),
            )
            for stage in stages
            if stage.get("planned_start") and stage.get("planned_end")
        ],
        *_occupied_ranges([item for item in activities if item.get("type") != "meal"]),
        *_occupied_ranges(existing_meals),
    ]
    available = sorted(
        (item for item in candidates if item.get("place", {}).get("name")),
        key=_candidate_quality,
    )
    candidate_cursor = 0
    for meal_index, (label, window_start, window_end, preferred_time) in enumerate(definitions):
        if meal_index < len(existing_meals):
            used_names.add(existing_meals[meal_index].get("place", {}).get("name"))
            continue
        candidate = next(
            (
                item
                for item in available[candidate_cursor:] + available[:candidate_cursor]
                if item.get("place", {}).get("name") not in used_names
            ),
            None,
        )
        reused_candidate = False
        if candidate is None and available:
            # Small destinations may return fewer restaurants than meal
            # slots. Reuse the best sourced option instead of leaving a meal
            # blank, and expose the reuse in the activity note.
            candidate = available[candidate_cursor % len(available)]
            reused_candidate = True
        if reused_candidate and candidate:
            candidate["user_note"] = (
                f"{candidate.get('user_note') or ''}；候选池不足，已轮换复用有来源餐厅"
            )
        if candidate:
            candidate_cursor = (available.index(candidate) + 1) % len(available)
            place = dict(candidate["place"])
            used_names.add(place.get("name"))
        else:
            anchor = next(
                (
                    stage.get("destination")
                    for stage in reversed(stages)
                    if stage.get("destination")
                ),
                next((stage.get("origin") for stage in stages if stage.get("origin")), {}),
            )
            place = {
                "name": f"{anchor.get('name', '目的地')}附近{label}",
                "city": anchor.get("city"),
                "coordinates": anchor.get("coordinates"),
            }
        slot = _closest_free_slot(
            datetime.combine(day_date, window_start, tzinfo=SHANGHAI),
            datetime.combine(day_date, window_end, tzinfo=SHANGHAI),
            preferred=datetime.combine(day_date, preferred_time, tzinfo=SHANGHAI),
            duration_minutes=45,
            occupied=occupied,
            minimum_minutes=30,
        )
        in_transit = False
        fallback_kind = None
        if not slot:
            # A long drive/train/flight often leaves only a short service or
            # rest window.  That is still a real meal opportunity: merge the
            # meal into the existing break first, then into the movement
            # stage.  Previously the code only looked for a stage containing
            # the exact preferred minute; a 11:56–12:16 rest or a stage ending
            # at 18:30 therefore caused lunch/dinner to disappear and made the
            # repair loop stop at 91%.
            fallback = _meal_fallback_slot(
                day_date,
                window_start,
                window_end,
                preferred_time,
                stages,
                activities,
            )
            if fallback is not None:
                slot_start, slot_duration, fallback_kind = fallback
                slot = (slot_start, slot_duration)
                in_transit = True
            else:
                # Do not fabricate an activity outside the requested day.  A
                # day with no executable movement/break is allowed to remain
                # a genuine free day; days with stages are covered by the
                # service/movement fallback above.
                continue
        start_at, duration = slot
        activity = _activity(
            day=day,
            sequence=len(activities),
            activity_type="meal",
            candidate=candidate,
            place=place,
            start_at=start_at,
            duration_minutes=duration,
            sources=candidate.get("source_records", []) if candidate else [],
            opening_text="营业时间与排队情况以当天为准",
            ticket_or_price=candidate.get("ticket_or_price") if candidate else None,
            user_note=(
                (
                    f"{label}；{candidate.get('user_note') or '出发前确认餐厅营业状态'}"
                    if candidate
                    else f"{label}；未返回可靠餐饮候选，可在附近灵活选择"
                )
                + (
                    "；途中用餐（与驾驶/交通或休息补能合并，不新增路线）"
                    if fallback_kind == "movement"
                    else "；途中用餐（与休息/补能合并，不新增路线）"
                    if fallback_kind == "stop"
                    else ""
                )
            ),
            description=candidate.get("description") if candidate else None,
            image_url=candidate.get("image_url") if candidate else None,
            detail_url=candidate.get("detail_url") if candidate else None,
            in_transit=in_transit,
        )
        activities.append(activity)
        occupied.append((start_at, start_at + timedelta(minutes=duration)))


def review_daily_schedule(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
    confirmed_additions: list[dict[str, Any]] | None = None,
    destination: dict[str, Any] | None = None,
    trip_request: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run a second pass over every day and phase after the first schedule.

    The first pass is intentionally conservative while route/weather data is
    still being assembled.  This review pass is idempotent and can safely add
    a missed attraction or overnight stay once all stage times are known.
    It returns non-blocking review notes for days that still have a large free
    window, so the UI can explain that the window is intentional instead of
    looking like an unfinished itinerary.
    """
    reviewed = schedule_tourism_activities(
        day_plans,
        candidates,
        confirmed_additions,
        destination,
        trip_request,
    )
    notes: list[dict[str, Any]] = []
    for day in reviewed:
        stages = day.get("stages", [])
        if not stages:
            continue
        activities = day.get("activities", [])
        meals = [item for item in activities if item.get("type") == "meal"]
        if len(meals) < 3:
            notes.append(
                {
                    "code": "DAILY_MEAL_REVIEW",
                    "severity": "warning",
                    "message": f"{day.get('date')} 的三餐仍需临近出发复核",
                }
            )
        last_stage_end = max(
            datetime.fromisoformat(stage["planned_end"]) for stage in stages
        )
        later_activities = [
            item
            for item in activities
            if item.get("planned_start")
            and datetime.fromisoformat(item["planned_start"]) >= last_stage_end
            and item.get("type") in {"attraction", "meal", "hotel", "rest"}
        ]
        if last_stage_end.hour < 17 and not later_activities:
            # Keep the afternoon/evening visible in the itinerary even when
            # no additional POI can be safely placed. This is an explicit,
            # adjustable free-time block rather than an invented attraction.
            day_date = datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date()
            if last_stage_end.date() != day_date:
                notes.append(
                    {
                        "code": "CROSS_DAY_ARRIVAL_REST",
                        "severity": "info",
                        "message": f"{day.get('date')} 的跨日抵达已留给次日恢复，不安排凌晨景点",
                    }
                )
                continue
            rest_start = max(
                last_stage_end + timedelta(minutes=30),
                datetime.combine(day_date, time(14, 0), tzinfo=SHANGHAI),
            )
            rest_end = datetime.combine(day_date, time(17, 30), tzinfo=SHANGHAI)
            if rest_start < rest_end:
                destination = next(
                    (
                        stage.get("destination")
                        for stage in reversed(stages)
                        if stage.get("destination")
                    ),
                    {"name": "目的地周边"},
                )
                activities.append(
                    _activity(
                        day=day,
                        sequence=len(activities),
                        activity_type="rest",
                        place=destination,
                        start_at=rest_start,
                        duration_minutes=int((rest_end - rest_start).total_seconds() // 60),
                        sources=[],
                        opening_text="可按体力与天气灵活调整",
                        user_note="自由活动 / 休息时段，可按体力调整；晚餐仍按当天窗口安排",
                    )
                )
                activities.sort(key=lambda item: item["planned_start"])
                for sequence, activity in enumerate(activities):
                    activity["sequence"] = sequence
                day["activities"] = activities
                day["items"] = [
                    *[{"type": "stage", "id": stage["id"]} for stage in stages],
                    *[{"type": "activity", "id": item["id"]} for item in activities],
                ]
            notes.append(
                {
                    "code": "DAILY_FREE_WINDOW",
                    "severity": "info",
                    "message": f"{day.get('date')} 到达后保留了下午/晚间自由时间",
                }
            )
    return reviewed, notes


def _reschedule_meals(
    day: dict[str, Any],
    activities: list[dict[str, Any]],
    stages: list[dict[str, Any]],
) -> None:
    meals = sorted(
        (item for item in activities if item.get("type") == "meal"),
        key=lambda item: item["planned_start"],
    )
    if not meals:
        return
    day_date = datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date()
    definitions = [
        (time(6, 0), time(9, 30), time(7, 30)),
        (time(11, 0), time(15, 0), time(12, 0)),
        (time(17, 0), time(22, 0), time(18, 30)),
    ]
    stage_ranges = [
        (
            datetime.fromisoformat(stage["planned_start"]),
            datetime.fromisoformat(stage["planned_end"]),
        )
        for stage in stages
    ]
    fixed_activity_ranges = _occupied_ranges(
        [item for item in activities if item.get("type") != "meal"]
    )
    scheduled_meals: list[tuple[datetime, datetime]] = []
    for meal, (window_start, window_end, preferred_time) in zip(
        meals,
        definitions,
        strict=False,
    ):
        duration = max(30, int(meal.get("duration_minutes") or 45))
        slot = _closest_free_slot(
            datetime.combine(day_date, window_start, tzinfo=SHANGHAI),
            datetime.combine(day_date, window_end, tzinfo=SHANGHAI),
            preferred=datetime.combine(day_date, preferred_time, tzinfo=SHANGHAI),
            duration_minutes=duration,
            occupied=[*stage_ranges, *fixed_activity_ranges, *scheduled_meals],
            minimum_minutes=duration,
        )
        if not slot:
            continue
        start_at, actual_duration = slot
        end_at = start_at + timedelta(minutes=actual_duration)
        meal["planned_start"] = start_at.isoformat()
        meal["planned_end"] = end_at.isoformat()
        meal["duration_minutes"] = actual_duration
        scheduled_meals.append((start_at, end_at))


def _occupied_ranges(
    activities: list[dict[str, Any]],
) -> list[tuple[datetime, datetime]]:
    return [
        (
            datetime.fromisoformat(item["planned_start"]),
            datetime.fromisoformat(item["planned_end"]),
        )
        for item in activities
        if item.get("planned_start") and item.get("planned_end")
    ]


def _closest_free_slot(
    window_start: datetime,
    window_end: datetime,
    *,
    preferred: datetime,
    duration_minutes: int,
    occupied: list[tuple[datetime, datetime]],
    minimum_minutes: int,
) -> tuple[datetime, int] | None:
    gaps: list[tuple[datetime, datetime]] = []
    cursor = window_start
    for start_at, end_at in sorted(occupied):
        if end_at <= window_start or start_at >= window_end:
            continue
        clipped_start = max(start_at, window_start)
        clipped_end = min(end_at, window_end)
        if clipped_start > cursor:
            gaps.append((cursor, clipped_start))
        cursor = max(cursor, clipped_end)
    if cursor < window_end:
        gaps.append((cursor, window_end))

    options: list[tuple[float, datetime, int]] = []
    for gap_start, gap_end in gaps:
        available = int((gap_end - gap_start).total_seconds() // 60)
        actual_duration = min(duration_minutes, available)
        if actual_duration < minimum_minutes:
            continue
        latest_start = gap_end - timedelta(minutes=actual_duration)
        candidate_start = min(max(preferred, gap_start), latest_start)
        options.append(
            (
                abs((candidate_start - preferred).total_seconds()),
                candidate_start,
                actual_duration,
            )
        )
    if not options:
        return None
    _, start_at, actual_duration = min(options, key=lambda item: item[0])
    return start_at, actual_duration


def verify_tourism_plan(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    scheduled_attraction_names = {
        _attraction_name_key(activity.get("place", {}).get("name"))
        for day in day_plans
        for activity in day.get("activities", [])
        if activity.get("type") == "attraction"
    }
    uncovered_required = [
        str(item.get("required_name") or item.get("place", {}).get("name") or "").strip()
        for item in candidates.get("attractions", [])
        if item.get("user_required")
        and item.get("place", {}).get("name")
        and _attraction_name_key(item["place"]["name"]) not in scheduled_attraction_names
    ]
    if uncovered_required:
        issues.append(
            {
                "code": "REQUIRED_PLACES_UNSCHEDULED",
                "severity": "blocker",
                "description": (
                    "用户明确指定的地点尚未进入可执行日程："
                    + "、".join(dict.fromkeys(uncovered_required))
                    + "；规划智能体需要重新分配游览时段，不能静默替换成其他地点。"
                ),
            }
        )
    uncovered_highlights = [
        item.get("place", {}).get("name")
        for item in candidates.get("attractions", [])
        if item.get("must_see")
        and item.get("place", {}).get("name")
        and _attraction_name_key(item["place"]["name"]) not in scheduled_attraction_names
        and not item.get("seasonal_excluded")
    ]
    if uncovered_highlights:
        issues.append(
            {
                "code": "DESTINATION_HIGHLIGHTS_UNCOVERED",
                "severity": "warning",
                "description": (
                    "目的地研究标记的代表性景点未全部进入当前可执行时间窗："
                    + "、".join(uncovered_highlights[:8])
                    + "；可延长天数、减少停留或在编辑面板中加入。"
                ),
            }
        )
    if candidates.get("hotels"):
        for day in day_plans[:-1]:
            if not any(
                activity.get("type") == "hotel"
                for activity in day.get("activities", [])
            ):
                issues.append(
                    {
                        "code": "OVERNIGHT_HOTEL_MISSING",
                        "severity": "blocker",
                        "description": f"{day['date']} 未安排过夜住宿",
                    }
                )
    for day in day_plans:
        try:
            day_date = datetime.fromisoformat(f"{day['date']}T00:00:00+08:00").date()
        except (KeyError, TypeError, ValueError):
            day_date = None
        ranges: list[tuple[datetime, datetime, str]] = [
            (
                datetime.fromisoformat(stage["planned_start"]),
                datetime.fromisoformat(stage["planned_end"]),
                f"移动阶段“{stage.get('title', stage.get('id', '未命名'))}”",
            )
            for stage in day.get("stages", [])
        ]
        for activity in day.get("activities", []):
            start_at = datetime.fromisoformat(activity["planned_start"])
            end_at = datetime.fromisoformat(activity["planned_end"])
            if end_at <= start_at:
                issues.append(
                    {
                        "code": "ACTIVITY_TIME_INCONSISTENT",
                        "severity": "blocker",
                        "description": (
                            f"{activity['place']['name']} 的结束时间不晚于开始时间"
                        ),
                    }
                )
            if (
                day_date is not None
                and activity.get("type") == "attraction"
                and (
                    start_at.date() != day_date
                    or start_at.hour < 7
                    or start_at.hour >= 22
                    or end_at.date() != day_date
                )
            ):
                issues.append(
                    {
                        "code": "ATTRACTION_OUTSIDE_COMFORT_WINDOW",
                        "severity": "blocker",
                        "description": (
                            f"{activity['place']['name']} 被安排在 {start_at:%m月%d日 %H:%M}，"
                            "景点仅允许在当天 07:00–22:00 的白天舒适窗口内游览"
                        ),
                    }
                )
            if activity.get("type") in {"meal", "attraction", "hotel"} and not (
                activity.get("type") == "meal" and activity.get("in_transit") is True
            ):
                ranges.append((start_at, end_at, activity["place"]["name"]))
        for index, (start_at, end_at, name) in enumerate(sorted(ranges)):
            for other_start, _, other_name in sorted(ranges)[index + 1 :]:
                if other_start >= end_at:
                    break
                issues.append(
                    {
                        "code": "ACTIVITY_TIME_OVERLAP",
                        "severity": "blocker",
                        "description": f"{name} 与 {other_name} 的活动时间重叠",
                    }
                )
    return issues


def _activity(
    *,
    day: dict[str, Any],
    sequence: int,
    activity_type: str,
    candidate: dict[str, Any] | None = None,
    place: dict[str, Any],
    start_at: datetime,
    duration_minutes: int,
    sources: list[dict[str, Any]],
    opening_text: str,
    required: bool = False,
    ticket_or_price: dict[str, Any] | None = None,
    user_note: str | None = None,
    description: str | None = None,
    image_url: str | None = None,
    detail_url: str | None = None,
    in_transit: bool = False,
) -> dict[str, Any]:
    end_at = start_at + timedelta(minutes=duration_minutes)
    checks = activity_checks(
        candidate,
        activity_type,
        start_at=start_at,
        end_at=end_at,
    )
    candidate_opening = (candidate or {}).get("opening_hours")
    opening_hours = (
        candidate_opening
        if isinstance(candidate_opening, dict) and candidate_opening.get("text")
        else {"text": opening_text, "confirmed": False}
    )
    return {
        "id": f"activity_{day['id']}_{activity_type}_{sequence}",
        "day_id": day["id"],
        "sequence": sequence,
        "type": activity_type,
        "place": place,
        "planned_start": start_at.isoformat(),
        "planned_end": end_at.isoformat(),
        "duration_minutes": duration_minutes,
        "locked": False,
        "required": required,
        "backup": False,
        "ticket_or_price": ticket_or_price,
        "opening_hours": opening_hours,
        **checks,
        "source_records": sources,
        "user_note": user_note,
        "description": description,
        "image_url": image_url,
        "detail_url": detail_url,
        "in_transit": in_transit,
        "warnings": [],
    }
