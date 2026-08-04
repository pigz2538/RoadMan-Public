from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from ..domain.models import SkillResult, SourceRecord
from .base import SkillAdapter, SkillContext


class FlyAIHotelInput(BaseModel):
    destination: str = Field(min_length=1, max_length=80)
    poi_name: str | None = Field(default=None, max_length=80)
    check_in_date: date
    check_out_date: date
    max_price: int | None = Field(default=None, ge=1, le=100000)
    sort: str = "rate_desc"


class FlyAIPoiInput(BaseModel):
    city_name: str = Field(min_length=1, max_length=80)
    keyword: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=40)
    poi_level: int | None = Field(default=None, ge=1, le=5)


class FlyAISearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=240)


FLYAI_POI_CATEGORIES = {
    "自然风光", "山湖田园", "森林丛林", "峡谷瀑布", "沙滩海岛", "沙漠草原",
    "人文古迹", "古镇古村", "历史古迹", "园林花园", "宗教场所", "公园乐园",
    "主题乐园", "水上乐园", "影视基地", "动物园", "植物园", "海洋馆",
    "体育场馆", "演出赛事", "剧院剧场", "博物馆", "纪念馆", "展览馆",
    "地标建筑", "市集", "文创街区", "城市观光", "户外活动", "滑雪",
    "漂流", "冲浪", "潜水", "露营", "温泉",
}


def _flyai_process_env() -> dict[str, str]:
    """Pass Docker/host proxy settings to Node's built-in fetch client."""
    environment = os.environ.copy()
    # Node 22's undici fetch does not consume HTTP_PROXY by default.  The
    # CLI needs this switch in the container, otherwise every call fails at
    # DNS resolution before the FlyAI service can be reached.
    environment.setdefault("NODE_USE_ENV_PROXY", "1")
    return environment


class FlyAIHotelAdapter(SkillAdapter):
    name = "flyai.hotel"
    version = "1.1.0"
    category = "travel_search"
    timeout_seconds = 25
    max_retries = 0
    cache_ttl_seconds = 30 * 60

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlyAIHotelInput.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        command = shutil.which("flyai")
        if not command:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["运行环境未安装 flyai CLI"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = FlyAIHotelInput.model_validate(payload)
        arguments = [
            command,
            "search-hotel",
            "--dest-name",
            request.destination,
            "--check-in-date",
            request.check_in_date.isoformat(),
            "--check-out-date",
            request.check_out_date.isoformat(),
            "--sort",
            request.sort,
        ]
        if request.poi_name:
            arguments.extend(["--poi-name", request.poi_name])
        if request.max_price:
            arguments.extend(["--max-price", str(request.max_price)])

        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_flyai_process_env(),
        )
        stdout, _ = await process.communicate()
        text = stdout.decode("utf-8", errors="replace").strip()
        # Windows CLI may exit non-zero after already returning a valid JSON payload.
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 未返回可解析的 JSON"],
                error_code="FLYAI_INVALID_RESPONSE",
            )
        raw_items = body.get("data", {}).get("itemList", [])
        items = []
        for item in raw_items:
            try:
                longitude = float(item["longitude"])
                latitude = float(item["latitude"])
            except (KeyError, TypeError, ValueError):
                continue
            price = _parse_price(item.get("price"))
            items.append(
                {
                    "id": item.get("shId"),
                    "name": item.get("name"),
                    "address": item.get("address"),
                    "location": f"{longitude},{latitude}",
                    "longitude": longitude,
                    "latitude": latitude,
                    "price_min_cny": price[0] if price else None,
                    "price_max_cny": price[1] if price else None,
                    "price_estimated": price[2] if price else None,
                    "rating": _float_or_none(item.get("rate")),
                    "star": item.get("star"),
                    "nearby": item.get("interestsPoi"),
                    "detail_url": item.get("detailUrl"),
                    "image_url": item.get("mainPic"),
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 未返回可用酒店"],
                error_code="FLYAI_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={"items": items, "count": len(items)},
            sources=[
                SourceRecord(
                    provider="FlyAI / 飞猪",
                    title="酒店实时搜索",
                    url="https://www.fliggy.com/",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if shutil.which("flyai") else "degraded",
            "configured": bool(shutil.which("flyai")),
        }


class FlyAIPoiAdapter(SkillAdapter):
    name = "flyai.poi"
    version = "1.0.0"
    category = "travel_search"
    timeout_seconds = 25
    max_retries = 0
    cache_ttl_seconds = 30 * 60

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlyAIPoiInput.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        command = shutil.which("flyai")
        if not command:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["运行环境未安装 flyai CLI"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = FlyAIPoiInput.model_validate(payload)
        arguments = [command, "search-poi", "--city-name", request.city_name]
        if request.keyword:
            arguments.extend(["--keyword", request.keyword])
        # The CLI rejects broad labels such as “景点”. Keep keyword search usable
        # and only forward category values accepted by the upstream command.
        if request.category in FLYAI_POI_CATEGORIES:
            arguments.extend(["--category", request.category])
        if request.poi_level:
            arguments.extend(["--poi-level", str(request.poi_level)])
        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_flyai_process_env(),
        )
        stdout, _ = await process.communicate()
        try:
            body = json.loads(stdout.decode("utf-8", errors="replace").strip())
        except json.JSONDecodeError:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 景点搜索未返回可解析的 JSON"],
                error_code="FLYAI_INVALID_RESPONSE",
            )
        items = []
        for item in body.get("data", {}).get("itemList", []):
            ticket = item.get("ticketInfo") or {}
            price = _parse_price(ticket.get("price"))
            if not item.get("name"):
                continue
            raw_location = item.get("location") or item.get("lnglat") or ""
            longitude = latitude = None
            if isinstance(raw_location, str) and "," in raw_location:
                try:
                    longitude, latitude = (float(value) for value in raw_location.split(",", 1))
                except (TypeError, ValueError):
                    longitude = latitude = None
            if longitude is None and item.get("longitude") is not None and item.get("latitude") is not None:
                try:
                    longitude, latitude = float(item["longitude"]), float(item["latitude"])
                except (TypeError, ValueError):
                    longitude = latitude = None
            items.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "address": item.get("address"),
                    "image_url": item.get("mainPic"),
                    "detail_url": item.get("jumpUrl"),
                    "free_status": item.get("freePoiStatus"),
                    "ticket_name": ticket.get("ticketName"),
                    "ticket_date": ticket.get("priceDate"),
                    "price_min_cny": price[0] if price else None,
                    "price_max_cny": price[1] if price else None,
                    "price_estimated": price[2] if price else None,
                    "location": raw_location or None,
                    "longitude": longitude,
                    "latitude": latitude,
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 未返回可用景点或门票"],
                error_code="FLYAI_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={"items": items, "count": len(items)},
            sources=[
                SourceRecord(
                    provider="FlyAI / 飞猪",
                    title="景点与门票搜索",
                    url="https://www.fliggy.com/",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if shutil.which("flyai") else "degraded",
            "configured": bool(shutil.which("flyai")),
        }


class _FlyAISearchAdapter(SkillAdapter):
    """Shared CLI adapter for FlyAI broad and semantic destination search."""

    command_name: str

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlyAISearchInput.model_validate(payload).model_dump(mode="json")

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        command = shutil.which("flyai")
        if not command:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["运行环境未安装 flyai CLI"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = FlyAISearchInput.model_validate(payload)
        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            command,
            self.command_name,
            "--query",
            request.query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_flyai_process_env(),
        )
        stdout, stderr = await process.communicate()
        raw_text = stdout.decode("utf-8", errors="replace").strip()
        try:
            body = json.loads(raw_text)
        except json.JSONDecodeError:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 搜索未返回可解析 JSON", stderr.decode("utf-8", errors="replace")[:160]],
                error_code="FLYAI_INVALID_RESPONSE",
            )
        raw_items = (body.get("data") or {}).get("itemList", [])
        items: list[dict[str, Any]] = []
        for raw in raw_items if isinstance(raw_items, list) else []:
            item = raw.get("info") if isinstance(raw, dict) and isinstance(raw.get("info"), dict) else raw
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title[:240],
                    "snippet": str(item.get("description") or item.get("summary") or item.get("tags") or "").strip()[:500],
                    "detail_url": item.get("jumpUrl") or item.get("detailUrl") or item.get("url"),
                    "image_url": item.get("picUrl") or item.get("mainPic") or item.get("imageUrl"),
                    "rating": _float_or_none(item.get("rate") or item.get("rating")),
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 搜索没有返回可用候选"],
                error_code="FLYAI_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={"query": request.query, "items": items, "count": len(items)},
            sources=[
                SourceRecord(
                    provider="FlyAI / 飞猪",
                    title=f"FlyAI {self.command_name} 目的地搜索",
                    url="https://flyai.open.fliggy.com/",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if shutil.which("flyai") else "degraded",
            "configured": bool(shutil.which("flyai")),
            "command": self.command_name,
            "api_key_configured": bool(os.getenv("FLYAI_API_KEY")),
        }


class FlyAIKeywordSearchAdapter(_FlyAISearchAdapter):
    name = "flyai.keyword_search"
    version = "1.0.0"
    category = "travel_search"
    command_name = "keyword-search"
    timeout_seconds = 25
    max_retries = 0
    cache_ttl_seconds = 30 * 60


class FlyAISemanticSearchAdapter(_FlyAISearchAdapter):
    name = "flyai.ai_search"
    version = "1.0.0"
    category = "travel_search"
    command_name = "ai-search"
    timeout_seconds = 20
    max_retries = 0
    cache_ttl_seconds = 30 * 60


def _float_or_none(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _parse_price(value: object) -> tuple[float, float, bool] | None:
    text = str(value or "").strip().lower()
    masked = re.search(r"(\d+)(x+)", text)
    if masked:
        magnitude = 10 ** len(masked.group(2))
        minimum = int(masked.group(1)) * magnitude
        return float(minimum), float(minimum + magnitude - 1), True
    exact = re.search(r"(\d+(?:\.\d+)?)", text)
    if exact:
        amount = float(exact.group(1))
        return amount, amount, False
    return None
