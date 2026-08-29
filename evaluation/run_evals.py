"""Run the curated requirement-understanding evaluation set.

The default run is inexpensive: every case goes through the real preflight
Agent and its tool-backed special-event research.  ``--full`` additionally
runs the complete acceptance journey once via the dedicated deployment script.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from statistics import mean
from time import monotonic
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = Path(__file__).with_name("scenarios.json")


def _contains(actual: object, expected: str) -> bool:
    return expected.lower() in str(actual or "").lower()


def score_case(extracted: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    actual_origin = extracted.get("origin_name") or (extracted.get("origin") or {}).get("name")
    expected_origins = expected.get("origin_any") or [expected.get("origin_contains")]
    if any(expected_origins) and not any(
        _contains(actual_origin, candidate) for candidate in expected_origins if candidate
    ):
        failures.append("origin")
    actual_destination = extracted.get("destination_name") or (extracted.get("destination") or {}).get("name")
    expected_destinations = expected.get("destination_any") or [expected.get("destination_contains")]
    if any(expected_destinations) and not any(
        _contains(actual_destination, candidate) for candidate in expected_destinations if candidate
    ):
        failures.append("destination")
    if expected.get("travelers") is not None and extracted.get("travelers") != expected["travelers"]:
        failures.append("travelers")
    if expected.get("date_order_valid"):
        try:
            start = date.fromisoformat(str(extracted["start_date"]))
            end = date.fromisoformat(str(extracted["end_date"]))
            if end < start:
                failures.append("date_order")
        except (KeyError, TypeError, ValueError):
            failures.append("date_fields")
    if expected.get("transport_any"):
        modes = {str(item).lower() for item in extracted.get("transport_modes") or []}
        if not modes.intersection({str(item).lower() for item in expected["transport_any"]}):
            failures.append("transport_modes")
    if expected.get("special_event_any"):
        events = " ".join(str(item) for item in extracted.get("special_events") or [])
        if not any(_contains(events, item) for item in expected["special_event_any"]):
            failures.append("special_events")
    return failures


def _ops_metrics(session: requests.Session, base_url: str) -> dict[str, Any]:
    try:
        response = session.get(f"{base_url}/api/v1/ops/metrics", timeout=10)
        response.raise_for_status()
        payload = response.json()
        return payload.get("skills") if isinstance(payload, dict) else {}
    except (requests.RequestException, ValueError):
        return {}


def build_quality_metrics(
    results: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    full_exit: int | None,
) -> dict[str, Any]:
    total = len(results)
    latencies = [float(item["latency_ms"]) for item in results if item.get("latency_ms") is not None]
    sorted_latencies = sorted(latencies)
    p95_index = max(0, min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95) - 1))

    def delta(key: str) -> int:
        return max(0, int(after.get(key) or 0) - int(before.get(key) or 0))

    tool_calls = delta("total_calls")
    successful_calls = delta("successful_calls")
    before_usage = before.get("agent_usage") or {}
    after_usage = after.get("agent_usage") or {}
    token_usage = {
        key: max(0, int(after_usage.get(key) or 0) - int(before_usage.get(key) or 0))
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        )
    }
    return {
        "task_completion_rate": round(sum(bool(item.get("passed")) for item in results) / total, 4) if total else 0.0,
        "tool_call_success_rate": round(successful_calls / tool_calls, 4) if tool_calls else None,
        "tool_calls": tool_calls,
        "successful_tool_calls": successful_calls,
        "route_executability_rate": None if full_exit is None else (1.0 if full_exit == 0 else 0.0),
        "average_latency_ms": round(mean(latencies), 2) if latencies else None,
        "p95_latency_ms": round(sorted_latencies[p95_index], 2) if sorted_latencies else None,
        "token_cost": {"unit": "tokens", **token_usage},
        "estimated_cost_usd": None,
        "cost_note": "Token 来自模型官方 usage；模型动态单价未在代码中硬编码，因此不伪造美元金额。",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("artifacts/evaluation-results.json"))
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    session = requests.Session()
    initial_ops = _ops_metrics(session, base_url)
    results = []
    for scenario in scenarios:
        started_at = monotonic()
        try:
            response = session.post(
                f"{base_url}/api/v1/trips/preflight",
                json={"raw_text": scenario["input"], "confirmed": False},
                timeout=150,
            )
            response.raise_for_status()
            payload = response.json()
            failures = score_case(payload.get("extracted") or {}, scenario["expect"])
            result = {
                "id": scenario["id"],
                "passed": not failures,
                "failed_dimensions": failures,
                "ready": payload.get("ready"),
                "questions": [item.get("code") for item in payload.get("issues") or []],
                "latency_ms": round((monotonic() - started_at) * 1000, 2),
            }
        except (requests.RequestException, ValueError) as error:
            result = {
                "id": scenario["id"],
                "passed": False,
                "error": str(error),
                "latency_ms": round((monotonic() - started_at) * 1000, 2),
            }
        results.append(result)
        print(
            f"[eval] {scenario['id']}: {'PASS' if result['passed'] else 'FAIL'} "
            f"{result.get('failed_dimensions') or result.get('error') or ''}",
            flush=True,
        )
    full_exit = None
    if args.full:
        full_exit = subprocess.call(
            [
                sys.executable,
                str(ROOT / "deploy" / "full_journey_acceptance.py"),
                "--base-url",
                base_url,
            ],
            cwd=ROOT,
        )
    final_ops = _ops_metrics(session, base_url)
    quality_metrics = build_quality_metrics(results, initial_ops, final_ops, full_exit)
    summary = {
        "total": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "full_journey_exit_code": full_exit,
        "metrics": quality_metrics,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[eval] {summary['passed']}/{summary['total']} requirement cases passed")
    if full_exit not in {None, 0}:
        return 1
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
