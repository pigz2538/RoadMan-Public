# Ensemble API

**Endpoint:** `https://ensemble-api.open-meteo.com/v1/ensemble`

Perturbed weather forecasts from hundreds of members. Multiple ensemble models with probabilistic outcomes — provides a range of possible outcomes and their likelihoods.

## Location and Time

- **Latitude/Longitude:** WGS84 coordinates
- **Timezone:** Auto-detect or specify
- **Forecast Days:** Default 7, up to 35 (varies by model)
- **Past Days:** Default 0

## Ensemble Models

Select via `&models=` (required parameter):

| Model | Region | Resolution | Members | Forecast Length | Update |
|-------|--------|-----------|---------|----------------|--------|
| ICON-D2-EPS (DWD) | Central Europe | 2 km | 20 | 2 days | Every 3h |
| ICON-EU-EPS (DWD) | Europe | 13 km | 40 | 5 days | Every 6h |
| ICON-EPS (DWD) | Global | 26 km | 40 | 7.5 days | Every 12h |
| GFS Ensemble 0.25° (NOAA) | Global | 0.25° (~25 km) | 31 | 10 days | Every 6h |
| GFS Ensemble 0.5° (NOAA) | Global | 50 km | 31 | 35 days | Every 6h |
| AIGFS 0.25° | Global | 0.25° (~25 km) | 31 | 16 days | Every 6h |
| ECMWF IFS 0.25° Ensemble | Global | 0.25° (~25 km) | 51 | 15 days | Every 6h |
| ECMWF AIFS 0.25° Ensemble | Global | 0.25° (~25 km) | 51 | 15 days | Every 6h |
| ECMWF IFS Europe (native O1280) | Europe | 9 km | 51 | 15 days | 0z and 6z only |
| ECMWF AIFS Europe (native N320) | Europe | 31 km | 51 | 15 days | Every 6h |
| GEM Ensemble (Canada) | Global | 0.25° (~25 km) | 21 | 16 days | Every 12h |
| ACCESS-GE (BOM Australia) | Global | 40 km | 18 | 10 days | Every 6h |
| MOGREPS-UK (UK Met Office) | UK | 2 km | 3 | 5 days | Every hour |
| MOGREPS-G (UK Met Office) | Global | 20 km | 18 | 8 days | Every 6h |
| ICON CH1/CH2 (MeteoSwiss) | Central Europe | 1-2 km | 11-21 | 12-33h | Every 3-6h |
| WeatherNext 2 (Google) | Global | 0.25° (~25 km) | 64 | 15 days | Every 12h |

## Hourly Weather Variables

| Variable | Valid time | Unit | Description |
|----------|-----------|------|-------------|
| `temperature_2m` | Instant | °C/°F | Air temperature at 2 meters above ground |
| `relative_humidity_2m` | Instant | % | Relative humidity at 2 meters |
| `dew_point_2m` | Instant | °C/°F | Dew point temperature |
| `apparent_temperature` | Instant | °C/°F | Feels-like temperature |
| `precipitation` | Preceding hour sum | mm/inch | Total precipitation (rain + snow) |
| `rain` | Preceding hour sum | mm/inch | Liquid precipitation |
| `snowfall` | Preceding hour sum | cm/inch | Snowfall amount |
| `weather_code` | Instant | WMO code | Weather condition code |
| `pressure_msl` | Instant | hPa | Sea level pressure |
| `surface_pressure` | Instant | hPa | Surface pressure |
| `cloud_cover` | Instant | % | Total cloud cover |
| `cloud_cover_low/mid/high` | Instant | % | Cloud cover by altitude |
| `visibility` | Instant | meters | Viewing distance |
| `wind_speed_10m/80m/100m/120m` | Instant | km/h | Wind speed at various heights |
| `wind_direction_10m/80m/100m/120m` | Instant | ° | Wind direction |
| `wind_gusts_10m` | Preceding hour max | km/h | Gusts |
| `temperature_80m/120m` | Instant | °C/°F | Temperature aloft |
| `surface_temperature` | Instant | °C/°F | Top soil level temperature |
| `soil_temperature_*` | Instant | °C/°F | Various soil depths |
| `soil_moisture_*` | Instant | m³/m³ | Various soil depths |
| `snow_depth` | Instant | meters | Snow depth |
| `freezing_level_height` | Instant | meters | Altitude of 0°C level |
| `cape` | Instant | J/kg | Convective Available Potential Energy |
| `et0_fao_evapotranspiration` | Preceding hour sum | mm | Reference evapotranspiration |
| `vapour_pressure_deficit` | Instant | kPa | Vapor Pressure Deficit |
| `shortwave_radiation` | Preceding hour mean | W/m² | Shortwave solar radiation |
| `direct_radiation`, `diffuse_radiation` | Preceding hour mean | W/m² | Solar radiation components |
| `sunshine_duration` | Preceding hour sum | seconds | Sunshine duration |
| `uv_index`, `uv_index_clear_sky` | Instant | Index | UV Index |
| `is_day` | Instant | 0/1 | Day or night |
| `wet_bulb_temperature_2m` | Instant | °C/°F | Wet bulb temperature |
| `visibility` | Instant | m | Viewing distance |

### Pressure Level Variables
Temperature, relative humidity, dew point, cloud cover, wind speed, wind direction, vertical velocity, geopotential height at various hPa levels (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50).

## Daily Weather Variables

Mean/min/max variants available for: `temperature_2m`, `apparent_temperature`, `wind_speed_10m`, `wind_gusts_10m`, `wind_speed_100m`, `cloud_cover`, `pressure_msl`, `surface_pressure`, `relative_humidity_2m`, `cape`, `dew_point_2m`

Sum variants: `precipitation_sum`, `precipitation_hours`, `rain_sum`, `snowfall_sum`, `shortwave_radiation_sum`, `et0_fao_evapotranspiration`

Other: `wind_direction_10m_dominant`, `wind_direction_100m_dominant`

## API Parameters

| Parameter | Format | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `latitude`, `longitude` | Floating point | Yes | — | WGS84 coordinates. Multiple comma-separated |
| `models` | String array | **Yes** | — | One or more ensemble model names |
| `elevation` | Floating point | No | — | Custom elevation for downscaling |
| `hourly` | String array | No | — | Hourly variables |
| `daily` | String array | No | — | Daily variables |
| `temperature_unit` | String | No | celsius | `celsius` or `fahrenheit` |
| `wind_speed_unit` | String | No | kmh | `kmh`, `ms`, `mph`, `kn` |
| `precipitation_unit` | String | No | mm | `mm` or `inch` |
| `timeformat` | String | No | iso8601 | `iso8601` or `unixtime` |
| `timezone` | String | No | GMT | IANA timezone or `auto` |
| `past_days` | Integer | No | 0 | Include past data |
| `forecast_days` | Integer | No | 7 | Forecast days (0-35, depends on model) |
| `forecast_hours`, `past_hours` | Integer | No | — | Override timesteps |
| `start_date`, `end_date` | String | No | — | Specific date range |
| `start_hour`, `end_hour` | String | No | — | Specific hour range |
| `cell_selection` | String | No | land | `land`, `sea`, `nearest` |
| `apikey` | String | No | — | For commercial use |

## JSON Response

```json
{
    "latitude": 52.52,
    "longitude": 13.419,
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
    }
}
```

## WMO Weather Codes

Same as Weather Forecast API. See `weather-forecast-api.md`.

## Key Concepts

- **Ensemble models** use multiple members with slightly different initial conditions to account for atmospheric uncertainties
- **Probabilistic approach** provides not just the most likely outcome but the range of possible outcomes
- All data interpolated to 1-hourly timesteps (native resolution may be lower for longer horizons)
- Use GFS 0.5° for up to 35-day forecasts, ICON/DWD for high-resolution European forecasts

## Error Response

```json
{
    "error": true,
    "reason": "Cannot initialize WeatherVariable from invalid String value tempeture_2m for key hourly"
}
```
