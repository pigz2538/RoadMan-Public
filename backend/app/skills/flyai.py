from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..domain.models import SkillResult, SourceRecord
from .base import SkillAdapter, SkillContext


class FlyAIHotelInput(BaseModel):
    destination: str = Field(min_length=1, max_length=80)
    poi_name: str | None = Field(default=None, max_length=80)
    check_in_date: date
    check_out_date: date
    max_price: int | None = Field(default=None, ge=1, le=100000)
    sort: str = "rate_desc"


class FlyAIPoiInput(BaseModel):
    city_name: str = Field(min_length=1, max_length=80)
    keyword: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=40)
    poi_level: int | None = Field(default=None, ge=1, le=5)


class FlyAISearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=240)


class FlyAITrainInput(BaseModel):
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    dep_date: date
    back_date: date | None = None
    dep_hour_start: int | None = Field(default=None, ge=0, le=23)
    dep_hour_end: int | None = Field(default=None, ge=0, le=23)
    total_duration_hour: int | None = Field(default=None, ge=1, le=48)
    sort_type: int = Field(default=4, ge=1, le=8)


class FlyAIFlightInput(BaseModel):
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    dep_date: date
    back_date: date | None = None
    dep_hour_start: int | None = Field(default=None, ge=0, le=23)
    dep_hour_end: int | None = Field(default=None, ge=0, le=23)
    total_duration_hour: int | None = Field(default=None, ge=1, le=48)
    sort_type: int = Field(default=4, ge=1, le=8)


class FlyAIFerryInput(BaseModel):
    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    dep_date: date
    query: str | None = Field(default=None, max_length=240)


FLYAI_POI_CATEGORIES = {
    "自然风光", "山湖田园", "森林丛林", "峡谷瀑布", "沙滩海岛", "沙漠草原",
    "人文古迹", "古镇古村", "历史古迹", "园林花园", "宗教场所", "公园乐园",
    "主题乐园", "水上乐园", "影视基地", "动物园", "植物园", "海洋馆",
    "体育场馆", "演出赛事", "剧院剧场", "博物馆", "纪念馆", "展览馆",
    "地标建筑", "市集", "文创街区", "城市观光", "户外活动", "滑雪",
    "漂流", "冲浪", "潜水", "露营", "温泉",
}


def _train_duration_minutes(value: object) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(1, round(float(value)))
    match = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))
    if not match:
        return None
    return max(1, round(float(match.group(1))))


def _train_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _flight_datetime(value: object) -> datetime | None:
    """Parse the date/time format returned by FlyAI flight search."""
    return _train_datetime(value)


def _duration_minutes(value: object) -> int | None:
    """Normalize minutes/hours returned by different FlyAI providers."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        # Structured flight/train responses use minutes; a decimal hour is
        # only treated as hours when the value is clearly below one hour.
        return max(1, round(numeric))
    text = str(value or "").strip().lower()
    if not text:
        return None
    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:小时|小時|hour|hr|h)", text)
    minute_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|分鐘|minute|min|m)", text)
    if hour_match:
        return max(1, round(float(hour_match.group(1)) * 60))
    if minute_match:
        return max(1, round(float(minute_match.group(1))))
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return max(1, round(float(match.group(1)))) if match else None


def _transport_number(segment: dict[str, Any], *fallback_keys: str) -> str | None:
    """Return a real service identifier without confusing carrier names with it."""
    for key in ("marketingTransportNo", *fallback_keys, "transportNo", "transportNumber", "number", "code"):
        value = segment.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text
    return None


def _segment_numbers(segments: list[dict[str, Any]], *keys: str) -> list[str]:
    values: list[str] = []
    for segment in segments:
        number = _transport_number(segment, *keys)
        if number and number not in values:
            values.append(number)
    return values


def _place_variants(value: object) -> set[str]:
    """Build conservative name variants for provider-result validation."""
    text = str(value or "").strip().casefold()
    if not text:
        return set()
    variants = {text}
    trimmed = re.sub(r"(?:国际机场|机场|火车站|高铁站|客运站|站|市|县|区)$", "", text)
    if trimmed:
        variants.add(trimmed)
    return {item for item in variants if item}


def _place_in_text(place: object, text: object) -> bool:
    haystack = str(text or "").strip().casefold()
    return any(token in haystack for token in _place_variants(place))


def _flyai_process_env() -> dict[str, str]:
    """Pass Docker/host proxy settings to Node's built-in fetch client."""
    environment = os.environ.copy()
    # Node 22's undici fetch does not consume HTTP_PROXY by default.  The
    # CLI needs this switch in the container, otherwise every call fails at
    # DNS resolution before the FlyAI service can be reached.
    environment.setdefault("NODE_USE_ENV_PROXY", "1")
    return environment


def _flyai_spawn_args(command: str, *arguments: str) -> list[str]:
    """Invoke the npm CLI without a Windows ``.cmd`` code-page hop.

    ``asyncio.create_subprocess_exec`` launches ``flyai.CMD`` directly on
    Windows.  That wrapper can transcode Chinese city names before Node sees
    them, which makes the provider return an empty result even though the same
    command works in PowerShell.  Calling the bundled Node entrypoint directly
    preserves UTF-8 arguments while keeping Linux/container execution intact.
    """
    command_path = Path(command)
    if os.name == "nt" and command_path.suffix.lower() in {".cmd", ".bat"}:
        node = shutil.which("node")
        bundle = command_path.parent / "node_modules" / "@fly-ai" / "flyai-cli" / "dist" / "flyai-bundle.cjs"
        if node and bundle.is_file():
            # Python's Windows subprocess layer in the desktop deployment can
            # replace non-ASCII argv characters with ``?`` before CreateProcess
            # reaches Node.  Keep the actual argv ASCII-only and let a tiny
            # Node bootstrap decode the UTF-8 arguments from an inline base64
            # payload before loading the FlyAI bundle.
            encoded_args = base64.b64encode(
                json.dumps(list(arguments), ensure_ascii=False).encode("utf-8")
            ).decode("ascii")
            bundle_literal = json.dumps(str(bundle))
            script = (
                f"const a=JSON.parse(Buffer.from('{encoded_args}','base64').toString('utf8'));"
                f"process.argv=[process.execPath,...a];"
                f"require({bundle_literal});"
            )
            return [node, "-e", script]
    return [command, *arguments]


class FlyAITrainAdapter(SkillAdapter):
    """Search real intercity train options through the FlyAI CLI."""

    name = "flyai.train"
    version = "1.0.0"
    category = "travel_search"
    timeout_seconds = 25
    max_retries = 0
    cache_ttl_seconds = 15 * 60

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlyAITrainInput.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        command = shutil.which("flyai")
        if not command:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["运行环境未安装 flyai CLI"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = FlyAITrainInput.model_validate(payload)
        arguments = [
            command,
            "search-train",
            "--origin",
            request.origin,
            "--destination",
            request.destination,
            "--dep-date",
            request.dep_date.isoformat(),
            "--sort-type",
            str(request.sort_type),
        ]
        if request.back_date:
            arguments.extend(["--back-date", request.back_date.isoformat()])
        if request.dep_hour_start is not None:
            arguments.extend(["--dep-hour-start", str(request.dep_hour_start)])
        if request.dep_hour_end is not None:
            arguments.extend(["--dep-hour-end", str(request.dep_hour_end)])
        if request.total_duration_hour is not None:
            arguments.extend(["--total-duration-hour", str(request.total_duration_hour)])
        arguments = _flyai_spawn_args(command, *arguments[1:])

        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_flyai_process_env(),
        )
        stdout, stderr = await process.communicate()
        text = stdout.decode("utf-8", errors="replace").strip()
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=[
                    "FlyAI 火车搜索未返回可解析的 JSON",
                    stderr.decode("utf-8", errors="replace")[:160],
                ],
                error_code="FLYAI_INVALID_RESPONSE",
            )

        items: list[dict[str, Any]] = []
        raw_items = (body.get("data") or {}).get("itemList", [])
        for index, raw in enumerate(raw_items if isinstance(raw_items, list) else []):
            if not isinstance(raw, dict):
                continue
            journeys = raw.get("journeys") or []
            journey = journeys[0] if journeys and isinstance(journeys[0], dict) else {}
            raw_segments = journey.get("segments") or []
            segments = [
                segment
                for segment in raw_segments
                if isinstance(segment, dict)
                and _train_datetime(segment.get("depDateTime"))
                and _train_datetime(segment.get("arrDateTime"))
            ]
            if not segments:
                continue
            first, last = segments[0], segments[-1]
            departure_at = _train_datetime(first.get("depDateTime"))
            arrival_at = _train_datetime(last.get("arrDateTime"))
            if not departure_at or not arrival_at:
                continue
            duration = _train_duration_minutes(
                journey.get("totalDuration") or raw.get("totalDuration")
            )
            if duration is None:
                duration = max(1, round((arrival_at - departure_at).total_seconds() / 60))
            train_numbers = _segment_numbers(segments, "trainNo", "trainNumber")
            items.append(
                {
                    "id": raw.get("id") or f"train_{index}",
                    "journey_type": journey.get("journeyType"),
                    "segments": segments,
                    "departure_at": departure_at.isoformat(),
                    "arrival_at": arrival_at.isoformat(),
                    "duration_minutes": duration,
                    "train_number": " / ".join(train_numbers) if train_numbers else None,
                    "service_number": " / ".join(train_numbers) if train_numbers else None,
                    "transport_name": first.get("marketingTransportName") or first.get("transportType"),
                    "operator": first.get("marketingTransportName") or first.get("operator"),
                    "departure_station": first.get("depStationName"),
                    "arrival_station": last.get("arrStationName"),
                    "service_status": "confirmed" if train_numbers else "unavailable",
                    "seat_class": first.get("seatClassName"),
                    "price": raw.get("price") or raw.get("adultPrice"),
                    "detail_url": raw.get("jumpUrl"),
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 未返回可用高铁/火车车次"],
                error_code="FLYAI_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={"items": items, "count": len(items)},
            sources=[
                SourceRecord(
                    provider="FlyAI / 飞猪",
                    title="高铁/火车实时车次搜索",
                    url="https://www.fliggy.com/",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if shutil.which("flyai") else "degraded",
            "configured": bool(shutil.which("flyai")),
            "api_key_configured": bool(os.getenv("FLYAI_API_KEY")),
        }


class FlyAIFlightAdapter(SkillAdapter):
    """Search real intercity flight options through the FlyAI CLI."""

    name = "flyai.flight"
    version = "1.0.0"
    category = "travel_search"
    timeout_seconds = 25
    max_retries = 0
    cache_ttl_seconds = 15 * 60

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlyAIFlightInput.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        command = shutil.which("flyai")
        if not command:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["运行环境未安装 flyai CLI"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = FlyAIFlightInput.model_validate(payload)
        arguments = [
            command,
            "search-flight",
            "--origin",
            request.origin,
            "--destination",
            request.destination,
            "--dep-date",
            request.dep_date.isoformat(),
            "--sort-type",
            str(request.sort_type),
        ]
        if request.back_date:
            arguments.extend(["--back-date", request.back_date.isoformat()])
        if request.dep_hour_start is not None:
            arguments.extend(["--dep-hour-start", str(request.dep_hour_start)])
        if request.dep_hour_end is not None:
            arguments.extend(["--dep-hour-end", str(request.dep_hour_end)])
        if request.total_duration_hour is not None:
            arguments.extend(["--total-duration-hour", str(request.total_duration_hour)])
        arguments = _flyai_spawn_args(command, *arguments[1:])

        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_flyai_process_env(),
        )
        stdout, stderr = await process.communicate()
        text = stdout.decode("utf-8", errors="replace").strip()
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=[
                    "FlyAI 航班搜索未返回可解析的 JSON",
                    stderr.decode("utf-8", errors="replace")[:160],
                ],
                error_code="FLYAI_INVALID_RESPONSE",
            )

        items: list[dict[str, Any]] = []
        raw_items = (body.get("data") or {}).get("itemList", [])
        for index, raw in enumerate(raw_items if isinstance(raw_items, list) else []):
            if not isinstance(raw, dict):
                continue
            journeys = raw.get("journeys") or []
            journey = journeys[0] if journeys and isinstance(journeys[0], dict) else {}
            raw_segments = journey.get("segments") or []
            segments = [
                segment
                for segment in raw_segments
                if isinstance(segment, dict)
                and _flight_datetime(segment.get("depDateTime"))
                and _flight_datetime(segment.get("arrDateTime"))
            ]
            if not segments:
                continue
            first, last = segments[0], segments[-1]
            departure_at = _flight_datetime(first.get("depDateTime"))
            arrival_at = _flight_datetime(last.get("arrDateTime"))
            if not departure_at or not arrival_at or arrival_at <= departure_at:
                continue
            # FlyAI can occasionally return a stale cached journey for a
            # different route.  Do not expose it as a valid option; the route
            # planner will then use the next candidate or its normal fallback.
            departure_city = first.get("depCityName")
            arrival_city = last.get("arrCityName")
            if (
                departure_city
                and not _place_in_text(request.origin, departure_city)
            ) or (
                arrival_city
                and not _place_in_text(request.destination, arrival_city)
            ):
                continue
            duration = _duration_minutes(
                journey.get("totalDuration")
                or first.get("duration")
                or raw.get("totalDuration")
            )
            if duration is None:
                duration = max(1, round((arrival_at - departure_at).total_seconds() / 60))
            flight_numbers = _segment_numbers(segments, "flightNo", "flightNumber")
            items.append(
                {
                    "id": raw.get("id") or f"flight_{index}",
                    "journey_type": journey.get("journeyType"),
                    "segments": segments,
                    "departure_at": departure_at.isoformat(),
                    "arrival_at": arrival_at.isoformat(),
                    "duration_minutes": duration,
                    "flight_number": " / ".join(flight_numbers) if flight_numbers else None,
                    "service_number": " / ".join(flight_numbers) if flight_numbers else None,
                    "carrier": first.get("marketingTransportName") or first.get("transportType"),
                    "operator": first.get("marketingTransportName") or first.get("operator"),
                    "departure_city": departure_city,
                    "arrival_city": arrival_city,
                    "departure_airport": first.get("depStationName"),
                    "arrival_airport": last.get("arrStationName"),
                    "service_status": "confirmed" if flight_numbers else "unavailable",
                    "seat_class": first.get("seatClassName"),
                    "price": raw.get("adultPrice") or raw.get("ticketPrice") or raw.get("price"),
                    "detail_url": raw.get("jumpUrl"),
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 未返回可用航班"],
                error_code="FLYAI_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={"items": items, "count": len(items)},
            sources=[
                SourceRecord(
                    provider="FlyAI / 飞猪",
                    title="航班实时搜索",
                    url="https://www.fliggy.com/",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if shutil.which("flyai") else "degraded",
            "configured": bool(shutil.which("flyai")),
            "api_key_configured": bool(os.getenv("FLYAI_API_KEY")),
        }


class FlyAIFerryAdapter(SkillAdapter):
    """Discover ferry/ship options when a water crossing is required.

    FlyAI currently exposes cruise/ship discovery through semantic search rather
    than a structured timetable command.  We preserve the provider result and
    mark the generated departure window as estimated, so the planner can keep a
    connected itinerary without pretending that a free-form result is a ticket.
    """

    name = "flyai.ferry"
    version = "1.0.0"
    category = "travel_search"
    timeout_seconds = 25
    max_retries = 0
    cache_ttl_seconds = 15 * 60

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlyAIFerryInput.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        command = shutil.which("flyai")
        if not command:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["运行环境未安装 flyai CLI"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = FlyAIFerryInput.model_validate(payload)
        query = request.query or (
            f"{request.dep_date.isoformat()} {request.origin} 到 {request.destination} "
            "轮渡 渡船 船票 班次"
        )
        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *_flyai_spawn_args(command, "keyword-search", "--query", query),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_flyai_process_env(),
        )
        stdout, stderr = await process.communicate()
        text = stdout.decode("utf-8", errors="replace").strip()
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=[
                    "FlyAI 轮船搜索未返回可解析的 JSON",
                    stderr.decode("utf-8", errors="replace")[:160],
                ],
                error_code="FLYAI_INVALID_RESPONSE",
            )
        raw_items = (body.get("data") or {}).get("itemList", [])
        items: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_items if isinstance(raw_items, list) else []):
            info = raw.get("info") if isinstance(raw, dict) and isinstance(raw.get("info"), dict) else raw
            if not isinstance(info, dict):
                continue
            title = str(info.get("title") or info.get("name") or "").strip()
            if not title:
                continue
            snippet = str(
                info.get("description")
                or info.get("summary")
                or info.get("tags")
                or ""
            ).strip()
            combined = f"{title} {snippet}".casefold()
            ferry_tokens = ("轮渡", "渡轮", "船票", "客运", "码头", "邮轮", "游船", "渡船", "船班", "开船", "ferry", "cruise")
            if not any(token in combined for token in ferry_tokens):
                continue
            # A semantic search for a ferry route often also returns local
            # sightseeing cruises or generic hotel/attraction cards.  A
            # cross-city candidate must mention both endpoints in its title or
            # snippet; otherwise it is not safe to turn it into a timetable.
            if not (
                _place_in_text(request.origin, combined)
                and _place_in_text(request.destination, combined)
            ):
                continue
            # Scenic-ticket packages are not a transport schedule.  Keep a
            # package only when it also names a real port/operator signal.
            if "门票" in combined and not any(token in combined for token in ("码头", "客运", "轮渡", "渡轮", "船班")):
                continue
            time_matches = re.findall(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", f"{title} {snippet}")
            departure = datetime.combine(request.dep_date, datetime.min.time())
            departure = departure.replace(hour=8, minute=0)
            duration = 120
            if time_matches:
                hour, minute = map(int, time_matches[0])
                departure = departure.replace(hour=hour, minute=minute)
            duration_match = re.search(
                r"(\d+(?:\.\d+)?)\s*(?:小时|小時|hour|hr|h|分钟|分鐘|minute|min|m)",
                f"{title} {snippet}",
                re.IGNORECASE,
            )
            if duration_match:
                duration = _duration_minutes(duration_match.group(0)) or duration
            items.append(
                {
                    "id": info.get("id") or f"ferry_{index}",
                    "departure_at": departure.isoformat(),
                    "arrival_at": (departure + timedelta(minutes=duration)).isoformat(),
                    "duration_minutes": duration,
                    "ship_name": title,
                    "departure_port": request.origin,
                    "arrival_port": request.destination,
                    "detail_url": info.get("jumpUrl") or info.get("detailUrl") or info.get("url"),
                    "snippet": snippet[:500],
                    "estimated": True,
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 未返回可用轮船/渡轮候选"],
                error_code="FLYAI_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={"items": items, "count": len(items), "estimated_schedule": True},
            warnings=["轮船班次来自语义检索，出发前需以船公司实时班次确认"],
            sources=[
                SourceRecord(
                    provider="FlyAI / 飞猪",
                    title="轮船/渡轮语义检索",
                    url="https://www.fliggy.com/",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if shutil.which("flyai") else "degraded",
            "configured": bool(shutil.which("flyai")),
            "api_key_configured": bool(os.getenv("FLYAI_API_KEY")),
        }


class FlyAIHotelAdapter(SkillAdapter):
    name = "flyai.hotel"
    version = "1.1.0"
    category = "travel_search"
    timeout_seconds = 25
    max_retries = 0
    cache_ttl_seconds = 30 * 60

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlyAIHotelInput.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        command = shutil.which("flyai")
        if not command:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["运行环境未安装 flyai CLI"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = FlyAIHotelInput.model_validate(payload)
        arguments = [
            command,
            "search-hotel",
            "--dest-name",
            request.destination,
            "--check-in-date",
            request.check_in_date.isoformat(),
            "--check-out-date",
            request.check_out_date.isoformat(),
            "--sort",
            request.sort,
        ]
        if request.poi_name:
            arguments.extend(["--poi-name", request.poi_name])
        if request.max_price:
            arguments.extend(["--max-price", str(request.max_price)])
        arguments = _flyai_spawn_args(command, *arguments[1:])

        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_flyai_process_env(),
        )
        stdout, _ = await process.communicate()
        text = stdout.decode("utf-8", errors="replace").strip()
        # Windows CLI may exit non-zero after already returning a valid JSON payload.
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 未返回可解析的 JSON"],
                error_code="FLYAI_INVALID_RESPONSE",
            )
        raw_items = body.get("data", {}).get("itemList", [])
        items = []
        for item in raw_items:
            try:
                longitude = float(item["longitude"])
                latitude = float(item["latitude"])
            except (KeyError, TypeError, ValueError):
                continue
            price = _parse_price(item.get("price"))
            items.append(
                {
                    "id": item.get("shId"),
                    "name": item.get("name"),
                    "address": item.get("address"),
                    "location": f"{longitude},{latitude}",
                    "longitude": longitude,
                    "latitude": latitude,
                    "price_min_cny": price[0] if price else None,
                    "price_max_cny": price[1] if price else None,
                    "price_estimated": price[2] if price else None,
                    "rating": _float_or_none(item.get("rate")),
                    "star": item.get("star"),
                    "nearby": item.get("interestsPoi"),
                    "detail_url": item.get("detailUrl"),
                    "image_url": item.get("mainPic"),
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 未返回可用酒店"],
                error_code="FLYAI_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={"items": items, "count": len(items)},
            sources=[
                SourceRecord(
                    provider="FlyAI / 飞猪",
                    title="酒店实时搜索",
                    url="https://www.fliggy.com/",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if shutil.which("flyai") else "degraded",
            "configured": bool(shutil.which("flyai")),
        }


class FlyAIPoiAdapter(SkillAdapter):
    name = "flyai.poi"
    version = "1.0.0"
    category = "travel_search"
    timeout_seconds = 25
    max_retries = 0
    cache_ttl_seconds = 30 * 60

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlyAIPoiInput.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        command = shutil.which("flyai")
        if not command:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["运行环境未安装 flyai CLI"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = FlyAIPoiInput.model_validate(payload)
        arguments = [command, "search-poi", "--city-name", request.city_name]
        if request.keyword:
            arguments.extend(["--keyword", request.keyword])
        # The CLI rejects broad labels such as “景点”. Keep keyword search usable
        # and only forward category values accepted by the upstream command.
        if request.category in FLYAI_POI_CATEGORIES:
            arguments.extend(["--category", request.category])
        if request.poi_level:
            arguments.extend(["--poi-level", str(request.poi_level)])
        arguments = _flyai_spawn_args(command, *arguments[1:])
        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_flyai_process_env(),
        )
        stdout, _ = await process.communicate()
        try:
            body = json.loads(stdout.decode("utf-8", errors="replace").strip())
        except json.JSONDecodeError:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 景点搜索未返回可解析的 JSON"],
                error_code="FLYAI_INVALID_RESPONSE",
            )
        items = []
        for item in body.get("data", {}).get("itemList", []):
            ticket = item.get("ticketInfo") or {}
            price = _parse_price(ticket.get("price"))
            if not item.get("name"):
                continue
            raw_location = item.get("location") or item.get("lnglat") or ""
            longitude = latitude = None
            if isinstance(raw_location, str) and "," in raw_location:
                try:
                    longitude, latitude = (float(value) for value in raw_location.split(",", 1))
                except (TypeError, ValueError):
                    longitude = latitude = None
            if longitude is None and item.get("longitude") is not None and item.get("latitude") is not None:
                try:
                    longitude, latitude = float(item["longitude"]), float(item["latitude"])
                except (TypeError, ValueError):
                    longitude = latitude = None
            items.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "address": item.get("address"),
                    "image_url": item.get("mainPic"),
                    "detail_url": item.get("jumpUrl"),
                    "free_status": item.get("freePoiStatus"),
                    "ticket_name": ticket.get("ticketName"),
                    "ticket_date": ticket.get("priceDate"),
                    "price_min_cny": price[0] if price else None,
                    "price_max_cny": price[1] if price else None,
                    "price_estimated": price[2] if price else None,
                    "location": raw_location or None,
                    "longitude": longitude,
                    "latitude": latitude,
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 未返回可用景点或门票"],
                error_code="FLYAI_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={"items": items, "count": len(items)},
            sources=[
                SourceRecord(
                    provider="FlyAI / 飞猪",
                    title="景点与门票搜索",
                    url="https://www.fliggy.com/",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if shutil.which("flyai") else "degraded",
            "configured": bool(shutil.which("flyai")),
        }


class _FlyAISearchAdapter(SkillAdapter):
    """Shared CLI adapter for FlyAI broad and semantic destination search."""

    command_name: str

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return FlyAISearchInput.model_validate(payload).model_dump(mode="json")

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        command = shutil.which("flyai")
        if not command:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["运行环境未安装 flyai CLI"],
                error_code="SKILL_NOT_CONFIGURED",
            )
        request = FlyAISearchInput.model_validate(payload)
        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *_flyai_spawn_args(command, self.command_name, "--query", request.query),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_flyai_process_env(),
        )
        stdout, stderr = await process.communicate()
        raw_text = stdout.decode("utf-8", errors="replace").strip()
        try:
            body = json.loads(raw_text)
        except json.JSONDecodeError:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 搜索未返回可解析 JSON", stderr.decode("utf-8", errors="replace")[:160]],
                error_code="FLYAI_INVALID_RESPONSE",
            )
        raw_data = body.get("data")
        # ``keyword-search`` returns a structured itemList, while the
        # semantic command returns a provider-authored Markdown answer in the
        # same ``data`` field.  Treat both as valid results instead of
        # assuming ``data`` is always a mapping (the old assumption raised an
        # AttributeError and surfaced as SKILL_EXECUTION_FAILED).
        if isinstance(raw_data, str):
            content = raw_data.strip()
            if not content:
                return SkillResult(
                    success=False,
                    provider="FlyAI / 飞猪",
                    warnings=["FlyAI 语义搜索返回空内容"],
                    error_code="FLYAI_NO_RESULTS",
                )
            return SkillResult(
                success=True,
                provider="FlyAI / 飞猪",
                data={"query": request.query, "items": [], "content": content[:12000]},
                sources=[
                    SourceRecord(
                        provider="FlyAI / 飞猪",
                        title="目的地语义搜索结果",
                        url="https://flyai.open.fliggy.com/",
                    )
                ],
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        raw_items = (raw_data or {}).get("itemList", []) if isinstance(raw_data, dict) else []
        items: list[dict[str, Any]] = []
        for raw in raw_items if isinstance(raw_items, list) else []:
            item = raw.get("info") if isinstance(raw, dict) and isinstance(raw.get("info"), dict) else raw
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title[:240],
                    "snippet": str(item.get("description") or item.get("summary") or item.get("tags") or "").strip()[:500],
                    "detail_url": item.get("jumpUrl") or item.get("detailUrl") or item.get("url"),
                    "image_url": item.get("picUrl") or item.get("mainPic") or item.get("imageUrl"),
                    "rating": _float_or_none(item.get("rate") or item.get("rating")),
                }
            )
        if not items:
            return SkillResult(
                success=False,
                provider="FlyAI / 飞猪",
                warnings=["FlyAI 搜索没有返回可用候选"],
                error_code="FLYAI_NO_RESULTS",
            )
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={"query": request.query, "items": items, "count": len(items)},
            sources=[
                SourceRecord(
                    provider="FlyAI / 飞猪",
                    title=f"FlyAI {self.command_name} 目的地搜索",
                    url="https://flyai.open.fliggy.com/",
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready" if shutil.which("flyai") else "degraded",
            "configured": bool(shutil.which("flyai")),
            "command": self.command_name,
            "api_key_configured": bool(os.getenv("FLYAI_API_KEY")),
        }


class FlyAIKeywordSearchAdapter(_FlyAISearchAdapter):
    name = "flyai.keyword_search"
    version = "1.0.0"
    category = "travel_search"
    command_name = "keyword-search"
    timeout_seconds = 25
    max_retries = 0
    cache_ttl_seconds = 30 * 60


class FlyAISemanticSearchAdapter(_FlyAISearchAdapter):
    name = "flyai.ai_search"
    version = "1.0.0"
    category = "travel_search"
    command_name = "ai-search"
    # Semantic search may compose several travel-index queries before the
    # CLI returns its structured answer.  A 20-second ceiling caused valid
    # requests to be cancelled while the provider was still working (the
    # regular keyword/POI searches remain on their shorter budget).
    timeout_seconds = 60
    max_retries = 0
    cache_ttl_seconds = 30 * 60


def _float_or_none(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _parse_price(value: object) -> tuple[float, float, bool] | None:
    text = str(value or "").strip().lower()
    masked = re.search(r"(\d+)(x+)", text)
    if masked:
        magnitude = 10 ** len(masked.group(2))
        minimum = int(masked.group(1)) * magnitude
        return float(minimum), float(minimum + magnitude - 1), True
    exact = re.search(r"(\d+(?:\.\d+)?)", text)
    if exact:
        amount = float(exact.group(1))
        return amount, amount, False
    return None
