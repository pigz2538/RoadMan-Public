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
        deterministic = deterministic_extract(raw_text, today)
        if (
            not self.settings.enable_llm_requirement_extraction
            or not self.settings.ollama_api_key
        ):
            # No cloud Agent means no semantic guess.  Keep only hard,
            # structural fields from the offline parser and let preflight ask
            # the user instead of silently inventing party size/preferences.
            return deterministic
        prompt = (
            "You are RoadMan Requirement Agent. Extract requirements only; never plan a route. "
            f"Today is {today.isoformat()}. Return ONLY one valid JSON object (no markdown, no explanation) "
            "with exactly these keys: origin_name, destination_name, start_date, end_date, "
            "departure_time, return_time, travelers, max_days, preferences, special_events. "
            "Dates must be YYYY-MM-DD; unknown scalar fields must be null and preferences/special_events must be arrays. "
            "Only fill start_date/end_date when the user's text explicitly gives a calendar date, a relative date "
            "such as 今天/明天, or an unambiguous weekday. Phrases such as 今年暑假、最多三天、玩三天 are not "
            "calendar dates; leave both date fields null instead of inventing dates. "
            "Normalize Chinese time phrases: 中午=12:00, 下午=14:00, 晚上=19:00. "
            "Infer travelers semantically (情侣/夫妻=2, 一家三口=3); do not default to 1 when evidence is absent. "
            "请根据语义判断同行人数，不要机械默认 1。"
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
                merged = _merge_extraction(deterministic, parsed)
                # Calendar tokens and explicit clock hints are deterministic
                # user constraints. Do not let an Agent hallucinate another
                # year while still allowing it to infer semantic fields.
                for date_field in ("start_date", "end_date"):
                    if deterministic.get(date_field):
                        merged[date_field] = deterministic[date_field]
                # The Requirement Agent may understand duration or season
                # semantically, but it must never turn those phrases into a
                # fabricated calendar date.  Dates are retained only when
                # the structural parser found an explicit user constraint.
                if not deterministic.get("start_date"):
                    merged.pop("start_date", None)
                    merged.pop("end_date", None)
                elif not deterministic.get("end_date"):
                    merged.pop("end_date", None)
                for place_field in ("origin_name", "destination_name"):
                    if deterministic.get(place_field):
                        merged[place_field] = deterministic[place_field]
                for clock_field in ("departure_time", "return_time"):
                    if deterministic.get(clock_field):
                        merged[clock_field] = deterministic[clock_field]
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
                has_explicit_travelers = bool(
                    re.search(r"[一二三四五六七八九十两\d]+\s*(?:人|位|口)", raw_text)
                )
                if has_explicit_travelers and deterministic.get("travelers"):
                    # An explicit count is a hard user constraint; semantic
                    # inference is only used when the user did not state one.
                    merged["travelers"] = deterministic["travelers"]
                else:
                    merged["travelers"] = _coerce_travelers(merged.get("travelers"))
                if merged.get("travelers") is None:
                    merged.pop("travelers", None)
                if deterministic.get("max_days") is not None:
                    merged["max_days"] = deterministic["max_days"]
                else:
                    max_days = _coerce_positive_int(merged.get("max_days"), maximum=30)
                    if max_days is None:
                        merged.pop("max_days", None)
                    else:
                        merged["max_days"] = max_days
                return merged
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return deterministic


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
            '"day_index":1,"category":"attractions|hotels|meals|null",'
            '"target_name":"","candidate_name":"","duration_delta_minutes":0,'
            '"reply":"给用户的简短中文说明"}。'
            "如果用户要求增加或减少天数、重排整体路线、改变出发/返回日期，intent 用 replan。"
            "如果是加入/删除/替换一个已有候选，尽量填出对应第几天和名称；不确定时留空，"
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
        return {
            "intent": intent,
            "day_index": day_index,
            "category": category,
            "target_name": str(value.get("target_name") or "").strip()[:120],
            "candidate_name": str(value.get("candidate_name") or "").strip()[:120],
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
        fallback = _fallback_poi_decisions(local_candidates, osm_items)
        if not self.settings.ollama_api_key or not osm_items:
            return fallback
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
                    return fallback
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
                return cleaned or fallback
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return fallback


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


def _fallback_poi_decisions(
    local_candidates: list[dict[str, Any]],
    osm_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    local_names = {
        str(item.get("place", {}).get("name") or "").replace(" ", "").lower()
        for item in local_candidates
    }
    decisions = []
    for item in osm_items:
        name = str(item.get("name") or "").strip()
        normalized = name.replace(" ", "").lower()
        target = next(
            (candidate for candidate in local_names if normalized and (normalized in candidate or candidate in normalized)),
            "",
        )
        decisions.append(
            {
                "source_id": str(item.get("id") or ""),
                "action": "merge" if target else ("add" if _has_cjk(name) else "skip"),
                "display_name_zh": name if _has_cjk(name) else "",
                "merge_target_name": target,
                "reason": "名称去重兜底" if target else "保留本地中文名称" if _has_cjk(name) else "等待 Agent 翻译",
            }
        )
    return decisions


def _has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def _merge_extraction(base: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in llm.items():
        if value not in (None, "", []):
            result[key] = value
    return result


def _resolve_weekday_window(
    raw_text: str,
    today: date,
    *,
    explicit_start: date | None = None,
) -> tuple[date | None, date | None]:
    """Resolve a weekday departure/return pair without swapping its order.

    ``周一出发，周五回来`` contains two independent weekday constraints.  The
    previous parser only used the first ``周X`` token and always moved a
    same-day match seven days forward.  On a Monday that turned an intended
    current-week trip into next Monday while an Agent-provided Friday return
    remained in the current week, producing an inverted date range.

    A pair is anchored to the next occurrence of the departure weekday (today
    is a valid occurrence).  The return weekday is then advanced from that
    departure by its weekday delta, with equal weekdays meaning a full week.
    ``下周X`` is treated explicitly as the following occurrence.
    """

    weekday_map = {
        "一": 0,
        "二": 1,
        "三": 2,
        "四": 3,
        "五": 4,
        "六": 5,
        "日": 6,
        "天": 6,
    }
    matches = list(re.finditer(r"(?:周|星期)([一二三四五六日天])", raw_text))
    if not matches:
        return None, None

    start_match = matches[0]
    start_weekday = weekday_map[start_match.group(1)]
    if explicit_start is not None:
        start = explicit_start
    else:
        start_delta = (start_weekday - today.weekday()) % 7
        prefix = raw_text[max(0, start_match.start() - 3) : start_match.start()]
        if "下周" in prefix or "下星期" in prefix:
            # A zero delta means the next week's occurrence, not today.
            start_delta = start_delta or 7
            start_delta += 7
        start = today + timedelta(days=start_delta)

    end_match = None
    return_markers = ("回来", "返回", "返程", "回程", "回到", "回家")
    for candidate in reversed(matches[1:]):
        tail = raw_text[candidate.end() : candidate.end() + 12]
        if any(marker in tail for marker in return_markers):
            end_match = candidate
            break
    if end_match is None and len(matches) >= 2:
        # Also support compact ranges such as “周一到周五”。
        end_match = matches[-1]
    if end_match is None:
        return start, None

    end_weekday = weekday_map[end_match.group(1)]
    day_delta = (end_weekday - start.weekday()) % 7
    if day_delta == 0:
        day_delta = 7
    return start, start + timedelta(days=day_delta)


def _is_weekday_token(value: str) -> bool:
    return bool(re.fullmatch(r"(?:周|星期)[一二三四五六日天]", value.strip()))


def deterministic_extract(raw_text: str, today: date) -> dict[str, Any]:
    result: dict[str, Any] = {"preferences": []}
    explicit_dates = [
        date.fromisoformat(value).isoformat()
        # Chinese characters are Unicode word characters, so ``\b`` does not
        # form a boundary between a date and the adjacent character (for
        # example ``2026-08-02从``).  Use digit lookarounds instead.
        for value in re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", raw_text)
    ]
    for month, day_value in re.findall(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)", raw_text):
        try:
            explicit_dates.append(date(today.year, int(month), int(day_value)).isoformat())
        except ValueError:
            continue
    if not explicit_dates:
        for year, month, day_value in re.findall(
            r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日",
            raw_text,
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
    if explicit_dates:
        result["start_date"] = explicit_dates[0]
        if len(explicit_dates) > 1:
            result["end_date"] = explicit_dates[1]
    if "今天" in raw_text and not result.get("start_date"):
        result["start_date"] = today.isoformat()
    elif "明天" in raw_text and not result.get("start_date"):
        result["start_date"] = (today + timedelta(days=1)).isoformat()
    elif "后天" in raw_text and not result.get("start_date"):
        result["start_date"] = (today + timedelta(days=2)).isoformat()
    if "昨天" in raw_text and any(word in raw_text for word in ("回", "返", "抵达", "到达")):
        result["end_date"] = (today - timedelta(days=1)).isoformat()
    departure_time, return_time = _extract_clock_preferences(raw_text)
    if departure_time:
        result["departure_time"] = departure_time
    if return_time:
        result["return_time"] = return_time
    route_match = re.search(
        r"从(?P<origin>[\u4e00-\u9fffA-Za-z0-9·]+?)(?:出发)?(?:去|到|前往)"
        r"(?P<destination>[\u4e00-\u9fffA-Za-z0-9·]+?)(?=看|赏|观|参加|体验|游玩|旅游|露营|拍摄|追|，|,|。|；|;|最多|三天|两天|一日|周[一二三四五六日天]|$)",
        raw_text,
    )
    if route_match:
        origin_name = route_match.group("origin")
        if not _is_weekday_token(origin_name):
            result["origin_name"] = origin_name
        result["destination_name"] = _normalize_place_name(route_match.group("destination"))
    else:
        origin_match = re.search(
            r"从(?P<origin>[\u4e00-\u9fffA-Za-z0-9·]{2,20}?)(?=出发|启程|去|前往|到)",
            raw_text,
        )
        destination_matches = re.findall(
            r"(?:去|前往|抵达|到达|到)"
            r"(?!今天|明天|后天|昨天|早上|上午|中午|下午|晚上|\d)"
            r"(?P<destination>[\u4e00-\u9fffA-Za-z0-9·]{2,20}?)"
            r"(?=，|,|。|；|;|两天|一日|周[一二三四五六日天]|$)",
            raw_text,
        )
        if origin_match:
            origin_name = origin_match.group("origin")
            if not _is_weekday_token(origin_name):
                result["origin_name"] = origin_name
        if destination_matches:
            result["destination_name"] = _normalize_place_name(destination_matches[-1])
    local_destination = re.search(
        r"在(?P<destination>[\u4e00-\u9fffA-Za-z0-9·]{2,30}?)(?=及其周边|周边地区|周边|附近|一带)",
        raw_text,
    )
    if local_destination:
        # A local “在 B 及其周边游览” clause is more specific than a later
        # “到 A 返程” clause, which otherwise looks like the destination.
        result["destination_name"] = _normalize_place_name(
            local_destination.group("destination")
        )
    if any(qualifier in raw_text for qualifier in ("及其周边", "周边地区", "周边", "附近", "一带")):
        result["preferences"].append("目的地周边")

    weekday_start, weekday_end = _resolve_weekday_window(
        raw_text,
        today,
        explicit_start=(
            date.fromisoformat(result["start_date"])
            if result.get("start_date")
            else None
        ),
    )
    if weekday_start and not result.get("start_date"):
        result["start_date"] = weekday_start.isoformat()
    if weekday_end and not result.get("end_date"):
        result["end_date"] = weekday_end.isoformat()

    nights_match = re.search(r"([一二三四五六七八九十两\d]+)天([一二三四五六七八九十两\d]+)夜", raw_text)
    if nights_match:
        days = _cn_number(nights_match.group(1))
        if days:
            result["max_days"] = days
        if result.get("start_date") and days:
            start = date.fromisoformat(result["start_date"])
            result["end_date"] = (start + timedelta(days=days - 1)).isoformat()

    duration_match = re.search(
        r"(?:最多|至多|不超过|不超過|最长|最長)\s*([一二三四五六七八九十两\d]+)\s*天",
        raw_text,
    )
    if duration_match:
        days = _cn_number(duration_match.group(1))
        if days:
            result["max_days"] = days
    if "max_days" not in result:
        plain_duration = re.search(
            r"([一二三四五六七八九十两\d]+)\s*天(?:行程|旅游|旅行|游玩|出游|游|玩)?",
            raw_text,
        )
        if plain_duration:
            days = _cn_number(plain_duration.group(1))
            if days:
                result["max_days"] = days

    traveler_match = re.search(r"([一二三四五六七八九十两\d]+)\s*(?:人|位|口)", raw_text)
    if traveler_match:
        result["travelers"] = _cn_number(traveler_match.group(1))
    # Semantic preferences are intentionally left to the Requirement Agent.
    # This offline parser only handles structural qualifiers such as a
    # destination radius above; it must not decide what “舒服”“情侣” or
    # “看流星雨” means by scanning keywords.
    return result


def _normalize_place_name(value: str) -> str:
    """Remove natural-language radius qualifiers before geocoding a place."""
    normalized = re.sub(r"\s+", "", value).strip("，,。；;、")
    for suffix in ("及其周边地区", "及其周边", "周边地区", "周边", "附近", "一带"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _normalize_special_events(value: Any) -> list[str]:
    events = [str(item).strip() for item in value] if isinstance(value, list) else []
    return list(dict.fromkeys(item for item in events if item))[:8]


def _cn_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    mapping = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return mapping.get(value)


def _coerce_travelers(value: Any) -> int | None:
    """Normalize the Requirement Agent's semantic count without inferring it locally."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 1 else None
    if isinstance(value, str):
        match = re.search(r"([一二三四五六七八九十两\d]+)", value)
        if match:
            return _cn_number(match.group(1))
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


def _extract_clock_preferences(raw_text: str) -> tuple[str | None, str | None]:
    """Extract explicit day-boundary hints for the offline fallback only."""
    marker_hours = {
        "凌晨": 5,
        "清晨": 6,
        "早上": 8,
        "上午": 9,
        "中午": 12,
        "下午": 14,
        "傍晚": 17,
        "晚上": 19,
    }
    departure: str | None = None
    return_time: str | None = None
    for marker, hour in marker_hours.items():
        if re.search(rf"{marker}[^，。；,;]{{0,8}}(?:从|出发|启程)", raw_text):
            departure = f"{hour:02d}:00"
        if re.search(rf"(?:返程|返回|回到|回来)[^，。；,;]{{0,8}}{marker}", raw_text):
            return_time = f"{hour:02d}:00"
    prefix_departure = re.search(
        r"(早上|上午|中午|下午|傍晚|晚上)\s*(\d{1,2})(?:[:：点时](\d{1,2}))?"
        r"[^，。；,;]{0,8}(?:从|出发|启程)",
        raw_text,
    )
    if prefix_departure:
        marker = prefix_departure.group(1)
        hour = int(prefix_departure.group(2))
        minute = int(prefix_departure.group(3) or 0)
        if marker in {"下午", "傍晚", "晚上"} and hour < 12:
            hour += 12
        if marker == "中午" and hour < 11:
            hour += 12
        departure = f"{hour:02d}:{minute:02d}"
    explicit_departure = re.search(
        r"(?:从|出发|启程)[^，。；,;]{0,12}?(早上|上午|中午|下午|傍晚|晚上)?"
        r"\s*(\d{1,2})(?:[:：点时](\d{1,2}))?",
        raw_text,
    )
    if explicit_departure:
        marker = explicit_departure.group(1)
        hour = int(explicit_departure.group(2))
        minute = int(explicit_departure.group(3) or 0)
        if marker in {"下午", "傍晚", "晚上"} and hour < 12:
            hour += 12
        if marker == "中午" and hour < 11:
            hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            departure = f"{hour:02d}:{minute:02d}"
    return departure, return_time
