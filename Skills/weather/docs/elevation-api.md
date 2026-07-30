# Elevation API

**Endpoint:** `https://api.open-meteo.com/v1/elevation`

90 meter resolution digital elevation model. Based on Copernicus DEM 2021 release GLO-90 (worldwide, free license).

## API Parameters

| Parameter | Format | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `latitude` | Floating point array | Yes | — | WGS84 latitude. Multiple comma-separated (up to 100 coordinates) |
| `longitude` | Floating point array | Yes | — | WGS84 longitude. Multiple comma-separated |
| `apikey` | String | No | — | For commercial use |

## JSON Response

```json
{
    "elevation": [38.0]
}
```

Always returns an array, even for a single coordinate.

## Error Response

```json
{
    "error": true,
    "reason": "Latitude must be in range of -90 to 90°. Given: 522.52."
}
```

## Example

Single location: `https://api.open-meteo.com/v1/elevation?latitude=52.52&longitude=13.41`

Multiple locations: `https://api.open-meteo.com/v1/elevation?latitude=52.52,48.85&longitude=13.41,2.35`

## Citation

ESA Copernicus DEM: https://doi.org/10.5270/ESA-c5d3d65

Attribution to Copernicus program and Open-Meteo required.
