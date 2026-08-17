"""Best-effort web enrichment for tourism candidates.

The planner keeps AMap/FlyAI/OpenTripMap as the authoritative location
sources.  This module only adds human-friendly copy, a canonical Baidu Baike
link and an openly published preview image when the page exposes one.  A
timeout or a blocked page never fails the itinerary.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import quote

import httpx


_PROVIDER_BOILERPLATE = (
    "在线地图官方网站，提供全国地图浏览，地点搜索，公交驾车查询服务。可同时查看商家团购、优惠信息。在线地图，您的出行、生活好帮手。",
    "飞猪AI开放平台（旅行信息服务）是飞猪旅行的AI能力开放平台，为开发者提供酒店预订、机票搜索、门票API、度假套餐等全品类旅行AI服务，支持OpenClaw协议实时接入飞猪官方商品库。",
)


def _clean_public_description(value: str | None) -> str:
    text = str(value or "").strip()
    for boilerplate in _PROVIDER_BOILERPLATE:
        text = text.replace(boilerplate, "")
    # A few pages append provider attribution after punctuation/line breaks.
    text = re.sub(r"(?:在线地图|飞猪AI开放平台|OpenClaw协议)[^。！？]*[。！？]?", "", text)
    return re.sub(r"\s{2,}", " ", text).strip(" \t\r\n，,；;")


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
    url = f"https://baike.baidu.com/item/{quote(name)}"
    try:
        response = await client.get(url, headers={"User-Agent": "RoadMan/1.0 POI research"})
        if response.status_code >= 400:
            return None
        meta = parse_web_meta(response.text, url=str(response.url))
    except (httpx.HTTPError, UnicodeError, ValueError):
        return None
    if not meta:
        return None
    return {"candidate": candidate, "meta": meta, "url": url}


async def enrich_tourism_candidates(
    candidates: dict[str, list[dict[str, Any]]],
    *,
    max_attractions: int = 6,
    max_other: int = 2,
    timeout_seconds: float = 1.8,
) -> dict[str, list[dict[str, Any]]]:
    targets: list[dict[str, Any]] = []
    for category, items in candidates.items():
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
        meta = result["meta"]
        # The card should open the human-readable encyclopedia page while the
        # original provider links remain available in source_records.
        candidate["detail_url"] = result["url"]
        if meta.get("description"):
            cleaned = _clean_public_description(meta["description"])
            if cleaned:
                candidate["description"] = cleaned[:320]
                candidate["information_summary"] = cleaned[:240]
                candidate.update(_extract_public_facts(cleaned))
        if meta.get("image_url") and not candidate.get("image_url"):
            candidate["image_url"] = meta["image_url"]
        records = candidate.setdefault("source_records", [])
        if not any(item.get("provider") == "百度百科" for item in records):
            records.append({
                "provider": "百度百科",
                "title": f"{(candidate.get('place') or {}).get('name', '')} 百科资料",
                "url": result["url"],
            })
    return candidates


async def enrich_scheduled_activities(
    day_plans: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
    *,
    timeout_seconds: float = 1.8,
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
    for activity, candidate in matches:
        for key in ("description", "information_summary", "image_url", "detail_url", "ticket_or_price", "parking_or_price", "parking_note", "opening_hours", "reservation_status", "reservation_note"):
            if candidate.get(key):
                activity[key] = candidate[key]
        if candidate.get("source_records"):
            activity["source_records"] = candidate["source_records"]
    return day_plans, candidates
