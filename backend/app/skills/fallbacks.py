"""Honest public-data fallbacks for scheduled transport and fuel prices.

The planner's first choice remains the connected travel-information Skill.  These
adapters are intentionally small, provider-agnostic boundaries: they normalize
the public responses into the same ``data.items`` contract used by the primary
search, preserve real service numbers when present, and return an explicit
unavailable result instead of inventing a timetable.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, time as clock, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..domain.models import SkillResult, SourceRecord
from .base import SkillAdapter, SkillContext


class TrainFallbackInput(BaseModel):
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    dep_date: date
    page: int = Field(default=1, ge=1, le=10)


class FlightFallbackInput(BaseModel):
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    dep_date: date
    dep_code: str | None = Field(default=None, min_length=3, max_length=8)
    arr_code: str | None = Field(default=None, min_length=3, max_length=8)
    page: int = Field(default=1, ge=1, le=10)


class OilPriceInput(BaseModel):
    province: str = Field(min_length=1, max_length=40)


class GeocodeFallbackInput(BaseModel):
    query: str = Field(min_length=1, max_length=160)
    limit: int = Field(default=3, ge=1, le=10)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _parse_clock(value: Any, day: date) -> datetime | None:
    """Parse both free-api clock fields and ISO/date-time variants."""
    if isinstance(value, datetime):
        return value
    text = _text(value)
    if not text:
        return None
    # Unix seconds/milliseconds occasionally appear in aggregator payloads.
    if re.fullmatch(r"\d{10,13}", text):
        try:
            stamp = int(text) / (1000 if len(text) == 13 else 1)
            return datetime.fromtimestamp(stamp)
        except (OverflowError, OSError, ValueError):
            pass
    normalized = text.replace("T", " ").replace("/", "-")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m-%d %H:%M",
        "%H:%M",
        "%H%M",
    ):
        try:
            parsed = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        if fmt in {"%H:%M", "%H%M"}:
            return datetime.combine(day, parsed.time())
        if fmt == "%Y-%m-%d":
            return datetime.combine(parsed.date(), clock(0, 0))
        if fmt == "%m-%d %H:%M":
            return parsed.replace(year=day.year)
        return parsed
    return None


def _duration_minutes(value: Any, start: datetime, end: datetime) -> int:
    text = _text(value).lower()
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:小时|hour|hr|h)", text)
    minutes = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|分|minute|min|m)", text)
    if hours or minutes:
        total = (float(hours.group(1)) * 60 if hours else 0) + (
            float(minutes.group(1)) if minutes else 0
        )
        return max(1, round(total))
    if text and re.fullmatch(r"\d+(?:\.\d+)?", text):
        # The free train endpoint returns a duration string in hours for some
        # deployments and minutes for others.  A value with a decimal is most
        # commonly hours; a large integer is minutes.
        numeric = float(text)
        return max(1, round(numeric * 60 if numeric < 24 and "." in text else numeric))
    return max(1, round((end - start).total_seconds() / 60))


def _same_place(expected: str, actual: Any) -> bool:
    expected_text = re.sub(r"(市|区|县|站|机场)$", "", _text(expected)).casefold()
    actual_text = _text(actual).casefold()
    return not actual_text or expected_text in actual_text or actual_text in expected_text


def _seat(value: Any) -> tuple[str | None, float | None]:
    """Pick one useful seat/price from common aggregator shapes."""
    if isinstance(value, dict):
        for name, raw in value.items():
            price_match = re.search(r"\d+(?:\.\d+)?", _text(raw))
            if price_match:
                try:
                    return _text(name) or None, float(price_match.group(0))
                except ValueError:
                    pass
        return None, None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                label = _first(item, "name", "seat", "seatName", "type", "title")
                raw_price = _first(item, "price", "amount", "value")
                match = re.search(r"\d+(?:\.\d+)?", _text(raw_price))
                if match:
                    return _text(label) or None, float(match.group(0))
            else:
                match = re.search(r"\d+(?:\.\d+)?", _text(item))
                if match:
                    return None, float(match.group(0))
    return None, None


class FreeApiTrainAdapter(SkillAdapter):
    """Use the public train endpoint when the primary travel Skill is down."""

    name = "freeapi.train"
    version = "1.0.0"
    category = "travel_search_fallback"
    timeout_seconds = 10
    max_retries = 1
    cache_ttl_seconds = 10 * 60

    def __init__(self, endpoint: str):
        self.endpoint = endpoint.rstrip("/")

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return TrainFallbackInput.model_validate(payload).model_dump(mode="json")

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = TrainFallbackInput.model_validate(payload)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    self.endpoint,
                    params={
                        "departure": request.origin,
                        "arrival": request.destination,
                        "type": "json",
                        "page": request.page,
                    },
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return SkillResult(
                success=False,
                provider="公共交通备选服务",
                warnings=[f"备用车次服务暂时不可用：{type(exc).__name__}"],
                error_code="FREEAPI_TRAIN_UNAVAILABLE",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        if str(body.get("code")) not in {"200", "1", "0.0"}:
            return SkillResult(
                success=False,
                provider="公共交通备选服务",
                warnings=[_text(body.get("msg")) or "备用车次服务未返回有效结果"],
                error_code="FREEAPI_TRAIN_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        raw_items = body.get("data")
        if isinstance(raw_items, dict):
            raw_items = _first(raw_items, "list", "items", "data")
        if not isinstance(raw_items, list):
            raw_items = []
        items: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            dep = _parse_clock(
                _first(raw, "DepartDateTime", "departDateTime", "DepartTime", "departureTime", "startTime"),
                request.dep_date,
            )
            arr = _parse_clock(
                _first(raw, "ArriveDateTime", "arriveDateTime", "ArriveTime", "arrivalTime", "endTime"),
                request.dep_date,
            )
            if not dep or not arr:
                continue
            if arr <= dep:
                arr += timedelta(days=1)
            start_station = _first(raw, "start", "StartStation", "from", "fromStation", "depStation")
            end_station = _first(raw, "end", "EndStation", "to", "toStation", "arrStation")
            if not _same_place(request.origin, start_station) or not _same_place(request.destination, end_station):
                continue
            number = _text(_first(raw, "TrainNumber", "trainNumber", "train_no", "trainNo", "number"))
            seat_class, price = _seat(_first(raw, "SeatList", "seatList", "seats", "seat"))
            if price is None:
                raw_price = _first(raw, "price", "adultPrice", "ticketPrice")
                match = re.search(r"\d+(?:\.\d+)?", _text(raw_price))
                price = float(match.group(0)) if match else None
            items.append(
                {
                    "id": number or f"freeapi_train_{index}",
                    "departure_at": dep.isoformat(),
                    "arrival_at": arr.isoformat(),
                    "duration_minutes": _duration_minutes(
                        _first(raw, "TimeDifference", "timeDifference", "duration", "durationMinutes"),
                        dep,
                        arr,
                    ),
                    "train_number": number or None,
                    "service_number": number or None,
                    "transport_name": _text(_first(raw, "TrainType", "trainType", "type")) or "列车",
                    "operator": _text(_first(raw, "operator", "company")) or None,
                    "departure_station": _text(start_station) or request.origin,
                    "arrival_station": _text(end_station) or request.destination,
                    "service_status": "confirmed" if number else "unavailable",
                    "seat_class": seat_class,
                    "price": price,
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="公共交通备选服务",
                warnings=["备用车次服务没有返回匹配日期和区间的直达车次"],
                error_code="FREEAPI_TRAIN_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return SkillResult(
            success=True,
            provider="公共交通备选服务",
            data={"items": items, "count": len(items), "fallback": True},
            sources=[
                SourceRecord(
                    provider="公共交通备选服务",
                    title="公开车次查询",
                    url="https://www.free-api.com/doc/675",
                    source_type="open_data",
                    confidence="medium",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": bool(self.endpoint)}


# A small metadata table is transport data, not intent recognition.  Unknown
# cities deliberately return an unavailable result so the primary provider can
# remain the source of truth instead of receiving a guessed airport code.
AIRPORT_CODES: dict[str, str] = {
    "北京": "PEK", "上海": "SHA", "广州": "CAN", "深圳": "SZX", "成都": "CTU",
    "重庆": "CKG", "武汉": "WUH", "长沙": "CSX", "西安": "XIY", "南京": "NKG",
    "杭州": "HGH", "厦门": "XMN", "福州": "FOC", "昆明": "KMG", "贵阳": "KWE",
    "哈尔滨": "HRB", "沈阳": "SHE", "大连": "DLC", "青岛": "TAO", "郑州": "CGO",
    "济南": "TNA", "天津": "TSN", "海口": "HAK", "三亚": "SYX", "乌鲁木齐": "URC",
    "西宁": "XNN", "兰州": "LHW", "南昌": "KHN", "合肥": "HFE", "宁波": "NGB",
    "无锡": "WUX", "太原": "TYN", "石家庄": "SJW", "呼和浩特": "HET", "长春": "CGQ",
}


def _airport_code(value: str) -> str | None:
    text = re.sub(r"(市|地区|自治州|省)$", "", _text(value))
    if re.fullmatch(r"[A-Za-z]{3}", text):
        return text.upper()
    return AIRPORT_CODES.get(text)


def _iter_flight_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    candidates: list[Any] = [body.get("data"), body.get("result"), body.get("list"), body.get("items")]
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = _first(candidate, "list", "items", "data", "records", "result")
        if isinstance(candidate, list):
            rows.extend(item for item in candidate if isinstance(item, dict))
    return rows


class SixApiFlightAdapter(SkillAdapter):
    """Optional 6API flight fallback; it is used only when a key is present."""

    name = "sixapi.flight"
    version = "1.0.0"
    category = "travel_search_fallback"
    timeout_seconds = 10
    max_retries = 1
    cache_ttl_seconds = 10 * 60

    def __init__(self, endpoint: str, api_key: str):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlightFallbackInput.model_validate(payload).model_dump(mode="json", exclude_none=True)

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = FlightFallbackInput.model_validate(payload)
        if not self.api_key:
            return SkillResult(
                success=False,
                provider="航班备选服务",
                warnings=["未配置航班备用服务密钥，已保留主查询结果"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        dep_code = request.dep_code or _airport_code(request.origin)
        arr_code = request.arr_code or _airport_code(request.destination)
        if not dep_code or not arr_code:
            return SkillResult(
                success=False,
                provider="航班备选服务",
                warnings=["备用航班服务无法为当前城市确定机场代码"],
                error_code="SIXAPI_AIRPORT_CODE_UNAVAILABLE",
            )
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    self.endpoint,
                    params={
                        "appkey": self.api_key,
                        "depCode": dep_code,
                        "arrCode": arr_code,
                        "depDate": request.dep_date.isoformat(),
                        "pageNo": request.page,
                    },
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return SkillResult(
                success=False,
                provider="航班备选服务",
                warnings=[f"备用航班服务暂时不可用：{type(exc).__name__}"],
                error_code="SIXAPI_FLIGHT_UNAVAILABLE",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        rows = _iter_flight_rows(body)
        items: list[dict[str, Any]] = []
        for index, raw in enumerate(rows):
            dep = _parse_clock(
                _first(raw, "depDateTime", "departureDateTime", "depTime", "departureTime", "takeOffTime"),
                request.dep_date,
            )
            arr = _parse_clock(
                _first(raw, "arrDateTime", "arrivalDateTime", "arrTime", "arrivalTime", "landingTime"),
                request.dep_date,
            )
            if not dep or not arr:
                continue
            if arr <= dep:
                arr += timedelta(days=1)
            number = _text(_first(raw, "flightNo", "flightNumber", "flight_num", "number"))
            if not number:
                continue
            items.append(
                {
                    "id": number or f"sixapi_flight_{index}",
                    "departure_at": dep.isoformat(),
                    "arrival_at": arr.isoformat(),
                    "duration_minutes": _duration_minutes(_first(raw, "duration", "durationMinutes"), dep, arr),
                    "flight_number": number,
                    "service_number": number,
                    "carrier": _text(_first(raw, "airline", "carrier", "airlineName")) or None,
                    "operator": _text(_first(raw, "airline", "carrier", "airlineName")) or None,
                    "departure_city": request.origin,
                    "arrival_city": request.destination,
                    "departure_airport": _text(_first(raw, "depAirport", "departureAirport", "depAirportName")) or dep_code,
                    "arrival_airport": _text(_first(raw, "arrAirport", "arrivalAirport", "arrAirportName")) or arr_code,
                    "service_status": "confirmed",
                    "price": _first(raw, "price", "ticketPrice", "adultPrice"),
                    "detail_url": _first(raw, "url", "detailUrl", "link"),
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="航班备选服务",
                warnings=["备用航班服务没有返回可解析的班次"],
                error_code="SIXAPI_FLIGHT_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return SkillResult(
            success=True,
            provider="航班备选服务",
            data={"items": items, "count": len(items), "fallback": True},
            sources=[
                SourceRecord(
                    provider="航班备选服务",
                    title="公开航班查询",
                    url="https://www.6api.net/api/flight/",
                    source_type="open_data",
                    confidence="medium",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready" if self.api_key else "degraded", "configured": bool(self.api_key)}


class FreeApiOilAdapter(SkillAdapter):
    """Optional public oil-price lookup; it never blocks itinerary planning."""

    name = "freeapi.oil"
    version = "1.0.0"
    category = "travel_context"
    timeout_seconds = 8
    max_retries = 1
    cache_ttl_seconds = 6 * 3600

    def __init__(self, endpoint: str, app_id: str, app_secret: str):
        self.endpoint = endpoint.rstrip("/")
        self.app_id = app_id
        self.app_secret = app_secret

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return OilPriceInput.model_validate(payload).model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = OilPriceInput.model_validate(payload)
        if not self.app_id or not self.app_secret:
            return SkillResult(
                success=False,
                provider="油价公开数据",
                warnings=["未配置油价服务凭据，已跳过油价查询"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    self.endpoint,
                    params={"province": request.province, "app_id": self.app_id, "app_secret": self.app_secret},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return SkillResult(
                success=False,
                provider="油价公开数据",
                warnings=[f"油价服务暂时不可用：{type(exc).__name__}"],
                error_code="FREEAPI_OIL_UNAVAILABLE",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        if str(body.get("code")) not in {"1", "200"} or not isinstance(body.get("data"), dict):
            return SkillResult(
                success=False,
                provider="油价公开数据",
                warnings=[_text(body.get("msg")) or "油价服务未返回有效结果"],
                error_code="FREEAPI_OIL_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        raw = body["data"]
        prices = {
            grade: _first(raw, key, key.upper())
            for grade, key in (("0", "t0"), ("89", "t89"), ("92", "t92"), ("95", "t95"), ("98", "t98"))
            if _first(raw, key, key.upper()) not in (None, "")
        }
        return SkillResult(
            success=True,
            provider="油价公开数据",
            data={
                "province": _text(raw.get("province")) or request.province,
                "prices": prices,
                "as_of": date.today().isoformat(),
                "estimated": False,
            },
            sources=[
                SourceRecord(
                    provider="油价公开数据",
                    title="今日油价公开接口",
                    url="https://www.free-api.com/doc/592",
                    source_type="open_data",
                    confidence="medium",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.app_id and self.app_secret else "degraded",
            "configured": bool(self.app_id and self.app_secret),
        }


class OpenStreetMapGeocodeAdapter(SkillAdapter):
    """Public geocoding fallback for a named scenic area.

    A commercial map key can expose POI search while geocoding is disabled or
    temporarily degraded.  Nominatim supplies a small, rate-limited public
    fallback so an explicit user requirement can still receive a concrete
    coordinate.  It is never used to infer a destination from free text.
    """

    name = "osm.geocode"
    version = "1.0.0"
    category = "geocoding_fallback"
    timeout_seconds = 8
    max_retries = 1
    cache_ttl_seconds = 7 * 24 * 3600

    def __init__(self, endpoint: str = "https://nominatim.openstreetmap.org/search"):
        self.endpoint = endpoint.rstrip("/")

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return GeocodeFallbackInput.model_validate(payload).model_dump(mode="json")

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = GeocodeFallbackInput.model_validate(payload)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    self.endpoint,
                    params={
                        "q": request.query,
                        "format": "jsonv2",
                        "limit": request.limit,
                        "addressdetails": 1,
                    },
                    headers={"User-Agent": "RoadMan/1.0 public-geocode"},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return SkillResult(
                success=False,
                provider="OpenStreetMap",
                warnings=[f"公开地理编码暂时不可用：{type(exc).__name__}"],
                error_code="OSM_GEOCODE_UNAVAILABLE",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        if not isinstance(body, list):
            return SkillResult(
                success=False,
                provider="OpenStreetMap",
                warnings=["公开地理编码未返回有效结果"],
                error_code="OSM_GEOCODE_INVALID_RESPONSE",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        items: list[dict[str, Any]] = []
        for row in body:
            if not isinstance(row, dict):
                continue
            try:
                latitude = float(row["lat"])
                longitude = float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            items.append(
                {
                    "id": f"osm:{row.get('osm_type')}:{row.get('osm_id')}",
                    "name": _text(row.get("name")) or request.query,
                    "formatted_address": _text(row.get("display_name")) or request.query,
                    "location": f"{longitude},{latitude}",
                    "latitude": latitude,
                    "longitude": longitude,
                    "category": row.get("type"),
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="OpenStreetMap",
                warnings=["公开地理编码未找到对应地点"],
                error_code="OSM_GEOCODE_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return SkillResult(
            success=True,
            provider="OpenStreetMap",
            data={"items": items, **items[0]},
            sources=[
                SourceRecord(
                    provider="OpenStreetMap",
                    title="公开地理编码（Nominatim）",
                    url="https://nominatim.openstreetmap.org/",
                    source_type="open_data",
                    confidence="medium",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": bool(self.endpoint)}
