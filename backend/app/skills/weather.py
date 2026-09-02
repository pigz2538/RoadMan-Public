"""Weather skills with a small, honest multi-source fallback chain.

The home weather card is intentionally more resilient than the planner's
hourly sampling. It queries several public forecast services concurrently,
selects the first usable current observation and keeps the source status in
the response. A provider outage therefore does not leave the UI spinning or
turn an unavailable forecast into a fabricated value.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx
from pydantic import BaseModel, Field

from ..domain.models import SkillResult, SourceRecord
from .base import SkillAdapter, SkillContext

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WTTR_URL = "https://wttr.in"
MET_NO_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
SEVEN_TIMER_URL = "https://www.7timer.info/bin/api.pl"


class WeatherInput(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    forecast_days: int = Field(default=5, ge=1, le=16)
    timezone: str = "Asia/Shanghai"


class OpenMeteoForecastAdapter(SkillAdapter):
    """Open-Meteo adapter retained for route/weather planning."""

    name = "open_meteo.forecast"
    category = "weather"
    cache_ttl_seconds = 1800
    timeout_seconds = 6.0

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return WeatherInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = WeatherInput.model_validate(payload)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
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
        except (httpx.HTTPError, ValueError) as exc:
            return _weather_failure(
                "Open-Meteo",
                "WEATHER_OPEN_METEO_UNAVAILABLE",
                "Open-Meteo 天气源暂时不可用",
                started,
                exc,
            )
        if not isinstance(body, dict):
            return _weather_failure(
                "Open-Meteo",
                "WEATHER_OPEN_METEO_INVALID_RESPONSE",
                "Open-Meteo 未返回有效天气数据",
                started,
            )
        hourly = body.get("hourly", {})
        if not isinstance(hourly, dict):
            hourly = {}
        times = hourly.get("time", [])
        if not isinstance(times, list):
            times = []
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
        current = body.get("current")
        if not isinstance(current, dict):
            current = {}
        return SkillResult(
            success=bool(current),
            provider="Open-Meteo",
            data={
                "latitude": body.get("latitude"),
                "longitude": body.get("longitude"),
                "elevation_m": body.get("elevation"),
                "timezone": body.get("timezone"),
                "current": current,
                "hourly_samples": samples,
            },
            sources=[
                SourceRecord(
                    provider="Open-Meteo",
                    title="公开天气预报",
                    url=OPEN_METEO_URL,
                    license="CC BY 4.0",
                    source_type="open_data",
                    confidence="high",
                )
            ],
            warnings=[] if current else ["Open-Meteo 未返回当前天气"],
            error_code=None if current else "WEATHER_OPEN_METEO_NO_CURRENT",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": True}


class WttrInForecastAdapter(SkillAdapter):
    """Public wttr.in fallback, useful when Open-Meteo is rate-limited."""

    name = "wttr.forecast"
    category = "weather"
    cache_ttl_seconds = 900
    timeout_seconds = 5.0
    max_retries = 0

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return WeatherInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = WeatherInput.model_validate(payload)
        started = time.perf_counter()
        endpoint = f"{WTTR_URL}/{request.latitude:.5f},{request.longitude:.5f}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    endpoint,
                    params={"format": "j1"},
                    headers={"User-Agent": "RoadMan/1.0 public-weather"},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return _weather_failure(
                "wttr.in",
                "WEATHER_WTTR_UNAVAILABLE",
                "wttr.in 天气源暂时不可用",
                started,
                exc,
            )
        if not isinstance(body, dict):
            return _weather_failure(
                "wttr.in",
                "WEATHER_WTTR_INVALID_RESPONSE",
                "wttr.in 未返回有效天气数据",
                started,
            )

        current_row = _first_dict(body.get("current_condition"))
        description = _nested_text(current_row, "weatherDesc", "value")
        current = {
            "temperature_2m": _number(current_row.get("temp_C")),
            "weather_code": _wttr_code(current_row.get("weatherCode"), description),
            "wind_speed_10m": _number(current_row.get("windspeedKmph")),
            "weather_description": description or None,
        }
        samples = _wttr_samples(body.get("weather"), request.forecast_days)
        if current["temperature_2m"] is None and current["weather_code"] is None:
            return _weather_failure(
                "wttr.in",
                "WEATHER_WTTR_NO_CURRENT",
                "wttr.in 未返回当前天气",
                started,
            )
        return SkillResult(
            success=True,
            provider="wttr.in",
            data={
                "latitude": request.latitude,
                "longitude": request.longitude,
                "timezone": request.timezone,
                "current": current,
                "hourly_samples": samples,
            },
            sources=[
                SourceRecord(
                    provider="wttr.in",
                    title="公开天气预报",
                    url=endpoint,
                    source_type="open_data",
                    confidence="medium",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": True}


class MetNoForecastAdapter(SkillAdapter):
    """MET Norway's keyless global forecast fallback."""

    name = "met_no.forecast"
    category = "weather"
    cache_ttl_seconds = 900
    timeout_seconds = 6.0
    max_retries = 0

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return WeatherInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = WeatherInput.model_validate(payload)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    MET_NO_URL,
                    params={"lat": request.latitude, "lon": request.longitude},
                    headers={"User-Agent": "RoadMan/1.0 (public weather fallback)"},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return _weather_failure(
                "MET Norway",
                "WEATHER_MET_NO_UNAVAILABLE",
                "MET Norway 天气源暂时不可用",
                started,
                exc,
            )
        if not isinstance(body, dict):
            return _weather_failure(
                "MET Norway",
                "WEATHER_MET_NO_INVALID_RESPONSE",
                "MET Norway 未返回有效天气数据",
                started,
            )
        timeseries = ((body.get("properties") or {}).get("timeseries") or [])
        if not isinstance(timeseries, list):
            timeseries = []
        samples = _met_no_samples(timeseries, request.forecast_days)
        current = samples[0] if samples else {}
        current = {
            "temperature_2m": current.get("temperature_c"),
            "weather_code": current.get("weather_code"),
            "wind_speed_10m": current.get("wind_speed_kmh"),
            "weather_description": current.get("condition"),
        }
        if current["temperature_2m"] is None and current["weather_code"] is None:
            return _weather_failure(
                "MET Norway",
                "WEATHER_MET_NO_NO_CURRENT",
                "MET Norway 未返回当前天气",
                started,
            )
        return SkillResult(
            success=True,
            provider="MET Norway",
            data={
                "latitude": request.latitude,
                "longitude": request.longitude,
                "timezone": request.timezone,
                "current": current,
                "hourly_samples": samples,
            },
            sources=[
                SourceRecord(
                    provider="MET Norway",
                    title="Locationforecast 公开预报",
                    url=MET_NO_URL,
                    source_type="open_data",
                    confidence="medium",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": True}


class SevenTimerForecastAdapter(SkillAdapter):
    """7Timer civil forecast fallback for a second independent model."""

    name = "seven_timer.forecast"
    category = "weather"
    cache_ttl_seconds = 900
    timeout_seconds = 5.0
    max_retries = 0

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return WeatherInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = WeatherInput.model_validate(payload)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    SEVEN_TIMER_URL,
                    params={
                        "lon": request.longitude,
                        "lat": request.latitude,
                        "product": "civil",
                        "output": "json",
                    },
                    headers={"User-Agent": "RoadMan/1.0 public-weather"},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return _weather_failure(
                "7Timer",
                "WEATHER_7TIMER_UNAVAILABLE",
                "7Timer 天气源暂时不可用",
                started,
                exc,
            )
        if not isinstance(body, dict):
            return _weather_failure(
                "7Timer",
                "WEATHER_7TIMER_INVALID_RESPONSE",
                "7Timer 未返回有效天气数据",
                started,
            )
        rows = body.get("dataseries")
        if not isinstance(rows, list):
            rows = []
        samples = _seven_timer_samples(rows, body.get("init"), request.forecast_days)
        current = samples[0] if samples else {}
        current = {
            "temperature_2m": current.get("temperature_c"),
            "weather_code": current.get("weather_code"),
            "wind_speed_10m": current.get("wind_speed_kmh"),
            "weather_description": current.get("condition"),
        }
        if current["temperature_2m"] is None and current["weather_code"] is None:
            return _weather_failure(
                "7Timer",
                "WEATHER_7TIMER_NO_CURRENT",
                "7Timer 未返回当前天气",
                started,
            )
        return SkillResult(
            success=True,
            provider="7Timer",
            estimated=True,
            data={
                "latitude": request.latitude,
                "longitude": request.longitude,
                "timezone": request.timezone,
                "current": current,
                "hourly_samples": samples,
            },
            sources=[
                SourceRecord(
                    provider="7Timer",
                    title="Civil 公开预报",
                    url=SEVEN_TIMER_URL,
                    source_type="open_data",
                    confidence="low",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": True}


class MultiSourceWeatherAdapter(SkillAdapter):
    """Concurrent weather fan-out used by the homepage forecast card."""

    name = "weather.multi_source"
    category = "weather"
    version = "1.0.0"
    cache_ttl_seconds = 600
    timeout_seconds = 10.0
    max_retries = 0

    def __init__(self, adapters: Iterable[SkillAdapter] | None = None):
        self.adapters = tuple(
            adapters
            or (
                OpenMeteoForecastAdapter(),
                WttrInForecastAdapter(),
                MetNoForecastAdapter(),
                SevenTimerForecastAdapter(),
            )
        )

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return WeatherInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], context: SkillContext) -> SkillResult:
        request = WeatherInput.model_validate(payload).model_dump()
        started = time.perf_counter()
        results = await asyncio.gather(
            *(self._safe_execute(adapter, request, context) for adapter in self.adapters),
        )
        usable = [result for result in results if _has_current_weather(result)]
        reports = [_provider_report(result) for result in results]
        if not usable:
            warnings = [
                str(warning)
                for result in results
                for warning in result.warnings[:1]
            ]
            return SkillResult(
                success=False,
                provider="weather.multi_source",
                data={"providers": reports, "source_count": 0},
                warnings=warnings or ["多个天气源均未返回可用当前天气"],
                error_code="WEATHER_ALL_SOURCES_UNAVAILABLE",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        selected = usable[0]
        selected_data = dict(selected.data) if isinstance(selected.data, dict) else {}
        selected_data.update(
            {
                "selected_provider": selected.provider,
                "source_count": len(usable),
                "providers": reports,
                "source_failures": [
                    report for report in reports if not report["success"]
                ],
            }
        )
        temperatures = [
            _number((result.data or {}).get("current", {}).get("temperature_2m"))
            for result in usable
            if isinstance(result.data, dict)
        ]
        temperatures = [value for value in temperatures if value is not None]
        warnings = [
            f"{report['provider']} 暂不可用，已切换其他天气源"
            for report in reports
            if not report["success"]
        ]
        if len(temperatures) >= 2 and max(temperatures) - min(temperatures) >= 8:
            warnings.append("多个天气源温度差异较大，首页仅展示首个可用源并建议出发前复核")
        return SkillResult(
            success=True,
            provider="weather.multi_source",
            data=selected_data,
            warnings=warnings,
            sources=[
                source
                for result in usable
                for source in result.sources
            ],
            estimated=selected.estimated,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _safe_execute(
        self,
        adapter: SkillAdapter,
        payload: dict[str, Any],
        context: SkillContext,
    ) -> SkillResult:
        try:
            result = await adapter.execute(payload, context)
            if isinstance(result, SkillResult):
                return result
        except Exception as exc:  # pragma: no cover - defensive provider boundary
            return _weather_failure(
                getattr(adapter, "name", "weather"),
                "WEATHER_PROVIDER_FAILED",
                "天气源调用失败",
                time.perf_counter(),
                exc,
            )
        return SkillResult(
            success=False,
            provider=getattr(adapter, "name", "weather"),
            warnings=["天气源返回格式无效"],
            error_code="WEATHER_PROVIDER_INVALID_RESULT",
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "configured": True,
            "providers": [getattr(adapter, "name", "weather") for adapter in self.adapters],
        }


def _weather_failure(
    provider: str,
    error_code: str,
    message: str,
    started: float,
    exc: Exception | None = None,
) -> SkillResult:
    detail = f"{message}（{type(exc).__name__}）" if exc else message
    return SkillResult(
        success=False,
        provider=provider,
        warnings=[detail],
        error_code=error_code,
        latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
    )


def _has_current_weather(result: SkillResult) -> bool:
    if not result.success or not isinstance(result.data, dict):
        return False
    current = result.data.get("current")
    if not isinstance(current, dict):
        return False
    return (
        _number(current.get("temperature_2m")) is not None
        or current.get("weather_code") is not None
    )


def _provider_report(result: SkillResult) -> dict[str, Any]:
    current = result.data.get("current") if isinstance(result.data, dict) else {}
    if not isinstance(current, dict):
        current = {}
    return {
        "provider": result.provider,
        "success": bool(result.success and _has_current_weather(result)),
        "estimated": bool(result.estimated),
        "temperature_2m": _number(current.get("temperature_2m")),
        "weather_code": current.get("weather_code"),
        "error_code": result.error_code,
    }


def _at(hourly: dict[str, Any], name: str, index: int) -> Any:
    values = hourly.get(name, [])
    return values[index] if isinstance(values, list) and index < len(values) else None


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return {}


def _nested_text(row: dict[str, Any], key: str, child: str) -> str:
    nested = _first_dict(row.get(key))
    return str(nested.get(child) or "").strip()


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return int(number) if number.is_integer() else number


def _wttr_code(value: Any, description: str = "") -> int | None:
    """Map wttr.in's weatherCode values to WMO-style codes used by the UI."""
    mapping = {
        113: 0,
        116: 2,
        119: 3,
        122: 3,
        143: 45,
        248: 45,
        260: 45,
        176: 61,
        263: 61,
        266: 61,
        293: 61,
        296: 63,
        299: 63,
        302: 65,
        305: 63,
        308: 65,
        353: 80,
        356: 81,
        359: 82,
        386: 95,
        389: 95,
        392: 95,
        395: 75,
    }
    parsed = _number(value)
    if parsed is not None and int(parsed) in mapping:
        return mapping[int(parsed)]
    text = description.casefold()
    if "雷" in text or "thunder" in text:
        return 95
    if "雪" in text or "snow" in text:
        return 71
    if "雨" in text or "rain" in text or "drizzle" in text:
        return 61
    if "雾" in text or "fog" in text or "mist" in text:
        return 45
    if "云" in text or "cloud" in text:
        return 2
    return None


def _wttr_samples(weather: Any, forecast_days: int) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    if not isinstance(weather, list):
        return samples
    for day in weather[:forecast_days]:
        if not isinstance(day, dict):
            continue
        date_text = str(day.get("date") or "")
        hourly = day.get("hourly")
        if not isinstance(hourly, list):
            continue
        for row in hourly:
            if not isinstance(row, dict):
                continue
            description = _nested_text(row, "weatherDesc", "value")
            samples.append(
                {
                    "sampled_at": _wttr_time(date_text, row.get("time")),
                    "temperature_c": _number(row.get("tempC")),
                    "precipitation_probability": _number(row.get("chanceofrain")),
                    "weather_code": _wttr_code(row.get("weatherCode"), description),
                    "wind_speed_kmh": _number(row.get("windspeedKmph")),
                    "condition": description or None,
                }
            )
    return [sample for sample in samples if sample["sampled_at"]]


def _wttr_time(date_text: str, raw_time: Any) -> str | None:
    if not date_text:
        return None
    try:
        value = int(float(raw_time or 0))
    except (TypeError, ValueError):
        value = 0
    hour, minute = divmod(max(0, value), 100)
    hour = min(hour, 23)
    minute = min(minute, 59)
    return f"{date_text}T{hour:02d}:{minute:02d}:00"


def _met_no_samples(timeseries: Any, forecast_days: int) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    if not isinstance(timeseries, list):
        return samples
    for row in timeseries[: max(24, forecast_days * 24)]:
        if not isinstance(row, dict):
            continue
        details = (((row.get("data") or {}).get("instant") or {}).get("details") or {})
        next_hour = ((row.get("data") or {}).get("next_1_hours") or {})
        summary = next_hour.get("summary") or {}
        symbol = str(summary.get("symbol_code") or "")
        samples.append(
            {
                "sampled_at": row.get("time"),
                "temperature_c": _number(details.get("air_temperature")),
                "weather_code": _symbol_code(symbol),
                "wind_speed_kmh": _kmh(details.get("wind_speed")),
                "condition": symbol or None,
            }
        )
    return [sample for sample in samples if sample["sampled_at"]]


def _symbol_code(symbol: str) -> int | None:
    text = symbol.casefold()
    if not text:
        return None
    if "thunder" in text:
        return 95
    if "snow" in text or "sleet" in text:
        return 71
    if "rain" in text or "drizzle" in text:
        return 61
    if "fog" in text:
        return 45
    if "partlycloudy" in text:
        return 2
    if "cloudy" in text:
        return 3
    if "clear" in text or "sunny" in text:
        return 0
    return None


def _kmh(value: Any) -> float | int | None:
    number = _number(value)
    return round(float(number) * 3.6, 1) if number is not None else None


def _seven_timer_samples(
    rows: Any,
    init: Any,
    forecast_days: int,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return samples
    base = _seven_timer_base(init)
    max_rows = max(8, forecast_days * 8)
    for row in rows[:max_rows]:
        if not isinstance(row, dict):
            continue
        try:
            offset_hours = int(row.get("timepoint") or 0)
        except (TypeError, ValueError):
            offset_hours = 0
        sampled_at = (base + timedelta(hours=offset_hours)).isoformat(timespec="minutes")
        symbol = str(row.get("weather") or "")
        samples.append(
            {
                "sampled_at": sampled_at,
                "temperature_c": _number(row.get("temp2m")),
                "weather_code": _symbol_code(symbol),
                "wind_speed_kmh": _seven_timer_wind(row.get("wind10m")),
                "condition": symbol or None,
            }
        )
    return samples


def _seven_timer_base(value: Any) -> datetime:
    try:
        return datetime.strptime(str(value), "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _seven_timer_wind(value: Any) -> float | int | None:
    if isinstance(value, dict):
        return _number(value.get("speed"))
    return _number(value)
