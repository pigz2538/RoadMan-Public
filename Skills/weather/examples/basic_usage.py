"""Basic usage examples for Open-Meteo Skill."""

import asyncio

from open_meteo_skill import OpenMeteoClient


def example_current_weather():
    """Get current weather for a location."""
    client = OpenMeteoClient()

    # Get current weather for New York
    forecast = client.get_forecast(
        latitude=40.7128,
        longitude=-74.0060,
        current=[
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "weather_code",
            "wind_speed_10m",
            "wind_direction_10m",
        ],
    )

    current = forecast.current
    print(f"Current Weather in New York:")
    print(f"  Temperature: {current.temperature_2m}°C")
    print(f"  Feels like: {current.apparent_temperature}°C")
    print(f"  Humidity: {current.relative_humidity_2m}%")
    print(f"  Wind: {current.wind_speed_10m} km/h from {current.wind_direction_10m}°")
    print(f"  Weather Code: {current.weather_code}")

    client.close()


def example_daily_forecast():
    """Get 7-day weather forecast."""
    client = OpenMeteoClient()

    forecast = client.get_forecast(
        latitude=51.5074,
        longitude=-0.1278,
        daily=[
            "temperature_2m_max",
            "temperature_2m_min",
            "weather_code",
            "precipitation_sum",
            "wind_speed_10m_max",
        ],
        forecast_days=7,
    )

    print("\n7-Day Forecast for London:")
    for i, date in enumerate(forecast.daily.time):
        max_temp = forecast.daily.temperature_2m_max[i]
        min_temp = forecast.daily.temperature_2m_min[i]
        precip = forecast.daily.precipitation_sum[i]
        wind = forecast.daily.wind_speed_10m_max[i]
        print(f"  {date}: {min_temp}°C - {max_temp}°C, "
              f"Rain: {precip}mm, Wind: {wind}km/h")

    client.close()


def example_hourly_forecast():
    """Get hourly forecast for the next 24 hours."""
    client = OpenMeteoClient()

    forecast = client.get_forecast(
        latitude=48.8566,
        longitude=2.3522,
        hourly=[
            "temperature_2m",
            "precipitation_probability",
            "weather_code",
        ],
        forecast_days=1,
    )

    print("\n24-Hour Forecast for Paris:")
    for i in range(24):
        time = forecast.hourly.time[i]
        temp = forecast.hourly.temperature_2m[i]
        precip_prob = forecast.hourly.precipitation_probability[i]
        print(f"  {time}: {temp}°C, Rain: {precip_prob}%")

    client.close()


def example_geocoding():
    """Search for locations."""
    client = OpenMeteoClient()

    # Search for a city
    results = client.search_location(name="Sydney", count=5)

    print("\nSearch results for 'Sydney':")
    for place in results.results or []:
        print(f"  {place.name}, {place.country or 'Unknown'}")
        print(f"    Coordinates: ({place.latitude}, {place.longitude})")
        print(f"    Elevation: {place.elevation}m")
        print(f"    Population: {place.population}")
        print()

    client.close()


def example_historical_weather():
    """Get historical weather data."""
    client = OpenMeteoClient()

    # Get weather for a specific date range
    weather = client.get_historical_weather(
        latitude=52.52,
        longitude=13.41,
        start_date="2024-01-01",
        end_date="2024-01-07",
        daily=[
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
        ],
    )

    print("\nHistorical Weather for Berlin (Jan 1-7, 2024):")
    for i, date in enumerate(weather.daily.time):
        max_temp = weather.daily.temperature_2m_max[i]
        min_temp = weather.daily.temperature_2m_min[i]
        precip = weather.daily.precipitation_sum[i]
        print(f"  {date}: {min_temp}°C - {max_temp}°C, Rain: {precip}mm")

    client.close()


def example_air_quality():
    """Get air quality data."""
    client = OpenMeteoClient()

    air_quality = client.get_air_quality(
        latitude=39.9042,
        longitude=116.4074,
        current=["pm10", "pm2_5", "us_aqi", "european_aqi"],
        hourly=["pm10", "pm2_5", "ozone"],
        forecast_days=3,
    )

    print("\nAir Quality for Beijing:")
    print(f"  PM10: {air_quality.current.pm10}")
    print(f"  PM2.5: {air_quality.current.pm2_5}")
    print(f"  US AQI: {air_quality.current.us_aqi}")
    print(f"  European AQI: {air_quality.current.european_aqi}")

    client.close()


def example_elevation():
    """Get elevation for multiple locations."""
    client = OpenMeteoClient()

    elevations = client.get_elevation(
        latitudes=[27.9881, 29.9792, 48.8566],
        longitudes=[86.9250, 31.1342, 2.3522],
    )

    locations = ["Mount Everest", "Great Pyramid", "Paris"]
    print("\nElevations:")
    for location, elevation in zip(locations, elevations.elevation):
        print(f"  {location}: {elevation}m")

    client.close()


async def example_async():
    """Example of async usage."""
    client = OpenMeteoClient()

    # Make multiple async requests
    tasks = [
        client.aget_forecast(
            latitude=35.6762,
            longitude=139.6503,
            current=["temperature_2m"],
        ),
        client.aget_forecast(
            latitude=37.7749,
            longitude=-122.4194,
            current=["temperature_2m"],
        ),
        client.asearch_location(name="Berlin", count=1),
    ]

    results = await asyncio.gather(*tasks)

    print("\nAsync Results:")
    print(f"  Tokyo: {results[0].current.temperature_2m}°C")
    print(f"  San Francisco: {results[1].current.temperature_2m}°C")
    print(f"  Berlin: {results[2].results[0].name if results[2].results else 'N/A'}")

    await client.aclose()


if __name__ == "__main__":
    print("=" * 60)
    print("Open-Meteo Skill Usage Examples")
    print("=" * 60)

    example_current_weather()
    example_daily_forecast()
    example_hourly_forecast()
    example_geocoding()
    example_historical_weather()
    example_air_quality()
    example_elevation()

    # Run async example
    asyncio.run(example_async())

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)
