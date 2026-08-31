"""Exercise casual, ambiguous and high-constraint prompts against live Agents.

Unlike unit tests, this suite calls the deployed preflight Agent for every
case. Cases marked ``full`` also create a real Trip, wait through automatic
repair loops, verify persisted itinerary invariants and export HTML evidence.
Failures are preserved in the JSON report; prompts are never rewritten to make
the score look better.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS = ROOT / "evaluation" / "weird_live_scenarios.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "weird-live-acceptance.json"

_DISALLOWED_COMFORT_LODGING_RE = re.compile(
    r"(?:青旅|青年旅舍|青年旅社|青年公寓|学生公寓|旅舍|背包客栈|青年旅店|青年旅馆|青年客栈|学生宿舍|宿舍型|床位房|胶囊旅馆|太空舱|hostel|backpacker)",
    re.IGNORECASE,
)


def _contains(value: Any, expected: str) -> bool:
    return expected.casefold() in str(value or "").casefold()


def _as_list(value: Any) -> list[Any]:
    """Normalize scalar/list expectation fields from old and new fixtures."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if item]
    return [value]


def _score_preflight(payload: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    extracted = payload.get("extracted") or {}
    if expected.get("clarification_required"):
        # A clarification is the intended, safe terminal state for prompts
        # that omit a location/date or are semantically infeasible.  Do not
        # turn that pause into a full-trip run when --force-full is enabled.
        if payload.get("ready") or not (
            payload.get("issues")
            or payload.get("missing_fields")
            or payload.get("clarification_question")
        ):
            failures.append("expected_clarification")
        return failures
    confirmation_ready = (
        payload.get("confirmation_required")
        and payload.get("semantic_checked")
        and not payload.get("issues")
    )
    if not payload.get("ready") and not confirmation_ready:
        failures.append("not_ready")
    origin = extracted.get("origin_name") or ((extracted.get("origin") or {}).get("name"))
    destinations = [
        extracted.get("destination_name"),
        *((extracted.get("destination_names") or [])),
    ]
    expected_origins = []
    expected_origins.extend(_as_list(expected.get("origin")))
    expected_origins.extend(_as_list(expected.get("origin_any")))
    expected_origins.extend(_as_list(expected.get("origin_contains")))
    if expected_origins and not any(_contains(origin, target) for target in expected_origins):
        failures.append("origin")
    expected_destinations = _as_list(expected.get("destination_any"))
    expected_destinations.extend(_as_list(expected.get("destination")))
    expected_destinations.extend(_as_list(expected.get("destination_contains")))
    if any(expected_destinations) and not any(
        _contains(actual, target)
        for actual in destinations
        for target in expected_destinations
        if actual and target
    ):
        failures.append("destination")
    if expected.get("travelers") and extracted.get("travelers") != expected["travelers"]:
        failures.append("travelers")
    if expected.get("date_order_valid"):
        try:
            if date.fromisoformat(extracted["end_date"]) < date.fromisoformat(extracted["start_date"]):
                failures.append("date_order")
        except (KeyError, TypeError, ValueError):
            failures.append("date_fields")
    if expected.get("not_in_past"):
        try:
            if date.fromisoformat(extracted["start_date"]) < date.today():
                failures.append("start_date_in_past")
        except (KeyError, TypeError, ValueError):
            failures.append("date_fields")
    must_text = " ".join(
        str(item) for item in [
            *(extracted.get("must_visit_names") or []),
            *(extracted.get("travel_intents") or []),
            extracted.get("_source_raw_text"),
        ]
    )
    for name in expected.get("must_visit") or []:
        if not _contains(must_text, name):
            failures.append(f"must_visit:{name}")
    if expected.get("transport_any"):
        transport_text = " ".join(
            str(item)
            for item in [
                *(extracted.get("transport_modes") or []),
                extracted.get("primary_transport_mode"),
                extracted.get("transport_mode"),
            ]
        )
        if not any(_contains(transport_text, mode) for mode in expected["transport_any"]):
            failures.append("transport")
    if expected.get("special_event_any"):
        event_text = " ".join(
            str(item)
            for item in [
                *(extracted.get("special_events") or []),
                *(extracted.get("travel_intents") or []),
                *(extracted.get("preferences") or []),
                extracted.get("_source_raw_text"),
            ]
        )
        if not any(_contains(event_text, event) for event in expected["special_event_any"]):
            failures.append("special_event")
    return failures


def _trip_payload(case: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    origin = str(extracted.get("origin_name") or "").strip()
    destination_names = [str(item).strip() for item in extracted.get("destination_names") or [] if str(item).strip()]
    destination = str(extracted.get("destination_name") or (destination_names[0] if destination_names else "")).strip()
    must_names = list(dict.fromkeys([
        *(str(item).strip() for item in extracted.get("must_visit_names") or [] if str(item).strip()),
        *(str(item).strip() for item in case.get("expect", {}).get("must_visit") or [] if str(item).strip()),
    ]))
    request = {
        "raw_text": case["input"],
        "origin": {"name": origin, "city": origin},
        "destination": {"name": destination, "city": destination},
        "destination_names": destination_names or [destination],
        "destination_scope": extracted.get("destination_scope") or "unknown",
        "travel_intents": extracted.get("travel_intents") or [],
        "start_date": extracted.get("start_date"),
        "end_date": extracted.get("end_date"),
        "departure_time": extracted.get("departure_time") or "08:00:00",
        "return_time": extracted.get("return_time") or "20:00:00",
        "travelers": extracted.get("travelers") or 1,
        "preferences": extracted.get("preferences") or [],
        "transport_modes": extracted.get("transport_modes") or [],
        "special_events": extracted.get("special_events") or [],
        "max_days": extracted.get("max_days"),
        "must_visit": [{"name": name, "city": destination} for name in must_names],
        "stay_only_at_destination": bool(extracted.get("stay_only_at_destination")),
        "max_continuous_drive_minutes": 120,
        "max_daily_drive_minutes": 540,
    }
    return {"title": f"奇怪输入验收：{case['id']}", "request": request}


def _wait(session: requests.Session, base: str, trip_id: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout
    progress_log: list[dict[str, Any]] = []
    last: tuple[int, str] | None = None
    while time.monotonic() < deadline:
        trip = session.get(f"{base}/api/v1/trips/{trip_id}", timeout=30).json()
        snapshot = session.get(f"{base}/api/v1/trips/{trip_id}/planning", timeout=30).json()
        progress = snapshot.get("progress") or {}
        current = (int(progress.get("value") or 0), str(progress.get("node") or "waiting"))
        if current != last:
            progress_log.append({"value": current[0], "node": current[1]})
            print(f"[weird:{trip_id}] {current[0]:3d}% {current[1]}", flush=True)
            last = current
        if trip.get("status") == "completed":
            return trip, snapshot, progress_log
        if trip.get("status") in {"failed", "clarification_required"}:
            raise RuntimeError(
                f"status={trip.get('status')}: "
                + json.dumps(snapshot.get("verification_result") or snapshot, ensure_ascii=False)[:1800]
            )
        time.sleep(2)
    raise TimeoutError(f"planning exceeded {timeout}s")


def _validate_trip(
    trip: dict[str, Any],
    snapshot: dict[str, Any],
    case: dict[str, Any],
    extracted: dict[str, Any] | None = None,
) -> list[str]:
    failures: list[str] = []
    verification = snapshot.get("verification_result") or {}
    if not verification.get("passed"):
        failures.append("verification")
    days = trip.get("days") or []
    if not days:
        failures.append("no_days")
    extracted = extracted or {}
    if extracted.get("start_date") and str(trip.get("start_date")) != str(extracted["start_date"]):
        failures.append("persisted_start_date_mismatch")
    if extracted.get("end_date") and str(trip.get("end_date")) != str(extracted["end_date"]):
        failures.append("persisted_end_date_mismatch")
    all_activities = [item for day in days for item in day.get("activities") or []]
    all_stages = [item for day in days for item in day.get("stages") or []]
    itinerary_text = " ".join(
        [str((item.get("place") or {}).get("name") or "") for item in all_activities]
        + [str((item.get("origin") or {}).get("name") or "") for item in all_stages]
        + [str((item.get("destination") or {}).get("name") or "") for item in all_stages]
    )
    for name in case.get("expect", {}).get("must_visit") or []:
        if not _contains(itinerary_text, name):
            failures.append(f"planned_must_visit:{name}")
    for day_index, day in enumerate(days, 1):
        if not day.get("stages"):
            failures.append(f"day_{day_index}_no_stages")
        meals = [item for item in day.get("activities") or [] if item.get("type") == "meal"]
        if len(meals) < 3:
            failures.append(f"day_{day_index}_meals:{len(meals)}")
        day_date = str(day.get("date") or "")
        previous_end: datetime | None = None
        for stage in sorted(day.get("stages") or [], key=lambda item: str(item.get("planned_start") or "")):
            try:
                start = datetime.fromisoformat(stage["planned_start"])
                end = datetime.fromisoformat(stage["planned_end"])
            except (KeyError, TypeError, ValueError):
                continue
            if stage.get("mode") == "driving" and (
                start.date().isoformat() != day_date or end.date() != start.date()
            ):
                failures.append(f"driving_calendar_mismatch:{stage.get('id')}")
            if previous_end is not None and start < previous_end:
                failures.append(f"stage_overlap:{stage.get('id')}")
            previous_end = max(previous_end, end) if previous_end is not None else end
        for hotel in [item for item in day.get("activities") or [] if item.get("type") == "hotel"]:
            hotel_place = hotel.get("place") or {}
            hotel_text = " ".join(
                str(hotel_place.get(field) or "") for field in ("name", "address")
            )
            if _DISALLOWED_COMFORT_LODGING_RE.search(hotel_text):
                failures.append(f"disallowed_hotel:day_{day_index}:{hotel_place.get('name') or 'unknown'}")
    for stage in all_stages:
        try:
            start = datetime.fromisoformat(stage["planned_start"])
            end = datetime.fromisoformat(stage["planned_end"])
        except (KeyError, TypeError, ValueError):
            failures.append(f"invalid_stage_time:{stage.get('id')}")
            continue
        if end <= start:
            failures.append(f"non_positive_stage:{stage.get('id')}")
        route_segments = stage.get("route_segments") or []
        movement_minutes = (
            (route_segments[0] or {}).get("duration_minutes")
            if route_segments and isinstance(route_segments[0], dict)
            else stage.get("duration_minutes", 0)
        )
        if stage.get("mode") == "driving" and int(movement_minutes or 0) > 540:
            failures.append(f"driving_over_daily_limit:{stage.get('id')}")
        if stage.get("mode") in {"walking", "riding"} and stage.get("duration_minutes", 0) > 180:
            failures.append(f"active_transfer_too_long:{stage.get('id')}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--full-limit", type=int, default=3)
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="Run a full trip for every selected case that passes preflight, even when the fixture lacks full=true.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only the named scenario; repeat to select several cases.",
    )
    parser.add_argument("--keep-trips", action="store_true")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    if args.case_id:
        selected_ids = set(args.case_id)
        scenarios = [item for item in scenarios if item.get("id") in selected_ids]
        missing_ids = selected_ids - {str(item.get("id")) for item in scenarios}
        if missing_ids:
            parser.error(f"unknown case id(s): {', '.join(sorted(missing_ids))}")
    session = requests.Session()
    results: list[dict[str, Any]] = []
    created: list[str] = []
    full_count = 0
    started_suite = time.monotonic()
    try:
        for case in scenarios:
            started = time.monotonic()
            result: dict[str, Any] = {
                "id": case["id"],
                "input": case["input"],
                "full_requested": bool(case.get("full") or args.force_full),
            }
            try:
                response = session.post(
                    f"{base}/api/v1/trips/preflight",
                    json={"raw_text": case["input"], "confirmed": False},
                    timeout=240,
                )
                response.raise_for_status()
                preflight = response.json()
                result["preflight"] = {
                    "ready": preflight.get("ready"),
                    "confirmation_required": preflight.get("confirmation_required"),
                    "semantic_checked": preflight.get("semantic_checked"),
                    "issues": [item.get("code") for item in preflight.get("issues") or []],
                    "extracted": preflight.get("extracted"),
                }
                failures = _score_preflight(preflight, case.get("expect") or {})
                expected_clarification = bool((case.get("expect") or {}).get("clarification_required"))
                if (
                    (case.get("full") or args.force_full)
                    and not expected_clarification
                    and full_count < args.full_limit
                    and not failures
                ):
                    full_count += 1
                    trip = session.post(
                        f"{base}/api/v1/trips", json=_trip_payload(case, preflight.get("extracted") or {}), timeout=60
                    )
                    trip.raise_for_status()
                    trip_id = trip.json()["id"]
                    created.append(trip_id)
                    result["trip_id"] = trip_id
                    start = session.post(f"{base}/api/v1/trips/{trip_id}/planning/start", timeout=120)
                    start.raise_for_status()
                    planned, snapshot, progress = _wait(session, base, trip_id, args.timeout)
                    result["progress"] = progress
                    result["verification"] = snapshot.get("verification_result")
                    failures.extend(
                        _validate_trip(
                            planned,
                            snapshot,
                            case,
                            preflight.get("extracted") or {},
                        )
                    )
                    export = session.get(f"{base}/api/v1/trips/{trip_id}/roadbook.html", timeout=180)
                    if export.status_code != 200 or not export.content.lower().startswith(b"<!doctype html>"):
                        failures.append("html_export")
                    result["html_export_bytes"] = len(export.content)
                result["failures"] = failures
                result["passed"] = not failures
            except (requests.RequestException, RuntimeError, TimeoutError, ValueError) as error:
                result["passed"] = False
                result["error"] = str(error)
            result["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
            results.append(result)
            print(f"[weird] {case['id']}: {'PASS' if result['passed'] else 'FAIL'} {result.get('failures') or result.get('error') or ''}", flush=True)
    finally:
        if not args.keep_trips:
            for trip_id in created:
                try:
                    session.delete(f"{base}/api/v1/trips/{trip_id}", timeout=30)
                except requests.RequestException:
                    pass
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "base_url": base,
        "scenario_count": len(results),
        "full_journey_count": sum(bool(item.get("trip_id")) for item in results),
        "passed": sum(bool(item.get("passed")) for item in results),
        "failed": sum(not item.get("passed") for item in results),
        "duration_seconds": round(time.monotonic() - started_suite, 2),
        "model_mode": "non-thinking",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[weird] {report['passed']}/{report['scenario_count']} passed; full={report['full_journey_count']}; report={args.output}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
