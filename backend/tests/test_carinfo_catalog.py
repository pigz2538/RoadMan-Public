from app.skills.carinfo import (
    _catalog_brand_ids_for_query,
    _catalog_detail_probe_items,
    _catalog_search_queries,
    _catalog_series_expansion_requests,
    _catalog_series_expansion_queries,
    _derive_vehicle_metrics,
    _extract_detail_specs,
    _infer_catalog_name_range,
    _infer_power_type_from_text,
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
