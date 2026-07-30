"""Pydantic models for Open-Meteo API responses."""

from typing import Any

from pydantic import BaseModel, Field


class HourlyData(BaseModel):
    """Hourly weather data."""

    time: list[str]
    temperature_2m: list[float | None] | None = None
    relative_humidity_2m: list[float | None] | None = None
    dew_point_2m: list[float | None] | None = None
    apparent_temperature: list[float | None] | None = None
    pressure_msl: list[float | None] | None = None
    surface_pressure: list[float | None] | None = None
    cloud_cover: list[float | None] | None = None
    cloud_cover_low: list[float | None] | None = None
    cloud_cover_mid: list[float | None] | None = None
    cloud_cover_high: list[float | None] | None = None
    wind_speed_10m: list[float | None] | None = None
    wind_speed_80m: list[float | None] | None = None
    wind_speed_120m: list[float | None] | None = None
    wind_speed_180m: list[float | None] | None = None
    wind_direction_10m: list[float | None] | None = None
    wind_direction_80m: list[float | None] | None = None
    wind_direction_120m: list[float | None] | None = None
    wind_direction_180m: list[float | None] | None = None
    wind_gusts_10m: list[float | None] | None = None
    shortwave_radiation: list[float | None] | None = None
    direct_radiation: list[float | None] | None = None
    direct_normal_irradiance: list[float | None] | None = None
    diffuse_radiation: list[float | None] | None = None
    global_tilted_irradiance: list[float | None] | None = None
    precipitation: list[float | None] | None = None
    precipitation_probability: list[float | None] | None = None
    rain: list[float | None] | None = None
    showers: list[float | None] | None = None
    snowfall: list[float | None] | None = None
    snow_depth: list[float | None] | None = None
    weather_code: list[int | None] | None = None
    visibility: list[float | None] | None = None
    evapotranspiration: list[float | None] | None = None
    et0_fao_evapotranspiration: list[float | None] | None = None
    vapour_pressure_deficit: list[float | None] | None = None
    cape: list[float | None] | None = None
    freezing_level_height: list[float | None] | None = None
    is_day: list[int | None] | None = None
    sunshine_duration: list[float | None] | None = None
    soil_temperature_0cm: list[float | None] | None = None
    soil_temperature_6cm: list[float | None] | None = None
    soil_temperature_18cm: list[float | None] | None = None
    soil_temperature_54cm: list[float | None] | None = None
    soil_moisture_0_to_1cm: list[float | None] | None = None
    soil_moisture_1_to_3cm: list[float | None] | None = None
    soil_moisture_3_to_9cm: list[float | None] | None = None
    soil_moisture_9_to_27cm: list[float | None] | None = None
    soil_moisture_27_to_81cm: list[float | None] | None = None


class DailyData(BaseModel):
    """Daily weather data."""

    time: list[str]
    weather_code: list[int | None] | None = None
    temperature_2m_max: list[float | None] | None = None
    temperature_2m_min: list[float | None] | None = None
    temperature_2m_mean: list[float | None] | None = None
    apparent_temperature_max: list[float | None] | None = None
    apparent_temperature_min: list[float | None] | None = None
    apparent_temperature_mean: list[float | None] | None = None
    sunrise: list[str | None] | None = None
    sunset: list[str | None] | None = None
    daylight_duration: list[float | None] | None = None
    sunshine_duration: list[float | None] | None = None
    precipitation_sum: list[float | None] | None = None
    rain_sum: list[float | None] | None = None
    showers_sum: list[float | None] | None = None
    snowfall_sum: list[float | None] | None = None
    precipitation_hours: list[float | None] | None = None
    precipitation_probability_max: list[float | None] | None = None
    precipitation_probability_min: list[float | None] | None = None
    precipitation_probability_mean: list[float | None] | None = None
    wind_speed_10m_max: list[float | None] | None = None
    wind_gusts_10m_max: list[float | None] | None = None
    wind_direction_10m_dominant: list[float | None] | None = None
    shortwave_radiation_sum: list[float | None] | None = None
    et0_fao_evapotranspiration: list[float | None] | None = None
    uv_index_max: list[float | None] | None = None
    uv_index_clear_sky_max: list[float | None] | None = None


class CurrentWeather(BaseModel):
    """Current weather conditions."""

    time: str
    interval: int
    temperature_2m: float | None = None
    relative_humidity_2m: float | None = None
    apparent_temperature: float | None = None
    is_day: int | None = None
    precipitation: float | None = None
    rain: float | None = None
    showers: float | None = None
    snowfall: float | None = None
    weather_code: int | None = None
    cloud_cover: float | None = None
    pressure_msl: float | None = None
    surface_pressure: float | None = None
    wind_speed_10m: float | None = None
    wind_direction_10m: float | None = None
    wind_gusts_10m: float | None = None


class HourlyUnits(BaseModel):
    """Units for hourly data."""

    time: str
    temperature_2m: str | None = None
    relative_humidity_2m: str | None = None
    dew_point_2m: str | None = None
    apparent_temperature: str | None = None
    pressure_msl: str | None = None
    surface_pressure: str | None = None
    cloud_cover: str | None = None
    wind_speed_10m: str | None = None
    wind_direction_10m: str | None = None
    precipitation: str | None = None
    rain: str | None = None
    showers: str | None = None
    snowfall: str | None = None
    weather_code: str | None = None


class DailyUnits(BaseModel):
    """Units for daily data."""

    time: str
    weather_code: str | None = None
    temperature_2m_max: str | None = None
    temperature_2m_min: str | None = None
    sunrise: str | None = None
    sunset: str | None = None
    precipitation_sum: str | None = None


class ForecastResponse(BaseModel):
    """Weather forecast API response."""

    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    current: CurrentWeather | None = None
    hourly: HourlyData | None = None
    hourly_units: HourlyUnits | None = None
    daily: DailyData | None = None
    daily_units: DailyUnits | None = None


class HistoricalWeatherResponse(BaseModel):
    """Historical weather API response."""

    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    hourly: HourlyData | None = None
    hourly_units: HourlyUnits | None = None
    daily: DailyData | None = None
    daily_units: DailyUnits | None = None


class AirQualityData(BaseModel):
    """Air quality hourly data."""

    time: list[str]
    pm10: list[float | None] | None = None
    pm2_5: list[float | None] | None = None
    carbon_monoxide: list[float | None] | None = None
    carbon_dioxide: list[float | None] | None = None
    nitrogen_dioxide: list[float | None] | None = None
    sulphur_dioxide: list[float | None] | None = None
    ozone: list[float | None] | None = None
    aerosol_optical_depth: list[float | None] | None = None
    dust: list[float | None] | None = None
    uv_index: list[float | None] | None = None
    uv_index_clear_sky: list[float | None] | None = None
    ammonia: list[float | None] | None = None
    methane: list[float | None] | None = None
    alder_pollen: list[float | None] | None = None
    birch_pollen: list[float | None] | None = None
    grass_pollen: list[float | None] | None = None
    mugwort_pollen: list[float | None] | None = None
    olive_pollen: list[float | None] | None = None
    ragweed_pollen: list[float | None] | None = None
    european_aqi: list[int | None] | None = None
    european_aqi_pm2_5: list[int | None] | None = None
    european_aqi_pm10: list[int | None] | None = None
    european_aqi_nitrogen_dioxide: list[int | None] | None = None
    european_aqi_ozone: list[int | None] | None = None
    us_aqi: list[int | None] | None = None
    us_aqi_pm2_5: list[int | None] | None = None
    us_aqi_pm10: list[int | None] | None = None
    us_aqi_carbon_monoxide: list[int | None] | None = None
    us_aqi_nitrogen_dioxide: list[int | None] | None = None
    us_aqi_ozone: list[int | None] | None = None
    us_aqi_sulphur_dioxide: list[int | None] | None = None


class CurrentAirQuality(BaseModel):
    """Current air quality conditions."""

    time: str
    interval: int
    pm10: float | None = None
    pm2_5: float | None = None
    carbon_monoxide: float | None = None
    nitrogen_dioxide: float | None = None
    sulphur_dioxide: float | None = None
    ozone: float | None = None
    aerosol_optical_depth: float | None = None
    dust: float | None = None
    uv_index: float | None = None
    uv_index_clear_sky: float | None = None
    ammonia: float | None = None
    alder_pollen: float | None = None
    birch_pollen: float | None = None
    grass_pollen: float | None = None
    mugwort_pollen: float | None = None
    olive_pollen: float | None = None
    ragweed_pollen: float | None = None
    european_aqi: int | None = None
    us_aqi: int | None = None


class AirQualityResponse(BaseModel):
    """Air quality API response."""

    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    current: CurrentAirQuality | None = None
    hourly: AirQualityData | None = None
    hourly_units: dict[str, str] | None = None


class GeocodingResult(BaseModel):
    """Geocoding search result."""

    id: int
    name: str
    latitude: float
    longitude: float
    elevation: float | None = None
    feature_code: str | None = None
    country_code: str | None = None
    admin1_id: int | None = None
    admin2_id: int | None = None
    admin3_id: int | None = None
    admin4_id: int | None = None
    timezone: str | None = None
    population: int | None = None
    country: str | None = None
    admin1: str | None = None
    admin2: str | None = None
    admin3: str | None = None
    admin4: str | None = None


class GeocodingResponse(BaseModel):
    """Geocoding API response."""

    results: list[GeocodingResult] | None = None
    generationtime_ms: float | None = None


class ElevationResponse(BaseModel):
    """Elevation API response."""

    elevation: list[float]


class MarineData(BaseModel):
    """Marine weather hourly data."""

    time: list[str]
    wave_height: list[float | None] | None = None
    wave_direction: list[float | None] | None = None
    wave_period: list[float | None] | None = None
    wind_wave_height: list[float | None] | None = None
    wind_wave_direction: list[float | None] | None = None
    wind_wave_period: list[float | None] | None = None
    wind_wave_peak_period: list[float | None] | None = None
    swell_wave_height: list[float | None] | None = None
    swell_wave_direction: list[float | None] | None = None
    swell_wave_period: list[float | None] | None = None
    swell_wave_peak_period: list[float | None] | None = None
    ocean_current_velocity: list[float | None] | None = None
    ocean_current_direction: list[float | None] | None = None
    sea_level_height_msl: list[float | None] | None = None
    sea_surface_temperature: list[float | None] | None = None
    sea_ice_concentration: list[float | None] | None = None


class MarineResponse(BaseModel):
    """Marine weather API response."""

    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    hourly: MarineData | None = None
    hourly_units: dict[str, str] | None = None


class EnsembleData(BaseModel):
    """Ensemble forecast hourly data."""

    time: list[str]
    temperature_2m: list[list[float | None]] | None = None
    precipitation: list[list[float | None]] | None = None


class EnsembleResponse(BaseModel):
    """Ensemble forecast API response."""

    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    hourly: EnsembleData | None = None
    hourly_units: dict[str, str] | None = None


class SeasonalData(BaseModel):
    """Seasonal forecast data."""

    time: list[str]
    temperature_2m_mean: list[float | None] | None = None
    temperature_2m_max: list[float | None] | None = None
    temperature_2m_min: list[float | None] | None = None
    precipitation: list[float | None] | None = None


class SeasonalForecastResponse(BaseModel):
    """Seasonal forecast API response."""

    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    daily: SeasonalData | None = None
    daily_units: dict[str, str] | None = None


class ClimateData(BaseModel):
    """Climate change data."""

    time: list[str]
    temperature_2m_mean: list[float | None] | None = None
    temperature_2m_max: list[float | None] | None = None
    temperature_2m_min: list[float | None] | None = None
    precipitation: list[float | None] | None = None


class ClimateResponse(BaseModel):
    """Climate change API response."""

    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    daily: ClimateData | None = None
    daily_units: dict[str, str] | None = None


class FloodData(BaseModel):
    """Flood forecast hourly data."""

    time: list[str]
    river_discharge: list[float | None] | None = None
    river_discharge_mean: list[float | None] | None = None
    river_discharge_median: list[float | None] | None = None
    river_discharge_max: list[float | None] | None = None
    river_discharge_min: list[float | None] | None = None
    river_discharge_p25: list[float | None] | None = None
    river_discharge_p75: list[float | None] | None = None


class FloodResponse(BaseModel):
    """Flood forecast API response."""

    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    daily: FloodData | None = None
    daily_units: dict[str, str] | None = None


class APIErrorResponse(BaseModel):
    """API error response."""

    error: bool = True
    reason: str
