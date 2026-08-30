"""Deterministic predicted-versus-observed range accuracy evaluation.

The evaluator deliberately has no network or model dependency.  Competition
reviewers can replace the bundled simulation replay file with exported road
measurements and obtain the same metrics and pass/fail contract.
"""

from __future__ import annotations

import argparse
import json
from math import sqrt
from pathlib import Path
from statistics import mean
from typing import Any


DEFAULT_INPUT = Path(__file__).with_name("range_observations.simulated.json")
DEFAULT_OUTPUT = Path(__file__).with_name("results") / "range-accuracy-baseline.json"


def _absolute_percentage_error(predicted: float, observed: float) -> float:
    if observed == 0:
        raise ValueError("observed value must be non-zero")
    return abs(predicted - observed) / abs(observed) * 100.0


def _percentile(values: list[float], percentile: float) -> float:
    """Return a linearly interpolated percentile without optional packages."""
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _summarize_cases(cases: list[dict[str, Any]]) -> dict[str, float | int]:
    signed_energy_errors = [item["predicted_energy_kwh"] - item["observed_energy_kwh"] for item in cases]
    energy_errors = [item["energy_error_kwh"] for item in cases]
    energy_apes = [item["energy_error_percent"] for item in cases]
    range_apes = [item["range_error_percent"] for item in cases]
    soc_errors = [item["soc_error_percentage_points"] for item in cases]
    return {
        "sample_count": len(cases),
        "energy_mape_percent": round(mean(energy_apes), 3),
        "energy_mae_kwh": round(mean(energy_errors), 3),
        "energy_rmse_kwh": round(sqrt(mean(value * value for value in signed_energy_errors)), 3),
        "energy_bias_kwh": round(mean(signed_energy_errors), 3),
        "energy_p95_absolute_error_percent": round(_percentile(energy_apes, 0.95), 3),
        "range_mape_percent": round(mean(range_apes), 3),
        "range_p95_absolute_error_percent": round(_percentile(range_apes, 0.95), 3),
        "soc_mae_percentage_points": round(mean(soc_errors), 3),
        "soc_p95_error_percentage_points": round(_percentile(soc_errors, 0.95), 3),
    }


def evaluate_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    records = payload.get("records") or []
    if not records:
        raise ValueError("dataset must contain at least one record")

    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in records:
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            raise ValueError("every record requires a non-empty id")
        if record_id in seen_ids:
            raise ValueError(f"duplicate record id: {record_id}")
        seen_ids.add(record_id)
        battery_kwh = float(record["battery_kwh"])
        distance_km = float(record["distance_km"])
        initial_soc = float(record["initial_soc_percent"])
        predicted_energy = float(record["predicted_energy_kwh"])
        observed_energy = float(record["observed_energy_kwh"])
        if min(battery_kwh, distance_km, predicted_energy, observed_energy) <= 0:
            raise ValueError(f"{record_id}: positive values required")
        if not 0 < initial_soc <= 100:
            raise ValueError(f"{record_id}: initial_soc_percent must be within (0, 100]")

        predicted_soc = max(0.0, initial_soc - predicted_energy / battery_kwh * 100.0)
        observed_soc = max(0.0, initial_soc - observed_energy / battery_kwh * 100.0)
        predicted_range = battery_kwh / (predicted_energy / distance_km)
        observed_range = battery_kwh / (observed_energy / distance_km)
        cases.append(
            {
                "id": record_id,
                "condition": record.get("condition", "unspecified"),
                "predicted_energy_kwh": round(predicted_energy, 3),
                "observed_energy_kwh": round(observed_energy, 3),
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
    metrics = _summarize_cases(cases)
    conditions = sorted({str(item["condition"]) for item in cases})
    condition_metrics = {
        condition: _summarize_cases(
            [item for item in cases if str(item["condition"]) == condition]
        )
        for condition in conditions
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
        "is_real_road_data": payload.get("data_kind") == "real_vehicle_telemetry",
        "metrics": metrics,
        "condition_metrics": condition_metrics,
        "thresholds": thresholds,
        "checks": checks,
        "passed": bool(checks) and all(checks.values()),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--require-real",
        action="store_true",
        help="fail unless the input explicitly declares real_vehicle_telemetry",
    )
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = evaluate_dataset(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.require_real and not result["is_real_road_data"]:
        print("[range-eval] REFUSED: input is not declared real_vehicle_telemetry")
        return 2
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
