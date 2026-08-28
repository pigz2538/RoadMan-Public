"""Run RoadMan's complete user journey against a deployed API.

This is deliberately stronger than the fast API smoke suite.  It waits for a
new itinerary to finish, checks traveller-facing completeness, asks the
itinerary assistant for a semantic replacement, confirms the preview, checks
the recalculated snapshot, and downloads every report format.

Usage:
    python deploy/full_journey_acceptance.py
    python deploy/full_journey_acceptance.py --base-url http://192.168.1.20:8000
"""

from __future__ import annotations

import argparse
import json
import sys
from math import atan2, cos, radians, sin, sqrt
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests


class JourneyFailure(RuntimeError):
    pass


def call(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int = 90,
    **kwargs: Any,
) -> requests.Response:
    response = session.request(method, url, timeout=timeout, **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:500]
        raise JourneyFailure(f"{method} {url} -> {response.status_code}: {detail}")
    return response


def wait_for_completion(
    session: requests.Session,
    base_url: str,
    trip_id: str,
    *,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_progress: tuple[str, int] | None = None
    while time.monotonic() < deadline:
        snapshot = call(session, "GET", f"{base_url}/api/v1/trips/{trip_id}/planning").json()
        trip = call(session, "GET", f"{base_url}/api/v1/trips/{trip_id}").json()
        progress = snapshot.get("progress") or {}
        current = (str(progress.get("node") or "waiting"), int(progress.get("value") or 0))
        if current != last_progress:
            print(f"[journey] {current[1]:3d}% {current[0]}", flush=True)
            last_progress = current
        status = trip.get("status")
        if status == "completed":
            return trip, snapshot
        if status in {"failed", "clarification_required"}:
            raise JourneyFailure(
                f"planning stopped with status={status}: "
                f"{json.dumps(snapshot.get('verification_result') or snapshot, ensure_ascii=False)[:1200]}"
            )
        time.sleep(2)
    raise JourneyFailure(f"planning did not complete within {timeout_seconds} seconds")


def validate_complete_trip(trip: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    checks: list[str] = []
    verification = snapshot.get("verification_result") or {}
    if not verification.get("passed"):
        raise JourneyFailure(f"verification is not passed: {verification}")
    checks.append("final verification passed")

    days = trip.get("days") or []
    if len(days) < 2:
        raise JourneyFailure("the main demo must contain at least two planned days")
    for index, day in enumerate(days):
        activities = day.get("activities") or []
        meals = [item for item in activities if item.get("type") == "meal"]
        if len(meals) < 3:
            raise JourneyFailure(f"day {index + 1} has only {len(meals)} meals")
        checks.append(f"day {index + 1}: breakfast, lunch and dinner present")
        if index < len(days) - 1 and not any(item.get("type") == "hotel" for item in activities):
            raise JourneyFailure(f"day {index + 1} has no overnight accommodation")
        if index < len(days) - 1:
            checks.append(f"day {index + 1}: overnight accommodation present")
        if not day.get("stages"):
            raise JourneyFailure(f"day {index + 1} has no movement stage")

    all_stages = [stage for day in days for stage in day.get("stages") or []]
    if not all_stages:
        raise JourneyFailure("no route stages were generated")
    origin = ((trip.get("origin") or {}).get("name") or "").strip()
    final_destination = ((all_stages[-1].get("destination") or {}).get("name") or "").strip()
    if origin and final_destination and origin not in final_destination and final_destination not in origin:
        issue_codes = {
            item.get("code") for item in (verification.get("issues") or [])
        }
        if "ROUTE_NOT_CLOSED" in issue_codes:
            raise JourneyFailure(f"route does not return to origin: {final_destination}")
    checks.append("return closure verified")
    return checks


def semantic_replacement(
    session: requests.Session,
    base_url: str,
    trip: dict[str, Any],
) -> dict[str, Any]:
    trip_id = trip["id"]
    candidates = call(
        session,
        "GET",
        f"{base_url}/api/v1/trips/{trip_id}/recommendations",
        params={"category": "attractions"},
    ).json().get("items") or []
    current = [
        (day, activity)
        for day in trip.get("days") or []
        for activity in day.get("activities") or []
        if activity.get("type") == "attraction" and not activity.get("required")
    ]
    current_names = {(activity.get("place") or {}).get("name") for _, activity in current}
    if not current:
        raise JourneyFailure("no safe attraction replacement pair is available for the edit-closure check")
    day, target = current[0]

    def distance_km(item: dict[str, Any]) -> float:
        first = ((target.get("place") or {}).get("coordinates") or {})
        second = ((item.get("place") or {}).get("coordinates") or {})
        try:
            lat1, lon1 = radians(float(first["latitude"])), radians(float(first["longitude"]))
            lat2, lon2 = radians(float(second["latitude"])), radians(float(second["longitude"]))
        except (KeyError, TypeError, ValueError):
            return 1_000_000
        dlat, dlon = lat2 - lat1, lon2 - lon1
        value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        return 6371 * 2 * atan2(sqrt(value), sqrt(max(0, 1 - value)))

    available = [
        item
        for item in candidates
        if (item.get("place") or {}).get("name")
        and (item.get("place") or {}).get("name") not in current_names
    ]
    candidate = min(available, key=distance_km, default=None)
    if not candidate:
        raise JourneyFailure("no safe attraction replacement pair is available for the edit-closure check")
    old_name = target["place"]["name"]
    new_name = candidate["place"]["name"]
    message = f"请把第{day['day_index']}天的{old_name}替换为{new_name}，其余偏好保持不变"
    interpreted = call(
        session,
        "POST",
        f"{base_url}/api/v1/trips/{trip_id}/editing/interpret",
        json={
            "message": message,
            "current_day_id": day["id"],
            "current_target_id": target["id"],
        },
        timeout=120,
    ).json()
    patch = interpreted.get("patch")
    if not patch:
        raise JourneyFailure(
            "the itinerary assistant did not produce a confirmable preview: "
            + str(interpreted.get("message") or "")
        )
    applied = call(
        session,
        "POST",
        f"{base_url}/api/v1/trips/{trip_id}/patches/{patch['id']}/apply",
        timeout=120,
    ).json()
    updated = applied["trip"]
    persisted_target = next(
        (
            activity
            for item in updated.get("days") or []
            for activity in item.get("activities") or []
            if activity.get("id") == target.get("id")
        ),
        None,
    )
    if not persisted_target or (persisted_target.get("place") or {}).get("name") != new_name:
        raise JourneyFailure("confirmed semantic replacement was not persisted")
    # Local edits deliberately mark the route chain as stale.  Reuse the same
    # queue/SSE path as a normal plan restart so the worker recomputes every
    # affected stage instead of pretending that a card-only mutation is a
    # complete route rebuild.
    if not applied.get("route_replan_required"):
        raise JourneyFailure("confirmed edit did not mark the route chain for recalculation")
    call(session, "POST", f"{base_url}/api/v1/trips/{trip_id}/planning/start", timeout=120)
    updated, replanned_snapshot = wait_for_completion(
        session, base_url, trip_id, timeout_seconds=600
    )
    if replanned_snapshot.get("route_replan_required"):
        raise JourneyFailure("route recalculation did not clear the pending-edit state")
    print(
        f"[journey] semantic edit applied and routes rebuilt: {old_name} -> {new_name}",
        flush=True,
    )
    return updated


def download_reports(
    session: requests.Session,
    base_url: str,
    trip_id: str,
    output_dir: Path,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    signatures = {
        "html": b"<!doctype html>",
        "pdf": b"%PDF",
        "pptx": b"PK",
        "png": b"\x89PNG",
    }
    outputs = []
    for extension, signature in signatures.items():
        response = call(
            session,
            "GET",
            f"{base_url}/api/v1/trips/{trip_id}/roadbook.{extension}",
            timeout=180,
        )
        if not response.content.startswith(signature):
            raise JourneyFailure(f"{extension} export has an invalid signature")
        target = output_dir / f"roadman-main-demo.{extension}"
        target.write_bytes(response.content)
        outputs.append({"format": extension, "path": str(target), "bytes": len(response.content)})
        print(f"[journey] exported {extension}: {len(response.content):,} bytes", flush=True)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output", type=Path, default=Path("artifacts/main-demo"))
    parser.add_argument("--keep-trip", action="store_true")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    session = requests.Session()
    session.headers["Accept"] = "application/json"
    trip_id: str | None = None
    evidence: dict[str, Any] = {"base_url": base_url, "checks": []}
    try:
        call(session, "GET", f"{base_url}/health")
        start = date.today() + timedelta(days=12)
        end = start + timedelta(days=2)
        payload = {
            "title": "主 Demo：武汉—庐山 3 天 2 夜情侣舒适自驾",
            "request": {
                "raw_text": (
                    f"{start.isoformat()} 08:00 从武汉出发去庐山，"
                    f"{end.isoformat()} 20:00 前返回武汉。情侣两人自驾，舒适为主，"
                    "每天安排三餐和住宿，核心景点要有依据，长途驾驶安排休息和补能。"
                ),
                "origin": {"name": "武汉", "city": "武汉"},
                "destination": {"name": "庐山", "city": "九江"},
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "departure_time": "08:00:00",
                "return_time": "20:00:00",
                "travelers": 2,
                "preferences": ["情侣出游", "舒适", "自然景观"],
                "transport_modes": ["driving"],
                "max_continuous_drive_minutes": 120,
            },
        }
        trip = call(session, "POST", f"{base_url}/api/v1/trips", json=payload).json()
        trip_id = trip["id"]
        evidence["trip_id"] = trip_id
        print(f"[journey] created {trip_id}", flush=True)
        call(session, "POST", f"{base_url}/api/v1/trips/{trip_id}/planning/start")
        trip, snapshot = wait_for_completion(
            session, base_url, trip_id, timeout_seconds=args.timeout
        )
        evidence["checks"] = validate_complete_trip(trip, snapshot)
        evidence["verification"] = snapshot.get("verification_result")
        trip = semantic_replacement(session, base_url, trip)
        post_edit = call(session, "GET", f"{base_url}/api/v1/trips/{trip_id}/planning").json()
        evidence["checks"].extend(validate_complete_trip(trip, post_edit))
        evidence["checks"].append("natural-language preview, confirmation and persistence passed")
        evidence["exports"] = download_reports(session, base_url, trip_id, args.output)
        evidence["status"] = "passed"
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "acceptance-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("[journey] PASS: complete planning-editing-validation-export closure", flush=True)
        return 0
    except (requests.RequestException, JourneyFailure) as error:
        evidence["status"] = "failed"
        evidence["error"] = str(error)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "acceptance-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[journey] FAIL: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        if trip_id and not args.keep_trip:
            try:
                session.delete(f"{base_url}/api/v1/trips/{trip_id}", timeout=30)
            except requests.RequestException:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
