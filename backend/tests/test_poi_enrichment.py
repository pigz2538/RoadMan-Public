from app.planning.poi_enrichment import parse_web_meta


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
