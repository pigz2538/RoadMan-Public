import json
import importlib.util
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


def test_quality_metrics_report_completion_tools_latency_route_and_tokens():
    module_path = SCENARIOS.with_name("run_evals.py")
    spec = importlib.util.spec_from_file_location("roadman_run_evals", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    metrics = module.build_quality_metrics(
        [
            {"passed": True, "latency_ms": 100},
            {"passed": False, "latency_ms": 300},
        ],
        {"total_calls": 10, "successful_calls": 8, "agent_usage": {"total_tokens": 100}},
        {
            "total_calls": 14,
            "successful_calls": 11,
            "agent_usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 160},
        },
        0,
    )

    assert metrics["task_completion_rate"] == 0.5
    assert metrics["tool_call_success_rate"] == 0.75
    assert metrics["route_executability_rate"] == 1.0
    assert metrics["average_latency_ms"] == 200.0
    assert metrics["token_cost"]["total_tokens"] == 60
