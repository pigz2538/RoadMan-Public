from safety_scenarios import evaluate_all


def test_all_competition_safety_scenarios_have_expected_outcomes():
    result = evaluate_all()

    assert result["sample_count"] == 12
    assert result["task_completion_rate"] == 1.0
    assert result["passed"] is True
    assert {item["id"] for item in result["cases"]} == {
        "low-soc-long-distance",
        "adverse-weather",
        "energy-facilities-insufficient",
        "vehicle-information-missing",
        "external-services-error",
        "overreported-charge-capped",
        "vehicle-charge-power-cap",
        "invalid-charger-metadata",
        "fuel-service-measured",
        "partial-weather-payload",
        "invalid-vehicle-energy-metadata",
        "short-trip-no-optional-services",
    }
    insufficient = next(
        item for item in result["cases"] if item["id"] == "energy-facilities-insufficient"
    )
    assert insufficient["route_executable"] is False
    assert "需真实服务确认" in insufficient["route_executability_basis"]
    assert result["degradation_handled_rate"] == 1.0
    assert result["p95_latency_ms"] >= 0
    capped = next(item for item in result["cases"] if item["id"] == "overreported-charge-capped")
    assert capped["energy_checks"]["soc_never_exceeds_100"] is True
    limited = next(item for item in result["cases"] if item["id"] == "vehicle-charge-power-cap")
    assert limited["energy_checks"]["vehicle_power_cap"] is True
