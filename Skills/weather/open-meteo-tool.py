"""
title: Open-Meteo Weather Tool
author: anzejing
author_url: https://github.com/anzejing
git_url: https://github.com/anzejing/open-meteo-skill.git
description: A comprehensive weather tool powered by Open-Meteo API. Get current weather, forecasts, air quality, and location search.
required_open_webui_version: 0.4.0
requirements: httpx>=0.27.0
version: 0.1.0
licence: MIT
"""

import asyncio
from typing import Optional
from pydantic import BaseModel, Field


class OpenMeteoClient:
    """Simplified Open-Meteo client for OpenWebUI tool."""

    BASE_URL = "https://api.open-meteo.com"
    AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com"
    GEOCODING_URL = "https://geocoding-api.open-meteo.com"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def _request(self, url: str, params: dict) -> dict:
        """Make async HTTP request."""
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def get_forecast(
        self,
        latitude: float,
        longitude: float,
        current: Optional[list] = None,
        hourly: Optional[list] = None,
        daily: Optional[list] = None,
        forecast_days: int = 7,
        timezone: str = "auto",
    ) -> dict:
        """Get weather forecast."""
        url = f"{self.BASE_URL}/v1/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "forecast_days": forecast_days,
            "timezone": timezone,
        }
        if current:
            params["current"] = ",".join(current)
        if hourly:
            params["hourly"] = ",".join(hourly)
        if daily:
            params["daily"] = ",".join(daily)

        return await self._request(url, params)

    async def get_air_quality(
        self,
        latitude: float,
        longitude: float,
        current: Optional[list] = None,
        hourly: Optional[list] = None,
        forecast_days: int = 5,
    ) -> dict:
        """Get air quality data."""
        url = f"{self.AIR_QUALITY_URL}/v1/air-quality"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "forecast_days": forecast_days,
        }
        if current:
            params["current"] = ",".join(current)
        if hourly:
            params["hourly"] = ",".join(hourly)

        return await self._request(url, params)

    async def search_location(
        self,
        name: str,
        count: int = 10,
        language: str = "en",
        country_code: str = "",
    ) -> dict:
        """Search for locations."""
        url = f"{self.GEOCODING_URL}/v1/search"
        params = {
            "name": name,
            "count": min(count, 100),
            "language": language,
        }
        if country_code:
            params["countryCode"] = country_code

        return await self._request(url, params)


class Tools:
    """Open-Meteo Weather Tools for OpenWebUI."""

    def __init__(self):
        """Initialize the Tool."""
        self.valves = self.Valves()
        self.client = OpenMeteoClient(timeout=self.valves.timeout)

    class Valves(BaseModel):
        """Configuration valves for the tool."""

        timeout: float = Field(
            default=30.0,
            description="Request timeout in seconds",
        )

    async def get_current_weather(
        self,
        latitude: float,
        longitude: float,
        timezone: str = "auto",
    ) -> str:
        """
        Get current weather conditions for a specific location.

        :param latitude: Latitude of the location (-90 to 90)
        :param longitude: Longitude of the location (-180 to 180)
        :param timezone: Timezone for the data (default: auto)
        :return: Current weather information in markdown format
        """
        try:
            # Validate coordinates
            if not -90 <= latitude <= 90:
                return "❌ Error: Latitude must be between -90 and 90"
            if not -180 <= longitude <= 180:
                return "❌ Error: Longitude must be between -180 and 180"

            # Get weather data
            data = await self.client.get_forecast(
                latitude=latitude,
                longitude=longitude,
                current=[
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "weather_code",
                    "wind_speed_10m",
                    "wind_direction_10m",
                    "pressure_msl",
                    "cloud_cover",
                    "precipitation",
                    "is_day",
                ],
                timezone=timezone,
            )

            current = data.get("current", {})

            # Weather code interpretation
            weather_codes = {
                0: ("☀️", "Clear sky"),
                1: ("🌤️", "Mainly clear"),
                2: ("⛅", "Partly cloudy"),
                3: ("☁️", "Overcast"),
                45: ("🌫️", "Fog"),
                48: ("🌫️", "Depositing rime fog"),
                51: ("🌦️", "Light drizzle"),
                53: ("🌧️", "Moderate drizzle"),
                55: ("🌧️", "Dense drizzle"),
                61: ("🌧️", "Slight rain"),
                63: ("🌧️", "Moderate rain"),
                65: ("🌧️", "Heavy rain"),
                71: ("🌨️", "Slight snow"),
                73: ("🌨️", "Moderate snow"),
                75: ("🌨️", "Heavy snow"),
                95: ("⛈️", "Thunderstorm"),
            }

            weather_code = current.get("weather_code", 0)
            emoji, description = weather_codes.get(weather_code, ("🌡️", "Unknown"))
            is_day = current.get("is_day", 1)
            day_night = "🌅 Day" if is_day else "🌙 Night"

            # Format output
            result = f"""## {emoji} Current Weather

**Location:** {latitude}°N, {longitude}°E

| Metric | Value |
|--------|-------|
| **Temperature** | {current.get('temperature_2m', 'N/A')}°C |
| **Feels Like** | {current.get('apparent_temperature', 'N/A')}°C |
| **Condition** | {description} |
| **Time** | {day_night} |
| **Humidity** | {current.get('relative_humidity_2m', 'N/A')}% |
| **Wind** | {current.get('wind_speed_10m', 'N/A')} km/h from {current.get('wind_direction_10m', 'N/A')}° |
| **Cloud Cover** | {current.get('cloud_cover', 'N/A')}% |
| **Precipitation** | {current.get('precipitation', 'N/A')} mm |
| **Pressure** | {current.get('pressure_msl', 'N/A')} hPa |

*Data time: {current.get('time', 'N/A')}*
"""
            return result

        except Exception as e:
            return f"❌ Error fetching weather data: {str(e)}"

    async def get_weather_forecast(
        self,
        location: str,
        days: int = 7,
        country_code: str = "",
    ) -> str:
        """
        Get weather forecast for a city by name.

        :param location: City name (e.g., "Beijing", "Shanghai", "Tokyo", "Suzhou")
                       Note: For Chinese cities, use Pinyin (e.g., "Suzhou" instead of "苏州")
        :param days: Number of forecast days (1-16, default: 7)
        :param country_code: Optional ISO country code to filter results (e.g., "CN", "US")
        :return: Weather forecast in markdown format
        """
        try:
            # Validate days
            if not 1 <= days <= 16:
                return "❌ Error: Days must be between 1 and 16"

            # Search for location
            location_data = await self.client.search_location(
                name=location,
                count=5,
                country_code=country_code,
            )

            results = location_data.get("results", [])
            if not results:
                # Check if location contains Chinese characters
                import re
                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', location))

                if has_chinese:
                    return f"""❌ Error: Location '{location}' not found

💡 **Tip:** The geocoding API doesn't support Chinese characters directly.
Please try using **Pinyin** instead:
- "苏州" → "Suzhou"
- "北京" → "Beijing"
- "上海" → "Shanghai"
- "广州" → "Guangzhou"
- "深圳" → "Shenzhen"

Or use coordinates with `get_current_weather()` or `get_air_quality()`."""
                else:
                    return f"❌ Error: Location '{location}' not found. Please check the spelling or try using coordinates."

            # Use first result
            place = results[0]
            lat = place["latitude"]
            lon = place["longitude"]
            name = place["name"]
            country = place.get("country", "Unknown")
            admin1 = place.get("admin1", "")

            # Get forecast
            data = await self.client.get_forecast(
                latitude=lat,
                longitude=lon,
                daily=[
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "weather_code",
                    "precipitation_sum",
                    "sunrise",
                    "sunset",
                ],
                forecast_days=days,
                timezone="auto",
            )

            daily = data.get("daily", {})
            times = daily.get("time", [])
            max_temps = daily.get("temperature_2m_max", [])
            min_temps = daily.get("temperature_2m_min", [])
            weather_codes = daily.get("weather_code", [])
            precipitations = daily.get("precipitation_sum", [])
            sunrises = daily.get("sunrise", [])
            sunsets = daily.get("sunset", [])

            # Weather emoji mapping
            weather_emojis = {
                0: "☀️",
                1: "🌤️",
                2: "⛅",
                3: "☁️",
                45: "🌫️",
                48: "🌫️",
                51: "🌦️",
                53: "🌧️",
                55: "🌧️",
                61: "🌧️",
                63: "🌧️",
                65: "🌧️",
                71: "🌨️",
                73: "🌨️",
                75: "🌨️",
                95: "⛈️",
            }

            # Build forecast table
            location_str = f"{name}, {admin1}" if admin1 else f"{name}, {country}"
            result = f"## 🌤️ Weather Forecast for {location_str}\n\n"
            result += f"**Coordinates:** {lat}°N, {lon}°E\n\n"
            result += "| Date | Weather | Temp Range | Rain | Sunrise | Sunset |\n"
            result += "|------|---------|------------|------|---------|--------|\n"

            for i in range(len(times)):
                date = times[i]
                max_temp = max_temps[i] if i < len(max_temps) else "N/A"
                min_temp = min_temps[i] if i < len(min_temps) else "N/A"
                weather_code = weather_codes[i] if i < len(weather_codes) else 0
                precip = precipitations[i] if i < len(precipitations) else 0
                sunrise = sunrises[i].split("T")[1][:5] if i < len(sunrises) else "N/A"
                sunset = sunsets[i].split("T")[1][:5] if i < len(sunsets) else "N/A"

                emoji = weather_emojis.get(weather_code, "🌡️")
                result += f"| {date} | {emoji} | {min_temp}°C - {max_temp}°C | {precip}mm | {sunrise} | {sunset} |\n"

            return result

        except Exception as e:
            return f"❌ Error fetching forecast: {str(e)}"

    async def get_air_quality(
        self,
        latitude: float,
        longitude: float,
    ) -> str:
        """
        Get air quality information for a specific location.

        :param latitude: Latitude of the location (-90 to 90)
        :param longitude: Longitude of the location (-180 to 180)
        :return: Air quality information in markdown format
        """
        try:
            # Validate coordinates
            if not -90 <= latitude <= 90:
                return "❌ Error: Latitude must be between -90 and 90"
            if not -180 <= longitude <= 180:
                return "❌ Error: Longitude must be between -180 and 180"

            # Get air quality data
            data = await self.client.get_air_quality(
                latitude=latitude,
                longitude=longitude,
                current=[
                    "pm10",
                    "pm2_5",
                    "carbon_monoxide",
                    "nitrogen_dioxide",
                    "ozone",
                    "us_aqi",
                    "european_aqi",
                ],
            )

            current = data.get("current", {})

            # AQI interpretation
            us_aqi = current.get("us_aqi")
            eu_aqi = current.get("european_aqi")

            def get_us_aqi_level(aqi):
                if aqi is None:
                    return "Unknown"
                if aqi <= 50:
                    return "🟢 Good"
                elif aqi <= 100:
                    return "🟡 Moderate"
                elif aqi <= 150:
                    return "🟠 Unhealthy for Sensitive Groups"
                elif aqi <= 200:
                    return "🔴 Unhealthy"
                elif aqi <= 300:
                    return "🟣 Very Unhealthy"
                else:
                    return "🟤 Hazardous"

            def get_eu_aqi_level(aqi):
                if aqi is None:
                    return "Unknown"
                if aqi <= 20:
                    return "🟢 Good"
                elif aqi <= 40:
                    return "🟡 Fair"
                elif aqi <= 60:
                    return "🟠 Moderate"
                elif aqi <= 80:
                    return "🔴 Poor"
                elif aqi <= 100:
                    return "🟣 Very Poor"
                else:
                    return "🟤 Extremely Poor"

            result = f"""## 💨 Air Quality

**Location:** {latitude}°N, {longitude}°E

### AQI Index

| Index | Value | Level |
|-------|-------|-------|
| **US AQI** | {us_aqi if us_aqi is not None else 'N/A'} | {get_us_aqi_level(us_aqi)} |
| **European AQI** | {eu_aqi if eu_aqi is not None else 'N/A'} | {get_eu_aqi_level(eu_aqi)} |

### Pollutant Levels

| Pollutant | Value |
|-----------|-------|
| **PM10** | {current.get('pm10', 'N/A')} μg/m³ |
| **PM2.5** | {current.get('pm2_5', 'N/A')} μg/m³ |
| **Carbon Monoxide** | {current.get('carbon_monoxide', 'N/A')} μg/m³ |
| **Nitrogen Dioxide** | {current.get('nitrogen_dioxide', 'N/A')} μg/m³ |
| **Ozone** | {current.get('ozone', 'N/A')} μg/m³ |

*Data time: {current.get('time', 'N/A')}*
"""
            return result

        except Exception as e:
            return f"❌ Error fetching air quality data: {str(e)}"

    async def search_location(
        self,
        name: str,
        count: int = 5,
        country_code: str = "",
    ) -> str:
        """
        Search for geographical locations by name.

        :param name: Location name to search (e.g., "Beijing", "Paris", "New York", "Suzhou")
                    Note: For Chinese cities, use Pinyin (e.g., "Suzhou" instead of "苏州")
        :param count: Maximum number of results (1-100, default: 5)
        :param country_code: Optional ISO country code to filter results (e.g., "CN", "US", "JP")
        :return: Search results in markdown format
        """
        try:
            # Validate count
            count = min(max(1, count), 100)

            # Search for location
            data = await self.client.search_location(
                name=name,
                count=count,
                country_code=country_code,
            )

            results = data.get("results", [])
            if not results:
                # Check if name contains Chinese characters
                import re
                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', name))

                if has_chinese:
                    return f"""❌ No locations found for '{name}'

💡 **Tip:** The geocoding API doesn't support Chinese characters directly.
Please try using **Pinyin** instead:
- "苏州" → "Suzhou"
- "北京" → "Beijing"
- "上海" → "Shanghai"
- "杭州" → "Hangzhou"
- "南京" → "Nanjing"
- "成都" → "Chengdu"
- "西安" → "Xian"
- "武汉" → "Wuhan"

Or use coordinates directly with other tools."""
                else:
                    return f"❌ No locations found for '{name}'. Please check the spelling."

            result = f"## 🌍 Location Search Results for '{name}'\n\n"
            result += f"Found {len(results)} location(s):\n\n"

            for i, place in enumerate(results, 1):
                name_found = place.get("name", "Unknown")
                country = place.get("country", "Unknown")
                admin1 = place.get("admin1", "")
                admin2 = place.get("admin2", "")
                lat = place.get("latitude", "N/A")
                lon = place.get("longitude", "N/A")
                elevation = place.get("elevation", "N/A")
                population = place.get("population")
                timezone = place.get("timezone", "N/A")

                location_parts = [p for p in [admin2, admin1, country] if p]
                location_str = ", ".join(location_parts) if location_parts else country

                result += f"### {i}. {name_found}\n"
                result += f"- **Location:** {location_str}\n"
                result += f"- **Coordinates:** {lat}°N, {lon}°E\n"
                result += f"- **Elevation:** {elevation}m\n"
                if population:
                    result += f"- **Population:** {population:,}\n"
                result += f"- **Timezone:** {timezone}\n\n"

            return result

        except Exception as e:
            return f"❌ Error searching location: {str(e)}"

    async def get_elevation(
        self,
        latitudes: list[float],
        longitudes: list[float],
    ) -> str:
        """
        Get elevation data for one or more coordinates.

        :param latitudes: List of latitude values (-90 to 90)
        :param longitudes: List of longitude values (-180 to 180)
        :return: Elevation data in markdown format
        """
        try:
            # Validate inputs
            if len(latitudes) != len(longitudes):
                return "❌ Error: Latitudes and longitudes must have the same length"

            if len(latitudes) > 100:
                return "❌ Error: Maximum 100 coordinates allowed"

            if not latitudes:
                return "❌ Error: At least one coordinate pair required"

            # Validate ranges
            for i, lat in enumerate(latitudes):
                if not -90 <= lat <= 90:
                    return f"❌ Error: Latitude at index {i} ({lat}) is out of range (-90 to 90)"

            for i, lon in enumerate(longitudes):
                if not -180 <= lon <= 180:
                    return f"❌ Error: Longitude at index {i} ({lon}) is out of range (-180 to 180)"

            # Get elevation data
            url = f"{self.client.BASE_URL}/v1/elevation"
            params = {
                "latitude": ",".join(str(lat) for lat in latitudes),
                "longitude": ",".join(str(lon) for lon in longitudes),
            }

            data = await self.client._request(url, params)
            elevations = data.get("elevation", [])

            result = "## ⛰️ Elevation Data\n\n"
            result += "| # | Latitude | Longitude | Elevation |\n"
            result += "|---|----------|-----------|-----------|\n"

            for i in range(len(latitudes)):
                elev = elevations[i] if i < len(elevations) else "N/A"
                result += f"| {i+1} | {latitudes[i]} | {longitudes[i]} | {elev}m |\n"

            return result

        except Exception as e:
            return f"❌ Error fetching elevation data: {str(e)}"
