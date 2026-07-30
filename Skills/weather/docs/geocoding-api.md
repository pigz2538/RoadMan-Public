# Geocoding API

**Endpoint:** `https://geocoding-api.open-meteo.com/v1/search`

Search locations globally in any language. Convert city names to coordinates for use with weather APIs.

## API Parameters

| Parameter | Format | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `name` | String | Yes | — | Search term (location name or postal code). Empty or 1 char returns empty. 2 chars = exact match. 3+ chars = fuzzy matching |
| `count` | Integer | No | 10 | Number of search results to return (max 100) |
| `format` | String | No | json | `json` or `protobuf` |
| `language` | String | No | en | Return translated results if available. Lower-cased |
| `countryCode` | String | No | — | ISO-3166-1 alpha2 country code filter |
| `apikey` | String | No | — | For commercial use |

## JSON Response

```json
{
    "results": [
        {
            "id": 2950159,
            "name": "Berlin",
            "latitude": 52.52437,
            "longitude": 13.41053,
            "elevation": 74.0,
            "feature_code": "PPLC",
            "country_code": "DE",
            "admin1_id": 2950157,
            "admin2_id": 0,
            "admin3_id": 6547383,
            "admin4_id": 6547539,
            "timezone": "Europe/Berlin",
            "population": 3426354,
            "postcodes": ["10967", "13347"],
            "country_id": 2921044,
            "country": "Deutschland",
            "admin1": "Berlin",
            "admin2": "",
            "admin3": "Berlin, Stadt",
            "admin4": "Berlin"
        }
    ]
}
```

### Response Fields

| Parameter | Format | Description |
|-----------|--------|-------------|
| `id` | Integer | Unique ID for this location |
| `name` | String | Location name (localized per `&language=` if possible) |
| `latitude`, `longitude` | Floating point | WGS84 coordinates |
| `elevation` | Floating point | Elevation above mean sea level |
| `timezone` | String | Time zone (IANA time zone database) |
| `feature_code` | String | Type of location (GeoNames feature_code) |
| `country_code` | String | 2-Character ISO-3166-1 alpha2 country code |
| `country` | String | Country name (localized if possible) |
| `country_id` | Integer | Unique ID for this country |
| `population` | Integer | Number of inhabitants |
| `postcodes` | String[] | List of postcodes for this location |
| `admin1`, `admin2`, `admin3`, `admin4` | String | Hierarchical administrative area names |
| `admin1_id`–`admin4_id` | Integer | Unique IDs for administrative areas |

All IDs can be resolved via `https://geocoding-api.open-meteo.com/v1/get?id=2950159`.

## Usage Notes

- **3+ characters** trigger fuzzy matching — useful for misspellings or partial names
- Use **`countryCode`** to narrow results to a specific country (e.g. `CN`, `JP`, `US`)
- **`language`** parameter returns results in your preferred language (e.g. `zh` for Chinese)
- **Population** field can help select the most relevant city when multiple matches exist
- Returns **`timezone`** directly — critical for weather API calls

## Error Response

```json
{
    "error": true,
    "reason": "Parameter count must be between 1 and 100."
}
```

## Attribution

- Location data based on [GeoNames](https://www.geonames.org)
- Country flags from [HatScripts/circle-flags](https://github.com/HatScripts/circle-flags)
