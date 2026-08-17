import json
from pathlib import Path


SCENARIOS = Path(__file__).resolve().parents[2] / "evaluation" / "scenarios.json"


def test_evaluation_dataset_covers_competition_critical_dimensions():
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))

    assert 8 <= len(scenarios) <= 12
    assert len({item["id"] for item in scenarios}) == len(scenarios)
    dimensions = {dimension for item in scenarios for dimension in item["dimension"]}
    assert {
        "日期理解",
        "情侣人数",
        "同名地点",
        "跨城交通",
        "三餐住宿",
        "新能源补能",
        "特殊事件",
        "路线闭环",
    } <= dimensions
    for scenario in scenarios:
        assert len(scenario["input"]) >= 15
        assert scenario["expect"]
