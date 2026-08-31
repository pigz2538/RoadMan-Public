from app.skills.carinfo import (
    _catalog_search_queries,
    _derive_vehicle_metrics,
    _extract_detail_specs,
    _infer_power_type_from_text,
)


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
