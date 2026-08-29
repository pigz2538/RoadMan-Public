from safety_scenarios import evaluate_all


def test_all_competition_safety_scenarios_have_expected_outcomes():
    result = evaluate_all()

    assert result["sample_count"] == 5
    assert result["task_completion_rate"] == 1.0
    assert result["passed"] is True
    assert {item["id"] for item in result["cases"]} == {
        "low-soc-long-distance",
        "adverse-weather",
        "energy-facilities-insufficient",
        "vehicle-information-missing",
        "external-services-error",
    }
    insufficient = next(
        item for item in result["cases"] if item["id"] == "energy-facilities-insufficient"
    )
    assert insufficient["route_executable"] is False
    assert "需真实服务确认" in insufficient["route_executability_basis"]
