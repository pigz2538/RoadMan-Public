from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..domain.models import SourceRecord, SkillResult
from .base import SkillAdapter, SkillContext

AMAP_BASE_URL = "https://restapi.amap.com"


class GeocodeInput(BaseModel):
    address: str = Field(min_length=1)
    city: str | None = None


class DrivingInput(BaseModel):
    origin: str = Field(pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$")
    destination: str = Field(pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$")
    strategy: int = Field(default=0, ge=0, le=20)


class AmapGeocodeAdapter(SkillAdapter):
    name = "amap.geocode"
    category = "geocoding"
    cache_ttl_seconds = 30 * 24 * 3600

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return GeocodeInput.model_validate(payload).model_dump(exclude_none=True)

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        if not self.api_key:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=["未配置 AMAP_WEBSERVICE_KEY"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{AMAP_BASE_URL}/v3/geocode/geo",
                params={**payload, "key": self.api_key},
            )
            response.raise_for_status()
            body = response.json()
        if body.get("status") != "1" or not body.get("geocodes"):
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=[body.get("info", "未找到地址")],
                error_code="AMAP_NO_RESULT",
            )
        item = body["geocodes"][0]
        return SkillResult(
            success=True,
            provider="高德地图",
            data={
                "formatted_address": item["formatted_address"],
                "location": item["location"],
                "province": item.get("province"),
                "city": item.get("city"),
                "district": item.get("district"),
                "adcode": item.get("adcode"),
            },
            sources=[SourceRecord(provider="高德地图", title="地理编码 API", url=f"{AMAP_BASE_URL}/v3/geocode/geo")],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready" if self.api_key else "degraded", "configured": bool(self.api_key)}


class AmapDrivingAdapter(SkillAdapter):
    name = "amap.driving"
    category = "routing"
    cache_ttl_seconds = 1800

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return DrivingInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        if not self.api_key:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=["未配置 AMAP_WEBSERVICE_KEY"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{AMAP_BASE_URL}/v3/direction/driving",
                params={**payload, "extensions": "all", "key": self.api_key},
            )
            response.raise_for_status()
            body = response.json()
        paths = body.get("route", {}).get("paths", [])
        if body.get("status") != "1" or not paths:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=[body.get("info", "未找到驾车路线")],
                error_code="AMAP_NO_RESULT",
            )
        path = paths[0]
        steps = path.get("steps", [])
        return SkillResult(
            success=True,
            provider="高德地图",
            data={
                "origin": body["route"]["origin"],
                "destination": body["route"]["destination"],
                "distance_km": round(int(path["distance"]) / 1000, 2),
                "duration_minutes": round(int(path["duration"]) / 60),
                "tolls_cny": float(path.get("tolls") or 0),
                "polyline": ";".join(step.get("polyline", "") for step in steps if step.get("polyline")),
                "steps": [
                    {
                        "instruction": step.get("instruction"),
                        "road": step.get("road"),
                        "distance_m": int(step.get("distance") or 0),
                        "duration_s": int(step.get("duration") or 0),
                    }
                    for step in steps
                ],
            },
            sources=[SourceRecord(provider="高德地图", title="驾车路径规划 API", url=f"{AMAP_BASE_URL}/v3/direction/driving")],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready" if self.api_key else "degraded", "configured": bool(self.api_key)}
