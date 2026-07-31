from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

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
from .llm import OllamaRequirementExtractor
from .recommendations import rank_tourism_candidates
from .state import RoadManState
from .tourism import schedule_tourism_activities, verify_tourism_plan

ProgressCallback = Callable[[str, str, str, int, str, str | None], Awaitable[None]]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def build_planning_graph(
    registry: SkillRegistry,
    settings: Settings,
    progress_callback: ProgressCallback | None = None,
):
    extractor = OllamaRequirementExtractor(settings)

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
        for field in ("start_date", "end_date", "travelers"):
            if extracted.get(field) is not None and current.get(field) is None:
                current[field] = extracted[field]
        current["raw_text"] = state["raw_input"]
        current["preferences"] = list(
            dict.fromkeys([*current.get("preferences", []), *extracted.get("preferences", [])])
        )
        return {
            "trip_request": current,
            "progress": {"node": "extract_trip_request", "value": 15},
        }

    async def apply_defaults(state: RoadManState) -> dict[str, Any]:
        await emit(state, "apply_defaults", "正在应用可见默认值", 22)
        request = dict(state["trip_request"])
        defaults = list(request.get("defaults_applied", []))
        if request.get("travelers") is None:
            request["travelers"] = 1
            defaults.append("travelers=1")
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
            "正在检索景点、餐饮与住宿候选",
            65,
            event="tool_started",
            tool="amap.poi",
        )
        destination = state["trip_request"]["destination"]
        coordinates = destination.get("coordinates")
        categories = {
            "attractions": ("景点", 12),
            "meals": ("餐厅", 10),
            "hotels": ("酒店", 10),
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
                        "source_records": source_records,
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
                    "limit": 12,
                    "language": "en",
                },
                SkillContext(trip_id=state["trip_id"]),
            )
            if open_trip_map.success and isinstance(open_trip_map.data, dict):
                source_records = [
                    item.model_dump(mode="json") for item in open_trip_map.sources
                ]
                tourism_sources.extend(source_records)
                known_names = {
                    item["place"]["name"].strip().lower()
                    for item in candidates["attractions"]
                }
                for item in open_trip_map.data.get("items", []):
                    name = str(item.get("name") or "").strip()
                    # OpenTripMap is backed by OSM and its English endpoint
                    # frequently returns Latin-only names in China. Do not
                    # leak those labels into the Chinese itinerary; AMap and
                    # FlyAI remain the authoritative local-name sources.
                    if not name or not _contains_cjk(name) or name.lower() in known_names:
                        continue
                    candidates["attractions"].append(
                        {
                            "place": {
                                "id": item.get("id"),
                                "name": name,
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
                            "source_records": source_records,
                            "provider": open_trip_map.provider,
                        }
                    )
                    known_names.add(name.lower())
        if flyai_ticket_items:
            for candidate in candidates["attractions"]:
                candidate_name = candidate["place"]["name"].replace(" ", "").lower()
                match = next(
                    (
                        item
                        for item in flyai_ticket_items
                        if item.get("name")
                        and (
                            item["name"].replace(" ", "").lower() in candidate_name
                            or candidate_name in item["name"].replace(" ", "").lower()
                        )
                    ),
                    None,
                )
                if not match:
                    continue
                candidate["ticket_name"] = match.get("ticket_name")
                candidate["ticket_date"] = match.get("ticket_date")
                if match.get("price_min_cny") is not None:
                    candidate["ticket_or_price"] = {
                        "currency": "CNY",
                        "minimum": match["price_min_cny"],
                        "maximum": match["price_max_cny"],
                        "estimated": match.get("price_estimated", False),
                    }
        candidates = rank_tourism_candidates(
            candidates,
            destination,
            state["trip_request"].get("preferences", []),
        )
        await emit(
            state,
            "discover_tourism",
            (
                f"已找到 {len(candidates['attractions'])} 个景点、"
                f"{len(candidates['meals'])} 个餐饮和 "
                f"{len(candidates['hotels'])} 个住宿候选"
            ),
            67,
            event="tool_completed",
            tool="amap.poi",
        )
        return {
            "tourism_candidates": candidates,
            "sources": [*state.get("sources", []), *tourism_sources],
            "progress": {"node": "discover_tourism", "value": 67},
        }

    async def build_local_routes(state: RoadManState) -> dict[str, Any]:
        await emit(
            state,
            "build_local_routes",
            "正在补充目的地公共交通、步行和骑行接驳",
            68,
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
        places = [item["place"] for item in attraction_candidates]
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
                target = places[cursor % len(places)]
                cursor += 1
                mode = modes[(day_index * 2 + sequence) % len(modes)]
                route = await _route(
                    registry,
                    anchor,
                    target,
                    state["trip_id"],
                    preferred_mode=mode,
                    fallback_modes=["walking", "riding", "transit", "driving"],
                )
                if route.get("success"):
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
        for index, day_def in enumerate(day_defs):
            stages: list[MovementStage] = []
            day_date = date.fromisoformat(day_def["date"])
            if index == 0:
                stages.append(
                    _movement_stage(
                        day_id=f"day_{index + 1}",
                        sequence=len(stages),
                        title="城市出发",
                        origin=request["origin"],
                        destination=request["destination"],
                        route=outbound,
                        start_at=datetime.combine(day_date, time(8, 0), tzinfo=SHANGHAI),
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
                return_start = max(
                    local_start,
                    datetime.combine(day_date, time(14, 30), tzinfo=SHANGHAI),
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
        return {"day_plans": plans, "progress": {"node": "build_stages", "value": 78}}

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

    async def verify_plan(state: RoadManState) -> dict[str, Any]:
        await emit(state, "verify_plan", "正在校验路线、交通方式、天气与时间约束", 89)
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
            "progress": {"node": "verify_plan", "value": 89},
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
        lines = [
            f"# {request['origin']['name']}—{request['destination']['name']}自驾行程安排",
            "",
            f"- 日期：{request['start_date']} 至 {request['end_date']}",
            f"- 出行人数：{request.get('travelers', 1)} 人",
            f"- 可见默认值：{', '.join(request.get('defaults_applied', [])) or '无'}",
            "",
        ]
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
    builder.add_node("verify_plan", verify_plan)
    builder.add_node("repair_plan", repair_plan)
    builder.add_node("render_markdown", render_markdown)
    builder.add_node("persist_trip", persist_trip)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "extract_trip_request")
    builder.add_edge("extract_trip_request", "apply_defaults")
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
    builder.add_edge("schedule_tourism", "verify_plan")
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
    return {
        **place,
        "address": result.data.get("formatted_address"),
        "city": result.data.get("city") or place.get("city"),
        "coordinates": {"longitude": float(longitude), "latitude": float(latitude)},
    }


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
    if result.success and isinstance(result.data, dict) and not _route_mode_feasible(result.data):
        retry_modes = [
            mode
            for mode in ["transit", "driving", "riding"]
            if mode != result.data.get("selected_mode")
        ]
        result = await registry.execute(
            "amap.route",
            {
                "origin": {**origin["coordinates"], "city": origin.get("city")},
                "destination": {
                    **destination["coordinates"],
                    "city": destination.get("city"),
                },
                "preferred_mode": retry_modes[0],
                "allowed_fallback_modes": retry_modes[1:],
            },
            SkillContext(trip_id=trip_id),
        )
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
        return duration <= 180 and distance <= 20
    if mode == "riding":
        return duration <= 240 and distance <= 60
    if mode == "transit":
        return duration <= 360
    return True


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
    )
    traffic_summary = data.get("traffic_summary")
    if data.get("selected_mode") == "driving" and traffic_summary:
        if start_at.date() != date.today():
            traffic_summary = f"当前路况参考（非未来预测）：{traffic_summary}"
    elif data.get("selected_mode") != "driving":
        traffic_summary = {
            "transit": "公共交通按高德当前班次规划",
            "walking": "步行路段不适用机动车实时路况",
            "riding": "骑行路段不适用机动车实时路况",
        }.get(data.get("selected_mode"), "按高德路线规划")
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
