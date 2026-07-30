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
            return deterministic
        prompt = (
            "你是 RoadMan Requirement Agent，只抽取需求，禁止规划路线。"
            f"今天是 {today.isoformat()}。将用户文本转成单个 JSON 对象，字段仅允许："
            "origin_name,destination_name,start_date,end_date,travelers,preferences。"
            "日期必须 YYYY-MM-DD；未知字段用 null 或空数组。不要 Markdown。用户文本："
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
                    },
                )
                response.raise_for_status()
                parsed = _parse_json_object(response.json().get("response", ""))
                return _merge_extraction(deterministic, parsed)
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return deterministic


def _parse_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("LLM response does not contain JSON")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("LLM response must be a JSON object")
    allowed = {
        "origin_name",
        "destination_name",
        "start_date",
        "end_date",
        "travelers",
        "preferences",
    }
    return {key: value for key, value in value.items() if key in allowed}


def _merge_extraction(base: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in llm.items():
        if value not in (None, "", []):
            result[key] = value
    return result


def deterministic_extract(raw_text: str, today: date) -> dict[str, Any]:
    result: dict[str, Any] = {"preferences": []}
    route_match = re.search(
        r"从(?P<origin>[\u4e00-\u9fffA-Za-z0-9·]+?)(?:出发)?(?:去|到|前往)"
        r"(?P<destination>[\u4e00-\u9fffA-Za-z0-9·]+?)(?=，|,|。|；|;|两天|一日|周[一二三四五六日天]|$)",
        raw_text,
    )
    if route_match:
        result["origin_name"] = route_match.group("origin")
        result["destination_name"] = route_match.group("destination")

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

    traveler_match = re.search(r"([一二三四五六七八九十两\d]+)人", raw_text)
    if traveler_match:
        result["travelers"] = _cn_number(traveler_match.group(1))
    for keyword in ("自然风景", "亲子", "轻松", "省钱", "不走夜路", "新能源"):
        if keyword in raw_text:
            result["preferences"].append(keyword)
    return result


def _cn_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    mapping = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return mapping.get(value)
