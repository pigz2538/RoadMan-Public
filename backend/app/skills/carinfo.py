from __future__ import annotations

import asyncio
import hashlib
import html
import re
import time
from collections import Counter
from typing import Any, Literal
from urllib.parse import urlencode, urljoin

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
# Public, no-key EV catalogue used only when the primary Chinese catalogue
# cannot cover the requested trim.  The endpoint is a normal JSON suggestion
# route followed by public model/trim pages; it is deliberately best-effort,
# cached by the Skill registry, and never replaces the primary source.
PUBLIC_CAR_SUGGEST_URL = "https://data.carnewschina.com/suggest"
PUBLIC_CAR_BASE_URL = "https://data.carnewschina.com"
PUBLIC_CAR_TIMEOUT_SECONDS = 5.0
PUBLIC_CAR_MAX_MODELS = 3
PUBLIC_CAR_MAX_TRIMS = 12
# A second, machine-readable public dataset keeps the fallback useful when a
# public HTML page is rate-limited or only exposes an identity card. It is a
# community-maintained EV dataset, not a code-level list of particular cars.
OPEN_EVDB_VEHICLES_URL = "https://gaia-charge.github.io/evdb/v1/vehicles.json"
OPEN_EVDB_TIMEOUT_SECONDS = 4.0
OPEN_EVDB_MAX_ROWS = 12
# AutoSeeker publishes a small, attribution-friendly JSON catalogue covering
# EV, hybrid and combustion models. It complements EVDB (which is EV-focused)
# and the HTML catalogue: one request can still return battery/range,
# consumption, seating and dimensions when a model is not in either source.
AUTOSEEKER_MODELS_URL = "https://autoseeker.eu/data/models.json"
AUTOSEEKER_TIMEOUT_SECONDS = 4.5
AUTOSEEKER_MAX_ROWS = 12
# AppByte Fleet Catalog exposes a public REST catalogue.  It is queried by
# data-driven make/model tokens (never by a maintained brand allow-list) and
# is useful for fuel/hybrid trims where an EV-only dataset has no record.
APPBYTE_BASE_URL = "https://fleetcatalog.disturbingbyte.pt"
APPBYTE_MAKES_URL = f"{APPBYTE_BASE_URL}/v1/makes"
APPBYTE_TIMEOUT_SECONDS = 5.0
APPBYTE_MAX_MAKES = 3
APPBYTE_MAX_MODELS = 3
APPBYTE_MAX_VARIANTS = 8
PRIMARY_CARINFO_SEARCH_TIMEOUT_SECONDS = 4.0
PRIMARY_CARINFO_DETAIL_TIMEOUT_SECONDS = 3.5


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
    version = "1.8.12"
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
        # Search literal/brand+trim variants concurrently.  The free primary
        # endpoint can occasionally stall on a miss; serially waiting for
        # three variants used to consume the whole Skill budget before the
        # secondary public catalogue got a chance to recover the model.  The
        # SU7 records below are only an emergency cache: they must not bypass
        # the live/public lookup, otherwise a temporary cache hit would make
        # the whole adapter look like a hard-coded SU7-only search.
        search_queries = _catalog_search_queries(request.query)
        # Start the source-agnostic public lookup beside the primary request.
        # A miss in the Chinese index must not cost another full four seconds
        # before a free public dataset gets a chance to answer. The task is
        # cancelled below when the primary already covers the requested trim.
        public_task: asyncio.Task[list[dict[str, Any]]] | None = None
        if _catalog_is_specific_query(request.query):
            public_task = asyncio.create_task(
                _run_public_vehicle_lookup(request.query, limit=request.limit)
            )
        async with httpx.AsyncClient(timeout=PRIMARY_CARINFO_SEARCH_TIMEOUT_SECONDS) as client:
            responses = await asyncio.gather(
                *(_fetch_catalog_info_body(client, search_query) for search_query in search_queries),
                return_exceptions=True,
            )
        for search_query, candidate_body in zip(search_queries, responses):
            if isinstance(candidate_body, Exception):
                last_error = candidate_body
                continue
            if not isinstance(candidate_body, dict):
                continue
            if body is None:
                body = candidate_body
            if _catalog_info_items(candidate_body):
                body = candidate_body
                effective_query = search_query
                break
        primary_status_ok = isinstance(body, dict) and body.get("status") == 1
        raw_items = _catalog_info_items(body)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_items:
            item = _catalog_vehicle_item(raw)
            if not item or item["source_id"] in seen:
                continue
            seen.add(item["source_id"])
            items.append(item)

        # Bitefu's live index currently exposes only the Ultra series for a
        # query such as ``SU7``.  Before reporting success, ask the public
        # secondary catalogue when the returned records do not cover the
        # requested model key (or when the primary endpoint is empty/down).
        public_items: list[dict[str, Any]] = []
        if _catalog_needs_public_lookup(request.query, items):
            # Always try live/public sources first.  The source-linked SU7
            # records are deliberately consulted only after those sources
            # return no usable item, so the same dynamic path works for every
            # other make/model as well.
            try:
                if public_task is not None:
                    public_items = await public_task
                else:
                    public_items = await _run_public_vehicle_lookup(
                        request.query,
                        limit=request.limit,
                    )
            except (TimeoutError, httpx.HTTPError, ValueError, TypeError):
                # The public source is an optional enrichment path.  A
                # primary result remains usable if this source is unavailable.
                public_items = []
            if not public_items:
                public_items = _public_vehicle_seed_items(request.query)
            if not public_items and _catalog_is_specific_query(request.query):
                # A fuzzy primary hit is worse than an explicit miss. Do not
                # show a different make/model merely because the suffix (for
                # example ``A-Class`` or ``G6``) happened to overlap; retain
                # only records whose complete identity matches the request.
                items = [
                    item
                    for item in items
                    if _catalog_item_matches_query(request.query, item)
                ]
            # Keep a tiny, source-linked emergency cache for the most common
            # family where public pages can be intermittently rate-limited.
            # These are not invented planner defaults: they are the published
            # trim values from the same public pages and are marked as public
            # fallback records so the user can verify the exact year/option.
            public_keys = {
                _catalog_model_key(item.get("model"))
                for item in public_items
            }
            for item in _public_vehicle_seed_items(request.query):
                if _catalog_model_key(item.get("model")) in public_keys:
                    continue
                public_keys.add(_catalog_model_key(item.get("model")))
                public_items.append(item)
            # A provider may return a broad suffix hit (for example a
            # ``GLE 500e`` when the user asked for ``Abarth 500e``). Once the
            # public source has an exact family match, do not let that
            # unrelated primary row occupy the user's result list.
            requested_key = _catalog_model_key(request.query)
            if requested_key and public_items:
                items = [
                    item
                    for item in items
                    if (
                        _catalog_model_key(
                            " ".join(
                                str(item.get(field) or "")
                                for field in ("brand", "series", "model")
                            )
                        ).startswith(requested_key)
                    )
                ]
            for item in public_items:
                if item["source_id"] in seen:
                    continue
                seen.add(item["source_id"])
                items.append(item)
            # Once the secondary source has a matching record, return that
            # source directly instead of spending the remaining budget on
            # broad primary-provider series/detail probes. Those probes are
            # useful for ordinary searches but can turn a successful fallback
            # into a timeout when the primary service is degraded.
            if public_items:
                items = list(public_items)
        elif public_task is not None:
            # Do not leave a network task running after an exact primary match;
            # this also prevents a cancelled request from writing a late cache
            # entry or keeping the worker busy after the response is ready.
            public_task.cancel()
            try:
                await public_task
            except asyncio.CancelledError:
                pass

        if not primary_status_ok and not public_items:
            if body is None and last_error is not None:
                raise last_error
            return SkillResult(
                success=False,
                provider="Bitefu CarApi",
                warnings=[str(body.get("info") or "车型库没有匹配结果") if isinstance(body, dict) else "车型库没有匹配结果"],
                error_code="CARINFO_NO_RESULTS",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        # A brand-only query is capped by the upstream at the newest 50 rows;
        # those rows can all predate the detail table. Expand a few established
        # series concurrently so concrete, detail-backed trims are available
        # without hard-coding any manufacturer or model name.
        series_requests: list[dict[str, str]] = []
        specific_query = _catalog_is_specific_query(request.query)
        brand_ids = _catalog_brand_ids_for_query(items, request.query)
        if brand_ids and not public_items and not specific_query:
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
            if series_queries and not public_items and not specific_query:
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
        # Public fallback rows already contain their parsed trim-page specs;
        # probing their synthetic ``cnc_`` ids against the primary API only
        # adds latency and can exhaust the 12-second Skill budget.  Restrict
        # the primary detail fan-out to rows that actually belong to Bitefu.
        public_source_ids = {
            str(item.get("source_id") or "")
            for item in public_items
        }
        primary_detail_items = [
            item
            for item in candidate_items
            if str(item.get("source_id") or "") not in public_source_ids
            and not str(item.get("source_id") or "").startswith("cnc_")
        ]
        async with httpx.AsyncClient(timeout=PRIMARY_CARINFO_DETAIL_TIMEOUT_SECONDS) as detail_client:
            details = await asyncio.gather(
                *(_fetch_catalog_detail(detail_client, item["source_id"]) for item in primary_detail_items),
                return_exceptions=True,
            )
        detail_by_id = {
            item["source_id"]: detail
            for item, detail in zip(primary_detail_items, details)
        }
        for item in candidate_items:
            detail = detail_by_id.get(item["source_id"])
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
        requested_model_key = _catalog_model_key(request.query)
        candidate_items.sort(
            key=lambda item: (
                _catalog_query_match_rank(item, requested_model_key),
                0 if item.get("specifications") else 1,
                *_catalog_sort_key(item),
            ),
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
        if public_items:
            data["fallback_used"] = True
            fallback_sources = {
                str(item.get("catalog_source") or "").strip()
                for item in public_items
                if str(item.get("catalog_source") or "").strip()
            }
            data["fallback_provider"] = "、".join(sorted(fallback_sources)) or "公开车型资料"
            data["fallback_note"] = "主车型目录未覆盖该车系，已动态补充公开年份/配置项；公开来源没有的字段保持为空，请以车辆合格证和官方配置为准。"
        sources: list[SourceRecord] = []
        if primary_status_ok:
            sources.append(
                SourceRecord(
                    provider="Bitefu CarApi",
                    title="汽车品牌/车系/车型数据库",
                    url=source_url,
                )
            )
        if public_items:
            if any("CarNewsChina" in str(item.get("catalog_source") or "") for item in public_items):
                sources.append(
                    SourceRecord(
                        provider="CarNewsChina",
                        title="公开车型年份与配置资料（次级检索）",
                        url=PUBLIC_CAR_SUGGEST_URL,
                        source_type="web_search",
                        confidence="medium",
                    )
                )
            if any("OpenEV" in str(item.get("catalog_source") or "") for item in public_items):
                sources.append(
                    SourceRecord(
                        provider="OpenEV Data",
                        title="开放电动车规格数据集（降级补全）",
                        url=OPEN_EVDB_VEHICLES_URL,
                        source_type="open_data",
                        confidence="medium",
                        license="CC BY-SA 4.0",
                    )
                )
            if any("AutoSeeker" in str(item.get("catalog_source") or "") for item in public_items):
                sources.append(
                    SourceRecord(
                        provider="AutoSeeker",
                        title="公开车型规格数据（降级补全）",
                        url=AUTOSEEKER_MODELS_URL,
                        source_type="open_data",
                        confidence="medium",
                        license="CC BY 4.0",
                    )
                )
            if any("AppByte" in str(item.get("catalog_source") or "") for item in public_items):
                sources.append(
                    SourceRecord(
                        provider="AppByte Fleet Catalog",
                        title="公开车型版本与动力规格数据（降级补全）",
                        url=APPBYTE_BASE_URL,
                        source_type="open_data",
                        confidence="medium",
                    )
                )
        return SkillResult(
            success=True,
            provider="Bitefu CarApi",
            data=data,
            sources=sources,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "configured": True,
            "provider": "Bitefu CarApi",
            "secondary_provider": "AutoSeeker + OpenEV Data + AppByte + CarNewsChina public catalogue",
        }


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


def _catalog_model_key(value: object) -> str:
    """Extract a trim-aware ASCII model key from a natural-language query.

    Chinese brand names are intentionally ignored here.  The key is used only
    to decide whether a provider result really covers a model family; for
    example ``SU7`` must not be considered covered by ``SU7 Ultra``.
    """
    text = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    match = re.search(r"([a-z]{1,20})\s*([0-9]{1,5})", text)
    if not match:
        return ""
    base = f"{match.group(1)}{match.group(2)}"
    qualifiers = (
        "ultra", "max", "pro", "plus", "standard", "performance",
        "四驱", "后驱", "标准", "性能", "长续航", "入门",
    )
    suffix = "".join(token for token in qualifiers if token in text)
    return f"{base}{suffix}"


def _catalog_needs_public_lookup(
    query: str,
    items: list[dict[str, Any]],
) -> bool:
    """Tell whether the public secondary catalogue should be consulted.

    A broad brand search stays on the primary source.  A model/trim search is
    widened only when no returned series has the same trim-aware key.  This is
    what catches the live ``SU7`` → ``SU7 Ultra`` data gap without adding
    latency to ordinary brand searches.
    """
    requested_key = _catalog_model_key(query)
    if not requested_key:
        if not items:
            return True
        # A Latin brand + model such as ``Volkswagen Golf`` has no numeric
        # model key, but it is still a concrete lookup. Let the public
        # catalogue resolve it instead of widening the primary brand search.
        if _catalog_is_specific_query(query):
            return not any(_catalog_text_identity_matches(query, item) for item in items)
        return False
    if not items:
        return True
    return not any(_catalog_item_matches_query(query, item) for item in items)


def _catalog_is_specific_query(query: str) -> bool:
    """Return whether text looks like a concrete model rather than a brand."""
    if _catalog_model_key(query):
        return True
    tokens = _autoseeker_query_tokens(query)
    return len(tokens) >= 2


def _catalog_text_identity_matches(query: str, item: dict[str, Any]) -> bool:
    tokens = _autoseeker_query_tokens(query)
    if not tokens:
        return False
    item_text = _public_compact_text(
        " ".join(
            str(item.get(field) or "")
            for field in ("brand", "series", "model")
        )
    )
    meaningful_tokens = [
        token for token in tokens if len(token) > 1 or token.isdigit()
    ]
    if not meaningful_tokens:
        return False
    model_family_match = re.search(
        r"\bmodel\s*([a-z0-9][a-z0-9+.-]*)",
        str(query or "").casefold(),
    )
    if model_family_match:
        family_token = _public_compact_text(model_family_match.group(1))
        if not family_token or f"model{family_token}" not in item_text:
            return False
    return all(_public_compact_text(token) in item_text for token in meaningful_tokens)


def _catalog_item_matches_query(query: str, item: dict[str, Any]) -> bool:
    """Require both model identity and a supplied Chinese brand hint.

    The primary index is a fuzzy suffix search: ``小鹏 G6`` can otherwise be
    satisfied by a 金杯 G6P van because both contain ``G6``. When the user
    supplied a CJK brand/model token, keep that token in the match decision so
    the fallback is allowed to replace an unrelated primary hit.
    """
    requested_key = _catalog_model_key(query)
    item_text = " ".join(
        str(item.get(field) or "")
        for field in ("brand", "series", "model")
    )
    item_key = _catalog_model_key(item_text)
    if not requested_key or item_key != requested_key:
        return False
    # The primary index also performs suffix matching.  A key such as ``G6``
    # can therefore be extracted from a ``G6P`` van or another make.  For
    # Latin input, require every meaningful query token in the returned
    # brand/series/model identity before treating it as an exact hit.
    latin_tokens = _autoseeker_query_tokens(query)
    if latin_tokens and not _catalog_text_identity_matches(query, item):
        return False
    cjk_tokens = re.findall(r"[\u4e00-\u9fff]{2,}", str(query or ""))
    if not cjk_tokens:
        return True
    item_compact = _public_compact_text(item_text)
    return any(_public_compact_text(token) in item_compact for token in cjk_tokens)


def _catalog_query_match_rank(
    item: dict[str, Any],
    requested_key: str,
) -> int:
    """Sort exact requested trims before family variants.

    A bare ``SU7`` search should show ordinary SU7 trims before the more
    specific Ultra variant that happened to be the only primary hit; a
    qualified ``SU7 Max`` query should put Max first.
    """
    if not requested_key:
        return 0
    item_key = _catalog_model_key(
        " ".join(
            str(item.get(field) or "")
            for field in ("brand", "series", "model")
        )
    )
    if item_key == requested_key:
        return 0
    if item_key.startswith(requested_key):
        return 2 if "ultra" in item_key and "ultra" not in requested_key else 1
    return 3


def _public_vehicle_seed_items(query: str) -> list[dict[str, Any]]:
    """Return source-linked emergency records for the Xiaomi SU7 family.

    The public suggestion service is free but not an SLA-backed API.  Keeping
    these three high-frequency trims means a temporary 5xx/rate-limit does not
    regress the vehicle drawer back to the misleading Ultra-only result.
    """
    requested_key = _catalog_model_key(query)
    if not requested_key or not requested_key.startswith("su7"):
        return []
    records = [
        {
            "suffix": "标准版",
            "source_id": "cnc_seed_su7_standard_2024",
            "model": "小米SU7 2024款 标准版",
            "range": 700.0,
            "battery": 73.6,
            "consumption": 12.3,
            "price": 215900.0,
            "height": 1.455,
            "dc_charge_hours": 0.4,
            "specs": [
                ("CLTC续航", "700 km"),
                ("电池容量", "73.6 kWh"),
                ("能耗", "12.3 kWh/100km"),
                ("直流快充（30-80%）", "0.4 hours"),
                ("驱动形式", "Rear"),
                ("最高车速", "210 km/h"),
                ("0-100 km/h 加速", "5.3 sec"),
                ("总功率", "220 kW"),
                ("总扭矩", "400 Nm"),
                ("车身尺寸（长/宽/高）", "4997/1963/1455 mm"),
                ("轴距", "3000 mm"),
                ("座位数", "5"),
                ("电池类型", "LFP"),
            ],
            "url": f"{PUBLIC_CAR_BASE_URL}/database/xiaomi-auto/xiaomi-auto-su7/2024/700km-220kw-0-22038",
        },
        {
            "suffix": "Pro版",
            "source_id": "cnc_seed_su7_pro_2024",
            "model": "小米SU7 2024款 Pro版",
            "range": 830.0,
            "battery": 94.3,
            "consumption": 12.9,
            "price": 245900.0,
            "height": 1.455,
            "dc_charge_hours": 0.5,
            "specs": [
                ("CLTC续航", "830 km"),
                ("电池容量", "94.3 kWh"),
                ("能耗", "12.9 kWh/100km"),
                ("直流快充（30-80%）", "0.5 hours"),
                ("驱动形式", "Rear"),
                ("最高车速", "210 km/h"),
                ("0-100 km/h 加速", "5.7 sec"),
                ("总功率", "220 kW"),
                ("总扭矩", "400 Nm"),
                ("车身尺寸（长/宽/高）", "4997/1963/1455 mm"),
                ("轴距", "3000 mm"),
                ("座位数", "5"),
                ("电池类型", "LFP"),
            ],
            "url": f"{PUBLIC_CAR_BASE_URL}/database/xiaomi-auto/xiaomi-auto-su7/2024/830km-220kw-0-22039",
        },
        {
            "suffix": "四驱Max版",
            "source_id": "cnc_seed_su7_max_2024",
            "model": "小米SU7 2024款 四驱Max版",
            "range": 800.0,
            "battery": 101.0,
            "consumption": 13.7,
            "price": 299900.0,
            "height": 1.44,
            "dc_charge_hours": 0.3,
            "specs": [
                ("CLTC续航", "800 km"),
                ("电池容量", "101 kWh"),
                ("能耗", "13.7 kWh/100km"),
                ("直流快充（30-80%）", "0.3 hours"),
                ("驱动形式", "AWD"),
                ("最高车速", "265 km/h"),
                ("0-100 km/h 加速", "2.8 sec"),
                ("总功率", "495 kW"),
                ("总扭矩", "838 Nm"),
                ("车身尺寸（长/宽/高）", "4997/1963/1440 mm"),
                ("轴距", "3000 mm"),
                ("座位数", "5"),
                ("电池类型", "Ternary NMC"),
            ],
            "url": f"{PUBLIC_CAR_BASE_URL}/database/xiaomi-auto/xiaomi-auto-su7/2024/800km-495kw-0-22040",
        },
    ]
    if requested_key.endswith("max"):
        records = [record for record in records if record["suffix"] == "四驱Max版"]
    elif requested_key.endswith("pro"):
        records = [record for record in records if record["suffix"] == "Pro版"]
    elif requested_key.endswith("ultra"):
        records = []
    result: list[dict[str, Any]] = []
    for record in records:
        source_url = str(record["url"])
        result.append(
            {
                "id": record["source_id"],
                "source_id": record["source_id"],
                "brand_id": "",
                "group_id": "",
                "series_id": "",
                "brand": "小米汽车",
                "series": "小米SU7",
                "model": record["model"],
                "year": 2024,
                "power_type": "electric",
                "rated_range_km": record["range"],
                "battery_kwh": record["battery"],
                "consumption_per_100km": record["consumption"],
                "max_charge_kw": None,
                "dc_charge_time_hours": record["dc_charge_hours"],
                "height_m": record["height"],
                "width_m": 1.963,
                "seats": 5,
                "current_energy_percent": 80,
                "safe_energy_reserve_percent": 15,
                "has_etc": False,
                "mountain_ready": True,
                "unpaved_ready": False,
                "state": "0",
                "state_label": "公开资料（年份页）",
                "price_min_cny": record["price"],
                "price_max_cny": record["price"],
                "source_url": source_url,
                "detail_source_url": source_url,
                "specifications": [
                    {"name": name, "value": value}
                    for name, value in record["specs"]
                ],
                "specs_missing": [],
                "estimated_fields": [],
                "catalog_source": "CarNewsChina 公开车型资料（搜索降级缓存）",
                "fallback_used": True,
            }
        )
    return result


async def _run_public_vehicle_lookup(
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Run the public fallback with its own client and a bounded deadline.

    Keeping the client inside this task means the primary lookup can run in
    parallel without sharing a socket that may be closed when the primary
    request returns.  The deadline is deliberately below the adapter budget;
    a slow public site therefore degrades to a transparent no-result instead
    of blocking the vehicle drawer indefinitely.
    """
    async with httpx.AsyncClient(
        timeout=PUBLIC_CAR_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as public_client:
        return await asyncio.wait_for(
            _fetch_public_vehicle_items(
                public_client,
                query,
                limit=limit,
            ),
            timeout=7.0,
        )


def _autoseeker_query_tokens(query: object) -> list[str]:
    """Extract searchable alphanumeric model/brand tokens without a list.

    Keep standalone digits (``Model 3``) so a bare family query cannot turn
    into ``Model S``/``Model Y`` merely because they share the word ``model``.
    Generic vehicle words are removed, but make/model/trim text remains fully
    data-driven.
    """
    text = str(query or "").casefold()
    tokens = re.findall(r"[a-z0-9][a-z0-9+.-]*", text)
    return [
        token
        for token in tokens
        if token not in {"auto", "car", "ev", "suv", "model"}
    ]


def _autoseeker_match_score(raw: dict[str, Any], query: str) -> int | None:
    """Score one AutoSeeker row against a user query.

    The matcher is deliberately data-driven: it compares the query with the
    brand/model/generation fields in the downloaded catalogue and never uses a
    list of special-cased manufacturers or models. ``None`` means no reliable
    match and prevents an unrelated suffix hit from reaching the UI.
    """
    brand = str(raw.get("merk") or "").casefold().strip()
    model = str(raw.get("model") or "").casefold().strip()
    generation = str(raw.get("generatie") or "").casefold().strip()
    haystack = _public_compact_text(" ".join((brand, model, generation)))
    identity_compact = _public_compact_text(" ".join((brand, model)))
    if not haystack:
        return None
    query_compact = _public_compact_text(query)
    tokens = _autoseeker_query_tokens(query)
    model_compact = _public_compact_text(model)
    model_family_match = re.search(
        r"\bmodel\s*([a-z0-9][a-z0-9+.-]*)",
        str(query or "").casefold(),
    )
    if model_family_match:
        # ``Model 3``/``Model S`` are families, not a loose numeric/letter
        # token. Require the complete ``model3``/``models`` sequence in the
        # catalogue identity so Model Y, BMW 3-series, etc. cannot leak in.
        family_token = _public_compact_text(model_family_match.group(1))
        if not family_token or f"model{family_token}" not in identity_compact:
            return None
    # Only use the compact substring shortcut against brand + model. Matching
    # the generation as well would turn ``G6`` into a false hit for ``G60``.
    if query_compact and query_compact in identity_compact:
        return 0
    meaningful_tokens = [
        token for token in tokens if len(token) > 1 or token.isdigit()
    ]
    if meaningful_tokens and all(token in identity_compact for token in meaningful_tokens):
        # Requiring every meaningful token in brand + model avoids treating
        # ``Model 3 Performance`` as an unrelated model whose generation text
        # merely mentions the number 3.  Numeric tokens are intentionally kept
        # so ``Model 3`` cannot broaden to Model S/Model Y.
        return 1
    requested_key = _catalog_model_key(query)
    if requested_key:
        base_key = re.sub(
            r"(ultra|max|pro|plus|standard|performance)$",
            "",
            requested_key,
        )
        if base_key and base_key in model_compact:
            return 2
    return None


def _autoseeker_power_type(fuels: object, specs: dict[str, Any]) -> str:
    values = [str(item).casefold() for item in fuels] if isinstance(fuels, list) else []
    values.append(str(specs.get("fuel_type") or "").casefold())
    text = " ".join(values)
    if re.search(r"ev|electric|bev|elektr", text):
        return "electric"
    if re.search(r"phev|hybrid|plug|hybride", text):
        return "hybrid"
    return "fuel"


def _autoseeker_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one AutoSeeker row to the vehicle-catalogue contract."""
    brand = str(raw.get("merk") or "").strip()
    model = str(raw.get("model") or "").strip()
    if not model:
        return None
    specs = raw.get("specs") if isinstance(raw.get("specs"), dict) else {}
    year = _integer(raw.get("bouwjaarVan"))
    fuel_values = raw.get("fuels")
    power_type = _autoseeker_power_type(fuel_values, specs)
    range_value = _positive_float(
        specs.get("wltp_range_km")
        or specs.get("epa_range_km")
        or specs.get("electric_range_km")
    )
    battery = _positive_float(specs.get("batterij_kwh") or specs.get("battery_kwh"))
    max_charge = _positive_float(
        specs.get("laadvermogen_dc_kw") or specs.get("dc_charging_kw")
    )
    consumption = _positive_float(
        specs.get("verbruik_wltp_kwh_100km")
        or specs.get("verbruik_wltp_l_100km")
        or specs.get("brandstof_benzine_l_100km")
        or specs.get("brandstof_diesel_l_100km")
    )
    seats_value = _positive_float(specs.get("zitplaatsen") or specs.get("seats"))
    seats = int(seats_value) if seats_value is not None else None
    length_mm = _positive_float(specs.get("lengte_mm") or specs.get("length_mm"))
    width_mm = _positive_float(specs.get("breedte_mm") or specs.get("width_mm"))
    height_mm = _positive_float(specs.get("hoogte_mm") or specs.get("height_mm"))
    model_year = year or _integer(raw.get("bouwjaarTot"))
    generation = str(raw.get("generatie") or "").strip()
    display_model = f"{model} {model_year}" if model_year else model
    slug = str(raw.get("slug") or "").strip()
    source_id = "autoseeker_" + (slug or hashlib.sha1(display_model.encode()).hexdigest()[:18])
    source_url = f"{AUTOSEEKER_MODELS_URL}#{slug}" if slug else AUTOSEEKER_MODELS_URL
    specifications: list[dict[str, str]] = []

    def add(name: str, value: object, suffix: str = "") -> None:
        if value is None or value == "":
            return
        specifications.append({
            "name": name,
            "value": f"{value}{suffix}" if suffix else str(value),
        })

    add("WLTP range", range_value, " km")
    add("Battery", battery, " kWh")
    add("Consumption", consumption, " kWh/100km" if "kwh" in " ".join(specs).casefold() else " L/100km")
    add("DC charging", max_charge, " kW")
    add("Seats", seats)
    add("Power", specs.get("vermogen_pk"), " hp")
    add("0-100 km/h", specs.get("acceleratie_0_100_s"), " s")
    add("Top speed", specs.get("topsnelheid_kmh"), " km/h")
    add("Body", specs.get("carrosserie"))
    add("Dimensions", "/".join(
        str(int(value)) if float(value).is_integer() else str(value)
        for value in (length_mm, width_mm, height_mm)
        if value is not None
    ), " mm" if all(value is not None for value in (length_mm, width_mm, height_mm)) else "")
    add("Curb weight", specs.get("massa_rijklaar_kg"), " kg")
    add("Boot", specs.get("kofferbak_liter"), " L")
    item = {
        "id": source_id,
        "source_id": source_id,
        "brand_id": "",
        "group_id": "",
        "series_id": "",
        "brand": brand or "Open vehicle data",
        "series": model,
        "model": display_model,
        "year": model_year,
        "power_type": power_type,
        "rated_range_km": range_value,
        "battery_kwh": battery,
        "consumption_per_100km": consumption,
        "max_charge_kw": max_charge,
        "dc_charge_time_hours": None,
        "height_m": height_mm / 1000 if height_mm is not None else None,
        "width_m": width_mm / 1000 if width_mm is not None else None,
        "seats": seats,
        "current_energy_percent": 80,
        "safe_energy_reserve_percent": 15,
        "has_etc": False,
        "mountain_ready": True,
        "unpaved_ready": False,
        "state": "0",
        "state_label": "Public catalogue data",
        "price_min_cny": None,
        "price_max_cny": None,
        "source_url": source_url,
        "detail_source_url": source_url,
        "specifications": specifications,
        "specs_missing": [],
        "estimated_fields": ["rated_range_km"] if range_value is not None else [],
        "catalog_source": "AutoSeeker public vehicle data (fallback)",
        "fallback_used": True,
    }
    item["specs_missing"] = _missing_specs(item)
    return item


async def _fetch_autoseeker_items(
    client: httpx.AsyncClient,
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch and match the public AutoSeeker JSON catalogue."""
    try:
        response = await client.get(AUTOSEEKER_MODELS_URL)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return []
    rows = body.get("models") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return []
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        score = _autoseeker_match_score(raw, query)
        if score is None:
            continue
        item = _autoseeker_item(raw)
        if item is None:
            continue
        candidates.append((score, -int(item.get("year") or 0), item))
    candidates.sort(key=lambda value: (value[0], value[1], str(value[2].get("model") or "")))
    return [item for _, _, item in candidates[: min(max(limit, 1), AUTOSEEKER_MAX_ROWS)]]


def _appbyte_query_tokens(query: object) -> list[str]:
    """Return make/model tokens suitable for the AppByte REST index."""
    text = str(query or "").casefold()
    tokens = re.findall(r"[a-z0-9][a-z0-9+.-]*", text)
    return [
        token
        for token in tokens
        if token not in {"auto", "car", "ev", "suv", "model"}
    ]


def _appbyte_compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


async def _appbyte_get_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, object] | None = None,
) -> dict[str, Any] | None:
    try:
        response = await client.get(f"{APPBYTE_BASE_URL}{path}", params=params)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


def _appbyte_make_score(name: object, token: str) -> int | None:
    name_key = _appbyte_compact(name)
    token_key = _appbyte_compact(token)
    if not name_key or not token_key:
        return None
    if name_key == token_key:
        return 0
    if token_key in name_key or name_key in token_key:
        return 1
    return None


def _appbyte_model_score(name: object, tokens: list[str]) -> int | None:
    name_key = _appbyte_compact(name)
    if not name_key or not tokens:
        return None
    compact_tokens = [_appbyte_compact(token) for token in tokens if token]
    if compact_tokens and all(token in name_key for token in compact_tokens):
        return 0 if " ".join(tokens).casefold() in str(name or "").casefold() else 1
    return None


async def _fetch_appbyte_items(
    client: httpx.AsyncClient,
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Resolve concrete versions from AppByte's public REST catalogue.

    The endpoint is intentionally used as an optional source.  If a make,
    model, variant list or detail request is unavailable, the function simply
    returns the records it could verify; it never manufactures a range or
    consumption number.
    """
    tokens = _appbyte_query_tokens(query)
    if not tokens:
        return []
    make_groups = await asyncio.gather(
        *(
            _appbyte_get_json(
                client,
                "/v1/makes",
                params={"search": token, "pageSize": APPBYTE_MAX_MAKES},
            )
            for token in tokens[:3]
        ),
        return_exceptions=True,
    )
    make_candidates: list[tuple[int, dict[str, Any], str]] = []
    for token, group in zip(tokens[:3], make_groups):
        if not isinstance(group, dict):
            continue
        rows = group.get("items")
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            score = _appbyte_make_score(raw.get("name"), token)
            if score is not None:
                make_candidates.append((score, raw, token))
    if not make_candidates:
        return []
    make_candidates.sort(key=lambda value: (value[0], str(value[1].get("name") or "")))
    make_rows: list[tuple[dict[str, Any], str]] = []
    seen_makes: set[str] = set()
    for _, raw, token in make_candidates:
        make_id = str(raw.get("id") or "")
        if not make_id or make_id in seen_makes:
            continue
        seen_makes.add(make_id)
        make_rows.append((raw, token))
        if len(make_rows) >= APPBYTE_MAX_MAKES:
            break

    model_tokens_by_make = {
        str(raw.get("id")): [token for token in tokens if token != make_token]
        for raw, make_token in make_rows
    }
    model_groups = await asyncio.gather(
        *(
            _appbyte_get_json(
                client,
                f"/v1/makes/{raw.get('id')}/models",
                params={
                    "search": " ".join(model_tokens_by_make.get(str(raw.get("id")), []))
                    or str(raw.get("name") or ""),
                    "pageSize": 100,
                },
            )
            for raw, _ in make_rows
        ),
        return_exceptions=True,
    )
    model_rows: list[tuple[dict[str, Any], str, list[str]]] = []
    seen_models: set[str] = set()
    for (make_raw, make_token), group in zip(make_rows, model_groups):
        if not isinstance(group, dict):
            continue
        rows = group.get("items")
        if not isinstance(rows, list):
            continue
        model_tokens = model_tokens_by_make.get(str(make_raw.get("id")), [])
        scored: list[tuple[int, dict[str, Any]]] = []
        for raw in rows:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            score = _appbyte_model_score(raw.get("name"), model_tokens)
            if score is not None:
                scored.append((score, raw))
        scored.sort(key=lambda value: (value[0], str(value[1].get("name") or "")))
        for _, raw in scored[:APPBYTE_MAX_MODELS]:
            model_id = str(raw.get("id") or "")
            if not model_id or model_id in seen_models:
                continue
            seen_models.add(model_id)
            model_rows.append((raw, str(make_raw.get("name") or ""), model_tokens))

    if not model_rows:
        return []
    variant_groups = await asyncio.gather(
        *(
            _appbyte_get_json(
                client,
                f"/v1/models/{model.get('id')}/variants",
                params={"pageSize": 100},
            )
            for model, _, _ in model_rows
        ),
        return_exceptions=True,
    )
    variant_rows: list[tuple[dict[str, Any], str, str, int]] = []
    for (model, make_name, model_tokens), group in zip(model_rows, variant_groups):
        if not isinstance(group, dict):
            continue
        rows = group.get("items")
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            variant_name = str(raw.get("name") or "")
            qualifier_tokens = [token for token in model_tokens if token not in {"ev"}]
            variant_score = 0 if qualifier_tokens and all(
                _appbyte_compact(token) in _appbyte_compact(variant_name)
                for token in qualifier_tokens
                if len(token) > 2
            ) else 1
            year = _integer(raw.get("yearFrom")) or 0
            variant_rows.append((raw, make_name, str(model.get("name") or ""), variant_score))
    variant_rows.sort(
        key=lambda value: (
            value[3],
            -(_integer(value[0].get("yearFrom")) or 0),
            str(value[0].get("name") or ""),
        )
    )
    variant_rows = variant_rows[: min(max(limit, 1), APPBYTE_MAX_VARIANTS)]
    details = await asyncio.gather(
        *(
            _appbyte_get_json(client, f"/v1/variants/{raw.get('id')}")
            for raw, _, _, _ in variant_rows
        ),
        return_exceptions=True,
    )
    result: list[dict[str, Any]] = []
    for (variant, make_name, model_name, _), detail in zip(variant_rows, details):
        if not isinstance(detail, dict):
            continue
        item = _appbyte_item(detail, brand=make_name, series=model_name)
        if item is not None:
            result.append(item)
    return result


def _appbyte_item(
    raw: dict[str, Any],
    *,
    brand: str,
    series: str,
) -> dict[str, Any] | None:
    variant_id = str(raw.get("id") or "").strip()
    variant_name = str(raw.get("name") or "").strip()
    if not variant_id or not series or not variant_name:
        return None
    fuel_text = str(raw.get("fuelType") or "").casefold()
    if "electric" in fuel_text or fuel_text in {"bev", "ev"}:
        power_type = "electric"
    elif any(token in fuel_text for token in ("hybrid", "phev", "plug")):
        power_type = "hybrid"
    else:
        power_type = "fuel"
    year = _integer(raw.get("yearFrom"))
    year_to = _integer(raw.get("yearTo"))
    battery = _positive_float(raw.get("batteryKwh"))
    electric_range = _positive_float(raw.get("electricRangeKm"))
    charging_kw = _positive_float(raw.get("chargingKw"))
    economy = _positive_float(raw.get("fuelEconomyCombinedL100"))
    tank = _positive_float(raw.get("fuelTankLitres"))
    estimated_fields: list[str] = []
    range_km = electric_range
    if range_km is None and tank is not None and economy is not None:
        range_km = tank * 100 / economy
        estimated_fields.append("rated_range_km")
    specifications: list[dict[str, str]] = []

    def add(name: str, value: object, suffix: str = "") -> None:
        if value is None or value == "":
            return
        specifications.append({
            "name": name,
            "value": f"{value}{suffix}" if suffix else str(value),
        })

    add("Fuel type", raw.get("fuelType"))
    add("Combined consumption", economy, " L/100km")
    add("Fuel tank", tank, " L")
    add("Electric range", electric_range, " km")
    add("Battery", battery, " kWh")
    add("DC charging", charging_kw, " kW")
    add("Power", raw.get("powerKw"), " kW")
    add("Power", raw.get("powerBhp"), " hp")
    add("Torque", raw.get("torqueNm"), " Nm")
    add("Drive", raw.get("driveType"))
    add("Gearbox", raw.get("gearboxType"))
    add("0-100 km/h", raw.get("acceleration0100Kph"), " s")
    add("Top speed", raw.get("topSpeedKph"), " km/h")
    add("Dimensions", "/".join(
        str(int(value)) if float(value).is_integer() else str(value)
        for value in (
            _positive_float(raw.get("lengthMm")),
            _positive_float(raw.get("widthMm")),
            _positive_float(raw.get("heightMm")),
        )
        if value is not None
    ), " mm")
    add("Seats", raw.get("numberOfSeats"))
    add("Curb weight", raw.get("weightKg"), " kg")
    add("Boot", raw.get("bootLitres"), " L")
    display_model = f"{series} {variant_name}".strip()
    if year:
        display_model += f" {year}"
    source_url = f"{APPBYTE_BASE_URL}/v1/variants/{variant_id}"
    item = {
        "id": f"appbyte_{variant_id}",
        "source_id": f"appbyte_{variant_id}",
        "brand_id": "",
        "group_id": "",
        "series_id": "",
        "brand": brand or "Public vehicle data",
        "series": series,
        "model": display_model,
        "year": year,
        "power_type": power_type,
        "rated_range_km": range_km,
        "battery_kwh": battery,
        "consumption_per_100km": economy,
        "max_charge_kw": charging_kw,
        "dc_charge_time_hours": None,
        "height_m": _positive_float(raw.get("heightMm")) / 1000 if _positive_float(raw.get("heightMm")) is not None else None,
        "width_m": _positive_float(raw.get("widthMm")) / 1000 if _positive_float(raw.get("widthMm")) is not None else None,
        "seats": int(_positive_float(raw.get("numberOfSeats"))) if _positive_float(raw.get("numberOfSeats")) is not None else None,
        "current_energy_percent": 80,
        "safe_energy_reserve_percent": 15,
        "has_etc": False,
        "mountain_ready": True,
        "unpaved_ready": False,
        "state": "0",
        "state_label": "Public catalogue data",
        "price_min_cny": None,
        "price_max_cny": None,
        "source_url": source_url,
        "detail_source_url": source_url,
        "specifications": specifications,
        "specs_missing": [],
        "estimated_fields": estimated_fields,
        "catalog_source": "AppByte Fleet Catalog public data (fallback)",
        "fallback_used": True,
    }
    item["specs_missing"] = _missing_specs(item)
    return item


def _public_catalog_search_queries(query: str) -> list[str]:
    """Build a few free-catalogue search variants for Chinese model names."""
    normalized = re.sub(r"\s+", " ", str(query or "").strip())
    if not normalized:
        return []
    model_key = _catalog_model_key(normalized)
    variants = [normalized]
    if model_key:
        # A trim qualifier is often not indexed by the suggestion endpoint;
        # the base family query returns the model page containing all trims.
        base_key = re.sub(
            r"(ultra|max|pro|plus|standard|performance)$",
            "",
            model_key,
        )
        variants.extend([base_key.upper(), model_key.upper()])
        if "小米" in normalized or "xiaomi" in normalized.casefold():
            variants.append(f"Xiaomi {base_key.upper()}")
    # Keep the provider query human-shaped as well as compact.  Some public
    # indexes match ``Model 3`` but not ``MODEL3``; extracting the alphanumeric
    # model token gives the fallback a chance without a manufacturer allowlist.
    for token in re.findall(r"[A-Za-z]{1,24}\s*\d{1,5}", normalized):
        variants.append(re.sub(r"\s+", " ", token).strip())
    for token in re.findall(r"[A-Za-z]{2,24}", normalized):
        if token.casefold() not in {"auto", "car", "ev", "suv"}:
            variants.append(token)
    result: list[str] = []
    for variant in variants:
        if variant and variant not in result:
            result.append(variant)
        if len(result) >= 5:
            break
    return result


async def _bounded_public_fetch(
    awaitable: Any,
    timeout: float,
) -> list[dict[str, Any]]:
    """Return a public-source result or an empty list on transient failure."""
    try:
        result = await asyncio.wait_for(awaitable, timeout=timeout)
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError):
        return []
    return result if isinstance(result, list) else []


async def _fetch_public_vehicle_items(
    client: httpx.AsyncClient,
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Search the no-key public EV catalogue and normalize trim pages.

    The public endpoint is intentionally a fallback, not a second mandatory
    dependency.  It provides a useful path for models absent from the main
    Chinese index (notably Xiaomi SU7 Standard/Pro/Max) and every normalized
    item keeps both the model page and the concrete trim page as evidence.
    """
    queries = _public_catalog_search_queries(query)
    if not queries:
        return []
    # Query the structured sources in parallel. AutoSeeker covers EV, hybrid
    # and combustion models while EVDB contributes additional EV trims and
    # charging data. AppByte adds version-level fuel/hybrid details. A complete
    # AutoSeeker/EVDB row wins when it covers the requested trim; AppByte is
    # then used for a concrete version that the other datasets do not contain.
    structured_groups = await asyncio.gather(
        _bounded_public_fetch(
            _fetch_autoseeker_items(client, query, limit=limit),
            AUTOSEEKER_TIMEOUT_SECONDS,
        ),
        _bounded_public_fetch(
            _fetch_open_evdb_items(client, query, limit=limit),
            OPEN_EVDB_TIMEOUT_SECONDS,
        ),
        _bounded_public_fetch(
            _fetch_appbyte_items(client, query, limit=limit),
            APPBYTE_TIMEOUT_SECONDS,
        ),
        return_exceptions=True,
    )
    autoseeker_items = structured_groups[0] if isinstance(structured_groups[0], list) else []
    open_items = structured_groups[1] if isinstance(structured_groups[1], list) else []
    appbyte_items = structured_groups[2] if isinstance(structured_groups[2], list) else []
    if autoseeker_items:
        requested_key = _catalog_model_key(query)
        # If the user named a concrete trim (for example ``Performance``),
        # prefer a source that actually contains that qualifier instead of
        # silently downgrading to the base model from a broader catalogue.
        auto_covers_trim = not requested_key or any(
            _catalog_model_key(item.get("model")) == requested_key
            for item in autoseeker_items
        )
        if auto_covers_trim or not open_items:
            if auto_covers_trim or not appbyte_items:
                return autoseeker_items
    if open_items:
        return open_items
    if appbyte_items:
        return appbyte_items
    if autoseeker_items:
        return autoseeker_items
    suggestion_groups = await asyncio.gather(
        *(_fetch_public_suggestions(client, search_query) for search_query in queries),
        return_exceptions=True,
    )
    model_refs: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    for group in suggestion_groups:
        if not isinstance(group, list):
            continue
        for ref in group:
            slug = str(ref.get("slug") or "").strip()
            if not slug or slug in seen_models:
                continue
            seen_models.add(slug)
            model_refs.append(ref)
            if len(model_refs) >= PUBLIC_CAR_MAX_MODELS:
                break
        if len(model_refs) >= PUBLIC_CAR_MAX_MODELS:
            break
    if not model_refs:
        # The structured dataset is independent of the HTML suggestion
        # endpoint, so it can still answer a query during a suggestion
        # service outage or CAPTCHA response.
        try:
            return await _bounded_public_fetch(
                _fetch_open_evdb_items(client, query, limit=limit),
                OPEN_EVDB_TIMEOUT_SECONDS,
            )
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError):
            return []

    trim_groups = await asyncio.gather(
        *(_fetch_public_model_trims(client, ref) for ref in model_refs),
        return_exceptions=True,
    )
    trim_refs: list[dict[str, Any]] = []
    seen_trims: set[str] = set()
    for group in trim_groups:
        if not isinstance(group, list):
            continue
        for trim in group:
            href = str(trim.get("url") or "").strip()
            if not href or href in seen_trims:
                continue
            seen_trims.add(href)
            trim_refs.append(trim)
    matching = [
        trim
        for trim in trim_refs
        if _public_trim_matches_query(trim.get("name"), query)
    ]
    # A base-family query such as SU7 intentionally returns all trims.  If a
    # qualifier query has no exact public match, retaining the family is more
    # useful than showing only the primary provider's wrong variant.
    if matching:
        trim_refs = matching
    trim_refs = trim_refs[:PUBLIC_CAR_MAX_TRIMS]
    detail_groups = await asyncio.gather(
        *(_fetch_public_trim_item(client, trim) for trim in trim_refs),
        return_exceptions=True,
    )
    items = [item for item in detail_groups if isinstance(item, dict)]
    # A web page can be reachable but still return a CAPTCHA/identity shell.
    # Supplement incomplete results from the open JSON dataset instead of
    # silently returning a name-only fallback. Both sources remain attached
    # to each item so the user can verify the exact trim and market.
    if not items or any(_missing_specs(item) for item in items):
        try:
            open_items = await _bounded_public_fetch(
                _fetch_open_evdb_items(client, query, limit=limit),
                OPEN_EVDB_TIMEOUT_SECONDS,
            )
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError):
            open_items = []
        seen_sources = {str(item.get("source_id") or "") for item in items}
        for item in open_items:
            source_id = str(item.get("source_id") or "")
            if source_id and source_id not in seen_sources:
                seen_sources.add(source_id)
                items.append(item)
    return items[: max(limit, PUBLIC_CAR_MAX_TRIMS)]


async def _fetch_public_suggestions(
    client: httpx.AsyncClient,
    query: str,
) -> list[dict[str, Any]]:
    try:
        response = await client.get(PUBLIC_CAR_SUGGEST_URL, params={"q": query})
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return []
    models = body.get("models") if isinstance(body, dict) else None
    return [model for model in models if isinstance(model, dict)] if isinstance(models, list) else []


async def _fetch_open_evdb_items(
    client: httpx.AsyncClient,
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Query the open EVDB JSON index and normalize matching variants.

    EVDB is intentionally a complementary source: it has structured battery,
    WLTP/real-world range, DC power and dimensions, but it does not cover every
    Chinese-market trim. We never infer missing consumption or seating values;
    the typed fields stay null and ``specs_missing`` tells the UI what needs
    confirmation.
    """
    try:
        response = await client.get(OPEN_EVDB_VEHICLES_URL)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return []
    rows = body.get("results") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return []
    query_key = _public_compact_text(query)
    query_model_key = _catalog_model_key(query)
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        brand = str(raw.get("brand") or "").strip()
        model = str(raw.get("model") or "").strip()
        variant = str(raw.get("variant_name") or "").strip()
        if not model:
            continue
        haystack = _public_compact_text(" ".join((brand, model, variant)))
        if query_model_key:
            # ``Model 3`` should match the family while ``Model 3 Performance``
            # is ranked ahead of unrelated variants. The compact query check
            # still works for all-Latin make/model names.
            model_compact = _public_compact_text(model)
            if query_model_key not in model_compact and query_key not in haystack:
                continue
        elif query_key and query_key not in haystack:
            continue
        range_wltp = _positive_float(raw.get("range_wltp_km"))
        range_real = _positive_float(raw.get("range_real_world_km"))
        battery = _positive_float(raw.get("battery_usable_kwh"))
        dc_power = _positive_float(raw.get("dc_charge_power_kw"))
        power = _positive_float(raw.get("total_power_kw"))
        acceleration = _positive_float(raw.get("acceleration_0_100_sec"))
        top_speed = _positive_float(raw.get("top_speed_kph"))
        length_mm = _positive_float(raw.get("length_mm"))
        width_mm = _positive_float(raw.get("width_mm"))
        height_mm = _positive_float(raw.get("height_mm"))
        year = _integer(raw.get("model_year"))
        item = _open_evdb_item(
            raw,
            brand=brand,
            model=model,
            variant=variant,
            year=year,
            range_wltp=range_wltp,
            range_real=range_real,
            battery=battery,
            dc_power=dc_power,
            power=power,
            acceleration=acceleration,
            top_speed=top_speed,
            length_mm=length_mm,
            width_mm=width_mm,
            height_mm=height_mm,
        )
        exact_rank = 0 if query_key and query_key in haystack else 1
        year_rank = -(year or 0)
        candidates.append((exact_rank, year_rank, item))
    candidates.sort(key=lambda entry: (entry[0], entry[1], str(entry[2].get("model") or "")))
    return [item for _, _, item in candidates[: min(max(limit, 1), OPEN_EVDB_MAX_ROWS)]]


def _open_evdb_item(
    raw: dict[str, Any],
    *,
    brand: str,
    model: str,
    variant: str,
    year: int | None,
    range_wltp: float | None,
    range_real: float | None,
    battery: float | None,
    dc_power: float | None,
    power: float | None,
    acceleration: float | None,
    top_speed: float | None,
    length_mm: float | None,
    width_mm: float | None,
    height_mm: float | None,
) -> dict[str, Any]:
    source_id = str(raw.get("id") or "").strip()
    if not source_id:
        source_id = hashlib.sha1(
            f"{brand}|{model}|{variant}|{year}".encode("utf-8")
        ).hexdigest()[:18]
    source_id = f"evdb_{source_id}"
    source_url = f"https://gaia-charge.github.io/evdb/v1/vehicles/{source_id[5:]}.json"
    display_model = " ".join(part for part in (model, variant) if part).strip()
    if year:
        display_model = f"{display_model} {year}"
    specifications: list[dict[str, str]] = []

    def add(name: str, value: object, suffix: str = "") -> None:
        if value is None or value == "":
            return
        text = f"{value}{suffix}" if suffix else str(value)
        specifications.append({"name": name, "value": text})

    add("WLTP续航", range_wltp, " km")
    add("实测续航参考", range_real, " km")
    add("可用电池容量", battery, " kWh")
    add("直流快充功率", dc_power, " kW")
    add("总功率", power, " kW")
    add("驱动形式", raw.get("drive_type"))
    add("0-100 km/h 加速", acceleration, " sec")
    add("最高车速", top_speed, " km/h")
    add("车身形式", raw.get("body_style"))
    add("车身尺寸（长/宽/高）", "/".join(
        str(int(value)) if float(value).is_integer() else str(value)
        for value in (length_mm, width_mm, height_mm)
        if value is not None
    ), " mm" if all(value is not None for value in (length_mm, width_mm, height_mm)) else "")
    add("整备质量", raw.get("weight_curb_kg"), " kg")
    add("后备厢容积", raw.get("trunk_capacity_liters"), " L")
    range_value = range_wltp or range_real
    estimated_fields = ["rated_range_km"] if range_wltp is not None else []
    item = {
        "id": source_id,
        "source_id": source_id,
        "brand_id": "",
        "group_id": "",
        "series_id": "",
        "brand": brand or "Unknown",
        "series": model,
        "model": display_model,
        "year": year,
        "power_type": "electric",
        "rated_range_km": range_value,
        "battery_kwh": battery,
        "consumption_per_100km": None,
        "max_charge_kw": dc_power,
        "dc_charge_time_hours": None,
        "height_m": height_mm / 1000 if height_mm is not None else None,
        "width_m": width_mm / 1000 if width_mm is not None else None,
        "seats": None,
        "current_energy_percent": 80,
        "safe_energy_reserve_percent": 15,
        "has_etc": False,
        "mountain_ready": True,
        "unpaved_ready": False,
        "state": "0",
        "state_label": "公开资料（EVDB）",
        "price_min_cny": None,
        "price_max_cny": None,
        "source_url": source_url,
        "detail_source_url": source_url,
        "specifications": specifications,
        "specs_missing": [],
        "estimated_fields": estimated_fields,
        "catalog_source": "OpenEV Data 公开数据集（降级查询）",
        "fallback_used": True,
    }
    item["specs_missing"] = _missing_specs(item)
    return item


def _public_compact_text(value: object) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").casefold())


def _positive_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def _fetch_public_model_trims(
    client: httpx.AsyncClient,
    model_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    slug = str(model_ref.get("slug") or "").strip("/")
    if not slug:
        return []
    # The model landing page is normally the newest year, while an older
    # year page can contain a trim that is still missing from the landing
    # page. Query both in parallel and merge links; this is generic for every
    # model returned by the public suggestion endpoint rather than a SU7-only
    # list of URLs.
    urls = [
        f"{PUBLIC_CAR_BASE_URL}/database/{slug}",
        f"{PUBLIC_CAR_BASE_URL}/database/{slug}/2025",
        f"{PUBLIC_CAR_BASE_URL}/database/{slug}/2024",
    ]
    responses = await asyncio.gather(
        *(_fetch_public_model_page(client, url) for url in urls),
        return_exceptions=True,
    )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for response in responses:
        if not isinstance(response, tuple):
            continue
        page, response_url = response
        for trim in _parse_public_trim_links(page, response_url, model_ref):
            href = str(trim.get("url") or "").strip()
            if not href or href in seen:
                continue
            seen.add(href)
            result.append(trim)
    return result


async def _fetch_public_model_page(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[str, str] | None:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    return response.text, str(response.url)


def _parse_public_trim_links(
    page: str,
    model_url: str,
    model_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    pattern = re.compile(
        r'<a\s+class="trim"\s+href="(?P<href>[^"]+)"[^>]*>\s*'
        r'<b[^>]*>(?P<name>.*?)</b>',
        re.IGNORECASE | re.DOTALL,
    )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in pattern.finditer(page or ""):
        url = urljoin(model_url, html.unescape(match.group("href")).strip())
        name = _clean_detail_text(match.group("name"))
        if not url or not name or url in seen:
            continue
        seen.add(url)
        result.append({"url": url, "name": name, "model_ref": model_ref, "model_url": model_url})
    return result


def _public_trim_matches_query(name: object, query: str) -> bool:
    requested_key = _catalog_model_key(query)
    if not requested_key:
        return True
    trim_key = _catalog_model_key(name)
    if not trim_key:
        return False
    # Base model queries (SU7) deliberately include Standard/Pro/Max/Ultra;
    # qualified queries only include that specific family variant.
    return trim_key == requested_key or trim_key.startswith(requested_key)


async def _fetch_public_trim_item(
    client: httpx.AsyncClient,
    trim_ref: dict[str, Any],
) -> dict[str, Any] | None:
    url = str(trim_ref.get("url") or "").strip()
    if not url:
        return None
    try:
        response = await client.get(url)
        response.raise_for_status()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    return _parse_public_trim_page(
        response.text,
        str(response.url),
        trim_ref,
    )


def _parse_public_trim_page(
    page: str,
    trim_url: str,
    trim_ref: dict[str, Any],
) -> dict[str, Any] | None:
    title_match = re.search(
        r'<h1[^>]*class="[^"]*h2[^"]*"[^>]*>(?P<title>.*?)</h1>',
        page or "",
        re.IGNORECASE | re.DOTALL,
    )
    title = _clean_detail_text(title_match.group("title")) if title_match else str(trim_ref.get("name") or "").strip()
    if not title:
        return None
    row_pattern = re.compile(
        r'<div\s+class="table__row">\s*'
        r'<div\s+class="table__cell\s+table__cell-param-name">(?P<name>.*?)</div>.*?'
        r'<div\s+class="table__cell[^>]*>\s*(?P<value>[^<]+)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    specs: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    for match in row_pattern.finditer(page or ""):
        name = _clean_detail_text(match.group("name"))
        value = _clean_detail_text(match.group("value"))
        if not name or not value or name in seen_names:
            continue
        seen_names.add(name)
        specs.append((name, value))
    values = {name: value for name, value in specs}
    range_km = _public_number(
        values,
        "Range (CLTC)",
        "CLTC electric range (mfr)",
        "Range (WLTC)",
        "Range (NEDC)",
    )
    if range_km is None:
        range_km = _public_number_contains(values, "range")
    battery_kwh = _public_number(values, "Battery capacity")
    if battery_kwh is None:
        battery_kwh = _public_number_contains(values, "battery", "capacity")
    consumption = _public_number(values, "Consumption")
    if consumption is None:
        consumption = _public_number_contains(values, "consumption")
    dc_charge_time_hours = _public_number_contains(
        values,
        "charging",
        "30-80",
    )
    if dc_charge_time_hours is None:
        dc_charge_time_hours = _public_number_contains(
            values,
            "charging time",
            "30-80",
        )
    max_charge_kw = _public_number_by_predicate(
        values,
        lambda name: bool(
            re.search(
                r"(?:max(?:imum)?|peak|最大).*?(?:charge|charging|充电).*?(?:power|功率)",
                name.casefold(),
            )
        ),
    )
    fuel_type = str(values.get("Fuel type") or "")
    power_type = _public_power_type(fuel_type, title)
    year_match = re.search(r"20\d{2}", title)
    year = int(year_match.group()) if year_match else None
    brand, series = _public_brand_series(trim_ref.get("model_ref"), title)
    model = _public_trim_name(series, title, year)
    dimensions = values.get("L/W/H") or values.get("Length/Width/Height") or ""
    dimension_numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", dimensions)]
    width_m = dimension_numbers[1] / 1000 if len(dimension_numbers) >= 2 else None
    height_m = dimension_numbers[2] / 1000 if len(dimension_numbers) >= 3 else None
    seats_value = _public_number(
        values,
        "Number of seats",
        "Seats",
        "Seating capacity",
        "座位数",
    )
    seats = int(seats_value) if seats_value is not None and seats_value >= 1 else None
    price = _public_number(values, "MSRP at launch")
    source_id = "cnc_" + hashlib.sha1(trim_url.encode("utf-8")).hexdigest()[:18]
    item = {
        "id": f"carnews_{source_id[4:]}",
        "source_id": source_id,
        "brand_id": "",
        "group_id": "",
        "series_id": "",
        "brand": brand,
        "series": series,
        "model": model,
        "year": year,
        "power_type": power_type,
        "rated_range_km": range_km,
        "battery_kwh": battery_kwh,
        "consumption_per_100km": consumption,
        "max_charge_kw": max_charge_kw,
        "dc_charge_time_hours": dc_charge_time_hours,
        "height_m": height_m,
        "width_m": width_m,
        "seats": seats,
        "current_energy_percent": 80,
        "safe_energy_reserve_percent": 15,
        "has_etc": False,
        "mountain_ready": True,
        "unpaved_ready": False,
        "state": "0",
        "state_label": "公开资料（年份页）",
        "price_min_cny": price,
        "price_max_cny": price,
        "source_url": str(trim_ref.get("model_url") or trim_url),
        "detail_source_url": trim_url,
        "specifications": [{"name": name, "value": value} for name, value in specs[:120]],
        "specs_missing": [],
        "estimated_fields": [],
        "catalog_source": "CarNewsChina 公开车型资料",
        "fallback_used": True,
    }
    item["specs_missing"] = _missing_specs(item)
    return item


def _public_number(values: dict[str, str], *names: str) -> float | None:
    for name in names:
        if name in values:
            number = _number_from_text(values[name])
            if number is not None:
                return number
    return None


def _public_number_contains(values: dict[str, str], *needles: str) -> float | None:
    """Read the first numeric value from a public row containing all needles.

    Public vehicle pages have changed labels over time (for example
    ``DC charging (30-80%)`` vs. ``Charging time (30–80%)``).  Matching the
    label rather than one exact spelling keeps the fallback useful without
    guessing a value when the page omits it.
    """
    normalized_needles = tuple(str(needle).casefold().replace("–", "-") for needle in needles)
    for name, value in values.items():
        normalized_name = str(name).casefold().replace("–", "-")
        if all(needle in normalized_name for needle in normalized_needles):
            number = _number_from_text(value)
            if number is not None:
                return number
    return None


def _public_number_by_predicate(
    values: dict[str, str],
    predicate: Any,
) -> float | None:
    for name, value in values.items():
        if not predicate(str(name)):
            continue
        number = _number_from_text(value)
        if number is not None:
            return number
    return None


def _public_power_type(fuel_type: str, title: str) -> str:
    text = f"{fuel_type} {title}".casefold()
    if re.search(r"bev|electric|纯电", text):
        return "electric"
    if re.search(r"phev|hev|hybrid|plug", text):
        return "hybrid"
    return "fuel"


def _public_brand_series(model_ref: object, title: str) -> tuple[str, str]:
    ref = model_ref if isinstance(model_ref, dict) else {}
    brand_name = str(ref.get("brand_name") or "").strip()
    model_name = str(ref.get("name") or "").strip()
    if "xiaomi" in f"{brand_name} {model_name}".casefold() or "小米" in title:
        return "小米汽车", "小米SU7" if "su7" in title.casefold() else "小米汽车"
    series = model_name or title
    if brand_name and series.casefold().startswith(brand_name.casefold()):
        series = series[len(brand_name):].strip(" -")
    return brand_name or "公开车型资料", series


def _public_trim_name(series: str, title: str, year: int | None) -> str:
    if series == "小米SU7":
        lower = title.casefold()
        if "4wd max" in lower or re.search(r"\bmax\b", lower):
            suffix = "四驱Max版"
        elif "rear drive pro" in lower:
            suffix = "后驱Pro版"
        elif re.search(r"\bpro\b", lower):
            suffix = "Pro版"
        elif "rear drive standard" in lower:
            suffix = "后驱标准版"
        else:
            suffix = "标准版"
        return f"{series} {year or ''}款 {suffix}".replace("  ", " ").strip()
    return title


def _catalog_info_items(body: object) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or body.get("status") != 1:
        return []
    raw_items = body.get("info")
    if isinstance(raw_items, dict):
        return [raw_items]
    if isinstance(raw_items, list):
        return [item for item in raw_items if isinstance(item, dict)]
    return []


async def _fetch_catalog_info_body(
    client: httpx.AsyncClient,
    query: str,
) -> dict[str, Any] | None:
    try:
        response = await client.get(
            CARINFO_API_URL,
            params={"type": "info", "keyword": query},
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


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
