"""Open-Meteo API Skill for LLM.

A comprehensive Python SDK for accessing Open-Meteo weather APIs.
"""

from open_meteo_skill.client import OpenMeteoClient
from open_meteo_skill.exceptions import (
    OpenMeteoError,
    APIError,
    ValidationError,
    RateLimitError,
)
from open_meteo_skill.models import (
    ForecastResponse,
    HistoricalWeatherResponse,
    AirQualityResponse,
    GeocodingResponse,
    ElevationResponse,
    MarineResponse,
    EnsembleResponse,
    SeasonalForecastResponse,
    ClimateResponse,
    FloodResponse,
)

__version__ = "0.1.0"
__all__ = [
    "OpenMeteoClient",
    "OpenMeteoError",
    "APIError",
    "ValidationError",
    "RateLimitError",
    "ForecastResponse",
    "HistoricalWeatherResponse",
    "AirQualityResponse",
    "GeocodingResponse",
    "ElevationResponse",
    "MarineResponse",
    "EnsembleResponse",
    "SeasonalForecastResponse",
    "ClimateResponse",
    "FloodResponse",
]
