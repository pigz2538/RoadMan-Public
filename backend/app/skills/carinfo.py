from __future__ import annotations

import asyncio
import html
import re
import time
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
    version = "1.5.0"
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
        items.sort(key=_catalog_sort_key)
        # Keep a bounded look-ahead window. Newer trims in the upstream search
        # list occasionally have no detail row while an adjacent trim does;
        # fetching only the first ``limit`` would therefore return an
        # identity-only list even though useful specifications are available.
        # Twelve concurrent detail calls stay below the Registry's 12-second
        # adapter budget; larger requested limits still return their remaining
        # identity rows without making the endpoint time out.
        candidate_count = min(
            len(items),
            max(min(request.limit, 12), min(request.limit * 3, 12)),
        )
        candidate_items = items[:candidate_count]
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
    state = str(raw.get("state") or "").strip()
    source_url = f"{CARINFO_API_URL}?{urlencode({'type': 'info', 'id': source_id})}"
    item = {
        "id": f"carinfo_{source_id}",
        "source_id": source_id,
        "brand": brand,
        "series": series,
        "model": model,
        "year": year,
        "power_type": power_type,
        # The catalog endpoint supplies identity/year/price, but not a
        # reliable per-trim battery or real-world range. Keep those fields
        # null so the traveller can confirm their exact configuration instead
        # of silently inheriting the demo SUV's numbers.
        "rated_range_km": None,
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
