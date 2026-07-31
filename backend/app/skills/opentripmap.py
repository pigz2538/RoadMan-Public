from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..domain.models import SkillResult, SourceRecord
from .base import SkillAdapter, SkillContext

BASE_URL = "https://api.opentripmap.com/0.1"


class OpenTripMapNearbyInput(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    radius_m: int = Field(default=25000, ge=100, le=100000)
    limit: int = Field(default=12, ge=1, le=50)
    language: str = Field(default="en", pattern="^(en|ru)$")
    kinds: str = "interesting_places"
    minimum_rate: str = Field(default="2", pattern="^(1|2|3|1h|2h|3h)$")


class OpenTripMapNearbyAdapter(SkillAdapter):
    name = "opentripmap.nearby"
    version = "1.0.0"
    category = "tourism_poi"
    cache_ttl_seconds = 24 * 3600
    timeout_seconds = 12

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return OpenTripMapNearbyInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        if not self.api_key:
            return SkillResult(
                success=False,
                provider="OpenTripMap",
                warnings=["未配置 OPENTRIPMAP_API_KEY"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = OpenTripMapNearbyInput.model_validate(payload)
        endpoint = f"{BASE_URL}/{request.language}/places/radius"
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                endpoint,
                params={
                    "apikey": self.api_key,
                    "lon": request.longitude,
                    "lat": request.latitude,
                    "radius": request.radius_m,
                    "limit": request.limit,
                    "kinds": request.kinds,
                    "rate": request.minimum_rate,
                    "format": "json",
                },
            )
            response.raise_for_status()
            body = response.json()
        items = []
        for item in body if isinstance(body, list) else []:
            point = item.get("point") or {}
            if not item.get("xid") or not item.get("name") or not point:
                continue
            items.append(
                {
                    "id": item["xid"],
                    "name": item["name"],
                    "name_en": item["name"] if request.language == "en" else None,
                    "name_local": item["name"],
                    "longitude": point.get("lon"),
                    "latitude": point.get("lat"),
                    "distance_m": item.get("dist"),
                    "kinds": item.get("kinds"),
                    "rating": _rate_number(item.get("rate")),
                    "detail_url": f"https://opentripmap.com/en/card/{item['xid']}",
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="OpenTripMap",
                warnings=["OpenTripMap 未返回可用景点"],
                error_code="OPENTRIPMAP_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="OpenTripMap",
            data={"items": items, "count": len(items), "language": request.language},
            sources=[
                SourceRecord(
                    provider="OpenTripMap",
                    title="OpenTripMap global POI",
                    url=endpoint,
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.api_key else "degraded",
            "configured": bool(self.api_key),
        }


def _rate_number(value: object) -> float | None:
    text = str(value or "").rstrip("h")
    try:
        return float(text)
    except ValueError:
        return None
