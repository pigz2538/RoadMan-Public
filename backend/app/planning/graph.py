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
from ..skills.registry import SkillRegistry
from .llm import OllamaRequirementExtractor
from .state import RoadManState

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

    async def build_stages(state: RoadManState) -> dict[str, Any]:
        await emit(state, "build_stages", "正在生成每天的移动阶段", 74)
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
            is_return = index == len(day_defs) - 1 and len(day_defs) > 1
            route = inbound if is_return else outbound
            origin = PlaceRef.model_validate(request["destination"] if is_return else request["origin"])
            destination = PlaceRef.model_validate(request["origin"] if is_return else request["destination"])
            start_at = datetime.combine(
                date.fromisoformat(day_def["date"]),
                time(14, 30) if is_return else time(8, 0),
                tzinfo=SHANGHAI,
            )
            duration = int(route["data"]["duration_minutes"])
            segment = RouteSegment(
                coordinates=[Coordinates.model_validate(point) for point in route["data"]["geometry"]],
                distance_km=route["data"]["distance_km"],
                duration_minutes=duration,
                toll=bool(route["data"].get("tolls_cny")),
            )
            stage = MovementStage(
                day_id=f"day_{index + 1}",
                sequence=0,
                title="返程" if is_return else "城市出发",
                mode=route["data"].get("selected_mode", "driving"),
                origin=origin,
                destination=destination,
                route_segments=[segment],
                planned_start=start_at,
                planned_end=start_at + timedelta(minutes=duration),
                distance_km=route["data"]["distance_km"],
                duration_minutes=duration,
                toll_fee={
                    "minimum": route["data"].get("tolls_cny", 0),
                    "maximum": route["data"].get("tolls_cny", 0),
                    "estimated": False,
                },
                source_records=[SourceRecord.model_validate(item) for item in route.get("sources", [])],
            )
            plan = DayPlan(
                id=f"day_{index + 1}",
                day_index=index + 1,
                date=date.fromisoformat(day_def["date"]),
                title=f"第 {index + 1} 天",
                items=[DayItemRef(type="stage", id=stage.id)],
                stages=[stage],
                total_distance_km=stage.distance_km,
                total_drive_minutes=stage.duration_minutes,
            )
            plans.append(plan.model_dump(mode="json"))
        return {"day_plans": plans, "progress": {"node": "build_stages", "value": 74}}

    async def verify_plan(state: RoadManState) -> dict[str, Any]:
        await emit(state, "verify_plan", "正在校验路线与时间约束", 84)
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
        return {
            "verification_result": {
                "passed": not any(item["severity"] == "blocker" for item in issues),
                "issues": issues,
            },
            "progress": {"node": "verify_plan", "value": 84},
        }

    async def repair_plan(state: RoadManState) -> dict[str, Any]:
        await emit(state, "repair_plan", "正在执行一次确定性自动修复", 88)
        return {
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
        await emit(state, "render_markdown", "正在生成 Markdown 路书", 94)
        request = state["trip_request"]
        lines = [
            f"# {request['origin']['name']}—{request['destination']['name']}自驾路书",
            "",
            f"- 日期：{request['start_date']} 至 {request['end_date']}",
            f"- 出行人数：{request.get('travelers', 1)} 人",
            f"- 可见默认值：{', '.join(request.get('defaults_applied', [])) or '无'}",
            "",
        ]
        for day in state.get("day_plans", []):
            lines.extend([f"## {day['title']} · {day['date']}", ""])
            for stage in day.get("stages", []):
                lines.extend(
                    [
                        f"### {stage['origin']['name']} → {stage['destination']['name']}",
                        f"- 方式：{stage['mode']}",
                        f"- 时间：{stage['planned_start'][11:16]}–{stage['planned_end'][11:16]}",
                        f"- 里程：{stage['distance_km']} km",
                        f"- 预计耗时：{stage['duration_minutes']} 分钟",
                        "",
                    ]
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
        await emit(state, "persist_trip", "路书生成完成", 100, event="planning_completed")
        return {"progress": {"node": "persist_trip", "value": 100}}

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
    builder.add_node("build_stages", build_stages)
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
    builder.add_edge("split_into_days", "build_stages")
    builder.add_edge("build_stages", "verify_plan")
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
) -> dict[str, Any]:
    result = await registry.execute(
        "amap.route",
        {
            "origin": {**origin["coordinates"], "city": origin.get("city")},
            "destination": {**destination["coordinates"], "city": destination.get("city")},
            "preferred_mode": "driving",
            "allowed_fallback_modes": ["riding", "walking", "transit"],
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
