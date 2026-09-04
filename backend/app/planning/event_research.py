"""Best-effort web research for user-requested seasonal/special events.

The event names come from the Requirement Agent.  This module does not guess
what a phrase means; it searches the returned event names and keeps source
links/snippets for the final review step.  Network failure is represented as a
review note so a trip is never blocked merely because a source is unavailable.
"""

from __future__ import annotations

import html
import io
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
_ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_ENGLISH_DATE_RANGE = re.compile(
    r"(?:(?P<year>20\d{2})\s+)?(?P<month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(?P<day>3[01]|[12]\d|0?[1-9])\s*"
    r"(?:[-—–~]|to)\s*(?:(?P<end_month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+)?(?P<end_day>3[01]|[12]\d|0?[1-9])"
    r"(?:,?\s*(?P<end_year>20\d{2}))?",
    re.I,
)
_ENGLISH_SINGLE_DATE = re.compile(
    r"(?:(?P<year>20\d{2})\s+)?(?P<month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(?P<day>3[01]|[12]\d|0?[1-9])"
    r"(?:,?\s*(?P<end_year>20\d{2}))?",
    re.I,
)

# Common names are only used to locate the matching row in the public IMO
# calendar.  The calendar remains the source of truth; no event date is
# embedded here.
_METEOR_SHOWER_ALIASES = (
    ("象限仪座", "Quadrantids", "QUA"),
    ("天琴座", "Lyrids", "LYR"),
    ("宝瓶座", "Aquariids", "ETA"),
    ("英仙座", "Perseids", "PER"),
    ("猎户座", "Orionids", "ORI"),
    ("狮子座", "Leonids", "LEO"),
    ("双子座", "Geminids", "GEM"),
    ("天龙座", "Draconids", "DRA"),
    ("金牛座", "Taurids", "STA"),
    ("小熊座", "Ursids", "URS"),
)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


async def _imo_calendar_source(
    client: httpx.AsyncClient,
    event: str,
    year: int,
) -> dict[str, Any] | None:
    """Return a source excerpt from the IMO annual calendar when applicable.

    This is deliberately best-effort.  It enriches (rather than replaces) web
    search, and all facts still go through the source-constrained Event Agent
    and the conservative parser below.
    """
    event_text = event.lower()
    if "流星雨" not in event_text and "meteor" not in event_text:
        return None
    alias_entry = next(
        ((english, code) for chinese, english, code in _METEOR_SHOWER_ALIASES if chinese in event),
        None,
    )
    alias = alias_entry[0] if alias_entry else None
    shower_code = alias_entry[1] if alias_entry else None
    if not alias and "meteor" in event_text:
        alias = event
    if not alias:
        return None
    url = f"https://www.imo.net/files/meteor-shower/cal{year}.pdf"
    try:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content
        if not content:
            return None
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
    except Exception:  # the normal web search must remain available on PDF failure
        return None
    # Match the formal IMO heading (name + IAU number) first.  A plain name
    # search can accidentally select a related shower such as “o-Leonids”
    # before it reaches the actual “Leonids (013 LEO)” section.
    heading_pattern = (
        rf"{re.escape(alias)}\s*\(\d{{3}}\s+{re.escape(shower_code)}\)"
        if shower_code
        else rf"{re.escape(alias)}\s*\(\d{{3}}\s+[A-Z]{{3}}\)"
    )
    matches = list(re.finditer(heading_pattern, text, flags=re.IGNORECASE))
    if not matches:
        matches = list(re.finditer(re.escape(alias), text, flags=re.IGNORECASE))
    if not matches:
        return None
    chosen = None
    for match in matches:
        window = text[match.start() : match.start() + 1800]
        # A formal name can also be mentioned in the calendar introduction.
        # The actual entry starts with an “Active:” or “Maximum:” field right
        # after the numbered heading; require that close-by marker.
        if re.search(r"(?:Active|Maximum)\s*:", window[:260], flags=re.IGNORECASE):
            chosen = window
            break
    if chosen is None:
        chosen = text[matches[-1].start() : matches[-1].start() + 1800]
    excerpt = _clean_text(chosen or text[matches[0].start() : matches[0].start() + 900])
    if not excerpt:
        return None
    return {
        "provider": "International Meteor Organization",
        "title": f"IMO {year} 流星雨日历（{alias}）",
        "url": url,
        "snippet": excerpt[:1800],
    }


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
                official_source = await _imo_calendar_source(client, event, year)
                if official_source:
                    item["sources"].append(official_source)
                    seen_sources.add(official_source["url"])
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
    utc_times: list[tuple[int, str]] = []
    official_utc_times: list[tuple[int, str]] = []
    active_periods: list[tuple[int, str]] = []
    official_active_periods: list[tuple[int, str]] = []
    official_zhr_values: list[tuple[int, int]] = []

    for index, source in enumerate(sources):
        text = _clean_text(f"{source.get('title') or ''} {source.get('snippet') or ''}")
        if not text:
            continue
        date_range = _explicit_date_range(text, year)
        if date_range:
            matched_ranges.append(date_range)
            matched_indexes.append(index)
        zhr_match = re.search(r"(?:ZHR|每小时)[^0-9]{0,10}(\d{1,4})", text, re.I)
        if zhr_match:
            zhr_values.append((index, int(zhr_match.group(1))))
            if source.get("provider") == "International Meteor Organization":
                official_zhr_values.append((index, int(zhr_match.group(1))))
        utc_match = re.search(
            r"(?:Maximum|极大期|极大时刻).{0,160}?(?P<h>\d{1,2})\s*h"
            r"(?:\s*(?P<m>\d{1,2})\s*m)?\s*UT\b",
            text,
            re.I,
        )
        if utc_match:
            value = f"{int(utc_match.group('h')):02d}:{int(utc_match.group('m') or 0):02d}"
            utc_times.append((index, value))
            if source.get("provider") == "International Meteor Organization":
                official_utc_times.append((index, value))
        active_match = re.search(
            r"(?:Active|活动期)\s*[:：]\s*([^.;。]{3,80})",
            text,
            re.I,
        )
        if active_match:
            active_periods.append((index, active_match.group(1).strip()))
            if source.get("provider") == "International Meteor Organization":
                official_active_periods.append((index, active_match.group(1).strip()))

    if matched_ranges:
        # Prefer the range corroborated by the greatest number of excerpts.
        best_range = max(set(matched_ranges), key=matched_ranges.count)
        facts.setdefault("peak_start_date", best_range[0])
        facts.setdefault("peak_end_date", best_range[1])
    if "zhr" not in facts and zhr_values:
        facts["zhr"] = zhr_values[0][1]
        matched_indexes.append(zhr_values[0][0])
    if official_zhr_values:
        facts["zhr"] = official_zhr_values[0][1]
        matched_indexes.append(official_zhr_values[0][0])
    if "peak_time_utc" not in facts and utc_times:
        facts["peak_time_utc"] = utc_times[0][1]
        matched_indexes.append(utc_times[0][0])
    if official_utc_times:
        # The official calendar's explicit UTC clock is preferred over a
        # model conversion that may accidentally use a local timezone.
        facts["peak_time_utc"] = official_utc_times[0][1]
        matched_indexes.append(official_utc_times[0][0])
    if "active_period" not in facts and active_periods:
        facts["active_period"] = active_periods[0][1]
        matched_indexes.append(active_periods[0][0])
    if official_active_periods:
        facts["active_period"] = official_active_periods[0][1]
        matched_indexes.append(official_active_periods[0][0])

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
    # A source excerpt can list an activity range before its peak date (for
    # example “Active: November 6–30; Maximum: November 17”).  Prefer the
    # date attached to the peak label when one is present.
    peak_hint = re.search(r"(?:Maximum|极大期|极大时刻|峰值)[^.;。]{0,160}", text, re.I)
    if peak_hint and re.search(
        r"(?:20\d{2}\s*年\s*)?(?:\d{1,2}\s*月\s*\d{1,2}\s*日|"
        r"January|February|March|April|May|June|July|August|September|October|November|December)\s*\d{1,2}",
        peak_hint.group(0),
        re.I,
    ):
        text = peak_hint.group(0)
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

    match = _ENGLISH_DATE_RANGE.search(text)
    if match:
        parsed_year = int(match.group("year") or match.group("end_year") or expected_year)
        if parsed_year != expected_year:
            return None
        month = _ENGLISH_MONTHS[match.group("month").lower()]
        end_month = _ENGLISH_MONTHS[(match.group("end_month") or match.group("month")).lower()]
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

    match = _ENGLISH_SINGLE_DATE.search(text)
    if match:
        parsed_year = int(match.group("year") or match.group("end_year") or expected_year)
        if parsed_year != expected_year:
            return None
        try:
            value = date(
                parsed_year,
                _ENGLISH_MONTHS[match.group("month").lower()],
                int(match.group("day")),
            )
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
