import json
from pathlib import Path

from range_accuracy import DEFAULT_INPUT, evaluate_dataset


def test_simulation_replay_meets_declared_baseline_thresholds():
    payload = json.loads(DEFAULT_INPUT.read_text(encoding="utf-8"))
    result = evaluate_dataset(payload)

    assert result["data_kind"] == "simulation_sensor_replay"
    assert result["metrics"]["sample_count"] == 12
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
