from __future__ import annotations

import asyncio
import html
import re
import time
from collections import Counter
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from ..domain.models import SkillResult, SourceRecord
from .base import SkillAdapter, SkillContext

CATALOG = [
    {
        "id": "vehicle_demo_ev",
        "brand": "RoadMan",
        "series": "Explorer",
        "model": "纯电 SUV",
        "year": 2026,
        "power_type": "electric",
        "rated_range_km": 560,
        "battery_kwh": 82,
        "consumption_per_100km": 18,
        "max_charge_kw": 180,
        "height_m": 1.68,
        "width_m": 1.92,
        "seats": 5,
        "mountain_ready": True,
        "unpaved_ready": False,
        "estimated": True,
    },
    {
        "id": "vehicle_demo_hybrid",
        "brand": "RoadMan",
        "series": "Tourer",
        "model": "混动 SUV",
        "year": 2026,
        "power_type": "hybrid",
        "rated_range_km": 950,
        "consumption_per_100km": 5.8,
        "height_m": 1.72,
        "width_m": 1.91,
        "seats": 5,
        "mountain_ready": True,
        "unpaved_ready": False,
        "estimated": True,
    },
]

CARINFO_API_URL = "https://tool.bitefu.net/car/"


class CarInfoCatalogInput(BaseModel):
    query: str = Field(min_length=2, max_length=80)
    limit: int = Field(default=12, ge=1, le=30)


class CarInfoCatalogAdapter(SkillAdapter):
    """Search the concrete brand/series/model catalog from the carinfo Skill.

    The old ``carinfo.demo`` adapter remains as the deterministic fallback used
    by planning tests. This adapter follows the real carinfo Skill's ``info``
    endpoint and keeps the provider record/price/year evidence with every
    result so the UI can create a vehicle profile without inventing specs.
    """

    name = "carinfo.catalog"
    # Bump the adapter contract when detail enrichment changes so Redis does
    # not serve pre-enrichment identity-only results after deployment.
    version = "1.7.0"
    category = "vehicle"
    timeout_seconds = 12
    max_retries = 0
    cache_ttl_seconds = 6 * 3600

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = {**payload, "query": str(payload.get("query") or "").strip()}
        value = CarInfoCatalogInput.model_validate(normalized)
        return value.model_dump()

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = CarInfoCatalogInput.model_validate(payload)
        started = time.perf_counter()
        effective_query = request.query
        body: object = None
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            # The upstream search is intentionally literal and may return no
            # rows for a natural brand+trim phrase such as “特斯拉 Model 3”,
            # even though “Model 3” itself is indexed. Try a tiny, deterministic
            # set of human-friendly variants before reporting no results.
            for search_query in _catalog_search_queries(request.query):
                try:
                    response = await client.get(
                        CARINFO_API_URL,
                        params={"type": "info", "keyword": search_query},
                    )
                    response.raise_for_status()
                    candidate_body = response.json()
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    last_error = exc
                    continue
                body = candidate_body
                if _catalog_info_items(candidate_body):
                    effective_query = search_query
                    break
        if body is None and last_error is not None:
            raise last_error
        if not isinstance(body, dict) or body.get("status") != 1:
            return SkillResult(
                success=False,
                provider="Bitefu CarApi",
                warnings=[str(body.get("info") or "车型库没有匹配结果") if isinstance(body, dict) else "车型库没有匹配结果"],
                error_code="CARINFO_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        raw_items = body.get("info")
        if not isinstance(raw_items, list):
            raw_items = [raw_items] if isinstance(raw_items, dict) else []
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = _catalog_vehicle_item(raw)
            if not item or item["source_id"] in seen:
                continue
            seen.add(item["source_id"])
            items.append(item)
        # A brand-only query is capped by the upstream at the newest 50 rows;
        # those rows can all predate the detail table. Expand a few established
        # series concurrently so concrete, detail-backed trims are available
        # without hard-coding any manufacturer or model name.
        series_requests: list[dict[str, str]] = []
        brand_ids = _catalog_brand_ids_for_query(items, request.query)
        if brand_ids:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as series_client:
                series_rows = await _fetch_catalog_series_rows(series_client, brand_ids[0])
                series_requests = _catalog_series_expansion_requests(
                    series_rows,
                    request.query,
                    limit=4,
                )
                # A provider deployment may not expose the series index. Keep
                # the older name-based expansion as a graceful fallback.
                if not series_requests:
                    series_requests = [
                        {"query": query}
                        for query in _catalog_series_expansion_queries(items, request.query)
                    ]
                expanded_groups = await asyncio.gather(
                    *(_fetch_catalog_info_rows(
                        series_client,
                        query=request_item.get("query"),
                        series_id=request_item.get("series_id"),
                    ) for request_item in series_requests),
                    return_exceptions=True,
                )
        else:
            series_queries = _catalog_series_expansion_queries(items, request.query)
            expanded_groups = []
            if series_queries:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as series_client:
                    expanded_groups = await asyncio.gather(
                        *(_fetch_catalog_info_rows(series_client, query=query) for query in series_queries),
                        return_exceptions=True,
                    )
        if series_requests or expanded_groups:
            # ``expanded_groups`` is intentionally handled uniformly for both
            # the brand-id and name-query paths above.
            for expanded in expanded_groups:
                if not isinstance(expanded, list):
                    continue
                for raw in expanded:
                    item = _catalog_vehicle_item(raw)
                    if not item or item["source_id"] in seen:
                        continue
                    seen.add(item["source_id"])
                    items.append(item)
        items.sort(key=_catalog_sort_key)
        # Keep a bounded look-ahead window. Newer trims in the upstream search
        # list occasionally have no detail row while an adjacent trim does;
        # fetching only the first ``limit`` would therefore return an
        # identity-only list even though useful specifications are available.
        # Twelve concurrent detail calls stay below the Registry's 12-second
        # adapter budget; larger requested limits still return their remaining
        # identity rows without making the endpoint time out.
        candidate_items = _catalog_detail_probe_items(items, request.limit)
        # The provider's ``info`` endpoint is an identity/search endpoint. It
        # intentionally returns only brand/series/trim/price. Fetch the
        # matching ``detail`` record for each result before returning it to the
        # UI; otherwise a catalogue search appears to work while every useful
        # specification is silently lost.
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as detail_client:
            details = await asyncio.gather(
                *(_fetch_catalog_detail(detail_client, item["source_id"]) for item in candidate_items),
                return_exceptions=True,
            )
        for item, detail in zip(candidate_items, details):
            if isinstance(detail, dict):
                item.update(detail)
                if detail.get("rated_range_km") is not None:
                    item["estimated_fields"] = [
                        field
                        for field in item.get("estimated_fields", [])
                        if field != "rated_range_km"
                    ]
            item["specs_missing"] = _missing_specs(item)
        # Prefer records with verified detail, then retain the provider's
        # availability/year ordering. This makes a normal brand/model search
        # immediately useful while still returning identity-only rows when the
        # upstream has no detailed record for a trim.
        candidate_items.sort(
            key=lambda item: (0 if item.get("specifications") else 1, *_catalog_sort_key(item)),
        )
        candidate_ids = {item["source_id"] for item in candidate_items}
        requested_tail = [
            item
            for item in items[0:request.limit]
            if item["source_id"] not in candidate_ids
        ]
        # For small limits the look-ahead may contain more candidates than the
        # caller requested; promote the best enriched rows. For large limits,
        # append the untouched tail so the response still honours ``limit``.
        items = (candidate_items[: request.limit] + requested_tail)[: request.limit]
        if not items:
            return SkillResult(
                success=False,
                provider="Bitefu CarApi",
                warnings=["车型库没有返回可添加的具体车型"],
                error_code="CARINFO_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        source_url = f"{CARINFO_API_URL}?{urlencode({'type': 'info', 'keyword': effective_query})}"
        data = {"query": request.query, "count": len(items), "items": items}
        if effective_query != request.query:
            data["provider_query"] = effective_query
        return SkillResult(
            success=True,
            provider="Bitefu CarApi",
            data=data,
            sources=[
                SourceRecord(
                    provider="Bitefu CarApi",
                    title="汽车品牌/车系/车型数据库",
                    url=source_url,
                )
            ],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": True, "provider": "Bitefu CarApi"}


def _catalog_search_queries(query: str) -> list[str]:
    """Return at most three provider-friendly variants for one user query."""
    normalized = re.sub(r"\s+", " ", query.strip())
    if not normalized:
        return []
    variants = [normalized]
    parts = [part for part in re.split(r"[\s,，、/|:：]+", normalized) if part]
    if len(parts) > 1:
        # Brand + trim is the common failure mode; searching the trim suffix
        # keeps the user's original query intact while matching the provider's
        # index (for example “特斯拉 Model 3” -> “Model 3”).
        variants.append(" ".join(parts[1:]))
        variants.append("".join(parts))
    result: list[str] = []
    for variant in variants:
        variant = variant.strip()
        if variant and variant not in result:
            result.append(variant)
        if len(result) >= 3:
            break
    return result


def _catalog_info_items(body: object) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or body.get("status") != 1:
        return []
    raw_items = body.get("info")
    if isinstance(raw_items, dict):
        return [raw_items]
    if isinstance(raw_items, list):
        return [item for item in raw_items if isinstance(item, dict)]
    return []


def _catalog_detail_probe_items(
    items: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Choose useful detail probes for a broad brand/series search.

    The upstream identity index is newer than its detail table. A broad query
    such as ``小鹏`` therefore starts with many 2026 trims whose detail endpoint
    legitimately has no row, while 2024/older concrete trims later in the same
    result set contain range, battery and consumption specifications. Probe the
    requested head plus a small, diverse stable-history sample instead of
    issuing dozens of blind calls or returning a wall of identity-only rows.
    """
    if not items:
        return []
    head_count = min(len(items), min(max(1, limit), 12))
    selected = list(items[:head_count])
    selected_ids = {str(item.get("source_id") or "") for item in selected}
    historical = [
        item
        for item in items[head_count:]
        if str(item.get("state") or "") == "0"
        and (item.get("year") is None or int(item.get("year") or 0) <= 2024)
    ]
    # First cover different series, then fill the remaining probe budget. This
    # lets a brand query expose several useful concrete models rather than 12
    # near-identical trims from one series.
    seen_series: set[str] = set()
    ordered_history: list[dict[str, Any]] = []
    for item in historical:
        series = str(item.get("series") or "")
        if series and series in seen_series:
            continue
        if series:
            seen_series.add(series)
        ordered_history.append(item)
    ordered_history.extend(item for item in historical if item not in ordered_history)
    probe_budget = min(len(items), max(head_count, 24))
    for item in ordered_history:
        source_id = str(item.get("source_id") or "")
        if not source_id or source_id in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(source_id)
        if len(selected) >= probe_budget:
            break
    return selected


def _catalog_brand_ids_for_query(
    items: list[dict[str, Any]],
    original_query: str,
) -> list[str]:
    """Return the dominant brand id only for a broad brand search.

    ``info?keyword=品牌`` is capped at the newest rows and does not expose all
    historical series. The provider can filter its series index by
    ``brand_id``; use that path only when the query is a brand-like phrase so
    a specific trim (for example ``P7`` or ``Model 3``) is not widened.
    """
    query_key = re.sub(r"\s+", "", str(original_query or "")).casefold()
    if not query_key or re.search(r"\d", query_key):
        return []
    counts: Counter[str] = Counter()
    brand_names: dict[str, str] = {}
    for item in items:
        brand_id = str(item.get("brand_id") or "").strip()
        brand = re.sub(r"\s+", "", str(item.get("brand") or "")).casefold()
        if not brand_id or not brand:
            continue
        counts[brand_id] += 1
        brand_names[brand_id] = brand
    # A query is broad when it is contained in (or equals) the provider's
    # brand name. This also handles the natural shorthand ``小鹏`` for
    # ``小鹏汽车`` without maintaining a manufacturer allow-list.
    return [
        brand_id
        for brand_id, _ in counts.most_common()
        if query_key in brand_names.get(brand_id, "")
    ][:1]


async def _fetch_catalog_series_rows(
    client: httpx.AsyncClient,
    brand_id: str,
) -> list[dict[str, Any]]:
    """Fetch all series metadata for one provider brand id."""
    try:
        response = await client.get(
            CARINFO_API_URL,
            params={
                "type": "series",
                "brand_id": brand_id,
                "pagesize": 100,
            },
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return []
    if not isinstance(body, dict) or body.get("status") != 1:
        return []
    rows = body.get("info")
    if isinstance(rows, dict):
        rows = [rows]
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _catalog_series_expansion_requests(
    rows: list[dict[str, Any]],
    original_query: str,
    *,
    limit: int = 4,
) -> list[dict[str, str]]:
    """Choose established series whose concrete trims are worth probing."""
    if not rows or limit <= 0:
        return []
    query_key = re.sub(r"\s+", "", str(original_query or "")).casefold()
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        series_id = str(row.get("id") or "").strip()
        # ``name`` is the concise series label (e.g. 小鹏P7); ``full_name``
        # usually includes the brand prefix and is retained as the search
        # fallback when older provider deployments omit ``name``.
        full_name = str(row.get("name") or row.get("full_name") or "").strip()
        if not series_id or not full_name or series_id in seen:
            continue
        series_key = re.sub(r"\s+", "", full_name).casefold()
        if query_key and series_key == query_key:
            # The broad query itself can already contain the active series;
            # it is still useful to keep that series when it is the only one,
            # but avoid spending every probe on the same literal match.
            continue
        state = str(row.get("seriesstate") or "").strip()
        has_info = str(row.get("has_info") or "").strip()
        try:
            numeric_id = int(series_id)
        except ValueError:
            numeric_id = 0
        candidates.append(
            {
                "series_id": series_id,
                "query": full_name,
                "has_info": has_info,
                "state": state,
                "numeric_id": numeric_id,
            }
        )
        seen.add(series_id)
    state_rank = {"20": 0, "10": 1, "40": 2, "0": 3}
    candidates.sort(
        key=lambda row: (
            0 if row["has_info"] == "1" else 1,
            state_rank.get(row["state"], 4),
            -int(row["numeric_id"]),
        )
    )
    return [
        {"series_id": row["series_id"], "query": row["query"]}
        for row in candidates[:limit]
    ]


def _catalog_series_expansion_queries(
    items: list[dict[str, Any]],
    original_query: str,
    *,
    limit: int = 4,
) -> list[str]:
    """Select established series for a broad brand-query detail expansion."""
    if not items or limit <= 0:
        return []
    query_key = re.sub(r"\s+", "", str(original_query or "")).casefold()
    series_keys = {
        re.sub(r"\s+", "", str(item.get("series") or "")).casefold()
        for item in items
        if item.get("series")
    }
    if any(key and key in query_key for key in series_keys) or (
        re.search(r"\d", query_key)
        and any(query_key and query_key in key for key in series_keys)
    ):
        return []
    grouped: dict[str, tuple[int, int]] = {}
    for item in items:
        series = str(item.get("series") or "").strip()
        series_key = re.sub(r"\s+", "", series).casefold()
        if not series or not series_key or series_key in query_key:
            continue
        year = int(item.get("year") or 9999)
        historical_rank = 0 if str(item.get("state") or "") in {"0", "40"} else 1
        current = grouped.get(series)
        rank = (historical_rank, year)
        if current is None or rank < current:
            grouped[series] = rank
    if len(grouped) <= 1:
        return []
    return [
        series
        for series, _ in sorted(
            grouped.items(),
            key=lambda pair: (pair[1][0], pair[1][1], pair[0]),
        )[:limit]
    ]


async def _fetch_catalog_info_rows(
    client: httpx.AsyncClient,
    query: str | None = None,
    series_id: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"type": "info"}
    if series_id:
        params["series_id"] = str(series_id)
    elif query:
        params["keyword"] = query
    else:
        return []
    try:
        response = await client.get(
            CARINFO_API_URL,
            params=params,
        )
        response.raise_for_status()
        return _catalog_info_items(response.json())
    except (httpx.HTTPError, ValueError, TypeError):
        return []


async def _fetch_catalog_detail(
    client: httpx.AsyncClient,
    source_id: str,
) -> dict[str, Any] | None:
    """Fetch and normalize one concrete trim's specification record.

    Detail data is best-effort: older/imported trims sometimes have no detail
    row. A missing detail must not discard the searchable identity returned by
    the first request.
    """
    try:
        response = await client.get(
            CARINFO_API_URL,
            params={"type": "detail", "id": source_id},
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    specs = _extract_detail_specs(body)
    if not specs:
        return None
    metrics = _derive_vehicle_metrics(specs)
    item = {
        **metrics,
        "specifications": [
            {"name": name, "value": value}
            for name, value in specs[:120]
        ],
        "detail_source_url": f"{CARINFO_API_URL}?{urlencode({'type': 'detail', 'id': source_id})}",
    }
    return item


def _extract_detail_specs(body: object) -> list[tuple[str, str]]:
    """Flatten the provider's nested ``info.param[].paramitems`` payload."""
    if not isinstance(body, dict) or body.get("status") != 1:
        return []
    info = body.get("info")
    if not isinstance(info, dict):
        return []
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    groups = info.get("param")
    if not isinstance(groups, list):
        return []
    for group in groups:
        if not isinstance(group, dict):
            continue
        paramitems = group.get("paramitems")
        if not isinstance(paramitems, list):
            continue
        for param in paramitems:
            if not isinstance(param, dict):
                continue
            name = _clean_detail_text(param.get("name"))
            if not name:
                continue
            valueitems = param.get("valueitems")
            values: list[str] = []
            if isinstance(valueitems, list):
                for valueitem in valueitems:
                    if not isinstance(valueitem, dict):
                        continue
                    value = _clean_detail_text(valueitem.get("value"))
                    if value:
                        values.append(value)
            value = " / ".join(dict.fromkeys(values))
            if not value or value in {"-", "—", "--"}:
                continue
            if name in seen:
                # Some trims repeat a parameter in two groups. Keep the first
                # populated value and avoid bloating the response.
                continue
            seen.add(name)
            result.append((name, value[:300]))
    return result


def _clean_detail_text(value: object) -> str:
    text = html.unescape(str(value or "")).replace("&nbsp;", " ")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _derive_vehicle_metrics(specs: list[tuple[str, str]]) -> dict[str, Any]:
    """Map common provider labels to the planner's typed vehicle fields."""
    values = {name: value for name, value in specs}

    def find_number(*predicates: Any) -> float | None:
        for name, value in specs:
            if all(predicate(name, value) for predicate in predicates):
                number = _number_from_text(value)
                if number is not None:
                    return number
        return None

    range_km = find_number(
        lambda name, value: "续航" in name and ("km" in name.casefold() or "里程" in name),
        lambda name, value: any(token in name.upper() for token in ("CLTC", "WLTC", "NEDC")),
    )
    if range_km is None:
        range_km = find_number(lambda name, value: "综合续航" in name)
    if range_km is None:
        range_km = find_number(lambda name, value: "续航" in name)

    battery_kwh = find_number(
        lambda name, value: "电池" in name and ("能量" in name or "容量" in name),
    )
    consumption = find_number(
        lambda name, value: "耗电" in name or "耗电量" in name,
    )
    if consumption is None:
        consumption = find_number(
            lambda name, value: "油耗" in name and "综合" in name,
        )
    if consumption is None:
        consumption = find_number(lambda name, value: "油耗" in name)

    max_charge_kw = find_number(
        lambda name, value: "快充" in name and "功率" in name,
    )

    dimensions = next(
        (value for name, value in specs if "长*宽*高" in name or "长×宽×高" in name),
        "",
    )
    dimension_numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", dimensions)]
    length_m = dimension_numbers[0] / 1000 if len(dimension_numbers) >= 1 else None
    width_m = dimension_numbers[1] / 1000 if len(dimension_numbers) >= 2 else None
    height_m = dimension_numbers[2] / 1000 if len(dimension_numbers) >= 3 else None
    if length_m is None:
        length_m = _mm_metric(specs, "长度")
    if width_m is None:
        width_m = _mm_metric(specs, "宽度")
    if height_m is None:
        height_m = _mm_metric(specs, "高度")

    seats: int | None = None
    for name, value in specs:
        if "座" not in name and "座" not in value:
            continue
        match = re.search(r"(\d+)\s*座", value)
        if match:
            seats = int(match.group(1))
            break
        if "座位数" in name:
            match = re.search(r"\d+", value)
            if match:
                seats = int(match.group())
                break

    power_type = _infer_power_type_from_text(" ".join(values.values()))
    return {
        "power_type": power_type,
        "rated_range_km": range_km,
        "battery_kwh": battery_kwh,
        "consumption_per_100km": consumption,
        "max_charge_kw": max_charge_kw,
        "height_m": height_m,
        "width_m": width_m,
        "seats": seats or 5,
    }


def _number_from_text(value: object) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return _number(match.group()) if match else None


def _mm_metric(specs: list[tuple[str, str]], label: str) -> float | None:
    for name, value in specs:
        if label in name and "mm" in name.casefold():
            number = _number_from_text(value)
            if number is not None:
                return number / 1000
    return None


def _infer_power_type_from_text(text: str) -> str:
    normalized = text.casefold()
    if re.search(r"混动|插混|插电|增程|双模|dm[- ]?i|dm[- ]?p|phev|hev|plug[- ]?in|range extender", normalized):
        return "hybrid"
    if re.search(r"纯电|电动|新能源|bev", normalized):
        return "electric"
    return "fuel"


def _infer_catalog_name_range(
    model: str,
    *,
    brand: str,
    series: str,
    power_type: str,
) -> float | None:
    """Recover an explicitly advertised range embedded in a trim name.

    New catalogue rows may precede their parameter-detail record by several
    months, but names such as ``纯电 665 Max`` and ``增程 1585 四驱`` already
    carry an official advertised range. Strip brand/series/year first so model
    numbers such as G9/P7 cannot be mistaken for kilometres. This value is
    marked estimated at the field level and must still be confirmed by trim.
    """
    if power_type not in {"electric", "hybrid"}:
        return None
    trim = str(model or "")
    for token in (brand, series):
        if token:
            trim = trim.replace(token, " ")
    trim = re.sub(r"20\d{2}\s*款?", " ", trim)
    preferred = re.search(r"(?:纯电|增程|续航)\D{0,8}([2-9]\d{2}|1\d{3})(?!\d)", trim)
    matches = preferred.groups() if preferred else ()
    if not matches:
        matches = tuple(re.findall(r"(?<!\d)([2-9]\d{2}|1\d{3})(?!\d)", trim))
    if not matches:
        return None
    value = float(matches[0])
    return value if 200 <= value <= 2000 else None


def _missing_specs(item: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if item.get("rated_range_km") is None:
        missing.append("额定续航/油箱续航")
    if item.get("consumption_per_100km") is None:
        missing.append("百公里能耗")
    if item.get("power_type") in {"electric", "hybrid"} and item.get("battery_kwh") is None:
        missing.append("电池容量（如适用）")
    return missing


class CarInfoInput(BaseModel):
    brand: str | None = None
    power_type: Literal["electric", "hybrid", "fuel"] | None = None


class CarInfoDemoAdapter(SkillAdapter):
    name = "carinfo.demo"
    category = "vehicle"
    cache_ttl_seconds = 24 * 3600
    max_retries = 0

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return CarInfoInput.model_validate(payload).model_dump(exclude_none=True)

    async def execute(self, payload: dict[str, Any], _: SkillContext) -> SkillResult:
        request = CarInfoInput.model_validate(payload)
        items = [
            item
            for item in CATALOG
            if (not request.brand or item["brand"].lower() == request.brand.lower())
            and (not request.power_type or item["power_type"] == request.power_type)
        ]
        return SkillResult(
            success=True,
            provider="RoadMan CarInfo Demo",
            data={"items": items},
            estimated=True,
            sources=[
                SourceRecord(
                    provider="RoadMan",
                    title="阶段 C 固定车型样本",
                )
            ],
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ready", "configured": True, "records": len(CATALOG)}


def _catalog_vehicle_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    source_id = str(raw.get("id") or "").strip()
    brand = str(raw.get("brand_name") or "").strip()
    series = str(raw.get("series_name") or raw.get("group_name") or "").strip()
    model = str(raw.get("full_name") or raw.get("name") or "").strip()
    if not source_id or not brand or not series or not model:
        return None
    year = _integer(raw.get("year"))
    power_type = _infer_power_type(raw)
    advertised_range = _infer_catalog_name_range(
        model,
        brand=brand,
        series=series,
        power_type=power_type,
    )
    state = str(raw.get("state") or "").strip()
    brand_id = str(raw.get("brand_id") or "").strip()
    group_id = str(raw.get("group_id") or "").strip()
    series_id = str(raw.get("series_id") or "").strip()
    source_url = f"{CARINFO_API_URL}?{urlencode({'type': 'info', 'id': source_id})}"
    item = {
        "id": f"carinfo_{source_id}",
        "source_id": source_id,
        "brand_id": brand_id,
        "group_id": group_id,
        "series_id": series_id,
        "brand": brand,
        "series": series,
        "model": model,
        "year": year,
        "power_type": power_type,
        # The catalog endpoint supplies identity/year/price, but not a
        # reliable per-trim battery or real-world range. Keep those fields
        # null so the traveller can confirm their exact configuration instead
        # of silently inheriting the demo SUV's numbers.
        "rated_range_km": advertised_range,
        "battery_kwh": None,
        "consumption_per_100km": None,
        "max_charge_kw": None,
        "height_m": None,
        "width_m": None,
        "seats": 5,
        "current_energy_percent": 80,
        "safe_energy_reserve_percent": 15,
        "has_etc": False,
        "mountain_ready": True,
        "unpaved_ready": False,
        "state": state,
        "state_label": {"20": "在售", "30": "停售在库", "40": "进口/其他", "0": "历史款"}.get(state, "状态待核实"),
        "price_min_cny": _number(raw.get("minprice")),
        "price_max_cny": _number(raw.get("maxprice")),
        "source_url": source_url,
        "specs_missing": [],
        "estimated_fields": ["rated_range_km"] if advertised_range is not None else [],
    }
    item["specs_missing"] = _missing_specs(item)
    return item


def _catalog_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    state_rank = {"20": 0, "40": 1, "30": 2, "0": 3}.get(str(item.get("state")), 4)
    year = int(item.get("year") or 0)
    return (state_rank, -year, 0 if item.get("power_type") == "electric" else 1, str(item.get("model")))


def _integer(value: object) -> int | None:
    match = re.search(r"\d{4}", str(value or ""))
    return int(match.group()) if match else None


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _infer_power_type(raw: dict[str, Any]) -> str:
    text = " ".join(
        str(raw.get(key) or "")
        for key in ("brand_name", "series_name", "full_name", "name")
    ).casefold()
    inferred = _infer_power_type_from_text(text)
    if inferred != "fuel":
        return inferred
    # A small provider-derived hint for brands whose catalogue is exclusively
    # battery-electric; users can still change the dropdown before saving.
    if any(name in text for name in ("特斯拉", "tesla", "蔚来", "小鹏", "极氪", "智己")):
        return "electric"
    return "fuel"
