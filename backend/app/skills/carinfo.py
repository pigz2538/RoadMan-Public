from __future__ import annotations

import re
import time
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from ..domain.models import SkillResult, SourceRecord
from .base import SkillAdapter, SkillContext

CATALOG = [
    {
        "id": "vehicle_demo_ev",
        "brand": "RoadMan",
        "series": "Explorer",
        "model": "纯电 SUV",
        "year": 2026,
        "power_type": "electric",
        "rated_range_km": 560,
        "battery_kwh": 82,
        "consumption_per_100km": 18,
        "max_charge_kw": 180,
        "height_m": 1.68,
        "width_m": 1.92,
        "seats": 5,
        "mountain_ready": True,
        "unpaved_ready": False,
        "estimated": True,
    },
    {
        "id": "vehicle_demo_hybrid",
        "brand": "RoadMan",
        "series": "Tourer",
        "model": "混动 SUV",
        "year": 2026,
        "power_type": "hybrid",
        "rated_range_km": 950,
        "consumption_per_100km": 5.8,
        "height_m": 1.72,
        "width_m": 1.91,
        "seats": 5,
        "mountain_ready": True,
        "unpaved_ready": False,
        "estimated": True,
    },
]

CARINFO_API_URL = "https://tool.bitefu.net/car/"


class CarInfoCatalogInput(BaseModel):
    query: str = Field(min_length=2, max_length=80)
    limit: int = Field(default=12, ge=1, le=30)


class CarInfoCatalogAdapter(SkillAdapter):
    """Search the concrete brand/series/model catalog from the carinfo Skill.

    The old ``carinfo.demo`` adapter remains as the deterministic fallback used
    by planning tests. This adapter follows the real carinfo Skill's ``info``
    endpoint and keeps the provider record/price/year evidence with every
    result so the UI can create a vehicle profile without inventing specs.
    """

    name = "carinfo.catalog"
    version = "1.0.0"
    category = "vehicle"
    timeout_seconds = 12
    max_retries = 0
    cache_ttl_seconds = 6 * 3600

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = {**payload, "query": str(payload.get("query") or "").strip()}
        value = CarInfoCatalogInput.model_validate(normalized)
        return value.model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = CarInfoCatalogInput.model_validate(payload)
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                CARINFO_API_URL,
                params={"type": "info", "keyword": request.query},
            )
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict) or body.get("status") != 1:
            return SkillResult(
                success=False,
                provider="Bitefu CarApi",
                warnings=[str(body.get("info") or "车型库没有匹配结果") if isinstance(body, dict) else "车型库没有匹配结果"],
                error_code="CARINFO_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        raw_items = body.get("info")
        if not isinstance(raw_items, list):
            raw_items = [raw_items] if isinstance(raw_items, dict) else []
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = _catalog_vehicle_item(raw)
            if not item or item["source_id"] in seen:
                continue
            seen.add(item["source_id"])
            items.append(item)
        items.sort(key=_catalog_sort_key)
        items = items[: request.limit]
        if not items:
            return SkillResult(
                success=False,
                provider="Bitefu CarApi",
                warnings=["车型库没有返回可添加的具体车型"],
                error_code="CARINFO_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        source_url = f"{CARINFO_API_URL}?{urlencode({'type': 'info', 'keyword': request.query})}"
        return SkillResult(
            success=True,
            provider="Bitefu CarApi",
            data={"query": request.query, "count": len(items), "items": items},
            sources=[
                SourceRecord(
                    provider="Bitefu CarApi",
                    title="汽车品牌/车系/车型数据库",
                    url=source_url,
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": True, "provider": "Bitefu CarApi"}


class CarInfoInput(BaseModel):
    brand: str | None = None
    power_type: Literal["electric", "hybrid", "fuel"] | None = None


class CarInfoDemoAdapter(SkillAdapter):
    name = "carinfo.demo"
    category = "vehicle"
    cache_ttl_seconds = 24 * 3600
    max_retries = 0

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return CarInfoInput.model_validate(payload).model_dump(exclude_none=True)

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = CarInfoInput.model_validate(payload)
        items = [
            item
            for item in CATALOG
            if (not request.brand or item["brand"].lower() == request.brand.lower())
            and (not request.power_type or item["power_type"] == request.power_type)
        ]
        return SkillResult(
            success=True,
            provider="RoadMan CarInfo Demo",
            data={"items": items},
            estimated=True,
            sources=[
                SourceRecord(
                    provider="RoadMan",
                    title="阶段 C 固定车型样本",
                )
            ],
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": True, "records": len(CATALOG)}


def _catalog_vehicle_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    source_id = str(raw.get("id") or "").strip()
    brand = str(raw.get("brand_name") or "").strip()
    series = str(raw.get("series_name") or raw.get("group_name") or "").strip()
    model = str(raw.get("full_name") or raw.get("name") or "").strip()
    if not source_id or not brand or not series or not model:
        return None
    year = _integer(raw.get("year"))
    power_type = _infer_power_type(raw)
    state = str(raw.get("state") or "").strip()
    source_url = f"{CARINFO_API_URL}?{urlencode({'type': 'info', 'id': source_id})}"
    return {
        "id": f"carinfo_{source_id}",
        "source_id": source_id,
        "brand": brand,
        "series": series,
        "model": model,
        "year": year,
        "power_type": power_type,
        # The catalog endpoint supplies identity/year/price, but not a
        # reliable per-trim battery or real-world range. Keep those fields
        # null so the traveller can confirm their exact configuration instead
        # of silently inheriting the demo SUV's numbers.
        "rated_range_km": None,
        "battery_kwh": None,
        "consumption_per_100km": None,
        "max_charge_kw": None,
        "height_m": None,
        "width_m": None,
        "seats": 5,
        "current_energy_percent": 80,
        "safe_energy_reserve_percent": 15,
        "has_etc": False,
        "mountain_ready": True,
        "unpaved_ready": False,
        "state": state,
        "state_label": {"20": "在售", "30": "停售在库", "40": "进口/其他", "0": "历史款"}.get(state, "状态待核实"),
        "price_min_cny": _number(raw.get("minprice")),
        "price_max_cny": _number(raw.get("maxprice")),
        "source_url": source_url,
        "specs_missing": ["额定续航/油箱续航", "百公里能耗", "电池容量（如适用）"],
    }


def _catalog_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    state_rank = {"20": 0, "40": 1, "30": 2, "0": 3}.get(str(item.get("state")), 4)
    year = int(item.get("year") or 0)
    return (state_rank, -year, 0 if item.get("power_type") == "electric" else 1, str(item.get("model")))


def _integer(value: object) -> int | None:
    match = re.search(r"\d{4}", str(value or ""))
    return int(match.group()) if match else None


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _infer_power_type(raw: dict[str, Any]) -> str:
    text = " ".join(
        str(raw.get(key) or "")
        for key in ("brand_name", "series_name", "full_name", "name")
    ).casefold()
    if re.search(r"混动|插混|增程|phev|hev", text):
        return "hybrid"
    if re.search(r"纯电|电动|新能源|\bev\b|\bbev\b", text):
        return "electric"
    # A small provider-derived hint for brands whose catalogue is exclusively
    # battery-electric; users can still change the dropdown before saving.
    if any(name in text for name in ("特斯拉", "tesla", "蔚来", "小鹏", "极氪", "智己")):
        return "electric"
    return "fuel"
