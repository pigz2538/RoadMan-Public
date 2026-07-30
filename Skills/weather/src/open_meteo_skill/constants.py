"""Constants and enums for Open-Meteo API."""

from enum import Enum
from typing import Final


class TemperatureUnit(str, Enum):
    """Temperature unit options."""

    CELSIUS = "celsius"
    FAHRENHEIT = "fahrenheit"


class WindSpeedUnit(str, Enum):
    """Wind speed unit options."""

    KMH = "kmh"
    MPH = "mph"
    MS = "ms"
    KNOTS = "kn"


class PrecipitationUnit(str, Enum):
    """Precipitation unit options."""

    MILLIMETER = "mm"
    INCH = "inch"


class TimeFormat(str, Enum):
    """Time format options."""

    ISO8601 = "iso8601"
    UNIXTIME = "unixtime"


class Domain(str, Enum):
    """Air quality domain options."""

    AUTO = "auto"
    CAMS_EUROPE = "cams_europe"
    CAMS_GLOBAL = "cams_global"


class CellSelection(str, Enum):
    """Cell selection method for weather models."""

    LAND = "land"
    SEA = "sea"
    NEAREST = "nearest"


# API Base URLs
BASE_URL: Final[str] = "https://api.open-meteo.com"
ARCHIVE_URL: Final[str] = "https://archive-api.open-meteo.com"
AIR_QUALITY_URL: Final[str] = "https://air-quality-api.open-meteo.com"
GEOCODING_URL: Final[str] = "https://geocoding-api.open-meteo.com"
MARINE_URL: Final[str] = "https://marine-api.open-meteo.com"
ENSEMBLE_URL: Final[str] = "https://ensemble-api.open-meteo.com"
SEASONAL_URL: Final[str] = "https://seasonal-api.open-meteo.com"
CLIMATE_URL: Final[str] = "https://climate-api.open-meteo.com"
FLOOD_URL: Final[str] = "https://flood-api.open-meteo.com"

# Hourly Weather Variables
HOURLY_WEATHER_VARIABLES: Final[list[str]] = [
    # Temperature
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    # Pressure
    "pressure_msl",
    "surface_pressure",
    # Cloud
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    # Wind
    "wind_speed_10m",
    "wind_speed_80m",
    "wind_speed_120m",
    "wind_speed_180m",
    "wind_direction_10m",
    "wind_direction_80m",
    "wind_direction_120m",
    "wind_direction_180m",
    "wind_gusts_10m",
    # Solar/Radiation
    "shortwave_radiation",
    "direct_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "global_tilted_irradiance",
    "terrestrial_radiation",
    "shortwave_radiation_instant",
    "direct_radiation_instant",
    "direct_normal_irradiance_instant",
    "diffuse_radiation_instant",
    "global_tilted_irradiance_instant",
    "terrestrial_radiation_instant",
    # Precipitation
    "precipitation",
    "precipitation_probability",
    "rain",
    "showers",
    "snowfall",
    "snow_depth",
    # Weather
    "weather_code",
    "visibility",
    "evapotranspiration",
    "et0_fao_evapotranspiration",
    # Other
    "vapour_pressure_deficit",
    "cape",
    "freezing_level_height",
    "is_day",
    "sunshine_duration",
    # Soil
    "soil_temperature_0cm",
    "soil_temperature_6cm",
    "soil_temperature_18cm",
    "soil_temperature_54cm",
    "soil_moisture_0_to_1cm",
    "soil_moisture_1_to_3cm",
    "soil_moisture_3_to_9cm",
    "soil_moisture_9_to_27cm",
    "soil_moisture_27_to_81cm",
]

# Daily Weather Variables
DAILY_WEATHER_VARIABLES: Final[list[str]] = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "apparent_temperature_mean",
    "sunrise",
    "sunset",
    "daylight_duration",
    "sunshine_duration",
    "precipitation_sum",
    "rain_sum",
    "showers_sum",
    "snowfall_sum",
    "precipitation_hours",
    "precipitation_probability_max",
    "precipitation_probability_min",
    "precipitation_probability_mean",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "shortwave_radiation_sum",
    "et0_fao_evapotranspiration",
    "uv_index_max",
    "uv_index_clear_sky_max",
]

# Current Weather Variables
CURRENT_WEATHER_VARIABLES: Final[list[str]] = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "is_day",
    "precipitation",
    "rain",
    "showers",
    "snowfall",
    "weather_code",
    "cloud_cover",
    "pressure_msl",
    "surface_pressure",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
]

# Air Quality Variables
AIR_QUALITY_VARIABLES: Final[list[str]] = [
    "pm10",
    "pm2_5",
    "carbon_monoxide",
    "carbon_dioxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
    "aerosol_optical_depth",
    "dust",
    "uv_index",
    "uv_index_clear_sky",
    "ammonia",
    "methane",
    "alder_pollen",
    "birch_pollen",
    "grass_pollen",
    "mugwort_pollen",
    "olive_pollen",
    "ragweed_pollen",
    "european_aqi",
    "european_aqi_pm2_5",
    "european_aqi_pm10",
    "european_aqi_nitrogen_dioxide",
    "european_aqi_ozone",
    "us_aqi",
    "us_aqi_pm2_5",
    "us_aqi_pm10",
    "us_aqi_carbon_monoxide",
    "us_aqi_nitrogen_dioxide",
    "us_aqi_ozone",
    "us_aqi_sulphur_dioxide",
]

# Marine Variables
MARINE_VARIABLES: Final[list[str]] = [
    "wave_height",
    "wave_direction",
    "wave_period",
    "wind_wave_height",
    "wind_wave_direction",
    "wind_wave_period",
    "wind_wave_peak_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
    "swell_wave_peak_period",
    "ocean_current_velocity",
    "ocean_current_direction",
    "sea_level_height_msl",
    "sea_surface_temperature",
    "sea_ice_concentration",
]

# Flood Variables
FLOOD_VARIABLES: Final[list[str]] = [
    "river_discharge",
    "river_discharge_mean",
    "river_discharge_median",
    "river_discharge_max",
    "river_discharge_min",
    "river_discharge_p25",
    "river_discharge_p75",
]

# Weather Models
WEATHER_MODELS: Final[list[str]] = [
    "best_match",
    "ecmwf_ifs04",
    "ecmwf_ifs025",
    "ecmwf_aifs025",
    "cma_grapes_global",
    "bom_access_global",
    "gfs_seamless",
    "gfs_global",
    "gfs_hrrr",
    "ncep_nbm_conus",
    "gfs_graphcast025",
    "meteofrance_seamless",
    "meteofrance_arpege_world",
    "meteofrance_arpege_europe",
    "meteofrance_arome_france",
    "meteofrance_arome_france_hd",
    "arpae_cosmo_seamless",
    "arpae_cosmo_2i",
    "arpae_cosmo_2i_ruc",
    "metno_seamless",
    "metno_nordic",
    "ukmo_seamless",
    "ukmo_global_deterministic_10km",
    "ukmo_uk_deterministic_2km",
    "jma_seamless",
    "jma_msm",
    "jma_gsm",
    "gem_seamless",
    "gem_global",
    "gem_regional",
    "gem_hrdps_continental",
    "icon_seamless",
    "icon_global",
    "icon_eu",
    "icon_d2",
    "dwd_ecmwf_euro_euro",
    "dwd_ecmwf_central_europe",
    "dwd_icon",
    "dwd_icon_eu",
    "dwd_icon_d2",
    "knmi_seamless",
    "knmi_harmonie_arome_euro",
    "knmi_harmonie_arome_netherlands",
    "dmi_seamless",
    "dmi_harmonie_arome_europe",
]
