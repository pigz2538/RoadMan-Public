"""Best-effort web enrichment for tourism candidates.

The planner keeps AMap/FlyAI/OpenTripMap as the authoritative location
sources.  This module only adds human-friendly copy, a canonical Baidu Baike
link and an openly published preview image when the page exposes one.  A
timeout or a blocked page never fails the itinerary.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx


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
            candidate["description"] = meta["description"][:320]
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
