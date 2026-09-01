import pytest

from app.skills.base import SkillContext
from app.skills.carinfo import (
    _catalog_brand_ids_for_query,
    _catalog_detail_probe_items,
    _catalog_model_key,
    _catalog_needs_public_lookup,
    _catalog_query_match_rank,
    _catalog_search_queries,
    _catalog_series_expansion_requests,
    _catalog_series_expansion_queries,
    _derive_vehicle_metrics,
    _extract_detail_specs,
    _infer_catalog_name_range,
    _infer_power_type_from_text,
    _autoseeker_item,
    _autoseeker_match_score,
    _appbyte_item,
    _appbyte_model_score,
    _parse_public_trim_links,
    _parse_public_trim_page,
    _open_evdb_item,
    _public_vehicle_seed_items,
    CarInfoCatalogAdapter,
)


def test_broad_brand_query_uses_provider_brand_id_only_for_brand_like_text():
    items = [
        {"brand_id": "275", "brand": "小鹏汽车", "series": "小鹏G01"},
        {"brand_id": "275", "brand": "小鹏汽车", "series": "小鹏P7"},
    ]

    assert _catalog_brand_ids_for_query(items, "小鹏") == ["275"]
    assert _catalog_brand_ids_for_query(items, "小鹏P7") == []


def test_series_index_prefers_detail_backed_established_series():
    rows = [
        {"id": "9000", "full_name": "品牌新车", "seriesstate": "20", "has_info": "0"},
        {"id": "5213", "full_name": "品牌P7", "seriesstate": "20", "has_info": "1"},
        {"id": "4489", "full_name": "品牌G3", "seriesstate": "20", "has_info": "1"},
    ]

    requests = _catalog_series_expansion_requests(rows, "品牌", limit=2)

    assert requests == [
        {"series_id": "5213", "query": "品牌P7"},
        {"series_id": "4489", "query": "品牌G3"},
    ]


def test_broad_catalog_search_probes_stable_detail_rows_beyond_new_head():
    items = [
        {
            "source_id": str(index),
            "series": "新车系",
            "year": 2026,
            "state": "20",
        }
        for index in range(12)
    ]
    items.extend(
        [
            {
                "source_id": "stable-a",
                "series": "P7",
                "year": 2024,
                "state": "0",
            },
            {
                "source_id": "stable-b",
                "series": "G9",
                "year": 2023,
                "state": "0",
            },
        ]
    )

    selected = _catalog_detail_probe_items(items, 12)

    assert len(selected) == 14
    assert {item["source_id"] for item in selected[-2:]} == {"stable-a", "stable-b"}


def test_brand_query_expands_established_series_but_specific_query_does_not():
    items = [
        {"series": "小鹏G01", "year": 2026, "state": "20"},
        {"series": "小鹏P7", "year": 2024, "state": "0"},
        {"series": "小鹏G9", "year": 2023, "state": "0"},
    ]

    assert _catalog_series_expansion_queries(items, "小鹏") == ["小鹏G9", "小鹏P7", "小鹏G01"]
    assert _catalog_series_expansion_queries(items, "小鹏P7") == []
    assert _catalog_series_expansion_queries(items, "P7") == []


def test_catalog_search_adds_trim_suffix_for_literal_provider_index():
    assert _catalog_search_queries("特斯拉 Model 3") == [
        "特斯拉 Model 3",
        "Model 3",
        "特斯拉Model3",
    ]


def test_public_fallback_detects_su7_ultra_as_an_incomplete_su7_match():
    primary_ultra = [
        {
            "brand": "小米汽车",
            "series": "小米SU7 Ultra",
            "model": "小米汽车小米SU7 Ultra 2025款 Ultra",
        }
    ]

    assert _catalog_model_key("小米SU7") == "su7"
    assert _catalog_model_key("SU7 Max") == "su7max"
    assert _catalog_needs_public_lookup("小米SU7", primary_ultra) is True
    assert _catalog_needs_public_lookup("小米SU7 Ultra", primary_ultra) is False


def test_public_fallback_rejects_fuzzy_suffix_from_another_chinese_brand():
    primary_g6_van = [
        {
            "brand": "\u91d1\u676f",
            "series": "\u91d1\u676f\u5feb\u8fd0",
            "model": "\u91d1\u676f\u5feb\u8fd0 2020 G6P-5",
        }
    ]

    assert _catalog_needs_public_lookup("\u5c0f\u9e4f G6", primary_g6_van) is True


def test_public_fallback_rejects_fuzzy_suffix_from_another_latin_brand():
    primary_g6_van = [
        {
            "brand": "Jinbei",
            "series": "Fast van",
            "model": "Jinbei G6P 2020",
        }
    ]

    assert _catalog_needs_public_lookup("Xpeng G6", primary_g6_van) is True


def test_public_su7_seed_covers_standard_pro_and_max_when_public_pages_miss():
    seeds = _public_vehicle_seed_items("小米SU7")
    assert {item["model"] for item in seeds} == {
        "小米SU7 2024款 标准版",
        "小米SU7 2024款 Pro版",
        "小米SU7 2024款 四驱Max版",
    }
    assert len(_public_vehicle_seed_items("SU7 Max")) == 1
    assert _public_vehicle_seed_items("SU7 Max")[0]["rated_range_km"] == 800
    max_item = _public_vehicle_seed_items("SU7 Max")[0]
    assert max_item["battery_kwh"] == 101
    assert max_item["consumption_per_100km"] == 13.7
    assert max_item["dc_charge_time_hours"] == 0.3
    assert max_item["width_m"] == 1.963
    assert len(max_item["specifications"]) >= 10


def test_requested_base_trim_is_sorted_before_ultra_variant():
    assert _catalog_query_match_rank(
        {"model": "小米SU7 2024款 四驱Max版"},
        "su7",
    ) == 1
    assert _catalog_query_match_rank(
        {"model": "小米汽车小米SU7 Ultra2025款 Ultra"},
        "su7",
    ) == 2


def test_public_trim_parser_keeps_concrete_trim_and_specs():
    model_page = '''
      <a class="trim" href="/database/xiaomi-auto/xiaomi-auto-su7/2024/800km-495kw-0-22040">
        <b class="mr-2">Xiaomi Auto SU7 2024 Xiaomi SU7 2024 4WD Max</b>
      </a>
    '''
    trims = _parse_public_trim_links(
        model_page,
        "https://data.carnewschina.com/database/xiaomi-auto/xiaomi-auto-su7/2024",
        {"brand_name": "Xiaomi Auto", "name": "Xiaomi Auto SU7"},
    )
    assert len(trims) == 1

    trim_page = '''
      <h1 class="h2">Xiaomi Auto SU7 2024 Xiaomi SU7 2024 4WD Max</h1>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">Range (CLTC)</div>
        <div><div class="table__cell">800 km</div></div>
      </div>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">Battery capacity</div>
        <div><div class="table__cell">101 kWh</div></div>
      </div>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">Consumption</div>
        <div><div class="table__cell">13.7 kWh/100km</div></div>
      </div>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">Fuel type</div>
        <div><div class="table__cell">BEV</div></div>
      </div>
    '''
    item = _parse_public_trim_page(
        trim_page,
        trims[0]["url"],
        trims[0],
    )
    assert item is not None
    assert item["series"] == "小米SU7"
    assert item["model"].endswith("四驱Max版")
    assert item["rated_range_km"] == 800
    assert item["battery_kwh"] == 101
    assert item["consumption_per_100km"] == 13.7
    assert item["power_type"] == "electric"


def test_public_trim_parser_extracts_optional_range_and_charging_details():
    trim_ref = {
        "url": "https://data.carnewschina.com/database/example/example/2025/trim",
        "name": "Example EV 2025 Long Range",
        "model_url": "https://data.carnewschina.com/database/example/example",
        "model_ref": {"brand_name": "Example", "name": "Example EV"},
    }
    trim_page = '''
      <h1 class="h2">Example EV 2025 Long Range</h1>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">Range (WLTC)</div>
        <div><div class="table__cell">620 km</div></div>
      </div>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">Battery capacity</div>
        <div><div class="table__cell">78.5 kWh</div></div>
      </div>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">Consumption</div>
        <div><div class="table__cell">14.1 kWh/100km</div></div>
      </div>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">DC charging (30-80%)</div>
        <div><div class="table__cell">0.35 hours</div></div>
      </div>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">L/W/H</div>
        <div><div class="table__cell">4800/1900/1600 mm</div></div>
      </div>
      <div class="table__row">
        <div class="table__cell table__cell-param-name">Number of seats</div>
        <div><div class="table__cell">5</div></div>
      </div>
    '''
    item = _parse_public_trim_page(trim_page, trim_ref["url"], trim_ref)
    assert item is not None
    assert item["rated_range_km"] == 620
    assert item["battery_kwh"] == 78.5
    assert item["consumption_per_100km"] == 14.1
    assert item["dc_charge_time_hours"] == 0.35
    assert item["width_m"] == 1.9
    assert item["height_m"] == 1.6
    assert item["seats"] == 5


def test_open_evdb_item_maps_structured_range_and_charge_fields_without_defaults():
    item = _open_evdb_item(
        {
            "id": "example-ev-long-range-2025",
            "drive_type": "AWD",
            "body_style": "suv",
            "weight_curb_kg": 2100,
            "trunk_capacity_liters": 520,
        },
        brand="Example",
        model="Example EV",
        variant="Long Range",
        year=2025,
        range_wltp=620,
        range_real=540,
        battery=82.0,
        dc_power=150,
        power=250,
        acceleration=5.1,
        top_speed=210,
        length_mm=4800,
        width_mm=1900,
        height_mm=1650,
    )
    assert item["rated_range_km"] == 620
    assert item["battery_kwh"] == 82.0
    assert item["max_charge_kw"] == 150
    assert item["width_m"] == 1.9
    assert item["height_m"] == 1.65
    assert item["seats"] is None
    assert "百公里能耗" in item["specs_missing"]
    assert item["estimated_fields"] == ["rated_range_km"]
    assert len(item["specifications"]) >= 8


def test_autoseeker_fallback_is_generic_and_preserves_published_vehicle_fields():
    raw = {
        "slug": "xpeng-g6-2024",
        "merk": "Xpeng",
        "model": "G6",
        "generatie": "I (2023+)",
        "bouwjaarVan": 2024,
        "fuels": ["ev"],
        "specs": {
            "carrosserie": "SUV",
            "zitplaatsen": 5,
            "wltp_range_km": 570,
            "batterij_kwh": 80.8,
            "vermogen_pk": 292,
            "acceleratie_0_100_s": 6.7,
            "topsnelheid_kmh": 202,
            "lengte_mm": 4758,
            "breedte_mm": 1920,
            "hoogte_mm": 1650,
            "laadvermogen_dc_kw": 280,
            "verbruik_wltp_kwh_100km": 17.5,
        },
    }

    item = _autoseeker_item(raw)

    assert item is not None
    assert item["brand"] == "Xpeng"
    assert item["model"] == "G6 2024"
    assert item["rated_range_km"] == 570
    assert item["battery_kwh"] == 80.8
    assert item["consumption_per_100km"] == 17.5
    assert item["max_charge_kw"] == 280
    assert item["width_m"] == 1.92
    assert item["height_m"] == 1.65
    assert item["seats"] == 5
    assert item["estimated_fields"] == ["rated_range_km"]
    assert item["specs_missing"] == []
    assert item["catalog_source"].startswith("AutoSeeker")


def test_autoseeker_match_does_not_accept_unrelated_suffix_hit():
    requested = {
        "merk": "Xpeng",
        "model": "G6",
        "generatie": "I (2023+)",
    }
    unrelated = {
        "merk": "Mercedes-Benz",
        "model": "GLE 500e",
        "generatie": "W167",
    }

    assert _autoseeker_match_score(requested, "小鹏 G6") is not None
    assert _autoseeker_match_score(unrelated, "小鹏 G6") is None


def test_autoseeker_match_keeps_numeric_model_family_exact():
    assert _autoseeker_match_score(
        {"merk": "Tesla", "model": "Model 3", "generatie": "Highland"},
        "Model 3",
    ) is not None
    assert _autoseeker_match_score(
        {"merk": "Tesla", "model": "Model Y", "generatie": "Juniper"},
        "Model 3",
    ) is None


def test_appbyte_fallback_maps_version_specs_and_estimated_fuel_range():
    assert _appbyte_model_score("Golf GTI", ["golf"]) is not None
    item = _appbyte_item(
        {
            "id": "variant-1",
            "name": "1.5 TSI DSG",
            "yearFrom": 2024,
            "fuelType": "Petrol",
            "fuelEconomyCombinedL100": 6.2,
            "fuelTankLitres": 50,
            "powerKw": 110,
            "lengthMm": 4284,
            "widthMm": 1789,
            "heightMm": 1491,
            "numberOfSeats": 5,
        },
        brand="VOLKSWAGEN",
        series="Golf VIII",
    )
    assert item is not None
    assert item["power_type"] == "fuel"
    assert item["consumption_per_100km"] == 6.2
    assert round(item["rated_range_km"], 1) == round(50 * 100 / 6.2, 1)
    assert item["estimated_fields"] == ["rated_range_km"]
    assert item["height_m"] == 1.491
    assert item["seats"] == 5
    assert item["specs_missing"] == []


@pytest.mark.asyncio
async def test_su7_emergency_cache_is_after_live_public_lookup(monkeypatch):
    async def fake_primary(_, query):
        return {
            "status": 1,
            "info": {
                "id": "primary-ultra",
                "brand_name": "Xiaomi Auto",
                "series_name": "Xiaomi SU7",
                "full_name": "Xiaomi SU7 Ultra",
                "year": 2025,
                "state": "20",
            },
        }

    live_item = {
        "id": "live_su7_standard",
        "source_id": "live_su7_standard",
        "brand": "Xiaomi Auto",
        "series": "SU7",
        "model": "SU7 Standard 2026",
        "year": 2026,
        "power_type": "electric",
        "rated_range_km": 750,
        "battery_kwh": 80,
        "consumption_per_100km": 12,
        "max_charge_kw": 180,
        "dc_charge_time_hours": None,
        "height_m": None,
        "width_m": None,
        "seats": 5,
        "current_energy_percent": 80,
        "safe_energy_reserve_percent": 15,
        "has_etc": False,
        "mountain_ready": True,
        "unpaved_ready": False,
        "state": "0",
        "state_label": "public",
        "price_min_cny": None,
        "price_max_cny": None,
        "source_url": "https://example.invalid/su7-standard",
        "detail_source_url": "https://example.invalid/su7-standard",
        "specifications": [{"name": "Range", "value": "750 km"}],
        "specs_missing": [],
        "estimated_fields": [],
        "catalog_source": "Live public catalogue",
        "fallback_used": True,
    }

    async def fake_public(query, *, limit):
        assert query == "SU7"
        return [live_item]

    monkeypatch.setattr("app.skills.carinfo._fetch_catalog_info_body", fake_primary)
    monkeypatch.setattr("app.skills.carinfo._run_public_vehicle_lookup", fake_public)

    result = await CarInfoCatalogAdapter().execute(
        {"query": "SU7", "limit": 5},
        SkillContext(),
    )

    assert result.success is True
    assert result.data["items"][0]["source_id"] == "live_su7_standard"
    assert result.data["items"][0]["rated_range_km"] == 750
    assert result.data["fallback_used"] is True


def test_catalog_detail_specs_map_to_planner_fields():
    body = {
        "status": 1,
        "info": {
            "param": [
                {
                    "paramitems": [
                        {"name": "CLTC纯电续航里程(km)", "valueitems": [{"value": "634"}]},
                        {"name": "电池能量(kWh)", "valueitems": [{"value": "62.5"}]},
                        {"name": "百公里耗电量(kWh/100km)", "valueitems": [{"value": "11.2"}]},
                        {"name": "快充功率(kW)", "valueitems": [{"value": "170"}]},
                        {"name": "长*宽*高(mm)", "valueitems": [{"value": "4720*1850*1440"}]},
                        {"name": "座位数", "valueitems": [{"value": "5"}]},
                    ]
                }
            ]
        },
    }
    specs = _extract_detail_specs(body)
    metrics = _derive_vehicle_metrics(specs)
    assert metrics["rated_range_km"] == 634
    assert metrics["battery_kwh"] == 62.5
    assert metrics["consumption_per_100km"] == 11.2
    assert metrics["max_charge_kw"] == 170
    assert metrics["width_m"] == 1.85
    assert metrics["height_m"] == 1.44
    assert metrics["seats"] == 5


def test_hybrid_name_is_not_misclassified_as_battery_electric():
    assert _infer_power_type_from_text("比亚迪 宋Pro DM-i") == "hybrid"
    assert _infer_power_type_from_text("特斯拉 Model 3 纯电") == "electric"


def test_catalog_trim_name_range_ignores_year_and_series_numbers():
    assert _infer_catalog_name_range(
        "小鹏汽车小鹏G012026款 纯电 665Max",
        brand="小鹏汽车",
        series="小鹏G01",
        power_type="electric",
    ) == 665
    assert _infer_catalog_name_range(
        "小鹏汽车小鹏G012026款 增程 1585四驱Ultra",
        brand="小鹏汽车",
        series="小鹏G01",
        power_type="hybrid",
    ) == 1585
    assert _infer_catalog_name_range(
        "大众高尔夫2024款 280TSI",
        brand="大众",
        series="高尔夫",
        power_type="fuel",
    ) is None
