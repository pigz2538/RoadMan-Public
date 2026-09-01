from __future__ import annotations

import asyncio
import re
import time as monotonic_time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from langgraph.graph import END, START, StateGraph

from ..core.config import Settings
from ..domain.models import (
    Coordinates,
    DayItemRef,
    DayPlan,
    MoneyRange,
    MovementStage,
    PlaceRef,
    PlanWarning,
    RouteSegment,
    SourceRecord,
)
from ..skills.base import SkillContext
from ..skills.amap import RoutePoint, _haversine_km
from ..skills.registry import SkillRegistry
from .deep_drive import (
    default_vehicle,
    enrich_deep_drive_plan,
    normalize_plan_calendar,
    verify_deep_drive_plan,
)
from .event_research import event_research_summary, research_special_events
from .destination_research import research_destination, research_destinations
from .llm import (
    DeepSeekDestinationPlanAgent,
    DeepSeekDestinationResearchAgent,
    DeepSeekEventResearchAgent,
    DeepSeekPoiCurator,
    DeepSeekPoiRanker,
    DeepSeekPoiSuitabilityAgent,
    DeepSeekRequirementExtractor,
)
from .recommendations import (
    apply_agent_ranking,
    apply_agent_suitability,
    apply_candidate_type_guard,
    plan_attraction_coverage,
    rank_tourism_candidates,
)
from .exclusions import filter_excluded_candidates
from .poi_enrichment import enrich_scheduled_activities, enrich_tourism_candidates
from .seasonality import apply_seasonal_guard, parse_trip_date
from .state import RoadManState
from .tourism import (
    deduplicate_attraction_candidates,
    review_daily_schedule,
    schedule_tourism_activities,
    select_primary_hotel,
    verify_tourism_plan,
)

ProgressCallback = Callable[[str, str, str, int, str, str | None], Awaitable[None]]
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_AUTO_REPAIR_ATTEMPTS = 3
# A return-time phrase is often a preference rather than a minute-precise
# appointment. Keep a half-day planning window before treating the itinerary
# as impossible; a few minutes of drift should not block completion.
RETURN_DEADLINE_GRACE_MINUTES = 12 * 60
RETURN_DEADLINE_SILENT_TOLERANCE_MINUTES = 15
# A station/entrance/hotel geocode can legitimately differ by a couple of
# kilometres. Treat those records as one connected place so a provider's
# entrance coordinate does not create a false route discontinuity; route
# geometry still keeps the exact displayed endpoint visible on the map.
PLACE_CONTINUITY_TOLERANCE_KM = 3.0


def _local_today() -> date:
    """Use the product timezone instead of the container's UTC calendar."""
    return datetime.now(SHANGHAI).date()


def _repair_plan_signature(day_plans: list[dict[str, Any]]) -> tuple[Any, ...]:
    """Return a stable structural fingerprint for one repair-loop result."""
    signature: list[Any] = []
    for day in day_plans:
        stages = tuple(
            (
                str(stage.get("title") or ""),
                str((stage.get("origin") or {}).get("name") or ""),
                str((stage.get("destination") or {}).get("name") or ""),
                str(stage.get("planned_start") or ""),
                str(stage.get("planned_end") or ""),
            )
            for stage in day.get("stages", [])
            if isinstance(stage, dict)
        )
        activities = tuple(
            (
                str(activity.get("type") or ""),
                str((activity.get("place") or {}).get("name") or ""),
                str(activity.get("planned_start") or ""),
                str(activity.get("planned_end") or ""),
            )
            for activity in day.get("activities", [])
            if isinstance(activity, dict)
        )
        signature.append((str(day.get("date") or ""), stages, activities))
    return tuple(signature)


def _return_deadline_issue(
    arrival: datetime,
    deadline: datetime,
) -> dict[str, str] | None:
    """Classify a late return without blocking on minute-level drift.

    The requested return time is a planning target.  A delay of a few minutes
    is silently accepted; a later arrival within the half-day window is shown
    as a warning; only a delay beyond that window blocks verification.
    """
    delay_minutes = max(0, int((arrival - deadline).total_seconds() / 60))
    if delay_minutes <= RETURN_DEADLINE_SILENT_TOLERANCE_MINUTES:
        return None
    if delay_minutes <= RETURN_DEADLINE_GRACE_MINUTES:
        return {
            "code": "RETURN_WINDOW_FLEXIBLE",
            "severity": "warning",
            "description": "返程预计略晚于目标时间，但仍在半天弹性范围内；可按实际路况灵活抵达。",
        }
    return {
        "code": "RETURN_DEADLINE_UNACHIEVABLE",
        "severity": "blocker",
        "description": (
            f"返程预计 {arrival.strftime('%m月%d日 %H:%M')} 抵达，"
            f"晚于用户要求的 {deadline.strftime('%m月%d日 %H:%M')}；"
            "请延长行程、提前离开或改用更快的交通方式。"
        ),
    }


def _normalize_poi_name(value: Any) -> str:
    """Normalize a model-selected POI name for exact identity matching."""
    return "".join(str(value or "").split()).casefold()


def _poi_name_matches(requested: Any, candidate: Any) -> bool:
    """Match a requested venue to a provider label without losing aliases.

    Map providers commonly append an entrance, branch, campus or scenic-area
    suffix to the exact name supplied by the traveller. Treat that as the same
    place, but never let an empty or one-character fragment claim an unrelated
    candidate.
    """
    requested_key = _normalize_poi_name(requested)
    candidate_key = _normalize_poi_name(candidate)
    if not requested_key or not candidate_key:
        return False
    if requested_key == candidate_key:
        return True
    return min(len(requested_key), len(candidate_key)) >= 2 and (
        requested_key in candidate_key or candidate_key in requested_key
    )


def _merge_extracted_place(
    existing: dict[str, Any] | None,
    extracted_name: Any,
    extracted_scope: Any = None,
) -> dict[str, Any]:
    """Reconcile a fresh semantic place with a persisted request.

    Requirement extraction runs again when a planning job starts (and on a
    replan).  Keeping an old nested place whenever it already exists caused a
    new, more-specific answer such as ``郑州`` to be stored only in the
    top-level fields while the route continued geocoding stale ``河南``.  If
    the semantic name changed, discard the old coordinates/POI metadata; if it
    is the same administrative place, preserve the provider-backed coordinates.
    """
    name = str(extracted_name or "").strip()
    if not name:
        return dict(existing or {})
    scope = str(extracted_scope or "").strip().lower()
    administrative_scopes = {"city", "province", "region", "multi_destination"}
    old_name = str((existing or {}).get("name") or "").strip()
    old_key = _normalize_poi_name(old_name)
    new_key = _normalize_poi_name(name)
    same_place = old_key == new_key
    if not same_place and scope in administrative_scopes:
        # “郑州” and “郑州市” are the same administrative anchor, whereas
        # “北京片皮烤鸭” and “北京” are intentionally different.
        same_place = _scope_name(old_name) == _scope_name(name)
    merged = dict(existing or {}) if same_place else {}
    merged["name"] = name
    if scope:
        merged["destination_scope"] = scope
    return merged


def _mark_existing_required_candidates(
    attractions: list[dict[str, Any]],
    required_places: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mark already-discovered must-visits and return names still unresolved.

    Previously an exact candidate was excluded from the directed lookup but
    was left as an ordinary recommendation. Ranking could then silently drop
    a place that the traveller explicitly required.
    """
    unresolved: list[dict[str, Any]] = []
    for required in required_places:
        requested_name = str(required.get("name") or "").strip()
        match = next(
            (
                item
                for item in attractions
                if _poi_name_matches(
                    requested_name,
                    (item.get("place") or {}).get("name"),
                )
            ),
            None,
        )
        if match is None:
            unresolved.append(required)
            continue
        match["user_required"] = True
        match["required_name"] = requested_name
        place = match.setdefault("place", {})
        # Keep the traveller's wording in the itinerary. Provider identifiers,
        # coordinates and evidence remain attached to the same record.
        place["name"] = requested_name
    return unresolved


def _merge_required_lookup_result(
    attractions: list[dict[str, Any]],
    required: dict[str, Any],
    result: Any,
    destination: dict[str, Any],
) -> bool:
    """Merge one directed map lookup into the required candidate pool."""
    if isinstance(result, Exception) or not result.success or not isinstance(result.data, dict):
        return False
    requested_name = str(required.get("name") or "").strip()
    items = [item for item in result.data.get("items", []) if isinstance(item, dict)]
    match = next(
        (
            item
            for item in items
            if _poi_name_matches(requested_name, item.get("name"))
            and item.get("location")
        ),
        None,
    )
    if match is None:
        return False
    try:
        longitude, latitude = str(match["location"]).split(",", 1)
        coordinates_value = {
            "longitude": float(longitude),
            "latitude": float(latitude),
        }
    except (TypeError, ValueError):
        return False
    source_records = [item.model_dump(mode="json") for item in result.sources]
    resolved = next(
        (
            item
            for item in attractions
            if _poi_name_matches(
                requested_name,
                (item.get("place") or {}).get("name"),
            )
        ),
        None,
    )
    if resolved is None:
        resolved = {
            "place": {
                "id": match.get("id") or requested_name,
                "name": requested_name,
                "address": match.get("address"),
                "city": match.get("city") or destination.get("city"),
                "coordinates": coordinates_value,
                "source_id": match.get("id") or requested_name,
            },
            "source_records": [],
        }
        attractions.append(resolved)
    resolved_place = resolved.setdefault("place", {})
    resolved_place["name"] = requested_name
    resolved_place["coordinates"] = coordinates_value
    resolved_place.setdefault("city", match.get("city") or destination.get("city"))
    resolved["source_records"] = [
        *resolved.get("source_records", []),
        *source_records,
        {
            "provider": "高德地图",
            "title": f"{requested_name} 指定景点详情",
            "url": f"https://www.amap.com/search?query={quote(requested_name)}",
        },
    ]
    resolved["detail_url"] = f"https://www.amap.com/search?query={quote(requested_name)}"
    resolved["image_url"] = (match.get("photos") or [None])[0] or resolved.get("image_url")
    resolved["provider"] = result.provider
    resolved["user_required"] = True
    resolved["required_name"] = requested_name
    return True


def _merge_required_geocode_result(
    attractions: list[dict[str, Any]],
    required: dict[str, Any],
    result: Any,
    destination: dict[str, Any],
) -> bool:
    """Promote a successful semantic geocode to a hard-required attraction.

    A region-level request (for example a province or autonomous region) can
    legitimately have no exact text-search hit for a named scenic area.  The
    geocoder still returns a concrete, provider-backed coordinate in many of
    those cases.  Treat that result as an executable candidate instead of
    dropping the user's requirement or replacing it with a random nearby POI.
    This remains provider/agent driven; no destination names are embedded in
    the planner.
    """
    if (
        isinstance(result, Exception)
        or not getattr(result, "success", False)
        or not isinstance(getattr(result, "data", None), dict)
    ):
        return False
    data = result.data
    location = str(data.get("location") or "").strip()
    if not location:
        return False
    try:
        longitude, latitude = location.split(",", 1)
        coordinates = {
            "longitude": float(longitude),
            "latitude": float(latitude),
        }
    except (TypeError, ValueError):
        return False
    requested_name = str(required.get("name") or "").strip()
    if not requested_name:
        return False

    resolved = next(
        (
            item
            for item in attractions
            if _poi_name_matches(
                requested_name,
                (item.get("place") or {}).get("name"),
            )
        ),
        None,
    )
    if resolved is None:
        resolved = {
            "place": {
                "id": f"required-geocode:{requested_name}",
                "name": requested_name,
                "address": data.get("formatted_address"),
                "city": data.get("city") or destination.get("city"),
                "coordinates": coordinates,
                "source_id": f"required-geocode:{requested_name}",
            },
            "source_records": [],
        }
        attractions.append(resolved)
    place = resolved.setdefault("place", {})
    place.update(
        {
            "name": requested_name,
            "address": place.get("address") or data.get("formatted_address"),
            "city": place.get("city") or data.get("city") or destination.get("city"),
            "coordinates": coordinates,
        }
    )
    source_records = [
        item.model_dump(mode="json")
        for item in getattr(result, "sources", [])
    ]
    resolved["source_records"] = [
        *resolved.get("source_records", []),
        *source_records,
        {
            "provider": "高德地图",
            "title": f"{requested_name} 指定景点地理编码",
            "url": f"https://www.amap.com/search?query={quote(requested_name)}",
        },
    ]
    resolved["detail_url"] = f"https://www.amap.com/search?query={quote(requested_name)}"
    resolved["user_required"] = True
    resolved["required_name"] = requested_name
    resolved.pop("lookup_unresolved", None)
    return True


def _destination_search_area(destination: dict[str, Any]) -> str:
    """Return the provider search scope without collapsing admin regions."""
    scope = str(destination.get("destination_scope") or "unknown").strip().lower()
    if scope in {"province", "region", "multi_destination"}:
        return str(
            destination.get("province")
            or destination.get("city")
            or destination.get("name")
            or ""
        ).strip()
    return str(destination.get("city") or destination.get("name") or "").strip()


# A scenic POI is a local anchor rather than a whole-city sightseeing brief.
# The radius is a semantic planning boundary, not a hard-coded destination
# list: the requirement extractor's scope and the resolved administrative
# fields decide whether it applies.  Fifty kilometres leaves room for nearby
# viewpoints and villages while preventing a two-day lake trip from drifting
# to an unrelated entertainment district.
DESTINATION_FOCUS_RADIUS_KM = 50.0
EXPLICIT_LOCAL_FOCUS_RADIUS_KM = 35.0


def _scope_name(value: Any) -> str:
    """Normalize a place label for generic city/POI scope comparison."""
    text = _normalize_poi_name(value)
    # Provider and model responses alternate between bare administrative names
    # ("河南", "郑州") and their formal suffixes ("河南省", "郑州市").
    # Treat those spellings as one anchor, but leave ordinary POI names intact.
    return re.sub(
        r"(?:特别行政区|自治区|自治州|地区|省|市|县|区|盟|旗)$",
        "",
        text,
    )


def _is_local_destination_anchor(destination: dict[str, Any] | None) -> bool:
    """Return whether sightseeing should stay around one named local anchor.

    City/province/region requests intentionally remain broad so famous
    landmarks in different districts can be covered.  A POI-scoped request,
    or an otherwise-unknown place whose resolved city differs from its name,
    is treated as a scenic/local anchor.  No destination names are embedded
    here; the decision comes from the semantic extraction and geocoder data.
    """
    if not destination:
        return False
    scope = str(destination.get("destination_scope") or "unknown").strip().lower()
    if scope == "poi":
        return True
    if scope in {"city", "province", "region", "multi_destination"}:
        return False
    level = str(destination.get("geocode_level") or "").strip().lower()
    if level in {"poi", "兴趣点", "门址", "门牌", "street", "road"}:
        return True
    name = _scope_name(destination.get("name"))
    city = _scope_name(destination.get("city"))
    return bool(name and city and name != city)


def _destination_focus_radius(
    destination: dict[str, Any] | None,
    *,
    explicit_local: bool = False,
) -> float | None:
    """Return the local focus radius, or ``None`` for broad city research."""
    if not destination:
        return None
    if explicit_local or destination.get("stay_only_at_destination"):
        return EXPLICIT_LOCAL_FOCUS_RADIUS_KM
    return DESTINATION_FOCUS_RADIUS_KM if _is_local_destination_anchor(destination) else None


def _is_authoritative_admin_geocode(requested_name: Any, geocode: dict[str, Any]) -> bool:
    """Return whether a geocoder result is authoritative for an admin place.

    ``_ensure_coordinates`` has a nearby-POI fallback for genuinely ambiguous
    short scenic names (``乌镇`` is a common example).  That fallback must not
    run for a city-level request such as ``北京``: searching nearby Wuhan POIs
    for the word "北京" can return ``北京片皮烤鸭`` and silently turn the trip's
    destination into a restaurant.  We intentionally infer this from AMap's
    returned administrative fields instead of maintaining a hard-coded city
    list, so the same guard works for Chinese, English, and arbitrary regions.
    """
    requested = _normalize_poi_name(requested_name)
    if not requested:
        return False

    level = _normalize_poi_name(geocode.get("level"))
    # AMap documents these levels as 省/市/区县.  Accept the English forms as
    # well because adapters/mocks may normalize the response.
    if level in {"省", "市", "区县", "province", "city", "district", "county"}:
        return any(
            requested == _normalize_poi_name(geocode.get(field))
            or requested == _normalize_poi_name(str(geocode.get(field) or "").removesuffix("市"))
            or requested == _normalize_poi_name(str(geocode.get(field) or "").removesuffix("省"))
            or requested == _normalize_poi_name(str(geocode.get(field) or "").removesuffix("区"))
            or requested == _normalize_poi_name(str(geocode.get(field) or "").removesuffix("县"))
            for field in ("province", "city", "district")
        )

    # Some AMap responses/mocks omit ``level``.  Matching the requested value
    # to an administrative field is still strong evidence that this is a
    # city/region result, while a town/scenic name normally only appears in
    # ``township`` or the formatted address and remains eligible for the
    # nearby disambiguation path.
    for field in ("province", "city", "district"):
        value = str(geocode.get(field) or "")
        if requested in {
            _normalize_poi_name(value),
            _normalize_poi_name(value.removesuffix("市")),
            _normalize_poi_name(value.removesuffix("省")),
            _normalize_poi_name(value.removesuffix("区")),
            _normalize_poi_name(value.removesuffix("县")),
        }:
            return True
    return False


def build_planning_graph(
    registry: SkillRegistry,
    settings: Settings,
    progress_callback: ProgressCallback | None = None,
):
    extractor = DeepSeekRequirementExtractor(settings)
    event_research_agent = DeepSeekEventResearchAgent(settings)
    poi_curator = DeepSeekPoiCurator(settings)
    poi_ranker = DeepSeekPoiRanker(settings)
    poi_suitability_agent = DeepSeekPoiSuitabilityAgent(settings)
    destination_research_agent = DeepSeekDestinationResearchAgent(settings)
    destination_plan_agent = DeepSeekDestinationPlanAgent(settings)

    async def emit(
        state: RoadManState,
        node: str,
        label: str,
        progress: int,
        event: str = "node_started",
        tool: str | None = None,
    ) -> None:
        if progress_callback:
            await progress_callback(state["trip_id"], node, label, progress, event, tool)

    async def load_context(state: RoadManState) -> dict[str, Any]:
        await emit(state, "load_context", "正在加载行程与车辆上下文", 5)
        return {
            "progress": {"node": "load_context", "value": 5, "label": "正在加载上下文"},
            "warnings": state.get("warnings", []),
            "messages": state.get("messages", []),
            "clarification_answers": state.get("clarification_answers", []),
        }

    async def extract_trip_request(state: RoadManState) -> dict[str, Any]:
        await emit(state, "extract_trip_request", "正在理解出发地、目的地与日期", 15)
        extracted = await extractor.extract(state["raw_input"], _local_today())
        current = dict(state.get("trip_request", {}))
        # A fresh semantic extraction is authoritative for route anchors. Do
        # not merely fill an empty nested object: preflight may have stored a
        # broad parent (河南) while the worker's second Agent turn resolves
        # the explicit child (郑州). Merging by field leaves the UI and map on
        # different places and makes destination research use a stale center.
        if extracted.get("origin_name"):
            current["origin"] = _merge_extracted_place(
                current.get("origin"),
                extracted.get("origin_name"),
            )
        if extracted.get("destination_name"):
            current["destination"] = _merge_extracted_place(
                current.get("destination"),
                extracted.get("destination_name"),
                extracted.get("destination_scope"),
            )
        elif extracted.get("destination_scope") and current.get("destination"):
            current["destination"]["destination_scope"] = extracted["destination_scope"]
        destination_names = extracted.get("destination_names")
        if isinstance(destination_names, list):
            current["destination_names"] = [
                str(item).strip()
                for item in destination_names
                if isinstance(item, str) and item.strip()
            ][:20]
        elif extracted.get("destination_name"):
            current["destination_names"] = [str(extracted["destination_name"]).strip()]
        if extracted.get("destination_scope"):
            current["destination_scope"] = extracted["destination_scope"]
        if isinstance(extracted.get("travel_intents"), list):
            current["travel_intents"] = list(
                dict.fromkeys(
                    [
                        *current.get("travel_intents", []),
                        *[
                            str(item).strip()
                            for item in extracted["travel_intents"]
                            if isinstance(item, str) and item.strip()
                        ],
                    ]
                )
            )
        defaults = set(current.get("defaults_applied", []))
        for field in ("start_date", "end_date", "departure_time", "return_time", "travelers", "max_days"):
            if extracted.get(field) is not None and (
                current.get(field) is None
                or (field == "travelers" and "travelers=1" in defaults)
            ):
                current[field] = extracted[field]
                if field == "travelers" and extracted[field] != 1:
                    # A retry may be starting from a request that previously
                    # applied the visible ``travelers=1`` default.  Once the
                    # requirement Agent has resolved a semantic party size,
                    # remove that stale marker so the UI and audit trail do
                    # not claim the value was still a default.
                    defaults.discard("travelers=1")
        if extracted.get("stay_only_at_destination") is True:
            current["stay_only_at_destination"] = True
        extracted_must_visit = extracted.get("must_visit_names") or []
        if isinstance(extracted_must_visit, list):
            existing_names = [
                str(item.get("name") or "").strip()
                for item in current.get("must_visit", [])
                if isinstance(item, dict)
            ]
            for name in extracted_must_visit:
                normalized = str(name or "").strip()
                if normalized and not any(
                    _poi_name_matches(normalized, existing)
                    for existing in existing_names
                ):
                    current.setdefault("must_visit", []).append({"name": normalized})
                    existing_names.append(normalized)
        current["defaults_applied"] = list(dict.fromkeys(defaults))
        current["raw_text"] = state["raw_input"]
        current["preferences"] = list(
            dict.fromkeys([*current.get("preferences", []), *extracted.get("preferences", [])])
        )
        current["transport_modes"] = list(
            dict.fromkeys(
                [*current.get("transport_modes", []), *extracted.get("transport_modes", [])]
            )
        )
        current["special_events"] = list(
            dict.fromkeys([*current.get("special_events", []), *extracted.get("special_events", [])])
        )
        return {
            "trip_request": current,
            "progress": {"node": "extract_trip_request", "value": 15},
        }

    async def research_events(state: RoadManState) -> dict[str, Any]:
        request = state.get("trip_request", {})
        events = list(request.get("special_events", []))
        if not events:
            return {"special_event_research": [], "progress": {"node": "research_events", "value": 18}}
        await emit(
            state,
            "research_events",
            "事件核验智能体正在核对极大值、观测窗口与公开来源",
            18,
            event="tool_started",
            tool="web.event_research",
        )
        try:
            year = date.fromisoformat(request.get("start_date", "")).year
        except (TypeError, ValueError):
            year = _local_today().year
        research = await research_special_events(
            events,
            year=year,
            destination=(request.get("destination") or {}).get("name"),
            fact_agent=event_research_agent.extract,
        )
        await emit(
            state,
            "research_events",
            "事件核验智能体已返回公开资料，安排时会避开不可验证的硬时间承诺",
            19,
            event="tool_completed",
            tool="web.event_research",
        )
        return {
            "special_event_research": research,
            "warnings": [
                *state.get("warnings", []),
                *[
                    {
                        "code": "SPECIAL_EVENT_REVIEW",
                        "message": event_research_summary(item),
                        "severity": "info",
                    }
                    for item in research
                ],
            ],
            "progress": {"node": "research_events", "value": 19},
        }

    async def apply_defaults(state: RoadManState) -> dict[str, Any]:
        await emit(state, "apply_defaults", "正在应用可见默认值", 22)
        request = dict(state["trip_request"])
        defaults = list(request.get("defaults_applied", []))
        if request.get("start_date") and not request.get("end_date"):
            request["end_date"] = (
                date.fromisoformat(request["start_date"]) + timedelta(days=1)
            ).isoformat()
            defaults.append("end_date=start_date+1day")
        request.setdefault("max_continuous_drive_minutes", 120)
        request.setdefault("max_daily_drive_minutes", 540)
        request["defaults_applied"] = list(dict.fromkeys(defaults))
        return {"trip_request": request, "progress": {"node": "apply_defaults", "value": 22}}

    async def validate_required_fields(state: RoadManState) -> dict[str, Any]:
        await emit(state, "validate_required_fields", "正在检查规划所需信息", 28)
        request = state["trip_request"]
        missing = [
            field
            for field in ("origin", "destination", "start_date", "end_date")
            if not request.get(field)
        ]
        return {
            "missing_fields": missing,
            "clarification_question": None,
            "progress": {"node": "validate_required_fields", "value": 28},
        }

    async def generate_clarification(state: RoadManState) -> dict[str, Any]:
        missing = state["missing_fields"]
        labels = {
            "origin": "从哪里出发",
            "destination": "想去哪里",
            "start_date": "哪天出发",
            "end_date": "计划哪天返回",
        }
        round_number = min(
            state.get("clarification_round", 0) + 1,
            settings.max_clarification_rounds,
        )
        if round_number >= settings.max_clarification_rounds:
            question = "还缺少：" + "、".join(labels[item] for item in missing) + "。请一次补充完整。"
        else:
            question = f"请告诉我{labels[missing[0]]}？"
        await emit(
            state,
            "generate_clarification",
            question,
            30,
            event="clarification_required",
        )
        return {
            "clarification_round": round_number,
            "clarification_question": question,
            "messages": [
                *state.get("messages", []),
                {"role": "assistant", "type": "clarification", "content": question},
            ],
            "progress": {"node": "generate_clarification", "value": 30, "paused": True},
        }

    async def build_base_route(state: RoadManState) -> dict[str, Any]:
        request = dict(state["trip_request"])
        requested_modes = {
            str(mode).strip().casefold()
            for mode in request.get("transport_modes", [])
            if str(mode).strip()
        }
        if "ship" in requested_modes or "boat" in requested_modes:
            requested_modes.add("ferry")
        intercity_order = [
            mode for mode in ("train", "flight", "ferry")
            if mode in requested_modes
        ]
        local_order = [
            mode for mode in ("transit", "walking", "riding")
            if mode in requested_modes
        ]
        cross_sea_mode = str(request.get("cross_sea_mode") or "").casefold()
        cross_sea_mode = {
            "ship": "ferry",
            "boat": "ferry",
            "ferryboat": "ferry",
            "轮船": "ferry",
            "渡轮": "ferry",
            "船": "ferry",
            "飞机": "flight",
            "桥": "bridge",
            "跨海大桥": "bridge",
        }.get(cross_sea_mode, cross_sea_mode)
        if (
            not intercity_order
            and request.get("cross_sea_required") is True
            and cross_sea_mode in {"ferry", "flight"}
        ):
            intercity_order.append(cross_sea_mode)
        explicit_intercity = bool(intercity_order)
        explicit_local = not explicit_intercity and bool(local_order)
        primary_mode = (
            intercity_order[0]
            if intercity_order
            else local_order[0]
            if local_order
            else "driving"
        )
        primary_tool = f"flyai.{primary_mode}" if explicit_intercity else "amap.route"
        await emit(
            state,
            "build_base_route",
            (
                f"正在查询 FlyAI {_intercity_mode_label(primary_mode)}与接驳"
                if explicit_intercity
                else f"正在查询高德{_transport_mode_label(primary_mode)}路线"
                if explicit_local
                else "正在查询真实驾车道路（默认方式）"
            ),
            40,
            event="tool_started",
            tool=primary_tool,
        )
        origin = await _ensure_coordinates(registry, request["origin"], state["trip_id"])
        destination = await _ensure_coordinates(
            registry,
            request["destination"],
            state["trip_id"],
            nearby=origin,
        )
        request["origin"] = origin
        request["destination"] = destination
        if not origin.get("coordinates") or not destination.get("coordinates"):
            return {
                "trip_request": request,
                "error": {"code": "GEOCODE_UNAVAILABLE", "message": "无法确定起终点坐标"},
                "route_candidates": [],
            }
        warnings: list[dict[str, Any]] = []
        start_date = _parse_request_date(request.get("start_date")) or _local_today()
        end_date = _parse_request_date(request.get("end_date")) or start_date
        departure_at = _request_clock(
            start_date,
            request.get("departure_time"),
            default=time(8, 0),
        )
        deadline = _request_clock(
            end_date,
            request.get("return_time"),
            default=time(20, 0),
        )

        # A province/region is a valid travel destination, but scheduled
        # providers need a concrete gateway city.  Resolve that gateway with
        # the semantic travel-search Agent (for example, 青海 -> 西宁) instead
        # of maintaining a brittle province-name table or querying a region
        # name as if it were a station.  The geographic anchor remains the
        # original resolved destination; only the provider query label is
        # enriched.
        if explicit_intercity or (
            str(request.get("destination_scope") or "").strip().lower()
            in {"province", "region"}
        ):
            for place in (origin, destination):
                transport_city, transport_warnings, transport_sources = (
                    await _resolve_transport_gateway(
                        registry,
                        place,
                        state["trip_id"],
                    )
                )
                warnings.extend(transport_warnings)
                if transport_sources:
                    place.setdefault("transport_sources", []).extend(transport_sources)
                if transport_city:
                    place["transport_city"] = transport_city

        async def scheduled(
            mode: str,
            leg_origin: dict[str, Any],
            leg_destination: dict[str, Any],
            travel_date: date,
            *,
            requested_departure: datetime | None = None,
            arrival_deadline: datetime | None = None,
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            if mode == "train":
                return await _train_route(
                    registry,
                    leg_origin,
                    leg_destination,
                    state["trip_id"],
                    travel_date=travel_date,
                    requested_departure=requested_departure,
                    arrival_deadline=arrival_deadline,
                )
            if mode == "flight":
                return await _flight_route(
                    registry,
                    leg_origin,
                    leg_destination,
                    state["trip_id"],
                    travel_date=travel_date,
                    requested_departure=requested_departure,
                    arrival_deadline=arrival_deadline,
                )
            return await _ferry_route(
                registry,
                leg_origin,
                leg_destination,
                state["trip_id"],
                travel_date=travel_date,
                requested_departure=requested_departure,
                arrival_deadline=arrival_deadline,
            )

        async def resolve_leg(
            leg_origin: dict[str, Any],
            leg_destination: dict[str, Any],
            travel_date: date,
            *,
            requested_departure: datetime | None = None,
            arrival_deadline: datetime | None = None,
        ) -> dict[str, Any]:
            # A non-driving mode is used only when the Requirement Agent
            # explicitly selected/allowed it (or it is required by a semantic
            # cross-sea decision).  With no such signal, driving is always the
            # first and only normal attempt.
            # A failed default driving lookup remains a driving failure.  It
            # must never silently turn a user's self-drive trip into a train,
            # flight, public-transit or riding itinerary.
            attempted: list[str] = []
            last_route: dict[str, Any] = {"success": False, "error_code": "ROUTE_UNAVAILABLE"}
            if explicit_intercity:
                # Compare every explicitly allowed intercity schedule.  The
                # first provider in the list must not win when it arrives in
                # the small hours and another allowed mode has a daytime
                # option; otherwise the local planner can put sightseeing at
                # 03:00 immediately after the terminal transfer.
                intercity_results: list[tuple[str, dict[str, Any]]] = []
                for mode in intercity_order:
                    attempted.append(mode)
                    route, mode_warnings = await scheduled(
                        mode,
                        leg_origin,
                        leg_destination,
                        travel_date,
                        requested_departure=requested_departure,
                        arrival_deadline=arrival_deadline,
                    )
                    warnings.extend(mode_warnings)
                    if route.get("success"):
                        intercity_results.append((mode, route))
                    else:
                        last_route = route
                if intercity_results:
                    selected_mode, selected = min(
                        intercity_results,
                        key=lambda item: _intercity_route_quality(
                            item[1],
                            requested_departure=requested_departure,
                            arrival_deadline=arrival_deadline,
                            mode_order=intercity_order.index(item[0]),
                        ),
                    )
                    if selected_mode != primary_mode:
                        warnings.append(
                            {
                                "code": "INTERCITY_COMFORT_OPTIMIZED",
                                "message": (
                                    f"已在允许的交通方式中选择{_transport_mode_label(selected_mode)}，"
                                    "优先避开凌晨抵达并保留舒适接驳时间"
                                ),
                                "severity": "info",
                            }
                        )
                    return selected
                # Never manufacture a timetable when every provider failed.
                # A requested flight/train without a real service number is
                # not an executable route and must not enter later planning.
                last_route.setdefault("warnings", []).append(
                    f"未从多个班次数据源取得可核对的{_intercity_mode_label(intercity_order[0])}"
                )
                return last_route
            modes = [*local_order, "driving"] if explicit_local else ["driving"]
            for mode in modes:
                attempted.append(mode)
                if mode in {"driving", "transit", "walking", "riding"}:
                    route = await _route(
                        registry,
                        leg_origin,
                        leg_destination,
                        state["trip_id"],
                        preferred_mode=mode,
                    )
                    mode_warnings: list[dict[str, Any]] = []
                else:
                    route, mode_warnings = await scheduled(
                        mode,
                        leg_origin,
                        leg_destination,
                        travel_date,
                        requested_departure=requested_departure,
                        arrival_deadline=arrival_deadline,
                    )
                warnings.extend(mode_warnings)
                if route.get("success"):
                    if mode != primary_mode:
                        warnings.append(
                            {
                                "code": "TRANSPORT_FALLBACK_USED",
                                "message": (
                                    f"{_transport_mode_label(primary_mode)}不可用，"
                                    f"已切换为{_transport_mode_label(mode)}"
                                ),
                                "severity": "warning",
                            }
                        )
                    return route
                last_route = route
            last_route.setdefault("warnings", []).append(
                f"已尝试交通方式：{'、'.join(_transport_mode_label(mode) for mode in attempted)}"
            )
            return last_route

        outbound = await resolve_leg(
            origin,
            destination,
            start_date,
            requested_departure=departure_at,
        )
        inbound = await resolve_leg(
            destination,
            origin,
            end_date,
            arrival_deadline=deadline,
        )

        # No mode was stated: keep both intercity legs as driving.  Feasibility
        # repair may split a long drive and add rest stops, but it must never
        # upgrade the traveller to air or rail without an explicit request.
        candidates = [item for item in (outbound, inbound) if item.get("success")]
        error = None
        if not outbound.get("success"):
            error = {"code": outbound.get("error_code") or "ROUTE_UNAVAILABLE", "message": "去程路线不可用"}
        elif not inbound.get("success"):
            error = {
                "code": inbound.get("error_code") or "ROUTE_UNAVAILABLE",
                "message": "返程路线不可用",
            }
        await emit(
            state,
            "build_base_route",
            (
                f"{_intercity_mode_label(primary_mode)}与接驳查询完成"
                if explicit_intercity
                else f"高德{_transport_mode_label(primary_mode)}路线查询完成"
                if explicit_local
                else "真实驾车道路查询完成"
            )
            if not error
            else "路线查询未成功",
            55,
            event="tool_completed",
            tool=primary_tool,
        )
        return {
            "trip_request": request,
            "route_candidates": candidates,
            "selected_route": outbound if outbound.get("success") else None,
            "error": error,
            "sources": [
                source
                for route in candidates
                for source in route.get("sources", [])
            ],
            "warnings": [*state.get("warnings", []), *warnings],
            "progress": {"node": "build_base_route", "value": 55},
        }

    async def split_into_days(state: RoadManState) -> dict[str, Any]:
        await emit(state, "split_into_days", "正在按日期拆分行程", 64)
        request = state["trip_request"]
        start = date.fromisoformat(request["start_date"])
        end = date.fromisoformat(request["end_date"])
        dates = [
            (start + timedelta(days=index)).isoformat()
            for index in range(max(1, (end - start).days + 1))
        ]
        return {
            "day_plans": [{"date": value, "day_index": index + 1} for index, value in enumerate(dates)],
            "progress": {"node": "split_into_days", "value": 64},
        }

    async def discover_tourism(state: RoadManState) -> dict[str, Any]:
        trip_request = state["trip_request"]
        destination = trip_request["destination"]
        # City/province requests stay broad so the destination research
        # intelligent agent can cover famous landmarks across districts. A
        # named scenic POI is a different semantic shape: short stays should
        # remain around that anchor even when the user did not literally say
        # “只在附近”. This boundary is inferred from destination scope and
        # geocoder fields, never from a destination-name catalogue.
        stay_only_at_destination = bool(
            trip_request.get("stay_only_at_destination")
        )
        destination_focus_radius_km = _destination_focus_radius(
            destination,
            explicit_local=stay_only_at_destination,
        )
        await emit(
            state,
            "destination_research",
            "目的地研究智能体正在检索目的地必去景点、代表性美食与来源",
            65,
            event="tool_started",
            tool="web.destination_research",
        )
        destination_names = [
            str(item).strip()
            for item in (trip_request.get("destination_names") or [])
            if isinstance(item, str) and item.strip()
        ]
        if not destination_names:
            destination_names = [_destination_search_area(destination)]
        if len(destination_names) == 1:
            destination_research = await research_destination(
                registry,
                destination_names[0],
                state["trip_id"],
            )
            research_bundles = [destination_research]
        else:
            destination_research = await research_destinations(
                registry,
                destination_names,
                state["trip_id"],
            )
            research_bundles = [
                item
                for item in destination_research.get("destinations", [])
                if isinstance(item, dict)
            ]
        recommendations: list[dict[str, Any]] = []
        research_timeout = min(45, max(15, int(settings.deepseek_timeout_seconds)))
        try:
            for bundle in research_bundles:
                bundle_recommendations = await asyncio.wait_for(
                    destination_research_agent.summarize(
                        str(bundle.get("destination") or destination_names[0]),
                        bundle,
                        state.get("trip_request", {}),
                    ),
                    timeout=research_timeout,
                )
                for item in bundle_recommendations:
                    if isinstance(item, dict):
                        recommendations.append(
                            {**item, "research_destination": bundle.get("destination")}
                        )
        except asyncio.TimeoutError:
            destination_research["agent_error"] = "DEEPSEEK_DESTINATION_RESEARCH_TIMEOUT"
        destination_research["agent_recommendations"] = recommendations[:48]
        destination_research["destination_names"] = destination_names
        try:
            await emit(
                state,
                "destination_plan",
                "目的地策划智能体正在根据研究结果生成分区与每日计划单",
                66,
                event="tool_started",
                tool="deepseek.destination_plan",
            )
            destination_research["agent_plan"] = await asyncio.wait_for(
                destination_plan_agent.draft(
                    destination_names,
                    destination_research,
                    state.get("trip_request", {}),
                ),
                timeout=research_timeout,
            )
        except asyncio.TimeoutError:
            destination_research["agent_plan"] = {}
            destination_research["agent_plan_error"] = "DEEPSEEK_DESTINATION_PLAN_TIMEOUT"
        await emit(
            state,
            "destination_plan",
            "目的地策划智能体已生成分区与每日计划单，交给路线智能体执行",
            67,
            event="tool_completed",
            tool="deepseek.destination_plan",
        )
        await emit(
            state,
            "destination_research",
            f"目的地研究完成：找到 {len(destination_research.get('sources', []))} 条来源，交给候选排序智能体决定是否纳入",
            66,
            event="tool_completed",
            tool="web.destination_research",
        )
        await emit(
            state,
            "flyai_poi_attractions",
            "旅行信息搜索智能体正在检索景点与门票候选",
            65,
            event="tool_started",
            tool="flyai.poi",
        )
        coordinates = destination.get("coordinates")
        categories = {
            "attractions": ("景点", 25),
            "meals": ("餐厅", 20),
            "hotels": ("酒店", 20),
        }
        candidates: dict[str, list[dict[str, Any]]] = {
            key: [] for key in categories
        }
        tourism_sources: list[dict[str, Any]] = []
        flyai_ticket_items: list[dict[str, Any]] = []
        flyai_pois = await registry.execute(
            "flyai.poi",
            {
                "city_name": _destination_search_area(destination),
                # Do not search a province/region by its bare name.  Providers
                # may return a restaurant or a university whose name contains
                # that region.  The semantic destination Agent has already
                # supplied the canonical scope; the provider query should ask
                # for tourist landmarks inside it.
                "keyword": (
                    f"{destination['name']} 著名旅游景点"
                    if str(destination.get("destination_scope") or "unknown")
                    in {"province", "region", "multi_destination", "city"}
                    else destination["name"]
                ),
            },
            SkillContext(trip_id=state["trip_id"]),
        )
        if flyai_pois.success and isinstance(flyai_pois.data, dict):
            flyai_ticket_items = list(flyai_pois.data.get("items", []))
            tourism_sources.extend(
                item.model_dump(mode="json") for item in flyai_pois.sources
            )
        await emit(
            state,
            "flyai_poi_attractions",
            (
                f"FlyAI 景点候选已返回 {len(flyai_ticket_items)} 项"
                if flyai_pois.success
                else "FlyAI 景点搜索暂不可用，后续由高德与其他来源补充"
            ),
            65,
            event="tool_completed",
            tool="flyai.poi",
        )
        await emit(
            state,
            "discover_tourism",
            "正在检索高德景点、餐饮与住宿候选",
            65,
            event="tool_started",
            tool="amap.poi",
        )
        # FlyAI is also a first-class source for dining candidates.  AMap is
        # still queried below for road-side coverage, but keeping this call
        # separate lets the POI/ranking agents compare richer restaurant and
        # meal metadata instead of silently falling back to one provider.
        await emit(
            state,
            "flyai_poi_meals",
            "旅行信息搜索智能体正在检索餐饮候选与营业信息",
            65,
            event="tool_started",
            tool="flyai.poi",
        )
        flyai_meals = await registry.execute(
            "flyai.poi",
            {
                "city_name": _destination_search_area(destination),
                "keyword": "餐厅",
            },
            SkillContext(trip_id=state["trip_id"], metadata={"category": "meals"}),
        )
        if flyai_meals.success and isinstance(flyai_meals.data, dict):
            meal_sources = [
                item.model_dump(mode="json") for item in flyai_meals.sources
            ]
            tourism_sources.extend(meal_sources)
            existing_meal_names = {
                _normalize_poi_name(item.get("place", {}).get("name", ""))
                for item in candidates["meals"]
            }
            for item in flyai_meals.data.get("items", []):
                name = str(item.get("name") or "").strip()
                longitude, latitude = item.get("longitude"), item.get("latitude")
                if not name or longitude is None or latitude is None:
                    continue
                normalized = _normalize_poi_name(name)
                if normalized in existing_meal_names:
                    continue
                try:
                    longitude_value = float(longitude)
                    latitude_value = float(latitude)
                except (TypeError, ValueError):
                    continue
                candidates["meals"].append(
                    {
                        "place": {
                            "id": item.get("id") or name,
                            "name": name,
                            "address": item.get("address"),
                            "city": destination.get("city"),
                            "coordinates": {
                                "longitude": longitude_value,
                                "latitude": latitude_value,
                            },
                            "source_id": item.get("id") or name,
                        },
                        "categories": item.get("categories") or item.get("kinds"),
                        "detail_url": item.get("detail_url"),
                        "image_url": item.get("image_url"),
                        "rating": item.get("rating"),
                        "source_records": [
                            *meal_sources,
                            {
                                "provider": "FlyAI / 飞猪",
                                "title": f"{name} 餐饮详情",
                                "url": item.get("detail_url") or "https://www.fliggy.com/",
                            },
                        ],
                        "provider": flyai_meals.provider,
                    }
                )
                existing_meal_names.add(normalized)
        await emit(
            state,
            "flyai_poi_meals",
            (
                f"旅行信息搜索智能体已返回 {len(candidates['meals'])} 项餐饮候选，交由候选排序智能体去重排序"
                if flyai_meals.success
                else "FlyAI 餐饮搜索暂不可用，后续由高德餐饮候选补充"
            ),
            65,
            event="tool_completed",
            tool="flyai.poi",
        )
        await emit(
            state,
            "flyai_hotels",
            "旅行信息搜索智能体正在检索入住日期内的酒店与民宿候选",
            65,
            event="tool_started",
            tool="flyai.hotel",
        )
        hotel_payload: dict[str, Any] = {
            "destination": _destination_search_area(destination),
            "poi_name": destination["name"],
            "check_in_date": state["trip_request"]["start_date"],
            "check_out_date": state["trip_request"]["end_date"],
            "sort": "rate_desc",
        }
        # Keep provider cards anchored to the resolved destination.  A broad
        # hotel search can otherwise return stale cards from an unrelated
        # city (or even another country), which then makes the hotel selector
        # place the whole itinerary beside an airport or a random centroid.
        # Province/region searches remain wider. A named scenic anchor gets a
        # focused hotel radius so the daily base is actually near the stay.
        if coordinates:
            try:
                hotel_payload.update(
                    {
                        "center_longitude": float(coordinates["longitude"]),
                        "center_latitude": float(coordinates["latitude"]),
                        "max_distance_km": (
                            260.0
                            if str(destination.get("destination_scope") or "")
                            in {"province", "region", "multi_destination"}
                            else destination_focus_radius_km
                            if destination_focus_radius_km is not None
                            else 100.0
                        ),
                    }
                )
            except (KeyError, TypeError, ValueError):
                pass
        flyai_hotels = await registry.execute(
            "flyai.hotel",
            hotel_payload,
            SkillContext(trip_id=state["trip_id"]),
        )
        if flyai_hotels.success and isinstance(flyai_hotels.data, dict):
            flyai_sources = [
                item.model_dump(mode="json") for item in flyai_hotels.sources
            ]
            tourism_sources.extend(flyai_sources)
            for item in flyai_hotels.data.get("items", []):
                if not item.get("name") or not item.get("location"):
                    continue
                candidates["hotels"].append(
                    {
                        "place": {
                            "id": item.get("id"),
                            "name": item["name"],
                            "address": item.get("address"),
                            "city": destination.get("city"),
                            "coordinates": {
                                "longitude": float(item["longitude"]),
                                "latitude": float(item["latitude"]),
                            },
                            "source_id": item.get("id"),
                        },
                        "source_records": flyai_sources,
                        "provider": flyai_hotels.provider,
                        "image_url": item.get("image_url"),
                        "detail_url": item.get("detail_url"),
                        "rating": item.get("rating"),
                        "ticket_or_price": (
                            {
                                "currency": "CNY",
                                "minimum": item["price_min_cny"],
                                "maximum": item["price_max_cny"],
                                "estimated": item.get("price_estimated", False),
                            }
                            if item.get("price_min_cny") is not None
                            else None
                        ),
                    }
                )
        await emit(
            state,
            "flyai_hotels",
            (
                f"FlyAI 住宿候选已返回 {len(candidates['hotels'])} 项"
                if flyai_hotels.success
                else "FlyAI 住宿搜索暂不可用，后续由高德住宿候选补充"
            ),
            65,
            event="tool_completed",
            tool="flyai.hotel",
        )
        for category, (keywords, page_size) in categories.items():
            if category == "hotels" and any(
                not re.search(
                    r"(?:青旅|青年旅舍|青年旅社|青年公寓|学生公寓|旅舍|背包客栈|青年旅店|青年旅馆|青年客栈|学生宿舍|宿舍型|床位房|胶囊旅馆|太空舱|hostel|backpacker)",
                    " ".join(
                        str((item.get("place") or {}).get(field) or "")
                        for field in ("name", "address")
                    ),
                    re.IGNORECASE,
                )
                for item in candidates["hotels"]
            ):
                continue
            poi_payload: dict[str, Any] = {
                "keywords": keywords,
                "city": _destination_search_area(destination),
                "page_size": page_size,
            }
            # City-wide requests search the full destination. A named scenic
            # anchor (or an explicit local-only request) uses the semantic
            # focus radius for provider-side precision.
            if destination_focus_radius_km is not None and coordinates:
                poi_payload.update(
                    {
                        "location": (
                            f"{coordinates['longitude']},{coordinates['latitude']}"
                        ),
                        "radius": int(destination_focus_radius_km * 1000),
                    }
                )
            result = await registry.execute(
                "amap.poi",
                poi_payload,
                SkillContext(trip_id=state["trip_id"]),
            )
            if not result.success or not isinstance(result.data, dict):
                continue
            source_records = [
                item.model_dump(mode="json") for item in result.sources
            ]
            tourism_sources.extend(source_records)
            for item in result.data.get("items", []):
                location = item.get("location")
                if not item.get("name") or not location:
                    continue
                try:
                    longitude, latitude = location.split(",", 1)
                    place = {
                        "id": item.get("id"),
                        "name": item["name"],
                        "address": item.get("address"),
                        "city": item.get("city") or destination.get("city"),
                        "coordinates": {
                            "longitude": float(longitude),
                            "latitude": float(latitude),
                        },
                        "source_id": item.get("id"),
                    }
                except (TypeError, ValueError):
                    continue
                candidates[category].append(
                    {
                        "place": place,
                        "categories": item.get("type"),
                        "detail_url": f"https://www.amap.com/search?query={quote(item['name'])}",
                        "amap_source_id": item.get("id"),
                        "amap_facts": {
                            key: item.get(key)
                            for key in (
                                "opening_hours_text", "price_text", "parking_text",
                                "ticket_ordering", "hotel_ordering", "website", "photos",
                            )
                            if item.get(key) not in (None, "", [])
                        },
                        "opening_hours": (
                            {"text": item["opening_hours_text"], "confirmed": True, "source_count": 1}
                            if item.get("opening_hours_text") else None
                        ),
                        "ticket_note": item.get("price_text"),
                        "parking_note": item.get("parking_text"),
                        "image_url": (item.get("photos") or [None])[0],
                        "source_records": [
                            *source_records,
                            {
                                "provider": "高德地图",
                                "title": f"{item['name']} 地点详情",
                                "url": f"https://www.amap.com/search?query={quote(item['name'])}",
                            },
                        ],
                        "provider": result.provider,
                    }
                )
        if coordinates:
            open_trip_map = await registry.execute(
                "opentripmap.nearby",
                {
                    "longitude": coordinates["longitude"],
                    "latitude": coordinates["latitude"],
                    # OpenTripMap requires a radius. Keep the broad default
                    # for city/province research; a scenic anchor uses the
                    # same semantic focus boundary as the other providers.
                    "radius_m": (
                        int(destination_focus_radius_km * 1000)
                        if destination_focus_radius_km is not None
                        else 100000
                    ),
                    "limit": 30,
                    "language": "en",
                },
                SkillContext(trip_id=state["trip_id"]),
            )
            if open_trip_map.success and isinstance(open_trip_map.data, dict):
                source_records = [
                    item.model_dump(mode="json") for item in open_trip_map.sources
                ]
                tourism_sources.extend(source_records)
                osm_items = list(open_trip_map.data.get("items", []))
                await emit(
                    state,
                    "discover_tourism",
                    "地点策展智能体正在比对多源景点、合并同地点并生成中文显示名",
                    66,
                    event="tool_started",
                    tool="deepseek.poi_curator",
                )
                decisions = await poi_curator.curate(
                    _destination_search_area(destination),
                    candidates["attractions"],
                    osm_items,
                )
                decision_by_id = {
                    str(item.get("source_id")): item for item in decisions
                }
                merged_count = 0
                translated_count = 0
                added_count = 0
                for item in osm_items:
                    name = str(item.get("name") or "").strip()
                    decision = decision_by_id.get(str(item.get("id")), {})
                    action = decision.get("action", "skip")
                    if not name or action == "skip":
                        continue
                    if action == "merge":
                        target_name = _normalize_poi_name(decision.get("merge_target_name"))
                        target = next(
                            (
                                candidate
                                for candidate in candidates["attractions"]
                                if target_name
                                and _normalize_poi_name(candidate["place"]["name"]) == target_name
                            ),
                            None,
                        )
                        if target:
                            target["source_records"] = [
                                *target.get("source_records", []),
                                *source_records,
                            ]
                            target.setdefault("alternate_names", []).append(name)
                            target.setdefault("agent_merge_reasons", []).append(decision.get("reason"))
                            merged_count += 1
                        continue
                    display_name = str(decision.get("display_name_zh") or "").strip()
                    if not display_name or not _contains_cjk(display_name):
                        continue
                    candidates["attractions"].append(
                        {
                            "place": {
                                "id": item.get("id"),
                                "name": display_name,
                                "name_en": item.get("name_en") or name,
                                "name_local": item.get("name_local") or name,
                                "city": destination.get("city"),
                                "coordinates": {
                                    "longitude": float(item["longitude"]),
                                    "latitude": float(item["latitude"]),
                                },
                                "source_id": item.get("id"),
                            },
                            "categories": item.get("kinds"),
                            "rating": item.get("rating"),
                            "detail_url": item.get("detail_url"),
                            "source_records": [
                                *source_records,
                                {
                                    "provider": "OpenTripMap / OpenStreetMap",
                                    "title": f"{display_name} 景点详情",
                                    "url": item.get("detail_url"),
                                },
                            ],
                            "provider": open_trip_map.provider,
                            "agent_reason": decision.get("reason"),
                        }
                    )
                    added_count += 1
                    if display_name != name:
                        translated_count += 1
                await emit(
                    state,
                    "discover_tourism",
                    (
                        f"地点策展智能体已合并 {merged_count} 个同地点，翻译 {translated_count} 个名称，"
                        f"从 OSM 保留 {added_count} 个独立景点"
                    ),
                    67,
                    event="tool_completed",
                    tool="deepseek.poi_curator",
                )
        if flyai_ticket_items:
            for candidate in candidates["attractions"]:
                candidate_name = _normalize_poi_name(candidate["place"]["name"])
                match = next(
                    (
                        item
                        for item in flyai_ticket_items
                        if item.get("name")
                        and _normalize_poi_name(item["name"]) == candidate_name
                    ),
                    None,
                )
                if not match:
                    continue
                candidate["ticket_name"] = match.get("ticket_name")
                candidate["ticket_date"] = match.get("ticket_date")
                candidate["image_url"] = match.get("image_url")
                candidate["detail_url"] = match.get("detail_url")
                if match.get("price_min_cny") is not None:
                    candidate["ticket_or_price"] = {
                        "currency": "CNY",
                        "minimum": match["price_min_cny"],
                        "maximum": match["price_max_cny"],
                        "estimated": match.get("price_estimated", False),
                    }
            # Keep FlyAI-only attractions when the CLI returns coordinates;
            # previously FlyAI could only enrich an existing AMap name match,
            # making most of its recommendations invisible to the user.
            existing_names = {
                _normalize_poi_name(item.get("place", {}).get("name", ""))
                for item in candidates["attractions"]
            }
            for item in flyai_ticket_items:
                name = str(item.get("name") or "").strip()
                longitude, latitude = item.get("longitude"), item.get("latitude")
                if not name or longitude is None or latitude is None:
                    continue
                normalized = _normalize_poi_name(name)
                if normalized in existing_names:
                    continue
                candidates["attractions"].append(
                    {
                        "place": {
                            "id": item.get("id"),
                            "name": name,
                            "address": item.get("address"),
                            "city": destination.get("city"),
                            "coordinates": {
                                "longitude": float(longitude),
                                "latitude": float(latitude),
                            },
                            "source_id": item.get("id") or name,
                        },
                        "detail_url": item.get("detail_url"),
                        "image_url": item.get("image_url"),
                        "categories": item.get("categories") or item.get("kinds"),
                        "ticket_name": item.get("ticket_name"),
                        "ticket_date": item.get("ticket_date"),
                        "source_records": [
                            {
                                "provider": "FlyAI / 飞猪",
                                "title": f"{name} 景点详情",
                                "url": item.get("detail_url") or "https://www.fliggy.com/",
                            }
                        ],
                        "provider": "FlyAI / 飞猪",
                        "rating": item.get("rating"),
                        "ticket_or_price": (
                            {
                                "currency": "CNY",
                                "minimum": item.get("price_min_cny"),
                                "maximum": item.get("price_max_cny"),
                                "estimated": item.get("price_estimated", False),
                            }
                            if item.get("price_min_cny") is not None
                            else None
                        ),
                    }
                )
                existing_names.add(normalized)
        # The broad “景点” query is intentionally not the only source of
        # attractions.  A city-wide research Agent may identify famous places
        # that fall outside the provider's first page or far from the chosen
        # hotel.  Resolve those source-backed names through AMap before
        # ranking so they remain executable itinerary candidates.  This is
        # data-driven from the research Agent; no city/POI keyword catalogue
        # is embedded in the planner.
        researched_attractions = [
            item
            for item in destination_research.get("agent_recommendations", [])
            if isinstance(item, dict)
            and item.get("category") == "attractions"
            and str(item.get("name") or "").strip()
        ]
        # The destination-plan Agent may select a source-backed highlight that
        # was not returned in the first recommendation slice.  Feed those
        # named selections into the same coordinate lookup so the high-level
        # plan is actually executable rather than decorative.
        planned_attractions = (
            destination_research.get("agent_plan", {}).get("selected_attractions", [])
            if isinstance(destination_research.get("agent_plan"), dict)
            else []
        )
        known_research_names = {
            _normalize_poi_name(item.get("name")) for item in researched_attractions
        }
        for item in planned_attractions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            normalized = _normalize_poi_name(name)
            if not name or normalized in known_research_names:
                continue
            researched_attractions.append(
                {
                    "name": name,
                    "category": "attractions",
                    "importance": 80,
                    "area": item.get("area"),
                    "reason": item.get("reason") or "目的地策划智能体纳入每日计划",
                    "suggested_minutes": 180,
                    "visit_scale": "major",
                    "best_time": "any",
                    "source_indexes": [],
                }
            )
            known_research_names.add(normalized)
        if researched_attractions and "amap.poi" in registry.names():
            existing_names = {
                _normalize_poi_name(item.get("place", {}).get("name"))
                for item in candidates["attractions"]
            }
            lookup_items = [
                item
                for item in researched_attractions[:12]
                if _normalize_poi_name(item["name"]) not in existing_names
            ]
            if lookup_items:
                await emit(
                    state,
                    "research_attraction_lookup",
                    f"目的地研究智能体正在回查 {len(lookup_items)} 个代表性景点的在线地图坐标",
                    67,
                    event="tool_started",
                    tool="amap.poi",
                )
                lookup_results = await asyncio.gather(
                    *[
                        registry.execute(
                            "amap.poi",
                            {
                                "keywords": str(item["name"])[:80],
                                "city": destination.get("city"),
                                "page_size": 5,
                            },
                            SkillContext(
                                trip_id=state["trip_id"],
                                metadata={
                                    "purpose": "destination_research_attraction",
                                    "research_name": item["name"],
                                },
                            ),
                        )
                        for item in lookup_items
                    ],
                    return_exceptions=True,
                )
                for recommendation, result in zip(lookup_items, lookup_results, strict=True):
                    if isinstance(result, Exception) or not result.success or not isinstance(result.data, dict):
                        continue
                    source_records = [item.model_dump(mode="json") for item in result.sources]
                    tourism_sources.extend(source_records)
                    for item in result.data.get("items", []):
                        location = item.get("location")
                        name = str(item.get("name") or "").strip()
                        if not name or not location:
                            continue
                        normalized = _normalize_poi_name(name)
                        if normalized in existing_names:
                            continue
                        try:
                            longitude, latitude = location.split(",", 1)
                            longitude_value, latitude_value = float(longitude), float(latitude)
                        except (TypeError, ValueError):
                            continue
                        candidates["attractions"].append(
                            {
                                "place": {
                                    "id": item.get("id") or name,
                                    "name": name,
                                    "address": item.get("address"),
                                    "city": item.get("city") or destination.get("city"),
                                    "coordinates": {
                                        "longitude": longitude_value,
                                        "latitude": latitude_value,
                                    },
                                    "source_id": item.get("id") or name,
                                },
                                "detail_url": f"https://www.amap.com/search?query={quote(name)}",
                                "categories": item.get("type"),
                                "source_records": [
                                    *source_records,
                                    {
                                        "provider": "高德地图",
                                        "title": f"{name} 地点详情",
                                        "url": f"https://www.amap.com/search?query={quote(name)}",
                                    },
                                ],
                                "provider": result.provider,
                                "research_hint": recommendation.get("reason"),
                                "research_hint_name": recommendation.get("name"),
                            }
                        )
                        existing_names.add(normalized)
                await emit(
                    state,
                    "research_attraction_lookup",
                    "目的地研究代表性景点已补齐坐标并合并重复地点",
                    67,
                    event="tool_completed",
                    tool="amap.poi",
                )

        # A named place in the user's request is a hard itinerary constraint,
        # not merely another ranked suggestion. Destination research can omit
        # a new/abbreviated venue (for example “麓湖CPI”), and a broad
        # first-page POI query may never return it. Search each explicit name
        # directly, keep the requested display name, and mark the resolved
        # candidate so the route selector reserves capacity for it.
        required_places = [
            item
            for item in state["trip_request"].get("must_visit", [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        if required_places:
            required_to_lookup = _mark_existing_required_candidates(
                candidates["attractions"],
                required_places,
            )
            # Always process unresolved requirements, even when the map POI
            # adapter is unavailable.  Otherwise a degraded provider silently
            # drops the user's hard requirement before verification.
            if required_to_lookup:
                await emit(
                    state,
                    "required_poi_lookup",
                    f"正在定向查找 {len(required_to_lookup)} 个指定景点，确保不会被优先级排序遗漏",
                    67,
                    event="tool_started",
                    tool="amap.poi",
                )
                # A multi-city request may name a place that belongs to a
                # later city (for example “西安再去洛阳…龙门石窟”).  Searching
                # every required place only in the first destination silently
                # drops it.  Try the explicitly attached city first, then the
                # ordered destination anchors, and retain a blocker marker if
                # none of the scoped searches can resolve the name.
                request_destination_names = [
                    str(value).strip()
                    for value in state["trip_request"].get("destination_names", [])
                    if isinstance(value, str) and value.strip()
                ]
                destination_areas: list[str] = []
                for area in [
                    *[str(item.get("city") or "").strip() for item in required_to_lookup],
                    *request_destination_names,
                    str(destination.get("city") or "").strip(),
                ]:
                    if area and area not in destination_areas:
                        destination_areas.append(area)

                async def lookup_required_place(required: dict[str, Any]) -> bool:
                    searched: list[str | None] = []
                    item_city = str(required.get("city") or "").strip()
                    for area in [item_city, *destination_areas, None]:
                        if area in searched:
                            continue
                        searched.append(area)
                        context = SkillContext(
                            trip_id=state["trip_id"],
                            metadata={
                                "purpose": "required_poi_lookup",
                                "required_name": required["name"],
                                "search_city": area,
                            },
                        )
                        if "amap.poi" in registry.names():
                            result = await registry.execute(
                                "amap.poi",
                                {
                                    "keywords": str(required["name"])[:80],
                                    "city": area,
                                    "page_size": 10,
                                },
                                context,
                            )
                            if _merge_required_lookup_result(
                                candidates["attractions"],
                                required,
                                result,
                                destination,
                            ):
                                return True
                        # Region scenic areas are often missing from a
                        # city-scoped text search.  Ask the geocoder for a
                        # concrete point before retaining an unresolved
                        # blocker, still keeping the user's exact wording.
                        if "amap.geocode" in registry.names():
                            geocode = await registry.execute(
                                "amap.geocode",
                                {
                                    "address": str(required["name"])[:80],
                                    "city": area,
                                },
                                context,
                            )
                            if _merge_required_geocode_result(
                                candidates["attractions"],
                                required,
                                geocode,
                                destination,
                            ):
                                return True
                        if "osm.geocode" in registry.names():
                            osm_query = " ".join(
                                item
                                for item in (str(required["name"]).strip(), area or destination.get("name"), "中国")
                                if item
                            )
                            osm_result = await registry.execute(
                                "osm.geocode",
                                {"query": osm_query, "limit": 3},
                                context,
                            )
                            if _merge_required_geocode_result(
                                candidates["attractions"],
                                required,
                                osm_result,
                                destination,
                            ):
                                return True
                    return False

                lookup_status = await asyncio.gather(
                    *(lookup_required_place(item) for item in required_to_lookup)
                )
                for required, resolved in zip(required_to_lookup, lookup_status, strict=True):
                    if not resolved:
                        # Keep an explicit unresolved marker so verification
                        # reports the named place as a blocker instead of
                        # falsely declaring a complete plan after a provider
                        # outage or a city-scoped search miss.
                        candidates["attractions"].append(
                            {
                                "place": {
                                    "id": f"required-unresolved:{required['name']}",
                                    "name": str(required["name"]).strip(),
                                    "city": required.get("city") or destination.get("city"),
                                    "coordinates": None,
                                },
                                "user_required": True,
                                "required_name": str(required["name"]).strip(),
                                "lookup_unresolved": True,
                                "source_records": [],
                            }
                        )
                await emit(
                    state,
                    "required_poi_lookup",
                    "指定景点已检索并加入候选；无法核实的地点会阻断静默遗漏",
                    67,
                    event="tool_completed",
                    tool="amap.poi",
                )

        # A confirmed map/candidate edit is a durable user decision.  The
        # provider search above is intentionally fresh on every replan, but
        # it must not erase a place the user explicitly added.  Re-inject the
        # exact candidate (including its chosen coordinates and category)
        # before ranking and scheduling so the route builder can connect it.
        confirmed_additions = state.get("confirmed_additions", []) or []
        if isinstance(confirmed_additions, list):
            for record in confirmed_additions:
                if not isinstance(record, dict):
                    continue
                category = str(record.get("category") or "").strip()
                candidate = record.get("candidate")
                if category not in candidates or not isinstance(candidate, dict):
                    continue
                place = candidate.get("place") or {}
                name = str(place.get("name") or "").strip()
                if not name or not place.get("coordinates"):
                    continue
                normalized = _normalize_poi_name(name)
                existing = next(
                    (
                        item
                        for item in candidates[category]
                        if _normalize_poi_name((item.get("place") or {}).get("name")) == normalized
                    ),
                    None,
                )
                if existing is not None:
                    # Keep the user-selected coordinates and metadata even
                    # when a provider returned the same name at a nearby POI.
                    existing.update(deepcopy(candidate))
                    existing["user_confirmed"] = True
                    existing["provider"] = existing.get("provider") or "用户已确认"
                else:
                    injected = deepcopy(candidate)
                    injected["user_confirmed"] = True
                    injected["provider"] = injected.get("provider") or "用户已确认"
                    candidates[category].insert(0, injected)

        # Final integrity pass: provider/ranking agents must never erase an
        # explicit must-visit. Resolve any name that still lacks a candidate
        # through the public geocoder fallback, or retain an honest marker.
        for required in required_places:
            requested_name = str(required.get("name") or "").strip()
            matching = next(
                (
                    item
                    for item in candidates["attractions"]
                    if _poi_name_matches(
                        requested_name,
                        (item.get("place") or {}).get("name"),
                    )
                ),
                None,
            )
            if matching is not None:
                matching["user_required"] = True
                matching["required_name"] = requested_name
                continue
            resolved = False
            if "osm.geocode" in registry.names():
                area = str(required.get("city") or destination.get("name") or "").strip()
                query = " ".join(item for item in (requested_name, area, "中国") if item)
                osm_result = await registry.execute(
                    "osm.geocode",
                    {"query": query, "limit": 3},
                    SkillContext(
                        trip_id=state["trip_id"],
                        metadata={
                            "purpose": "required_poi_integrity",
                            "required_name": requested_name,
                        },
                    ),
                )
                resolved = _merge_required_geocode_result(
                    candidates["attractions"],
                    required,
                    osm_result,
                    destination,
                )
            if not resolved:
                candidates["attractions"].append(
                    {
                        "place": {
                            "id": f"required-unresolved:{requested_name}",
                            "name": requested_name,
                            "city": required.get("city") or destination.get("city"),
                            "coordinates": None,
                        },
                        "user_required": True,
                        "required_name": requested_name,
                        "lookup_unresolved": True,
                        "source_records": [],
                    }
                )

        # Keep a POI-scoped short trip local to its named anchor. Provider
        # radius parameters are only a first filter; apply the same boundary
        # after all FlyAI/map/geocoder merges so a distant generic venue cannot
        # re-enter through a second source. Explicit must-visits and confirmed
        # map edits always survive this locality pass.
        if destination_focus_radius_km is not None and coordinates:
            try:
                destination_point = RoutePoint(
                    longitude=float(coordinates["longitude"]),
                    latitude=float(coordinates["latitude"]),
                )
            except (KeyError, TypeError, ValueError):
                destination_point = None
            if destination_point is not None:
                required_names = [
                    str(item.get("name") or "").strip()
                    for item in required_places
                    if isinstance(item, dict) and str(item.get("name") or "").strip()
                ]
                locality_kept: dict[str, int] = {}
                locality_unmapped: dict[str, list[str]] = {}
                for category, items in list(candidates.items()):
                    kept: list[dict[str, Any]] = []
                    unmapped: list[str] = []
                    for candidate in items:
                        place = candidate.get("place") or {}
                        name = str(place.get("name") or "").strip()
                        if (
                            candidate.get("user_required")
                            or candidate.get("user_confirmed")
                            or any(_poi_name_matches(required, name) for required in required_names)
                        ):
                            kept.append(candidate)
                            continue
                        point = place.get("coordinates")
                        if not point:
                            # An unlocated generic result cannot be proven to
                            # be near a named scenic anchor. Keep its name only
                            # in diagnostics; otherwise a venue such as a far
                            # away KTV could leak into the executable schedule.
                            if name:
                                unmapped.append(name)
                            continue
                        try:
                            distance = _haversine_km(
                                destination_point,
                                RoutePoint(
                                    longitude=float(point["longitude"]),
                                    latitude=float(point["latitude"]),
                                ),
                            )
                        except (KeyError, TypeError, ValueError):
                            if name:
                                unmapped.append(name)
                            continue
                        if distance <= destination_focus_radius_km:
                            kept.append(candidate)
                    locality_kept[category] = len(items) - len(kept)
                    locality_unmapped[category] = unmapped[:20]
                    candidates[category] = kept
                destination_research["local_focus_radius_km"] = destination_focus_radius_km
                destination_research["local_focus_filtered"] = locality_kept
                destination_research["local_focus_unmapped"] = locality_unmapped

        candidates = rank_tourism_candidates(
            candidates,
            destination,
            state["trip_request"].get("preferences", []),
            destination_research=destination_research,
        )
        if settings.enable_poi_web_enrichment:
            await emit(
                state,
                "enrich_poi_details",
                "信息检索智能体正在汇总公开网页介绍、图片与可追溯来源",
                67,
                event="tool_started",
                tool="web.poi_research",
            )
            candidates = await enrich_tourism_candidates(
                candidates,
                timeout_seconds=settings.poi_web_timeout_seconds,
            )
            await emit(
                state,
                "enrich_poi_details",
                "信息检索智能体已完成景点详情与图片补充",
                67,
                event="tool_completed",
                tool="web.poi_research",
            )
        if settings.deepseek_api_key:
            await emit(
                state,
                "rank_tourism_candidates",
                "候选排序智能体正在根据偏好、距离、评分、价格综合排序候选",
                68,
                event="tool_started",
                tool="deepseek.poi_ranker",
            )
            agent_decisions = await poi_ranker.rank(
                candidates,
                state["trip_request"].get("preferences", []),
                state["trip_request"].get("special_events", []),
                travel_start=state["trip_request"].get("start_date"),
                travel_end=state["trip_request"].get("end_date"),
                destination_research=destination_research,
            )
            if agent_decisions:
                candidates = apply_agent_ranking(candidates, agent_decisions)
        # Run the category integrity check after research/ranking has attached
        # destination priorities. A researched university or landmark may be
        # intentionally visitable; an unresearched nearby KTV/pharmacy is not.
        candidates = apply_candidate_type_guard(candidates)
        await emit(
                state,
                "rank_tourism_candidates",
                "候选排序智能体已完成候选排序与推荐理由",
                68,
                event="tool_completed",
                tool="deepseek.poi_ranker",
            )
        # A deleted activity is a durable user constraint.  Apply it after all
        # provider and Agent ranking passes so a second provider cannot put the
        # same place back into the replan candidate pool.
        candidates = filter_excluded_candidates(
            candidates,
            state.get("excluded_places"),
        )
        candidates["attractions"] = deduplicate_attraction_candidates(
            candidates.get("attractions", [])
        )
        destination_research["attraction_coverage"] = plan_attraction_coverage(
            candidates.get("attractions", []),
            len(state.get("day_plans", [])),
        )
        await emit(
            state,
            "discover_tourism",
            (
                f"已找到 {len(candidates['attractions'])} 个景点、"
                f"{len(candidates['meals'])} 个餐饮和 "
                f"{len(candidates['hotels'])} 个住宿候选；"
                f"研究智能体规划覆盖 {destination_research['attraction_coverage'].get('priority_count', 0)} 个代表性景点，"
                f"分为 {destination_research['attraction_coverage'].get('cluster_count', 0)} 个地理片区"
            ),
            69,
            event="tool_completed",
            tool="amap.poi",
        )
        return {
            "tourism_candidates": candidates,
            "destination_research": destination_research,
            "sources": [
                *state.get("sources", []),
                *tourism_sources,
                *destination_research.get("sources", []),
            ],
            "progress": {"node": "discover_tourism", "value": 69},
        }

    async def build_local_routes(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "build_local_routes",
            "正在补充目的地公共交通、步行和骑行接驳",
            70,
            event="tool_started",
            tool="amap.poi/amap.route",
        )
        request = state["trip_request"]
        destination = request["destination"]
        coordinates = destination.get("coordinates")
        requested_modes = {
            str(mode).strip().casefold()
            for mode in request.get("transport_modes", [])
            if str(mode).strip()
        }
        if "ship" in requested_modes or "boat" in requested_modes:
            requested_modes.add("ferry")
        if not coordinates:
            return {"local_routes": []}
        attraction_candidates = state.get("tourism_candidates", {}).get(
            "attractions", []
        )
        # Pick the same comfortable base that the tourism scheduler will use.
        # Local movement must be built around that hotel; otherwise the cards
        # can show an overnight property while the route still starts from a
        # city-centre geocode on every morning.
        primary_hotel = select_primary_hotel(
            state.get("tourism_candidates", {}).get("hotels", []),
            destination,
            attraction_candidates,
            required_names={
                str(item.get("name") or "").strip()
                for item in request.get("must_visit", [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            },
        )
        local_base = (primary_hotel or {}).get("place") or destination
        # A semantic request such as “三天都在九宫山，不去其他地方” must not
        # reuse stale candidates from the origin city or a previous plan. The
        # same locality boundary is also used for a named scenic anchor (for
        # example a two-day lake trip), while city requests remain broad.
        local_focus_radius_km = _destination_focus_radius(
            destination,
            explicit_local=bool(request.get("stay_only_at_destination")),
        )
        if local_focus_radius_km is not None:
            destination_city = str(destination.get("city") or destination.get("name") or "").strip()
            destination_point = destination.get("coordinates")
            required_names = {
                str(item.get("name") or "").strip()
                for item in request.get("must_visit", [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            }
            filtered_candidates: list[dict[str, Any]] = []
            for candidate in attraction_candidates:
                place = candidate.get("place") or {}
                name = str(place.get("name") or "").strip()
                if name in required_names:
                    filtered_candidates.append(candidate)
                    continue
                candidate_city = str(place.get("city") or "").strip()
                point = place.get("coordinates")
                same_city = bool(destination_city and candidate_city and (
                    destination_city in candidate_city or candidate_city in destination_city
                ))
                nearby = False
                if destination_point and point:
                    try:
                        origin_point = destination_point if isinstance(destination_point, RoutePoint) else RoutePoint(**destination_point)
                        candidate_point = point if isinstance(point, RoutePoint) else RoutePoint(**point)
                        nearby = _haversine_km(origin_point, candidate_point) <= local_focus_radius_km
                    except (TypeError, ValueError):
                        nearby = False
                # A same-city label can cover an entire county. For a scenic
                # anchor, coordinates are therefore the authoritative scope.
                # Do not schedule an unlocated generic result: its locality
                # cannot be proven and it may be a provider's stale/far match.
                if nearby or (not destination_point and same_city):
                    filtered_candidates.append(candidate)
            attraction_candidates = filtered_candidates
        attraction_candidates = deduplicate_attraction_candidates(attraction_candidates)
        if not attraction_candidates:
            return {
                "local_routes": [],
                "warnings": [
                    *state.get("warnings", []),
                    {
                        "code": "LOCAL_MOBILITY_UNAVAILABLE",
                        "message": "目的地周边 POI 暂不可用，未生成本地接驳阶段",
                        "severity": "warning",
                    },
                ],
            }
        day_count = len(state["day_plans"])
        # Match local route capacity to the researched highlight set.  A
        # destination with twelve named highlights over four days gets three
        # connected sightseeing legs per day; a small candidate pool keeps the
        # comfortable two-leg baseline.  Transfer days are included too so
        # city highlights are not silently dropped at either end of the trip.
        priority_count = sum(
            1
            for item in attraction_candidates
            if item.get("destination_research_priority")
            and not item.get("seasonal_excluded")
        )
        # A comfortable visit normally consumes three hours or more.  Keep
        # the executable route to at most two principal stops per full day;
        # transfer days get one stop so airport/station access and hotel
        # check-in never drift into the night.  The research pool remains
        # available as alternatives in the UI.
        daily_budget = (
            max(2, min(3, (priority_count + day_count - 1) // day_count))
            if priority_count
            else 2
        )
        local_anchor_request = (
            str(request.get("destination_scope") or "").strip().lower() == "poi"
            or bool(request.get("stay_only_at_destination"))
        )
        if local_anchor_request:
            # A named scenic anchor over one or two nights needs breathing
            # room, not a city-wide POI sweep. One principal stop per day is
            # enough to leave a genuine three-hour visit window and unhurried
            # time at the lake/park itself.
            daily_budget = 1
        scheduled_transfer_days = (
            {0, day_count - 1}
            if day_count > 1 and requested_modes & {"flight", "train", "ferry"}
            else set()
        )
        day_budgets = {
            index: (1 if index in scheduled_transfer_days else daily_budget)
            for index in range(day_count)
        }
        selected_candidates = _select_itinerary_places(
            attraction_candidates,
            destination,
            max(2, sum(day_budgets.values())),
            return_candidates=True,
        )
        if not selected_candidates:
            return {"local_routes": []}

        # Keep local movement consistent with the trip's transport choice.
        # Self-drive requests must not silently switch to public transit once
        # the traveller reaches the destination city.
        selected_intercity_mode = str(
            (state.get("selected_route") or {}).get("data", {}).get("selected_mode")
            or ""
        ).casefold()
        normalized_transport_modes = {
            str(mode).strip().casefold()
            for mode in request.get("transport_modes", [])
            if str(mode).strip()
        }
        scheduled_intercity_requested = bool(
            normalized_transport_modes & {"flight", "train", "ferry", "ship", "boat"}
        )
        driving_requested = not scheduled_intercity_requested and (
            not normalized_transport_modes
            or "driving" in normalized_transport_modes
            or selected_intercity_mode == "driving"
        )
        explicit_local_modes = [
            mode for mode in ("transit", "walking", "riding")
            if mode in normalized_transport_modes
        ]
        modes = (
            ["driving"]
            if driving_requested
            else explicit_local_modes or ["transit", "walking"]
        )
        local_fallback_modes = list(modes)
        default_local_mode = "driving" if driving_requested else "transit"

        # A destination research result can legitimately contain a highlight
        # in another city (for example Xi'an + Luoyang).  It must never be
        # sent through the city-local AMap connector: that turns a 350 km
        # intercity move into a 12-hour "local transit" stage and leaves the
        # required attraction without a usable sightseeing window.  Keep this
        # threshold generic and derive the actual transport from the user's
        # selected modes; no city or attraction names are embedded here.
        LOCAL_LEG_MAX_KM = 35.0
        requested_intercity_modes = [
            mode
            for mode in ("train", "flight", "ferry")
            if mode in requested_modes
        ]
        if not requested_intercity_modes and selected_intercity_mode in {
            "train",
            "flight",
            "ferry",
        }:
            requested_intercity_modes = [selected_intercity_mode]

        def _distance_between_places(
            first: dict[str, Any], second: dict[str, Any]
        ) -> float | None:
            try:
                first_coordinates = first.get("coordinates") or {}
                second_coordinates = second.get("coordinates") or {}
                return _haversine_km(
                    RoutePoint(
                        longitude=float(first_coordinates["longitude"]),
                        latitude=float(first_coordinates["latitude"]),
                    ),
                    RoutePoint(
                        longitude=float(second_coordinates["longitude"]),
                        latitude=float(second_coordinates["latitude"]),
                    ),
                )
            except (KeyError, TypeError, ValueError):
                return None

        async def _intercity_leg_route(
            leg_origin: dict[str, Any],
            leg_destination: dict[str, Any],
            day_index: int,
        ) -> dict[str, Any]:
            """Build a truthful route for a long leg between city clusters.

            Explicit train/flight/ferry preferences are resolved through the
            corresponding travel-search adapter.  Self-drive remains the
            default for an unqualified request, so a long leg is left as a
            driving route and the deep-drive pass can split it across days.
            If a schedule provider is unavailable, retain an estimated
            intercity connector instead of mislabelling it as local transit.
            """
            travel_date = date.fromisoformat(
                state["day_plans"][day_index]["date"]
            )
            if requested_intercity_modes:
                last_scheduled_route: dict[str, Any] = {
                    "success": False,
                    "error_code": "INTERCITY_SCHEDULE_UNAVAILABLE",
                }
                for mode in requested_intercity_modes:
                    if mode == "train":
                        route, _ = await _train_route(
                            registry,
                            leg_origin,
                            leg_destination,
                            state["trip_id"],
                            travel_date=travel_date,
                        )
                    elif mode == "flight":
                        route, _ = await _flight_route(
                            registry,
                            leg_origin,
                            leg_destination,
                            state["trip_id"],
                            travel_date=travel_date,
                        )
                    else:
                        route, _ = await _ferry_route(
                            registry,
                            leg_origin,
                            leg_destination,
                            state["trip_id"],
                            travel_date=travel_date,
                        )
                    if route.get("success"):
                        return route
                    last_scheduled_route = route
                if any(mode in {"flight", "train"} for mode in requested_intercity_modes):
                    # An invented air/rail connector would later be rendered
                    # as a ticket. Stop here and let the planner report the
                    # unavailable schedule instead of showing placeholders.
                    return last_scheduled_route

            # Explicit air/rail journeys use their selected scheduled mode;
            # otherwise intra-region travel remains driving.
            if driving_requested and selected_intercity_mode not in {
                "train",
                "flight",
                "ferry",
            }:
                route = await _route(
                    registry,
                    leg_origin,
                    leg_destination,
                    state["trip_id"],
                    preferred_mode="driving",
                    fallback_modes=["driving"],
                )
                if route.get("success"):
                    return route

            # Non-ticketed local/ferry moves may retain an estimated geometry;
            # flight and train have already returned above unless a real
            # service number was found.
            distance = _distance_between_places(leg_origin, leg_destination) or 0.1
            fallback_mode = requested_intercity_modes[0] if requested_intercity_modes else default_local_mode
            speed_kmh = {
                "train": 180.0,
                "flight": 520.0,
                "ferry": 30.0,
                "driving": 75.0,
                "transit": 25.0,
            }.get(fallback_mode, 25.0)
            duration = max(10, round(distance / speed_kmh * 60) + 30)
            origin_coordinates = leg_origin.get("coordinates") or {}
            destination_coordinates = leg_destination.get("coordinates") or {}
            geometry: list[dict[str, float]] = []
            try:
                geometry = [
                    {
                        "longitude": float(origin_coordinates["longitude"]),
                        "latitude": float(origin_coordinates["latitude"]),
                    },
                    {
                        "longitude": float(destination_coordinates["longitude"]),
                        "latitude": float(destination_coordinates["latitude"]),
                    },
                ]
            except (KeyError, TypeError, ValueError):
                pass
            return {
                "success": True,
                "data": {
                    "selected_mode": fallback_mode,
                    "distance_km": round(distance, 2),
                    "duration_minutes": duration,
                    "tolls_cny": 0,
                    "geometry": geometry,
                    "steps": [],
                    "traffic_summary": "跨城市接驳为估算值，出发前请确认具体班次/路况",
                    "estimated": True,
                },
                "sources": [],
                "warnings": [
                    "当前接驳仅保留路线时间估算，出发前会再次复核路况",
                ],
            }

        # Research clusters may assign several hard requirements to the same
        # day. Spread user-required places over available stay days so each
        # one receives a real sightseeing window instead of being squeezed
        # into a transfer-day evening.
        required_candidates = [
            candidate
            for candidate in selected_candidates
            if candidate.get("user_required") or candidate.get("user_confirmed")
        ]
        if required_candidates:
            # Do not assign named must-visits to calendar days that are still
            # owned by a cross-day highway leg.  ``build_stages`` deliberately
            # suppresses local routes on those dates; assigning the requirement
            # there would make the later scheduler report a false omission
            # even though the route has not arrived at the destination yet.
            stay_day_indexes = list(range(day_count))
            day_dates = [
                date.fromisoformat(str(item.get("date")))
                for item in state.get("day_plans", [])
                if item.get("date")
            ]
            selected_route_data = (state.get("selected_route") or {}).get("data") or {}
            selected_mode = str(
                selected_route_data.get("selected_mode") or selected_intercity_mode
            ).casefold()
            try:
                outbound_minutes = int(selected_route_data.get("duration_minutes") or 0)
            except (TypeError, ValueError):
                outbound_minutes = 0
            if selected_mode == "driving" and outbound_minutes and day_dates:
                outbound_start = _request_clock(
                    day_dates[0],
                    request.get("departure_time"),
                    default=time(8, 0),
                )
                effective_daily = max(
                    180,
                    int(request.get("max_daily_drive_minutes") or 9 * 60) - 80,
                )
                outbound_arrival = _estimated_driving_arrival_date(
                    outbound_start,
                    outbound_minutes,
                    effective_daily,
                )
                after_arrival = [
                    index for index, value in enumerate(day_dates)
                    if value > outbound_arrival
                ]
                if after_arrival:
                    stay_day_indexes = after_arrival
            if selected_intercity_mode in {"train", "flight", "ferry"} and day_count > 2:
                stay_day_indexes = stay_day_indexes[1:-1] or stay_day_indexes
            if len(stay_day_indexes) > 1 and selected_mode == "driving":
                try:
                    inbound_route = next(
                        route
                        for route in state.get("route_candidates", [])[1:]
                        if route.get("success")
                    )
                    inbound_data = inbound_route.get("data") or {}
                    inbound_minutes = int(inbound_data.get("duration_minutes") or 0)
                except (StopIteration, TypeError, ValueError):
                    inbound_minutes = 0
                if inbound_minutes and day_dates:
                    effective_daily = max(
                        180,
                        int(request.get("max_daily_drive_minutes") or 9 * 60) - 80,
                    )
                    return_days = max(1, ceil(inbound_minutes / effective_daily))
                    return_start_index = max(0, len(day_dates) - return_days)
                    before_return = [
                        index for index in stay_day_indexes
                        if index < return_start_index
                    ]
                    if before_return:
                        stay_day_indexes = before_return
            for requirement_index, candidate in enumerate(required_candidates):
                candidate["coverage_day_index"] = (
                    stay_day_indexes[requirement_index % len(stay_day_indexes)] + 1
                )
        local_routes: list[dict[str, Any]] = []
        used_target_names: set[str] = set()
        for day_index in range(day_count):
            day_pool = [
                candidate
                for candidate in selected_candidates
                if candidate.get("coverage_day_index") == day_index + 1
                and _normalize_poi_name(candidate.get("place", {}).get("name"))
                not in used_target_names
            ]
            day_pool.extend(
                candidate
                for candidate in selected_candidates
                if candidate not in day_pool
                and not (
                    candidate.get("user_required") or candidate.get("user_confirmed")
                )
                and not candidate.get("coverage_day_index")
                and _normalize_poi_name(candidate.get("place", {}).get("name"))
                not in used_target_names
            )
            # A small destination may expose fewer unique POIs than the
            # number of comfortable sightseeing legs. Reuse only
            # non-researched fallback places in that case; a source-backed
            # highlight is never repeated until the full researched set has
            # had a chance to enter the itinerary.
            day_pool.extend(
                candidate
                for candidate in selected_candidates
                if candidate not in day_pool
                and not candidate.get("destination_research_priority")
                and not (
                    candidate.get("user_required") or candidate.get("user_confirmed")
                )
                and not candidate.get("coverage_day_index")
            )
            day_targets = _select_itinerary_places(
                day_pool,
                destination,
                day_budgets[day_index],
                return_candidates=True,
            )
            if not day_targets:
                # A provider outage or a sparse destination must not leave a
                # calendar day without a route node. Keep the day connected
                # with an explicit zero-distance local placeholder; the UI
                # can render it as free time while the user can still edit
                # the day later.
                local_routes.append(
                    {
                        "day_index": day_index,
                        "sequence": 0,
                        "origin": local_base,
                        "destination": local_base,
                        "route": _fallback_local_route(
                            local_base,
                            local_base,
                            default_local_mode,
                        ),
                        "return_to_base": True,
                        "free_time": True,
                    }
                )
                continue
            anchor = local_base
            # Sequence numbers describe the *successful* route chain, not
            # the candidate index.  Candidates can be skipped when a route
            # lookup fails; using the candidate index here and a hard-coded
            # ``2`` for the return leg allowed the return-to-base stage to
            # sort before a later sightseeing leg (for days with three or
            # more researched highlights).  Keep a monotonic counter so the
            # final return leg is always last.
            route_sequence = 0
            for target_candidate in day_targets:
                route = None
                target = None
                target = target_candidate["place"]
                mode = modes[(day_index * 2 + route_sequence) % len(modes)]
                leg_distance = _distance_between_places(anchor, target)
                if leg_distance is not None and leg_distance > LOCAL_LEG_MAX_KM:
                    route = await _intercity_leg_route(anchor, target, day_index)
                else:
                    candidate_route = await _route(
                        registry,
                        anchor,
                        target,
                        state["trip_id"],
                        preferred_mode=mode,
                        fallback_modes=local_fallback_modes,
                    )
                    if candidate_route.get("success") and _local_route_reasonable(candidate_route.get("data", {})):
                        route = candidate_route
                    else:
                        # Keep the selected place in the itinerary when the
                        # routing provider returns no usable geometry. The
                        # fallback is visibly marked as estimated and is still
                        # rechecked by the planner before persistence.
                        fallback_mode = _safe_fallback_local_mode(anchor, target, mode)
                        route = _fallback_local_route(anchor, target, fallback_mode)
                # A schedule adapter can return a structured failure (for
                # example when a flight search is unavailable) without a
                # ``data`` payload.  Never append that object to the stage
                # chain: the builder expects a routable movement and the UI
                # must receive a truthful estimated local connector instead
                # of crashing with ``KeyError: data``.  This is especially
                # important for flight/train trips where local sightseeing
                # legs still need ordinary city transport.
                if (
                    route
                    and target
                    and route.get("success")
                    and isinstance(route.get("data"), dict)
                    and route.get("data", {}).get("selected_mode")
                ):
                    local_routes.append(
                        {
                            "day_index": day_index,
                            "sequence": route_sequence,
                            "origin": anchor,
                            "destination": target,
                            "route": route,
                        }
                    )
                    anchor = target
                    used_target_names.add(_normalize_poi_name(target.get("name")))
                    route_sequence += 1
                elif target:
                    fallback_mode = _safe_fallback_local_mode(
                        anchor,
                        target,
                        default_local_mode,
                    )
                    fallback_route = _fallback_local_route(
                        anchor,
                        target,
                        fallback_mode,
                    )
                    local_routes.append(
                        {
                            "day_index": day_index,
                            "sequence": route_sequence,
                            "origin": anchor,
                            "destination": target,
                            "route": fallback_route,
                            "estimated": True,
                        }
                    )
                    anchor = target
                    used_target_names.add(_normalize_poi_name(target.get("name")))
                    route_sequence += 1
            if not _same_place(anchor, local_base):
                return_distance = _distance_between_places(anchor, local_base)
                if return_distance is not None and return_distance > LOCAL_LEG_MAX_KM:
                    route = await _intercity_leg_route(anchor, local_base, day_index)
                else:
                    route = await _route(
                        registry,
                        anchor,
                        local_base,
                        state["trip_id"],
                        preferred_mode=default_local_mode,
                        fallback_modes=local_fallback_modes,
                    )
                    if not route.get("success"):
                        route = _fallback_local_route(anchor, local_base, default_local_mode)
                if not (
                    route
                    and route.get("success")
                    and isinstance(route.get("data"), dict)
                    and route.get("data", {}).get("selected_mode")
                ):
                    route = _fallback_local_route(anchor, local_base, default_local_mode)
                local_routes.append(
                    {
                        "day_index": day_index,
                        "sequence": route_sequence,
                        "origin": anchor,
                        "destination": local_base,
                        "route": route,
                        "return_to_base": True,
                    }
                )
        await emit(
            state,
            "build_local_routes",
            f"已生成 {len(local_routes)} 个目的地接驳阶段",
            72,
            event="tool_completed",
            tool="amap.poi/amap.route",
        )
        return {
            "local_routes": local_routes,
            "sources": [
                *state.get("sources", []),
                *[
                    source
                    for item in local_routes
                    for source in item["route"].get("sources", [])
                ],
            ],
            "progress": {"node": "build_local_routes", "value": 72},
        }

    async def build_stages(state: RoadManState) -> dict[str, Any]:
        await emit(state, "build_stages", "正在生成每天的多方式移动阶段", 76)
        if not state.get("selected_route"):
            return {"day_plans": state.get("day_plans", [])}
        request = state["trip_request"]
        local_anchor_request = (
            str(request.get("destination_scope") or "").strip().lower() == "poi"
            or bool(request.get("stay_only_at_destination"))
        )
        outbound = state["selected_route"]
        inbound = next(
            (route for route in state.get("route_candidates", [])[1:] if route.get("success")),
            outbound,
        )
        normalized_transport_modes = {
            str(mode).strip().casefold()
            for mode in request.get("transport_modes", [])
            if str(mode).strip()
        }
        scheduled_intercity_requested = bool(
            normalized_transport_modes & {"flight", "train", "ferry", "ship", "boat"}
        )
        driving_requested = not scheduled_intercity_requested and (
            not normalized_transport_modes
            or "driving" in normalized_transport_modes
            or str((outbound.get("data") or {}).get("selected_mode") or "").casefold() == "driving"
        )
        explicit_local_modes = [
            mode for mode in ("transit", "walking", "riding")
            if mode in normalized_transport_modes
        ]
        stage_fallback_modes = (
            ["driving"]
            if driving_requested
            else explicit_local_modes or ["transit", "walking"]
        )
        day_defs = state["day_plans"]
        plans: list[dict[str, Any]] = []
        elevation_sources: list[dict[str, Any]] = []
        elevation_cache: dict[str, float | None] = {}
        hotel_base = select_primary_hotel(
            state.get("tourism_candidates", {}).get("hotels", []),
            request.get("destination"),
            state.get("tourism_candidates", {}).get("attractions", []),
            required_names={
                str(item.get("name") or "").strip()
                for item in request.get("must_visit", [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            },
        )
        hotel_place = (hotel_base or {}).get("place") or request["destination"]
        # The first scheduled leg may start at an airport/station rather than
        # the city's geocoded centroid.  Use that concrete terminal as the
        # return anchor as well, so a fallback road leg cannot close at a
        # random city centre and trigger a false/non-closed itinerary.
        origin_return_anchor = outbound.get("origin_place") or request["origin"]

        # A long outbound self-drive leg can occupy several calendar days.
        # Local routes are discovered before this stage builder runs, so the
        # raw ``local_routes`` list may still contain sightseeing legs for a
        # day that is actually spent driving toward the destination.  Record
        # the expected arrival date up front and suppress those stale local
        # legs; ``enrich_deep_drive_plan`` will distribute the real driving
        # pieces to the corresponding day plans afterwards.
        outbound_arrival_date: date | None = None
        outbound_duration = int(
            (outbound.get("data") or {}).get("duration_minutes") or 0
        )
        outbound_start_date = (
            date.fromisoformat(day_defs[0]["date"]) if day_defs else None
        )
        if outbound_start_date is not None and outbound_duration > 0:
            outbound_start_at = _request_clock(
                outbound_start_date,
                request.get("departure_time"),
                default=time(8, 0),
            )
            if (outbound.get("data") or {}).get("selected_mode") == "driving":
                outbound_arrival_date = _estimated_driving_arrival_date(
                    outbound_start_at,
                    outbound_duration,
                    max(
                        180,
                        int(request.get("max_daily_drive_minutes") or 9 * 60) - 80,
                    ),
                )
            else:
                outbound_arrival_date = (
                    outbound_start_at + timedelta(minutes=outbound_duration)
                ).date()

        inbound_data = inbound.get("data") or {}
        try:
            inbound_duration = int(inbound_data.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            inbound_duration = 0
        max_daily_drive = max(
            180,
            int(request.get("max_daily_drive_minutes") or 9 * 60) - 80,
        )
        long_driving_return = (
            str(inbound_data.get("selected_mode")) == "driving"
            and inbound_duration > max_daily_drive
        )
        inbound_departure_date: date | None = None
        if long_driving_return and day_defs:
            return_days = max(1, ceil(inbound_duration / max(1, max_daily_drive)))
            inbound_departure_date = date.fromisoformat(day_defs[-1]["date"]) - timedelta(
                days=return_days - 1
            )

        async def prepare_route(route: dict[str, Any]) -> dict[str, Any]:
            """Attach best-effort terrain gain to walking/riding routes."""
            if not settings.enable_route_elevation:
                return route
            data = route.get("data") or {}
            mode = data.get("selected_mode")
            geometry = data.get("geometry") or []
            if mode not in {"walking", "riding"} or len(geometry) < 2:
                return route
            cache_key = ";".join(
                f"{point.get('longitude')},{point.get('latitude')}"
                for point in geometry[:: max(1, len(geometry) // 24)]
                if isinstance(point, dict)
            )
            if cache_key in elevation_cache:
                data["elevation_gain_m"] = elevation_cache[cache_key]
                return route
            sampled = [
                point
                for point in geometry[:: max(1, len(geometry) // 24)]
                if isinstance(point, dict) and point.get("longitude") is not None and point.get("latitude") is not None
            ]
            if sampled[-1] is not geometry[-1] and isinstance(geometry[-1], dict):
                sampled.append(geometry[-1])
            try:
                async with httpx.AsyncClient(timeout=3.5) as client:
                    response = await client.get(
                        "https://api.open-meteo.com/v1/forecast",
                        params={
                            "latitude": ",".join(str(point["latitude"]) for point in sampled),
                            "longitude": ",".join(str(point["longitude"]) for point in sampled),
                            "current": "temperature_2m",
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                entries = payload if isinstance(payload, list) else [payload]
                elevations = [float(item["elevation"]) for item in entries if item.get("elevation") is not None]
                gain = round(sum(max(0.0, current - previous) for previous, current in zip(elevations, elevations[1:])), 1)
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                gain = None
            elevation_cache[cache_key] = gain
            data["elevation_gain_m"] = gain
            if gain is not None:
                elevation_sources.append(
                    SourceRecord(
                        provider="Open-Meteo",
                        title="路线高程 API",
                        url="https://api.open-meteo.com/v1/forecast",
                    ).model_dump(mode="json")
                )
            return route

        # Resolve a self-drive return from the actual hotel once, before the
        # return start day. A long return may begin several days before the
        # requested end date; waiting until the final-day branch would make it
        # teleport from the city centroid and then overflow beyond the trip.
        if (
            str((inbound.get("data") or {}).get("selected_mode")) == "driving"
            and hotel_place.get("coordinates")
            and not _same_place(hotel_place, origin_return_anchor)
        ):
            hotel_return = await _route(
                registry,
                hotel_place,
                origin_return_anchor,
                state["trip_id"],
                preferred_mode="driving",
                fallback_modes=stage_fallback_modes,
            )
            if hotel_return.get("success"):
                hotel_return["origin_place"] = hotel_place
                hotel_return["destination_place"] = origin_return_anchor
                inbound = hotel_return

        # Keep a real sightseeing window between local transfer stages. The
        # normal city plan uses a shorter buffer because several highlights
        # may share one day; a named scenic anchor gets a full three-hour
        # principal visit unless the terminal deadline forces an earlier stop.
        local_visit_buffer = 195 if local_anchor_request else 105

        for index, day_def in enumerate(day_defs):
            stages: list[MovementStage] = []
            day_date = date.fromisoformat(day_def["date"])
            return_route = inbound
            if (
                index == len(day_defs) - 1
                and not long_driving_return
                and str((inbound.get("data") or {}).get("selected_mode")) == "driving"
                and hotel_place.get("coordinates")
                and not _same_place(hotel_place, origin_return_anchor)
            ):
                # A self-drive return should leave from the booked hotel, not
                # teleport back to the destination city centroid.
                return_route = await _route(
                    registry,
                    hotel_place,
                    origin_return_anchor,
                    state["trip_id"],
                    preferred_mode="driving",
                    fallback_modes=stage_fallback_modes,
                )
                if not return_route.get("success"):
                    return_route = inbound
                else:
                    return_route["origin_place"] = hotel_place
                    return_route["destination_place"] = origin_return_anchor
            if index == 0:
                outbound = await prepare_route(outbound)
                outbound_start = _request_clock(
                    day_date,
                    request.get("departure_time"),
                    default=time(8, 0),
                )
                scheduled_departure = _parse_train_datetime(
                    (outbound.get("data") or {}).get("scheduled_departure_at")
                )
                if scheduled_departure and scheduled_departure < outbound_start:
                    # A late-evening request can have its final available
                    # train a few minutes earlier.  Start the stage with the
                    # station-access buffer instead of displaying a leg that
                    # begins after the train has already departed.
                    outbound_start = scheduled_departure - timedelta(
                        minutes=_intercity_departure_buffer(
                            (outbound.get("data") or {}).get("selected_mode")
                        )
                    )
                stages.append(
                    _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="城市出发",
                        origin=outbound.get("origin_place") or request["origin"],
                        destination=outbound.get("destination_place") or request["destination"],
                        route=outbound,
                        start_at=outbound_start,
                    )
                )
            local_start = (
                stages[-1].planned_end + timedelta(minutes=60)
                if stages
                else datetime.combine(day_date, time(9, 30), tzinfo=SHANGHAI)
            )
            return_cutoff: datetime | None = None
            if index == len(day_defs) - 1:
                scheduled_departure = _parse_train_datetime(
                    (return_route.get("data") or {}).get("scheduled_departure_at")
                )
                if scheduled_departure:
                    return_cutoff = scheduled_departure - timedelta(
                        minutes=_intercity_departure_buffer(
                            (return_route.get("data") or {}).get("selected_mode")
                        )
                    )
                elif request.get("return_time"):
                    # For a road/local return there is no provider departure
                    # timestamp to act as the terminal cutoff.  The explicit
                    # return clock is an *arrival* deadline, so reserve the
                    # whole return-leg duration before it.  Without this
                    # cutoff the sightseeing scheduler fills the day until
                    # the deadline and ``_return_stage_start`` subsequently
                    # pushes a long drive into the next calendar day (for
                    # example a route ending at 00:30 on the following day).
                    # Treating the latest feasible departure as the cutoff
                    # lets the existing connector logic trim the last local
                    # activities and keep the final stage on the requested
                    # day.
                    deadline = _request_clock(
                        day_date,
                        request.get("return_time"),
                        default=time(23, 59),
                    )
                    try:
                        return_duration = int(
                            (return_route.get("data") or {}).get(
                                "duration_minutes"
                            )
                            or 0
                        )
                    except (TypeError, ValueError):
                        return_duration = 0
                    return_buffer = 0
                    if str(
                        (return_route.get("data") or {}).get("selected_mode")
                    ) == "driving":
                        # ``enrich_deep_drive_plan`` adds real rest/charging
                        # stops after stages are built. Reserve that buffer
                        # now, otherwise a route that appears to end exactly
                        # at the user's deadline can drift past it during the
                        # safety pass and be split into the next day.
                        max_continuous = max(
                            60,
                            int(
                                request.get("max_continuous_drive_minutes")
                                or 120
                            ),
                        )
                        rest_count = max(
                            0,
                            ceil(return_duration / max_continuous) - 1,
                        )
                        return_buffer = 30 + (rest_count * 20)
                    return_cutoff = deadline - timedelta(
                        minutes=max(0, return_duration + return_buffer)
                    )
            local_items = [
                item
                for item in state.get("local_routes", [])
                if item["day_index"] == index
            ]
            if (
                index > 0
                and outbound_arrival_date is not None
                and day_date <= outbound_arrival_date
                and str((outbound.get("data") or {}).get("selected_mode"))
                == "driving"
            ):
                # Do not teleport from a route-derived overnight stop to a
                # destination attraction while the outbound leg is still in
                # progress.  The next usable sightseeing day starts after
                # the final driving piece arrives.
                local_items = []
            if (
                inbound_departure_date is not None
                and day_date >= inbound_departure_date
            ):
                # The return route owns these calendar days. Do not inject
                # destination sightseeing or hotel-area connectors into the
                # middle of a cross-day drive home.
                local_items = []
            # A late intercity train can cross midnight.  Do not append local
            # sightseeing transfers to the departure calendar day after the
            # traveller is still on the train; those items belong to the
            # destination day and are regenerated there by the daily planner.
            if (
                index == 0
                and stages
                and (
                    stages[-1].planned_end.date() > day_date
                    or local_start.date() != day_date
                    or local_start.time() < time(7, 0)
                    # A comfortable intercity arrival leaves no useful
                    # sightseeing window late in the evening.  Previously a
                    # 19:10 arrival still spawned a 20:10 transfer, which
                    # chained past midnight and eventually rendered a 03:00
                    # attraction.  Keep the arrival day for dinner, hotel
                    # check-in and rest; the next calendar day owns local
                    # sightseeing.
                    or stages[-1].planned_end.time() >= time(18, 0)
                )
            ):
                local_items = []
            if local_items and stages:
                first_local_origin = local_items[0].get("origin") or hotel_place
                if not _same_place(
                    stages[-1].destination.model_dump(mode="json"),
                    first_local_origin,
                ):
                    connector_mode = (
                        "driving"
                        if (outbound.get("data") or {}).get("selected_mode") == "driving"
                        else "transit"
                    )
                    connector_route = await _route(
                        registry,
                        stages[-1].destination.model_dump(mode="json"),
                        first_local_origin,
                        state["trip_id"],
                        preferred_mode=connector_mode,
                        fallback_modes=stage_fallback_modes,
                    )
                    if not connector_route.get("success"):
                        connector_route = _fallback_local_route(
                            stages[-1].destination.model_dump(mode="json"),
                            first_local_origin,
                            connector_mode,
                        )
                    connector_route = await prepare_route(connector_route)
                    arrival_connector = _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="入住酒店接驳",
                        origin=stages[-1].destination.model_dump(mode="json"),
                        destination=first_local_origin,
                        route=connector_route,
                        start_at=local_start,
                    )
                    stages.append(arrival_connector)
                    local_start = arrival_connector.planned_end + timedelta(minutes=30)
            for local in sorted(local_items, key=lambda item: item["sequence"]):
                # Never trust a provider/Agent-produced origin after the
                # previous local stage has already been materialized.  A
                # multi-area result can contain stale origins (or two POIs
                # returned in a different order); carrying that value into a
                # MovementStage creates a visible map jump and a hard
                # ROUTE_DISCONTINUITY blocker. Reconnect the leg from the
                # actual previous destination while preserving its intended
                # target.
                expected_origin = (
                    stages[-1].destination.model_dump(mode="json")
                    if stages
                    else (local.get("origin") or hotel_place)
                )
                local_destination = local.get("destination") or expected_origin
                if not _same_place(expected_origin, local.get("origin")):
                    reconnect_mode = str(
                        (local.get("route") or {}).get("data", {}).get("selected_mode")
                        or (outbound.get("data") or {}).get("selected_mode")
                        or ("driving" if driving_requested else "transit")
                    ).casefold()
                    if reconnect_mode not in {
                        "driving",
                        "transit",
                        "riding",
                        "walking",
                    }:
                        reconnect_mode = "driving" if driving_requested else "transit"
                    reconnect_route = await _route(
                        registry,
                        expected_origin,
                        local_destination,
                        state["trip_id"],
                        preferred_mode=reconnect_mode,
                        fallback_modes=stage_fallback_modes,
                    )
                    if not reconnect_route.get("success"):
                        reconnect_route = _fallback_local_route(
                            expected_origin,
                            local_destination,
                            reconnect_mode,
                        )
                    local["origin"] = expected_origin
                    local["route"] = reconnect_route
                local["route"] = await prepare_route(local["route"])
                stage = _movement_stage(
                    day_id=f"day_{index + 1}",
                    sequence=len(stages),
                    title=_local_stage_title(
                        local["route"]["data"].get("selected_mode"),
                        return_to_base=local.get("return_to_base", False),
                    ),
                    origin=local.get("origin") or expected_origin,
                    destination=local_destination,
                    route=local["route"],
                    start_at=local_start,
                )
                if (
                    stages
                    and index < len(day_defs) - 1
                    and (
                        stage.planned_start.date() != day_date
                        or stage.planned_end.date() != day_date
                        or stage.planned_start.time() < time(7, 0)
                        or stage.planned_end.time() > time(21, 30)
                    )
                ):
                    # A local sightseeing leg on a non-return day must stay
                    # inside the same calendar day and finish early enough
                    # for a comfortable return to the booked hotel.  The old
                    # guard only covered index 0, so a later day could append
                    # a 23:50 attraction, push hotel check-in past midnight,
                    # and make the next day's first connector overlap the
                    # overnight hotel.  Stop the local chain here; the
                    # day-base connector below closes the day cleanly and the
                    # following day starts from the hotel.
                    break
                if return_cutoff and stage.planned_end > return_cutoff:
                    # A scheduled intercity return is a hard timetable.  Do
                    # not let a late local transfer push the traveller onto a
                    # train they can no longer catch; keep the feasible
                    # morning items and leave for the station on time.
                    break
                stages.append(stage)
                local_start = stage.planned_end + timedelta(minutes=local_visit_buffer)
            if index == len(day_defs) - 1 and return_cutoff and not long_driving_return:
                # The local-route builder may have stopped before its
                # generated "back to base" connector because that connector
                # would miss the train. Reconcile the chain here: retain as
                # many morning activities as fit, then add a final connector
                # to the departure terminal so the return stage is continuous.
                return_origin = return_route.get("origin_place") or request["destination"]
                # On a non-self-drive return, the last sightseeing leg must
                # first go back to the booked hotel.  The intercity return
                # route starts at the departure airport/station, so using it
                # directly here would silently connect an attraction to the
                # terminal and skip the hotel entirely.
                return_base = (
                    hotel_place
                    if hotel_place.get("coordinates")
                    and not _same_place(hotel_place, return_origin)
                    else return_origin
                )
                destination_coordinates = return_base.get("coordinates") or {}

                def reaches_destination(stage: MovementStage) -> bool:
                    coordinates = stage.destination.coordinates
                    try:
                        return _haversine_km(
                            RoutePoint(
                                longitude=float(coordinates.longitude),
                                latitude=float(coordinates.latitude),
                            ),
                            RoutePoint(
                                longitude=float(destination_coordinates["longitude"]),
                                latitude=float(destination_coordinates["latitude"]),
                            ),
                        ) <= 0.2
                    except (AttributeError, KeyError, TypeError, ValueError):
                        return stage.destination.name == return_base.get("name")

                if stages and not reaches_destination(stages[-1]):
                    connector_route = await _route(
                        registry,
                        stages[-1].destination.model_dump(mode="json"),
                        return_base,
                        state["trip_id"],
                        preferred_mode=("driving" if driving_requested else "transit"),
                        fallback_modes=stage_fallback_modes,
                    )
                    if not connector_route.get("success"):
                        connector_route = _fallback_local_route(
                            stages[-1].destination.model_dump(mode="json"),
                            return_base,
                            "driving" if driving_requested else "transit",
                        )
                    connector_route = await prepare_route(connector_route)
                    connector_start = stages[-1].planned_end + timedelta(minutes=45)
                    connector_stage = _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="返程接驳",
                        origin=stages[-1].destination.model_dump(mode="json"),
                        destination=return_base,
                        route=connector_route,
                        start_at=connector_start,
                    )
                    same_return_anchor = False
                    while connector_stage.planned_end > return_cutoff and stages:
                        stages.pop()
                        if stages:
                            connector_start = stages[-1].planned_end + timedelta(minutes=45)
                            next_origin = stages[-1].destination.model_dump(mode="json")
                            connector_route = await _route(
                                registry,
                                next_origin,
                                return_base,
                                state["trip_id"],
                                preferred_mode=("driving" if driving_requested else "transit"),
                                fallback_modes=stage_fallback_modes,
                            )
                            if not connector_route.get("success"):
                                connector_route = _fallback_local_route(
                                    next_origin, return_base, "driving" if driving_requested else "transit"
                                )
                            connector_route = await prepare_route(connector_route)
                            connector_stage = _movement_stage(
                                day_id=f"day_{index + 1}",
                                sequence=len(stages),
                                title="返程接驳",
                                origin=next_origin,
                                destination=return_base,
                                route=connector_route,
                                start_at=connector_start,
                            )
                        else:
                            # All local sightseeing legs were removed to meet
                            # the terminal cutoff.  Keep the hotel-to-terminal
                            # transfer rather than creating a disconnected
                            # terminal-to-terminal placeholder.
                            connector_origin = hotel_place
                            connector_destination = return_origin
                            same_return_anchor = _same_place(
                                connector_origin,
                                connector_destination,
                            )
                            if same_return_anchor:
                                local_start = max(
                                    local_start,
                                    _request_clock(day_date, "08:00", default=time(8, 0)),
                                )
                            connector_mode = (
                                "driving"
                                if driving_requested
                                else "transit"
                            )
                            connector_route = (
                                {"success": False}
                                if same_return_anchor
                                else await _route(
                                    registry,
                                    connector_origin,
                                    connector_destination,
                                    state["trip_id"],
                                    preferred_mode=connector_mode,
                                    fallback_modes=stage_fallback_modes,
                                )
                            )
                            if not connector_route.get("success"):
                                connector_route = _fallback_local_route(
                                    connector_origin,
                                    connector_destination,
                                    connector_mode,
                                )
                            connector_route = await prepare_route(connector_route)
                            connector_start = return_cutoff - timedelta(
                                minutes=int(
                                    (connector_route.get("data") or {}).get(
                                        "duration_minutes"
                                    )
                                    or 30
                                )
                            )
                            connector_stage = _movement_stage(
                                day_id=f"day_{index + 1}",
                                sequence=0,
                                title="返程接驳",
                                origin=connector_origin,
                                destination=connector_destination,
                                route=connector_route,
                                start_at=connector_start,
                            )
                            break
                    if (
                        (not stages or connector_stage.planned_end <= return_cutoff)
                        and not same_return_anchor
                    ):
                        stages.append(connector_stage)
                        local_start = connector_stage.planned_end + timedelta(minutes=15)
                elif not stages:
                    # A morning train/flight can leave no sightseeing window on
                    # the return day, but the stage chain still has to connect
                    # the overnight hotel to the departure terminal.
                    connector_origin = hotel_place
                    connector_destination = return_origin
                    same_return_anchor = _same_place(
                        connector_origin,
                        connector_destination,
                    )
                    if same_return_anchor:
                        # A nearby hotel and the return anchor represent the
                        # same place.  Do not expose a zero-minute
                        # hotel-to-hotel stage; resume from a comfortable
                        # daytime window for the actual return leg.
                        local_start = max(
                            local_start,
                            _request_clock(day_date, "08:00", default=time(8, 0)),
                        )
                    connector_mode = (
                        "driving"
                        if driving_requested
                        else "transit"
                    )
                    connector_route = (
                        {"success": False}
                        if same_return_anchor
                        else await _route(
                            registry,
                            connector_origin,
                            connector_destination,
                            state["trip_id"],
                            preferred_mode=connector_mode,
                            fallback_modes=stage_fallback_modes,
                        )
                    )
                    if not connector_route.get("success"):
                        connector_route = _fallback_local_route(
                            connector_origin,
                            connector_destination,
                            connector_mode,
                        )
                    connector_route = await prepare_route(connector_route)
                    connector_start = return_cutoff - timedelta(
                        minutes=int((connector_route.get("data") or {}).get("duration_minutes") or 30)
                    )
                    connector_stage = _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=0,
                        title="返程接驳",
                        origin=connector_origin,
                        destination=connector_destination,
                        route=connector_route,
                        start_at=connector_start,
                    )
                    if (
                        connector_stage.planned_end <= return_cutoff
                        and not same_return_anchor
                    ):
                        stages.append(connector_stage)
                        local_start = connector_stage.planned_end + timedelta(minutes=15)
            # Every calendar day must finish at the booked hotel base before the
            # next day starts. If the evening cutoff stopped the sightseeing
            # chain early, retain an explicit return-to-hotel connector rather
            # than letting the next day's first local stage start elsewhere.
            # Add that missing connector before persisting the day plan.
            day_base = (
                (local_items[0].get("origin") if local_items else None)
                or hotel_place
                or request["destination"]
            )
            needs_day_base_connector = (
                bool(stages)
                and not _same_place(
                    stages[-1].destination.model_dump(mode="json"),
                    day_base,
                )
                and stages[-1].planned_end.date() == day_date
                # The final sightseeing day also returns to the booked hotel
                # before the intercity departure, even when a provider did
                # not return a scheduled departure timestamp.
                and bool(day_defs)
            )
            if needs_day_base_connector:
                local_mode = "driving" if driving_requested else "transit"
                # A local sightseeing chain must close at the booked base
                # before the night.  Reusing the previous ``local_start``
                # (which includes a full sightseeing dwell) used to push the
                # connector to 23:00–01:00 and move the hotel check-in onto
                # the next calendar day.  For a comfortable itinerary, remove
                # the latest optional local leg and recompute the connector
                # until it ends by 22:30.  This is destination-agnostic and
                # preserves required places whenever there is a feasible slot.
                connector_latest_end = datetime.combine(
                    day_date,
                    time(22, 30) if index < len(day_defs) - 1 else time(23, 15),
                    tzinfo=SHANGHAI,
                )

                async def build_day_base_connector(
                    start_at: datetime,
                ) -> MovementStage:
                    base_origin = stages[-1].destination.model_dump(mode="json")
                    connector_route = await _route(
                        registry,
                        base_origin,
                        day_base,
                        state["trip_id"],
                        preferred_mode=local_mode,
                        fallback_modes=stage_fallback_modes,
                    )
                    if not connector_route.get("success"):
                        connector_route = _fallback_local_route(
                            base_origin,
                            day_base,
                            local_mode,
                        )
                    connector_route = await prepare_route(connector_route)
                    return _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="返回住宿或目的地核心区",
                        origin=base_origin,
                        destination=day_base,
                        route=connector_route,
                        start_at=start_at,
                    )

                connector_stage = await build_day_base_connector(
                    max(
                        stages[-1].planned_end + timedelta(minutes=15),
                        local_start,
                    )
                )
                while (
                    connector_stage.planned_end > connector_latest_end
                    and len(stages) > 1
                    and index < len(day_defs) - 1
                ):
                    # Do not remove the outbound arrival itself. If even the
                    # hotel connector cannot fit after that arrival, keeping
                    # the route is more truthful than inventing a teleport;
                    # ordinary city trips have enough room here.
                    stages.pop()
                    connector_stage = await build_day_base_connector(
                        stages[-1].planned_end + timedelta(minutes=15)
                    )
                if connector_stage.planned_end <= connector_latest_end or len(stages) <= 1:
                    stages.append(connector_stage)
                    local_start = connector_stage.planned_end + timedelta(minutes=15)
            if index == len(day_defs) - 1 and not long_driving_return:
                # Finish the hotel-to-terminal leg explicitly.  The flight or
                # train route itself starts at the terminal, not at the hotel.
                return_origin = return_route.get("origin_place") or request["destination"]
                if stages and not _same_place(
                    stages[-1].destination.model_dump(mode="json"),
                    return_origin,
                ):
                    connector_origin = stages[-1].destination.model_dump(mode="json")
                    connector_mode = (
                        "driving"
                        if driving_requested
                        else "transit"
                    )
                    connector_route = await _route(
                        registry,
                        connector_origin,
                        return_origin,
                        state["trip_id"],
                        preferred_mode=connector_mode,
                        fallback_modes=stage_fallback_modes,
                    )
                    if not connector_route.get("success"):
                        connector_route = _fallback_local_route(
                            connector_origin,
                            return_origin,
                            connector_mode,
                        )
                    connector_route = await prepare_route(connector_route)
                    connector_stage = _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="返程接驳",
                        origin=connector_origin,
                        destination=return_origin,
                        route=connector_route,
                        start_at=stages[-1].planned_end + timedelta(minutes=15),
                    )
                    # If a tight terminal departure leaves no room for the
                    # connector, remove the latest local leg and recompute
                    # from the remaining chain instead of persisting an
                    # impossible overlap.
                    while (
                        return_cutoff
                        and connector_stage.planned_end > return_cutoff
                        and len(stages) > 1
                    ):
                        # Never remove the final return-to-hotel leg just to
                        # meet a tight terminal cutoff.  If there is no room
                        # for both sightseeing and hotel return, drop the
                        # sightseeing chain and keep a clean hotel-to-terminal
                        # transfer instead of producing attraction -> airport.
                        if _same_place(
                            stages[-1].destination.model_dump(mode="json"),
                            day_base,
                        ):
                            stages.clear()
                            connector_origin = hotel_place
                            connector_route = await _route(
                                registry,
                                connector_origin,
                                return_origin,
                                state["trip_id"],
                                preferred_mode=connector_mode,
                                fallback_modes=stage_fallback_modes,
                            )
                            if not connector_route.get("success"):
                                connector_route = _fallback_local_route(
                                    connector_origin,
                                    return_origin,
                                    connector_mode,
                                )
                            connector_route = await prepare_route(connector_route)
                            connector_start = return_cutoff - timedelta(
                                minutes=int(
                                    (connector_route.get("data") or {}).get(
                                        "duration_minutes"
                                    )
                                    or 30
                                )
                            )
                            connector_stage = _movement_stage(
                                day_id=f"day_{index + 1}",
                                sequence=0,
                                title="返程接驳",
                                origin=connector_origin,
                                destination=return_origin,
                                route=connector_route,
                                start_at=connector_start,
                            )
                            break
                        stages.pop()
                        connector_origin = stages[-1].destination.model_dump(mode="json")
                        connector_route = await _route(
                            registry,
                            connector_origin,
                            return_origin,
                            state["trip_id"],
                            preferred_mode=connector_mode,
                            fallback_modes=stage_fallback_modes,
                        )
                        if not connector_route.get("success"):
                            connector_route = _fallback_local_route(
                                connector_origin,
                                return_origin,
                                connector_mode,
                            )
                        connector_route = await prepare_route(connector_route)
                        connector_stage = _movement_stage(
                            day_id=f"day_{index + 1}",
                            sequence=len(stages),
                            title="返程接驳",
                            origin=connector_origin,
                            destination=return_origin,
                            route=connector_route,
                            start_at=stages[-1].planned_end + timedelta(minutes=15),
                        )
                    if return_cutoff is None or connector_stage.planned_end <= return_cutoff:
                        stages.append(connector_stage)
                        local_start = connector_stage.planned_end + timedelta(minutes=15)
            if long_driving_return and day_date == inbound_departure_date:
                return_route = await prepare_route(inbound)
                return_origin = return_route.get("origin_place") or hotel_place
                return_destination = return_route.get("destination_place") or origin_return_anchor
                stages.append(
                    _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="返程",
                        origin=return_origin,
                        destination=return_destination,
                        route=return_route,
                        start_at=_request_clock(day_date, "08:00:00", default=time(8, 0)),
                    )
                )
            elif index == len(day_defs) - 1 and not long_driving_return:
                return_route = await prepare_route(return_route)
                return_start = _return_stage_start(
                    day_date,
                    request.get("return_time"),
                    local_start,
                    return_route,
                )
                # A road return with an explicit arrival clock also carries
                # the deep-drive safety buffer reserved above.  The generic
                # helper only knows the provider movement duration, so its
                # latest start would still be ``deadline - movement`` and
                # the later energy/rest pass could push arrival past the
                # requested clock.  Honour the effective cutoff when the
                # local chain still fits; this keeps the final stage on the
                # same day while preserving all existing provider-timetable
                # handling for trains/flights/ferries.
                if (
                    return_cutoff
                    and request.get("return_time")
                    and str((return_route.get("data") or {}).get("selected_mode"))
                    == "driving"
                    and return_start > return_cutoff
                    and local_start <= return_cutoff
                ):
                    return_start = return_cutoff
                return_origin = return_route.get("origin_place") or request["destination"]
                return_destination = return_route.get("destination_place") or origin_return_anchor
                stages.append(
                    _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="返程",
                        origin=return_origin,
                        destination=return_destination,
                        route=return_route,
                        start_at=return_start,
                    )
                )
                # A provider may return a different terminal on the inbound
                # leg (for example, an airport outbound and a railway station
                # inbound). Join that terminal to the exact outbound anchor
                # instead of declaring the city-centre centroid a closed
                # route. The connector is visible on the map and is subject
                # to the same timing/transport checks as every other stage.
                if not _same_place(
                    stages[-1].destination.model_dump(mode="json"),
                    origin_return_anchor,
                ):
                    connector_origin = stages[-1].destination.model_dump(mode="json")
                    connector_mode = (
                        "driving"
                        if driving_requested
                        else "transit"
                    )
                    connector_route = await _route(
                        registry,
                        connector_origin,
                        origin_return_anchor,
                        state["trip_id"],
                        preferred_mode=connector_mode,
                        fallback_modes=stage_fallback_modes,
                    )
                    if not connector_route.get("success"):
                        connector_route = _fallback_local_route(
                            connector_origin,
                            origin_return_anchor,
                            connector_mode,
                        )
                    connector_route = await prepare_route(connector_route)
                    stages.append(
                        _movement_stage(
                            day_id=f"day_{index + 1}",
                            sequence=len(stages),
                            title="鍥炲埌鍑哄彂浜ら€氭灑绾?",
                            origin=connector_origin,
                            destination=origin_return_anchor,
                            route=connector_route,
                            start_at=stages[-1].planned_end + timedelta(minutes=15),
                        )
                    )
            # Provider fallbacks can still produce a same-place connector
            # while trimming a tight terminal window.  It carries no actual
            # movement and would fail the positive-duration invariant, so
            # remove only that invalid placeholder before persisting the day.
            stages = [
                stage
                for stage in stages
                if stage.duration_minutes > 0
                or not _same_place(
                    stage.origin.model_dump(mode="json"),
                    stage.destination.model_dump(mode="json"),
                )
            ]
            plan = DayPlan(
                id=f"day_{index + 1}",
                day_index=index + 1,
                date=day_date,
                title=f"第 {index + 1} 天",
                items=[DayItemRef(type="stage", id=stage.id) for stage in stages],
                stages=stages,
                total_distance_km=round(sum(stage.distance_km for stage in stages), 2),
                total_drive_minutes=sum(
                    stage.duration_minutes for stage in stages if stage.mode == "driving"
                ),
                total_walk_minutes=sum(
                    stage.duration_minutes for stage in stages if stage.mode == "walking"
                ),
            )
            plans.append(plan.model_dump(mode="json"))
        return {
            "day_plans": plans,
            "sources": [*state.get("sources", []), *elevation_sources],
            "progress": {"node": "build_stages", "value": 78},
        }

    async def sample_weather(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "sample_weather",
            "正在按各阶段预计抵达时间匹配小时天气",
            82,
            event="tool_started",
            tool="open_meteo.forecast",
        )
        plans = state.get("day_plans", [])
        weather_cache: dict[tuple[float, float, int], dict[str, Any] | None] = {}
        weather_sources: list[dict[str, Any]] = []
        now = datetime.now(SHANGHAI)
        for day in plans:
            for stage in day.get("stages", []):
                coordinates = stage["destination"].get("coordinates")
                if not coordinates:
                    continue
                planned = datetime.fromisoformat(stage["planned_end"])
                hours_until = (planned - now).total_seconds() / 3600
                # Open-Meteo's hourly forecast is only treated as actionable
                # for the next 24 hours.  For a later departure we still show
                # a useful, honest snapshot of today's weather instead of
                # fabricating a future hourly value or asking the user to
                # re-check an estimate.
                current_day_reference = hours_until > 24
                horizon = max(1, (planned.date() - now.date()).days + 1)
                forecast_days = 1 if current_day_reference else min(16, horizon)
                key = (coordinates["longitude"], coordinates["latitude"], forecast_days)
                if key not in weather_cache:
                    result = await registry.execute(
                        "open_meteo.forecast",
                        {
                            "latitude": coordinates["latitude"],
                            "longitude": coordinates["longitude"],
                            "forecast_days": forecast_days,
                            "timezone": "Asia/Shanghai",
                        },
                        SkillContext(trip_id=state["trip_id"]),
                    )
                    weather_cache[key] = (
                        result.data
                        if result.success and isinstance(result.data, dict)
                        else None
                    )
                    weather_sources.extend(
                        item.model_dump(mode="json") for item in result.sources
                    )
                sample = None
                using_current_reference = False
                if current_day_reference:
                    sample = _current_weather_sample(weather_cache[key], now)
                    using_current_reference = sample is not None
                else:
                    sample = _closest_weather_sample(weather_cache[key], planned)
                    if sample is None:
                        # A provider may return a partial hourly payload. Use
                        # the current-day snapshot rather than displaying an
                        # unusable “re-check later” placeholder.
                        sample = _current_weather_sample(weather_cache[key], now)
                        using_current_reference = sample is not None
                if sample:
                    temperature = sample.get("temperature_c")
                    precipitation = sample.get("precipitation_probability")
                    # Even when the hourly horizon is beyond 24 hours and we
                    # use today's snapshot as a conservative reference, the
                    # user-facing label must remain a forecast reference. A
                    # trip plan should never imply that this is live weather
                    # at the future arrival time.
                    prefix = "预报天气参考" if using_current_reference else "预计抵达"
                    precipitation_text = (
                        f"{precipitation}%" if precipitation is not None else "暂无"
                    )
                    stage["weather_summary"] = (
                        f"{prefix} {temperature}°C，降水概率 {precipitation_text}"
                    )
                    stage["weather_samples"] = [
                        {
                            "place": stage["destination"],
                            "sampled_at": sample["sampled_at"],
                            "temperature_c": temperature,
                            "precipitation_probability": precipitation,
                            "weather_code": sample.get("weather_code"),
                            "visibility_m": sample.get("visibility_m"),
                            "wind_speed_kmh": sample.get("wind_speed_kmh"),
                            "estimated": False,
                        }
                    ]
                else:
                    stage["weather_summary"] = "预报天气暂不可用，已按基础风险继续规划"
        await emit(
            state,
            "sample_weather",
            "阶段天气匹配完成",
            86,
            event="tool_completed",
            tool="open_meteo.forecast",
        )
        return {
            "day_plans": plans,
            "weather_results": [
                value for value in weather_cache.values() if value is not None
            ],
            "sources": [*state.get("sources", []), *weather_sources],
            "progress": {"node": "sample_weather", "value": 86},
        }

    async def review_tourism_suitability(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "review_tourism_suitability",
            "候选适配智能体正在逐项结合日期、天气、气温、海拔与用户偏好复核候选",
            87,
            event="tool_started",
            tool="deepseek.poi_suitability",
        )
        candidates = state.get("tourism_candidates", {})
        # A cloud suitability pass can legitimately take longer than a single
        # HTTP request. Keep the planning stream alive with bounded heartbeats
        # instead of appearing frozen at 87%, while still enforcing a finite
        # fallback window.
        review_task = asyncio.create_task(
            poi_suitability_agent.review(
                candidates,
                state.get("trip_request", {}),
                state.get("day_plans", []),
                state.get("weather_results", []),
            )
        )
        started_at = monotonic_time.monotonic()
        decisions: list[dict[str, Any]] = []
        while not review_task.done():
            try:
                decisions = await asyncio.wait_for(asyncio.shield(review_task), timeout=4.0)
            except asyncio.TimeoutError:
                elapsed = int(monotonic_time.monotonic() - started_at)
                await emit(
                    state,
                    "review_tourism_suitability_wait",
                    f"候选适配智能体仍在核验（已等待 {elapsed} 秒），将保留可解释的保守候选",
                    88,
                    event="progress",
                    tool="deepseek.poi_suitability",
                )
                if elapsed >= min(45, max(12, int(settings.deepseek_timeout_seconds))):
                    review_task.cancel()
                    await emit(
                        state,
                        "review_tourism_suitability_timeout",
                        "候选适配智能体超时，已切换到逐候选保守复核",
                        89,
                        event="progress",
                        tool="deepseek.poi_suitability",
                    )
                    break
            except (Exception, asyncio.CancelledError):
                decisions = []
                break
        if review_task.done() and not decisions:
            try:
                decisions = await review_task
            except (Exception, asyncio.CancelledError):
                decisions = []
        await emit(
            state,
            "review_tourism_suitability_finalize",
            "候选适配智能体已返回，正在合并每个景点的日期与天气结论",
            89,
            event="progress",
            tool="deepseek.poi_suitability",
        )
        if decisions:
            candidates = apply_agent_suitability(candidates, decisions)
        # A model review is supported by a provider-category integrity pass.
        # Broad map searches can return KTVs, pharmacies or generic services
        # under a scenic keyword; keep those records as explainable backups,
        # but never let them become executable attraction activities.
        candidates = apply_candidate_type_guard(candidates)
        candidates, seasonal_review = apply_seasonal_guard(
            candidates,
            parse_trip_date(state.get("trip_request", {}).get("start_date")),
            parse_trip_date(state.get("trip_request", {}).get("end_date")),
        )
        await emit(
            state,
            "review_tourism_suitability",
            (
                f"候选逐项复核完成：智能体返回 {len(decisions)} 条判断，"
                f"{len(seasonal_review)} 项降为备选"
            ),
            89,
            event="tool_completed",
            tool="deepseek.poi_suitability",
        )
        return {
            "tourism_candidates": candidates,
            "seasonal_review": seasonal_review,
            "progress": {"node": "review_tourism_suitability", "value": 89},
        }

    async def load_vehicle_profile(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "load_vehicle_profile",
            "车辆资料智能体正在读取续航、电量与车辆限制",
            79,
            event="tool_started",
            tool="carinfo.demo",
        )
        vehicle = state.get("vehicle_profile")
        sources: list[dict[str, Any]] = []
        if not vehicle:
            result = await registry.execute(
                "carinfo.demo",
                {"power_type": "electric"},
                SkillContext(trip_id=state["trip_id"]),
            )
            items = result.data.get("items", []) if isinstance(result.data, dict) else []
            vehicle = {**items[0], "current_energy_percent": 80} if items else default_vehicle()
            sources = [item.model_dump(mode="json") for item in result.sources]
        vehicle = {
            **default_vehicle(),
            **vehicle,
            "safe_energy_reserve_percent": (
                vehicle.get("safe_energy_reserve_percent") or 15
            ),
            "estimated": bool(vehicle.get("estimated", state.get("vehicle_profile") is None)),
        }
        await emit(
            state,
            "load_vehicle_profile",
            f"车辆上下文已就绪：{vehicle['model']}",
            81,
            event="tool_completed",
            tool="carinfo.demo",
        )
        return {
            "vehicle_profile": vehicle,
            "sources": [*state.get("sources", []), *sources],
            "progress": {"node": "load_vehicle_profile", "value": 81},
        }

    async def discover_services(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "discover_services",
            "正在查询沿途服务区、补能、停车、餐饮、医院和厕所",
            83,
            event="tool_started",
            tool="amap.poi",
        )
        categories = {
            "rest": "服务区",
            "charging": "充电站",
            "fueling": "加油站",
            "parking": "停车场",
            "meal": "餐厅",
            "overnight_hotel": "酒店",
            "hospital": "医院",
            "toilet": "公共厕所",
        }
        services: dict[str, dict[str, list[dict[str, Any]]]] = {}
        service_centers: dict[str, tuple[float, float]] = {}
        sources: list[dict[str, Any]] = []
        reused_categories = 0
        for day in state.get("day_plans", []):
            for stage in day.get("stages", []):
                if stage["mode"] != "driving" or not stage.get("route_segments"):
                    continue
                coordinates = stage["route_segments"][0].get("coordinates", [])
                if not coordinates:
                    continue
                center = coordinates[len(coordinates) // 2]
                center_key = (center["longitude"], center["latitude"])
                stage_services: dict[str, list[dict[str, Any]]] = {}
                # These category lookups are independent.  Running them as a
                # bounded fan-out keeps a long-distance plan from spending
                # the worker's entire timeout on eight sequential map calls.
                category_items = list(categories.items())
                lookup_results = await asyncio.gather(
                    *(
                        registry.execute(
                            "amap.poi",
                            {
                                "keywords": keyword,
                                "location": (
                                    f"{center['longitude']},{center['latitude']}"
                                ),
                                "radius": 30000,
                                "page_size": 3,
                            },
                            SkillContext(trip_id=state["trip_id"]),
                        )
                        for _, keyword in category_items
                    )
                )
                for (category, _), result in zip(category_items, lookup_results, strict=True):
                    if result.success and isinstance(result.data, dict):
                        places = [
                            _poi_place(item)
                            for item in result.data.get("items", [])
                            if item.get("name") and item.get("location")
                        ]
                        stage_services[category] = places
                        sources.extend(
                            item.model_dump(mode="json") for item in result.sources
                        )
                    else:
                        stage_services[category] = []
                for category, places in stage_services.items():
                    if places:
                        continue
                    reusable = next(
                        (
                            previous[category]
                            for stage_id, previous in services.items()
                            if previous.get(category)
                            and _nearby_corridor(
                                center_key,
                                service_centers[stage_id],
                            )
                        ),
                        None,
                    )
                    if reusable:
                        stage_services[category] = reusable
                        reused_categories += 1
                services[stage["id"]] = stage_services
                service_centers[stage["id"]] = center_key
        await emit(
            state,
            "discover_services",
            f"已为 {len(services)} 个驾车阶段建立沿途服务清单",
            85,
            event="tool_completed",
            tool="amap.poi",
        )
        return {
            "service_pois": services,
            "sources": [*state.get("sources", []), *sources],
            "warnings": [
                *state.get("warnings", []),
                *(
                    [
                        {
                            "code": "SERVICE_POI_CORRIDOR_REUSED",
                            "message": (
                                f"{reused_categories} 类沿途服务查询失败，"
                                "已复用同一往返走廊的已确认 POI"
                            ),
                            "severity": "warning",
                            "estimated": True,
                        }
                    ]
                    if reused_categories
                    else []
                ),
            ],
            "progress": {"node": "discover_services", "value": 85},
        }

    async def enrich_deep_drive(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "enrich_deep_drive",
            "正在合并补能、驾驶休息、午餐与天气风险",
            90,
        )
        plans, warnings = enrich_deep_drive_plan(
            state.get("day_plans", []),
            state.get("vehicle_profile") or default_vehicle(),
            state.get("service_pois", {}),
            int(state["trip_request"].get("max_continuous_drive_minutes") or 120),
            max_daily_drive_minutes=int(
                state["trip_request"].get("max_daily_drive_minutes") or 540
            ),
        )

        # The stage builder creates one raw intercity driving stage and the
        # deep-drive pass later cuts it into calendar-day pieces.  A connector
        # to the destination hotel cannot be scheduled reliably before that
        # cut: the raw stage still ends on the provider's original date.  Add
        # the connector now, from the actual final outbound piece, so the
        # first sightseeing stage of the following day never starts from a
        # city centroid or from a stale local-route origin.
        request = state["trip_request"]
        selected_route = state.get("selected_route") or {}
        if (selected_route.get("data") or {}).get("selected_mode") == "driving":
            hotel_base = select_primary_hotel(
                state.get("tourism_candidates", {}).get("hotels", []),
                request.get("destination"),
                state.get("tourism_candidates", {}).get("attractions", []),
                required_names={
                    str(item.get("name") or "").strip()
                    for item in request.get("must_visit", [])
                    if isinstance(item, dict) and str(item.get("name") or "").strip()
                },
            )
            hotel_place = (hotel_base or {}).get("place") or request.get("destination")
            if hotel_place:
                for day in plans:
                    stages = sorted(
                        day.get("stages", []),
                        key=lambda item: (item.get("planned_start", ""), item.get("sequence", 0)),
                    )
                    final_outbound = next(
                        (
                            stage
                            for stage in reversed(stages)
                            if "跨天" in str(stage.get("title") or "")
                            and "城市出发" in str(stage.get("title") or "")
                            and _same_place(stage.get("destination"), request.get("destination"))
                        ),
                        None,
                    )
                    if not final_outbound or _same_place(
                        final_outbound.get("destination"), hotel_place
                    ):
                        continue
                    already_connected = any(
                        "返回住宿或目的地核心区" in str(stage.get("title") or "")
                        and _same_place(stage.get("origin"), final_outbound.get("destination"))
                        and _same_place(stage.get("destination"), hotel_place)
                        for stage in stages
                    )
                    if already_connected:
                        continue
                    connector_route = await _route(
                        registry,
                        final_outbound["destination"],
                        hotel_place,
                        state["trip_id"],
                        preferred_mode="driving",
                        fallback_modes=["driving"],
                    )
                    if not connector_route.get("success"):
                        connector_route = _fallback_local_route(
                            final_outbound["destination"],
                            hotel_place,
                            "driving",
                        )
                    connector_stage = _movement_stage(
                        day_id=day.get("id") or f"day_{day.get('day_index', 1)}",
                        sequence=len(stages),
                        title="返回住宿或目的地核心区",
                        origin=final_outbound["destination"],
                        destination=hotel_place,
                        route=connector_route,
                        start_at=datetime.fromisoformat(final_outbound["planned_end"])
                        + timedelta(minutes=15),
                    )
                    connector_data = connector_stage.model_dump(mode="json")
                    connector_limit = max(
                        60,
                        int(request.get("max_continuous_drive_minutes") or 120),
                    )
                    if (
                        connector_stage.mode == "driving"
                        and connector_stage.duration_minutes > connector_limit
                    ):
                        connector_data.setdefault("warnings", []).append(
                            {
                                "code": "REST_STOP_SCHEDULED",
                                "message": (
                                    f"continuity connector exceeds {connector_limit} minutes; "
                                    "a driving rest stop is planned"
                                ),
                                "severity": "warning",
                                "estimated": True,
                            }
                        )
                        connector_data["risk_tags"] = list(
                            dict.fromkeys(
                                [*(connector_data.get("risk_tags") or []), "continuous_drive"]
                            )
                        )
                    stages.append(connector_data)
                    stages = sorted(
                        stages,
                        key=lambda item: (item.get("planned_start", ""), item.get("sequence", 0)),
                    )
                    for sequence, stage in enumerate(stages):
                        stage["sequence"] = sequence
                    day["stages"] = stages
                    day["items"] = [
                        *[{"type": "stage", "id": stage["id"]} for stage in stages],
                        *[
                            {"type": "activity", "id": activity["id"]}
                            for activity in day.get("activities", [])
                        ],
                    ]
        async def ensure_global_stage_continuity() -> None:
            """Join every persisted movement stage with a visible connector.

            Cross-day driving is split before this node runs.  A split can
            still leave a legitimate overnight hotel between the final
            outbound piece and the first return piece (or between two
            provider terminals).  The old verifier reported that as a hard
            discontinuity and the repair loop repeated the same broken
            snapshot.  Build one provider-backed connector for each gap and
            place it on the side of the calendar where it fits.
            """
            for _ in range(max(1, len(plans) * 2 + 2)):
                flattened: list[tuple[dict[str, Any], dict[str, Any]]] = []
                for owner in sorted(plans, key=lambda item: item.get("day_index", 0)):
                    for stage in sorted(
                        owner.get("stages", []),
                        key=lambda item: (
                            str(item.get("planned_start") or ""),
                            int(item.get("sequence") or 0),
                        ),
                    ):
                        flattened.append((owner, stage))
                repaired_gap = False
                for position, ((previous_day, previous), (current_day, current)) in enumerate(
                    zip(flattened, flattened[1:])
                ):
                    if _same_place(previous.get("destination"), current.get("origin")):
                        continue
                    origin = previous.get("destination") or {}
                    destination = current.get("origin") or {}
                    if not origin or not destination:
                        continue
                    try:
                        previous_end = datetime.fromisoformat(previous["planned_end"])
                        current_start = datetime.fromisoformat(current["planned_start"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    requested_modes = {
                        str(mode).strip().casefold()
                        for mode in request.get("transport_modes", [])
                        if str(mode).strip()
                    }
                    preferred_mode = (
                        "driving"
                        if "driving" in requested_modes
                        or previous.get("mode") == "driving"
                        or current.get("mode") == "driving"
                        else "transit"
                    )
                    connector_route = await _route(
                        registry,
                        origin,
                        destination,
                        state["trip_id"],
                        preferred_mode=preferred_mode,
                        fallback_modes=(
                            ["driving"]
                            if preferred_mode == "driving"
                            else ["transit", "walking"]
                        ),
                    )
                    if not connector_route.get("success"):
                        connector_route = _fallback_local_route(
                            origin,
                            destination,
                            preferred_mode,
                        )
                    try:
                        connector_minutes = max(
                            5,
                            int((connector_route.get("data") or {}).get("duration_minutes") or 5),
                        )
                    except (TypeError, ValueError):
                        connector_minutes = 5
                    connector_start = previous_end + timedelta(minutes=15)
                    connector_end = connector_start + timedelta(minutes=connector_minutes)
                    # Keep driving connectors within a calendar day.  When a
                    # corridor is too long for the remaining evening, place
                    # the connector at the next day's first usable hour and
                    # shift that day's chain forward instead of persisting an
                    # impossible cross-midnight driving stage.
                    target_day = previous_day
                    if (
                        preferred_mode == "driving"
                        and connector_end.date() != connector_start.date()
                    ) or (
                        connector_end > current_start
                        and current_day is not previous_day
                    ):
                        target_day = current_day
                        connector_start = max(
                            current_start,
                            datetime.combine(
                                current_start.date(),
                                time(8, 0),
                                tzinfo=current_start.tzinfo,
                            ),
                        )
                        connector_end = connector_start + timedelta(minutes=connector_minutes)
                    connector = _movement_stage(
                        day_id=target_day.get("id") or f"day_{target_day.get('day_index', 1)}",
                        sequence=len(target_day.get("stages", [])),
                        title="返回住宿或目的地核心区",
                        origin=origin,
                        destination=destination,
                        route=connector_route,
                        start_at=connector_start,
                    ).model_dump(mode="json")
                    # A continuity connector can itself be a long highway
                    # transfer. Mark a planned break directly on the stage so
                    # verification does not repeatedly report a planner-
                    # created continuous-driving omission.
                    connector_limit = max(
                        60,
                        int(request.get("max_continuous_drive_minutes") or 120),
                    )
                    if preferred_mode == "driving" and connector_minutes > connector_limit:
                        connector.setdefault("warnings", []).append(
                            {
                                "code": "REST_STOP_SCHEDULED",
                                "message": (
                                    f"continuity connector exceeds {connector_limit} minutes; "
                                    "a driving rest stop is planned"
                                ),
                                "severity": "warning",
                                "estimated": True,
                            }
                        )
                        connector["risk_tags"] = list(
                            dict.fromkeys(
                                [*(connector.get("risk_tags") or []), "continuous_drive"]
                            )
                        )
                    connector["id"] = (
                        f"continuity_{previous.get('id', position)}_"
                        f"{current.get('id', position)}"
                    )
                    connector.setdefault("warnings", []).append(
                        {
                            "code": "STAGE_CONTINUITY_REPAIRED",
                            "message": "已为相邻阶段补充住宿/终端接驳，确保路线连续",
                            "severity": "info",
                        }
                    )
                    target_day.setdefault("stages", []).append(connector)
                    if target_day is current_day and connector_end > current_start:
                        shift = connector_end + timedelta(minutes=15) - current_start
                        shifting = False
                        for stage in sorted(
                            current_day.get("stages", []),
                            key=lambda item: str(item.get("planned_start") or ""),
                        ):
                            if stage is current:
                                shifting = True
                            if not shifting or stage is connector:
                                continue
                            start = _parse_train_datetime(stage.get("planned_start"))
                            end = _parse_train_datetime(stage.get("planned_end"))
                            if start and end:
                                stage["planned_start"] = (start + shift).isoformat()
                                stage["planned_end"] = (end + shift).isoformat()
                    for owner in plans:
                        owner["stages"] = sorted(
                            owner.get("stages", []),
                            key=lambda item: str(item.get("planned_start") or ""),
                        )
                        for sequence, stage in enumerate(owner["stages"]):
                            stage["sequence"] = sequence
                        owner["items"] = [
                            {"type": "stage", "id": stage["id"]}
                            for stage in owner["stages"]
                        ] + [
                            {"type": "activity", "id": activity["id"]}
                            for activity in owner.get("activities", [])
                        ]
                    repaired_gap = True
                    break
                if not repaired_gap:
                    break

        await ensure_global_stage_continuity()

        for day in plans:
            previous_end: datetime | None = None
            for stage in sorted(
                day.get("stages", []),
                key=lambda item: item.get("sequence", 0),
            ):
                start_at = datetime.fromisoformat(stage["planned_start"])
                end_at = datetime.fromisoformat(stage["planned_end"])
                if previous_end and start_at < previous_end:
                    duration = end_at - start_at
                    start_at = previous_end + timedelta(minutes=15)
                    end_at = start_at + duration
                    stage["planned_start"] = start_at.isoformat()
                    stage["planned_end"] = end_at.isoformat()
                previous_end = end_at
            day["total_drive_minutes"] = sum(
                stage["duration_minutes"]
                for stage in day.get("stages", [])
                if stage["mode"] == "driving"
            )
        return {
            "day_plans": plans,
            "warnings": [*state.get("warnings", []), *warnings],
            "progress": {"node": "enrich_deep_drive", "value": 90},
        }

    async def schedule_tourism(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "schedule_tourism",
            "正在安排景点停留、每日餐食与过夜住宿",
            91,
        )
        plans = schedule_tourism_activities(
            state.get("day_plans", []),
            state.get("tourism_candidates", {}),
            state.get("confirmed_additions", []),
            state.get("trip_request", {}).get("destination"),
            state.get("trip_request", {}),
        )
        if settings.enable_poi_web_enrichment:
            plans, enriched_candidates = await enrich_scheduled_activities(
                plans,
                state.get("tourism_candidates", {}),
                timeout_seconds=settings.poi_web_timeout_seconds,
                registry=registry,
                trip_id=state["trip_id"],
            )
        else:
            enriched_candidates = state.get("tourism_candidates", {})
        research = dict(state.get("destination_research", {}))
        coverage = dict(research.get("attraction_coverage", {}))
        scheduled_names = {
            _normalize_poi_name(activity.get("place", {}).get("name"))
            for day in plans
            for activity in day.get("activities", [])
            if activity.get("type") == "attraction"
        }
        coverage["scheduled_names"] = [
            item.get("place", {}).get("name")
            for item in state.get("tourism_candidates", {}).get("attractions", [])
            if item.get("must_see")
            and _normalize_poi_name(item.get("place", {}).get("name")) in scheduled_names
        ]
        coverage["uncovered_names"] = [
            item.get("place", {}).get("name")
            for item in state.get("tourism_candidates", {}).get("attractions", [])
            if item.get("must_see")
            and _normalize_poi_name(item.get("place", {}).get("name")) not in scheduled_names
        ]
        research["attraction_coverage"] = coverage
        return {
            "day_plans": plans,
            "tourism_candidates": enriched_candidates,
            "destination_research": research,
            "progress": {"node": "schedule_tourism", "value": 91},
        }

    async def review_daily_schedule_node(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "review_daily_schedule",
            "每日复核智能体正在检查上午、下午、晚间与三餐住宿",
            93,
        )
        plans, review_notes = review_daily_schedule(
            state.get("day_plans", []),
            state.get("tourism_candidates", {}),
            state.get("confirmed_additions", []),
            state.get("trip_request", {}).get("destination"),
            state.get("trip_request", {}),
        )
        return {
            "day_plans": plans,
            "warnings": [*state.get("warnings", []), *review_notes],
            "progress": {"node": "review_daily_schedule", "value": 93},
        }

    async def verify_plan(state: RoadManState) -> dict[str, Any]:
        await emit(state, "verify_plan", "正在校验路线、交通方式、天气与时间约束", 95)
        # Normalize provider timestamps before enforcing hard constraints.
        # A service or meal can be returned at the exact start of the next
        # movement segment; move that segment forward to avoid a false blocker.
        normalized_days = normalize_plan_calendar(
            _repair_activity_stage_overlaps(state.get("day_plans", []))
        )
        issues: list[dict[str, Any]] = []
        if state.get("error"):
            issues.append(
                {
                    "code": state["error"]["code"],
                    "severity": "blocker",
                    "description": state["error"]["message"],
                }
            )
        if not state.get("day_plans") or not any(day.get("stages") for day in state.get("day_plans", [])):
            issues.append(
                {
                    "code": "EMPTY_PLAN",
                    "severity": "blocker",
                    "description": "未生成可执行移动阶段",
                }
            )
        for day in state.get("day_plans", []):
            if not day.get("stages"):
                issues.append(
                    {
                        "code": "EMPTY_DAY_STAGES",
                        "severity": "blocker",
                        "description": f"第 {day.get('day_index', '?')} 天没有可执行的移动或本地活动节点",
                    }
                )
        issues.extend(
            verify_deep_drive_plan(
                normalized_days,
                state.get("vehicle_profile"),
                int(state["trip_request"].get("max_continuous_drive_minutes") or 120),
            )
        )
        issues.extend(_verify_route_closure(normalized_days))
        issues.extend(
            verify_tourism_plan(
                normalized_days,
                state.get("tourism_candidates", {}),
            )
        )
        # ``return_time`` is an arrival deadline (e.g. “周日晚八点前
        # 回来”), not a request to start the return leg at 20:00.  Check the
        # final persisted stage after all rest/energy inserts so an impossible
        # long-distance window is reported explicitly instead of being shown
        # as a completed but late itinerary.
        request = state.get("trip_request", {})
        if request.get("return_time") and normalized_days:
            try:
                deadline = _request_clock(
                    date.fromisoformat(request["end_date"]),
                    request.get("return_time"),
                    default=time(23, 59),
                )
                final_stage = next(
                    (
                        stage
                        for day in reversed(normalized_days)
                        for stage in reversed(day.get("stages", []))
                        if str(stage.get("title") or "").startswith("返程")
                    ),
                    None,
                )
                if final_stage:
                    arrival = datetime.fromisoformat(final_stage["planned_end"])
                    deadline_issue = _return_deadline_issue(arrival, deadline)
                    if deadline_issue:
                        issues.append(deadline_issue)
            except (KeyError, TypeError, ValueError):
                # Invalid optional clocks are already handled by Requirement
                # preflight; never make verification crash while reporting the
                # rest of the route issues.
                pass
        # Present actionable blockers before degradations.  Weather gaps and
        # uncovered optional highlights are warnings by design; if another
        # constraint really blocks completion, the UI should explain that
        # constraint instead of showing the first warning as the failure.
        issues.sort(key=lambda item: 0 if item.get("severity") == "blocker" else 1)
        repair_attempts = int(state.get("repair_attempts") or 0)
        return {
            "day_plans": normalized_days,
            "verification_result": {
                "passed": not any(item["severity"] == "blocker" for item in issues),
                "issues": issues,
                "auto_repair_attempts": repair_attempts,
                "auto_repair_exhausted": bool(issues)
                and repair_attempts >= MAX_AUTO_REPAIR_ATTEMPTS,
                "auto_repair_history": state.get("repair_history", []),
            },
            "progress": {"node": "verify_plan", "value": 95},
        }

    async def repair_plan(state: RoadManState) -> dict[str, Any]:
        previous_attempts = int(state.get("repair_attempts") or 0)
        attempt = previous_attempts + 1
        issue_codes = [
            str(item.get("code"))
            for item in (state.get("verification_result") or {}).get("issues", [])
            if item.get("severity") == "blocker"
        ]
        before_signature = _repair_plan_signature(state.get("day_plans", []))
        await emit(
            state,
            "repair_plan_refined",
            f"复核发现可修复问题，智能体协作第 {attempt}/{MAX_AUTO_REPAIR_ATTEMPTS} 次自动重排",
            min(95 + attempt, 99),
            event="progress",
        )
        await emit(
            state,
            "repair_plan",
            "正在重排三餐、停留、接驳与驾驶休息，并重新复核",
            min(96 + attempt, 99),
        )
        # Re-run the itinerary/review agents instead of sending the traveller
        # a failure dialog for a defect the system can repair itself.  Route
        # continuity blockers require rebuilding movement stages from the
        # chained local routes; merely rescheduling activities cannot repair a
        # disconnected stage pair.
        rebuild_route = any(
            code
            in {
                "ROUTE_DISCONTINUITY",
                "ROUTE_NOT_CLOSED",
                "EMPTY_DAY_STAGES",
                "STAGE_OVERLAP",
                "ACTIVITY_TIME_OVERLAP",
                "REQUIRED_PLACES_UNSCHEDULED",
                "OVERNIGHT_HOTEL_MISSING",
                "DRIVING_STAGE_CALENDAR_MISMATCH",
                # A late return is often caused by the route stage being
                # built after the day's activities were already filled.  A
                # fresh stage build applies the arrival-deadline cutoff above
                # and trims/reconnects the final day instead of replaying the
                # same impossible timeline four times.
                "RETURN_DEADLINE_UNACHIEVABLE",
            }
            for code in issue_codes
        )
        repair_drive_constraints = rebuild_route or any(
            code in {"CONTINUOUS_DRIVE", "ENERGY_UNSAFE"}
            for code in issue_codes
        )
        repair_input_days = state.get("day_plans", [])
        if rebuild_route:
            rebuilt = await build_stages(state)
            repair_input_days = rebuilt.get("day_plans", repair_input_days)
        drive_warnings: list[dict[str, Any]] = []
        if repair_drive_constraints:
            # A verification blocker such as “城市出发 缺少驾驶休息” is a
            # planner defect, not a traveller-facing clarification.  Re-run
            # the driving/energy pass during the repair loop so long legs are
            # split again after any route rebuild and route-derived safety
            # stops are materialized before the next verification pass.
            repair_input_days, drive_warnings = enrich_deep_drive_plan(
                repair_input_days,
                state.get("vehicle_profile") or default_vehicle(),
                state.get("service_pois", {}),
                int(state["trip_request"].get("max_continuous_drive_minutes") or 120),
                max_daily_drive_minutes=int(
                    state["trip_request"].get("max_daily_drive_minutes") or 540
                ),
            )
        repaired_days = schedule_tourism_activities(
            repair_input_days,
            state.get("tourism_candidates", {}),
            state.get("confirmed_additions", []),
            state.get("trip_request", {}).get("destination"),
            state.get("trip_request", {}),
        )
        repaired_days, review_notes = review_daily_schedule(
            repaired_days,
            state.get("tourism_candidates", {}),
            state.get("confirmed_additions", []),
            state.get("trip_request", {}).get("destination"),
            state.get("trip_request", {}),
        )
        repaired_days = _repair_activity_stage_overlaps(repaired_days)

        # A route rebuild starts from freshly split stages and therefore does
        # not pass through the normal deep-drive node's continuity guard.
        # Reconnect the rebuilt snapshot here as well; otherwise each retry
        # can reintroduce the same hotel/terminal jump it was meant to fix.
        for _ in range(max(1, len(repaired_days) * 2 + 2)):
            flattened: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for owner in sorted(repaired_days, key=lambda item: item.get("day_index", 0)):
                for stage in sorted(
                    owner.get("stages", []),
                    key=lambda item: (
                        str(item.get("planned_start") or ""),
                        int(item.get("sequence") or 0),
                    ),
                ):
                    flattened.append((owner, stage))
            repaired_gap = False
            for position, ((previous_day, previous), (current_day, current)) in enumerate(
                zip(flattened, flattened[1:])
            ):
                if _same_place(previous.get("destination"), current.get("origin")):
                    continue
                try:
                    previous_end = datetime.fromisoformat(previous["planned_end"])
                    current_start = datetime.fromisoformat(current["planned_start"])
                except (KeyError, TypeError, ValueError):
                    continue
                origin = previous.get("destination") or {}
                destination = current.get("origin") or {}
                requested_modes = {
                    str(mode).strip().casefold()
                    for mode in state["trip_request"].get("transport_modes", [])
                    if str(mode).strip()
                }
                preferred_mode = (
                    "driving"
                    if "driving" in requested_modes
                    or previous.get("mode") == "driving"
                    or current.get("mode") == "driving"
                    else "transit"
                )
                connector_route = await _route(
                    registry,
                    origin,
                    destination,
                    state["trip_id"],
                    preferred_mode=preferred_mode,
                    fallback_modes=(
                        ["driving"]
                        if preferred_mode == "driving"
                        else ["transit", "walking"]
                    ),
                )
                if not connector_route.get("success"):
                    connector_route = _fallback_local_route(
                        origin,
                        destination,
                        preferred_mode,
                    )
                connector_minutes = max(
                    5,
                    int((connector_route.get("data") or {}).get("duration_minutes") or 5),
                )
                connector_start = previous_end + timedelta(minutes=15)
                connector_end = connector_start + timedelta(minutes=connector_minutes)
                target_day = previous_day
                if (
                    preferred_mode == "driving"
                    and connector_end.date() != connector_start.date()
                ) or (
                    connector_end > current_start
                    and current_day is not previous_day
                ):
                    target_day = current_day
                    connector_start = max(
                        current_start,
                        datetime.combine(
                            current_start.date(),
                            time(8, 0),
                            tzinfo=current_start.tzinfo,
                        ),
                    )
                connector = _movement_stage(
                    day_id=target_day.get("id") or f"day_{target_day.get('day_index', 1)}",
                    sequence=len(target_day.get("stages", [])),
                    title="返回住宿或目的地核心区",
                    origin=origin,
                    destination=destination,
                    route=connector_route,
                    start_at=connector_start,
                ).model_dump(mode="json")
                connector_limit = max(
                    60,
                    int(state["trip_request"].get("max_continuous_drive_minutes") or 120),
                )
                if preferred_mode == "driving" and connector_minutes > connector_limit:
                    connector.setdefault("warnings", []).append(
                        {
                            "code": "REST_STOP_SCHEDULED",
                            "message": (
                                f"continuity connector exceeds {connector_limit} minutes; "
                                "a driving rest stop is planned"
                            ),
                            "severity": "warning",
                            "estimated": True,
                        }
                    )
                    connector["risk_tags"] = list(
                        dict.fromkeys(
                            [*(connector.get("risk_tags") or []), "continuous_drive"]
                        )
                    )
                connector["id"] = (
                    f"repair_continuity_{previous.get('id', position)}_"
                    f"{current.get('id', position)}"
                )
                connector.setdefault("warnings", []).append(
                    {
                        "code": "STAGE_CONTINUITY_REPAIRED",
                        "message": "已为重排后的相邻阶段补充住宿/终端接驳",
                        "severity": "info",
                    }
                )
                target_day.setdefault("stages", []).append(connector)
                if target_day is current_day and connector_end > current_start:
                    shift = connector_end + timedelta(minutes=15) - current_start
                    shifting = False
                    for stage in sorted(
                        current_day.get("stages", []),
                        key=lambda item: str(item.get("planned_start") or ""),
                    ):
                        if stage is current:
                            shifting = True
                        if not shifting or stage is connector:
                            continue
                        start = _parse_train_datetime(stage.get("planned_start"))
                        end = _parse_train_datetime(stage.get("planned_end"))
                        if start and end:
                            stage["planned_start"] = (start + shift).isoformat()
                            stage["planned_end"] = (end + shift).isoformat()
                for owner in repaired_days:
                    owner["stages"] = sorted(
                        owner.get("stages", []),
                        key=lambda item: str(item.get("planned_start") or ""),
                    )
                    for sequence, stage in enumerate(owner["stages"]):
                        stage["sequence"] = sequence
                    owner["items"] = [
                        {"type": "stage", "id": stage["id"]}
                        for stage in owner["stages"]
                    ] + [
                        {"type": "activity", "id": activity["id"]}
                        for activity in owner.get("activities", [])
                    ]
                repaired_gap = True
                break
            if not repaired_gap:
                break
        after_signature = _repair_plan_signature(repaired_days)
        changed = before_signature != after_signature
        repair_history = [
            *state.get("repair_history", []),
            {
                "attempt": attempt,
                "issue_codes": issue_codes,
                "changed": changed,
                "strategy": (
                    "route_rebuild_and_reschedule"
                    if rebuild_route
                    else "daily_reschedule"
                ),
            },
        ]
        return {
            "day_plans": repaired_days,
            "repair_attempts": attempt,
            "repair_attempted": True,
            "repair_history": repair_history,
            "warnings": [
                *state.get("warnings", []),
                *drive_warnings,
                *review_notes,
                {
                    "code": "AUTO_REPAIR_ATTEMPTED",
                    "message": (
                        f"已完成第 {attempt}/{MAX_AUTO_REPAIR_ATTEMPTS} 次自动复核"
                        + ("，行程结构已更新" if changed else "，未产生有效结构变化")
                        + (f"（问题：{'、'.join(issue_codes[:3])}）" if issue_codes else "")
                    ),
                    "severity": "warning",
                },
            ],
        }

    async def render_markdown(state: RoadManState) -> dict[str, Any]:
        await emit(state, "render_markdown", "正在生成 Markdown 行程安排", 97)
        request = state["trip_request"]
        traveler_count = request.get("travelers")
        traveler_label = f"{traveler_count} 人" if traveler_count else "待确认人数"
        stage_modes = {
            stage.get("mode")
            for day in state.get("day_plans", [])
            for stage in day.get("stages", [])
        }
        transport_label = (
            "飞机" if "flight" in stage_modes
            else "轮船" if "ferry" in stage_modes
            else "火车" if "train" in stage_modes
            else "自驾"
        )
        lines = [
            f"# {request['origin']['name']}—{request['destination']['name']}{transport_label}行程安排",
            "",
            f"- 日期：{request['start_date']} 至 {request['end_date']}",
            f"- 出行人数：{traveler_label}",
            *([f"- 行程时长上限：最多 {request['max_days']} 天"] if request.get("max_days") else []),
            f"- 可见默认值：{', '.join(request.get('defaults_applied', [])) or '无'}",
            "",
        ]
        if request.get("special_events"):
            lines.extend([
                f"- 重点体验：{'、'.join(request['special_events'])}",
                "- 事件校验：出发前根据官方/专业天文或活动来源复核极大值、开放时间、天气与月相。",
                "",
            ])
        if state.get("special_event_research"):
            lines.extend(["### 特殊活动核验", ""])
            for item in state["special_event_research"]:
                lines.append(f"- {event_research_summary(item)}")
                for source in (item.get("sources") or [])[:2]:
                    lines.append(f"  - 来源：{source.get('title') or '公开网页'} {source.get('url') or ''}")
            lines.append("")
        for day in state.get("day_plans", []):
            day_title = day.get("title") or f"第 {day.get('day_index', 1)} 天"
            lines.extend([f"## {day_title} · {day['date']}", ""])
            for stage in day.get("stages", []):
                service_number = stage.get("service_number")
                terminal_text = ""
                if stage.get("departure_terminal") or stage.get("arrival_terminal"):
                    terminal_text = (
                        f" · {stage.get('departure_terminal') or '出发地'} → "
                        f"{stage.get('arrival_terminal') or '到达地'}"
                    )
                service_line = (
                    f"- 班次：{service_number or '未返回'}"
                    f"{terminal_text}"
                    if stage.get("mode") in {"train", "flight", "ferry"}
                    else ""
                )
                transit_lines = [
                    f"{leg.get('line_name') or leg.get('line_type') or '公共交通'}："
                    f"{leg.get('departure_stop') or '上车'} → {leg.get('arrival_stop') or '下车'}"
                    for leg in stage.get("transit_legs", [])
                    if isinstance(leg, dict)
                ]
                lines.extend(
                    [
                        f"### {stage['origin']['name']} → {stage['destination']['name']}",
                        f"- 方式：{stage['mode']}",
                        f"- 时间：{stage['planned_start'][11:16]}–{stage['planned_end'][11:16]}",
                        f"- 里程：{stage['distance_km']} km",
                        f"- 预计耗时：{stage['duration_minutes']} 分钟",
                        f"- 路况：{stage.get('traffic_summary') or '不适用'}",
                        f"- 天气：{stage.get('weather_summary') or '待更新'}",
                        f"- 风险：{stage.get('risk_level', 'low')} · "
                        f"{'、'.join(stage.get('risk_tags', [])) or '无'}",
                        f"- 能耗：{_energy_markdown(stage.get('energy_estimate'))}",
                        *([service_line] if service_line else []),
                        *([f"- 公交/地铁：{'；'.join(transit_lines)}"] if transit_lines else []),
                        "",
                    ]
                )
            for activity in day.get("activities", []):
                opening = (activity.get("opening_hours") or {}).get("text") or "营业时间暂未返回"
                ticket = activity.get("ticket_or_price") or {}
                ticket_text = (
                    f"¥{ticket.get('minimum')}–{ticket.get('maximum')}"
                    if ticket.get("minimum") is not None
                    else {"free": "免费", "known": "票价已返回", "unknown": "门票信息暂未返回"}.get(
                        activity.get("ticket_status"), "门票信息暂未返回"
                    )
                )
                parking = activity.get("parking_note") or "停车信息暂未返回"
                lines.append(
                    f"- 沿途服务：{activity['place']['name']}（{activity['type']}，"
                    f"{activity['duration_minutes']} 分钟）"
                )
                lines.extend([
                    f"  - 营业时间：{opening}",
                    f"  - 门票：{ticket_text}",
                    f"  - 停车：{parking}",
                    f"  - 资料状态：{activity.get('information_status', 'unavailable')} · "
                    f"{activity.get('information_sources_count', 0)} 个来源",
                ])
        if state.get("verification_result", {}).get("issues"):
            lines.extend(["## 校验提示", ""])
            lines.extend(
                f"- {item['description']}"
                for item in state["verification_result"]["issues"]
            )
        lines.extend(
            [
                "",
                "## 安全与数据边界",
                "",
                "- RoadMan 仅提供行程规划辅助，不连接 CAN 总线，不控制车辆，也不替代实时导航、道路公告、运营方或驾驶员判断。",
                "- 天气、补能、班次、开放与预约数据缺失时会保留降级标记；估算位置和估算时长必须在出发前复核。",
                "- 行程、对话与规划状态保存在本地数据库；删除行程时同步清理版本、附件、任务与该行程的工具调用记录。",
            ]
        )
        return {
            "plan_markdown": "\n".join(lines),
            "progress": {"node": "render_markdown", "value": 97},
        }

    async def persist_trip(state: RoadManState) -> dict[str, Any]:
        await emit(state, "persist_trip", "正在保存并核对行程安排", 99, event="progress")
        return {"progress": {"node": "persist_trip", "value": 99}}

    def after_validation(state: RoadManState) -> Literal["clarify", "route"]:
        return "clarify" if state.get("missing_fields") else "route"

    def after_verification(state: RoadManState) -> Literal["repair", "render"]:
        has_blocker = not state.get("verification_result", {}).get("passed", False)
        attempts = int(state.get("repair_attempts") or 0)
        return "repair" if has_blocker and attempts < MAX_AUTO_REPAIR_ATTEMPTS else "render"

    builder = StateGraph(RoadManState)
    builder.add_node("load_context", load_context)
    builder.add_node("extract_trip_request", extract_trip_request)
    builder.add_node("research_events", research_events)
    builder.add_node("apply_defaults", apply_defaults)
    builder.add_node("validate_required_fields", validate_required_fields)
    builder.add_node("generate_clarification", generate_clarification)
    builder.add_node("build_base_route", build_base_route)
    builder.add_node("split_into_days", split_into_days)
    builder.add_node("discover_tourism", discover_tourism)
    builder.add_node("build_local_routes", build_local_routes)
    builder.add_node("build_stages", build_stages)
    builder.add_node("sample_weather", sample_weather)
    builder.add_node("review_tourism_suitability", review_tourism_suitability)
    builder.add_node("load_vehicle_profile", load_vehicle_profile)
    builder.add_node("discover_services", discover_services)
    builder.add_node("enrich_deep_drive", enrich_deep_drive)
    builder.add_node("schedule_tourism", schedule_tourism)
    builder.add_node("review_daily_schedule", review_daily_schedule_node)
    builder.add_node("verify_plan", verify_plan)
    builder.add_node("repair_plan", repair_plan)
    builder.add_node("render_markdown", render_markdown)
    builder.add_node("persist_trip", persist_trip)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "extract_trip_request")
    builder.add_edge("extract_trip_request", "research_events")
    builder.add_edge("research_events", "apply_defaults")
    builder.add_edge("apply_defaults", "validate_required_fields")
    builder.add_conditional_edges(
        "validate_required_fields",
        after_validation,
        {"clarify": "generate_clarification", "route": "build_base_route"},
    )
    builder.add_edge("generate_clarification", END)
    builder.add_edge("build_base_route", "split_into_days")
    builder.add_edge("split_into_days", "discover_tourism")
    builder.add_edge("discover_tourism", "build_local_routes")
    builder.add_edge("build_local_routes", "build_stages")
    builder.add_edge("build_stages", "load_vehicle_profile")
    builder.add_edge("load_vehicle_profile", "discover_services")
    builder.add_edge("discover_services", "sample_weather")
    builder.add_edge("sample_weather", "review_tourism_suitability")
    builder.add_edge("review_tourism_suitability", "enrich_deep_drive")
    builder.add_edge("enrich_deep_drive", "schedule_tourism")
    builder.add_edge("schedule_tourism", "review_daily_schedule")
    builder.add_edge("review_daily_schedule", "verify_plan")
    builder.add_conditional_edges(
        "verify_plan",
        after_verification,
        {"repair": "repair_plan", "render": "render_markdown"},
    )
    builder.add_edge("repair_plan", "verify_plan")
    builder.add_edge("render_markdown", "persist_trip")
    builder.add_edge("persist_trip", END)
    return builder.compile()


async def _ensure_coordinates(
    registry: SkillRegistry,
    place: dict[str, Any],
    trip_id: str,
    nearby: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if place.get("coordinates"):
        return place
    result = await registry.execute(
        "amap.geocode",
        {"address": place["name"], "city": place.get("city")},
        SkillContext(trip_id=trip_id),
    )
    if not result.success or not isinstance(result.data, dict):
        return place
    longitude, latitude = result.data["location"].split(",", 1)
    resolved = {
        **place,
        "address": result.data.get("formatted_address"),
        "city": result.data.get("city") or place.get("city"),
        "province": result.data.get("province"),
        "district": result.data.get("district"),
        "geocode_level": result.data.get("level"),
        "coordinates": {"longitude": float(longitude), "latitude": float(latitude)},
    }
    # A short scenic name can be ambiguous in AMap geocoding (乌镇, 南山, etc.).
    # If the first geocode is implausibly far from the departure point, use a
    # nearby POI search to select the matching local attraction instead of
    # silently planning a 1,900 km detour.
    nearby_coordinates = (nearby or {}).get("coordinates") or {}
    destination_scope = str(place.get("destination_scope") or "unknown").strip().lower()
    # Administrative requests (a province, city or broad region) must stay an
    # administrative anchor.  Never disambiguate them by searching nearby POIs:
    # a query for “新疆” around Wuhan can otherwise become a restaurant named
    # “新疆烤肉”, and a city can become a university campus.  Scenic POI
    # requests keep the older nearby-POI correction path.
    administrative_scope = destination_scope in {
        "city",
        "province",
        "region",
        "multi_destination",
    }
    if nearby_coordinates:
        distance = _haversine_km(
            RoutePoint(
                longitude=float(nearby_coordinates["longitude"]),
                latitude=float(nearby_coordinates["latitude"]),
            ),
            RoutePoint(longitude=float(longitude), latitude=float(latitude)),
        )
        if (
            distance > 250
            and len(str(place.get("name") or "")) <= 12
            and not _is_authoritative_admin_geocode(place.get("name"), result.data)
            and not administrative_scope
        ):
            poi_result = await registry.execute(
                "amap.poi",
                {
                    "keywords": place["name"],
                    "location": f"{nearby_coordinates['longitude']},{nearby_coordinates['latitude']}",
                    "radius": 50000,
                    "page_size": 10,
                },
                SkillContext(trip_id=trip_id),
            )
            items = poi_result.data.get("items", []) if poi_result.success and isinstance(poi_result.data, dict) else []
            # The geocoder already reported an implausibly distant result. At
            # this point the nearby POI search is a coordinate disambiguation
            # step; choose the closest valid point rather than guessing with
            # a name substring table (乌镇/乌镇风景区 is a common example).
            candidates = [
                item for item in items
                if item.get("name") and item.get("location")
            ]
            if candidates:
                chosen = min(
                    candidates,
                    key=lambda item: _haversine_km(
                        RoutePoint(
                            longitude=float(nearby_coordinates["longitude"]),
                            latitude=float(nearby_coordinates["latitude"]),
                        ),
                        RoutePoint(
                            longitude=float(item["location"].split(",", 1)[0]),
                            latitude=float(item["location"].split(",", 1)[1]),
                        ),
                    ),
                )
                item_longitude, item_latitude = chosen["location"].split(",", 1)
                resolved.update(
                    {
                        "name": chosen.get("name") or place["name"],
                        "address": chosen.get("address") or resolved.get("address"),
                        "city": chosen.get("city") or resolved.get("city"),
                        "province": chosen.get("province") or resolved.get("province"),
                        "district": chosen.get("district") or resolved.get("district"),
                        "coordinates": {
                            "longitude": float(item_longitude),
                            "latitude": float(item_latitude),
                        },
                    }
                )
    return resolved


def _scheduled_terminal_name(data: dict[str, Any], mode: str, side: str) -> str | None:
    field_by_mode = {
        "train": {"origin": "departure_station", "destination": "arrival_station"},
        "flight": {"origin": "departure_airport", "destination": "arrival_airport"},
        "ferry": {"origin": "departure_port", "destination": "arrival_port"},
    }
    name = data.get(field_by_mode.get(mode, {}).get(side, ""))
    text = str(name or "").strip()
    if not text or text in {"出发站", "到达站", "出发机场", "到达机场", "出发码头", "到达码头"}:
        return None
    return text


async def _attach_scheduled_terminals(
    registry: SkillRegistry,
    route: dict[str, Any],
    origin: dict[str, Any],
    destination: dict[str, Any],
    trip_id: str,
) -> dict[str, Any]:
    """Attach geocoded terminal places to a scheduled route.

    The schedule itself remains authoritative for time and service number. A
    terminal geocode is only used for map/stage endpoints; if a provider or
    geocoder omits it, the original city place is retained as an honest
    fallback instead of inventing a coordinate.
    """
    data = route.get("data") or {}
    mode = str(data.get("selected_mode") or "")
    departure_name = _scheduled_terminal_name(data, mode, "origin")
    arrival_name = _scheduled_terminal_name(data, mode, "destination")
    if not departure_name and not arrival_name:
        return route
    origin_place = origin
    destination_place = destination
    if departure_name:
        origin_place = await _ensure_coordinates(
            registry,
            {"name": departure_name, "city": origin.get("city")},
            trip_id,
        )
    if arrival_name:
        destination_place = await _ensure_coordinates(
            registry,
            {"name": arrival_name, "city": destination.get("city")},
            trip_id,
        )
    if not origin_place.get("coordinates"):
        origin_place = origin
    if not destination_place.get("coordinates"):
        destination_place = destination
    route["origin_place"] = origin_place
    route["destination_place"] = destination_place
    origin_coordinates = origin_place.get("coordinates") or {}
    destination_coordinates = destination_place.get("coordinates") or {}
    try:
        origin_point = RoutePoint(
            longitude=float(origin_coordinates["longitude"]),
            latitude=float(origin_coordinates["latitude"]),
        )
        destination_point = RoutePoint(
            longitude=float(destination_coordinates["longitude"]),
            latitude=float(destination_coordinates["latitude"]),
        )
    except (KeyError, TypeError, ValueError):
        return route
    distance_km = round(_haversine_km(origin_point, destination_point), 2)
    data["geometry"] = [
        {"longitude": origin_point.longitude, "latitude": origin_point.latitude},
        {"longitude": destination_point.longitude, "latitude": destination_point.latitude},
    ]
    data["distance_km"] = distance_km
    for step in data.get("steps", []):
        if isinstance(step, dict):
            step["distance_m"] = distance_km * 1000
    route["data"] = data
    return route


async def _route(
    registry: SkillRegistry,
    origin: dict[str, Any],
    destination: dict[str, Any],
    trip_id: str,
    preferred_mode: str = "driving",
    fallback_modes: list[str] | None = None,
) -> dict[str, Any]:
    origin_coordinates = origin.get("coordinates") or {}
    destination_coordinates = destination.get("coordinates") or {}
    if not (
        origin_coordinates.get("longitude") is not None
        and origin_coordinates.get("latitude") is not None
        and destination_coordinates.get("longitude") is not None
        and destination_coordinates.get("latitude") is not None
    ):
        # An unresolved user-required POI is kept in the candidate pool so
        # verification can report it honestly. Do not let a missing provider
        # coordinate crash the whole graph while trying to route to it.
        return {
            "success": False,
            "data": {},
            "error_code": "ROUTE_COORDINATES_MISSING",
            "warnings": ["路线端点缺少坐标，已保留为待核实地点"],
            "sources": [],
        }
    result = await registry.execute(
        "amap.route",
        {
            "origin": {**origin_coordinates, "city": origin.get("city")},
            "destination": {**destination_coordinates, "city": destination.get("city")},
            "preferred_mode": preferred_mode,
            "allowed_fallback_modes": fallback_modes or ["riding", "walking", "transit"],
        },
        SkillContext(trip_id=trip_id),
    )
    attempted = {result.data.get("selected_mode")} if result.success and isinstance(result.data, dict) else set()
    retry_modes = [
        mode
        for mode in ["transit", "driving", "riding", "walking"]
        if mode not in attempted
    ]
    while (
        result.success
        and isinstance(result.data, dict)
        and not _route_mode_feasible(result.data)
        and retry_modes
    ):
        retry_mode = retry_modes.pop(0)
        result = await registry.execute(
            "amap.route",
            {
                "origin": {**origin["coordinates"], "city": origin.get("city")},
                "destination": {
                    **destination_coordinates,
                    "city": destination.get("city"),
                },
                "preferred_mode": retry_mode,
                "allowed_fallback_modes": retry_modes,
            },
            SkillContext(trip_id=trip_id),
        )
        if result.success and isinstance(result.data, dict):
            selected_mode = result.data.get("selected_mode")
            retry_modes = [mode for mode in retry_modes if mode != selected_mode]
    # A route provider can return a successful distance/time response without a
    # polyline (this is common for ferry/short-transfer fallbacks).  The domain
    # model requires at least two map points, so synthesize a clearly estimated
    # straight connector from the already geocoded endpoints.  If even those
    # endpoints are unavailable, report a normal route failure and let callers
    # choose their existing fallback instead of crashing during RouteSegment
    # validation.
    if result.success and isinstance(result.data, dict):
        data = dict(result.data)
        geometry = data.get("geometry")
        if not isinstance(geometry, list) or len(geometry) < 2:
            try:
                origin_point = RoutePoint(
                    longitude=float(origin_coordinates["longitude"]),
                    latitude=float(origin_coordinates["latitude"]),
                )
                destination_point = RoutePoint(
                    longitude=float(destination_coordinates["longitude"]),
                    latitude=float(destination_coordinates["latitude"]),
                )
            except (KeyError, TypeError, ValueError):
                return {
                    "success": False,
                    "data": data,
                    "error_code": "ROUTE_GEOMETRY_MISSING",
                    "warnings": [*result.warnings, "路线服务未返回可绘制轨迹"],
                    "sources": [item.model_dump(mode="json") for item in result.sources],
                }
            distance_km = round(_haversine_km(origin_point, destination_point), 2)
            mode = str(data.get("selected_mode") or preferred_mode)
            speed_kmh = {"walking": 4.5, "riding": 15.0, "transit": 25.0, "driving": 45.0}.get(mode, 25.0)
            try:
                duration_minutes = int(data.get("duration_minutes") or 0)
            except (TypeError, ValueError):
                duration_minutes = 0
            data["geometry"] = [
                {"longitude": origin_point.longitude, "latitude": origin_point.latitude},
                {"longitude": destination_point.longitude, "latitude": destination_point.latitude},
            ]
            data["distance_km"] = float(data.get("distance_km") or distance_km)
            data["duration_minutes"] = duration_minutes or max(5, round(distance_km / max(speed_kmh, 1) * 60))
            data["estimated"] = True
            return {
                "success": True,
                "data": data,
                "error_code": result.error_code,
                "warnings": [*result.warnings, "路线服务未返回完整轨迹，已使用端点直连估算"],
                "sources": [item.model_dump(mode="json") for item in result.sources],
            }
    return {
        "success": result.success,
        "data": result.data,
        "error_code": result.error_code,
        "warnings": result.warnings,
        "sources": [item.model_dump(mode="json") for item in result.sources],
    }


def _parse_request_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_train_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=SHANGHAI)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI)


def _transport_city_name(place: dict[str, Any] | None) -> str:
    """Return the concrete city label used by a flight/train provider.

    Administrative destinations intentionally keep their original name and
    coordinates for map and tourism planning.  ``transport_city`` is an
    optional semantic gateway resolved by the travel-search Agent, so a
    province such as 青海 can be queried as 西宁 without changing the user's
    destination wording in the itinerary.
    """
    if not place:
        return ""
    return str(
        place.get("transport_city")
        or place.get("city")
        or place.get("name")
        or ""
    ).strip()


async def _resolve_transport_gateway(
    registry: SkillRegistry,
    place: dict[str, Any],
    trip_id: str,
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Ask the semantic travel-search Agent for a gateway city when needed.

    Structured intercity APIs require a city/station query and do not accept a
    whole province reliably.  This is deliberately a provider-backed semantic
    lookup rather than a code-side list of Chinese administrative names.  If
    the lookup is unavailable, the caller keeps the original label and the
    normal route fallback remains responsible for reporting the limitation.
    """
    scope = str(place.get("destination_scope") or "").strip().lower()
    name = str(place.get("name") or "").strip()
    city = str(place.get("city") or "").strip()
    if scope not in {"province", "region"} or not name:
        return None, [], []
    if city and _normalize_poi_name(city) != _normalize_poi_name(name):
        return None, [], []
    if "flyai.ai_search" not in set(registry.names()):
        return None, [], []
    result = await registry.execute(
        "flyai.ai_search",
        {
            "query": (
                f"{name} 适合从外地乘坐飞机或高铁到达的具体城市和主要交通枢纽。"
                "请优先返回省会或最常用落地城市，只给出城市名称，不要返回餐馆、大学或景点名称。"
            )
        },
        SkillContext(trip_id=trip_id, metadata={"purpose": "transport_gateway"}),
    )
    if not result.success or not isinstance(result.data, dict):
        return None, [], []
    content = str(result.data.get("content") or "")
    gateway: str | None = None
    # FlyAI semantic answers normally present a cited city as a Markdown link.
    # Prefer that explicit answer; the second pattern handles plain-text
    # responses from a degraded provider without hard-coding any city names.
    markdown_match = re.search(r"\*\*\[([^\]]{2,24})\]", content)
    if markdown_match:
        gateway = markdown_match.group(1).strip()
    if not gateway:
        plain_match = re.search(
            r"(?:首选|推荐|落地城市|具体城市)\s*[:：]\s*([^\s，,。；;（）()]{2,24})",
            content,
        )
        if plain_match:
            gateway = plain_match.group(1).strip()
    if not gateway or _normalize_poi_name(gateway) == _normalize_poi_name(name):
        return None, [], [item.model_dump(mode="json") for item in result.sources]
    sources = [item.model_dump(mode="json") for item in result.sources]
    return (
        gateway,
        [
            {
                "code": "TRANSPORT_GATEWAY_RESOLVED",
                "message": f"{name} 的班次查询已由语义搜索解析为交通门户城市 {gateway}",
                "severity": "info",
            }
        ],
        sources,
    )


def _intercity_pair_within_window(
    outbound: dict[str, Any],
    inbound: dict[str, Any],
    *,
    requested_departure: datetime | None,
    arrival_deadline: datetime | None,
    start_date: date,
    end_date: date,
) -> bool:
    """Accept an automatic flight/train replacement only when both legs fit."""
    outbound_data = outbound.get("data") or {}
    inbound_data = inbound.get("data") or {}
    outbound_departure = _parse_train_datetime(
        outbound_data.get("scheduled_departure_at")
    )
    outbound_arrival = _parse_train_datetime(
        outbound_data.get("scheduled_arrival_at")
    )
    inbound_departure = _parse_train_datetime(
        inbound_data.get("scheduled_departure_at")
    )
    inbound_arrival = _parse_train_datetime(
        inbound_data.get("scheduled_arrival_at")
    )
    if not all((outbound_departure, outbound_arrival, inbound_departure, inbound_arrival)):
        return False
    if outbound_departure.date() != start_date or outbound_arrival.date() > end_date:
        return False
    if inbound_departure.date() > end_date or inbound_arrival.date() > end_date:
        return False
    if requested_departure and outbound_departure < requested_departure:
        return False
    if arrival_deadline and inbound_arrival + timedelta(minutes=30) > arrival_deadline:
        return False
    return True


def _train_route_result(
    item: dict[str, Any],
    origin: dict[str, Any],
    destination: dict[str, Any],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    train_number = str(item.get("train_number") or "").strip()
    if not train_number:
        return {"success": False, "error_code": "TRAIN_SERVICE_NUMBER_MISSING", "warnings": [], "sources": sources}
    departure_at = _parse_train_datetime(item.get("departure_at"))
    arrival_at = _parse_train_datetime(item.get("arrival_at"))
    if not departure_at or not arrival_at:
        return {"success": False, "error_code": "TRAIN_INVALID_SCHEDULE", "warnings": [], "sources": []}
    origin_coordinates = origin.get("coordinates") or {}
    destination_coordinates = destination.get("coordinates") or {}
    try:
        origin_point = RoutePoint(
            longitude=float(origin_coordinates["longitude"]),
            latitude=float(origin_coordinates["latitude"]),
        )
        destination_point = RoutePoint(
            longitude=float(destination_coordinates["longitude"]),
            latitude=float(destination_coordinates["latitude"]),
        )
        distance_km = round(_haversine_km(origin_point, destination_point), 2)
        geometry = [
            {"longitude": origin_point.longitude, "latitude": origin_point.latitude},
            {"longitude": destination_point.longitude, "latitude": destination_point.latitude},
        ]
    except (KeyError, TypeError, ValueError):
        return {"success": False, "error_code": "TRAIN_COORDINATES_INVALID", "warnings": [], "sources": []}
    train_minutes = max(1, round((arrival_at - departure_at).total_seconds() / 60))
    try:
        listed_minutes = int(item.get("duration_minutes") or train_minutes)
    except (TypeError, ValueError):
        listed_minutes = train_minutes
    # Reserve time for getting to the station, boarding, and the final
    # station-to-destination transfer.  This keeps the schedule honest and
    # gives the verifier a usable arrival deadline instead of a raw train
    # timetable that ignores station access.
    duration_minutes = max(train_minutes, listed_minutes) + 75
    departure_station = item.get("departure_station") or "出发站"
    arrival_station = item.get("arrival_station") or "到达站"
    display_number = train_number
    price = item.get("price")
    return {
        "success": True,
        "data": {
            "selected_mode": "train",
            "distance_km": distance_km,
            "duration_minutes": duration_minutes,
            "tolls_cny": 0,
            "geometry": geometry,
            "steps": [
                {
                    "road": f"{departure_station} → {arrival_station}",
                    "distance_m": distance_km * 1000,
                    "duration_s": duration_minutes * 60,
                }
            ],
            "traffic_summary": (
                f"{display_number} {departure_station} {departure_at:%H:%M} → "
                f"{arrival_station} {arrival_at:%H:%M}，已预留车站接驳时间"
            ),
            "elevation_gain_m": None,
            "estimated": True,
            "scheduled_departure_at": departure_at.isoformat(),
            "scheduled_arrival_at": arrival_at.isoformat(),
            "train_number": train_number,
            "service_number": train_number,
            "service_operator": item.get("operator") or item.get("transport_name"),
            "service_status": item.get("service_status") or ("confirmed" if train_number else "unavailable"),
            "departure_station": departure_station,
            "arrival_station": arrival_station,
            "seat_class": item.get("seat_class"),
            "price": price,
            "detail_url": item.get("detail_url"),
        },
        "warnings": [],
        "sources": sources,
    }


async def _train_route(
    registry: SkillRegistry,
    origin: dict[str, Any],
    destination: dict[str, Any],
    trip_id: str,
    *,
    travel_date: date,
    requested_departure: datetime | None = None,
    arrival_deadline: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Select a real FlyAI train option for an intercity leg.

    The requirement Agent decides whether trains are allowed/preferred.  This
    helper only consumes that semantic decision; it does not inspect raw
    text or maintain a keyword list.
    """
    if requested_departure and requested_departure.tzinfo is None:
        requested_departure = requested_departure.replace(tzinfo=SHANGHAI)
    if arrival_deadline and arrival_deadline.tzinfo is None:
        arrival_deadline = arrival_deadline.replace(tzinfo=SHANGHAI)
    warnings: list[dict[str, Any]] = []
    payload = {
        "origin": _transport_city_name(origin),
        "destination": _transport_city_name(destination),
        "dep_date": travel_date.isoformat(),
        "sort_type": 4,
    }
    results = await asyncio.gather(
        *(
            registry.execute(adapter, payload, SkillContext(trip_id=trip_id))
            for adapter in ("flyai.train", "mcp12306.train", "freeapi.train")
        )
    )
    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    provider_warnings: list[str] = []
    seen_services: set[tuple[str, str, str]] = set()
    for result in results:
        provider_warnings.extend(result.warnings)
        sources.extend(item.model_dump(mode="json") for item in result.sources)
        rows = result.data.get("items") if result.success and isinstance(result.data, dict) else None
        if not isinstance(rows, list):
            continue
        for item in rows:
            if (
                not isinstance(item, dict)
                or not str(item.get("train_number") or "").strip()
                or not str(item.get("departure_station") or "").strip()
                or not str(item.get("arrival_station") or "").strip()
            ):
                continue
            service_key = (
                str(item.get("train_number")).strip().casefold(),
                str(item.get("departure_at") or ""),
                str(item.get("arrival_at") or ""),
            )
            if service_key not in seen_services:
                seen_services.add(service_key)
                items.append(item)
    if items:
        # Missing credentials on an optional source are recorded by the skill
        # audit, but must not blemish a route confirmed by another provider.
        provider_warnings = []
    if not items:
        return (
            {
                "success": False,
                "error_code": "TRAIN_SEARCH_FAILED",
                "warnings": provider_warnings,
                "sources": sources,
            },
            warnings,
        )
    parsed: list[tuple[dict[str, Any], datetime, datetime]] = []
    for item in items:
        departure_at = _parse_train_datetime(item.get("departure_at"))
        arrival_at = _parse_train_datetime(item.get("arrival_at"))
        if departure_at and arrival_at and arrival_at > departure_at:
            parsed.append((item, departure_at, arrival_at))
    if not parsed:
        return (
            {
                "success": False,
                "error_code": "TRAIN_NO_VALID_SCHEDULE",
                "warnings": provider_warnings,
                "sources": sources,
            },
            warnings,
        )

    # Prefer options satisfying the user's time window.  A small station
    # buffer is accounted for when testing the arrival deadline.
    candidates = parsed
    if requested_departure:
        on_or_after = [item for item in parsed if item[1] >= requested_departure]
        if on_or_after:
            candidates = on_or_after
        else:
            # If the last train has already left, take the latest available
            # train rather than the first morning service.  This preserves a
            # user's "晚上出发" intent and avoids an obviously wrong timetable.
            candidates = [max(parsed, key=lambda item: item[1])]
            warnings.append(
                {
                    "code": "TRAIN_DEPARTURE_WINDOW_RELAXED",
                    "message": "当天没有晚于期望出发时间的直达车次，已选择最接近班次",
                    "severity": "warning",
                }
            )
    if arrival_deadline:
        before_deadline = [item for item in candidates if item[2] + timedelta(minutes=30) <= arrival_deadline]
        if before_deadline:
            candidates = before_deadline
        else:
            warnings.append(
                {
                    "code": "TRAIN_ARRIVAL_WINDOW_RELAXED",
                    "message": "当天车次无法在截止时间前完成站点接驳，已选择最早抵达班次",
                    "severity": "warning",
                }
            )
    candidates = _prefer_daylight_arrivals(
        candidates,
        mode="train",
        warnings=warnings,
    )

    if arrival_deadline:
        selected = min(
            candidates,
            key=lambda item: (
                0 if item[2] + timedelta(minutes=30) <= arrival_deadline else 1,
                abs((arrival_deadline - item[2]).total_seconds()),
            ),
        )
    elif requested_departure:
        selected = min(candidates, key=lambda item: item[1])
    else:
        selected = min(candidates, key=lambda item: item[2] - item[1])
    route = _train_route_result(
        selected[0],
        origin,
        destination,
        sources,
    )
    if route.get("success"):
        route = await _attach_scheduled_terminals(
            registry,
            route,
            origin,
            destination,
            trip_id,
        )
    route["warnings"] = [*provider_warnings, *warnings]
    return route, warnings


def _scheduled_route_result(
    item: dict[str, Any],
    origin: dict[str, Any],
    destination: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any]:
    """Turn a FlyAI train/flight/ferry result into a route-shaped record.

    Intercity providers do not return an AMap road polyline.  The map therefore
    receives a geodesic connector between the two terminals, while the stage
    retains the real schedule, terminal names and booking source.  A connector
    is deliberately marked estimated so the UI never presents it as a road.
    """
    if mode == "flight" and not str(item.get("flight_number") or "").strip():
        return {"success": False, "error_code": "FLIGHT_SERVICE_NUMBER_MISSING", "warnings": [], "sources": sources}
    if mode == "train" and not str(item.get("train_number") or "").strip():
        return {"success": False, "error_code": "TRAIN_SERVICE_NUMBER_MISSING", "warnings": [], "sources": sources}
    departure_at = _parse_train_datetime(item.get("departure_at"))
    arrival_at = _parse_train_datetime(item.get("arrival_at"))
    if not departure_at or not arrival_at or arrival_at <= departure_at:
        return {
            "success": False,
            "error_code": f"{mode.upper()}_INVALID_SCHEDULE",
            "warnings": [],
            "sources": [],
        }
    origin_coordinates = origin.get("coordinates") or {}
    destination_coordinates = destination.get("coordinates") or {}
    try:
        origin_point = RoutePoint(
            longitude=float(origin_coordinates["longitude"]),
            latitude=float(origin_coordinates["latitude"]),
        )
        destination_point = RoutePoint(
            longitude=float(destination_coordinates["longitude"]),
            latitude=float(destination_coordinates["latitude"]),
        )
        distance_km = round(_haversine_km(origin_point, destination_point), 2)
        geometry = [
            {"longitude": origin_point.longitude, "latitude": origin_point.latitude},
            {"longitude": destination_point.longitude, "latitude": destination_point.latitude},
        ]
    except (KeyError, TypeError, ValueError):
        return {
            "success": False,
            "error_code": f"{mode.upper()}_COORDINATES_INVALID",
            "warnings": [],
            "sources": [],
        }
    elapsed_minutes = max(1, round((arrival_at - departure_at).total_seconds() / 60))
    try:
        listed_minutes = int(item.get("duration_minutes") or elapsed_minutes)
    except (TypeError, ValueError):
        listed_minutes = elapsed_minutes
    buffer_minutes = {"flight": 150, "ferry": 45}.get(mode, 75)
    duration_minutes = max(elapsed_minutes, listed_minutes) + buffer_minutes
    if mode == "flight":
        identifier = item.get("flight_number")
        departure_terminal = item.get("departure_airport") or "出发机场"
        arrival_terminal = item.get("arrival_airport") or "到达机场"
        detail_label = "已预留值机、安检与机场接驳时间"
    elif mode == "ferry":
        identifier = item.get("ship_name") or "船名未返回"
        departure_terminal = item.get("departure_port") or "出发码头"
        arrival_terminal = item.get("arrival_port") or "到达码头"
        detail_label = "轮船班次为语义检索结果，已预留码头接驳时间"
    else:
        identifier = item.get("train_number")
        departure_terminal = item.get("departure_station") or "出发站"
        arrival_terminal = item.get("arrival_station") or "到达站"
        detail_label = "已预留车站接驳时间"
    return {
        "success": True,
        "data": {
            "selected_mode": mode,
            "distance_km": distance_km,
            "duration_minutes": duration_minutes,
            "tolls_cny": 0,
            "geometry": geometry,
            "steps": [
                {
                    "road": f"{departure_terminal} → {arrival_terminal}",
                    "distance_m": distance_km * 1000,
                    "duration_s": duration_minutes * 60,
                }
            ],
            "traffic_summary": (
                f"{identifier} {departure_terminal} {departure_at:%H:%M} → "
                f"{arrival_terminal} {arrival_at:%H:%M}，{detail_label}"
            ),
            "elevation_gain_m": None,
            "estimated": bool(item.get("estimated", False)) or mode == "ferry",
            "scheduled_departure_at": departure_at.isoformat(),
            "scheduled_arrival_at": arrival_at.isoformat(),
            "flight_number": item.get("flight_number"),
            "train_number": item.get("train_number"),
            "service_number": item.get("flight_number") or item.get("train_number"),
            "service_operator": item.get("carrier") or item.get("operator"),
            "service_status": (
                "estimated" if mode == "ferry" and not (item.get("flight_number") or item.get("train_number"))
                else "confirmed" if (item.get("flight_number") or item.get("train_number"))
                else "unavailable"
            ),
            "ship_name": item.get("ship_name"),
            "departure_station": item.get("departure_station") or departure_terminal,
            "arrival_station": item.get("arrival_station") or arrival_terminal,
            "departure_airport": item.get("departure_airport") or departure_terminal,
            "arrival_airport": item.get("arrival_airport") or arrival_terminal,
            "departure_port": item.get("departure_port") or departure_terminal,
            "arrival_port": item.get("arrival_port") or arrival_terminal,
            "seat_class": item.get("seat_class"),
            "price": item.get("price"),
            "detail_url": item.get("detail_url"),
        },
        "warnings": [],
        "sources": sources,
    }


async def _scheduled_route(
    registry: SkillRegistry,
    origin: dict[str, Any],
    destination: dict[str, Any],
    trip_id: str,
    *,
    mode: str,
    travel_date: date,
    requested_departure: datetime | None = None,
    arrival_deadline: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Select a schedule returned by a FlyAI intercity adapter."""
    if requested_departure and requested_departure.tzinfo is None:
        requested_departure = requested_departure.replace(tzinfo=SHANGHAI)
    if arrival_deadline and arrival_deadline.tzinfo is None:
        arrival_deadline = arrival_deadline.replace(tzinfo=SHANGHAI)
    payload: dict[str, Any] = {
        "origin": _transport_city_name(origin),
        "destination": _transport_city_name(destination),
        "dep_date": travel_date.isoformat(),
    }
    warnings: list[dict[str, Any]] = []
    mode_upper = mode.upper()
    adapter_names = (
        ("flyai.flight", "sixapi.flight", "aviationstack.flight")
        if mode == "flight"
        else (f"flyai.{mode}",)
    )
    results = await asyncio.gather(
        *(
            registry.execute(adapter, payload, SkillContext(trip_id=trip_id))
            for adapter in adapter_names
        )
    )
    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    provider_warnings: list[str] = []
    seen_services: set[tuple[str, str, str]] = set()
    for result in results:
        provider_warnings.extend(result.warnings)
        sources.extend(item.model_dump(mode="json") for item in result.sources)
        rows = result.data.get("items") if result.success and isinstance(result.data, dict) else None
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            raw_identifier = (
                item.get("flight_number")
                if mode == "flight"
                else item.get("ship_name")
                if mode == "ferry"
                else item.get("train_number")
            )
            identifier = str(raw_identifier or "").strip()
            if mode in {"flight", "train"} and not identifier:
                continue
            if mode == "flight" and (
                not str(item.get("departure_airport") or "").strip()
                or not str(item.get("arrival_airport") or "").strip()
            ):
                continue
            service_key = (
                identifier.casefold(),
                str(item.get("departure_at") or ""),
                str(item.get("arrival_at") or ""),
            )
            if service_key not in seen_services:
                seen_services.add(service_key)
                items.append(item)
    if items:
        provider_warnings = []
    if not items:
        return (
            {
                "success": False,
                "error_code": f"{mode_upper}_SEARCH_FAILED",
                "warnings": provider_warnings,
                "sources": sources,
            },
            warnings,
        )
    parsed: list[tuple[dict[str, Any], datetime, datetime]] = []
    for item in items:
        departure_at = _parse_train_datetime(item.get("departure_at"))
        arrival_at = _parse_train_datetime(item.get("arrival_at"))
        if departure_at and arrival_at and arrival_at > departure_at:
            parsed.append((item, departure_at, arrival_at))
    if not parsed:
        return (
            {
                "success": False,
                "error_code": f"{mode_upper}_NO_VALID_SCHEDULE",
                "warnings": provider_warnings,
                "sources": sources,
            },
            warnings,
        )
    candidates = parsed
    if requested_departure:
        on_or_after = [item for item in parsed if item[1] >= requested_departure]
        if on_or_after:
            candidates = on_or_after
        else:
            candidates = [max(parsed, key=lambda item: item[1])]
            warnings.append(
                {
                    "code": f"{mode_upper}_DEPARTURE_WINDOW_RELAXED",
                    "message": f"当天没有满足期望时间的{_intercity_mode_label(mode)}，已选择最接近班次",
                    "severity": "warning",
                }
            )
    if arrival_deadline:
        before_deadline = [
            item for item in candidates
            if item[2] + timedelta(minutes=30) <= arrival_deadline
        ]
        if before_deadline:
            candidates = before_deadline
        else:
            warnings.append(
                {
                    "code": f"{mode_upper}_ARRIVAL_WINDOW_RELAXED",
                    "message": f"当天{_intercity_mode_label(mode)}无法在截止时间前完成接驳，已选择最接近班次",
                    "severity": "warning",
                }
            )
    candidates = _prefer_daylight_arrivals(
        candidates,
        mode=mode,
        warnings=warnings,
    )
    if arrival_deadline:
        selected = min(
            candidates,
            key=lambda item: (
                0 if item[2] + timedelta(minutes=30) <= arrival_deadline else 1,
                abs((arrival_deadline - item[2]).total_seconds()),
            ),
        )
    elif requested_departure:
        selected = min(candidates, key=lambda item: item[1])
    else:
        selected = min(candidates, key=lambda item: item[2] - item[1])
    route = _scheduled_route_result(
        selected[0],
        origin,
        destination,
        sources,
        mode=mode,
    )
    if route.get("success"):
        route = await _attach_scheduled_terminals(
            registry,
            route,
            origin,
            destination,
            trip_id,
        )
    route["warnings"] = [*provider_warnings, *warnings]
    return route, warnings


def _intercity_mode_label(mode: str) -> str:
    return {"train": "火车", "flight": "航班", "ferry": "轮船"}.get(mode, "交通班次")


def _transport_mode_label(mode: str) -> str:
    return {
        "driving": "驾车",
        "train": "火车",
        "flight": "航班",
        "ferry": "轮船",
        "transit": "公共交通",
        "walking": "步行",
        "riding": "骑行",
    }.get(mode, _intercity_mode_label(mode))


def _intercity_departure_buffer(mode: Any) -> int:
    """Minutes needed before a scheduled intercity departure."""
    return {"flight": 150, "train": 45, "ferry": 45}.get(str(mode or ""), 45)


def _intercity_route_quality(
    route: dict[str, Any],
    *,
    requested_departure: datetime | None,
    arrival_deadline: datetime | None,
    mode_order: int,
) -> tuple[int, int, float, int, int]:
    """Rank allowed intercity schedules by safety, comfort and feasibility."""
    data = route.get("data") or {}
    departure = _parse_train_datetime(data.get("scheduled_departure_at"))
    arrival = _parse_train_datetime(data.get("scheduled_arrival_at"))
    if not departure or not arrival:
        return (9, 9, 999999.0, 999999, mode_order)
    arrival_clock = arrival.timetz().replace(tzinfo=None)
    # 07:00–23:00 is the comfortable arrival window.  A quiet-hour arrival
    # remains usable as a last resort, but it can never beat a daytime option.
    quiet_penalty = 0 if time(7, 0) <= arrival_clock < time(23, 0) else 1
    deadline_penalty = 0
    deadline_distance = 0.0
    if arrival_deadline:
        deadline_penalty = 0 if arrival + timedelta(minutes=30) <= arrival_deadline else 1
        deadline_distance = abs((arrival_deadline - arrival).total_seconds())
    departure_wait = (
        max(0.0, (departure - requested_departure).total_seconds())
        if requested_departure
        else 0.0
    )
    duration = int(data.get("duration_minutes") or 0)
    return (
        quiet_penalty,
        deadline_penalty,
        deadline_distance if arrival_deadline else departure_wait,
        duration,
        mode_order,
    )


def _prefer_daylight_arrivals(
    candidates: list[tuple[dict[str, Any], datetime, datetime]],
    *,
    mode: str,
    warnings: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], datetime, datetime]]:
    daylight = [
        item
        for item in candidates
        if time(7, 0) <= item[2].timetz().replace(tzinfo=None) < time(23, 0)
    ]
    if daylight and len(daylight) < len(candidates):
        warnings.append(
            {
                "code": f"{mode.upper()}_QUIET_HOUR_AVOIDED",
                "message": f"已避开{_intercity_mode_label(mode)}凌晨抵达班次，优先选择白天到达",
                "severity": "info",
            }
        )
        return daylight
    return candidates


async def _flight_route(
    registry: SkillRegistry,
    origin: dict[str, Any],
    destination: dict[str, Any],
    trip_id: str,
    *,
    travel_date: date,
    requested_departure: datetime | None = None,
    arrival_deadline: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return await _scheduled_route(
        registry,
        origin,
        destination,
        trip_id,
        mode="flight",
        travel_date=travel_date,
        requested_departure=requested_departure,
        arrival_deadline=arrival_deadline,
    )


async def _ferry_route(
    registry: SkillRegistry,
    origin: dict[str, Any],
    destination: dict[str, Any],
    trip_id: str,
    *,
    travel_date: date,
    requested_departure: datetime | None = None,
    arrival_deadline: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return await _scheduled_route(
        registry,
        origin,
        destination,
        trip_id,
        mode="ferry",
        travel_date=travel_date,
        requested_departure=requested_departure,
        arrival_deadline=arrival_deadline,
    )


def _route_mode_feasible(data: dict[str, Any]) -> bool:
    mode = data.get("selected_mode")
    duration = int(data.get("duration_minutes") or 0)
    distance = float(data.get("distance_km") or 0)
    if mode == "walking":
        return duration <= 45 and distance <= 3.5
    if mode == "riding":
        return duration <= 75 and distance <= 15
    if mode == "transit":
        return duration <= 120
    return True


def _local_route_reasonable(data: dict[str, Any]) -> bool:
    """Keep sightseeing transfers bounded while allowing city-wide highlights."""
    mode = data.get("selected_mode")
    duration = int(data.get("duration_minutes") or 0)
    distance = float(data.get("distance_km") or 0)
    if mode == "walking":
        return 0.05 <= distance <= 4 and 1 <= duration <= 60
    if mode == "riding":
        return 0.05 <= distance <= 18 and 1 <= duration <= 90
    return 0.05 <= distance <= 35 and 1 <= duration <= 120


def _safe_fallback_local_mode(
    origin: dict[str, Any],
    destination: dict[str, Any],
    requested_mode: str,
) -> str:
    """Downgrade an overlong walking/riding fallback to public transit."""
    try:
        first = origin.get("coordinates") or {}
        second = destination.get("coordinates") or {}
        distance = _haversine_km(
            RoutePoint(longitude=float(first["longitude"]), latitude=float(first["latitude"])),
            RoutePoint(longitude=float(second["longitude"]), latitude=float(second["latitude"])),
        )
    except (KeyError, TypeError, ValueError):
        distance = 0.0
    if requested_mode == "walking" and (distance > 4 or distance / 4.5 * 60 > 60):
        return "riding" if distance <= 18 else "transit"
    if requested_mode == "riding" and (distance > 18 or distance / 15 * 60 > 90):
        return "transit"
    return requested_mode


def _select_itinerary_places(
    candidates: list[dict[str, Any]],
    anchor: dict[str, Any],
    limit: int,
    *,
    return_candidates: bool = False,
) -> list[dict[str, Any]]:
    """Greedily balance Agent score with transfer distance to avoid scattered POIs."""
    candidates = deduplicate_attraction_candidates(candidates)
    remaining: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_coordinates: list[tuple[float, float]] = []
    for candidate in candidates:
        if (
            candidate.get("seasonal_excluded") or candidate.get("agent_suitability") is False
        ) and not (candidate.get("user_required") or candidate.get("user_confirmed")):
            # Provider/model review keeps rejected records in the backup pool,
            # but route construction must never turn them into movement legs.
            continue
        place = candidate.get("place") or {}
        normalized_name = _normalize_poi_name(place.get("name"))
        coordinates = place.get("coordinates") or {}
        try:
            point = (float(coordinates["longitude"]), float(coordinates["latitude"]))
        except (KeyError, TypeError, ValueError):
            point = None
        duplicate_coordinate = bool(
            point and any(abs(point[0] - existing[0]) < 0.001 and abs(point[1] - existing[1]) < 0.001 for existing in seen_coordinates)
        )
        if not normalized_name or normalized_name in seen_names or duplicate_coordinate:
            continue
        seen_names.add(normalized_name)
        if point:
            seen_coordinates.append(point)
        remaining.append(candidate)
    # Explicit user additions and named must-visit places are hard
    # requirements, not ordinary ranked suggestions. Reserve capacity for
    # them first; a provider's score/radius must never silently crowd out a
    # place the user explicitly asked to see.
    required = [
        item
        for item in remaining
        if item.get("user_confirmed") or item.get("user_required")
    ]
    limit = max(limit, len(required))
    selected: list[dict[str, Any]] = required[:limit]
    remaining = [item for item in remaining if item not in selected]
    current = anchor
    if selected:
        current = selected[-1].get("place") or anchor
    while remaining and len(selected) < limit:
        current_coordinates = current.get("coordinates") or {}

        def utility(item: dict[str, Any]) -> float:
            place = item.get("place") or {}
            try:
                distance = _haversine_km(
                    RoutePoint(
                        longitude=float(current_coordinates["longitude"]),
                        latitude=float(current_coordinates["latitude"]),
                    ),
                    RoutePoint(
                        longitude=float(place["coordinates"]["longitude"]),
                        latitude=float(place["coordinates"]["latitude"]),
                    ),
                )
            except (KeyError, TypeError, ValueError):
                distance = 25.0
            try:
                score = float(item.get("score") or 0)
            except (TypeError, ValueError):
                score = 0.0
            try:
                research_priority = float(item.get("destination_research_priority") or 0)
            except (TypeError, ValueError):
                research_priority = 0.0
            # Research-backed city highlights should be reachable even when
            # another generic POI is closer to the hotel.  Distance still
            # shapes the order inside the same regional cluster.
            return score + research_priority * 1.5 - distance * 1.5

        chosen = max(remaining, key=utility)
        selected.append(chosen if return_candidates else chosen["place"])
        current = chosen["place"]
        remaining.remove(chosen)
    return selected


def _movement_stage(
    *,
    day_id: str,
    sequence: int,
    title: str,
    origin: dict[str, Any],
    destination: dict[str, Any],
    route: dict[str, Any],
    start_at: datetime,
) -> MovementStage:
    data = route["data"]
    duration = int(data.get("duration_minutes") or 0)
    road_names = list(
        dict.fromkeys(
            step.get("road")
            for step in data.get("steps", [])
            if step.get("road")
        )
    )
    route_segments: list[RouteSegment] = []
    geometry = data.get("geometry") if isinstance(data.get("geometry"), list) else []
    if len(geometry) >= 2:
        try:
            route_segments.append(
                RouteSegment(
                    coordinates=[Coordinates.model_validate(point) for point in geometry],
                    distance_km=float(data.get("distance_km") or 0),
                    duration_minutes=duration,
                    road_name=" / ".join(road_names[:3]) or None,
                    toll=bool(data.get("tolls_cny")),
                    estimated=bool(data.get("estimated", False)),
                    elevation_gain_m=data.get("elevation_gain_m"),
                )
            )
        except (TypeError, ValueError):
            route_segments = []
    route_warnings: list[PlanWarning] = []
    if not route_segments:
        route_warnings.append(
            PlanWarning(
                code="ROUTE_GEOMETRY_UNAVAILABLE",
                message="路线服务未返回可绘制轨迹，已保留时间与端点信息",
                severity="warning",
                estimated=True,
            )
        )
    traffic_summary = data.get("traffic_summary")
    if data.get("selected_mode") == "driving" and traffic_summary:
        if start_at.date() != _local_today():
            traffic_summary = f"当前路况：{traffic_summary}"
    elif data.get("selected_mode") != "driving":
        mode = data.get("selected_mode")
        if mode in {"walking", "riding"}:
            gain = data.get("elevation_gain_m")
            traffic_summary = (
                f"路线起伏：总爬升约 {gain:g} m"
                if gain is not None
                else "路线起伏：高程数据暂不可用"
            )
        else:
            traffic_summary = {
                "transit": "公共交通按高德当前班次规划",
                "train": traffic_summary or "已按 FlyAI 实时高铁车次规划，并预留车站接驳时间",
                "flight": traffic_summary or "已按 FlyAI 实时航班规划，并预留机场接驳时间",
                "ferry": traffic_summary or "已按 FlyAI 轮船候选规划，并预留码头接驳时间",
            }.get(mode, "按高德路线规划")
    transit_legs = [item for item in data.get("transit_legs", []) if isinstance(item, dict)]
    service_number = data.get("service_number") or data.get("train_number") or data.get("flight_number")
    service_status = data.get("service_status")
    if service_status not in {"confirmed", "estimated", "unavailable"}:
        service_status = "confirmed" if service_number else ("estimated" if data.get("estimated") else "unavailable")
    service_price = None
    raw_price = data.get("price")
    if raw_price not in (None, ""):
        # Providers return both numeric values and display strings such as
        # "¥ 540" or "540-680 元". Keep the numeric part when available and
        # leave the field null when the provider did not return a price.
        price_values = [
            float(value.replace(",", ""))
            for value in re.findall(r"\d+(?:\.\d+)?", str(raw_price))
        ]
        if price_values:
            service_price = MoneyRange(
                minimum=min(price_values),
                maximum=max(price_values),
                estimated=True,
                note=str(raw_price),
            )
    return MovementStage(
        day_id=day_id,
        sequence=sequence,
        title=title,
        mode=data.get("selected_mode", "driving"),
        transit_type=(
            "ferry" if data.get("selected_mode") == "ferry"
            else (
                "subway" if transit_legs and transit_legs[0].get("mode") == "subway" else "bus"
            ) if data.get("selected_mode") == "transit"
            else None
        ),
        transit_legs=transit_legs,
        origin=PlaceRef.model_validate(origin),
        destination=PlaceRef.model_validate(destination),
        route_segments=route_segments,
        planned_start=start_at,
        planned_end=start_at + timedelta(minutes=duration),
        distance_km=float(data.get("distance_km") or 0),
        duration_minutes=duration,
        elevation_gain_m=data.get("elevation_gain_m"),
        traffic_summary=traffic_summary,
        service_number=service_number,
        service_operator=data.get("service_operator") or data.get("carrier") or data.get("operator"),
        departure_terminal=data.get("departure_terminal") or data.get("departure_airport") or data.get("departure_station"),
        arrival_terminal=data.get("arrival_terminal") or data.get("arrival_airport") or data.get("arrival_station"),
        service_detail_url=data.get("detail_url"),
        service_departure_at=_parse_train_datetime(data.get("scheduled_departure_at")),
        service_arrival_at=_parse_train_datetime(data.get("scheduled_arrival_at")),
        service_seat_class=data.get("seat_class"),
        service_price=service_price,
        service_status=service_status,
        transit_fare_cny=data.get("fare_cny"),
        toll_fee={
            "minimum": data.get("tolls_cny", 0),
            "maximum": data.get("tolls_cny", 0),
            "estimated": False,
        },
        source_records=[
            SourceRecord.model_validate(item)
            for item in route.get("sources", [])
        ],
        warnings=route_warnings,
    )


def _request_clock(day: date, value: Any, *, default: time) -> datetime:
    if isinstance(value, time):
        selected = value
    else:
        selected = default
        if isinstance(value, str):
            try:
                selected = time.fromisoformat(value)
            except ValueError:
                selected = default
    return datetime.combine(day, selected, tzinfo=SHANGHAI)


def _estimated_driving_arrival_date(
    start_at: datetime,
    duration_minutes: int,
    max_daily_drive_minutes: int,
) -> date:
    """Return the calendar day on which a long drive is expected to arrive.

    This mirrors the overnight splitter in ``deep_drive``: driving is kept
    inside the daytime window (08:00--20:00) and a daily driving budget is
    respected.  The estimate is intentionally conservative and is used only
    to decide which locally generated sightseeing legs must be deferred until
    the outbound leg has actually arrived.
    """
    remaining = max(0, int(duration_minutes or 0))
    daily_budget = max(60, int(max_daily_drive_minutes or 9 * 60))
    cursor = start_at
    while remaining > 0:
        day_end = cursor.replace(hour=20, minute=0, second=0, microsecond=0)
        if cursor >= day_end:
            cursor = (cursor + timedelta(days=1)).replace(
                hour=8, minute=0, second=0, microsecond=0
            )
            continue
        available_window = max(
            0, int((day_end - cursor).total_seconds() // 60)
        )
        available = min(remaining, daily_budget, available_window)
        if available <= 0:
            cursor = (cursor + timedelta(days=1)).replace(
                hour=8, minute=0, second=0, microsecond=0
            )
            continue
        remaining -= available
        cursor += timedelta(minutes=available)
        if remaining > 0:
            cursor = (cursor + timedelta(days=1)).replace(
                hour=8, minute=0, second=0, microsecond=0
            )
    return cursor.date()


def _return_stage_start(
    day: date,
    return_time: Any,
    earliest_start: datetime,
    route: dict[str, Any],
) -> datetime:
    """Schedule a return leg so an explicit return time is an arrival deadline.

    Requirement extraction treats phrases such as “周日晚八点前回来” as the
    time by which the traveller must arrive back at the origin.  The previous
    implementation used that clock value as the *departure* time, which made a
    long return leg necessarily finish after the user's deadline.  For an
    explicit deadline, leave by ``deadline - route duration`` while respecting
    the end of the day's local activities.  If the day's activities already
    consume that buffer, the returned start remains safe and the normal plan
    verification can surface the late-arrival constraint instead of silently
    moving departure to the deadline.
    """
    deadline = _request_clock(day, return_time, default=time(14, 30))
    if return_time is None:
        return max(earliest_start, deadline)
    scheduled_departure = _parse_train_datetime(
        (route.get("data") or {}).get("scheduled_departure_at")
    )
    if scheduled_departure:
        # The provider timetable is authoritative.  Flights need a much
        # larger airport check-in/security buffer than a train or ferry.
        return max(
            earliest_start,
            scheduled_departure
            - timedelta(
                minutes=_intercity_departure_buffer(
                    (route.get("data") or {}).get("selected_mode")
                )
            ),
        )
    try:
        duration_minutes = int((route.get("data") or {}).get("duration_minutes") or 0)
    except (TypeError, ValueError):
        duration_minutes = 0
    latest_start = deadline - timedelta(minutes=max(0, duration_minutes))
    scheduled_start = max(earliest_start, latest_start)
    # A road return should never be scheduled in the middle of the night just
    # because an explicit arrival clock is earlier than the driving duration.
    # Keep the leg in a daylight window and let the normal half-day return
    # grace surface any resulting delay as a warning.  Long cross-day drives
    # are handled by ``enrich_deep_drive_plan`` and do not use this helper.
    if str((route.get("data") or {}).get("selected_mode") or "").casefold() == "driving":
        scheduled_start = max(
            scheduled_start,
            _request_clock(day, "07:00", default=time(7, 0)),
        )
    return scheduled_start


def _fallback_local_route(
    origin: dict[str, Any],
    destination: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Keep a local itinerary connected when a provider has no short-route result."""
    origin_coordinates = origin.get("coordinates") or {}
    destination_coordinates = destination.get("coordinates") or {}
    try:
        origin_point = RoutePoint(
            longitude=float(origin_coordinates["longitude"]),
            latitude=float(origin_coordinates["latitude"]),
        )
        destination_point = RoutePoint(
            longitude=float(destination_coordinates["longitude"]),
            latitude=float(destination_coordinates["latitude"]),
        )
        distance_km = round(_haversine_km(origin_point, destination_point), 2)
    except (KeyError, TypeError, ValueError):
        distance_km = 0.1
    speed_kmh = {"walking": 4.5, "riding": 15.0, "transit": 25.0}.get(mode, 25.0)
    duration_minutes = max(5, round(distance_km / speed_kmh * 60))
    geometry = [
        {"longitude": origin_point.longitude, "latitude": origin_point.latitude},
        {"longitude": destination_point.longitude, "latitude": destination_point.latitude},
    ] if "origin_point" in locals() and "destination_point" in locals() else []
    return {
        "success": True,
        "data": {
            "selected_mode": mode,
            "distance_km": distance_km,
            "duration_minutes": duration_minutes,
            "tolls_cny": 0,
            "geometry": geometry,
            "steps": [],
            "traffic_summary": None,
            "estimated": True,
        },
        "sources": [],
        "warnings": ["高德未返回完整接驳路线，已使用估算直连，仅用于保持行程闭环"],
    }


def _local_stage_title(mode: str | None, *, return_to_base: bool = False) -> str:
    if return_to_base:
        return "返回住宿或目的地核心区"
    return {
        "transit": "公共交通前往景点",
        "walking": "步行游览接驳",
        "riding": "骑行游览接驳",
        "driving": "目的地短途接驳",
    }.get(mode, "目的地接驳")


def _verify_route_closure(day_plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stages = [
        stage
        for day in sorted(day_plans, key=lambda item: item.get("day_index", 0))
        for stage in sorted(day.get("stages", []), key=lambda item: item.get("sequence", 0))
    ]
    if not stages:
        return []

    issues: list[dict[str, Any]] = []
    for previous, current in zip(stages, stages[1:]):
        if not _same_place(previous.get("destination"), current.get("origin")):
            issues.append(
                {
                    "code": "ROUTE_DISCONTINUITY",
                    "severity": "blocker",
                    "description": (
                        f"阶段“{previous.get('title', '')}”终点与"
                        f"“{current.get('title', '')}”起点不连续"
                    ),
                }
            )
    if not _same_place(stages[0].get("origin"), stages[-1].get("destination")):
        issues.append(
            {
                "code": "ROUTE_NOT_CLOSED",
                "severity": "blocker",
                "description": "行程终点未回到整体出发点，路线尚未形成闭环",
            }
        )
    return issues


def _repair_activity_stage_overlaps(day_plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Move fixed service/rest stops out of split driving stages.

    Deep-drive splitting inserts a required stop at a segment boundary. A provider
    may return the next segment with the same timestamp as that stop; shift the
    affected segment and all following segments instead of leaving a blocker.
    """
    movable_types = {"charging", "fueling", "rest", "service", "parking", "meal"}
    for day in day_plans:
        stages = sorted(day.get("stages", []), key=lambda item: item.get("sequence", 0))
        activities = sorted(day.get("activities", []), key=lambda item: item.get("planned_start", ""))
        # Walk the complete day timeline and push the later item forward when
        # a provider returns overlapping timestamps.  Durations are retained,
        # so meal/attraction/hotel blocks and movement stages remain usable.
        timeline = sorted(
            [
                *stages,
                *[
                    activity
                    for activity in activities
                    if not (
                        activity.get("type") == "meal"
                        and activity.get("in_transit") is True
                    )
                ],
            ],
            key=lambda item: (item.get("planned_start", ""), item.get("sequence", 0)),
        )
        previous_end: datetime | None = None
        for item in timeline:
            start = datetime.fromisoformat(item["planned_start"])
            end = datetime.fromisoformat(item["planned_end"])
            if previous_end is not None and start < previous_end:
                duration = end - start
                start = previous_end
                end = start + duration
                item["planned_start"] = start.isoformat()
                item["planned_end"] = end.isoformat()
            previous_end = end
        for sequence, item in enumerate(sorted(stages + activities, key=lambda value: value.get("planned_start", ""))):
            item["sequence"] = sequence
        day["items"] = [
            *({"type": "stage", "id": item["id"]} for item in stages),
            *({"type": "activity", "id": item["id"]} for item in activities),
        ]
    return day_plans


def _same_place(first: dict[str, Any] | None, second: dict[str, Any] | None) -> bool:
    if not first or not second:
        return False
    if first.get("name") and first.get("name") == second.get("name"):
        return True
    first_coordinates = first.get("coordinates")
    second_coordinates = second.get("coordinates")
    if not first_coordinates or not second_coordinates:
        return False
    try:
        return _haversine_km(
            RoutePoint(
                longitude=float(first_coordinates["longitude"]),
                latitude=float(first_coordinates["latitude"]),
            ),
            RoutePoint(
                longitude=float(second_coordinates["longitude"]),
                latitude=float(second_coordinates["latitude"]),
            ),
        ) <= PLACE_CONTINUITY_TOLERANCE_KM
    except (KeyError, TypeError, ValueError):
        return False


def _poi_place(item: dict[str, Any]) -> dict[str, Any]:
    longitude, latitude = item["location"].split(",", 1)
    return {
        "id": item.get("id"),
        "name": item["name"],
        "address": item.get("address"),
        "city": item.get("city"),
        # Preserve the provider's semantic category tree.  A text search for
        # “景点” can legitimately return a KTV, pharmacy or campus; the
        # suitability intelligent agent needs this evidence to distinguish a
        # genuine attraction from an incidental nearby business.
        "categories": item.get("type"),
        "coordinates": {
            "longitude": float(longitude),
            "latitude": float(latitude),
        },
        "source_id": item.get("id"),
    }


def _contains_cjk(value: str) -> bool:
    """Return whether a POI label contains at least one CJK ideograph."""
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _energy_markdown(estimate: dict[str, Any] | None) -> str:
    if not estimate:
        return "不适用"
    remaining = estimate.get("remaining_percent")
    remaining_text = f"，预计剩余 {remaining}%" if remaining is not None else ""
    replenished = estimate.get("replenished_amount")
    replenished_text = (
        f"，补能 {replenished} {estimate.get('replenished_unit') or estimate['unit']}"
        if replenished is not None
        else ""
    )
    return (
        f"{estimate['amount']} {estimate['unit']}{replenished_text}{remaining_text}"
        f"{'（估算）' if estimate.get('estimated') else ''}"
    )


def _nearby_corridor(
    first: tuple[float, float],
    second: tuple[float, float],
) -> bool:
    longitude_delta = (first[0] - second[0]) * 0.87
    latitude_delta = first[1] - second[1]
    return (longitude_delta**2 + latitude_delta**2) ** 0.5 <= 0.6


def _closest_weather_sample(
    weather: dict[str, Any] | None,
    planned_at: datetime,
) -> dict[str, Any] | None:
    if not weather:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for sample in weather.get("hourly_samples", []):
        try:
            sampled_at = datetime.fromisoformat(sample["sampled_at"])
            if sampled_at.tzinfo is None:
                sampled_at = sampled_at.replace(tzinfo=SHANGHAI)
            delta = abs((sampled_at - planned_at).total_seconds())
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or delta < best[0]:
            best = (delta, sample)
    if not best or best[0] > 2 * 60 * 60:
        return None
    return best[1]


def _current_weather_sample(
    weather: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any] | None:
    """Return a current-day weather snapshot for plans more than 24h away.

    The planner must not present a far-future hourly value as if it were a
    reliable forecast. Open-Meteo still returns a ``current`` object and often
    today's hourly precipitation/visibility, so combine the nearest same-day
    hourly sample with the current fields. This keeps the itinerary useful
    without presenting an outdated recheck message as a forecast.
    """
    if not weather:
        return None
    current = weather.get("current")
    if not isinstance(current, dict):
        current = {}
    best: tuple[float, dict[str, Any]] | None = None
    for sample in weather.get("hourly_samples", []):
        if not isinstance(sample, dict):
            continue
        try:
            sampled_at = datetime.fromisoformat(str(sample["sampled_at"]))
            if sampled_at.tzinfo is None:
                sampled_at = sampled_at.replace(tzinfo=SHANGHAI)
        except (KeyError, TypeError, ValueError):
            continue
        if sampled_at.astimezone(SHANGHAI).date() != now.astimezone(SHANGHAI).date():
            continue
        delta = abs((sampled_at - now).total_seconds())
        if best is None or delta < best[0]:
            best = (delta, sample)
    sample = dict(best[1]) if best else {}
    sampled_at = sample.get("sampled_at") or current.get("time")
    temperature = sample.get("temperature_c")
    if temperature is None:
        temperature = current.get("temperature_2m")
    weather_code = sample.get("weather_code")
    if weather_code is None:
        weather_code = current.get("weather_code")
    wind = sample.get("wind_speed_kmh")
    if wind is None:
        wind = current.get("wind_speed_10m")
    if sampled_at is None and temperature is None and weather_code is None:
        return None
    return {
        "sampled_at": sampled_at or now.isoformat(),
        "temperature_c": temperature,
        "precipitation_probability": sample.get("precipitation_probability"),
        "weather_code": weather_code,
        "visibility_m": sample.get("visibility_m"),
        "wind_speed_kmh": wind,
    }
