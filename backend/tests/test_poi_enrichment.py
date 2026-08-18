from app.planning.poi_enrichment import _merge_structured_facts, parse_web_meta


def test_parse_web_meta_extracts_description_image_and_title():
    meta = parse_web_meta(
        """
        <html><head>
          <title>庐山风景名胜区</title>
          <meta property="og:description" content="世界文化遗产与自然景观。">
          <meta property="og:image" content="https://example.com/lushan.jpg">
          <meta property="og:url" content="https://baike.baidu.com/item/庐山">
        </head></html>
        """,
        url="https://baike.baidu.com/item/庐山",
    )
    assert meta["title"] == "庐山风景名胜区"
    assert meta["description"] == "世界文化遗产与自然景观。"
    assert meta["image_url"].endswith("lushan.jpg")


def test_structured_poi_facts_keep_ticket_parking_and_reservation_state():
    candidate = {"place": {"name": "测试景点"}, "source_records": []}
    _merge_structured_facts(
        candidate,
        {
            "opening_hours_text": "08:30-17:30",
            "price_text": "¥60-80",
            "parking_text": "停车场收费 10 元/次",
            "ticket_ordering": "需提前预约，官方平台购票",
            "website": "https://example.com/official",
            "photos": ["https://example.com/poi.jpg"],
        },
        provider="高德地图",
    )
    assert candidate["opening_hours"]["text"] == "08:30-17:30"
    assert candidate["ticket_or_price"]["minimum"] == 60
    assert candidate["ticket_or_price"]["maximum"] == 80
    assert candidate["parking_or_price"]["minimum"] == 10
    assert candidate["reservation_status"] == "recommended"
    assert candidate["official_url"].endswith("official")
    assert candidate["image_url"].endswith("poi.jpg")
