"""Run ten edit -> confirm -> replan -> verify -> rollback acceptance cases.

The cases deliberately exercise the same public API used by the detail page.
They use a completed trip as an isolated fixture, apply one edit (or one
small batch), wait for the route rebuild, assert the edit is present and the
verification is passed, then roll the patch(es) back in reverse order.  This
keeps the fixture reusable while proving that edits are not merely rendered
locally in the browser.

Usage::

    python deploy/edit_replan_acceptance.py
    python deploy/edit_replan_acceptance.py --trip-id trip_xxx

The default fixture is the checked-in live-demo trip when it exists.  A
different completed trip may be supplied with ``--trip-id``; it must contain
at least one optional attraction for the semantic delete/replace cases.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests


DEFAULT_TRIP_ID = "trip_a4328f2fa4b0"


class AcceptanceFailure(RuntimeError):
    pass


def request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int = 120,
    **kwargs: Any,
) -> dict[str, Any]:
    response = session.request(method, url, timeout=timeout, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:500]}
    if response.status_code >= 400:
        raise AcceptanceFailure(f"{method} {url} -> {response.status_code}: {payload}")
    return payload


def get_trip(session: requests.Session, base: str) -> dict[str, Any]:
    return request(session, "GET", base)


def activity_name(activity: dict[str, Any]) -> str:
    return str((activity.get("place") or {}).get("name") or "").strip()


def activity_names(trip: dict[str, Any]) -> list[str]:
    return [
        activity_name(activity)
        for day in trip.get("days") or []
        for activity in day.get("activities") or []
    ]


def map_point(trip: dict[str, Any], longitude_delta: float, latitude_delta: float) -> tuple[float, float]:
    """Choose a test point near this fixture's destination, not a fixed city."""
    coordinates = ((trip.get("request") or {}).get("destination") or {}).get("coordinates") or {}
    try:
        return (
            float(coordinates["longitude"]) + longitude_delta,
            float(coordinates["latitude"]) + latitude_delta,
        )
    except (KeyError, TypeError, ValueError):
        return (116.045, 29.448)


def optional_attraction(
    trip: dict[str, Any],
    *,
    preferred_day: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    days = trip.get("days") or []
    ordered = sorted(
        days,
        key=lambda day: (0 if day.get("id") == preferred_day else 1, day.get("day_index", 0)),
    )
    for day in ordered:
        for activity in day.get("activities") or []:
            if (
                activity.get("type") == "attraction"
                and not activity.get("required")
                and not activity.get("locked")
            ):
                return day, activity
    raise AcceptanceFailure("fixture has no unlocked optional attraction")


def current_names(trip: dict[str, Any], category: str | None = None) -> set[str]:
    allowed = {"attractions", "hotels", "meals"}
    wanted_type = {"attractions": "attraction", "hotels": "hotel", "meals": "meal"}.get(category or "")
    return {
        activity_name(activity)
        for day in trip.get("days") or []
        for activity in day.get("activities") or []
        if not wanted_type or activity.get("type") == wanted_type
        if category is None or category in allowed
    }


def candidates(
    session: requests.Session,
    base: str,
    category: str,
) -> list[dict[str, Any]]:
    return request(
        session,
        "GET",
        f"{base}/recommendations",
        params={"category": category},
    ).get("items") or []


def unused_candidate(
    session: requests.Session,
    base: str,
    trip: dict[str, Any],
    category: str,
) -> dict[str, Any]:
    used = current_names(trip, category)
    for candidate in candidates(session, base, category):
        name = str((candidate.get("place") or {}).get("name") or "").strip()
        if name and name not in used and (candidate.get("place") or {}).get("coordinates"):
            return candidate
    raise AcceptanceFailure(f"fixture has no unused {category} recommendation")


def preview_map(
    session: requests.Session,
    base: str,
    *,
    day_id: str,
    category: str,
    name: str,
    longitude: float,
    latitude: float,
) -> dict[str, Any]:
    return request(
        session,
        "POST",
        f"{base}/patches/preview-map-point",
        json={
            "day_id": day_id,
            "category": category,
            "name": name,
            "address": "编辑验收测试点",
            "longitude": longitude,
            "latitude": latitude,
        },
    )


def preview_candidate(
    session: requests.Session,
    base: str,
    *,
    day_id: str,
    category: str,
    candidate_id: str,
    operation: str = "add",
    target_activity_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "day_id": day_id,
        "category": category,
        "candidate_id": candidate_id,
        "operation": operation,
    }
    if target_activity_id:
        body["target_activity_id"] = target_activity_id
    return request(session, "POST", f"{base}/patches/preview", json=body)


def preview_delete(
    session: requests.Session,
    base: str,
    *,
    day_id: str,
    activity_id: str,
) -> dict[str, Any]:
    return request(
        session,
        "POST",
        f"{base}/patches/preview-delete",
        json={"day_id": day_id, "activity_id": activity_id},
    )


def apply(session: requests.Session, base: str, patch_id: str) -> dict[str, Any]:
    result = request(session, "POST", f"{base}/patches/{patch_id}/apply")
    if not result.get("route_replan_required"):
        raise AcceptanceFailure(f"patch {patch_id} was applied without replan marker")
    return result


def replan(session: requests.Session, base: str) -> dict[str, Any]:
    # Route edits are rebuilt through the same worker/SSE planning path as a
    # fresh plan.  This keeps the acceptance suite aligned with the public API
    # used by the detail page instead of relying on a removed synchronous
    # ``editing/replan`` endpoint.
    request(session, "POST", f"{base}/planning/start", timeout=120)
    # A real edit replan performs the same multi-agent search, route and
    # verification loop as a fresh plan.  External map/travel providers can
    # legitimately take several minutes; timing out at five minutes leaves a
    # still-running job and makes the next case mutate a dirty fixture.  Keep
    # polling until the worker's normal acceptance window expires.
    deadline = time.monotonic() + 900
    result: dict[str, Any] = {}
    while time.monotonic() < deadline:
        result = request(session, "GET", f"{base}/planning", timeout=30)
        if result.get("status") == "completed":
            break
        if result.get("status") in {"failed", "clarification_required"}:
            raise AcceptanceFailure(f"route replan stopped: {result}")
        time.sleep(2)
    else:
        raise AcceptanceFailure("route replan timed out")
    verification = result.get("verification_result") or {}
    if verification.get("passed") is not True:
        raise AcceptanceFailure(f"edited trip verification did not pass: {verification}")
    # The planning snapshot carries progress and verification; the public
    # trip resource carries the persisted activities.  Return both so each
    # case can assert that the confirmed edit survived the worker rebuild.
    result["trip"] = get_trip(session, base)
    return result


def rollback(session: requests.Session, base: str, patch_id: str) -> None:
    request(session, "POST", f"{base}/patches/{patch_id}/rollback", timeout=120)


def assert_activity_present(trip: dict[str, Any], name: str) -> None:
    if name not in activity_names(trip):
        raise AcceptanceFailure(f"edited activity was not present after replan: {name}")


def assert_activity_absent(trip: dict[str, Any], name: str) -> None:
    if name in activity_names(trip):
        raise AcceptanceFailure(f"deleted activity still appears after replan: {name}")


@dataclass
class CaseContext:
    session: requests.Session
    base: str
    original: dict[str, Any]
    patch_ids: list[str] = field(default_factory=list)

    def track(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.patch_ids.append(str(patch["id"]))
        return patch

    def finish_and_restore(self) -> None:
        # Replan clears pending_edit_patch_ids intentionally.  Rollback still
        # uses the persisted per-patch snapshot; reverse order also handles a
        # batch where the second patch's backup contains the first edit.
        for patch_id in reversed(self.patch_ids):
            rollback(self.session, self.base, patch_id)
        restored = get_trip(self.session, self.base)
        if activity_names(restored) != activity_names(self.original):
            raise AcceptanceFailure("fixture was not restored after rollback")


def case_map_attraction(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    day = next(day for day in trip["days"] if day["id"] == "day_2")
    longitude, latitude = map_point(trip, 0.004, 0.002)
    patch = ctx.track(preview_map(
        ctx.session, ctx.base,
        day_id=day["id"], category="attractions", name="地图新增自然景点",
        longitude=longitude, latitude=latitude,
    ))
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, "地图新增自然景点")


def case_map_meal(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    longitude, latitude = map_point(trip, -0.003, 0.002)
    patch = ctx.track(preview_map(
        ctx.session, ctx.base,
        day_id="day_2", category="meals", name="地图新增晚餐",
        longitude=longitude, latitude=latitude,
    ))
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, "地图新增晚餐")


def case_map_hotel(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    longitude, latitude = map_point(trip, 0.002, -0.003)
    patch = ctx.track(preview_map(
        ctx.session, ctx.base,
        day_id="day_3", category="hotels", name="地图新增连续住宿",
        longitude=longitude, latitude=latitude,
    ))
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, "地图新增连续住宿")


def case_candidate_attraction(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    candidate = unused_candidate(ctx.session, ctx.base, trip, "attractions")
    patch = ctx.track(preview_candidate(
        ctx.session, ctx.base, day_id="day_3", category="attractions",
        candidate_id=candidate["candidate_id"],
    ))
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, str((candidate.get("place") or {}).get("name")))


def case_candidate_meal(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    candidate = unused_candidate(ctx.session, ctx.base, trip, "meals")
    patch = ctx.track(preview_candidate(
        ctx.session, ctx.base, day_id="day_2", category="meals",
        candidate_id=candidate["candidate_id"],
    ))
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, str((candidate.get("place") or {}).get("name")))


def case_direct_delete(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    day, target = optional_attraction(trip, preferred_day="day_2")
    patch = ctx.track(preview_delete(
        ctx.session, ctx.base, day_id=day["id"], activity_id=target["id"],
    ))
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_absent(updated, activity_name(target))


def case_semantic_delete(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    day, target = optional_attraction(trip, preferred_day="day_3")
    result = request(
        ctx.session, "POST", f"{ctx.base}/editing/interpret", timeout=180,
        json={
            "message": f"请删除第{day['day_index']}天的{activity_name(target)}，其他安排保持不变",
            "current_day_id": day["id"],
            "current_target_id": target["id"],
        },
    )
    patch = result.get("patch")
    if not patch or patch.get("operation") != "delete":
        raise AcceptanceFailure(f"semantic delete did not return a delete preview: {result}")
    ctx.track(patch)
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_absent(updated, activity_name(target))


def case_semantic_replace(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    day, target = optional_attraction(trip, preferred_day="day_2")
    candidate = unused_candidate(ctx.session, ctx.base, trip, "attractions")
    new_name = str((candidate.get("place") or {}).get("name") or "").strip()
    result = request(
        ctx.session, "POST", f"{ctx.base}/editing/interpret", timeout=180,
        json={
            "message": f"请把第{day['day_index']}天的{activity_name(target)}替换为{new_name}，其余安排不变",
            "current_day_id": day["id"],
            "current_target_id": target["id"],
        },
    )
    patch = result.get("patch")
    if not patch or patch.get("operation") != "replace":
        raise AcceptanceFailure(f"semantic replace did not return a replace preview: {result}")
    ctx.track(patch)
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, new_name)
    assert_activity_absent(updated, activity_name(target))


def case_batch_add_delete(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    longitude, latitude = map_point(trip, 0.005, -0.002)
    add_patch = ctx.track(preview_map(
        ctx.session, ctx.base,
        day_id="day_2", category="attractions", name="批量编辑临时点",
        longitude=longitude, latitude=latitude,
    ))
    applied = apply(ctx.session, ctx.base, add_patch["id"])
    added = next(
        (
            activity
            for day in applied["trip"].get("days") or []
            for activity in day.get("activities") or []
            if activity_name(activity) == "批量编辑临时点"
        ),
        None,
    )
    if not added:
        raise AcceptanceFailure("batch add did not persist the map-selected activity")
    delete_patch = ctx.track(preview_delete(
        ctx.session, ctx.base, day_id="day_2", activity_id=added["id"],
    ))
    apply(ctx.session, ctx.base, delete_patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_absent(updated, "批量编辑临时点")


def case_semantic_add(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    candidate = unused_candidate(ctx.session, ctx.base, trip, "attractions")
    name = str((candidate.get("place") or {}).get("name") or "").strip()
    result = request(
        ctx.session, "POST", f"{ctx.base}/editing/interpret", timeout=180,
        json={
            "message": f"请在第2天加入{name}作为下午景点，先给我修改预览，不要删除其他必达安排",
            "current_day_id": "day_2",
        },
    )
    patch = result.get("patch")
    if not patch or patch.get("operation") not in {"add", "replace"}:
        raise AcceptanceFailure(f"semantic add did not return an executable preview: {result}")
    ctx.track(patch)
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, name)


# The first generated version of this harness contained mojibake literals in
# the three natural-language cases.  Keep the public API calls identical, but
# use real Chinese user messages so these cases exercise the same intent
# parser that the UI uses instead of testing an accidental gibberish fallback.
def case_semantic_replace(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    day, target = optional_attraction(trip, preferred_day="day_2")
    candidate = unused_candidate(ctx.session, ctx.base, trip, "attractions")
    new_name = str((candidate.get("place") or {}).get("name") or "").strip()
    result = request(
        ctx.session, "POST", f"{ctx.base}/editing/interpret", timeout=180,
        json={
            "message": f"请把第{day['day_index']}天的{activity_name(target)}替换为{new_name}，其余安排不变。",
            "current_day_id": day["id"],
            "current_target_id": target["id"],
        },
    )
    patch = result.get("patch")
    if not patch or patch.get("operation") != "replace":
        raise AcceptanceFailure(f"semantic replace did not return a delete preview: {result}")
    ctx.track(patch)
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, new_name)
    assert_activity_absent(updated, activity_name(target))


def case_batch_add_delete(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    longitude, latitude = map_point(trip, 0.005, -0.002)
    add_patch = ctx.track(preview_map(
        ctx.session, ctx.base,
        day_id="day_2", category="attractions", name="批量编辑临时点",
        longitude=longitude, latitude=latitude,
    ))
    applied = apply(ctx.session, ctx.base, add_patch["id"])
    added = next(
        (
            activity
            for day in applied["trip"].get("days") or []
            for activity in day.get("activities") or []
            if activity_name(activity) == "批量编辑临时点"
        ),
        None,
    )
    if not added:
        raise AcceptanceFailure("batch add did not persist the map-selected activity")
    delete_patch = ctx.track(preview_delete(
        ctx.session, ctx.base, day_id="day_2", activity_id=added["id"],
    ))
    apply(ctx.session, ctx.base, delete_patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_absent(updated, "批量编辑临时点")


def case_semantic_add(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    candidate = unused_candidate(ctx.session, ctx.base, trip, "attractions")
    name = str((candidate.get("place") or {}).get("name") or "").strip()
    result = request(
        ctx.session, "POST", f"{ctx.base}/editing/interpret", timeout=180,
        json={
            "message": f"请在第2天加入{name}作为下午景点，先给我修改预览，不要删除其他必到安排。",
            "current_day_id": "day_2",
        },
    )
    patch = result.get("patch")
    if not patch or patch.get("operation") not in {"add", "replace"}:
        raise AcceptanceFailure(f"semantic add did not return an executable preview: {result}")
    ctx.track(patch)
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, name)


# Keep the map/delete cases in the same human-readable form as the UI.  This
# also prevents an old generated harness from accidentally exercising the
# model's unknown-intent path with mojibake literals.
def case_map_attraction(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    day = next(day for day in trip["days"] if day["id"] == "day_2")
    longitude, latitude = map_point(trip, 0.004, 0.002)
    patch = ctx.track(preview_map(
        ctx.session, ctx.base,
        day_id=day["id"], category="attractions", name="地图新增自然景点",
        longitude=longitude, latitude=latitude,
    ))
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, "地图新增自然景点")


def case_map_meal(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    longitude, latitude = map_point(trip, -0.003, 0.002)
    patch = ctx.track(preview_map(
        ctx.session, ctx.base,
        day_id="day_2", category="meals", name="地图新增晚餐",
        longitude=longitude, latitude=latitude,
    ))
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, "地图新增晚餐")


def case_map_hotel(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    longitude, latitude = map_point(trip, 0.002, -0.003)
    patch = ctx.track(preview_map(
        ctx.session, ctx.base,
        day_id="day_3", category="hotels", name="地图新增连续住宿",
        longitude=longitude, latitude=latitude,
    ))
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_present(updated, "地图新增连续住宿")


def case_semantic_delete(ctx: CaseContext) -> None:
    trip = get_trip(ctx.session, ctx.base)
    day, target = optional_attraction(trip, preferred_day="day_3")
    result = request(
        ctx.session, "POST", f"{ctx.base}/editing/interpret", timeout=180,
        json={
            "message": f"请删除第{day['day_index']}天的{activity_name(target)}，其他安排保持不变。",
            "current_day_id": day["id"],
            "current_target_id": target["id"],
        },
    )
    patch = result.get("patch")
    if not patch or patch.get("operation") != "delete":
        raise AcceptanceFailure(f"semantic delete did not return a delete preview: {result}")
    ctx.track(patch)
    apply(ctx.session, ctx.base, patch["id"])
    updated = replan(ctx.session, ctx.base)["trip"]
    assert_activity_absent(updated, activity_name(target))


CASES: list[tuple[str, Callable[[CaseContext], None]]] = [
    ("地图选点新增景点", case_map_attraction),
    ("地图选点新增餐饮", case_map_meal),
    ("地图选点新增住宿", case_map_hotel),
    ("候选池新增景点", case_candidate_attraction),
    ("候选池新增餐饮", case_candidate_meal),
    ("直接删除原有景点", case_direct_delete),
    ("自然语言删除景点", case_semantic_delete),
    ("自然语言替换景点", case_semantic_replace),
    ("批量新增后删除再统一重排", case_batch_add_delete),
    ("自然语言新增景点", case_semantic_add),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--trip-id", default=DEFAULT_TRIP_ID)
    parser.add_argument("--output", default="artifacts/edit-replan-acceptance.json")
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--end-index", type=int, default=len(CASES))
    args = parser.parse_args()
    base = f"{args.base_url.rstrip('/')}/api/v1/trips/{args.trip_id}"
    session = requests.Session()
    session.headers["Accept"] = "application/json"
    evidence: dict[str, Any] = {
        "base_url": args.base_url.rstrip("/"),
        "trip_id": args.trip_id,
        "cases": [],
    }
    try:
        original = get_trip(session, base)
        if original.get("status") != "completed":
            raise AcceptanceFailure(f"fixture status is not completed: {original.get('status')}")
        selected_cases = [
            (index, name, function)
            for index, (name, function) in enumerate(CASES, start=1)
            if args.start_index <= index <= args.end_index
        ]
        if not selected_cases:
            raise AcceptanceFailure("no edit cases selected")
        for index, name, function in selected_cases:
            started = time.monotonic()
            context = CaseContext(session=session, base=base, original=original)
            try:
                function(context)
                context.finish_and_restore()
                row = {
                    "index": index,
                    "name": name,
                    "status": "passed",
                    "seconds": round(time.monotonic() - started, 2),
                }
                evidence["cases"].append(row)
                print(f"[edit-replan] PASS {index:02d} {name} ({row['seconds']}s)", flush=True)
            except Exception:
                # Restore anything that was applied before the failure so the
                # next local run starts from the same fixture.
                error = sys.exc_info()[1]
                try:
                    context.finish_and_restore()
                except Exception as restore_error:
                    print(f"[edit-replan] restore warning: {restore_error}", file=sys.stderr)
                row = {
                    "index": index,
                    "name": name,
                    "status": "failed",
                    "error": str(error),
                    "seconds": round(time.monotonic() - started, 2),
                }
                evidence["cases"].append(row)
                print(f"[edit-replan] FAIL {index:02d} {name}: {error}", file=sys.stderr, flush=True)
                # Keep executing the remaining cases. A broken edit should
                # not hide independent failures, and every case is restored
                # to the same completed fixture before the next one starts.
        passed = sum(item.get("status") == "passed" for item in evidence["cases"])
        failed = len(evidence["cases"]) - passed
        evidence["status"] = "passed" if failed == 0 else "failed"
        print(f"[edit-replan] RESULT: {passed}/{len(selected_cases)} edit cases passed", flush=True)
        return_code = 0 if failed == 0 else 1
    except (requests.RequestException, AcceptanceFailure) as error:
        evidence["status"] = "failed"
        evidence["error"] = str(error)
        print(f"[edit-replan] FAIL: {error}", file=sys.stderr)
        return_code = 1
    finally:
        from pathlib import Path

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
