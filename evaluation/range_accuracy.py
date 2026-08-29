"""Deterministic predicted-versus-observed range accuracy evaluation.

The evaluator deliberately has no network or model dependency.  Competition
reviewers can replace the bundled simulation replay file with exported road
measurements and obtain the same metrics and pass/fail contract.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


DEFAULT_INPUT = Path(__file__).with_name("range_observations.simulated.json")
DEFAULT_OUTPUT = Path(__file__).with_name("results") / "range-accuracy-baseline.json"


def _absolute_percentage_error(predicted: float, observed: float) -> float:
    if observed == 0:
        raise ValueError("observed value must be non-zero")
    return abs(predicted - observed) / abs(observed) * 100.0


def evaluate_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    records = payload.get("records") or []
    if not records:
        raise ValueError("dataset must contain at least one record")

    cases: list[dict[str, Any]] = []
    for record in records:
        battery_kwh = float(record["battery_kwh"])
        distance_km = float(record["distance_km"])
        initial_soc = float(record["initial_soc_percent"])
        predicted_energy = float(record["predicted_energy_kwh"])
        observed_energy = float(record["observed_energy_kwh"])
        if min(battery_kwh, distance_km, predicted_energy, observed_energy) <= 0:
            raise ValueError(f"{record.get('id', 'record')}: positive values required")

        predicted_soc = max(0.0, initial_soc - predicted_energy / battery_kwh * 100.0)
        observed_soc = max(0.0, initial_soc - observed_energy / battery_kwh * 100.0)
        predicted_range = battery_kwh / (predicted_energy / distance_km)
        observed_range = battery_kwh / (observed_energy / distance_km)
        cases.append(
            {
                "id": record["id"],
                "condition": record.get("condition", "unspecified"),
                "energy_error_percent": round(
                    _absolute_percentage_error(predicted_energy, observed_energy), 3
                ),
                "energy_error_kwh": round(abs(predicted_energy - observed_energy), 3),
                "soc_error_percentage_points": round(abs(predicted_soc - observed_soc), 3),
                "predicted_remaining_soc_percent": round(predicted_soc, 2),
                "observed_remaining_soc_percent": round(observed_soc, 2),
                "predicted_range_km": round(predicted_range, 1),
                "observed_range_km": round(observed_range, 1),
                "range_error_percent": round(
                    _absolute_percentage_error(predicted_range, observed_range), 3
                ),
            }
        )

    thresholds = {
        "energy_mape_percent": 12.0,
        "range_mape_percent": 12.0,
        "soc_mae_percentage_points": 5.0,
        **(payload.get("thresholds") or {}),
    }
    metrics = {
        "sample_count": len(cases),
        "energy_mape_percent": round(mean(item["energy_error_percent"] for item in cases), 3),
        "energy_mae_kwh": round(mean(item["energy_error_kwh"] for item in cases), 3),
        "range_mape_percent": round(mean(item["range_error_percent"] for item in cases), 3),
        "soc_mae_percentage_points": round(
            mean(item["soc_error_percentage_points"] for item in cases), 3
        ),
    }
    checks = {
        key: metrics[key] <= float(limit)
        for key, limit in thresholds.items()
        if key in metrics
    }
    return {
        "dataset_id": payload.get("dataset_id"),
        "data_kind": payload.get("data_kind", "unknown"),
        "claim_boundary": payload.get("claim_boundary"),
        "metrics": metrics,
        "thresholds": thresholds,
        "checks": checks,
        "passed": bool(checks) and all(checks.values()),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = evaluate_dataset(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "[range-eval] "
        f"samples={result['metrics']['sample_count']} "
        f"energy_mape={result['metrics']['energy_mape_percent']}% "
        f"range_mape={result['metrics']['range_mape_percent']}% "
        f"soc_mae={result['metrics']['soc_mae_percentage_points']}pp "
        f"status={'PASS' if result['passed'] else 'FAIL'}"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
