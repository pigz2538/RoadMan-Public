from app.skills.amap import AmapRouteAdapter, _text_value


def test_driving_path_normalizes_geometry_and_live_traffic_segments():
    result = AmapRouteAdapter._normal_path(
        {
            "distance": "3000",
            "duration": "420",
            "tolls": "12",
            "traffic_lights": "4",
            "restriction": "0",
            "steps": [
                {
                    "instruction": "向东行驶",
                    "road": "珞喻路",
                    "distance": "3000",
                    "duration": "420",
                    "polyline": "114.36,30.53;114.38,30.54",
                    "tmcs": [
                        {
                            "status": "畅通",
                            "distance": "2400",
                            "polyline": "114.36,30.53;114.37,30.535",
                        },
                        {
                            "status": "拥堵",
                            "distance": "600",
                            "polyline": "114.37,30.535;114.38,30.54",
                        },
                    ],
                }
            ],
        },
        "/v3/direction/driving",
    )

    assert result["distance_km"] == 3
    assert result["duration_minutes"] == 7
    assert result["traffic_lights"] == 4
    assert result["traffic_summary"] == "高德当前缓行或拥堵路段约占 20%"
    assert len(result["traffic_segments"]) == 2
    assert result["traffic_segments"][1]["geometry"][-1] == {
        "longitude": 114.38,
        "latitude": 30.54,
    }


def test_driving_path_reports_missing_segment_traffic_explicitly():
    result = AmapRouteAdapter._normal_path(
        {"distance": "1000", "duration": "60", "steps": []},
        "/v3/direction/driving",
    )

    assert result["traffic_segments"] == []
    assert result["traffic_summary"] == "高德未返回分段实时路况"


def test_amap_empty_list_fields_are_normalized_for_domain_models():
    assert _text_value([]) is None
    assert _text_value(["洪山区", "珞喻路"]) == "洪山区、珞喻路"
