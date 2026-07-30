# Weather Forecast API

**Endpoint:** `https://api.open-meteo.com/v1/forecast`

Seamless integration of high-resolution weather models with up to 16 days forecast.

## Location and Time

- **Latitude/Longitude:** WGS84 coordinates
- **Timezone:** Auto-detect or specify (IANA timezone database)
- **Forecast Days:** Default 7, max 16
- **Past Days:** Default 0, for archived forecasts

## Hourly Weather Variables

| Variable | Valid time | Unit | Description |
|----------|-----------|------|-------------|
| `temperature_2m` | Instant | °C/°F | Air temperature at 2 meters above ground |
| `relative_humidity_2m` | Instant | % | Relative humidity at 2 meters above ground |
| `dew_point_2m` | Instant | °C/°F | Dew point temperature at 2 meters above ground |
| `apparent_temperature` | Instant | °C/°F | Perceived feels-like temperature combining wind chill, humidity and solar radiation |
| `precipitation_probability` | Instant | % | Probability of precipitation (requires a weather model) |
| `precipitation` | Preceding hour sum | mm/inch | Total precipitation (rain, showers, snow) sum of preceding hour |
| `rain` | Preceding hour sum | mm/inch | Liquid precipitation of the preceding hour |
| `showers` | Preceding hour sum | mm/inch | Showers of the preceding hour |
| `snowfall` | Preceding hour sum | cm/inch | Snowfall amount of preceding hour in centimeters |
| `snow_depth` | Instant | meters | Snow depth on the ground |
| `weather_code` | Instant | WMO code | Weather condition as numeric code (WMO interpretation) |
| `pressure_msl` | Instant | hPa | Atmospheric air pressure reduced to mean sea level |
| `surface_pressure` | Instant | hPa | Surface pressure |
| `cloud_cover` | Instant | % | Total cloud cover as area fraction |
| `cloud_cover_low` | Instant | % | Low level clouds and fog up to 2 km altitude |
| `cloud_cover_mid` | Instant | % | Mid level clouds from 2 to 6 km altitude |
| `cloud_cover_high` | Instant | % | High level clouds from 6 km altitude |
| `visibility` | Instant | meters | Viewing distance |
| `evapotranspiration` | Preceding hour sum | mm/inch | Evapotranspiration from land surface and plants |
| `et0_fao_evapotranspiration` | Preceding hour sum | mm/inch | Reference Evapotranspiration (FAO-56 Penman-Monteith) |
| `vapour_pressure_deficit` | Instant | kPa | Vapor Pressure Deficit |
| `wind_speed_10m` | Instant | km/h | Wind speed at 10 meters above ground |
| `wind_speed_80m` | Instant | km/h | Wind speed at 80 meters above ground |
| `wind_speed_120m` | Instant | km/h | Wind speed at 120 meters above ground |
| `wind_speed_180m` | Instant | km/h | Wind speed at 180 meters above ground |
| `wind_direction_10m` | Instant | ° | Wind direction at 10 meters |
| `wind_direction_80m` | Instant | ° | Wind direction at 80 meters |
| `wind_direction_120m` | Instant | ° | Wind direction at 120 meters |
| `wind_direction_180m` | Instant | ° | Wind direction at 180 meters |
| `wind_gusts_10m` | Preceding hour max | km/h | Gusts at 10 meters as maximum of preceding hour |
| `temperature_80m` | Instant | °C/°F | Temperature at 80 meters above ground |
| `temperature_120m` | Instant | °C/°F | Temperature at 120 meters above ground |
| `temperature_180m` | Instant | °C/°F | Temperature at 180 meters above ground |
| `soil_temperature_0cm` | Instant | °C/°F | Temperature at surface |
| `soil_temperature_6cm` | Instant | °C/°F | Temperature at 6 cm depth |
| `soil_temperature_18cm` | Instant | °C/°F | Temperature at 18 cm depth |
| `soil_temperature_54cm` | Instant | °C/°F | Temperature at 54 cm depth |
| `soil_moisture_0_to_1cm` | Instant | m³/m³ | Soil water content at 0-1 cm |
| `soil_moisture_1_to_3cm` | Instant | m³/m³ | Soil water content at 1-3 cm |
| `soil_moisture_3_to_9cm` | Instant | m³/m³ | Soil water content at 3-9 cm |
| `soil_moisture_9_to_27cm` | Instant | m³/m³ | Soil water content at 9-27 cm |
| `soil_moisture_27_to_81cm` | Instant | m³/m³ | Soil water content at 27-81 cm |

### Additional Hourly Variables
- `uv_index`, `uv_index_clear_sky` — UV Index
- `is_day` — 1 if day, 0 if night
- `sunshine_duration` — seconds (preceding hour sum)
- `wet_bulb_temperature_2m` — °C/°F
- `cape` — J/kg, Convective Available Potential Energy
- `freezing_level_height` — meters above sea level
- `sunshine_duration` — seconds

## Solar Radiation Variables
- `shortwave_radiation` — W/m² (preceding hour mean)
- `direct_radiation`, `direct_normal_irradiance` — W/m²
- `diffuse_radiation` — W/m²
- `global_tilted_irradiance` — W/m² (requires tilt & azimuth)
- `terrestrial_radiation` — W/m²
- Instant versions available (e.g. `shortwave_radiation_instant`)

## Daily Weather Variables

| Variable | Unit | Description |
|----------|------|-------------|
| `weather_code` | WMO code | Most severe weather condition on given day |
| `temperature_2m_max`, `temperature_2m_min` | °C/°F | Max/min daily air temperature |
| `temperature_2m_mean` | °C/°F | Mean daily air temperature |
| `apparent_temperature_max`, `apparent_temperature_min` | °C/°F | Max/min daily apparent temperature |
| `precipitation_sum` | mm | Sum of daily precipitation |
| `rain_sum` | mm | Sum of daily rain |
| `showers_sum` | mm | Sum of daily showers |
| `snowfall_sum` | cm | Sum of daily snowfall |
| `precipitation_hours` | hours | Number of hours with rain |
| `sunrise`, `sunset` | iso8601 | Sun rise and set times |
| `sunshine_duration` | seconds | Number of seconds of sunshine |
| `daylight_duration` | seconds | Number of seconds of daylight |
| `wind_speed_10m_max`, `wind_gusts_10m_max` | km/h | Max wind speed and gusts |
| `wind_direction_10m_dominant` | ° | Dominant wind direction |
| `shortwave_radiation_sum` | MJ/m² | Sum of solar radiation |
| `et0_fao_evapotranspiration` | mm | Daily sum of reference evapotranspiration |
| `precipitation_probability_max` | % | Maximum probability of precipitation |
| `precipitation_probability_min` | % | Minimum probability of precipitation |
| `precipitation_probability_mean` | % | Mean probability of precipitation |
| `uv_index_max`, `uv_index_clear_sky_max` | Index | Max UV Index |

## Current Conditions

Returns real-time weather data for the current moment. Available variables include: `temperature_2m`, `relative_humidity_2m`, `apparent_temperature`, `precipitation`, `rain`, `showers`, `snowfall`, `weather_code`, `cloud_cover`, `pressure_msl`, `surface_pressure`, `wind_speed_10m`, `wind_direction_10m`, `wind_gusts_10m`, etc.

## Weather Models

Select via `&models=` parameter. Options include:
- Best match (default, seamless combo)
- ECMWF IFS, ECMWF IFS 0.25°
- GFS Seamless, GFS 0.25°, GFS 0.5°
- DWD ICON Seamless, DWD ICON EU, DWD ICON D2
- MeteoFrance ARPEGE, MeteoFrance ARPEGE Europe, AROME
- MET Norway
- JMA, JMA MSM, KMA
- GEM, GEM RDPS, GEM GDPS
- MeteoSwiss
- BOM ACCESS-G
- KNMI, KNMI Harmonie
- CMA

## API Parameters

| Parameter | Format | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `latitude`, `longitude` | Floating point | Yes | — | WGS84 coordinates. Multiple comma-separated supported |
| `hourly` | String array | No | — | Comma-separated list of hourly variables |
| `daily` | String array | No | — | Comma-separated list of daily variables |
| `current` | String array | No | — | Comma-separated list of current condition variables |
| `temperature_unit` | String | No | celsius | `celsius` or `fahrenheit` |
| `wind_speed_unit` | String | No | kmh | `kmh`, `ms`, `mph`, `kn` |
| `precipitation_unit` | String | No | mm | `mm` or `inch` |
| `timeformat` | String | No | iso8601 | `iso8601` or `unixtime` |
| `timezone` | String | No | GMT | IANA timezone or `auto` |
| `past_days` | Integer | No | 0 | Include past data (0-92) |
| `forecast_days` | Integer | No | 7 | Forecast days (0-16) |
| `forecast_hours` | Integer | No | — | Override hourly timesteps (from current hour) |
| `past_hours` | Integer | No | — | Past hourly timesteps |
| `start_date`, `end_date` | String (yyyy-mm-dd) | No | — | Specific date range |
| `start_hour`, `end_hour` | String (yyyy-mm-ddThh:mm) | No | — | Specific hour range |
| `models` | String array | No | best_match | Weather model selection |
| `cell_selection` | String | No | land | `land`, `sea`, `nearest` |
| `apikey` | String | No | — | For commercial use |

## JSON Response Structure

```json
{
    "latitude": 52.52,
    "longitude": 13.419,
    "elevation": 44.812,
    "generationtime_ms": 2.2119,
    "utc_offset_seconds": 0,
    "timezone": "Europe/Berlin",
    "timezone_abbreviation": "CEST",
    "current_units": { ... },
    "current": { ... },
    "hourly": {
        "time": ["2026-07-29T00:00", ...],
        "temperature_2m": [13, 12.7, ...]
    },
    "hourly_units": {
        "temperature_2m": "°C"
    },
    "daily": {
        "time": ["2026-07-29", ...],
        "temperature_2m_max": [23.1, ...]
    },
    "daily_units": {
        "temperature_2m_max": "°C"
    }
}
```

## WMO Weather Codes

| Code | Description |
|------|-------------|
| 0 | Clear sky |
| 1, 2, 3 | Mainly clear, partly cloudy, overcast |
| 45, 48 | Fog, depositing rime fog |
| 51, 53, 55 | Drizzle: light, moderate, dense |
| 56, 57 | Freezing Drizzle: light, dense |
| 61, 63, 65 | Rain: slight, moderate, heavy |
| 66, 67 | Freezing Rain: light, heavy |
| 71, 73, 75 | Snow fall: slight, moderate, heavy |
| 77 | Snow grains |
| 80, 81, 82 | Rain showers: slight, moderate, violent |
| 85, 86 | Snow showers: slight, heavy |
| 95* | Thunderstorm: slight or moderate |
| 96, 99* | Thunderstorm with slight and heavy hail |

(*) Thunderstorm with hail only available in Central Europe.

## Error Response

```json
{
    "error": true,
    "reason": "Cannot initialize WeatherVariable from invalid String value tempeture_2m for key hourly"
}
```
