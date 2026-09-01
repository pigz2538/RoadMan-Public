from app.planning.poi_enrichment import (
    _clean_public_description,
    _merge_structured_facts,
    _sanitize_candidate_copy,
    parse_web_meta,
)


def test_provider_boilerplate_is_removed_from_saved_copy():
    candidate = {
        "description": "在线地图官方网站，提供全国地图浏览，地点搜索，公交驾车查询服务。可同时查看商家团购、优惠信息。在线地图，您的出行、生活好帮手。景区湖岸适合散步。",
        "information_summary": "飞猪AI开放平台（旅行信息服务）是飞猪旅行的AI能力开放平台，为开发者提供酒店预订、机票搜索、门票API、度假套餐等全品类旅行AI服务，支持OpenClaw协议实时接入飞猪官方商品库。门票需预约。",
    }
    _sanitize_candidate_copy(candidate)
    assert candidate["description"] == "景区湖岸适合散步。"
    assert candidate["information_summary"] == "门票需预约。"
    assert _clean_public_description("在线地图官方网站") == ""


def test_amap_variant_boilerplate_is_removed_too():
    candidate = {
        "description": (
            "\u9ad8\u5fb7\u5730\u56fe\u5b98\u65b9\u7f51\u7ad9\uff0c\u63d0\u4f9b\u5168\u56fd\u5730\u56fe\u6d4f\u89c8\uff0c"
            "\u5730\u70b9\u641c\u7d22\u548c\u516c\u4ea4\u9a7e\u8f66\u67e5\u8be2\u670d\u52a1\u3002"
            "\u666f\u533a\u6e56\u8fb9\u9002\u5408\u6563\u6b65\u3002"
        ),
        "information_summary": "\u9ad8\u5fb7\u5730\u56fe\uff0c\u60a8\u7684\u51fa\u884c\u3001\u751f\u6d3b\u597d\u5e2e\u624b\u3002",
    }
    _sanitize_candidate_copy(candidate)
    assert candidate["description"] == "\u666f\u533a\u6e56\u8fb9\u9002\u5408\u6563\u6b65\u3002"
    assert "information_summary" not in candidate


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
