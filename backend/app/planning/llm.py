import json
import re
from datetime import date
from typing import Any

import httpx

from ..core.config import Settings


class OllamaRequirementExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def extract(self, raw_text: str, today: date) -> dict[str, Any]:
        # The model owns semantic interpretation. The offline parser is now
        # limited to literal calendar validation and never extracts places,
        # weekdays, preferences, party size or travel modes.
        legacy_fallback = deterministic_extract(raw_text, today)
        if (
            not self.settings.enable_llm_requirement_extraction
            or not self.settings.ollama_api_key
        ):
            # No cloud Agent means no semantic guess.  Keep only hard,
            # structural fields from the offline parser and let preflight ask
            # the user instead of silently inventing party size/preferences.
            return legacy_fallback
        prompt = (
            "You are RoadMan Requirement Agent. Extract requirements only; never plan a route. "
            f"Today is {today.isoformat()}. Return ONLY one valid JSON object (no markdown, no explanation) "
            "with exactly these keys: origin_name, destination_name, start_date, end_date, "
            "departure_time, return_time, travelers, max_days, preferences, special_events, "
            "cross_sea_required, cross_sea_mode, past_return_requested, time_window_minutes. "
            "Dates must be YYYY-MM-DD; unknown scalar fields must be null and preferences/special_events must be arrays. "
            "Only fill start_date/end_date when the user's text explicitly gives a calendar date, a relative date "
            "such as 今天/明天, or an unambiguous weekday. Phrases such as 今年暑假、最多三天、玩三天 are not "
            "calendar dates; leave both date fields null instead of inventing dates. "
            "Normalize Chinese time phrases: 中午=12:00, 下午=14:00, 晚上=19:00. "
            "Understand relative weekdays and ranges as a pair: 周一出发、周五回来 means a Monday-to-Friday "
            "window in the same upcoming week; do not return an end date earlier than the start date. "
            "Infer travelers semantically (情侣/夫妻=2, 一家三口=3); do not default to 1 when evidence is absent. "
            "请根据语义判断同行人数，不要机械默认 1。"
            "cross_sea_required must be true only when the trip actually requires crossing a sea or water barrier; "
            "cross_sea_mode may be ferry, flight, bridge or null. past_return_requested is true only when the user "
            "explicitly requests returning before the current date. time_window_minutes is the user's explicit "
            "departure-to-arrival window, or null when no such window is stated. "
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
                structural = _extract_literal_constraints(raw_text, today)
                merged = _merge_extraction(structural, parsed)
                # Literal calendar tokens are hard user constraints. Do not
                # let the Agent hallucinate another year.
                for date_field in ("start_date", "end_date"):
                    if structural.get(date_field):
                        merged[date_field] = structural[date_field]
                if not merged.get("start_date"):
                    merged.pop("end_date", None)
                if merged.get("destination_name"):
                    merged["destination_name"] = _normalize_place_name(
                        str(merged["destination_name"])
                    )
                merged["special_events"] = _normalize_special_events(merged.get("special_events"))
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
                max_days = _coerce_positive_int(merged.get("max_days"), maximum=30)
                if max_days is None:
                    merged.pop("max_days", None)
                else:
                    merged["max_days"] = max_days
                merged["cross_sea_required"] = _coerce_optional_bool(
                    merged.get("cross_sea_required")
                )
                if merged.get("cross_sea_mode") not in {"ferry", "flight", "bridge"}:
                    merged["cross_sea_mode"] = None
                merged["past_return_requested"] = _coerce_optional_bool(
                    merged.get("past_return_requested")
                )
                merged["time_window_minutes"] = _coerce_positive_minutes(
                    merged.get("time_window_minutes")
                )
                return merged
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return legacy_fallback


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
            '"reply":"给用户的简短中文说明"}。'
            "如果用户要求增加或减少天数、重排整体路线、改变出发/返回日期，intent 用 replan。"
            "如果是加入/删除/替换一个已有候选，尽量填出对应第几天和名称；不确定时留空，"
            "优先返回上下文中的 day_id、阶段 ID、活动 ID、候选 ID；名称仅用于精确核对，禁止返回不存在的 ID。"
            "不要把‘看流星雨’、‘赏花’、‘泡温泉’等体验目标误当成景点名称。"
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
                        "rating": item.get("rating"),
                        "distance_km": item.get("distance_km"),
                        "price": item.get("ticket_or_price"),
                    }
                )
        if not compact:
            return []
        prompt = (
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
                    "reason": str(item.get("reason") or "Agent 综合偏好、距离与数据质量").strip()[:120],
                }
            )
        return cleaned


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
        "start_date",
        "end_date",
        "departure_time",
        "return_time",
        "travelers",
        "preferences",
        "special_events",
        "max_days",
        "issues",
        "cross_sea_required",
        "cross_sea_mode",
        "past_return_requested",
        "time_window_minutes",
    }
    return {key: value for key, value in value.items() if key in allowed}


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


# Compatibility symbol retained for callers that import the old helper. The
# implementation is intentionally Agent-free: only literal dates are kept.
def deterministic_extract(raw_text: str, today: date) -> dict[str, Any]:
    return _extract_literal_constraints(raw_text, today)
