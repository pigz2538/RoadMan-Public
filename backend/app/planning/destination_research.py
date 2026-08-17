"""Evidence-first destination research for the planning graph.

The search layer deliberately does not choose an itinerary.  It gathers
source-backed local highlights from the web and FlyAI; the POI/ranking Agents
decide which returned candidates are credible enough to schedule.
"""

from __future__ import annotations

import asyncio
import html
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..skills.base import SkillContext
from ..skills.registry import SkillRegistry


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _url(value: str) -> str:
    parsed = urlparse(html.unescape(value))
    if parsed.path in {"/l/", ""}:
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return html.unescape(value)


async def _web_sources(destination: str) -> tuple[list[dict[str, Any]], list[str]]:
    queries = [
        f"{destination} 著名景点 地标 必去 景点分布 官方 推荐",
        f"{destination} 经典旅游路线 不同城区 必游景点",
        f"{destination} 必吃美食 老字号 地方特色 推荐",
    ]
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    async def search_one(client: httpx.AsyncClient, query: str) -> str | None:
        try:
            response = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
            response.raise_for_status()
            return response.text
        except (httpx.HTTPError, ValueError):
            return None

    try:
        async with httpx.AsyncClient(
            timeout=2.5,
            follow_redirects=True,
            headers={"User-Agent": "RoadMan/1.0 destination-research"},
        ) as client:
            bodies = await asyncio.gather(*(search_one(client, query) for query in queries))
            for query, body in zip(queries, bodies, strict=True):
                if not body:
                    continue
                links = re.findall(
                    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                    body,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                snippets = re.findall(
                    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
                    body,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                for index, (raw_url, title) in enumerate(links[:8]):
                    normalized_url = _url(raw_url)
                    if normalized_url in seen:
                        continue
                    seen.add(normalized_url)
                    sources.append(
                        {
                            "provider": "Web Destination Research",
                            "query": query,
                            "title": _clean(title)[:240],
                            "url": normalized_url,
                            "snippet": _clean(snippets[index])[:500] if index < len(snippets) else "",
                            "category_hint": "meals" if "美食" in query else "attractions",
                        }
                    )
    except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
        pass
    return sources[:30], queries


async def research_destination(
    registry: SkillRegistry,
    destination: str,
    trip_id: str,
) -> dict[str, Any]:
    """Search the public web and both FlyAI modes without blocking planning."""
    # A lightweight test/dry-run registry may intentionally omit external
    # search adapters. Do not make those runs wait on public-network timeouts;
    # the production registry always registers both commands and records their
    # real success/degradation in the audit trail.
    available_skills = set(registry.names())
    if not {"flyai.keyword_search", "flyai.ai_search"} & available_skills:
        return {
            "destination": destination,
            "status": "needs_review",
            "queries": [],
            "web_sources": [],
            "flyai_items": [],
            "sources": [],
            "providers": {
                "web": False,
                "flyai_keyword_search": False,
                "flyai_ai_search": False,
                "flyai_errors": ["SEARCH_ADAPTERS_UNAVAILABLE"],
            },
        }
    web_task = asyncio.create_task(_web_sources(destination))
    keyword_task = asyncio.create_task(
        registry.execute(
            "flyai.keyword_search",
            {
                "query": (
                    f"{destination} 著名必去景点 城市地标 不同片区 代表性美食"
                )
            },
            SkillContext(trip_id=trip_id, metadata={"purpose": "destination_research"}),
        )
    )
    semantic_task = asyncio.create_task(
        registry.execute(
            "flyai.ai_search",
            {
                "query": (
                    f"请为{destination}做目的地研究：列出不同城区的著名必去景点、"
                    "代表性地方美食，说明适合停留时长、游览顺序和来源，不要只推荐酒店附近"
                )
            },
            SkillContext(trip_id=trip_id, metadata={"purpose": "destination_research"}),
        )
    )
    (web_sources, queries), keyword_result, semantic_result = await asyncio.gather(
        web_task,
        keyword_task,
        semantic_task,
    )
    flyai_items: list[dict[str, Any]] = []
    flyai_sources: list[dict[str, Any]] = []
    semantic_text: list[str] = []
    for result in (keyword_result, semantic_result):
        if result.success and isinstance(result.data, dict):
            flyai_items.extend(result.data.get("items", []))
            flyai_sources.extend(item.model_dump(mode="json") for item in result.sources)
            content = str(result.data.get("content") or "").strip()
            if content:
                semantic_text.append(content[:12000])
    return {
        "destination": destination,
        "status": "researched" if web_sources or flyai_items else "needs_review",
        "queries": queries,
        "web_sources": web_sources,
        "flyai_items": flyai_items[:24],
        "flyai_semantic_text": "\n\n".join(semantic_text),
        "sources": [*web_sources, *flyai_sources],
        "providers": {
            "web": bool(web_sources),
            "flyai_keyword_search": keyword_result.success,
            "flyai_ai_search": semantic_result.success,
            "flyai_errors": [
                result.error_code
                for result in (keyword_result, semantic_result)
                if not result.success and result.error_code
            ],
        },
    }
