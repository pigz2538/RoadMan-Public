import json
from pathlib import Path

from range_accuracy import DEFAULT_INPUT, evaluate_dataset


def test_simulation_replay_meets_declared_baseline_thresholds():
    payload = json.loads(DEFAULT_INPUT.read_text(encoding="utf-8"))
    result = evaluate_dataset(payload)

    assert result["data_kind"] == "simulation_sensor_replay"
    assert result["metrics"]["sample_count"] == 12
    assert result["is_real_road_data"] is False
    assert result["metrics"]["energy_p95_absolute_error_percent"] > 0
    assert result["metrics"]["energy_rmse_kwh"] > 0
    assert result["metrics"]["energy_bias_kwh"] < 0
    assert len(result["condition_metrics"]) == 12
    assert result["passed"] is True
    assert all(result["checks"].values())


def test_observed_zero_is_rejected_instead_of_hiding_error(tmp_path: Path):
    del tmp_path
    payload = {
        "records": [
            {
                "id": "invalid",
                "battery_kwh": 82,
                "distance_km": 100,
                "initial_soc_percent": 80,
                "predicted_energy_kwh": 18,
                "observed_energy_kwh": 0,
            }
        ]
    }

    try:
        evaluate_dataset(payload)
    except ValueError as error:
        assert "positive values required" in str(error)
    else:
        raise AssertionError("zero observed energy must fail the evaluation")


def test_duplicate_ids_and_impossible_soc_are_rejected():
    base = {
        "id": "same",
        "battery_kwh": 82,
        "distance_km": 100,
        "initial_soc_percent": 80,
        "predicted_energy_kwh": 18,
        "observed_energy_kwh": 19,
    }
    for payload, message in [
        ({"records": [base, dict(base)]}, "duplicate record id"),
        ({"records": [{**base, "initial_soc_percent": 101}]}, "initial_soc_percent"),
        ({"records": [{**base, "id": ""}]}, "non-empty id"),
    ]:
        try:
            evaluate_dataset(payload)
        except ValueError as error:
            assert message in str(error)
        else:
            raise AssertionError(f"invalid dataset must fail: {message}")


def test_condition_metrics_are_separated_and_real_data_is_labeled():
    payload = {
        "data_kind": "real_vehicle_telemetry",
        "records": [
            {
                "id": "warm-1", "condition": "warm", "battery_kwh": 80,
                "distance_km": 100, "initial_soc_percent": 90,
                "predicted_energy_kwh": 20, "observed_energy_kwh": 22,
            },
            {
                "id": "cold-1", "condition": "cold", "battery_kwh": 80,
                "distance_km": 100, "initial_soc_percent": 90,
                "predicted_energy_kwh": 20, "observed_energy_kwh": 25,
            },
        ],
    }
    result = evaluate_dataset(payload)
    assert result["is_real_road_data"] is True
    assert set(result["condition_metrics"]) == {"warm", "cold"}
    assert result["condition_metrics"]["cold"]["energy_mape_percent"] > result["condition_metrics"]["warm"]["energy_mape_percent"]
