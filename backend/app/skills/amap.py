from __future__ import annotations

import time
from math import asin, cos, radians, sin, sqrt
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from ..domain.models import SourceRecord, SkillResult
from .base import SkillAdapter, SkillContext

AMAP_BASE_URL = "https://restapi.amap.com"


class GeocodeInput(BaseModel):
    address: str = Field(min_length=1)
    city: str | None = None


class DrivingInput(BaseModel):
    origin: str = Field(pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$")
    destination: str = Field(pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$")
    strategy: int = Field(default=0, ge=0, le=20)


class RoutePoint(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    city: str | None = None

    @property
    def location(self) -> str:
        return f"{self.longitude},{self.latitude}"


class UnifiedRouteInput(BaseModel):
    origin: RoutePoint
    destination: RoutePoint
    preferred_mode: str = Field(default="driving", pattern="^(driving|riding|walking|transit)$")
    allowed_fallback_modes: list[Literal["driving", "riding", "walking", "transit"]] = Field(
        default_factory=lambda: ["riding", "walking", "transit"],
    )
    waypoints: list[RoutePoint] = Field(default_factory=list)
    strategy: int = Field(default=0, ge=0, le=20)


class PoiInput(BaseModel):
    keywords: str = Field(min_length=1)
    city: str | None = None
    types: str | None = None
    location: str | None = Field(
        default=None,
        pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$",
    )
    radius: int = Field(default=5000, ge=0, le=50000)
    page: int = Field(default=1, ge=1, le=100)
    page_size: int = Field(default=20, ge=1, le=25)


class PoiDetailInput(BaseModel):
    poi_id: str = Field(min_length=1, max_length=80)


def _fact_text(value: Any) -> str | None:
    """Normalize the loosely typed business fields returned by AMap."""
    if isinstance(value, dict):
        for key in ("name", "text", "value", "content"):
            if value.get(key) not in (None, ""):
                return _fact_text(value[key])
        return None
    return _text_value(value)


def _first_fact(item: dict[str, Any], *keys: str) -> Any:
    containers = [item, item.get("business") or {}, item.get("biz_ext") or {}]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _poi_facts(item: dict[str, Any]) -> dict[str, Any]:
    price = _first_fact(item, "cost", "price", "ticket_price", "ticketPrice")
    photos = _first_fact(item, "photos", "photo")
    if isinstance(photos, dict):
        photos = [photos]
    if not isinstance(photos, list):
        photos = []
    photo_urls = []
    for photo in photos:
        if isinstance(photo, dict):
            url = photo.get("url") or photo.get("src") or photo.get("image")
        else:
            url = photo
        if url:
            photo_urls.append(str(url))
    return {
        "opening_hours_text": _fact_text(
            _first_fact(item, "opentime", "open_time", "opening_hours", "openTime", "business_time")
        ),
        "price_text": _fact_text(price),
        "parking_text": _fact_text(
            _first_fact(item, "parking_info", "parking", "parking_type", "parkingInfo")
        ),
        "ticket_ordering": _fact_text(_first_fact(item, "ticket_ordering", "ticketOrdering")),
        "hotel_ordering": _fact_text(_first_fact(item, "hotel_ordering", "hotelOrdering")),
        "website": _fact_text(_first_fact(item, "website", "weburl", "url")),
        "tel": _fact_text(_first_fact(item, "tel", "telephone")),
        "rating": _fact_text(_first_fact(item, "rating", "star")),
        "photos": photo_urls,
    }


class AmapGeocodeAdapter(SkillAdapter):
    name = "amap.geocode"
    category = "geocoding"
    cache_ttl_seconds = 30 * 24 * 3600

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return GeocodeInput.model_validate(payload).model_dump(exclude_none=True)

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        if not self.api_key:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=["未配置 AMAP_WEBSERVICE_KEY"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{AMAP_BASE_URL}/v3/geocode/geo",
                params={**payload, "key": self.api_key},
            )
            response.raise_for_status()
            body = response.json()
        if body.get("status") != "1" or not body.get("geocodes"):
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=[body.get("info", "未找到地址")],
                error_code="AMAP_NO_RESULT",
            )
        item = body["geocodes"][0]
        return SkillResult(
            success=True,
            provider="高德地图",
            data={
                "formatted_address": item["formatted_address"],
                "location": item["location"],
                "province": item.get("province"),
                "city": item.get("city"),
                "district": item.get("district"),
                # Keep the administrative granularity returned by AMap.  The
                # planner uses this to distinguish a city-level destination
                # (for example, "北京") from a short scenic/POI name.  Without
                # it, the nearby-POI ambiguity fallback can replace a city
                # with an unrelated local restaurant whose name happens to
                # contain the same word.
                "township": item.get("township"),
                "street": item.get("street"),
                "number": item.get("number"),
                "level": item.get("level"),
                "adcode": item.get("adcode"),
            },
            sources=[SourceRecord(provider="高德地图", title="地理编码 API", url=f"{AMAP_BASE_URL}/v3/geocode/geo")],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready" if self.api_key else "degraded", "configured": bool(self.api_key)}


class AmapDrivingAdapter(SkillAdapter):
    name = "amap.driving"
    category = "routing"
    cache_ttl_seconds = 1800

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return DrivingInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        if not self.api_key:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=["未配置 AMAP_WEBSERVICE_KEY"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{AMAP_BASE_URL}/v3/direction/driving",
                params={**payload, "extensions": "all", "key": self.api_key},
            )
            response.raise_for_status()
            body = response.json()
        paths = body.get("route", {}).get("paths", [])
        if body.get("status") != "1" or not paths:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=[body.get("info", "未找到驾车路线")],
                error_code="AMAP_NO_RESULT",
            )
        path = paths[0]
        steps = path.get("steps", [])
        return SkillResult(
            success=True,
            provider="高德地图",
            data={
                "origin": body["route"]["origin"],
                "destination": body["route"]["destination"],
                "distance_km": round(int(path["distance"]) / 1000, 2),
                "duration_minutes": round(int(path["duration"]) / 60),
                "tolls_cny": float(path.get("tolls") or 0),
                "polyline": ";".join(step.get("polyline", "") for step in steps if step.get("polyline")),
                "steps": [
                    {
                        "instruction": step.get("instruction"),
                        "road": step.get("road"),
                        "distance_m": int(step.get("distance") or 0),
                        "duration_s": int(step.get("duration") or 0),
                    }
                    for step in steps
                ],
            },
            sources=[SourceRecord(provider="高德地图", title="驾车路径规划 API", url=f"{AMAP_BASE_URL}/v3/direction/driving")],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready" if self.api_key else "degraded", "configured": bool(self.api_key)}


def _polyline_points(polyline: str) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    for item in polyline.split(";"):
        if not item or "," not in item:
            continue
        longitude, latitude = item.split(",", 1)
        points.append({"longitude": float(longitude), "latitude": float(latitude)})
    return points


def _haversine_km(origin: RoutePoint, destination: RoutePoint) -> float:
    earth_radius_km = 6371.0
    lat1, lat2 = radians(origin.latitude), radians(destination.latitude)
    delta_lat = lat2 - lat1
    delta_lng = radians(destination.longitude - origin.longitude)
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lng / 2) ** 2
    return 2 * earth_radius_km * asin(sqrt(value))


class AmapRouteAdapter(SkillAdapter):
    name = "amap.route"
    category = "routing"
    cache_ttl_seconds = 1800
    timeout_seconds = 7.0
    max_retries = 1

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return UnifiedRouteInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        if not self.api_key:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=["未配置 AMAP_WEBSERVICE_KEY"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = UnifiedRouteInput.model_validate(payload)
        candidates = self._candidates(request)
        attempted: list[str] = []
        failure_reasons: list[str] = []
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for mode in candidates:
                attempted.append(mode)
                route, reason = await self._fetch_mode(client, mode, request)
                if route:
                    endpoint = route.pop("endpoint")
                    return SkillResult(
                        success=True,
                        provider="高德地图",
                        data={
                            "requested_mode": request.preferred_mode,
                            "selected_mode": mode,
                            "fallback_used": mode != request.preferred_mode,
                            "fallback_reason": failure_reasons[-1] if failure_reasons else None,
                            "attempted_modes": attempted,
                            **route,
                        },
                        sources=[
                            SourceRecord(
                                provider="高德地图",
                                title=f"{mode} 路径规划 API",
                                url=endpoint,
                            )
                        ],
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                failure_reasons.append(reason or f"AMAP_{mode.upper()}_NO_RESULT")
        return SkillResult(
            success=False,
            provider="高德地图",
            data={"attempted_modes": attempted, "failure_reasons": failure_reasons},
            warnings=["允许的交通方式均未返回可执行路线"],
            latency_ms=int((time.perf_counter() - started) * 1000),
            error_code="ROUTE_UNAVAILABLE",
        )

    def _candidates(self, request: UnifiedRouteInput) -> list[str]:
        distance = _haversine_km(request.origin, request.destination)
        same_city = (
            bool(request.origin.city)
            and request.origin.city == request.destination.city
        )
        fallback = list(request.allowed_fallback_modes)
        if request.preferred_mode == "driving":
            if same_city and distance <= 3:
                preference = ["walking", "riding", "transit"]
            elif same_city and distance <= 30:
                preference = ["riding", "transit", "walking"]
            elif same_city:
                preference = ["transit"]
            else:
                preference = []
            fallback = [mode for mode in preference if mode in fallback]
        candidates = [request.preferred_mode, *fallback]
        return list(dict.fromkeys(candidates))

    async def _fetch_mode(
        self,
        client: httpx.AsyncClient,
        mode: str,
        request: UnifiedRouteInput,
    ) -> tuple[dict[str, Any] | None, str | None]:
        origin = request.origin.location
        destination = request.destination.location
        if mode == "riding":
            endpoint = f"{AMAP_BASE_URL}/v4/direction/bicycling"
            response = await client.get(
                endpoint,
                params={"key": self.api_key, "origin": origin, "destination": destination},
            )
            response.raise_for_status()
            body = response.json()
            paths = body.get("data", {}).get("paths", [])
            if str(body.get("errcode")) not in {"0", "10000"} or not paths:
                return None, body.get("errmsg") or "AMAP_RIDING_NO_RESULT"
            return self._normal_path(paths[0], endpoint), None

        if mode == "transit":
            city = request.origin.city
            if not city or city != request.destination.city:
                return None, "AMAP_TRANSIT_REQUIRES_SAME_CITY"
            endpoint = f"{AMAP_BASE_URL}/v3/direction/transit/integrated"
            response = await client.get(
                endpoint,
                params={
                    "key": self.api_key,
                    "origin": origin,
                    "destination": destination,
                    "city": city,
                    "cityd": request.destination.city,
                    "extensions": "all",
                },
            )
            response.raise_for_status()
            body = response.json()
            transits = body.get("route", {}).get("transits", [])
            if body.get("status") != "1" or not transits:
                return None, body.get("info") or "AMAP_TRANSIT_NO_RESULT"
            return self._transit_path(transits[0], endpoint), None

        endpoint = f"{AMAP_BASE_URL}/v3/direction/{mode}"
        params: dict[str, Any] = {
            "key": self.api_key,
            "origin": origin,
            "destination": destination,
            "extensions": "all",
        }
        if mode == "driving":
            params["strategy"] = request.strategy
            if request.waypoints:
                params["waypoints"] = ";".join(point.location for point in request.waypoints)
        response = await client.get(endpoint, params=params)
        response.raise_for_status()
        body = response.json()
        paths = body.get("route", {}).get("paths", [])
        if body.get("status") != "1" or not paths:
            return None, body.get("info") or f"AMAP_{mode.upper()}_NO_RESULT"
        return self._normal_path(paths[0], endpoint), None

    @staticmethod
    def _normal_path(path: dict[str, Any], endpoint: str) -> dict[str, Any]:
        steps = path.get("steps", [])
        traffic_segments = [
            {
                "status": traffic.get("status") or "未知",
                "distance_m": int(float(traffic.get("distance") or 0)),
                "geometry": _polyline_points(traffic.get("polyline", "")),
            }
            for step in steps
            for traffic in step.get("tmcs", [])
        ]
        traffic_distance: dict[str, int] = {}
        for segment in traffic_segments:
            status = segment["status"]
            traffic_distance[status] = traffic_distance.get(status, 0) + segment["distance_m"]
        known_distance = sum(traffic_distance.values())
        congested_distance = sum(
            distance
            for status, distance in traffic_distance.items()
            if status in {"缓行", "拥堵", "严重拥堵"}
        )
        if not traffic_segments:
            traffic_summary = "高德未返回分段实时路况"
        elif congested_distance == 0:
            traffic_summary = "高德当前路况整体畅通"
        else:
            ratio = round(congested_distance / max(known_distance, 1) * 100)
            traffic_summary = f"高德当前缓行或拥堵路段约占 {ratio}%"
        geometry = [
            point
            for step in steps
            for point in _polyline_points(step.get("polyline", ""))
        ]
        return {
            "distance_km": round(float(path.get("distance") or 0) / 1000, 2),
            "duration_minutes": round(float(path.get("duration") or 0) / 60),
            "tolls_cny": float(path.get("tolls") or 0),
            "geometry": geometry,
            "steps": [
                {
                    "instruction": step.get("instruction"),
                    "road": step.get("road"),
                    "distance_m": int(float(step.get("distance") or 0)),
                    "duration_s": int(float(step.get("duration") or 0)),
                }
                for step in steps
            ],
            "traffic_summary": traffic_summary,
            "traffic_segments": traffic_segments,
            "traffic_lights": int(float(path.get("traffic_lights") or 0)),
            "restriction": str(path.get("restriction") or "0"),
            "transfers": [],
            "fare_cny": None,
            "endpoint": endpoint,
        }

    @staticmethod
    def _transit_path(transit: dict[str, Any], endpoint: str) -> dict[str, Any]:
        geometry: list[dict[str, float]] = []
        transfers: list[dict[str, Any]] = []
        transit_legs: list[dict[str, Any]] = []
        for segment in transit.get("segments", []):
            for step in segment.get("walking", {}).get("steps", []):
                geometry.extend(_polyline_points(step.get("polyline", "")))
            railway = segment.get("railway") or segment.get("rail") or {}
            for line in railway.get("spaces", []) if isinstance(railway, dict) else []:
                transit_legs.append(
                    {
                        "mode": "subway" if "地铁" in str(line.get("name") or "") else "rail",
                        "line_name": line.get("name"),
                        "line_id": line.get("id") or line.get("line_id"),
                        "line_type": "rail",
                        "departure_stop": (line.get("departure_stop") or {}).get("name"),
                        "arrival_stop": (line.get("arrival_stop") or {}).get("name"),
                        "stop_count": line.get("via_num") or line.get("stop_count"),
                        "duration_minutes": round(float(line.get("duration") or 0) / 60) or None,
                        "distance_km": round(float(line.get("distance") or 0) / 1000, 2) or None,
                        "fare_cny": float(line.get("cost") or 0) or None,
                    }
                )
            for line in segment.get("bus", {}).get("buslines", []):
                geometry.extend(_polyline_points(line.get("polyline", "")))
                line_name = line.get("name") or line.get("line_name")
                line_type = str(line.get("type") or "bus").lower()
                mode = "subway" if "地铁" in str(line_name or "") or "subway" in line_type else "bus"
                leg = {
                    "mode": mode,
                    "line_name": line_name,
                    "line_id": line.get("id") or line.get("line_id"),
                    "line_type": line.get("type") or mode,
                    "departure_stop": (line.get("departure_stop") or {}).get("name"),
                    "arrival_stop": (line.get("arrival_stop") or {}).get("name"),
                    "departure_time": line.get("stime") or line.get("departure_time"),
                    "arrival_time": line.get("etime") or line.get("arrival_time"),
                    "stop_count": line.get("via_num") or line.get("stop_count"),
                    "duration_minutes": round(float(line.get("duration") or 0) / 60) or None,
                    "distance_km": round(float(line.get("distance") or 0) / 1000, 2) or None,
                    "fare_cny": float(line.get("cost") or 0) or None,
                }
                transfers.append(
                    {
                        "name": line_name,
                        "departure_stop": leg["departure_stop"],
                        "arrival_stop": leg["arrival_stop"],
                    }
                )
                transit_legs.append(leg)
        transit_summary = "；".join(
            f"{leg.get('line_name') or '公共交通'}（{leg.get('departure_stop') or '上车'}→{leg.get('arrival_stop') or '下车'}）"
            for leg in transit_legs
        )
        return {
            "distance_km": round(float(transit.get("distance") or 0) / 1000, 2),
            "duration_minutes": round(float(transit.get("duration") or 0) / 60),
            "tolls_cny": 0,
            "geometry": geometry,
            "steps": [],
            "transfers": transfers,
            "transit_legs": transit_legs,
            "transit_summary": transit_summary or None,
            "fare_cny": float(transit.get("cost") or 0) or None,
            "endpoint": endpoint,
        }

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready" if self.api_key else "degraded", "configured": bool(self.api_key)}


class AmapPoiAdapter(SkillAdapter):
    name = "amap.poi"
    # The provider can return status=1 with an empty POI array.  Bump the
    # adapter version so any previously cached empty response is ignored after
    # deploying the non-empty-result guard.
    version = "1.1.0"
    category = "poi"
    cache_ttl_seconds = 6 * 3600

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return PoiInput.model_validate(payload).model_dump(exclude_none=True)

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        if not self.api_key:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=["未配置 AMAP_WEBSERVICE_KEY"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        started = time.perf_counter()
        endpoint = f"{AMAP_BASE_URL}/v5/place/text"
        request = PoiInput.model_validate(payload)
        params = {
            "key": self.api_key,
            "keywords": request.keywords,
            "region": request.city,
            "city_limit": "true" if request.city else None,
            "types": request.types,
            "location": request.location,
            "radius": request.radius if request.location else None,
            "page_num": request.page,
            "page_size": request.page_size,
            "show_fields": "business,photos",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                endpoint,
                params={key: value for key, value in params.items() if value is not None},
            )
            response.raise_for_status()
            body = response.json()
        pois = body.get("pois", [])
        if body.get("status") != "1":
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=[body.get("info", "POI 查询失败")],
                error_code="AMAP_POI_FAILED",
            )
        if not pois:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=["高德地图未返回匹配的地点"],
                error_code="AMAP_POI_EMPTY",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return SkillResult(
            success=True,
            provider="高德地图",
            data={
                "count": int(body.get("count") or len(pois)),
                "items": [
                    {
                        "id": poi.get("id"),
                        "name": poi.get("name"),
                        "address": _text_value(poi.get("address")),
                        "type": poi.get("type"),
                        "location": poi.get("location"),
                        "city": poi.get("cityname"),
                        "district": poi.get("adname"),
                        **_poi_facts(poi),
                    }
                    for poi in pois
                ],
            },
            sources=[SourceRecord(provider="高德地图", title="POI 2.0 API", url=endpoint)],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready" if self.api_key else "degraded", "configured": bool(self.api_key)}


class AmapPoiDetailAdapter(SkillAdapter):
    """Fetch authoritative business facts for one concrete AMap POI."""

    name = "amap.poi_detail"
    category = "poi"
    cache_ttl_seconds = 6 * 3600

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return PoiDetailInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        if not self.api_key:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=["未配置 AMAP_WEBSERVICE_KEY"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = PoiDetailInput.model_validate(payload)
        started = time.perf_counter()
        endpoint = f"{AMAP_BASE_URL}/v3/place/detail"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                endpoint,
                params={"key": self.api_key, "id": request.poi_id, "extensions": "all"},
            )
            response.raise_for_status()
            body = response.json()
        pois = body.get("pois") or body.get("poi") or []
        if isinstance(pois, dict):
            pois = [pois]
        if body.get("status") != "1" or not pois:
            return SkillResult(
                success=False,
                provider="高德地图",
                warnings=[body.get("info", "未找到 POI 详情")],
                error_code="AMAP_POI_DETAIL_FAILED",
            )
        poi = pois[0]
        facts = _poi_facts(poi)
        item = {
            "id": poi.get("id") or request.poi_id,
            "name": poi.get("name"),
            "address": _text_value(poi.get("address")),
            "location": poi.get("location"),
            "city": poi.get("cityname"),
            "district": poi.get("adname"),
            **facts,
        }
        source = SourceRecord(
            provider="高德地图",
            title="POI 详情与营业信息",
            url=endpoint,
            source_type="map",
            confidence="high",
            facts={key: value for key, value in facts.items() if value not in (None, [], "")},
        )
        return SkillResult(
            success=True,
            provider="高德地图",
            data={"item": item},
            sources=[source],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready" if self.api_key else "degraded", "configured": bool(self.api_key)}


def _text_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        text = "、".join(str(item) for item in value if item)
        return text or None
    return str(value) if value is not None else None
