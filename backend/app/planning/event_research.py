"""Best-effort web research for user-requested seasonal/special events.

The event names come from the Requirement Agent.  This module does not guess
what a phrase means; it searches the returned event names and keeps source
links/snippets for the final review step.  Network failure is represented as a
review note so a trip is never blocked merely because a source is unavailable.
"""

from __future__ import annotations

import html
import re
from datetime import date
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx


_CHINESE_DATE_RANGE = re.compile(
    r"(?:(?P<year>20\d{2})\s*年\s*)?"
    r"(?P<month>1[0-2]|0?[1-9])\s*月\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])\s*日?\s*"
    r"(?:至|到|[-—–~～])\s*"
    r"(?:(?P<end_month>1[0-2]|0?[1-9])\s*月\s*)?"
    r"(?P<end_day>3[01]|[12]\d|0?[1-9])\s*日"
)
_CHINESE_SINGLE_DATE = re.compile(
    r"(?:(?P<year>20\d{2})\s*年\s*)?"
    r"(?P<month>1[0-2]|0?[1-9])\s*月\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])\s*日"
)


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
                    facts: dict[str, Any] = {}
                    if fact_agent:
                        try:
                            facts = await fact_agent(event, year, item["sources"])
                        except Exception:  # source search must remain non-blocking
                            facts = {}
                    # The model is the primary evidence interpreter.  A
                    # conservative parser fills only dates/numbers literally
                    # present in search excerpts, so a transient model failure
                    # never turns a useful result into “已找到资料” with no
                    # details.  It does not infer astronomical facts.
                    facts = _merge_explicit_source_facts(facts, year, item["sources"])
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


def _merge_explicit_source_facts(
    agent_facts: dict[str, Any] | None,
    year: int,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fill missing event fields using only literal source-excerpt evidence."""
    facts = dict(agent_facts or {})
    matched_indexes: list[int] = []
    matched_ranges: list[tuple[str, str]] = []
    zhr_values: list[tuple[int, int]] = []

    for index, source in enumerate(sources):
        text = _clean_text(f"{source.get('title') or ''} {source.get('snippet') or ''}")
        if not text:
            continue
        date_range = _explicit_date_range(text, year)
        if date_range:
            matched_ranges.append(date_range)
            matched_indexes.append(index)
        zhr_match = re.search(r"(?:ZHR\s*[:：约为可达]*|每小时[^0-9]{0,10})(\d{1,4})", text, re.I)
        if zhr_match:
            zhr_values.append((index, int(zhr_match.group(1))))

    if matched_ranges:
        # Prefer the range corroborated by the greatest number of excerpts.
        best_range = max(set(matched_ranges), key=matched_ranges.count)
        facts.setdefault("peak_start_date", best_range[0])
        facts.setdefault("peak_end_date", best_range[1])
    if "zhr" not in facts and zhr_values:
        facts["zhr"] = zhr_values[0][1]
        matched_indexes.append(zhr_values[0][0])

    evidence = facts.get("evidence_source_indexes")
    if matched_indexes and (not isinstance(evidence, list) or not evidence):
        facts["evidence_source_indexes"] = list(dict.fromkeys(matched_indexes))[:8]
    if facts.get("peak_start_date") and not facts.get("summary"):
        end = facts.get("peak_end_date")
        date_text = str(facts["peak_start_date"])
        if end and end != facts["peak_start_date"]:
            date_text += f" 至 {end}"
        facts["summary"] = f"公开资料明确给出的核心日期窗口为 {date_text}；具体时刻及现场可见性仍需临近出发复核。"
    if facts and not facts.get("confidence"):
        corroborations = matched_ranges.count(max(set(matched_ranges), key=matched_ranges.count)) if matched_ranges else 0
        facts["confidence"] = "medium" if corroborations >= 2 else "low"
    return facts


def _explicit_date_range(text: str, expected_year: int) -> tuple[str, str] | None:
    match = _CHINESE_DATE_RANGE.search(text)
    if match:
        parsed_year = int(match.group("year") or expected_year)
        if parsed_year != expected_year:
            return None
        month = int(match.group("month"))
        end_month = int(match.group("end_month") or month)
        try:
            start = date(parsed_year, month, int(match.group("day")))
            end = date(parsed_year, end_month, int(match.group("end_day")))
        except ValueError:
            return None
        if end < start:
            return None
        return start.isoformat(), end.isoformat()

    match = _CHINESE_SINGLE_DATE.search(text)
    if match:
        parsed_year = int(match.group("year") or expected_year)
        if parsed_year != expected_year:
            return None
        try:
            value = date(parsed_year, int(match.group("month")), int(match.group("day")))
        except ValueError:
            return None
        return value.isoformat(), value.isoformat()
    return None


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
