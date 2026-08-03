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
            return _offline_semantic_fallback(deterministic, raw_text)
        prompt = (
            "You are RoadMan Requirement Agent. Extract requirements only; never plan a route. "
            f"Today is {today.isoformat()}. Return ONLY one valid JSON object (no markdown, no explanation) "
            "with exactly these keys: origin_name, destination_name, start_date, end_date, "
            "departure_time, return_time, travelers, preferences. "
            "Dates must be YYYY-MM-DD; unknown scalar fields must be null and preferences must be an array. "
            "Normalize Chinese time phrases: 中午=12:00, 下午=14:00, 晚上=19:00. "
            "Infer travelers semantically (情侣/夫妻=2, 一家三口=3); do not default to 1 when evidence is absent. "
            "请根据语义判断同行人数，不要机械默认 1。"
            "For text like 从A出发，在B及其周边旅游, use A as origin and B as destination. "
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
                return _offline_semantic_fallback(merged, raw_text)
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return _offline_semantic_fallback(deterministic, raw_text)


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
        r"(?P<destination>[\u4e00-\u9fffA-Za-z0-9·]+?)(?=，|,|。|；|;|两天|一日|周[一二三四五六日天]|$)",
        raw_text,
    )
    if route_match:
        result["origin_name"] = route_match.group("origin")
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
            result["origin_name"] = origin_match.group("origin")
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

    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    weekday_match = re.search(r"周([一二三四五六日天])", raw_text)
    if weekday_match:
        target = weekday_map[weekday_match.group(1)]
        delta = (target - today.weekday()) % 7
        if delta == 0:
            delta = 7
        start = today + timedelta(days=delta)
        result["start_date"] = start.isoformat()

    nights_match = re.search(r"([一二三四五六七八九十两\d]+)天([一二三四五六七八九十两\d]+)夜", raw_text)
    if nights_match:
        days = _cn_number(nights_match.group(1))
        if result.get("start_date") and days:
            start = date.fromisoformat(result["start_date"])
            result["end_date"] = (start + timedelta(days=days - 1)).isoformat()

    traveler_match = re.search(r"([一二三四五六七八九十两\d]+)\s*(?:人|位|口)", raw_text)
    if traveler_match:
        result["travelers"] = _cn_number(traveler_match.group(1))
    for keyword in ("自然风景", "自然景观", "山水风景", "亲子", "轻松", "省钱", "不走夜路", "新能源"):
        if keyword in raw_text:
            result["preferences"].append(keyword)
    return result


def _normalize_place_name(value: str) -> str:
    """Remove natural-language radius qualifiers before geocoding a place."""
    normalized = re.sub(r"\s+", "", value).strip("，,。；;、")
    for suffix in ("及其周边地区", "及其周边", "周边地区", "周边", "附近", "一带"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


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


def _offline_semantic_fallback(extracted: dict[str, Any], raw_text: str) -> dict[str, Any]:
    """Keep semantic party-size hints usable when the cloud Agent is offline.

    This is deliberately only a degradation path: the Requirement Agent's
    value wins whenever it returns a valid count. It prevents a transient
    Ollama/DNS failure from silently turning an explicit couple trip into the
    visible default of one traveler.
    """
    result = dict(extracted)
    if result.get("travelers") is None:
        if re.search(r"情侣|夫妻|伴侣|二人|两人|两位", raw_text):
            result["travelers"] = 2
        elif re.search(r"一家三口|三人|三位", raw_text):
            result["travelers"] = 3
    return result


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
