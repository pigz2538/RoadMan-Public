import json
import re
from datetime import date, timedelta
from typing import Any

import httpx

from ..core.config import Settings


class OllamaRequirementExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def extract(self, raw_text: str, today: date) -> dict[str, Any]:
        # Semantic intent is owned by the Requirement Agent.  The deterministic
        # parser is used only for calendar literals/relative calendar structure;
        # it must never guess an origin, destination, POI, party size or travel
        # mode.  In particular, an unavailable Agent must not turn “新疆” into
        # whichever restaurant/campus happens to be returned by a nearby search.
        structural = extract_structural_constraints(raw_text, today)
        if (
            not self.settings.enable_llm_requirement_extraction
            or not self.settings.ollama_api_key
        ):
            return {
                **structural,
                "_intent_status": "unavailable",
                "_intent_error": "REQUIREMENT_AGENT_UNAVAILABLE",
            }
        prompt = (
            "You are RoadMan Requirement Agent. Extract requirements only; never plan a route. "
            f"Today is {today.isoformat()}. Return ONLY one valid JSON object (no markdown, no explanation) "
            "with exactly these keys: origin_name, destination_name, destination_names, destination_scope, "
            "travel_intents, start_date, end_date, "
            "departure_time, return_time, travelers, max_days, preferences, transport_modes, special_events, "
            "cross_sea_required, cross_sea_mode, past_return_requested, time_window_minutes, stay_only_at_destination, must_visit_names. "
            "Dates must be YYYY-MM-DD; unknown scalar fields must be null and all list fields must be arrays. "
            "destination_name is one canonical route anchor string or null; destination_names is the complete ordered "
            "list of places explicitly requested by the user. Never stringify a list into destination_name. "
            "destination_scope must be exactly one of poi, city, province, region, multi_destination, unknown. "
            "For a province/region/city, keep the administrative name itself; never replace it with a nearby hotel, "
            "restaurant, university, campus or other POI. For multiple regions/cities, preserve every destination in "
            "destination_names and use the first explicitly ordered region as destination_name only when a route anchor "
            "is needed. travel_intents contains the user's actual experience goals (for example stargazing or food), "
            "not guessed place names. "
            "Only fill start_date/end_date when the user's text explicitly gives a calendar date, a relative date "
            "such as 今天/明天/后天/前天, or an unambiguous weekday. English relative dates and weekdays "
            "(today, tomorrow, the day after tomorrow, Monday, next Sunday/next week Sunday, this weekend) must also be "
            "resolved against Today. Phrases such as 今年暑假、最多三天、玩三天 are not "
            "calendar dates; leave both date fields null instead of inventing dates. "
            "Normalize Chinese time phrases: 中午=12:00, 下午=14:00, 晚上=19:00. "
            "Understand relative weekdays and ranges as a pair: 周一出发、周五回来 means a Monday-to-Friday "
            "window in the same upcoming week; 周末 means Saturday through Sunday; do not return an end date "
            "earlier than the start date. "
            "When a weekday pair is present, resolve both concrete dates from Today instead of leaving one null. "
            "Infer travelers semantically (情侣/夫妻=2, 一家三口=3); do not default to 1 when evidence is absent. "
            "请根据语义判断同行人数，不要机械默认 1。"
            "transport_modes must be an array containing only explicitly preferred or explicitly allowed modes from "
            "driving, train, flight, ferry, transit, walking, riding.  Interpret 高铁/动车/火车 as train, "
            "飞机/航班 as flight, 轮船/渡轮/船 as ferry, "
            "自驾/开车 as driving, and 公交/地铁/公共交通 as transit.  ‘可以坐高铁’ means train is an allowed and "
            "preferred intercity option; do not silently force driving for a long-distance trip.  If no transport "
            "preference is stated, return an empty array. "
            "cross_sea_required must be true only when the trip actually requires crossing a sea or water barrier; "
            "cross_sea_mode may be ferry, flight, bridge or null. past_return_requested is true only when the user "
            "explicitly requests returning before the current date. time_window_minutes is the user's explicit "
            "departure-to-arrival window, or null when no such window is stated. "
            "stay_only_at_destination is true only when the user explicitly says to stay all days in one destination or not visit other places; must_visit_names contains explicitly required named places. "
            "For text like 从A出发，在B及其周边旅游, use A as origin and B as destination. "
            "Separate the destination from the experience: in 到九宫山看流星雨, destination_name is 九宫山, "
            "special_events contains 流星雨. For astronomy, festivals, flowers or seasonal events, preserve the "
            "event name and ask a later research step to verify date, peak time, cloud/moon conditions and visibility. "
            "User text: "
            + raw_text
        )
        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                parsed = _parse_json_object(response.json().get("response", ""))
                if _destination_payload_needs_repair(parsed):
                    parsed = await self._repair_destination_payload(
                        raw_text,
                        parsed,
                    )
                merged = _merge_extraction(structural, parsed)
                # Literal calendar tokens are hard user constraints. Do not
                # let the Agent hallucinate another year.
                for date_field in ("start_date", "end_date"):
                    if structural.get(date_field):
                        merged[date_field] = structural[date_field]
                # Explicit clock windows are hard constraints just like
                # literal calendar dates.  Do not let a partial/cloud Agent
                # response erase an impossible same-leg window.
                for clock_field in (
                    "departure_time",
                    "return_time",
                    "time_window_minutes",
                ):
                    if structural.get(clock_field) is not None:
                        merged[clock_field] = structural[clock_field]
                if structural.get("cross_sea_required") is True:
                    merged["cross_sea_required"] = True
                # The cloud Agent must not invent a ferry/flight choice just
                # because a trip crosses water. Keep a mode only when the
                # user explicitly wrote one; otherwise preflight asks.
                merged["cross_sea_mode"] = structural.get("cross_sea_mode")
                if not merged.get("start_date"):
                    merged.pop("end_date", None)
                destination_name = merged.get("destination_name")
                if destination_name is not None:
                    if not isinstance(destination_name, str) or _looks_like_serialized_list(destination_name):
                        return {
                            **structural,
                            "_intent_status": "invalid",
                            "_intent_error": "INVALID_DESTINATION_SHAPE",
                        }
                    normalized_destination = _normalize_place_name(destination_name)
                    if normalized_destination:
                        merged["destination_name"] = normalized_destination
                    else:
                        merged.pop("destination_name", None)
                merged["destination_names"] = _normalize_destination_names(
                    merged.get("destination_names")
                )
                if not merged.get("destination_name") and len(merged["destination_names"]) == 1:
                    # This is not a place-name inference: the Agent already
                    # returned the typed destination_names array.  A single
                    # entry has an unambiguous route anchor.
                    merged["destination_name"] = merged["destination_names"][0]
                scope = str(merged.get("destination_scope") or "unknown").strip().lower()
                merged["destination_scope"] = (
                    scope
                    if scope in {"poi", "city", "province", "region", "multi_destination", "unknown"}
                    else "unknown"
                )
                merged["travel_intents"] = _normalize_text_list(
                    merged.get("travel_intents"), maximum=20
                )
                merged["preferences"] = _normalize_text_list(
                    merged.get("preferences"), maximum=40
                )
                merged["special_events"] = _normalize_special_events(merged.get("special_events"))
                merged["must_visit_names"] = [
                    str(item).strip() for item in (merged.get("must_visit_names") or [])
                    if str(item).strip()
                ][:40]
                merged["stay_only_at_destination"] = bool(merged.get("stay_only_at_destination"))
                for clock_field in ("departure_time", "return_time"):
                    normalized_clock = _normalize_clock(merged.get(clock_field))
                    if normalized_clock:
                        merged[clock_field] = normalized_clock
                    else:
                        merged.pop(clock_field, None)
                # Party size is a semantic decision owned by the Requirement
                # Agent (for example, “情侣” means two people). The backend
                # only validates the returned scalar and never scans raw text.
                merged["travelers"] = _coerce_travelers(merged.get("travelers"))
                if merged.get("travelers") is None:
                    merged.pop("travelers", None)
                merged["transport_modes"] = _normalize_transport_modes(
                    merged.get("transport_modes")
                )
                max_days = _coerce_positive_int(merged.get("max_days"), maximum=30)
                if max_days is None:
                    merged.pop("max_days", None)
                else:
                    merged["max_days"] = max_days
                merged["cross_sea_required"] = _coerce_optional_bool(
                    merged.get("cross_sea_required")
                )
                cross_sea_mode = str(merged.get("cross_sea_mode") or "").strip().casefold()
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
                merged["cross_sea_mode"] = (
                    cross_sea_mode if cross_sea_mode in {"ferry", "flight", "bridge"} else None
                )
                merged["past_return_requested"] = _coerce_optional_bool(
                    merged.get("past_return_requested")
                )
                merged["time_window_minutes"] = _coerce_time_window_minutes(
                    merged.get("time_window_minutes")
                )
                merged["_intent_status"] = "ok"
                merged["_intent_source"] = "ollama"
                merged["_source_raw_text"] = raw_text
                return merged
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return {
                **structural,
                "_intent_status": "unavailable",
                "_intent_error": "REQUIREMENT_AGENT_UNAVAILABLE",
            }

    async def _repair_destination_payload(
        self,
        raw_text: str,
        invalid_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Ask the same semantic Agent to repair a malformed destination shape.

        A model sometimes returns ``destination_name: "['西藏', '新疆']"``.
        Treating that value as a normal string is dangerous because the map
        geocoder may resolve a similarly named restaurant or university.  The
        repair is deliberately another LLM call, not ``ast.literal_eval`` or
        a keyword table, so the Agent remains responsible for entity boundaries
        and administrative scope.
        """
        repair_prompt = (
            "你是 RoadMan Requirement Agent 的 JSON 修复回合。原始用户需求如下。"
            "上一回合的目的地字段类型不合法，绝不能把数组字符串当作地点。请根据原文重新理解，"
            "只返回一个 JSON 对象，字段为 destination_name（单个规范字符串或 null）、"
            "destination_names（按用户顺序排列的规范字符串数组）、destination_scope（poi/city/province/"
            "region/multi_destination/unknown）、origin_name（字符串或 null）、travel_intents（字符串数组）、"
            "must_visit_names（字符串数组）。省份/城市必须保留行政区名称，不要替换成附近餐馆、校园或酒店；"
            "‘看流星雨’等体验放入 travel_intents，不要变成地点。"
            f"原始需求：{raw_text}；错误回合：{json.dumps(invalid_payload, ensure_ascii=False)}"
        )
        async with httpx.AsyncClient(timeout=min(self.settings.ollama_timeout_seconds, 45)) as client:
            response = await client.post(
                self.settings.ollama_api_url,
                headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                json={
                    "model": self.settings.ollama_model,
                    "prompt": repair_prompt,
                    "stream": False,
                    "think": False,
                    "format": "json",
                },
            )
            response.raise_for_status()
            repaired = _parse_json_object(response.json().get("response", ""))
        if _destination_payload_needs_repair(repaired):
            raise ValueError("LLM returned an invalid destination shape after repair")
        return repaired


class OllamaRequirementValidator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def validate(
        self,
        raw_text: str,
        extracted: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self.settings.ollama_api_key:
            return []
        prompt = (
            "你是 RoadMan Requirement Guard，只检查旅行需求是否自相矛盾、"
            "明显不可执行或缺少必须由用户决定的信息，不规划路线，不重复检查"
            "出发地、目的地、日期顺序、跨海方式和明确时间窗。"
            "返回单个 JSON：{\"issues\":[{\"code\":\"SEMANTIC_*\","
            "\"message\":\"给用户的简短问题\",\"field\":\"preferences\","
            "\"answer_type\":\"text\",\"options\":[]}]}。没有问题返回 {\"issues\":[]}。"
            f"结构化需求：{json.dumps(extracted, ensure_ascii=False)}；原始需求：{raw_text}"
        )
        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                    },
                )
                response.raise_for_status()
                payload = _parse_json_object(response.json().get("response", ""))
                issues = payload.get("issues", [])
                if not isinstance(issues, list):
                    return []
                return [
                    {
                        "code": str(item.get("code") or "SEMANTIC_REQUIREMENT"),
                        "message": str(item.get("message") or "请进一步确认该项需求。"),
                        "field": str(item.get("field") or "preferences"),
                        "answer_type": (
                            item.get("answer_type")
                            if item.get("answer_type") in {"text", "date", "choice", "time"}
                            else "text"
                        ),
                        "options": [str(option) for option in item.get("options", [])][:5],
                    }
                    for item in issues[:5]
                    if isinstance(item, dict)
                ]
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return []


class OllamaTripEditAgent:
    """Interpret free-form edits before the deterministic patch builder runs.

    The patch builder remains the only component allowed to mutate a trip.  This
    Agent only turns natural language into a small, validated intent so the
    chat panel can handle requests such as "多加一天" or "把第二天晚上换成
    夜游" instead of falling back to a canned help sentence.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def interpret(
        self,
        message: str,
        trip_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self.settings.ollama_api_key:
            return None
        prompt = (
            "你是 RoadMan 行程修改 Agent。你只能理解用户要如何调整已经存在的行程，"
            "不能虚构不存在的景点、酒店或餐厅，也不能直接声称修改已经完成。"
            "请只返回一个 JSON 对象，不要 Markdown："
            '{"intent":"add|delete|replace|replan|adjust|question|unknown",'
            '"day_index":1,"day_id":"","category":"attractions|hotels|meals|null",'
            '"target_stage_id":"","target_activity_id":"","candidate_id":"",'
              '"target_name":"","candidate_name":"","duration_minutes":null,'
              '"duration_delta_minutes":0,'
              '"origin_name":"","destination_name":"","start_date":"","end_date":"",'
              '"must_visit_names":[],"exclude_names":[],"restore_names":[],"stay_only_at_destination":false,'
              '"reply":"给用户的简短中文说明"}。'
            "如果用户要求增加或减少天数、重排整体路线、改变出发/返回日期，intent 用 replan。"
            "如果是加入/删除/替换一个已有候选，尽量填出对应第几天和名称；不确定时留空，"
            "优先返回上下文中的 day_id、阶段 ID、活动 ID、候选 ID；名称仅用于精确核对，禁止返回不存在的 ID。"
              "不要把‘看流星雨’、‘赏花’、‘泡温泉’等体验目标误当成景点名称。"
              "如果用户说‘这几天都住/玩在某地、不去其他地方’，用 replan 并将 stay_only_at_destination=true；"
              "用户明确点名的地点放入 must_visit_names，体验目标不要放入该数组。"
              "上下文中的 excluded_places 是用户已经明确删除的安排；除非本次消息明确要求恢复/重新加入，"
              "否则不要把其中的地点作为 add、replace 或 must_visit_names 返回。"
              "只有用户明确要求恢复已删除地点时，才把地点写入 restore_names。"
            f"当前行程上下文：{json.dumps(trip_context, ensure_ascii=False)}；"
            f"用户修改请求：{message}"
        )
        try:
            async with httpx.AsyncClient(timeout=min(self.settings.ollama_timeout_seconds, 45)) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                value = _parse_unfiltered_json_object(response.json().get("response", ""))
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return None
        intent = str(value.get("intent") or "unknown").strip().lower()
        if intent not in {"add", "delete", "replace", "replan", "adjust", "question", "unknown"}:
            intent = "unknown"
        category = value.get("category")
        if category not in {"attractions", "hotels", "meals"}:
            category = None
        try:
            day_index = int(value.get("day_index")) if value.get("day_index") is not None else None
        except (TypeError, ValueError):
            day_index = None
        try:
            delta = int(value.get("duration_delta_minutes") or 0)
        except (TypeError, ValueError):
            delta = 0
        try:
            duration = int(value.get("duration_minutes")) if value.get("duration_minutes") is not None else None
        except (TypeError, ValueError):
            duration = None
        duration = max(30, min(240, duration)) if duration is not None else None
        return {
            "intent": intent,
            "day_index": day_index,
            "day_id": str(value.get("day_id") or "").strip()[:120],
            "category": category,
            "target_stage_id": str(value.get("target_stage_id") or "").strip()[:120],
            "target_activity_id": str(value.get("target_activity_id") or "").strip()[:120],
            "candidate_id": str(value.get("candidate_id") or "").strip()[:200],
            "target_name": str(value.get("target_name") or "").strip()[:120],
            "candidate_name": str(value.get("candidate_name") or "").strip()[:120],
              "duration_minutes": duration,
              "duration_delta_minutes": max(-240, min(240, delta)),
              "origin_name": str(value.get("origin_name") or "").strip()[:120],
              "destination_name": str(value.get("destination_name") or "").strip()[:120],
              "start_date": str(value.get("start_date") or "").strip()[:20],
              "end_date": str(value.get("end_date") or "").strip()[:20],
              "must_visit_names": [str(item).strip()[:120] for item in (value.get("must_visit_names") or []) if str(item).strip()][:40],
              "exclude_names": [str(item).strip()[:120] for item in (value.get("exclude_names") or []) if str(item).strip()][:40],
              "restore_names": [str(item).strip()[:120] for item in (value.get("restore_names") or []) if str(item).strip()][:40],
              "stay_only_at_destination": bool(value.get("stay_only_at_destination")),
              "reply": str(value.get("reply") or "").strip()[:500],
        }


class OllamaPoiCurator:
    """Let the planning agent reconcile AMap and OSM/OpenTripMap POIs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def curate(
        self,
        destination_name: str,
        local_candidates: list[dict[str, Any]],
        osm_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Merging two POI sources is a semantic decision.  Never guess it from
        # a local substring/keyword table when the curator Agent is unavailable.
        if not self.settings.ollama_api_key or not osm_items:
            return []
        local_places = [item.get("place", {}) for item in local_candidates]
        compact_osm = [
            {
                "source_id": item.get("id"),
                "name": item.get("name"),
                "longitude": item.get("longitude"),
                "latitude": item.get("latitude"),
                "distance_m": item.get("distance_m"),
                "kinds": item.get("kinds"),
            }
            for item in osm_items
        ]
        prompt = (
            "你是 RoadMan 的多源 POI 策展 Agent。请比较高德本地候选与 OpenStreetMap/"
            "OpenTripMap 候选，结合名称语义、坐标距离和类别判断是否为同一真实景点。"
            "对每个 OSM 候选输出一个决定：merge 表示合并到同一地点，add 表示作为独立景点加入，"
            "skip 表示信息不足或明显无旅游价值。所有最终展示名必须是自然、准确的简体中文；"
            "不要凭空创造景点。只返回 JSON："
            '{"decisions":[{"source_id":"...","action":"merge|add|skip",'
            '"display_name_zh":"...","merge_target_name":"...","reason":"..."}]}。'
            f"目的地：{destination_name}；高德候选：{json.dumps(local_places, ensure_ascii=False)}；"
            f"OSM 候选：{json.dumps(compact_osm, ensure_ascii=False)}"
        )
        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                payload = _parse_unfiltered_json_object(response.json().get("response", ""))
                decisions = payload.get("decisions")
                if not isinstance(decisions, list):
                    return []
                valid_ids = {str(item.get("id")) for item in osm_items}
                cleaned = []
                for item in decisions:
                    if not isinstance(item, dict):
                        continue
                    source_id = str(item.get("source_id") or "")
                    action = str(item.get("action") or "skip")
                    display_name = str(item.get("display_name_zh") or "").strip()
                    if source_id not in valid_ids or action not in {"merge", "add", "skip"}:
                        continue
                    if action == "add" and not _has_cjk(display_name):
                        action = "skip"
                    cleaned.append(
                        {
                            "source_id": source_id,
                            "action": action,
                            "display_name_zh": display_name,
                            "merge_target_name": str(item.get("merge_target_name") or "").strip(),
                            "reason": str(item.get("reason") or "Agent 多源核验"),
                        }
                    )
                return cleaned
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return []


class OllamaDestinationResearchAgent:
    """Turn destination search evidence into source-backed highlights."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def summarize(
        self,
        destination: str,
        research: dict[str, Any],
        trip_request: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self.settings.ollama_api_key:
            return []
        evidence = [
            {
                "index": index,
                "title": item.get("title"),
                "snippet": item.get("snippet"),
                "url": item.get("url") or item.get("detail_url"),
                "category_hint": item.get("category_hint"),
            }
            for index, item in enumerate(
                [*(research.get("web_sources") or []), *(research.get("flyai_items") or [])][:36]
            )
        ]
        semantic_text = str(research.get("flyai_semantic_text") or "").strip()
        if semantic_text:
            evidence.append(
                {
                    "index": len(evidence),
                    "title": "飞猪目的地语义搜索",
                    "snippet": semantic_text[:6000],
                    "url": "https://flyai.open.fliggy.com/",
                    "category_hint": "attractions_and_meals",
                }
            )
        if not evidence:
            return []
        prompt = (
            "You are RoadMan Destination Research Agent. Based ONLY on supplied web/FlyAI evidence, "
            "identify famous, source-backed must-see attractions and representative local foods. "
            "For a city destination, cover different districts and landmark types instead of only places near a hotel; "
            "for a province or broad region, first organize the evidence into representative cities/景区片区 and "
            "select nationally or locally recognized landmarks before nearby generic POIs; "
            "return enough distinct attractions for the number of travel days (up to 12 attraction recommendations). "
            "Do not invent names, choose a restaurant/university/campus as a province or city destination, or promote "
            "obscure nearby POIs just because they are close to a geocoder point. Do not turn an experience into a place name. "
            "Return JSON only: {\"recommendations\":[{\"name\":\"...\",\"category\":\"attractions|meals\","
            "\"importance\":0,\"area\":\"城区或地理片区\",\"suggested_minutes\":90,"
            "\"best_time\":\"morning|afternoon|evening|any\",\"reason\":\"中文依据\","
            "\"source_indexes\":[0]}]}. "
            "importance is 0-100 and reflects local fame and evidence quality. "
            f"Destination: {destination}; trip request: {json.dumps(trip_request, ensure_ascii=False)}; "
            f"evidence: {json.dumps(evidence, ensure_ascii=False)}"
        )
        try:
            async with httpx.AsyncClient(timeout=min(self.settings.ollama_timeout_seconds, 45)) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                payload = _parse_unfiltered_json_object(response.json().get("response", ""))
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return []
        raw = payload.get("recommendations")
        if not isinstance(raw, list):
            return []
        cleaned: list[dict[str, Any]] = []
        for item in raw[:24]:
            if not isinstance(item, dict):
                continue
            category = item.get("category")
            name = str(item.get("name") or "").strip()
            if category not in {"attractions", "meals"} or not name:
                continue
            try:
                importance = float(item.get("importance") or 0)
            except (TypeError, ValueError):
                importance = 0
            indexes = item.get("source_indexes")
            cleaned.append(
                {
                    "name": name[:120],
                    "category": category,
                    "importance": max(0.0, min(100.0, importance)),
                    "area": str(item.get("area") or "").strip()[:80],
                    "suggested_minutes": _coerce_positive_minutes(
                        item.get("suggested_minutes")
                    )
                    or 90,
                    "best_time": (
                        item.get("best_time")
                        if item.get("best_time") in {"morning", "afternoon", "evening", "any"}
                        else "any"
                    ),
                    "reason": str(item.get("reason") or "来源支持的目的地推荐").strip()[:240],
                    "source_indexes": [
                        int(index)
                        for index in indexes
                        if isinstance(index, int) and 0 <= index < len(evidence)
                    ][:8]
                    if isinstance(indexes, list)
                    else [],
                }
            )
        return cleaned


class OllamaDestinationPlanAgent:
    """Turn destination evidence into a high-level, routeable trip brief.

    This is intentionally separate from the route adapter.  The Agent first
    selects recognizable, source-backed landmarks and geographic clusters;
    only after this brief is available does the graph ask map/schedule tools
    for executable legs.  A province therefore becomes a set of representative
    city/area clusters instead of a random POI returned by geocoding.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def draft(
        self,
        destinations: list[str],
        research: dict[str, Any],
        trip_request: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.settings.ollama_api_key:
            return {}
        recommendations = research.get("agent_recommendations") or []
        if not recommendations and research.get("destinations"):
            recommendations = [
                item
                for bundle in research.get("destinations", [])
                if isinstance(bundle, dict)
                for item in bundle.get("agent_recommendations", [])
                if isinstance(item, dict)
            ]
        prompt = (
            "你是 RoadMan 目的地行程策划 Agent。你已经拿到公开网页和旅行信息服务的证据，"
            "现在只写一份供路线 Agent 执行的高层计划单，不直接返回地图坐标。"
            "对于省份/大区域，必须拆成有代表性的城市或景区片区；对于城市，必须覆盖不同片区的著名地标，"
            "不要只围绕酒店，不要把学校、餐馆或搜索结果中的偶然地点当作目的地。"
            "根据用户日期、交通、人数和舒适度约束安排每天的主轴，给出景点、三餐和住宿区域的建议。"
            "没有证据支持的地点不要写入 selected_attractions。只返回 JSON："
            '{"strategy":"中文总体策略","selected_attractions":[{"name":"...","destination":"...",'
            '"area":"...","reason":"..."}],"day_plans":[{"day":1,"focus":"...",'
            '"area":"...","attraction_names":["..."],"meal_notes":["早餐","午餐","晚餐"],'
            '"overnight_area":"...","reason":"..."}],"unresolved_questions":[]}。'
            f"目的地列表：{json.dumps(destinations, ensure_ascii=False)}；"
            f"用户约束：{json.dumps(trip_request, ensure_ascii=False)}；"
            f"目的地研究与候选：{json.dumps({**research, 'agent_recommendations': recommendations}, ensure_ascii=False)}"
        )
        try:
            async with httpx.AsyncClient(timeout=min(self.settings.ollama_timeout_seconds, 45)) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                payload = _parse_unfiltered_json_object(response.json().get("response", ""))
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        selected = []
        for item in payload.get("selected_attractions", [])[:30]:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            selected.append(
                {
                    "name": str(item.get("name")).strip()[:120],
                    "destination": str(item.get("destination") or "").strip()[:80],
                    "area": str(item.get("area") or "").strip()[:80],
                    "reason": str(item.get("reason") or "").strip()[:240],
                }
            )
        days = []
        for item in payload.get("day_plans", [])[:30]:
            if not isinstance(item, dict):
                continue
            try:
                day = int(item.get("day"))
            except (TypeError, ValueError):
                continue
            names = _normalize_text_list(item.get("attraction_names"), maximum=12)
            days.append(
                {
                    "day": max(1, day),
                    "focus": str(item.get("focus") or "").strip()[:160],
                    "area": str(item.get("area") or "").strip()[:80],
                    "attraction_names": names,
                    "meal_notes": _normalize_text_list(item.get("meal_notes"), maximum=6),
                    "overnight_area": str(item.get("overnight_area") or "").strip()[:100],
                    "reason": str(item.get("reason") or "").strip()[:240],
                }
            )
        return {
            "strategy": str(payload.get("strategy") or "").strip()[:500],
            "selected_attractions": selected,
            "day_plans": days,
            "unresolved_questions": _normalize_text_list(
                payload.get("unresolved_questions"), maximum=8
            ),
            "source": "destination_plan_agent",
        }


class OllamaPoiRanker:
    """Rank discovered POIs from the Agent's semantic requirements.

    The local scorer is deliberately objective (distance, rating and price).
    Preference words are not interpreted with a Python keyword table; when an
    Ollama key is available this Agent decides the trade-offs and supplies the
    human-readable reason shown in the UI.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def rank(
        self,
        candidates: dict[str, list[dict[str, Any]]],
        preferences: list[str],
        special_events: list[str],
        *,
        travel_start: str | None = None,
        travel_end: str | None = None,
        destination_research: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.settings.ollama_api_key:
            return []
        compact: list[dict[str, Any]] = []
        for category, items in candidates.items():
            for item in items[:40]:
                place = item.get("place") or {}
                compact.append(
                    {
                        "candidate_id": item.get("candidate_id"),
                        "category": category,
                        "name": place.get("name"),
                        "categories": item.get("categories"),
                        "description": item.get("description"),
                        "rating": item.get("rating"),
                        "distance_km": item.get("distance_km"),
                        "price": item.get("ticket_or_price"),
                    }
                )
        if not compact:
            return []
        prompt = (
            "Travel dates: "
            f"{travel_start or 'unknown'} to {travel_end or travel_start or 'unknown'}. "
            "Assess seasonal_fit for every candidate; reject clearly off-season outdoor activities, "
            "but keep indoor or all-season venues when provider details support them. "
            "Return seasonal_fit and seasonal_reason in each decision. "
            f"Destination research evidence (use it to prioritize famous source-backed places, never as a hard-coded list): {json.dumps(destination_research or {}, ensure_ascii=False)}. "
            "你是 RoadMan POI 行程策展 Agent。请根据已经由 Requirement Agent 提取的偏好、特殊体验、"
            "距离、评分、价格和类别，为候选景点、住宿、餐饮排序。不要从原始用户文本猜测偏好，"
            "也不要凭空创造候选；只返回候选 ID 的 JSON 决策。可以遗漏不适合的候选。"
            "返回格式：{\"decisions\":[{\"candidate_id\":\"...\",\"score\":0,"
            "\"reason\":\"简短中文理由\"}]}。score 为 0-100 的相对匹配度。"
            f"偏好：{json.dumps(preferences, ensure_ascii=False)}；"
            f"特殊体验：{json.dumps(special_events, ensure_ascii=False)}；"
            f"候选：{json.dumps(compact, ensure_ascii=False)}"
        )
        try:
            async with httpx.AsyncClient(timeout=min(self.settings.ollama_timeout_seconds, 45)) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                payload = _parse_unfiltered_json_object(response.json().get("response", ""))
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return []
        decisions = payload.get("decisions")
        if not isinstance(decisions, list):
            return []
        valid_ids = {str(item.get("candidate_id")) for item in compact}
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in decisions:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("candidate_id") or "")
            if not candidate_id or candidate_id not in valid_ids or candidate_id in seen:
                continue
            try:
                score = float(item.get("score"))
            except (TypeError, ValueError):
                continue
            seen.add(candidate_id)
            cleaned.append(
                {
                    "candidate_id": candidate_id,
                    "score": max(0.0, min(100.0, score)),
                    "seasonal_fit": (
                        bool(item.get("seasonal_fit"))
                        if isinstance(item.get("seasonal_fit"), bool)
                        else None
                    ),
                    "seasonal_reason": str(item.get("seasonal_reason") or "").strip()[:160],
                    "reason": str(item.get("reason") or "Agent 综合偏好、距离与数据质量").strip()[:120],
                }
            )
        return cleaned


class OllamaPoiSuitabilityAgent:
    """Review every candidate against the actual travel conditions.

    Ranking answers "which option is attractive".  This pass answers the
    stricter question "can this specific option reasonably be visited on the
    requested dates" using the candidate metadata, forecast, terrain and the
    already extracted user preferences.  A missing/invalid answer is ignored
    so the deterministic planner can keep working offline.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def review(
        self,
        candidates: dict[str, list[dict[str, Any]]],
        trip_request: dict[str, Any],
        day_plans: list[dict[str, Any]],
        weather_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not self.settings.ollama_api_key:
            return []
        compact: list[dict[str, Any]] = []
        for category, items in candidates.items():
            for item in items:
                place = item.get("place") or {}
                coordinates = place.get("coordinates") or {}
                compact.append(
                    {
                        "candidate_id": item.get("candidate_id"),
                        "category": category,
                        "name": place.get("name"),
                        "address": place.get("address"),
                        "description": item.get("description"),
                        "categories": item.get("categories") or item.get("kinds"),
                        "rating": item.get("rating"),
                        "elevation_m": item.get("elevation_m"),
                        "coordinates": coordinates,
                        "provider": item.get("provider"),
                        "opening_hours": item.get("opening_hours"),
                    }
                )
        if not compact:
            return []
        weather = _compact_weather_centers(weather_results)
        candidate_weather = _match_candidate_weather(compact, weather)
        route_context = [
            {
                "day": day.get("date"),
                "stages": [
                    {
                        "destination": (stage.get("destination") or {}).get("name"),
                        "elevation_gain_m": stage.get("elevation_gain_m"),
                        "weather": stage.get("weather_summary"),
                    }
                    for stage in day.get("stages", [])
                ],
            }
            for day in day_plans
        ]
        prompt = (
            "You are RoadMan's POI safety and suitability Agent. Review EVERY "
            "candidate independently; do not use a fixed month table. Consider "
            "the requested date range, forecast temperature/precipitation/wind, "
            "elevation and terrain, opening information, activity characteristics, "
            "travel mode, party size and the user's preferences/special events. "
            "A seasonal activity can still be suitable when the provider details, "
            "indoor setting or local conditions support it. Mark unsuitable only "
            "when the evidence makes it unreasonable or unsafe. Return ONLY JSON: "
            '{"decisions":[{"candidate_id":"...","suitable":true,'
            '"confidence":"high|medium|low","reason":"中文依据",'
            '"weather_reason":"...","terrain_reason":"...",'
            '"personal_reason":"..."}]}。必须覆盖输入中的每个 candidate_id。'
            f"\n行程需求：{json.dumps({key: trip_request.get(key) for key in ('start_date', 'end_date', 'preferences', 'special_events', 'travelers', 'destination')}, ensure_ascii=False)}"
            f"\n候选：{json.dumps(compact, ensure_ascii=False)}"
            f"\n天气中心：{json.dumps(weather, ensure_ascii=False)}"
            f"\n候选对应天气：{json.dumps(candidate_weather, ensure_ascii=False)}"
            f"\n路线地形与阶段天气：{json.dumps(route_context, ensure_ascii=False)}"
        )
        try:
            async with httpx.AsyncClient(timeout=min(self.settings.ollama_timeout_seconds, 45)) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                payload = _parse_unfiltered_json_object(response.json().get("response", ""))
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return []
        decisions = payload.get("decisions")
        if not isinstance(decisions, list):
            return []
        valid_ids = {str(item.get("candidate_id")) for item in compact}
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in decisions:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("candidate_id") or "")
            suitable = item.get("suitable")
            if (
                not candidate_id
                or candidate_id not in valid_ids
                or candidate_id in seen
                or not isinstance(suitable, bool)
            ):
                continue
            confidence = str(item.get("confidence") or "low").lower()
            if confidence not in {"high", "medium", "low"}:
                confidence = "low"
            seen.add(candidate_id)
            cleaned.append(
                {
                    "candidate_id": candidate_id,
                    "suitable": suitable,
                    "confidence": confidence,
                    "reason": str(item.get("reason") or "Agent 综合日期、天气、地形与偏好复核").strip()[:240],
                    "weather_reason": str(item.get("weather_reason") or "").strip()[:160],
                    "terrain_reason": str(item.get("terrain_reason") or "").strip()[:160],
                    "personal_reason": str(item.get("personal_reason") or "").strip()[:160],
                }
            )
        return cleaned


def _compact_weather_centers(weather_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for result in weather_results[:30]:
        samples = [sample for sample in result.get("hourly_samples", []) if isinstance(sample, dict)]
        temperatures = [sample.get("temperature_c") for sample in samples if isinstance(sample.get("temperature_c"), (int, float))]
        precipitation = [sample.get("precipitation_probability") for sample in samples if isinstance(sample.get("precipitation_probability"), (int, float))]
        winds = [sample.get("wind_speed_kmh") for sample in samples if isinstance(sample.get("wind_speed_kmh"), (int, float))]
        compact.append(
            {
                "latitude": result.get("latitude"),
                "longitude": result.get("longitude"),
                "elevation_m": result.get("elevation_m"),
                "temperature_range_c": [min(temperatures), max(temperatures)] if temperatures else None,
                "precipitation_probability_max": max(precipitation) if precipitation else None,
                "wind_speed_max_kmh": max(winds) if winds else None,
                "sample_count": len(samples),
            }
        )
    return compact


def _match_candidate_weather(
    candidates: list[dict[str, Any]],
    weather_centers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach the nearest forecast/elevation center to every candidate."""
    matched: list[dict[str, Any]] = []
    for candidate in candidates:
        coordinates = candidate.get("coordinates") or {}
        try:
            longitude = float(coordinates["longitude"])
            latitude = float(coordinates["latitude"])
        except (KeyError, TypeError, ValueError):
            matched.append({"candidate_id": candidate.get("candidate_id"), "weather": None})
            continue
        nearest = min(
            weather_centers,
            key=lambda center: (
                (float(center.get("longitude") or 0) - longitude) ** 2
                + (float(center.get("latitude") or 0) - latitude) ** 2
            ),
            default=None,
        )
        matched.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "weather": nearest,
            }
        )
    return matched


class OllamaEventResearchAgent:
    """Extract source-backed facts for a user-requested seasonal event.

    Web search only supplies evidence.  This Agent turns the snippets into a
    small structured record while being explicitly forbidden from filling an
    unknown date/time from memory.  The caller can therefore show the exact
    peak window and ask the user to choose dates around it.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def extract(
        self,
        event: str,
        year: int,
        sources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.settings.ollama_api_key or not sources:
            return {}
        compact = [
            {
                "index": index,
                "title": str(source.get("title") or ""),
                "snippet": str(source.get("snippet") or ""),
                "url": str(source.get("url") or ""),
            }
            for index, source in enumerate(sources[:8])
        ]
        prompt = (
            "你是 RoadMan 事件事实核验 Agent。只根据下面公开网页标题、摘要和链接提取事实，"
            "不要依靠记忆补全，也不要把搜索词当成事实。返回且只能返回一个 JSON 对象："
            '{"peak_start_date":"YYYY-MM-DD或null","peak_end_date":"YYYY-MM-DD或null",'
            '"peak_time_utc":"HH:MM或null","peak_time_local":"HH:MM或null",'
            '"peak_time_label":"来源原文中的时间表述或null",'
            '"observation_window_local":"中文观测窗口或null","active_period":"活动期或null",'
            '"zhr":null,"confidence":"high|medium|low",'
            '"evidence_source_indexes":[0],"summary":"有证据支持的中文事实摘要"}。'
            f"事件：{event}；目标年份：{year}。日期必须确实出现在来源中；只有来源明确给出时才填日期或时间，"
            "如果来源互相矛盾，保留 null 并在 summary 说明需要临近出发复核。peak_time_local 使用北京时间/"
            "当地时间的明确时刻；不能把‘夜间’臆造为具体小时。若来源给出了 02:00、上午/中午等具体"
            "表述但时区不清，保留在 peak_time_label，不要强行转换成北京时间。zhr 只能填来源明确的数字。"
            f"来源：{json.dumps(compact, ensure_ascii=False)}"
        )
        try:
            async with httpx.AsyncClient(timeout=min(self.settings.ollama_timeout_seconds, 45)) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    headers={"Authorization": f"Bearer {self.settings.ollama_api_key}"},
                    json={
                        "model": self.settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "think": False,
                        "format": "json",
                    },
                )
                response.raise_for_status()
                payload = _parse_unfiltered_json_object(response.json().get("response", ""))
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        return _clean_event_facts(payload, year, len(compact))


def _clean_event_facts(value: dict[str, Any], year: int, source_count: int) -> dict[str, Any]:
    """Validate the event Agent's source-backed JSON before exposing it."""
    facts: dict[str, Any] = {}
    for field in ("peak_start_date", "peak_end_date"):
        raw = value.get(field)
        if isinstance(raw, str):
            try:
                parsed = date.fromisoformat(raw.strip())
            except ValueError:
                parsed = None
            if parsed is not None and parsed.year == year:
                facts[field] = parsed.isoformat()
    for field in ("peak_time_utc", "peak_time_local"):
        normalized = _normalize_clock(value.get(field))
        if normalized:
            facts[field] = normalized
    for field in ("peak_time_label", "observation_window_local", "active_period", "summary"):
        raw = value.get(field)
        if isinstance(raw, str) and raw.strip():
            facts[field] = raw.strip()[:500]
    confidence = str(value.get("confidence") or "low").lower().strip()
    facts["confidence"] = confidence if confidence in {"high", "medium", "low"} else "low"
    indexes = value.get("evidence_source_indexes")
    if isinstance(indexes, list):
        facts["evidence_source_indexes"] = [
            int(index)
            for index in indexes
            if isinstance(index, int) and 0 <= index < source_count
        ][:8]
    zhr = value.get("zhr")
    if isinstance(zhr, (int, float)) and not isinstance(zhr, bool) and 0 <= zhr <= 10000:
        facts["zhr"] = zhr
    return facts


def _parse_json_object(text: str) -> dict[str, Any]:
    value = _parse_unfiltered_json_object(text)
    allowed = {
        "origin_name",
        "destination_name",
        "destination_names",
        "destination_scope",
        "travel_intents",
        "start_date",
        "end_date",
        "departure_time",
        "return_time",
        "travelers",
        "preferences",
        "transport_modes",
        "special_events",
        "max_days",
        "issues",
        "cross_sea_required",
        "cross_sea_mode",
        "past_return_requested",
        "time_window_minutes",
        "stay_only_at_destination",
        "must_visit_names",
    }
    return {key: value for key, value in value.items() if key in allowed}


def _looks_like_serialized_list(value: str) -> bool:
    text = value.strip()
    return len(text) >= 2 and text[0] == "[" and text[-1] == "]"


def _normalize_text_list(value: Any, *, maximum: int = 40) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        text = re.sub(r"\s+", " ", item).strip(" \t\r\n，,。；;、:：")
        if not text or text in seen:
            continue
        result.append(text[:120])
        seen.add(text)
        if len(result) >= maximum:
            break
    return result


def _normalize_destination_names(value: Any) -> list[str]:
    return _normalize_text_list(value, maximum=20)


def _destination_payload_needs_repair(value: dict[str, Any]) -> bool:
    """Reject ambiguous model shapes before anything reaches geocoding."""
    destination_name = value.get("destination_name")
    if destination_name is not None and (
        not isinstance(destination_name, str)
        or _looks_like_serialized_list(destination_name)
    ):
        return True
    destination_names = value.get("destination_names")
    if destination_names is not None and (
        not isinstance(destination_names, list)
        or any(not isinstance(item, str) or not item.strip() for item in destination_names)
    ):
        return True
    return False


def _parse_unfiltered_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("LLM response does not contain JSON")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("LLM response must be a JSON object")
    return value


def _has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def _merge_extraction(base: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in llm.items():
        if value not in (None, "", []):
            result[key] = value
    return result


def _extract_literal_constraints(raw_text: str, today: date) -> dict[str, Any]:
    """Extract only unambiguous numeric calendar literals for validation."""
    explicit_dates = [
        date.fromisoformat(value).isoformat()
        for value in re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", raw_text)
    ]
    for month, day_value in re.findall(
        r"(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)", raw_text
    ):
        try:
            explicit_dates.append(
                date(today.year, int(month), int(day_value)).isoformat()
            )
        except ValueError:
            continue
    if not explicit_dates:
        for year, month, day_value in re.findall(
            r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日", raw_text
        ):
            try:
                explicit_dates.append(
                    date(
                        int(year) if year else today.year,
                        int(month),
                        int(day_value),
                    ).isoformat()
                )
            except ValueError:
                continue
    result: dict[str, Any] = {}
    if explicit_dates:
        result["start_date"] = explicit_dates[0]
        if len(explicit_dates) > 1:
            result["end_date"] = explicit_dates[1]
    return result


def _normalize_transport_modes(value: Any) -> list[str]:
    """Keep only transport mode decisions returned by the Requirement Agent."""
    allowed = {"driving", "train", "flight", "ferry", "transit", "walking", "riding"}
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        mode = str(item or "").strip().lower()
        if mode in {"ship", "boat", "ferryboat", "轮船", "渡轮", "船"}:
            mode = "ferry"
        if mode in allowed and mode not in result:
            result.append(mode)
    return result


_WEEKDAY_PATTERN = re.compile(r"(?P<scope>下周|下星期|本周|这周|周|星期)(?P<day>[一二三四五六日天])")
_WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_ENGLISH_WEEKDAY_PATTERN = re.compile(
    r"(?P<scope>next\s+week\s+|next\s+|this\s+)?"
    r"(?P<day>monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
    re.IGNORECASE,
)
_ENGLISH_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_RELATIVE_DAY_OFFSETS = {
    "大后天": 3,
    "后天": 2,
    "明天": 1,
    "今天": 0,
    "昨天": -1,
    "前天": -2,
    "the day after tomorrow": 2,
    "day after tomorrow": 2,
    "tomorrow": 1,
    "today": 0,
    "yesterday": -1,
    "day before yesterday": -2,
}


def _weekday_date(match: re.Match[str], today: date) -> date:
    """Resolve one Chinese weekday token without interpreting travel intent."""
    monday = today - timedelta(days=today.weekday())
    candidate = monday + timedelta(days=_WEEKDAY_INDEX[match.group("day")])
    scope = match.group("scope")
    if scope in {"下周", "下星期"}:
        candidate += timedelta(days=7)
    elif scope in {"周", "星期"} and candidate < today:
        candidate += timedelta(days=7)
    return candidate


def _english_weekday_date(match: re.Match[str], today: date) -> date:
    monday = today - timedelta(days=today.weekday())
    day = match.group("day").casefold()
    candidate = monday + timedelta(days=_ENGLISH_WEEKDAY_INDEX[day])
    scope = (match.group("scope") or "").strip().casefold()
    if scope in {"next", "next week"}:
        candidate += timedelta(days=7)
    elif scope == "" and candidate < today:
        candidate += timedelta(days=7)
    return candidate


def extract_structural_constraints(raw_text: str, today: date) -> dict[str, Any]:
    """Extract only deterministic calendar structure for Agent fallback/validation.

    This deliberately does not classify destinations, experiences, party size or
    preferences.  A weekday pair such as “周一出发、周五回来” is a calendar
    constraint, so resolving it here prevents a transient Agent failure from
    asking the user for a date that is already unambiguous in the request.
    """
    result = _extract_literal_constraints(raw_text, today)

    # An explicit request to cross a water barrier is a safety constraint, not
    # a destination or preference guess.  Preserve it across clarification
    # rounds even when the cloud Requirement Agent is unavailable; do not
    # infer it from names such as "海岛" or from a geography dictionary.
    if re.search(r"跨海|跨水|过海|渡海|跨海峡", raw_text or ""):
        result["cross_sea_required"] = True
        explicit_mode = _explicit_cross_sea_mode(raw_text or "")
        if explicit_mode:
            result["cross_sea_mode"] = explicit_mode

    explicit_window = _extract_explicit_time_window(raw_text or "")
    if explicit_window:
        result.update(explicit_window)

    relative = list(
        re.finditer(
            r"大后天|后天|明天|今天|昨天|前天|the day after tomorrow|day after tomorrow|"
            r"day before yesterday|tomorrow|today|yesterday",
            raw_text,
            re.IGNORECASE,
        )
    )
    if relative:
        relative_dates = [
            today + timedelta(days=_RELATIVE_DAY_OFFSETS[item.group(0).casefold()])
            for item in relative[:2]
        ]
        if "start_date" not in result:
            result["start_date"] = relative_dates[0].isoformat()
        if len(relative_dates) > 1 and "end_date" not in result:
            result["end_date"] = relative_dates[1].isoformat()

    weekday_matches = list(_WEEKDAY_PATTERN.finditer(raw_text))
    english_weekday_matches = list(_ENGLISH_WEEKDAY_PATTERN.finditer(raw_text))
    weekday_dates = [
        *[_weekday_date(match, today) for match in weekday_matches[:2]],
        *[_english_weekday_date(match, today) for match in english_weekday_matches[:2]],
    ]
    weekend_requested = bool(
        re.search(r"周末|本周末|这个周末|this\s+weekend|weekend", raw_text, re.IGNORECASE)
    )
    if weekend_requested:
        monday = today - timedelta(days=today.weekday())
        saturday = monday + timedelta(days=5)
        if saturday < today:
            saturday += timedelta(days=7)
        # “周末/周日” is a common shorthand. Prefer the complete weekend
        # window over a second regex match for 周日 so it cannot become a
        # Sunday-to-next-Saturday range.
        weekday_dates = [saturday, saturday + timedelta(days=1)]
    if weekday_dates:
        weekday_dates = weekday_dates[:2]
        if len(weekday_dates) > 1 and weekday_dates[1] <= weekday_dates[0]:
            # A bare second weekday in a range belongs after the first one,
            # including ranges that cross the Sunday/Monday boundary.
            second = weekday_dates[1]
            while second <= weekday_dates[0]:
                second += timedelta(days=7)
            weekday_dates[1] = second
        if "start_date" not in result:
            result["start_date"] = weekday_dates[0].isoformat()
        if len(weekday_dates) > 1 and "end_date" not in result:
            result["end_date"] = weekday_dates[1].isoformat()

    return result


def _clean_explicit_place(value: str) -> str | None:
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n，,。；;、:：")
    value = re.sub(r"(?:及其周边|周边)$", "", value).strip()
    value = re.sub(r"(?:早上|上午|中午|下午|晚上|夜间)$", "", value).strip()
    if not value or len(value) > 64:
        return None
    return value


def extract_explicit_location_constraints(raw_text: str) -> dict[str, str]:
    """Recover only explicit origin/destination grammar when the Agent is down.

    The fallback intentionally does not contain a city/POI dictionary and does
    not infer a destination from an experience (for example, “看流星雨”).
    It is limited to common travel clauses and is used only for fields the
    semantic Agent did not return.
    """
    text = re.sub(r"\s+", " ", raw_text or "").strip()
    if not text:
        return {}

    result: dict[str, str] = {}

    # A very common natural form is “从 A 坐/乘……去 B”.  The previous
    # fallback only understood “从 A 出发” and therefore missed the origin in
    # this form.  Keep the recovery grammar transport-agnostic: it looks for a
    # travel verb between the two places rather than maintaining a city list.
    transport_origin_match = re.search(
        r"从\s*(?P<origin>[^，,。；;、]+?)\s*"
        r"(?=(?:坐|乘坐|乘|搭乘|搭|开车|自驾|骑车|骑行|步行)"
        r"\s*[^，,。；;、]{0,12}?(?:去|到|前往))",
        text,
    )
    origin_match = transport_origin_match or re.search(
        r"从\s*(?P<origin>[^，,。；;、]+?)\s*(?:出发|启程|开始(?:行程)?|出游|到)",
        text,
    )
    if origin_match:
        origin = _clean_explicit_place(origin_match.group("origin"))
        if origin:
            result["origin_name"] = origin

    # “去/前往 B 看/玩…” and “在 B 及其周边…” are explicit destination
    # clauses.  Stop before the experience so it is not mistaken for a POI.
    destination_patterns = (
        # Do not treat bare “到” as a destination marker: in “周日到下周三”
        # it is a date-range connector, not a place.  “去/前往/抵达” are
        # unambiguous travel clauses here; “玩” also stops “去成都玩三天”
        # before the duration text.
        r"(?:去|前往|抵达|游览|参观)\s*(?P<destination>[^，,。；;、]+?)"
        r"(?=及其周边|周边|看|赏|游玩|玩|旅游|转转|参观|住宿|停留|度假|出游|[，,。；;]|$)",
        r"在\s*(?P<destination>[^，,。；;、]+?)"
        r"(?=及其周边|周边|旅游|游玩|转转|住宿|停留|度假|出游|[，,。；;]|$)",
    )
    for pattern in destination_patterns:
        destination_match = re.search(pattern, text)
        if not destination_match:
            continue
        destination = _clean_explicit_place(destination_match.group("destination"))
        if destination and destination != result.get("origin_name"):
            result["destination_name"] = destination
            break

    # Also support concise “从 A 到 B” and English “from A to B” clauses.
    if "origin_name" not in result or "destination_name" not in result:
        compact_match = re.search(
            r"从\s*(?P<origin>[^，,。；;、]+?)\s*到\s*(?P<destination>[^，,。；;、]+?)"
            r"(?=看|赏|游玩|旅游|转转|及其周边|周边|[，,。；;]|$)",
            text,
        )
        english_match = re.search(
            r"\bfrom\s+(?P<origin>[^,.;]+?)\s+to\s+(?P<destination>[^,.;]+?)"
            r"(?=\s+(?:for|with|on)\b|[,.;]|$)",
            text,
            re.IGNORECASE,
        )
        match = compact_match or english_match
        if match:
            origin = _clean_explicit_place(match.group("origin"))
            destination = _clean_explicit_place(match.group("destination"))
            if origin and "origin_name" not in result:
                result["origin_name"] = origin
            if destination and "destination_name" not in result:
                result["destination_name"] = destination

    return result


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "是", "需要"}:
            return True
        if normalized in {"false", "no", "0", "否", "不需要"}:
            return False
    return None


def _coerce_positive_minutes(value: Any) -> int | None:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if 1 <= minutes <= 7 * 24 * 60 else None


def _coerce_time_window_minutes(value: Any) -> int | None:
    """Validate an explicit travel window, including a zero-minute conflict."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if 0 <= minutes <= 7 * 24 * 60 else None


def _normalize_place_name(value: str) -> str:
    """Normalize whitespace without interpreting place language."""
    return " ".join(value.split()).strip("，,。；;、")


def _normalize_special_events(value: Any) -> list[str]:
    events = [str(item).strip() for item in value] if isinstance(value, list) else []
    return list(dict.fromkeys(item for item in events if item))[:8]


def _coerce_travelers(value: Any) -> int | None:
    """Validate the numeric count returned by the Requirement Agent."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 1 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number >= 1 else None
    return None


def _coerce_positive_int(value: Any, *, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= maximum else None
    if isinstance(value, float) and value.is_integer():
        integer = int(value)
        return integer if 1 <= integer <= maximum else None
    if isinstance(value, str) and value.strip().isdigit():
        integer = int(value.strip())
        return integer if 1 <= integer <= maximum else None
    return None


def _normalize_clock(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


_CLOCK_LITERAL = (
    r"(?:(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|夜间)\s*)?"
    r"\d{1,2}(?:(?::\d{1,2})|(?:点|时)(?:\d{1,2}分?)?)"
)


def _clock_literal_to_minutes(value: str) -> int | None:
    match = re.fullmatch(
        r"\s*(?P<period>凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|夜间)?\s*"
        r"(?P<hour>\d{1,2})(?:(?::(?P<colon_minute>\d{1,2}))|"
        r"(?:点|时)(?P<cn_minute>\d{1,2})?分?)\s*",
        value,
    )
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("colon_minute") or match.group("cn_minute") or 0)
    if hour > 23 or minute > 59:
        return None
    period = match.group("period") or ""
    if period in {"下午", "傍晚", "晚上", "中午"} and hour < 12:
        hour += 12
    elif period in {"凌晨"} and hour == 12:
        hour = 0
    return hour * 60 + minute


def _extract_explicit_time_window(raw_text: str) -> dict[str, Any]:
    """Read an explicit same-leg departure/arrival clock window.

    This is a structural safety check, not a semantic classifier.  It only
    activates when the text contains two clock literals connected to an
    explicit departure and arrival phrase, so ordinary times in a day plan do
    not become a guessed travel constraint.
    """
    match = re.search(
        rf"(?P<start>{_CLOCK_LITERAL})\s*(?:出发|启程|离开)"
        rf".{{0,32}}?(?:到|抵达|到达)\s*(?P<end>{_CLOCK_LITERAL})",
        raw_text or "",
    )
    if not match:
        return {}
    start_minutes = _clock_literal_to_minutes(match.group("start"))
    end_minutes = _clock_literal_to_minutes(match.group("end"))
    if start_minutes is None or end_minutes is None:
        return {}
    elapsed = end_minutes - start_minutes
    if elapsed < 0:
        elapsed += 24 * 60
    return {
        "departure_time": f"{start_minutes // 60:02d}:{start_minutes % 60:02d}",
        "return_time": f"{end_minutes // 60:02d}:{end_minutes % 60:02d}",
        "time_window_minutes": elapsed,
    }


def _explicit_cross_sea_mode(raw_text: str) -> str | None:
    """Return a water-crossing mode only when the user explicitly states it."""
    if re.search(r"轮渡|渡轮|坐船|乘船|船运|ferry|boat|ship", raw_text, re.IGNORECASE):
        return "ferry"
    if re.search(r"飞机|航班|飞过去|flight|plane", raw_text, re.IGNORECASE):
        return "flight"
    if re.search(r"跨海大桥|大桥通行|bridge", raw_text, re.IGNORECASE):
        return "bridge"
    return None


# Compatibility symbol retained for callers that import the old helper. The
# implementation is intentionally Agent-free: only literal dates are kept.
def deterministic_extract(raw_text: str, today: date) -> dict[str, Any]:
    return _extract_literal_constraints(raw_text, today)
