# Seasonal Forecast API

**Endpoint:** `https://seasonal-api.open-meteo.com/v1/seasonal`

Sub-seasonal and long range forecast for up to 7 months. Based on ECMWF SEAS5 (7 months) and EC46 (46 days) at 36 km resolution with 51 ensemble members.

**Important:** Data is not bias-corrected. Interpret as area forecasts — indication of whether coming months are likely to be warmer/colder/wetter/drier than average.

## Models

Select via `&models=`:

| Model | Resolution | Period | Forecast Length | Update |
|-------|-----------|--------|----------------|--------|
| ECMWF Seasonal Seamless (EC46 + SEAS5) | 36 km | Combined | 7 months | — |
| ECMWF SEAS5 | 36 km | Month 2-7 | 7 months | Monthly on 5th |
| ECMWF EC46 | 36 km | First 46 days | 46 days | Daily at 20:30Z |
| Ensemble Mean variants | 36 km | — | — | — |

Selecting "Ensemble Mean" returns the mean of all 51 members instead of individual member data.

## 6-Hourly Weather Variables

| Variable | Unit | Description |
|----------|------|-------------|
| `temperature_2m` | °C/°F | Air temperature at 2 meters |
| `temperature_2m_max_6h`* | °C/°F | 6-hour maximum temperature |
| `temperature_2m_min_6h`* | °C/°F | 6-hour minimum temperature |
| `dew_point_2m` | °C/°F | Dew point temperature |
| `relative_humidity_2m` | % | Relative humidity |
| `apparent_temperature` | °C/°F | Perceived feels-like temperature |
| `et0_fao_evapotranspiration` | mm | Reference Evapotranspiration |
| `vapour_pressure_deficit` | kPa | Vapor Pressure Deficit |
| `pressure_msl` | hPa | Sea level pressure |
| `weather_code` | WMO code | Weather condition |
| `precipitation` | mm | Total precipitation |
| `showers`* | mm | Showers |
| `snowfall` | cm | Snowfall |
| `rain` | mm | Rain |
| `wave_height`* | m | Wave height |
| `wave_direction`* | ° | Wave direction |
| `wave_period`* | s | Wave period |
| `wave_peak_period`* | s | Wave peak period |
| `cloud_cover` | % | Total cloud cover |
| `sunshine_duration`* | s | Sunshine duration |
| `wind_speed_10m/100m/200m` | km/h | Wind speed at various heights |
| `wind_direction_10m/100m/200m` | ° | Wind direction |
| `wind_gusts_10m`* | km/h | Wind gusts |
| `sea_surface_temperature` | °C/°F | Sea surface temperature |
| `soil_temperature_0_to_7cm` | °C/°F | Soil temperature |
| `soil_moisture_0_to_7cm`* | m³/m³ | Soil moisture |

(*) Only available for EC46 (first 46 days).

## Solar Radiation Variables
- `shortwave_radiation`, `direct_radiation`, `diffuse_radiation`
- `direct_normal_irradiance`, `global_tilted_irradiance`
- `terrestrial_radiation`
- Instant versions available (e.g. `shortwave_radiation_instant`)

## Daily Weather Variables

Available with min/mean/max variants for: `temperature_2m`, `apparent_temperature`, `relative_humidity_2m`, `dew_point_2m`, `pressure_msl`, `surface_pressure`, `sea_surface_temperature`, `cloud_cover`, `wet_bulb_temperature_2m`, `vapour_pressure_deficit`, `wind_speed_10m/100m/200m`, `wind_gusts_10m`

Sum variants: `precipitation_sum`, `rain_sum`, `showers_sum`*, `snowfall_sum`, `snowfall_water_equivalent_sum`, `et0_fao_evapotranspiration`, `shortwave_radiation_sum`

Other: `wind_direction_10m/100m/200m_dominant`, `sunrise`, `sunset`, `weather_code`, `soil_temperature_mean` (various depths), `soil_moisture_mean` (various depths)

## Weekly Weather Variables

Weekly data from EC46 (6 weeks). Each variable has Mean and Anomaly variants.

| Variable | Description |
|----------|-------------|
| `temperature_2m` | Mean weekly temperature and anomaly |
| `temperature_2m_max_6h`, `temperature_2m_min_6h` | Mean and anomaly |
| `dew_point_2m` | Mean and anomaly |
| `soil_temperature_0_to_7cm` | Mean and anomaly |
| `precipitation` | Mean and anomaly |
| `snowfall` | Mean and anomaly |
| `snow_depth` | Mean and anomaly |
| `pressure_msl` | Mean and anomaly |
| `sea_surface_temperature` | Mean and anomaly |
| `sunshine_duration` | Mean and anomaly |
| `cloud_cover` | Mean and anomaly |
| `wind_speed_10m/100m` | Mean and anomaly |
| `wind_direction_10m/100m` | Mean and anomaly |

### Additional Weekly Variables
`snow_density`, `snow_depth_water_equivalent`, `snowfall_water_equivalent`, `total_column_integrated_water_vapour`

### Anomaly Probabilities, EFI & SOT
- `temperature_2m_extreme_forecast_index`
- `temperature_2m_shift_of_tails_10`, `temperature_2m_shift_of_tails_90`
- `temperature_2m_anomaly_greater_than_0k/1k/2k`
- `temperature_2m_anomaly_lower_than_-1k/-2k`
- `precipitation_extreme_forecast_index`, `precipitation_shift_of_tails_90`
- `precipitation_anomaly_greater_than_0mm/10mm/20mm`
- `pressure_msl_greater_than_0pa`
- `surface_temperature_anomaly_greater_than_0k`

## Monthly Weather Variables

Monthly data from SEAS5 (up to 7 months). Each variable has Mean and Anomaly variants.

Similar to weekly: `temperature_2m`, `temperature_2m_max_24h`, `temperature_2m_min_24h`, `dew_point_2m`, `precipitation`, `showers`, `snowfall`, `snow_depth`, `cloud_cover`, `cloud_cover_low`, `sunshine_duration`, `shortwave_radiation`, `pressure_msl`, `sea_surface_temperature`, `wind_speed_10m`, `wind_gusts_10m`, `soil_temperature` (various depths), `soil_moisture` (various depths).

### Additional Monthly Variables
`runoff`, `evapotranspiration`, `snow_density`, `snow_depth_water_equivalent`, `total_column_integrated_water_vapour`, `sea_ice_cover`, `longwave_radiation`, `snowfall_water_equivalent`, `albedo`, `latent_heat_flux`, `sensible_heat_flux`

## API Parameters

| Parameter | Format | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `latitude`, `longitude` | Floating point | Yes | — | WGS84 coordinates |
| `daily` | String array | No | — | Daily variables |
| `hourly` | String array | No | — | 6-hourly variables (interpolatable to 1h/3h) |
| `weekly` | String array | No | — | Weekly variables |
| `monthly` | String array | No | — | Monthly variables |
| `models` | String array | Yes | — | Model selection |
| `temperature_unit` | String | No | celsius | `celsius` or `fahrenheit` |
| `wind_speed_unit` | String | No | kmh | `kmh`, `ms`, `mph`, `kn` |
| `precipitation_unit` | String | No | mm | `mm` or `inch` |
| `timeformat` | String | No | iso8601 | `iso8601` or `unixtime` |
| `timezone` | String | No | GMT | IANA timezone or `auto` |
| `past_days` | Integer | No | 0 | Include past data |
| `forecast_days` | Integer | No | 183 | Forecast days (up to ~215 for 7 months) |
| `cell_selection` | String | No | land | `land`, `sea`, `nearest` |
| `apikey` | String | No | — | For commercial use |

## Key Concepts

- **Anomalies:** Forecast value minus model climatology (20-30+ year hindcast baseline)
- **Extreme Forecast Index (EFI):** How unusual a forecast is relative to model climate. +1 = much warmer/wetter, -1 = much colder/drier than normal
- **Shift of Tails (SOT):** How extreme an event could become — examines outer tails of forecast distribution for rare events
- **Bias correction:** Not yet applied to this dataset
- **6-hourly resolution:** Native resolution; can be interpolated to 3-hourly or 1-hourly but doesn't increase accuracy

## Attribution

Based on data and products of ECMWF. Source: www.ecmwf.int. ECMWF does not accept any liability for errors or omissions.
