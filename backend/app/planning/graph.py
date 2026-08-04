from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
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
    verify_deep_drive_plan,
)
from .event_research import event_research_summary, research_special_events
from .llm import (
    OllamaEventResearchAgent,
    OllamaPoiCurator,
    OllamaPoiRanker,
    OllamaRequirementExtractor,
)
from .recommendations import apply_agent_ranking, rank_tourism_candidates
from .poi_enrichment import enrich_tourism_candidates
from .seasonality import apply_seasonal_guard, parse_trip_date
from .state import RoadManState
from .tourism import review_daily_schedule, schedule_tourism_activities, verify_tourism_plan

ProgressCallback = Callable[[str, str, str, int, str, str | None], Awaitable[None]]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _normalize_poi_name(value: Any) -> str:
    """Normalize a model-selected POI name for exact identity matching."""
    return "".join(str(value or "").split()).casefold()


def build_planning_graph(
    registry: SkillRegistry,
    settings: Settings,
    progress_callback: ProgressCallback | None = None,
):
    extractor = OllamaRequirementExtractor(settings)
    event_research_agent = OllamaEventResearchAgent(settings)
    poi_curator = OllamaPoiCurator(settings)
    poi_ranker = OllamaPoiRanker(settings)

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
        extracted = await extractor.extract(state["raw_input"], date.today())
        current = dict(state.get("trip_request", {}))
        if extracted.get("origin_name") and not current.get("origin"):
            current["origin"] = {"name": extracted["origin_name"]}
        if extracted.get("destination_name") and not current.get("destination"):
            current["destination"] = {"name": extracted["destination_name"]}
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
        current["defaults_applied"] = list(dict.fromkeys(defaults))
        current["raw_text"] = state["raw_input"]
        current["preferences"] = list(
            dict.fromkeys([*current.get("preferences", []), *extracted.get("preferences", [])])
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
            "事件 Agent 正在核对极大值、观测窗口与公开来源",
            18,
            event="tool_started",
            tool="web.event_research",
        )
        try:
            year = date.fromisoformat(request.get("start_date", "")).year
        except (TypeError, ValueError):
            year = date.today().year
        research = await research_special_events(
            events,
            year=year,
            destination=(request.get("destination") or {}).get("name"),
            fact_agent=event_research_agent.extract,
        )
        await emit(
            state,
            "research_events",
            "事件 Agent 已返回公开资料，安排时会避开不可验证的硬时间承诺",
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
        await emit(
            state,
            "build_base_route",
            "正在查询真实驾车道路",
            40,
            event="tool_started",
            tool="amap.route",
        )
        request = dict(state["trip_request"])
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
        outbound = await _route(registry, origin, destination, state["trip_id"])
        inbound = await _route(registry, destination, origin, state["trip_id"])
        candidates = [item for item in (outbound, inbound) if item.get("success")]
        error = None
        if not outbound.get("success"):
            error = {"code": outbound.get("error_code") or "ROUTE_UNAVAILABLE", "message": "去程路线不可用"}
        await emit(
            state,
            "build_base_route",
            "真实道路查询完成" if not error else "路线查询未成功",
            55,
            event="tool_completed",
            tool="amap.route",
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
        await emit(
            state,
            "discover_tourism",
            "FlyAI Agent 正在检索景点与门票候选",
            65,
            event="tool_started",
            tool="flyai.poi",
        )
        destination = state["trip_request"]["destination"]
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
                "city_name": destination.get("city") or destination["name"],
                "keyword": destination["name"],
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
            "discover_tourism",
            "FlyAI Agent 正在检索餐饮候选与营业信息",
            65,
            event="tool_started",
            tool="flyai.poi",
        )
        flyai_meals = await registry.execute(
            "flyai.poi",
            {
                "city_name": destination.get("city") or destination["name"],
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
            "discover_tourism",
            "FlyAI Agent 已返回餐饮候选，交由 POI Agent 去重排序",
            65,
            event="tool_completed",
            tool="flyai.poi",
        )
        flyai_hotels = await registry.execute(
            "flyai.hotel",
            {
                "destination": destination.get("city") or destination["name"],
                "poi_name": destination["name"],
                "check_in_date": state["trip_request"]["start_date"],
                "check_out_date": state["trip_request"]["end_date"],
                "sort": "rate_desc",
            },
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
        for category, (keywords, page_size) in categories.items():
            if category == "hotels" and candidates["hotels"]:
                continue
            result = await registry.execute(
                "amap.poi",
                {
                    "keywords": keywords,
                    "city": destination.get("city"),
                    "location": (
                        f"{coordinates['longitude']},{coordinates['latitude']}"
                        if coordinates
                        else None
                    ),
                    "radius": 25000,
                    "page_size": page_size,
                },
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
                        "detail_url": f"https://www.amap.com/search?query={quote(item['name'])}",
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
                    "radius_m": 25000,
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
                    "Agent 正在比对高德与 OpenStreetMap 景点、合并同地点并生成中文显示名",
                    66,
                    event="tool_started",
                    tool="ollama.poi_curator",
                )
                decisions = await poi_curator.curate(
                    destination.get("city") or destination["name"],
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
                        f"Agent 已合并 {merged_count} 个同地点，翻译 {translated_count} 个名称，"
                        f"从 OSM 保留 {added_count} 个独立景点"
                    ),
                    67,
                    event="tool_completed",
                    tool="ollama.poi_curator",
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
        candidates = rank_tourism_candidates(
            candidates,
            destination,
            state["trip_request"].get("preferences", []),
        )
        if settings.enable_poi_web_enrichment:
            await emit(
                state,
                "enrich_poi_details",
                "POI Agent 正在汇总百科介绍、图片与可追溯来源",
                67,
                event="tool_started",
                tool="baidu.baike",
            )
            candidates = await enrich_tourism_candidates(
                candidates,
                timeout_seconds=settings.poi_web_timeout_seconds,
            )
            await emit(
                state,
                "enrich_poi_details",
                "POI Agent 已完成景点详情与图片补充",
                67,
                event="tool_completed",
                tool="baidu.baike",
            )
        if settings.ollama_api_key:
            await emit(
                state,
                "rank_tourism_candidates",
                "POI Agent 正在根据偏好、距离、评分、价格综合排序候选",
                68,
                event="tool_started",
                tool="ollama.poi_ranker",
            )
            agent_decisions = await poi_ranker.rank(
                candidates,
                state["trip_request"].get("preferences", []),
                state["trip_request"].get("special_events", []),
                travel_start=state["trip_request"].get("start_date"),
                travel_end=state["trip_request"].get("end_date"),
            )
            if agent_decisions:
                candidates = apply_agent_ranking(candidates, agent_decisions)
            await emit(
                state,
                "rank_tourism_candidates",
                "POI Agent 已完成候选排序与推荐理由",
                68,
                event="tool_completed",
                tool="ollama.poi_ranker",
            )
        candidates, seasonal_review = apply_seasonal_guard(
            candidates,
            parse_trip_date(state["trip_request"].get("start_date")),
            parse_trip_date(state["trip_request"].get("end_date")),
        )
        if seasonal_review:
            await emit(
                state,
                "review_seasonality",
                f"季节复核已将 {len(seasonal_review)} 个不合时令候选降为备选",
                69,
                event="tool_completed",
                tool="seasonality.guard",
            )
        await emit(
            state,
            "discover_tourism",
            (
                f"已找到 {len(candidates['attractions'])} 个景点、"
                f"{len(candidates['meals'])} 个餐饮和 "
                f"{len(candidates['hotels'])} 个住宿候选"
            ),
            69,
            event="tool_completed",
            tool="amap.poi",
        )
        return {
            "tourism_candidates": candidates,
            "seasonal_review": seasonal_review,
            "sources": [*state.get("sources", []), *tourism_sources],
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
        if not coordinates:
            return {"local_routes": []}
        attraction_candidates = state.get("tourism_candidates", {}).get(
            "attractions", []
        )
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
        places = _select_itinerary_places(
            attraction_candidates,
            destination,
            max(2, len(state["day_plans"]) * 2),
        )
        if not places:
            return {"local_routes": []}

        day_count = len(state["day_plans"])
        local_day_indexes = list(range(1, day_count - 1))
        if not local_day_indexes:
            local_day_indexes = [0]
        modes = ["transit", "walking", "riding"]
        local_routes: list[dict[str, Any]] = []
        cursor = 0
        for day_index in local_day_indexes:
            anchor = destination
            for sequence in range(2):
                route = None
                target = None
                for _ in range(min(6, len(places))):
                    target = places[cursor % len(places)]
                    cursor += 1
                    mode = modes[(day_index * 2 + sequence) % len(modes)]
                    candidate_route = await _route(
                        registry,
                        anchor,
                        target,
                        state["trip_id"],
                        preferred_mode=mode,
                        fallback_modes=["walking", "riding", "transit", "driving"],
                    )
                    if candidate_route.get("success") and _local_route_reasonable(candidate_route.get("data", {})):
                        route = candidate_route
                        break
                if route and target:
                    local_routes.append(
                        {
                            "day_index": day_index,
                            "sequence": sequence,
                            "origin": anchor,
                            "destination": target,
                            "route": route,
                        }
                    )
                    anchor = target
            if anchor is not destination:
                route = await _route(
                    registry,
                    anchor,
                    destination,
                    state["trip_id"],
                    preferred_mode="transit",
                    fallback_modes=["walking", "riding", "driving"],
                )
                if not route.get("success"):
                    route = _fallback_local_route(anchor, destination, "transit")
                local_routes.append(
                    {
                        "day_index": day_index,
                        "sequence": 2,
                        "origin": anchor,
                        "destination": destination,
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
        outbound = state["selected_route"]
        inbound = next(
            (route for route in state.get("route_candidates", [])[1:] if route.get("success")),
            outbound,
        )
        day_defs = state["day_plans"]
        plans: list[dict[str, Any]] = []
        elevation_sources: list[dict[str, Any]] = []
        elevation_cache: dict[str, float | None] = {}

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

        for index, day_def in enumerate(day_defs):
            stages: list[MovementStage] = []
            day_date = date.fromisoformat(day_def["date"])
            if index == 0:
                outbound = await prepare_route(outbound)
                stages.append(
                    _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="城市出发",
                        origin=request["origin"],
                        destination=request["destination"],
                        route=outbound,
                        start_at=_request_clock(
                            day_date,
                            request.get("departure_time"),
                            default=time(8, 0),
                        ),
                    )
                )
            local_start = (
                stages[-1].planned_end + timedelta(minutes=60)
                if stages
                else datetime.combine(day_date, time(9, 30), tzinfo=SHANGHAI)
            )
            for local in sorted(
                (
                    item
                    for item in state.get("local_routes", [])
                    if item["day_index"] == index
                ),
                key=lambda item: item["sequence"],
            ):
                local["route"] = await prepare_route(local["route"])
                stage = _movement_stage(
                    day_id=f"day_{index + 1}",
                    sequence=len(stages),
                    title=_local_stage_title(
                        local["route"]["data"].get("selected_mode"),
                        return_to_base=local.get("return_to_base", False),
                    ),
                    origin=local["origin"],
                    destination=local["destination"],
                    route=local["route"],
                    start_at=local_start,
                )
                stages.append(stage)
                local_start = stage.planned_end + timedelta(minutes=105)
            if index == len(day_defs) - 1:
                inbound = await prepare_route(inbound)
                return_start = max(
                    local_start,
                    _request_clock(
                        day_date,
                        request.get("return_time"),
                        default=time(14, 30),
                    ),
                )
                stages.append(
                    _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="返程",
                        origin=request["destination"],
                        destination=request["origin"],
                        route=inbound,
                        start_at=return_start,
                    )
                )
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
        weather_cache: dict[tuple[float, float], dict[str, Any] | None] = {}
        weather_sources: list[dict[str, Any]] = []
        for day in plans:
            for stage in day.get("stages", []):
                coordinates = stage["destination"].get("coordinates")
                if not coordinates:
                    continue
                key = (coordinates["longitude"], coordinates["latitude"])
                if key not in weather_cache:
                    planned = datetime.fromisoformat(stage["planned_end"])
                    horizon = (planned.date() - date.today()).days + 1
                    if horizon < 1 or horizon > 16:
                        weather_cache[key] = None
                    else:
                        result = await registry.execute(
                            "open_meteo.forecast",
                            {
                                "latitude": coordinates["latitude"],
                                "longitude": coordinates["longitude"],
                                "forecast_days": max(1, horizon),
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
                sample = _closest_weather_sample(
                    weather_cache[key],
                    datetime.fromisoformat(stage["planned_end"]),
                )
                if sample:
                    temperature = sample.get("temperature_c")
                    precipitation = sample.get("precipitation_probability")
                    stage["weather_summary"] = (
                        f"预计抵达 {temperature}°C，降水概率 {precipitation}%"
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
                    stage["weather_summary"] = "计划时间超出逐小时预报范围，请临近出发复核"
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

    async def load_vehicle_profile(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "load_vehicle_profile",
            "Vehicle Agent 正在读取续航、电量与车辆限制",
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
                for category, keyword in categories.items():
                    result = await registry.execute(
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
            88,
        )
        plans, warnings = enrich_deep_drive_plan(
            state.get("day_plans", []),
            state.get("vehicle_profile") or default_vehicle(),
            state.get("service_pois", {}),
            int(state["trip_request"].get("max_continuous_drive_minutes") or 120),
        )
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
            "progress": {"node": "enrich_deep_drive", "value": 88},
        }

    async def schedule_tourism(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "schedule_tourism",
            "正在安排景点停留、每日餐食与过夜住宿",
            88,
        )
        plans = schedule_tourism_activities(
            state.get("day_plans", []),
            state.get("tourism_candidates", {}),
        )
        return {
            "day_plans": plans,
            "progress": {"node": "schedule_tourism", "value": 88},
        }

    async def review_daily_schedule_node(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "review_daily_schedule",
            "每日复核 Agent 正在检查上午、下午、晚间与三餐住宿",
            90,
        )
        plans, review_notes = review_daily_schedule(
            state.get("day_plans", []),
            state.get("tourism_candidates", {}),
        )
        return {
            "day_plans": plans,
            "warnings": [*state.get("warnings", []), *review_notes],
            "progress": {"node": "review_daily_schedule", "value": 90},
        }

    async def verify_plan(state: RoadManState) -> dict[str, Any]:
        await emit(state, "verify_plan", "正在校验路线、交通方式、天气与时间约束", 92)
        # Normalize provider timestamps before enforcing hard constraints.
        # A service or meal can be returned at the exact start of the next
        # movement segment; move that segment forward to avoid a false blocker.
        normalized_days = _repair_activity_stage_overlaps(state.get("day_plans", []))
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
        return {
            "day_plans": normalized_days,
            "verification_result": {
                "passed": not any(item["severity"] == "blocker" for item in issues),
                "issues": issues,
            },
            "progress": {"node": "verify_plan", "value": 92},
        }

    async def repair_plan(state: RoadManState) -> dict[str, Any]:
        await emit(state, "repair_plan", "正在执行一次确定性自动修复", 88)
        repaired_days = _repair_activity_stage_overlaps(state.get("day_plans", []))
        return {
            "day_plans": repaired_days,
            "repair_attempted": True,
            "warnings": [
                *state.get("warnings", []),
                {
                    "code": "AUTO_REPAIR_ATTEMPTED",
                    "message": "已执行一次计划结构修复",
                    "severity": "warning",
                },
            ],
        }

    async def render_markdown(state: RoadManState) -> dict[str, Any]:
        await emit(state, "render_markdown", "正在生成 Markdown 行程安排", 94)
        request = state["trip_request"]
        traveler_count = request.get("travelers")
        traveler_label = f"{traveler_count} 人" if traveler_count else "待确认人数"
        lines = [
            f"# {request['origin']['name']}—{request['destination']['name']}自驾行程安排",
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
                        "",
                    ]
                )
            for activity in day.get("activities", []):
                lines.append(
                    f"- 沿途服务：{activity['place']['name']}（{activity['type']}，"
                    f"{activity['duration_minutes']} 分钟，估算安排）"
                )
        if state.get("verification_result", {}).get("issues"):
            lines.extend(["## 校验提示", ""])
            lines.extend(
                f"- {item['description']}"
                for item in state["verification_result"]["issues"]
            )
        return {
            "plan_markdown": "\n".join(lines),
            "progress": {"node": "render_markdown", "value": 94},
        }

    async def persist_trip(state: RoadManState) -> dict[str, Any]:
        await emit(state, "persist_trip", "正在保存并核对行程安排", 96, event="progress")
        return {"progress": {"node": "persist_trip", "value": 96}}

    def after_validation(state: RoadManState) -> Literal["clarify", "route"]:
        return "clarify" if state.get("missing_fields") else "route"

    def after_verification(state: RoadManState) -> Literal["repair", "render"]:
        has_blocker = not state.get("verification_result", {}).get("passed", False)
        return "repair" if has_blocker and not state.get("repair_attempted") else "render"

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
    builder.add_edge("sample_weather", "enrich_deep_drive")
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
        "coordinates": {"longitude": float(longitude), "latitude": float(latitude)},
    }
    # A short scenic name can be ambiguous in AMap geocoding (乌镇, 南山, etc.).
    # If the first geocode is implausibly far from the departure point, use a
    # nearby POI search to select the matching local attraction instead of
    # silently planning a 1,900 km detour.
    nearby_coordinates = (nearby or {}).get("coordinates") or {}
    if nearby_coordinates:
        distance = _haversine_km(
            RoutePoint(
                longitude=float(nearby_coordinates["longitude"]),
                latitude=float(nearby_coordinates["latitude"]),
            ),
            RoutePoint(longitude=float(longitude), latitude=float(latitude)),
        )
        if distance > 250 and len(str(place.get("name") or "")) <= 12:
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
                        "coordinates": {
                            "longitude": float(item_longitude),
                            "latitude": float(item_latitude),
                        },
                    }
                )
    return resolved


async def _route(
    registry: SkillRegistry,
    origin: dict[str, Any],
    destination: dict[str, Any],
    trip_id: str,
    preferred_mode: str = "driving",
    fallback_modes: list[str] | None = None,
) -> dict[str, Any]:
    result = await registry.execute(
        "amap.route",
        {
            "origin": {**origin["coordinates"], "city": origin.get("city")},
            "destination": {**destination["coordinates"], "city": destination.get("city")},
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
                    **destination["coordinates"],
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
    return {
        "success": result.success,
        "data": result.data,
        "error_code": result.error_code,
        "warnings": result.warnings,
        "sources": [item.model_dump(mode="json") for item in result.sources],
    }


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
    """A sightseeing transfer should not consume most of a half-day."""
    duration = int(data.get("duration_minutes") or 0)
    distance = float(data.get("distance_km") or 0)
    return 0.05 <= distance <= 30 and 1 <= duration <= 75


def _select_itinerary_places(
    candidates: list[dict[str, Any]],
    anchor: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """Greedily balance Agent score with transfer distance to avoid scattered POIs."""
    remaining: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_coordinates: list[tuple[float, float]] = []
    for candidate in candidates:
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
    selected: list[dict[str, Any]] = []
    current = anchor
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
            return float(item.get("score") or 0) - distance * 1.5

        chosen = max(remaining, key=utility)
        selected.append(chosen["place"])
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
    duration = int(data["duration_minutes"])
    road_names = list(
        dict.fromkeys(
            step.get("road")
            for step in data.get("steps", [])
            if step.get("road")
        )
    )
    segment = RouteSegment(
        coordinates=[
            Coordinates.model_validate(point)
            for point in data["geometry"]
        ],
        distance_km=data["distance_km"],
        duration_minutes=duration,
        road_name=" / ".join(road_names[:3]) or None,
        toll=bool(data.get("tolls_cny")),
        elevation_gain_m=data.get("elevation_gain_m"),
    )
    traffic_summary = data.get("traffic_summary")
    if data.get("selected_mode") == "driving" and traffic_summary:
        if start_at.date() != date.today():
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
            }.get(mode, "按高德路线规划")
    return MovementStage(
        day_id=day_id,
        sequence=sequence,
        title=title,
        mode=data.get("selected_mode", "driving"),
        transit_type="bus" if data.get("selected_mode") == "transit" else None,
        origin=PlaceRef.model_validate(origin),
        destination=PlaceRef.model_validate(destination),
        route_segments=[segment],
        planned_start=start_at,
        planned_end=start_at + timedelta(minutes=duration),
        distance_km=data["distance_km"],
        duration_minutes=duration,
        elevation_gain_m=data.get("elevation_gain_m"),
        traffic_summary=traffic_summary,
        toll_fee={
            "minimum": data.get("tolls_cny", 0),
            "maximum": data.get("tolls_cny", 0),
            "estimated": False,
        },
        source_records=[
            SourceRecord.model_validate(item)
            for item in route.get("sources", [])
        ],
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
            [*stages, *activities],
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
    longitude_delta = (
        first_coordinates["longitude"] - second_coordinates["longitude"]
    ) * 0.87
    latitude_delta = first_coordinates["latitude"] - second_coordinates["latitude"]
    return (longitude_delta**2 + latitude_delta**2) ** 0.5 <= 0.02


def _poi_place(item: dict[str, Any]) -> dict[str, Any]:
    longitude, latitude = item["location"].split(",", 1)
    return {
        "id": item.get("id"),
        "name": item["name"],
        "address": item.get("address"),
        "city": item.get("city"),
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
    return (
        f"{estimate['amount']} {estimate['unit']}{remaining_text}"
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
