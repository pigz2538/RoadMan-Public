# Historical Weather API

**Endpoint:** `https://archive-api.open-meteo.com/v1/archive`

Discover how weather has shaped our world from 1940 until now. Gap-free and consistent historical weather data using weather reanalysis from ERA5 (0.25°, from 1940), ERA5-Land (0.1°, from 1950), and ECMWF IFS (9 km, from 2017).

## Location and Time

- **Latitude/Longitude:** WGS84 coordinates
- **Timezone:** Auto-detect or specify
- **Start Date / End Date:** Required (yyyy-mm-dd format)
- Data available from 1940 onwards

## Hourly Weather Variables

| Variable | Valid time | Unit | Description |
|----------|-----------|------|-------------|
| `temperature_2m` | Instant | °C/°F | Air temperature at 2 meters above ground |
| `relative_humidity_2m` | Instant | % | Relative humidity at 2 meters above ground |
| `dew_point_2m` | Instant | °C/°F | Dew point temperature at 2 meters above ground |
| `apparent_temperature` | Instant | °C/°F | Perceived feels-like temperature |
| `precipitation` | Preceding hour sum | mm/inch | Total precipitation (rain, showers, snow) sum of preceding hour |
| `rain` | Preceding hour sum | mm/inch | Only liquid precipitation of the preceding hour |
| `snowfall` | Preceding hour sum | cm/inch | Snowfall amount of the preceding hour in centimeters |
| `weather_code` | Instant | WMO code | Weather condition as numeric code |
| `pressure_msl` | Instant | hPa | Atmospheric air pressure reduced to mean sea level |
| `surface_pressure` | Instant | hPa | Surface pressure |
| `cloud_cover` | Instant | % | Total cloud cover as area fraction |
| `cloud_cover_low` | Instant | % | Low level clouds up to 2 km |
| `cloud_cover_mid` | Instant | % | Mid level clouds 2 to 6 km |
| `cloud_cover_high` | Instant | % | High level clouds from 6 km |
| `shortwave_radiation` | Preceding hour mean | W/m² | Shortwave solar radiation average |
| `direct_radiation` | Preceding hour mean | W/m² | Direct solar radiation |
| `diffuse_radiation` | Preceding hour mean | W/m² | Diffuse solar radiation |
| `direct_normal_irradiance` | Preceding hour mean | W/m² | Direct Normal Irradiance (DNI) |
| `global_tilted_irradiance` | Preceding hour mean | W/m² | Total radiation on tilted pane |
| `sunshine_duration` | Preceding hour sum | seconds | Seconds of sunshine per hour |
| `wind_speed_10m` | Instant | km/h | Wind speed at 10 meters above ground |
| `wind_speed_100m` | Instant | km/h | Wind speed at 100 meters above ground |
| `wind_direction_10m` | Instant | ° | Wind direction at 10 meters |
| `wind_direction_100m` | Instant | ° | Wind direction at 100 meters |
| `wind_gusts_10m` | Instant | km/h | Gusts at 10 meters |
| `et0_fao_evapotranspiration` | Preceding hour sum | mm/inch | Reference Evapotranspiration (FAO-56) |
| `vapour_pressure_deficit` | Instant | kPa | Vapor Pressure Deficit |
| `snow_depth` | Instant | meters | Snow depth on the ground |
| `soil_temperature_0_to_7cm` | Instant | °C/°F | Average temperature at 0-7 cm depth |
| `soil_temperature_7_to_28cm` | Instant | °C/°F | Average temperature at 7-28 cm depth |
| `soil_temperature_28_to_100cm` | Instant | °C/°F | Average temperature at 28-100 cm depth |
| `soil_temperature_100_to_255cm` | Instant | °C/°F | Average temperature at 100-255 cm depth |
| `soil_moisture_0_to_7cm` | Instant | m³/m³ | Soil water content at 0-7 cm |
| `soil_moisture_7_to_28cm` | Instant | m³/m³ | Soil water content at 7-28 cm |
| `soil_moisture_28_to_100cm` | Instant | m³/m³ | Soil water content at 28-100 cm |
| `soil_moisture_100_to_255cm` | Instant | m³/m³ | Soil water content at 100-255 cm |

### Additional Options
- `wet_bulb_temperature_2m` — °C/°F
- `boundary_layer_height_pbl` — meters
- `total_column_integrated_water_vapour` — kg/m²
- `is_day` — 1 if day, 0 if night
- `sunshine_duration` — seconds
- `albedo` (only CERRA) — %
- `snow_depth_water_equivalent` (only CERRA) — kg/m²

## Solar Radiation Variables
Instant versions available (suffix `_instant`): `shortwave_radiation_instant`, `direct_radiation_instant`, `diffuse_radiation_instant`, `direct_normal_irradiance_instant`, `global_tilted_irradiance_instant`, `terrestrial_radiation`, `terrestrial_radiation_instant`

## Daily Weather Variables

| Variable | Unit | Description |
|----------|------|-------------|
| `weather_code` | WMO code | Most severe weather condition on given day |
| `temperature_2m_max`, `temperature_2m_min` | °C/°F | Maximum and minimum daily air temperature |
| `temperature_2m_mean` | °C/°F | Mean daily air temperature |
| `apparent_temperature_max`, `apparent_temperature_min` | °C/°F | Maximum and minimum daily apparent temperature |
| `apparent_temperature_mean` | °C/°F | Mean daily apparent temperature |
| `sunrise`, `sunset` | iso8601 | Sun rise and set times |
| `daylight_duration` | seconds | Number of seconds of daylight per day |
| `sunshine_duration` | seconds | Number of seconds of sunshine per day |
| `precipitation_sum` | mm | Sum of daily precipitation |
| `rain_sum` | mm | Sum of daily rain |
| `snowfall_sum` | cm | Sum of daily snowfall |
| `precipitation_hours` | hours | The number of hours with rain |
| `wind_speed_10m_max` | km/h | Maximum wind speed on a day |
| `wind_gusts_10m_max` | km/h | Maximum wind gusts on a day |
| `wind_direction_10m_dominant` | ° | Dominant wind direction |
| `shortwave_radiation_sum` | MJ/m² | The sum of solar radiation on a given day |
| `et0_fao_evapotranspiration` | mm | Daily sum of reference evapotranspiration |

### Additional Daily Variables
- `temperature_2m_mean`, `apparent_temperature_mean`, `cloud_cover_mean/max/min`, `dew_point_2m_mean/max/min`
- `precipitation_sum`, `snowfall_sum`, `rain_sum`, `snowfall_water_equivalent_sum`
- `pressure_msl_mean/max/min`, `surface_pressure_mean/max/min`
- `wind_speed_10m_mean/min`, `wind_gusts_10m_mean/min`
- `wind_direction_10m_dominant`, `wind_direction_100m_dominant`
- `wet_bulb_temperature_2m_mean/max/min`
- `vapour_pressure_deficit_max`
- `soil_moisture_mean` (various depths), `soil_temperature_mean` (various depths)
- `et0_fao_evapotranspiration` sum
- `relative_humidity_2m_mean/max/min`

## Reanalysis Models

Select via `&models=`:

| Model | Resolution | Period | Update |
|-------|-----------|--------|--------|
| ECMWF IFS | 9 km | 2017 to present | Every 6 hours |
| ERA5-Seamless | Merged | Best combo | — |
| ERA5 | 0.25° (~25 km) | 1940 to present | Daily, 5 day delay |
| ERA5-Land | 0.1° (~11 km) | 1950 to present | Daily, 5 day delay |
| ERA5-Ensemble | 0.5° (~55 km) | 1940 to present | Daily, 5 day delay |
| CERRA | 5 km | 1985 to June 2021 | — |
| ECMWF IFS Assimilation | 9 km | 2024 to present | Daily, 2 day delay |

### ERA5-Ensemble Spread Variables
Additional ensemble spread variables when using `era5_ensemble` model: `temperature_2m`, `dew_point_2m`, `precipitation`, `snowfall`, `shortwave_radiation`, `direct_radiation`, `pressure_msl`, `cloud_cover_low/mid/high`, `wind_speed_10m/100m`, `wind_direction_10m/100m`, `wind_gusts_10m`, soil temperature and moisture at various depths.

## API Parameters

| Parameter | Format | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `latitude`, `longitude` | Floating point | Yes | — | WGS84 coordinates. Multiple comma-separated supported |
| `start_date`, `end_date` | String (yyyy-mm-dd) | Yes | — | Date range for data retrieval |
| `hourly` | String array | No | — | Comma-separated list of hourly variables |
| `daily` | String array | No | — | Comma-separated list of daily variables |
| `temperature_unit` | String | No | celsius | `celsius` or `fahrenheit` |
| `wind_speed_unit` | String | No | kmh | `kmh`, `ms`, `mph`, `kn` |
| `precipitation_unit` | String | No | mm | `mm` or `inch` |
| `timeformat` | String | No | iso8601 | `iso8601` or `unixtime` |
| `timezone` | String | No | GMT | IANA timezone or `auto` |
| `cell_selection` | String | No | land | `land`, `sea`, `nearest` |
| `apikey` | String | No | — | For commercial use |

## JSON Response

```json
{
    "latitude": 52.52,
    "longitude": 13.419,
    "elevation": 44.812,
    "generationtime_ms": 2.2119,
    "utc_offset_seconds": 0,
    "timezone": "Europe/Berlin",
    "timezone_abbreviation": "CEST",
    "hourly": {
        "time": ["2022-07-01T00:00", ...],
        "temperature_2m": [13, 12.7, ...]
    },
    "hourly_units": {
        "temperature_2m": "°C"
    },
    "daily": {
        "time": ["2022-07-01", ...],
        "temperature_2m_max": [23.1, ...]
    },
    "daily_units": {
        "temperature_2m_max": "°C"
    }
}
```

## WMO Weather Codes

Same as Weather Forecast API. See `weather-forecast-api.md`.

## Error Response

```json
{
    "error": true,
    "reason": "Cannot initialize WeatherVariable from invalid String value tempeture_2m for key hourly"
}
```

## Citation

Zippenfenig, P. (2023). Open-Meteo.com Weather API [Computer software]. Zenodo. https://doi.org/10.5281/ZENODO.7970649
