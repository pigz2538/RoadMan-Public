# Air Quality API

**Endpoint:** `https://air-quality-api.open-meteo.com/v1/air-quality`

Pollutants and pollen forecast in 11 km resolution. Based on CAMS European and Global atmospheric composition forecasts.

## Location and Time

- **Latitude/Longitude:** WGS84 coordinates
- **Timezone:** Auto-detect or specify
- **Forecast Days:** Default 5, max 7
- **Past Days:** Default 0, max 92
- **Domain:** `auto` (default, combines both), `cams_europe`, `cams_global`

## Hourly Air Quality Variables

| Variable | Valid time | Unit | Description |
|----------|-----------|------|-------------|
| `pm10`, `pm2_5` | Instant | μg/m³ | Particulate matter (PM10, PM2.5) near surface |
| `carbon_monoxide` | Instant | μg/m³ | CO near surface |
| `nitrogen_dioxide` | Instant | μg/m³ | NO₂ near surface |
| `sulphur_dioxide` | Instant | μg/m³ | SO₂ near surface |
| `ozone` | Instant | μg/m³ | O₃ near surface |
| `carbon_dioxide` | Instant | ppm | CO₂ near surface |
| `ammonia`* | Instant | μg/m³ | NH₃ (Europe only) |
| `aerosol_optical_depth` | Instant | — | Aerosol optical depth at 550 nm |
| `methane` | Instant | μg/m³ | CH₄ near surface |
| `dust` | Instant | μg/m³ | Saharan dust near surface |
| `uv_index` | Instant | Index | UV index considering clouds |
| `uv_index_clear_sky` | Instant | Index | UV index clear sky |
| `alder_pollen`* | Instant | Grains/m³ | Alder pollen (Europe, pollen season) |
| `birch_pollen`* | Instant | Grains/m³ | Birch pollen (Europe, pollen season) |
| `grass_pollen`* | Instant | Grains/m³ | Grass pollen (Europe, pollen season) |
| `mugwort_pollen`* | Instant | Grains/m³ | Mugwort pollen (Europe, pollen season) |
| `olive_pollen`* | Instant | Grains/m³ | Olive pollen (Europe, pollen season) |
| `ragweed_pollen`* | Instant | Grains/m³ | Ragweed pollen (Europe, pollen season) |

(*) Only available in Europe during pollen season with 4 days forecast.

## European Air Quality Index (AQI)

| Variable | Description |
|----------|-------------|
| `european_aqi` | Consolidated European AQI (max of all individual indices) |
| `european_aqi_pm2_5` | European AQI for PM2.5 |
| `european_aqi_pm10` | European AQI for PM10 |
| `european_aqi_nitrogen_dioxide` | European AQI for NO₂ |
| `european_aqi_ozone` | European AQI for O₃ |
| `european_aqi_sulphur_dioxide` | European AQI for SO₂ |

**European AQI ranges:**
- 0-20: Good
- 20-40: Fair
- 40-60: Moderate
- 60-80: Poor
- 80-100: Very Poor
- >100: Extremely Poor

## United States Air Quality Index (AQI)

| Variable | Description |
|----------|-------------|
| `us_aqi` | Consolidated US AQI (max of all individual indices) |
| `us_aqi_pm2_5` | US AQI for PM2.5 |
| `us_aqi_pm10` | US AQI for PM10 |
| `us_aqi_nitrogen_dioxide` | US AQI for NO₂ |
| `us_aqi_ozone` | US AQI for O₃ |
| `us_aqi_sulphur_dioxide` | US AQI for SO₂ |
| `us_aqi_carbon_monoxide` | US AQI for CO |

**U.S. AQI ranges:**
- 0-50: Good
- 51-100: Moderate
- 101-150: Unhealthy for Sensitive Groups
- 151-200: Unhealthy
- 201-300: Very Unhealthy
- 301-500: Hazardous

## Current Conditions

Available variables: `european_aqi`, `us_aqi`, `pm10`, `pm2_5`, `carbon_monoxide`, `nitrogen_dioxide`, `sulphur_dioxide`, `ozone`, `aerosol_optical_depth`, `dust`, `uv_index`, `uv_index_clear_sky`, `ammonia`, `alder_pollen`, `birch_pollen`, `grass_pollen`, `mugwort_pollen`, `olive_pollen`, `ragweed_pollen`

## API Parameters

| Parameter | Format | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `latitude`, `longitude` | Floating point | Yes | — | WGS84 coordinates. Multiple comma-separated |
| `hourly` | String array | No | — | Air quality variables |
| `current` | String array | No | — | Current conditions |
| `domains` | String | No | auto | `auto`, `cams_europe`, `cams_global` |
| `timeformat` | String | No | iso8601 | `iso8601` or `unixtime` |
| `timezone` | String | No | GMT | IANA timezone or `auto` |
| `past_days` | Integer | No | 0 | Include past data (0-92) |
| `forecast_days` | Integer | No | 5 | Forecast days (0-7) |
| `forecast_hours`, `past_hours` | Integer | No | — | Override hourly timesteps |
| `start_date`, `end_date` | String | No | — | Specific date range |
| `start_hour`, `end_hour` | String | No | — | Specific hour range |
| `cell_selection` | String | No | nearest | `land`, `sea`, `nearest` |
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
        "pm10": [1, 1.7, ...]
    },
    "hourly_units": {
        "pm10": "μg/m³"
    }
}
```

## Data Sources

| Dataset | Region | Resolution | Availability | Update |
|---------|--------|-----------|-------------|--------|
| CAMS European Air Quality Forecast | Europe | 0.1° (~11 km) | Oct 2023+ | Every 24h, 4 day forecast |
| CAMS European Air Quality Reanalysis | Europe | 0.1° (~11 km) | 2013+ | — |
| CAMS Global Atmospheric Composition | Global | 0.4° (~45 km) | Aug 2022+ | Every 12h, 5 day forecast |
| CAMS Global Greenhouse Gas Forecast | Global | 0.1° (~11 km) | Nov 2024+ | Every 24h, 5 day forecast |

## Error Response

```json
{
    "error": true,
    "reason": "Cannot initialize WeatherVariable from invalid String value tempeture_2m for key hourly"
}
```

## Citation

All users must provide attribution to CAMS ENSEMBLE data provider and Open-Meteo.
