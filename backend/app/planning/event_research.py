"""Best-effort web research for user-requested seasonal/special events.

The event names come from the Requirement Agent.  This module does not guess
what a phrase means; it searches the returned event names and keeps source
links/snippets for the final review step.  Network failure is represented as a
review note so a trip is never blocked merely because a source is unavailable.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote

import httpx


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


async def research_special_events(
    events: list[str],
    *,
    year: int,
    destination: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not events:
        return results
    async with httpx.AsyncClient(
        timeout=5,
        follow_redirects=True,
        headers={"User-Agent": "RoadMan/1.0 event-research"},
    ) as client:
        for event in list(dict.fromkeys(item.strip() for item in events if item.strip()))[:6]:
            query = f"{event} {year} 极大值 时间 观测"
            if destination:
                query += f" {destination}"
            item: dict[str, Any] = {
                "event": event,
                "query": query,
                "status": "needs_review",
                "sources": [],
            }
            try:
                response = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                )
                response.raise_for_status()
                body = response.text
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
                for index, (url, title) in enumerate(links[:5]):
                    item["sources"].append(
                        {
                            "provider": "Web Event Research",
                            "title": _clean_text(title),
                            "url": html.unescape(url),
                            "snippet": _clean_text(snippets[index]) if index < len(snippets) else "",
                        }
                    )
                if item["sources"]:
                    item["status"] = "researched"
            except (httpx.HTTPError, ValueError):
                item["status"] = "needs_review"
            item["search_url"] = f"https://duckduckgo.com/?q={quote(query)}"
            results.append(item)
    return results


def event_research_summary(item: dict[str, Any]) -> str:
    event = item.get("event") or "该活动"
    if item.get("status") != "researched":
        return f"{event}：暂未取得可验证的公开资料，出发前需要重新查询极大值/开放时间。"
    source = (item.get("sources") or [{}])[0]
    snippet = source.get("snippet") or source.get("title") or "已找到公开资料"
    return f"{event}：{snippet}（来源：{source.get('title') or '公开网页'}）"
