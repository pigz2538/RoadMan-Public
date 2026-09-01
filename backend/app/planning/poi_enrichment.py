"""Best-effort public-web enrichment for tourism candidates.

AMap/FlyAI/OpenTripMap remain the authoritative location and ticket sources.
This module adds human-friendly copy, images and traceable public-web facts.
It deliberately searches broadly (DuckDuckGo's public instant-answer index,
official pages and provider detail pages) and keeps Baidu Baike only as a
last-resort fallback. A timeout or blocked page never fails the itinerary.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from ..skills.base import SkillContext


_PROVIDER_BOILERPLATE = (
    "在线地图官方网站，提供全国地图浏览，地点搜索，公交驾车查询服务。可同时查看商家团购、优惠信息。在线地图，您的出行、生活好帮手。",
    "飞猪AI开放平台（旅行信息服务）是飞猪旅行的AI能力开放平台，为开发者提供酒店预订、机票搜索、门票API、度假套餐等全品类旅行AI服务，支持OpenClaw协议实时接入飞猪官方商品库。",
)

# Providers sometimes prepend a slightly different attribution sentence
# (notably “高德地图官方网站” instead of “在线地图官方网站”).  Treat the
# whole sentence as transport metadata rather than POI copy.  The expressions
# use Unicode escapes so this filter stays stable even when a Windows console
# opens the source file with a legacy code page.
_PROVIDER_BOILERPLATE_SENTENCE_RE = re.compile(
    r"(?:\u9ad8\u5fb7|\u5728\u7ebf)\u5730\u56fe\u5b98\u65b9\u7f51\u7ad9[^\u3002\uff01\uff1f]*[\u3002\uff01\uff1f]?"
    r"|(?:\u9ad8\u5fb7|\u5728\u7ebf)\u5730\u56fe\uff0c[^\u3002\uff01\uff1f]*[\u3002\uff01\uff1f]?"
    r"|\u53ef\u540c\u65f6\u67e5\u770b\u5546\u5bb6\u56e2\u8d2d\u3001\u4f18\u60e0\u4fe1\u606f[^\u3002\uff01\uff1f]*[\u3002\uff01\uff1f]?"
    r"|\u98de\u732aAI\u5f00\u653e\u5e73\u53f0[^\u3002\uff01\uff1f]*[\u3002\uff01\uff1f]?"
    r"|OpenClaw\u534f\u8bae[^\u3002\uff01\uff1f]*[\u3002\uff01\uff1f]?",
    re.IGNORECASE,
)


def _clean_public_description(value: str | None) -> str:
    text = str(value or "").strip()
    for boilerplate in _PROVIDER_BOILERPLATE:
        text = text.replace(boilerplate, "")
    text = _PROVIDER_BOILERPLATE_SENTENCE_RE.sub("", text)
    # A few pages append provider attribution after punctuation/line breaks.
    text = re.sub(r"(?:在线地图|飞猪AI开放平台|OpenClaw协议)[^。！？]*[。！？]?", "", text)
    return re.sub(r"\s{2,}", " ", text).strip(" \t\r\n，,；;")


def _sanitize_candidate_copy(candidate: dict[str, Any]) -> None:
    """Remove provider boilerplate from every user-visible POI field.

    Provider detail responses can carry a generic platform introduction in
    ``description``.  It is not evidence about the place and used to leak
    into saved activity cards when web enrichment timed out.  Clean both the
    provider snapshot and the final activity projection.
    """
    for key, limit in (("description", 320), ("information_summary", 240)):
        raw = candidate.get(key)
        if raw in (None, ""):
            continue
        cleaned = _clean_public_description(str(raw))
        if cleaned:
            candidate[key] = cleaned[:limit]
        else:
            candidate.pop(key, None)


def _extract_public_facts(description: str | None) -> dict[str, Any]:
    text = _clean_public_description(description)
    facts: dict[str, Any] = {}
    if not text:
        return facts
    hours = re.search(r"((?:每日|周一至周日|周[一二三四五六日天])[^。；;]{0,50}(?:\d{1,2}:\d{2}|开放|营业)[^。；;]*)", text)
    if hours:
        facts["opening_hours"] = {"text": hours.group(1)[:180], "confirmed": False}
    reservation = re.search(r"(预约|预订|实名|门票|购票)[^。；;]{0,80}", text)
    if reservation:
        facts["reservation_status"] = "recommended" if re.search(r"预约|预订|实名", text) else "unknown"
        facts["reservation_note"] = f"公开页面信息：{reservation.group(1)[:140]}，请以官方公告为准。"
    parking = re.search(r"(停车|停车场|停车费|收费)[^。；;]{0,80}", text)
    if parking:
        facts["parking_note"] = f"公开页面停车信息：{parking.group(1)[:140]}"
    def money_range(amount: float) -> dict[str, Any]:
        return {
            "minimum": amount,
            "maximum": amount,
            "currency": "CNY",
            "estimated": True,
        }

    ticket = re.search(r"门票[^。；;]{0,30}?(\d+(?:\.\d+)?)\s*元", text)
    if ticket:
        try:
            facts["ticket_or_price"] = money_range(float(ticket.group(1)))
        except ValueError:
            pass
    parking_price = re.search(r"停车(?:费|场)?[^。；;]{0,30}?(\d+(?:\.\d+)?)\s*元", text)
    if parking_price:
        try:
            facts["parking_or_price"] = money_range(float(parking_price.group(1)))
        except ValueError:
            pass
    return facts


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = values.get("property") or values.get("name")
            content = values.get("content")
            if key and content:
                self.meta[key.lower()] = content.strip()
        elif tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title_parts.append(data.strip())


def parse_web_meta(html: str, *, url: str) -> dict[str, str]:
    parser = _MetaParser()
    parser.feed(html[:1_000_000])
    description = parser.meta.get("og:description") or parser.meta.get("description")
    image_url = parser.meta.get("og:image") or parser.meta.get("twitter:image")
    title = parser.meta.get("og:title") or " ".join(parser.title_parts).strip()
    return {
        key: value
        for key, value in {
            "title": title,
            "description": description or "",
            "image_url": image_url or "",
            "detail_url": parser.meta.get("og:url") or url,
        }.items()
        if value
    }


async def _fetch_candidate(client: httpx.AsyncClient, candidate: dict[str, Any]) -> dict[str, Any] | None:
    name = str((candidate.get("place") or {}).get("name") or "").strip()
    if not name:
        return None
    city = str((candidate.get("place") or {}).get("city") or "").strip()
    query = f"{city} {name}".strip()
    # Public search evidence is preferred over a single encyclopedia. The
    # endpoint is intentionally optional: if it is unavailable, continue with
    # ordinary page metadata below.
    try:
        answer = await client.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers={"User-Agent": "RoadMan/1.0 public-web-research"},
        )
        if answer.status_code < 400:
            payload = answer.json()
            abstract = str(payload.get("AbstractText") or "").strip()
            abstract_url = str(payload.get("AbstractURL") or "").strip()
            image_url = str(payload.get("Image") or "").strip()
            if abstract or abstract_url:
                return {
                    "candidate": candidate,
                    "meta": {
                        "title": str(payload.get("Heading") or name),
                        "description": abstract,
                        "image_url": image_url,
                        "detail_url": abstract_url or f"https://duckduckgo.com/?q={quote(query)}",
                    },
                    "url": abstract_url or f"https://duckduckgo.com/?q={quote(query)}",
                    "provider": "公开网页搜索",
                }
    except (httpx.HTTPError, ValueError, TypeError):
        pass

    urls: list[tuple[str, str]] = []
    for key in ("official_url", "detail_url"):
        value = str(candidate.get(key) or "").strip()
        if value.startswith("http"):
            urls.append((value, "公开网页"))
    urls.extend(
        [
            (f"https://www.amap.com/search?query={quote(name)}", "地图详情页"),
            (f"https://baike.baidu.com/item/{quote(name)}", "百科资料"),
        ]
    )
    for url, provider in urls:
        try:
            response = await client.get(url, headers={"User-Agent": "RoadMan/1.0 public-web-research"})
            if response.status_code >= 400:
                continue
            meta = parse_web_meta(response.text, url=str(response.url))
        except (httpx.HTTPError, UnicodeError, ValueError):
            continue
        if meta:
            return {"candidate": candidate, "meta": meta, "url": str(response.url), "provider": provider}
    return None


async def enrich_tourism_candidates(
    candidates: dict[str, list[dict[str, Any]]],
    *,
    max_attractions: int = 6,
    max_other: int = 2,
    timeout_seconds: float = 1.8,
) -> dict[str, list[dict[str, Any]]]:
    targets: list[dict[str, Any]] = []
    for category, items in candidates.items():
        for candidate in items:
            if isinstance(candidate, dict):
                _sanitize_candidate_copy(candidate)
        limit = max_attractions if category == "attractions" else max_other
        targets.extend(items[:limit])
    if not targets:
        return candidates
    timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        results = await asyncio.gather(
            *[_fetch_candidate(client, candidate) for candidate in targets],
            return_exceptions=True,
        )
    for result in results:
        if not isinstance(result, dict):
            continue
        candidate = result["candidate"]
        _sanitize_candidate_copy(candidate)
        meta = result["meta"]
        # The card opens the best public-web page found while the original
        # provider links remain available in source_records.
        candidate["detail_url"] = result["url"]
        if meta.get("description"):
            cleaned = _clean_public_description(meta["description"])
            if cleaned:
                candidate["description"] = cleaned[:320]
                candidate["information_summary"] = cleaned[:240]
                candidate.update(_extract_public_facts(cleaned))
        _sanitize_candidate_copy(candidate)
        if meta.get("image_url") and not candidate.get("image_url"):
            candidate["image_url"] = meta["image_url"]
        records = candidate.setdefault("source_records", [])
        provider = result.get("provider") or "公开网页"
        if not any(item.get("provider") == provider for item in records):
            records.append({
                "provider": provider,
                "title": f"{(candidate.get('place') or {}).get('name', '')} 公开资料",
                "url": result["url"],
            })
    return candidates


def _numeric_price(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"(\d+(?:\.\d+)?)", str(value))
    return float(match.group(1)) if match else None


def _price_range(value: Any) -> tuple[float, float] | None:
    """Read a conservative CNY range from provider display text."""
    if value in (None, ""):
        return None
    values = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", str(value))]
    if not values:
        return None
    return min(values), max(values)


def _append_sources(candidate: dict[str, Any], records: list[Any]) -> None:
    existing = candidate.setdefault("source_records", [])
    seen = {(item.get("provider"), item.get("url")) for item in existing if isinstance(item, dict)}
    for record in records:
        data = record.model_dump(mode="json") if hasattr(record, "model_dump") else record
        if not isinstance(data, dict):
            continue
        key = (data.get("provider"), data.get("url"))
        if key in seen:
            continue
        existing.append(data)
        seen.add(key)


def _merge_structured_facts(candidate: dict[str, Any], item: dict[str, Any], *, provider: str) -> None:
    """Merge provider facts while retaining an explicit unknown state."""
    if item.get("name") and not candidate.get("place", {}).get("name"):
        candidate.setdefault("place", {})["name"] = item["name"]
    opening = item.get("opening_hours_text") or item.get("openingHours")
    if opening:
        candidate["opening_hours"] = {
            "text": str(opening)[:240],
            "confirmed": provider == "高德地图",
            "source_count": max(1, int((candidate.get("opening_hours") or {}).get("source_count") or 0) + 1),
            "as_of": datetime.now(timezone.utc).date().isoformat(),
        }
    ticket_name = item.get("ticket_name") or item.get("ticketName")
    free_status = str(item.get("free_status") or item.get("freePoiStatus") or "").lower()
    price_text = item.get("price_text") or item.get("price") or item.get("price_min_cny")
    price = _price_range(price_text)
    if ticket_name:
        candidate["ticket_name"] = str(ticket_name)
    if price is not None:
        candidate["ticket_or_price"] = {
            "currency": "CNY",
            "minimum": price[0],
            "maximum": _price_range(item.get("price_max_cny"))[1] if _price_range(item.get("price_max_cny")) else price[1],
            "estimated": provider != "高德地图",
            "source_count": 1,
        }
        candidate["ticket_status"] = "known"
    elif "free" in free_status or free_status in {"0", "false"}:
        candidate["ticket_status"] = "free"
    elif ticket_name:
        candidate["ticket_status"] = "known"
    parking = item.get("parking_text") or item.get("parking_note")
    if parking:
        candidate["parking_note"] = str(parking)[:240]
        if re.search(r"(?:元|¥|￥)", str(parking)):
            parking_price = _price_range(parking)
            if parking_price:
                candidate["parking_or_price"] = {
                    "currency": "CNY",
                    "minimum": parking_price[0],
                    "maximum": parking_price[1],
                    "estimated": provider != "高德地图",
                    "source_count": 1,
                }
    ticket_ordering = item.get("ticket_ordering") or item.get("ticketOrdering")
    if ticket_ordering:
        ordering_text = str(ticket_ordering)[:240]
        candidate["reservation_status"] = (
            "recommended"
            if re.search(r"预约|预订|实名|购票", ordering_text)
            else "unknown"
        )
        candidate["reservation_note"] = f"门票信息：{ordering_text}；请以官方公告为准"
        if re.match(r"https?://", ordering_text):
            candidate["booking_url"] = ordering_text
    website = item.get("website") or item.get("official_url")
    if website and re.match(r"https?://", str(website)):
        candidate["official_url"] = str(website)
    if item.get("rating") and not candidate.get("information_summary"):
        candidate["information_summary"] = f"公开评分：{item['rating']}"
    image = (item.get("photos") or [None])[0] if isinstance(item.get("photos"), list) else item.get("image_url")
    if image and not candidate.get("image_url"):
        candidate["image_url"] = image
    if item.get("detail_url") and not candidate.get("detail_url"):
        candidate["detail_url"] = item["detail_url"]


async def _enrich_with_provider_skills(
    matches: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    registry: Any,
    trip_id: str | None,
    timeout_seconds: float,
) -> None:
    """Fetch exact map/ticket facts for scheduled items.

    A failed provider is intentionally non-blocking: the activity keeps an
    explicit ``partial``/``unavailable`` status instead of receiving invented
    opening hours or ticket prices.
    """
    if registry is None:
        return
    semaphore = asyncio.Semaphore(6)

    async def enrich_one(activity: dict[str, Any], candidate: dict[str, Any]) -> None:
        async with semaphore:
            _sanitize_candidate_copy(candidate)
            place = candidate.get("place") or activity.get("place") or {}
            name = str(place.get("name") or "").strip()
            city = str(place.get("city") or "").strip()
            amap_id = candidate.get("amap_source_id")
            if not amap_id:
                has_map_source = any(
                    isinstance(record, dict) and "高德" in str(record.get("provider") or "")
                    for record in candidate.get("source_records", [])
                )
                if has_map_source:
                    amap_id = place.get("source_id") or place.get("id")
            if not amap_id and name and "amap.poi" in registry.names():
                try:
                    lookup = await asyncio.wait_for(
                        registry.execute(
                            "amap.poi",
                            {"keywords": name, "city": city or None, "page_size": 5},
                            SkillContext(trip_id=trip_id, metadata={"exact_name": name}),
                        ),
                        timeout=timeout_seconds + 1,
                    )
                    if lookup.success and isinstance(lookup.data, dict):
                        normalized = "".join(name.split()).casefold()
                        match = next(
                            (
                                item for item in lookup.data.get("items", [])
                                if "".join(str(item.get("name") or "").split()).casefold() == normalized
                            ),
                            None,
                        )
                        if isinstance(match, dict) and match.get("id"):
                            amap_id = match["id"]
                            place["source_id"] = amap_id
                            _append_sources(candidate, lookup.sources)
                except Exception:
                    pass
            if amap_id and "amap.poi_detail" in registry.names():
                try:
                    result = await asyncio.wait_for(
                        registry.execute("amap.poi_detail", {"poi_id": str(amap_id)}, SkillContext(trip_id=trip_id)),
                        timeout=timeout_seconds + 1,
                    )
                    if result.success and isinstance(result.data, dict):
                        item = result.data.get("item") or {}
                        _merge_structured_facts(candidate, item, provider="高德地图")
                        _sanitize_candidate_copy(candidate)
                        _append_sources(candidate, result.sources)
                except Exception:
                    pass
            # Exact ticket/attraction lookup supplies admission details and a
            # travel-platform URL; meals/hotels already have dedicated pools.
            if activity.get("type") == "attraction" and name and "flyai.poi" in registry.names():
                try:
                    result = await asyncio.wait_for(
                        registry.execute(
                            "flyai.poi",
                            {"city_name": city or name, "keyword": name},
                            SkillContext(trip_id=trip_id, metadata={"exact_name": name}),
                        ),
                        timeout=timeout_seconds + 1,
                    )
                    if result.success and isinstance(result.data, dict):
                        normalized = "".join(name.split()).casefold()
                        item = next(
                            (
                                item for item in result.data.get("items", [])
                                if "".join(str(item.get("name") or "").split()).casefold() == normalized
                            ),
                            None,
                        ) or (result.data.get("items") or [None])[0]
                        if isinstance(item, dict):
                            _merge_structured_facts(candidate, item, provider="旅行服务")
                            _sanitize_candidate_copy(candidate)
                            _append_sources(candidate, result.sources)
                            if item.get("detail_url"):
                                _append_sources(candidate, [{
                                    "provider": "旅行服务",
                                    "title": f"{name} 门票与预约",
                                    "url": item["detail_url"],
                                    "source_type": "ticketing",
                                    "confidence": "medium",
                                }])
                except Exception:
                    pass
            providers = {
                str(item.get("provider"))
                for item in candidate.get("source_records", [])
                if isinstance(item, dict) and item.get("provider")
            }
            candidate["information_sources_count"] = len(providers)
            has_opening = bool((candidate.get("opening_hours") or {}).get("text"))
            has_ticket = candidate.get("ticket_status") in {"known", "free"}
            candidate["information_status"] = (
                "complete" if len(providers) >= 2 and (has_opening or has_ticket)
                else "partial" if providers else "unavailable"
            )
            candidate["information_checked_at"] = datetime.now(timezone.utc).isoformat()

    await asyncio.gather(*(enrich_one(activity, candidate) for activity, candidate in matches))


async def enrich_scheduled_activities(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
    *,
    timeout_seconds: float = 1.8,
    registry: Any = None,
    trip_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Search every scheduled POI, including items absent from the pool.

    The planner may create a required or user-added place directly.  Matching
    only the recommendation pool used to leave those cards without a public
    description, image, ticket or parking hint.  Synthetic candidates let the
    same research agent enrich them without changing the itinerary choice.
    """
    category_by_type = {"attraction": "attractions", "meal": "meals", "hotel": "hotels"}
    synthetic: dict[str, list[dict[str, Any]]] = {"attractions": [], "meals": [], "hotels": []}
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for day in day_plans:
        for activity in day.get("activities", []):
            category = category_by_type.get(str(activity.get("type") or ""))
            if not category:
                continue
            place = activity.get("place") or {}
            name = str(place.get("name") or "").strip()
            if not name:
                continue
            candidate = next((item for item in candidates.get(category, []) if str((item.get("place") or {}).get("name") or "").strip() == name), None)
            if candidate is None:
                candidate = {"candidate_id": f"scheduled:{category}:{name}", "place": place}
                synthetic[category].append(candidate)
            matches.append((activity, candidate))
    if any(synthetic.values()):
        await enrich_tourism_candidates(synthetic, max_attractions=None, max_other=None, timeout_seconds=timeout_seconds)
        for category, items in synthetic.items():
            candidates.setdefault(category, []).extend(item for item in items if item.get("detail_url"))
    await _enrich_with_provider_skills(
        matches,
        registry=registry,
        trip_id=trip_id,
        timeout_seconds=timeout_seconds,
    )
    for activity, candidate in matches:
        _sanitize_candidate_copy(candidate)
        for key in (
            "description", "information_summary", "image_url", "detail_url", "ticket_or_price",
            "ticket_name", "ticket_status", "ticket_note", "parking_or_price", "parking_note",
            "opening_hours", "reservation_status", "reservation_note", "official_url", "booking_url",
            "information_status", "information_checked_at", "information_sources_count",
        ):
            if key in {"description", "information_summary"}:
                # Clean old activity snapshots too, including records created
                # before the provider-copy filter was introduced.
                cleaned = _clean_public_description(candidate.get(key))
                if cleaned:
                    activity[key] = cleaned[:320 if key == "description" else 240]
                else:
                    activity.pop(key, None)
            elif candidate.get(key):
                activity[key] = candidate[key]
        if candidate.get("source_records"):
            activity["source_records"] = candidate["source_records"]
    return day_plans, candidates
