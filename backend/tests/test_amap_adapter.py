from app.domain.models import SourceRecord
from app.skills.amap import AmapRouteAdapter, _poi_facts, _text_value


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


def test_transit_path_keeps_lines_and_boarding_stops():
    result = AmapRouteAdapter._transit_path(
        {
            "distance": "5200",
            "duration": "1800",
            "cost": "3",
            "segments": [{
                "walking": {
                    "distance": "680",
                    "duration": "540",
                    "steps": [{"polyline": "114.30,30.50;114.31,30.51"}],
                },
                "bus": {"buslines": [{
                    "name": "2号线", "id": "line-2", "type": "地铁",
                    "departure_stop": {"name": "汉口站"},
                    "arrival_stop": {"name": "江汉路站"},
                    "stime": "09:00", "etime": "09:18", "via_num": "5",
                    "distance": "4200", "duration": "1080", "cost": "3",
                    "polyline": "114.31,30.51;114.34,30.54",
                }]},
            }],
        },
        "/v3/direction/transit/integrated",
    )
    assert result["transit_legs"][0]["mode"] == "walk"
    assert result["transit_legs"][0]["duration_minutes"] == 9
    assert result["transit_legs"][0]["distance_km"] == 0.68
    assert result["transit_legs"][1]["line_name"] == "2号线"
    assert result["transit_legs"][1]["departure_stop"] == "汉口站"
    assert result["transit_legs"][1]["arrival_stop"] == "江汉路站"
    assert result["fare_cny"] == 3


def test_poi_facts_normalize_opening_ticket_parking_and_photos():
    facts = _poi_facts({
        "business": {
            "opentime": "09:00-17:00",
            "cost": "¥80",
            "parking_info": "停车场收费",
            "photos": [{"url": "https://example.com/poi.jpg"}],
        }
    })
    assert facts["opening_hours_text"] == "09:00-17:00"
    assert facts["price_text"] == "¥80"
    assert facts["parking_text"] == "停车场收费"
    assert facts["photos"] == ["https://example.com/poi.jpg"]
    assert _text_value(["洪山区", "珞喻路"]) == "洪山区、珞喻路"


def test_legacy_source_records_infer_auditable_source_type():
    assert SourceRecord(provider="高德地图", title="POI API").source_type == "map"
    assert SourceRecord(provider="FlyAI / 飞猪", title="实时车次").source_type == "travel_platform"
    assert SourceRecord(provider="OpenTripMap", title="global POI").source_type == "open_data"
