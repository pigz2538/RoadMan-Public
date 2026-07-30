"""Open-Meteo API client implementation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from open_meteo_skill.constants import (
    AIR_QUALITY_URL,
    ARCHIVE_URL,
    BASE_URL,
    CLIMATE_URL,
    ENSEMBLE_URL,
    FLOOD_URL,
    GEOCODING_URL,
    MARINE_URL,
    SEASONAL_URL,
    CellSelection,
    Domain,
    PrecipitationUnit,
    TemperatureUnit,
    TimeFormat,
    WindSpeedUnit,
)
from open_meteo_skill.models import (
    AirQualityResponse,
    ClimateResponse,
    ElevationResponse,
    EnsembleResponse,
    FloodResponse,
    ForecastResponse,
    GeocodingResponse,
    HistoricalWeatherResponse,
    MarineResponse,
    SeasonalForecastResponse,
)
from open_meteo_skill.exceptions import APIError, ConnectionError, TimeoutError, ValidationError
from open_meteo_skill.models import APIErrorResponse

if TYPE_CHECKING:
    from collections.abc import Sequence


class OpenMeteoClient:
    """Open-Meteo API client.

    Provides access to all Open-Meteo APIs including weather forecast,
    historical data, air quality, geocoding, and more.

    Example:
        >>> client = OpenMeteoClient()
        >>> forecast = client.get_forecast(latitude=52.52, longitude=13.41)
        >>> print(forecast.current.temperature_2m)
    """

    def __init__(
        self,
        timeout: float = 30.0,
        api_key: str | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            timeout: Request timeout in seconds.
            api_key: Optional API key for commercial use.
        """
        self.timeout = timeout
        self.api_key = api_key
        self._sync_client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    def _get_sync_client(self) -> httpx.Client:
        """Get or create synchronous HTTP client."""
        if self._sync_client is None:
            self._sync_client = httpx.Client(timeout=self.timeout)
        return self._sync_client

    def _get_async_client(self) -> httpx.AsyncClient:
        """Get or create asynchronous HTTP client."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self.timeout)
        return self._async_client

    def _build_params(self, **kwargs: Any) -> dict[str, Any]:
        """Build request parameters, filtering out None values."""
        params = {}
        for key, value in kwargs.items():
            if value is None:
                continue
            if isinstance(value, list):
                params[key] = ",".join(str(v) for v in value)
            elif isinstance(value, bool):
                params[key] = "true" if value else "false"
            else:
                params[key] = str(value)
        return params

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """Handle API response and errors."""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            try:
                error_data = e.response.json()
                error = APIErrorResponse.model_validate(error_data)
                raise APIError(error.reason, status_code=e.response.status_code) from e
            except (ValueError, KeyError):
                raise APIError(
                    f"HTTP {e.response.status_code}: {e.response.text}",
                    status_code=e.response.status_code,
                ) from e

        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise APIError(data.get("reason", "Unknown API error"))

        return data

    def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make synchronous HTTP request."""
        client = self._get_sync_client()
        try:
            response = client.request(method, url, params=params)
            return self._handle_response(response)
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Request timed out: {e}") from e
        except httpx.ConnectError as e:
            raise ConnectionError(f"Connection failed: {e}") from e

    async def _arequest(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make asynchronous HTTP request."""
        client = self._get_async_client()
        try:
            response = await client.request(method, url, params=params)
            return self._handle_response(response)
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Request timed out: {e}") from e
        except httpx.ConnectError as e:
            raise ConnectionError(f"Connection failed: {e}") from e

    def close(self) -> None:
        """Close synchronous HTTP client."""
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None

    async def aclose(self) -> None:
        """Close asynchronous HTTP client."""
        if self._async_client:
            await self._async_client.aclose()
            self._async_client = None

    def __enter__(self) -> OpenMeteoClient:
        """Context manager entry."""
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit."""
        self.close()

    async def __aenter__(self) -> OpenMeteoClient:
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Async context manager exit."""
        await self.aclose()

    # ==================== Weather Forecast API ====================

    def get_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        hourly: list[str] | None = None,
        daily: list[str] | None = None,
        current: list[str] | None = None,
        forecast_days: int = 7,
        past_days: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KMH,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
        cell_selection: CellSelection | None = None,
    ) -> ForecastResponse:
        """Get weather forecast.

        Args:
            latitude: Latitude coordinate.
            longitude: Longitude coordinate.
            hourly: List of hourly variables to retrieve.
            daily: List of daily variables to retrieve.
            current: List of current weather variables.
            forecast_days: Number of forecast days (1-16).
            past_days: Number of past days to include.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            models: List of weather models to use.
            timezone: Timezone for the data.
            temperature_unit: Temperature unit.
            wind_speed_unit: Wind speed unit.
            precipitation_unit: Precipitation unit.
            timeformat: Time format.
            cell_selection: Cell selection method.

        Returns:
            ForecastResponse object containing the forecast data.

        Raises:
            ValidationError: If parameters are invalid.
            APIError: If the API returns an error.
        """
        if not -90 <= latitude <= 90:
            raise ValidationError("Latitude must be between -90 and 90")
        if not -180 <= longitude <= 180:
            raise ValidationError("Longitude must be between -180 and 180")
        if forecast_days < 1 or forecast_days > 16:
            raise ValidationError("forecast_days must be between 1 and 16")

        url = f"{BASE_URL}/v1/forecast"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            hourly=hourly,
            daily=daily,
            current=current,
            forecast_days=forecast_days,
            past_days=past_days,
            start_date=start_date,
            end_date=end_date,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            wind_speed_unit=wind_speed_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
            cell_selection=cell_selection.value if cell_selection else None,
        )

        data = self._request("GET", url, params)
        return ForecastResponse.model_validate(data)

    async def aget_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        hourly: list[str] | None = None,
        daily: list[str] | None = None,
        current: list[str] | None = None,
        forecast_days: int = 7,
        past_days: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KMH,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
        cell_selection: CellSelection | None = None,
    ) -> ForecastResponse:
        """Async version of get_forecast."""
        if not -90 <= latitude <= 90:
            raise ValidationError("Latitude must be between -90 and 90")
        if not -180 <= longitude <= 180:
            raise ValidationError("Longitude must be between -180 and 180")
        if forecast_days < 1 or forecast_days > 16:
            raise ValidationError("forecast_days must be between 1 and 16")

        url = f"{BASE_URL}/v1/forecast"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            hourly=hourly,
            daily=daily,
            current=current,
            forecast_days=forecast_days,
            past_days=past_days,
            start_date=start_date,
            end_date=end_date,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            wind_speed_unit=wind_speed_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
            cell_selection=cell_selection.value if cell_selection else None,
        )

        data = await self._arequest("GET", url, params)
        return ForecastResponse.model_validate(data)

    # ==================== Historical Weather API ====================

    def get_historical_weather(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        end_date: str,
        *,
        hourly: list[str] | None = None,
        daily: list[str] | None = None,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KMH,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
        cell_selection: CellSelection | None = None,
    ) -> HistoricalWeatherResponse:
        """Get historical weather data.

        Args:
            latitude: Latitude coordinate.
            longitude: Longitude coordinate.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            hourly: List of hourly variables to retrieve.
            daily: List of daily variables to retrieve.
            models: List of reanalysis models to use.
            timezone: Timezone for the data.
            temperature_unit: Temperature unit.
            wind_speed_unit: Wind speed unit.
            precipitation_unit: Precipitation unit.
            timeformat: Time format.
            cell_selection: Cell selection method.

        Returns:
            HistoricalWeatherResponse object containing the historical data.
        """
        url = f"{ARCHIVE_URL}/v1/archive"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            start_date=start_date,
            end_date=end_date,
            hourly=hourly,
            daily=daily,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            wind_speed_unit=wind_speed_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
            cell_selection=cell_selection.value if cell_selection else None,
        )

        data = self._request("GET", url, params)
        return HistoricalWeatherResponse.model_validate(data)

    async def aget_historical_weather(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        end_date: str,
        *,
        hourly: list[str] | None = None,
        daily: list[str] | None = None,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KMH,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
        cell_selection: CellSelection | None = None,
    ) -> HistoricalWeatherResponse:
        """Async version of get_historical_weather."""
        url = f"{ARCHIVE_URL}/v1/archive"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            start_date=start_date,
            end_date=end_date,
            hourly=hourly,
            daily=daily,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            wind_speed_unit=wind_speed_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
            cell_selection=cell_selection.value if cell_selection else None,
        )

        data = await self._arequest("GET", url, params)
        return HistoricalWeatherResponse.model_validate(data)

    # ==================== Air Quality API ====================

    def get_air_quality(
        self,
        latitude: float,
        longitude: float,
        *,
        hourly: list[str] | None = None,
        current: list[str] | None = None,
        forecast_days: int = 5,
        past_days: int = 0,
        domains: Domain = Domain.AUTO,
        timezone: str = "GMT",
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> AirQualityResponse:
        """Get air quality forecast.

        Args:
            latitude: Latitude coordinate.
            longitude: Longitude coordinate.
            hourly: List of hourly air quality variables.
            current: List of current air quality variables.
            forecast_days: Number of forecast days.
            past_days: Number of past days to include.
            domains: Domain for air quality data.
            timezone: Timezone for the data.
            timeformat: Time format.

        Returns:
            AirQualityResponse object containing the air quality data.
        """
        url = f"{AIR_QUALITY_URL}/v1/air-quality"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            hourly=hourly,
            current=current,
            forecast_days=forecast_days,
            past_days=past_days,
            domains=domains.value,
            timezone=timezone,
            timeformat=timeformat.value,
        )

        data = self._request("GET", url, params)
        return AirQualityResponse.model_validate(data)

    async def aget_air_quality(
        self,
        latitude: float,
        longitude: float,
        *,
        hourly: list[str] | None = None,
        current: list[str] | None = None,
        forecast_days: int = 5,
        past_days: int = 0,
        domains: Domain = Domain.AUTO,
        timezone: str = "GMT",
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> AirQualityResponse:
        """Async version of get_air_quality."""
        url = f"{AIR_QUALITY_URL}/v1/air-quality"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            hourly=hourly,
            current=current,
            forecast_days=forecast_days,
            past_days=past_days,
            domains=domains.value,
            timezone=timezone,
            timeformat=timeformat.value,
        )

        data = await self._arequest("GET", url, params)
        return AirQualityResponse.model_validate(data)

    # ==================== Geocoding API ====================

    def search_location(
        self,
        name: str,
        *,
        count: int = 10,
        language: str = "en",
        format: str = "json",
        country_code: str | None = None,
    ) -> GeocodingResponse:
        """Search for locations by name.

        Args:
            name: Location name or postal code to search for.
            count: Number of results to return (max 100).
            language: Language for the results.
            format: Response format (json or protobuf).
            country_code: ISO 3166-1 alpha-2 country code to filter results.

        Returns:
            GeocodingResponse object containing the search results.
        """
        url = f"{GEOCODING_URL}/v1/search"
        params = self._build_params(
            name=name,
            count=min(count, 100),
            language=language,
            format=format,
            countryCode=country_code,
        )

        data = self._request("GET", url, params)
        return GeocodingResponse.model_validate(data)

    async def asearch_location(
        self,
        name: str,
        *,
        count: int = 10,
        language: str = "en",
        format: str = "json",
        country_code: str | None = None,
    ) -> GeocodingResponse:
        """Async version of search_location."""
        url = f"{GEOCODING_URL}/v1/search"
        params = self._build_params(
            name=name,
            count=min(count, 100),
            language=language,
            format=format,
            countryCode=country_code,
        )

        data = await self._arequest("GET", url, params)
        return GeocodingResponse.model_validate(data)

    # ==================== Elevation API ====================

    def get_elevation(
        self,
        latitudes: Sequence[float],
        longitudes: Sequence[float],
    ) -> ElevationResponse:
        """Get elevation data for coordinates.

        Args:
            latitudes: List of latitude coordinates.
            longitudes: List of longitude coordinates.

        Returns:
            ElevationResponse object containing the elevation data.

        Raises:
            ValidationError: If latitudes and longitudes have different lengths.
        """
        if len(latitudes) != len(longitudes):
            raise ValidationError("Latitudes and longitudes must have the same length")
        if len(latitudes) > 100:
            raise ValidationError("Maximum 100 coordinates allowed")

        url = f"{BASE_URL}/v1/elevation"
        params = self._build_params(
            latitude=list(latitudes),
            longitude=list(longitudes),
        )

        data = self._request("GET", url, params)
        return ElevationResponse.model_validate(data)

    async def aget_elevation(
        self,
        latitudes: Sequence[float],
        longitudes: Sequence[float],
    ) -> ElevationResponse:
        """Async version of get_elevation."""
        if len(latitudes) != len(longitudes):
            raise ValidationError("Latitudes and longitudes must have the same length")
        if len(latitudes) > 100:
            raise ValidationError("Maximum 100 coordinates allowed")

        url = f"{BASE_URL}/v1/elevation"
        params = self._build_params(
            latitude=list(latitudes),
            longitude=list(longitudes),
        )

        data = await self._arequest("GET", url, params)
        return ElevationResponse.model_validate(data)

    # ==================== Marine Weather API ====================

    def get_marine_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        hourly: list[str] | None = None,
        daily: list[str] | None = None,
        forecast_days: int = 7,
        past_days: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        timezone: str = "GMT",
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> MarineResponse:
        """Get marine weather forecast.

        Args:
            latitude: Latitude coordinate.
            longitude: Longitude coordinate.
            hourly: List of hourly marine variables.
            daily: List of daily marine variables.
            forecast_days: Number of forecast days.
            past_days: Number of past days to include.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            timezone: Timezone for the data.
            timeformat: Time format.

        Returns:
            MarineResponse object containing the marine forecast data.
        """
        url = f"{MARINE_URL}/v1/marine"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            hourly=hourly,
            daily=daily,
            forecast_days=forecast_days,
            past_days=past_days,
            start_date=start_date,
            end_date=end_date,
            timezone=timezone,
            timeformat=timeformat.value,
        )

        data = self._request("GET", url, params)
        return MarineResponse.model_validate(data)

    async def aget_marine_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        hourly: list[str] | None = None,
        daily: list[str] | None = None,
        forecast_days: int = 7,
        past_days: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        timezone: str = "GMT",
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> MarineResponse:
        """Async version of get_marine_forecast."""
        url = f"{MARINE_URL}/v1/marine"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            hourly=hourly,
            daily=daily,
            forecast_days=forecast_days,
            past_days=past_days,
            start_date=start_date,
            end_date=end_date,
            timezone=timezone,
            timeformat=timeformat.value,
        )

        data = await self._arequest("GET", url, params)
        return MarineResponse.model_validate(data)

    # ==================== Ensemble Forecast API ====================

    def get_ensemble_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        hourly: list[str] | None = None,
        forecast_days: int = 7,
        past_days: int = 0,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KMH,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> EnsembleResponse:
        """Get ensemble weather forecast.

        Args:
            latitude: Latitude coordinate.
            longitude: Longitude coordinate.
            hourly: List of hourly ensemble variables.
            forecast_days: Number of forecast days.
            past_days: Number of past days to include.
            models: List of ensemble models.
            timezone: Timezone for the data.
            temperature_unit: Temperature unit.
            wind_speed_unit: Wind speed unit.
            precipitation_unit: Precipitation unit.
            timeformat: Time format.

        Returns:
            EnsembleResponse object containing the ensemble forecast data.
        """
        url = f"{ENSEMBLE_URL}/v1/ensemble"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            hourly=hourly,
            forecast_days=forecast_days,
            past_days=past_days,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            wind_speed_unit=wind_speed_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
        )

        data = self._request("GET", url, params)
        return EnsembleResponse.model_validate(data)

    async def aget_ensemble_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        hourly: list[str] | None = None,
        forecast_days: int = 7,
        past_days: int = 0,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KMH,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> EnsembleResponse:
        """Async version of get_ensemble_forecast."""
        url = f"{ENSEMBLE_URL}/v1/ensemble"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            hourly=hourly,
            forecast_days=forecast_days,
            past_days=past_days,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            wind_speed_unit=wind_speed_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
        )

        data = await self._arequest("GET", url, params)
        return EnsembleResponse.model_validate(data)

    # ==================== Seasonal Forecast API ====================

    def get_seasonal_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        daily: list[str] | None = None,
        forecast_days: int = 210,
        past_days: int = 0,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KMH,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> SeasonalForecastResponse:
        """Get seasonal weather forecast.

        Args:
            latitude: Latitude coordinate.
            longitude: Longitude coordinate.
            daily: List of daily seasonal variables.
            forecast_days: Number of forecast days.
            past_days: Number of past days to include.
            models: List of seasonal models.
            timezone: Timezone for the data.
            temperature_unit: Temperature unit.
            wind_speed_unit: Wind speed unit.
            precipitation_unit: Precipitation unit.
            timeformat: Time format.

        Returns:
            SeasonalForecastResponse object containing the seasonal forecast data.
        """
        url = f"{SEASONAL_URL}/v1/seasonal"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            daily=daily,
            forecast_days=forecast_days,
            past_days=past_days,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            wind_speed_unit=wind_speed_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
        )

        data = self._request("GET", url, params)
        return SeasonalForecastResponse.model_validate(data)

    async def aget_seasonal_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        daily: list[str] | None = None,
        forecast_days: int = 210,
        past_days: int = 0,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KMH,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> SeasonalForecastResponse:
        """Async version of get_seasonal_forecast."""
        url = f"{SEASONAL_URL}/v1/seasonal"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            daily=daily,
            forecast_days=forecast_days,
            past_days=past_days,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            wind_speed_unit=wind_speed_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
        )

        data = await self._arequest("GET", url, params)
        return SeasonalForecastResponse.model_validate(data)

    # ==================== Climate Change API ====================

    def get_climate_data(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        end_date: str,
        *,
        daily: list[str] | None = None,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> ClimateResponse:
        """Get climate change data.

        Args:
            latitude: Latitude coordinate.
            longitude: Longitude coordinate.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            daily: List of daily climate variables.
            models: List of climate models.
            timezone: Timezone for the data.
            temperature_unit: Temperature unit.
            precipitation_unit: Precipitation unit.
            timeformat: Time format.

        Returns:
            ClimateResponse object containing the climate data.
        """
        url = f"{CLIMATE_URL}/v1/climate"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            start_date=start_date,
            end_date=end_date,
            daily=daily,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
        )

        data = self._request("GET", url, params)
        return ClimateResponse.model_validate(data)

    async def aget_climate_data(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        end_date: str,
        *,
        daily: list[str] | None = None,
        models: list[str] | None = None,
        timezone: str = "GMT",
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETER,
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> ClimateResponse:
        """Async version of get_climate_data."""
        url = f"{CLIMATE_URL}/v1/climate"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            start_date=start_date,
            end_date=end_date,
            daily=daily,
            models=models,
            timezone=timezone,
            temperature_unit=temperature_unit.value,
            precipitation_unit=precipitation_unit.value,
            timeformat=timeformat.value,
        )

        data = await self._arequest("GET", url, params)
        return ClimateResponse.model_validate(data)

    # ==================== Flood API ====================

    def get_flood_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        daily: list[str] | None = None,
        forecast_days: int = 30,
        past_days: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        models: list[str] | None = None,
        timezone: str = "GMT",
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> FloodResponse:
        """Get flood forecast.

        Args:
            latitude: Latitude coordinate.
            longitude: Longitude coordinate.
            daily: List of daily flood variables.
            forecast_days: Number of forecast days.
            past_days: Number of past days to include.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            models: List of flood models.
            timezone: Timezone for the data.
            timeformat: Time format.

        Returns:
            FloodResponse object containing the flood forecast data.
        """
        url = f"{FLOOD_URL}/v1/flood"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            daily=daily,
            forecast_days=forecast_days,
            past_days=past_days,
            start_date=start_date,
            end_date=end_date,
            models=models,
            timezone=timezone,
            timeformat=timeformat.value,
        )

        data = self._request("GET", url, params)
        return FloodResponse.model_validate(data)

    async def aget_flood_forecast(
        self,
        latitude: float,
        longitude: float,
        *,
        daily: list[str] | None = None,
        forecast_days: int = 30,
        past_days: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        models: list[str] | None = None,
        timezone: str = "GMT",
        timeformat: TimeFormat = TimeFormat.ISO8601,
    ) -> FloodResponse:
        """Async version of get_flood_forecast."""
        url = f"{FLOOD_URL}/v1/flood"
        params = self._build_params(
            latitude=latitude,
            longitude=longitude,
            daily=daily,
            forecast_days=forecast_days,
            past_days=past_days,
            start_date=start_date,
            end_date=end_date,
            models=models,
            timezone=timezone,
            timeformat=timeformat.value,
        )

        data = await self._arequest("GET", url, params)
        return FloodResponse.model_validate(data)
