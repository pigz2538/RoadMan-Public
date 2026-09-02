import pytest

from app.domain.models import SkillResult, SourceRecord
from app.skills.base import SkillAdapter, SkillContext
from app.skills.weather import (
    MultiSourceWeatherAdapter,
    _wttr_code,
    _wttr_samples,
)


class StubWeatherAdapter(SkillAdapter):
    category = "weather"

    def __init__(self, name: str, result: SkillResult):
        self.name = name
        self.result = result

    async def execute(self, _: dict, __: SkillContext) -> SkillResult:
        return self.result

    async def health_check(self) -> dict:
        return {"status": "ready"}


def weather_result(
    provider: str,
    temperature: float | None,
    *,
    estimated: bool = False,
    success: bool = True,
) -> SkillResult:
    return SkillResult(
        success=success,
        provider=provider,
        estimated=estimated,
        data={
            "current": {
                "temperature_2m": temperature,
                "weather_code": 1,
            },
            "hourly_samples": [{"sampled_at": "2026-09-02T12:00", "temperature_c": temperature}],
        }
        if success
        else None,
        sources=[
            SourceRecord(
                provider=provider,
                title="公开天气预报",
                url="https://example.test/weather",
                source_type="open_data",
                confidence="medium",
            )
        ]
        if success
        else [],
        error_code=None if success else "WEATHER_PROVIDER_DOWN",
        warnings=[] if success else ["天气源不可用"],
    )


@pytest.mark.asyncio
async def test_multi_source_selects_first_usable_and_reports_all_sources():
    adapter = MultiSourceWeatherAdapter(
        (
            StubWeatherAdapter("source.one", weather_result("source.one", None, success=False)),
            StubWeatherAdapter("source.two", weather_result("source.two", 27)),
            StubWeatherAdapter("source.three", weather_result("source.three", 29)),
        )
    )

    result = await adapter.execute(
        {"latitude": 30.59, "longitude": 114.30, "forecast_days": 1},
        SkillContext(),
    )

    assert result.success is True
    assert result.provider == "weather.multi_source"
    assert result.data["selected_provider"] == "source.two"
    assert result.data["source_count"] == 2
    assert result.data["current"]["temperature_2m"] == 27
    assert len(result.data["providers"]) == 3
    assert result.data["source_failures"][0]["provider"] == "source.one"
    assert result.sources[0].provider == "source.two"
    assert any("source.one" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_multi_source_returns_explicit_failure_when_every_source_is_down():
    adapter = MultiSourceWeatherAdapter(
        (
            StubWeatherAdapter("source.one", weather_result("source.one", None, success=False)),
            StubWeatherAdapter("source.two", weather_result("source.two", None, success=False)),
        )
    )

    result = await adapter.execute(
        {"latitude": 39.90, "longitude": 116.40, "forecast_days": 1},
        SkillContext(),
    )

    assert result.success is False
    assert result.error_code == "WEATHER_ALL_SOURCES_UNAVAILABLE"
    assert result.data["source_count"] == 0
    assert len(result.data["providers"]) == 2


def test_wttr_codes_are_normalized_to_the_frontend_wmo_contract():
    assert _wttr_code("113") == 0
    assert _wttr_code("116") == 2
    assert _wttr_code("386") == 95
    assert _wttr_code("unknown", "小雨") == 61


def test_wttr_hourly_payload_is_normalized_without_invalid_times():
    samples = _wttr_samples(
        [
            {
                "date": "2026-09-02",
                "hourly": [
                    {
                        "time": "300",
                        "tempC": "26",
                        "weatherCode": "116",
                        "chanceofrain": "20",
                    }
                ],
            }
        ],
        1,
    )

    assert samples == [
        {
            "sampled_at": "2026-09-02T03:00:00",
            "temperature_c": 26,
            "precipitation_probability": 20,
            "weather_code": 2,
            "wind_speed_kmh": None,
            "condition": None,
        }
    ]
