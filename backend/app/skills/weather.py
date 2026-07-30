import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..domain.models import SkillResult, SourceRecord
from .base import SkillAdapter, SkillContext

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherInput(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    forecast_days: int = Field(default=5, ge=1, le=16)
    timezone: str = "Asia/Shanghai"


class OpenMeteoForecastAdapter(SkillAdapter):
    name = "open_meteo.forecast"
    category = "weather"
    cache_ttl_seconds = 1800
    timeout_seconds = 6.0

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return WeatherInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = WeatherInput.model_validate(payload)
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                OPEN_METEO_URL,
                params={
                    "latitude": request.latitude,
                    "longitude": request.longitude,
                    "timezone": request.timezone,
                    "forecast_days": request.forecast_days,
                    "current": "temperature_2m,weather_code,wind_speed_10m",
                    "hourly": (
                        "temperature_2m,precipitation_probability,weather_code,"
                        "visibility,wind_speed_10m"
                    ),
                },
            )
            response.raise_for_status()
            body = response.json()
        hourly = body.get("hourly", {})
        times = hourly.get("time", [])
        samples = [
            {
                "sampled_at": sampled_at,
                "temperature_c": _at(hourly, "temperature_2m", index),
                "precipitation_probability": _at(
                    hourly,
                    "precipitation_probability",
                    index,
                ),
                "weather_code": _at(hourly, "weather_code", index),
                "visibility_m": _at(hourly, "visibility", index),
                "wind_speed_kmh": _at(hourly, "wind_speed_10m", index),
            }
            for index, sampled_at in enumerate(times)
        ]
        return SkillResult(
            success=True,
            provider="Open-Meteo",
            data={
                "latitude": body.get("latitude"),
                "longitude": body.get("longitude"),
                "timezone": body.get("timezone"),
                "current": body.get("current"),
                "hourly_samples": samples,
            },
            sources=[
                SourceRecord(
                    provider="Open-Meteo",
                    title="Forecast API",
                    url=OPEN_METEO_URL,
                    license="CC BY 4.0",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": True}


def _at(hourly: dict[str, Any], name: str, index: int) -> Any:
    values = hourly.get(name, [])
    return values[index] if index < len(values) else None
