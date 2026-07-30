# Marine Weather API

**Endpoint:** `https://marine-api.open-meteo.com/v1/marine`

Hourly wave forecasts at 5 km resolution. Combines wave models from multiple sources (MeteoFrance, DWD, ECMWF, NCEP GFS).

## Location and Time

- **Latitude/Longitude:** WGS84 coordinates
- **Timezone:** Auto-detect or specify
- **Forecast Days:** Default 7, max 8 (some models up to 16)
- **Past Days:** Default 0, max 92
- **Cell Selection:** Default `sea` (prefers sea grid-cells)

## Hourly Marine Variables

| Variable | Unit | Description |
|----------|------|-------------|
| `wave_height` | m | Significant mean wave height |
| `wave_direction` | ° | Mean wave direction (0° = from north) |
| `wave_period` | s | Mean wave period |
| `wave_peak_period` | s | Peak wave period |
| `wind_wave_height` | m | Wind wave height |
| `wind_wave_direction` | ° | Wind wave direction |
| `wind_wave_period` | s | Wind wave period |
| `wind_wave_peak_period` | s | Wind wave peak period |
| `swell_wave_height` | m | Swell wave height |
| `swell_wave_direction` | ° | Swell wave direction |
| `swell_wave_period` | s | Swell wave period |
| `swell_wave_peak_period` | s | Swell wave peak period |
| `secondary_swell_wave_height` | m | Secondary swell height |
| `secondary_swell_wave_period` | s | Secondary swell period |
| `secondary_swell_wave_direction` | ° | Secondary swell direction |
| `tertiary_swell_wave_height` | m | Tertiary swell height |
| `tertiary_swell_wave_period` | s | Tertiary swell period |
| `tertiary_swell_wave_direction` | ° | Tertiary swell direction |
| `sea_surface_temperature` | °C | Sea surface temperature |
| `ocean_current_velocity` | km/h | Ocean current velocity (Eulerian + Waves + Tides) |
| `ocean_current_direction` | ° | Ocean current direction (0° = going north) |
| `sea_level_height_msl` | m | Sea level height including tides (above global MSL) |
| `inverted_barometer_height` | m | Inverted barometer effect height |

**Important:** Tides and currents are at 0.08° (~8 km). Not suitable for coastal navigation.

## Daily Marine Variables

| Variable | Unit | Description |
|----------|------|-------------|
| `wave_height_max` | m | Maximum wave height on a given day |
| `wave_direction_dominant` | ° | Dominant wave direction |
| `wave_period_max` | s | Maximum wave period |
| `wind_wave_height_max` | m | Max wind wave height |
| `wind_wave_direction_dominant` | ° | Dominant wind wave direction |
| `wind_wave_period_max` | s | Max wind wave period |
| `wind_wave_peak_period_max` | s | Max wind wave peak period |
| `swell_wave_height_max` | m | Max swell wave height |
| `swell_wave_direction_dominant` | ° | Dominant swell wave direction |
| `swell_wave_period_max` | s | Max swell wave period |
| `swell_wave_peak_period_max` | s | Max swell wave peak period |

## 15-Minutely Variables

- `ocean_current_velocity`, `ocean_current_direction`
- `sea_level_height_msl`

(Only available in Central Europe and North America.)

## Current Conditions

Same hourly variables available as current conditions, based on 15-minutely model data.

## Weather Models

Select via `&models=`. Default: Best Match (seamless combo for any location).

| Model | Region | Resolution | Forecast Length | Update |
|-------|--------|-----------|----------------|--------|
| MeteoFrance MFWAM | Global | 0.08° (~8 km) | 10 days | Every 12h |
| MeteoFrance SMOC (Currents, Tides) | Global | 0.08° (~8 km) | 10 days | Every 24h |
| MeteoFrance SST | Global | 0.08° (~8 km) | 10 days | Every 24h |
| ECMWF WAM | Global | 9 km | 15 days | Every 6h |
| ECMWF WAM 0.25° | Global | 0.25° (~25 km) | 15 days | Every 6h |
| NCEP GFS Wave 0.25° | Global | 0.25° (~25 km) | 16 days | Every 6h |
| NCEP GFS Wave 0.16° | Limited | 0.16° (~16 km) | 16 days | Every 6h |
| DWD EWAM | Europe | 0.05° (~5 km) | 8 days | Every 12h |
| DWD GWAM | Global | 0.25° (~25 km) | 4 days | Every 12h |
| ERA5-Ocean | Global | 0.5° (~50 km) | 1940-present | Daily, 5 day delay |

## API Parameters

| Parameter | Format | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `latitude`, `longitude` | Floating point | Yes | — | WGS84 coordinates. Multiple comma-separated |
| `hourly` | String array | No | — | Marine variables |
| `daily` | String array | No | — | Daily aggregated variables |
| `current` | String array | No | — | Current conditions |
| `timeformat` | String | No | iso8601 | `iso8601` or `unixtime` |
| `timezone` | String | No | GMT | IANA timezone or `auto` |
| `past_days` | Integer | No | 0 | Include past data (0-92) |
| `forecast_days` | Integer | No | 7 | Forecast days (0-8) |
| `forecast_hours`, `past_hours` | Integer | No | — | Override timesteps |
| `start_date`, `end_date` | String | No | — | Specific date range |
| `start_hour`, `end_hour` | String | No | — | Specific hour range |
| `length_unit` | String | No | metric | `metric` or `imperial` |
| `cell_selection` | String | No | sea | `land`, `sea`, `nearest` |
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
        "wave_height": [1, 1.7, ...]
    },
    "hourly_units": {
        "wave_height": "m"
    }
}
```

## Error Response

```json
{
    "error": true,
    "reason": "Cannot initialize WeatherVariable from invalid String value tempeture_2m for key hourly"
}
```

## Citation

Generated using ICON Wave forecast from DWD. Attribution to DWD and Open-Meteo required.
