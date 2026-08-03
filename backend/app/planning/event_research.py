"""Best-effort web research for user-requested seasonal/special events.

The event names come from the Requirement Agent.  This module does not guess
what a phrase means; it searches the returned event names and keeps source
links/snippets for the final review step.  Network failure is represented as a
review note so a trip is never blocked merely because a source is unavailable.
"""

from __future__ import annotations

import html
import re
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


async def research_special_events(
    events: list[str],
    *,
    year: int,
    destination: str | None = None,
    fact_agent: Callable[[str, int, list[dict[str, Any]]], Awaitable[dict[str, Any]]] | None = None,
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
            # Keep the Agent's event wording, but do not quote the whole
            # phrase: a model may return “英仙座流星雨极大值” while sources
            # naturally title the event “英仙座流星雨”.  Unquoted terms let
            # the search engine match both forms.
            query = f"{event} {year} 极大值 极大期 峰值 具体时间 北京时间 UTC 观测"
            if destination:
                query += f" {destination}"
            item: dict[str, Any] = {
                "event": event,
                "query": query,
                "search_queries": [
                    query,
                    f"{event} {year} maximum peak exact UTC IMO calendar",
                    f"{event} {year} 官方 天文台 极大时刻",
                ],
                "status": "needs_review",
                "sources": [],
            }
            try:
                seen_sources: set[str] = set()
                for search_query in item["search_queries"]:
                    try:
                        response = await client.get(
                            "https://html.duckduckgo.com/html/",
                            params={"q": search_query},
                        )
                        response.raise_for_status()
                    except (httpx.HTTPError, ValueError):
                        continue
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
                    for index, (url, title) in enumerate(links[:8]):
                        parsed_url = _normalize_result_url(url)
                        title_text = _clean_text(title)
                        source_key = parsed_url or title_text
                        if source_key in seen_sources:
                            continue
                        seen_sources.add(source_key)
                        item["sources"].append(
                            {
                                "provider": "Web Event Research",
                                "title": title_text,
                                "url": parsed_url,
                                "snippet": _clean_text(snippets[index]) if index < len(snippets) else "",
                            }
                        )
                        if len(item["sources"]) >= 12:
                            break
                    if len(item["sources"]) >= 12:
                        break
                if item["sources"]:
                    item["status"] = "researched"
                    if fact_agent:
                        try:
                            facts = await fact_agent(event, year, item["sources"])
                        except Exception:  # source search must remain non-blocking
                            facts = {}
                        if facts:
                            item["facts"] = facts
            except (httpx.HTTPError, ValueError):
                item["status"] = "needs_review"
            item["search_url"] = f"https://duckduckgo.com/?q={quote(query)}"
            results.append(item)
    return results


def _normalize_result_url(value: str) -> str:
    """Unwrap DuckDuckGo redirect links so users see the real source URL."""
    url = html.unescape(value)
    parsed = urlparse(url)
    if parsed.path.endswith("/l/") or parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return url


def event_research_summary(item: dict[str, Any]) -> str:
    event = item.get("event") or "该活动"
    if item.get("status") != "researched":
        return f"{event}：暂未取得可验证的公开资料，出发前需要重新查询极大值/开放时间。"
    facts = item.get("facts") or {}
    summary = facts.get("summary")
    details: list[str] = []
    start, end = facts.get("peak_start_date"), facts.get("peak_end_date")
    if start and end and start != end:
        details.append(f"极大期约为 {start} 至 {end}")
    elif start:
        details.append(f"极大期约为 {start}")
    if facts.get("peak_time_local"):
        details.append(f"北京时间 {facts['peak_time_local']}")
    elif facts.get("peak_time_utc"):
        details.append(f"UTC {facts['peak_time_utc']}")
    elif facts.get("peak_time_label"):
        details.append(f"来源时间表述：{facts['peak_time_label']}")
    if facts.get("observation_window_local"):
        details.append(f"观测窗口：{facts['observation_window_local']}")
    if facts.get("zhr") is not None:
        details.append(f"预计 ZHR {facts['zhr']}")
    if summary:
        details.insert(0, summary)
    if details:
        return f"{event}：{'；'.join(details)}（来源核验置信度：{facts.get('confidence', 'low')}）"
    source = (item.get("sources") or [{}])[0]
    snippet = source.get("snippet") or source.get("title") or "已找到公开资料"
    return f"{event}：{snippet}（来源：{source.get('title') or '公开网页'}）"
