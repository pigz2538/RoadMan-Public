"""Live smoke test for the Docker API surface.

The script deliberately prints status/summary fields only; provider credentials
and response payloads are never printed.  It creates a temporary trip/vehicle
for mutation coverage and removes the trip and vehicle when finished.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests


BASE = "http://127.0.0.1:8000"
session = requests.Session()
session.headers["Accept"] = "application/json"
rows: list[tuple[str, object, str, int, object]] = []


def summary(response: requests.Response) -> object:
    try:
        body = response.json()
    except ValueError:
        return {
            "content_type": response.headers.get("content-type", ""),
            "bytes": len(response.content),
        }
    if not isinstance(body, dict):
        return type(body).__name__
    output: dict[str, object] = {}
    for key in (
        "success",
        "status",
        "provider",
        "error_code",
        "code",
        "message",
        "count",
    ):
        if key in body:
            output[key] = body[key]
    if "items" in body and isinstance(body["items"], list):
        output["items"] = f"[{len(body['items'])} items]"
    if "detail" in body:
        detail = body["detail"]
        output["detail"] = detail.get("code") if isinstance(detail, dict) else str(detail)[:120]
    if not output:
        output = {key: body[key] for key in list(body)[:5]}
    return output


def call(
    label: str,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    params: dict[str, Any] | None = None,
    stream: bool = False,
    expected: set[int] | None = None,
    require_success: bool = False,
    timeout: int = 45,
) -> requests.Response | None:
    started = time.perf_counter()
    try:
        response = session.request(
            method,
            BASE + path,
            json=json_body,
            data=data,
            files=files,
            params=params,
            timeout=timeout,
            stream=stream,
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        expected = expected or {200, 201, 202, 204}
        result = "PASS" if response.status_code in expected else "FAIL"
        if stream:
            # SSE endpoints intentionally stay open. Reading one event proves
            # the connection and headers work without waiting for completion.
            try:
                next(response.iter_lines(decode_unicode=True))
            except (StopIteration, requests.RequestException):
                pass
            detail = {
                "content_type": response.headers.get("content-type", ""),
                "stream_checked": True,
            }
            response.close()
        else:
            detail = summary(response)
            if require_success and response.status_code in expected:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and payload.get("success") is False:
                    result = "FAIL"
        rows.append((label, response.status_code, result, elapsed, detail))
        print(f"[smoke] {label}: {response.status_code} {result} ({elapsed} ms) {detail}", flush=True)
        return response
    except Exception as error:  # pragma: no cover - live smoke diagnostics
        elapsed = round((time.perf_counter() - started) * 1000)
        detail = str(error)[:140]
        rows.append((label, "ERR", "FAIL", elapsed, detail))
        print(f"[smoke] {label}: ERR FAIL ({elapsed} ms) {detail}", flush=True)
        return None


def main() -> int:
    call("health", "GET", "/health")
    trip_response = call("trips.list", "GET", "/api/v1/trips")
    trip_rows = trip_response.json() if trip_response and trip_response.ok else []
    fixture = next((item for item in trip_rows if item.get("status") == "completed"), None)
    fixture_id = fixture.get("id") if fixture else None

    call("trips.mock", "GET", "/api/v1/trips/mock/wuhan-lushan")
    call("skills.health", "GET", "/api/v1/skills/health")
    call("skills.calls", "GET", "/api/v1/skills/calls", params={"limit": 5})
    call("skills.metrics", "GET", "/api/v1/skills/metrics")
    call("ops.metrics", "GET", "/api/v1/ops/metrics")
    call("vehicles.list", "GET", "/api/v1/vehicles")

    raw = "2026年8月20日早上从武汉出发去九江，8月22日晚返回武汉，情侣出游，舒适自驾。"
    call(
        "trips.preflight",
        "POST",
        "/api/v1/trips/preflight",
        json_body={"raw_text": raw, "confirmed": True},
    )

    # Provider skills validate credentials/quota as well as HTTP status. A
    # 200 response with success=false is a provider failure and must fail the
    # smoke run rather than being mistaken for a healthy API route.
    call("skill.amap.geocode", "POST", "/api/v1/skills/amap/geocode", json_body={"address": "武汉市"}, require_success=True)
    call(
        "skill.amap.driving",
        "POST",
        "/api/v1/skills/amap/driving",
        json_body={"origin": "114.3055,30.5928", "destination": "115.989,29.565"},
        require_success=True,
        timeout=60,
    )
    call(
        "skill.amap.route",
        "POST",
        "/api/v1/skills/amap/route",
        json_body={
            "origin": {"longitude": 114.3055, "latitude": 30.5928},
            "destination": {"longitude": 115.989, "latitude": 29.565},
            "preferred_mode": "driving",
            "allowed_fallback_modes": [],
        },
        require_success=True,
        timeout=60,
    )
    amap_poi_response = call(
        "skill.amap.poi",
        "POST",
        "/api/v1/skills/amap/poi",
        json_body={"keywords": "景点", "city": "武汉", "page_size": 2},
        require_success=True,
        timeout=60,
    )
    if amap_poi_response and amap_poi_response.ok:
        try:
            poi_items = (amap_poi_response.json().get("data") or {}).get("items") or []
            poi_id = poi_items[0].get("id") if poi_items and isinstance(poi_items[0], dict) else None
        except (ValueError, AttributeError):
            poi_id = None
        if poi_id:
            call(
                "skill.amap.poi-detail",
                "POST",
                "/api/v1/skills/amap/poi-detail",
                json_body={"poi_id": poi_id},
                require_success=True,
                timeout=60,
            )
    call(
        "skill.weather.forecast",
        "POST",
        "/api/v1/skills/weather/forecast",
        json_body={"latitude": 30.5928, "longitude": 114.3055, "forecast_days": 2},
        require_success=True,
        timeout=30,
    )
    call(
        "skill.carinfo.search",
        "POST",
        "/api/v1/skills/carinfo/search",
        json_body={"query": "小米汽车", "limit": 2},
        # A valid catalogue request may legitimately return no matching model
        # when the upstream catalogue changes. The smoke check verifies the
        # endpoint contract and records the provider result without treating a
        # normal ``CARINFO_NO_RESULTS`` response as an API failure.
        require_success=False,
        timeout=60,
    )
    call(
        "skill.flyai.poi",
        "POST",
        "/api/v1/skills/flyai/poi",
        json_body={"city_name": "北京", "keyword": "故宫"},
        require_success=True,
        timeout=60,
    )
    call(
        "skill.flyai.hotel",
        "POST",
        "/api/v1/skills/flyai/hotel",
        json_body={"destination": "北京", "check_in_date": "2026-08-20", "check_out_date": "2026-08-21"},
        require_success=True,
        timeout=60,
    )
    call(
        "skill.flyai.train",
        "POST",
        "/api/v1/skills/flyai/train",
        json_body={"origin": "武汉", "destination": "北京", "dep_date": "2026-08-20", "sort_type": 4},
        timeout=60,
    )
    call(
        "skill.flyai.flight",
        "POST",
        "/api/v1/skills/flyai/flight",
        json_body={"origin": "武汉", "destination": "北京", "dep_date": "2026-08-20", "sort_type": 4},
        timeout=60,
    )
    call(
        "skill.flyai.ferry",
        "POST",
        "/api/v1/skills/flyai/ferry",
        json_body={"origin": "上海", "destination": "舟山", "dep_date": "2026-08-20"},
        timeout=60,
    )
    call(
        "skill.flyai.keyword-search",
        "POST",
        "/api/v1/skills/flyai/keyword-search",
        json_body={"query": "北京情侣必去景点"},
        require_success=True,
        timeout=60,
    )
    call(
        "skill.flyai.ai-search",
        "POST",
        "/api/v1/skills/flyai/ai-search",
        json_body={"query": "北京情侣舒适三日旅行建议"},
        require_success=True,
        timeout=60,
    )
    call(
        "skill.opentripmap.nearby",
        "POST",
        "/api/v1/skills/opentripmap/nearby",
        json_body={"longitude": 116.397, "latitude": 39.916, "radius_m": 5000, "limit": 2},
        require_success=True,
        timeout=60,
    )

    smoke_trip_id: str | None = None
    smoke_vehicle_id: str | None = None
    try:
        start = date.today() + timedelta(days=14)
        end = start + timedelta(days=2)
        create_trip = call(
            "trips.create",
            "POST",
            "/api/v1/trips",
            json_body={
                "title": "API smoke itinerary",
                "request": {
                    "raw_text": f"{start.isoformat()} 从武汉出发到九江，{end.isoformat()} 返回，舒适自驾。",
                    "origin": {"name": "武汉", "city": "武汉"},
                    "destination": {"name": "九江", "city": "九江"},
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "travelers": 2,
                    "preferences": ["舒适"],
                    "transport_modes": ["driving"],
                },
            },
        )
        if create_trip and create_trip.ok:
            smoke_trip_id = create_trip.json().get("id")
        if smoke_trip_id:
            call("trips.get", "GET", f"/api/v1/trips/{smoke_trip_id}")
            call("trips.patch", "PATCH", f"/api/v1/trips/{smoke_trip_id}", json_body={"title": "API smoke itinerary updated"})
            call("trips.recommendations.attractions", "GET", f"/api/v1/trips/{smoke_trip_id}/recommendations", params={"category": "attractions"})
            call("trips.planning.snapshot", "GET", f"/api/v1/trips/{smoke_trip_id}/planning")
            call("trips.planning.events", "GET", f"/api/v1/trips/{smoke_trip_id}/planning/events", stream=True, timeout=10)
            call("trips.risks", "GET", f"/api/v1/trips/{smoke_trip_id}/risks")
            call("trips.services", "GET", f"/api/v1/trips/{smoke_trip_id}/services")
            call("trips.clarification.expected-409", "POST", f"/api/v1/trips/{smoke_trip_id}/planning/clarifications", json_body={"answer": "补充信息"}, expected={409})
            call("trips.editing.interpret", "POST", f"/api/v1/trips/{smoke_trip_id}/editing/interpret", json_body={"message": "请增加一个景点", "current_day_id": None}, expected={200, 422, 503}, timeout=60)
            call("trips.planning.start", "POST", f"/api/v1/trips/{smoke_trip_id}/planning/start", timeout=30)
            call("trips.planning.snapshot.after-start", "GET", f"/api/v1/trips/{smoke_trip_id}/planning")

            version = call("trip.versions.create", "POST", f"/api/v1/trips/{smoke_trip_id}/versions", json_body={"name": "smoke", "note": "API smoke"})
            call("trip.versions.list", "GET", f"/api/v1/trips/{smoke_trip_id}/versions")
            if version and version.ok:
                version_id = version.json().get("id")
                if version_id:
                    call("trip.versions.restore", "POST", f"/api/v1/trips/{smoke_trip_id}/versions/{version_id}/restore")

            vehicle = call(
                "vehicles.create",
                "POST",
                "/api/v1/vehicles",
                json_body={
                    "brand": "Smoke",
                    "series": "Test",
                    "model": "API Vehicle",
                    "power_type": "electric",
                    "rated_range_km": 500,
                    "current_energy_percent": 80,
                    "seats": 5,
                },
            )
            if vehicle and vehicle.ok:
                smoke_vehicle_id = vehicle.json().get("id")
                call("vehicles.get", "GET", f"/api/v1/vehicles/{smoke_vehicle_id}")
                call("vehicles.patch", "PATCH", f"/api/v1/vehicles/{smoke_vehicle_id}", json_body={"seats": 4})

            uploaded = call(
                "files.upload",
                "POST",
                "/api/v1/files",
                data={"trip_id": smoke_trip_id},
                files={"upload": ("api-smoke.md", "# Smoke\n\n武汉 九江".encode("utf-8"), "text/markdown")},
            )
            if uploaded and uploaded.ok:
                file_id = uploaded.json().get("id")
                if file_id:
                    call("files.metadata", "GET", f"/api/v1/files/{file_id}")
                    call("files.content", "GET", f"/api/v1/files/{file_id}/content")
                    extraction = call("files.extract", "POST", f"/api/v1/files/{file_id}/extract")
                    call("files.confirm", "POST", f"/api/v1/files/{file_id}/confirm", json_body={"accepted_places": []}, expected={200, 409})

            job = call("jobs.create", "POST", "/api/v1/jobs", json_body={"kind": "skill_probe", "payload": {"source": "api-smoke"}})
            if job and job.ok:
                job_id = job.json().get("id")
                if job_id:
                    call("jobs.get", "GET", f"/api/v1/jobs/{job_id}")
                    call("jobs.cancel", "POST", f"/api/v1/jobs/{job_id}/cancel")
        else:
            rows.append(("mutation.fixture", "SKIP", "FAIL", 0, "temporary trip creation failed"))
    finally:
        if smoke_vehicle_id:
            call("vehicles.delete", "DELETE", f"/api/v1/vehicles/{smoke_vehicle_id}")
        if smoke_trip_id:
            call("trips.delete", "DELETE", f"/api/v1/trips/{smoke_trip_id}")

    # Completed fixture covers every roadbook/export response without
    # mutating the user's temporary smoke trip.
    if fixture_id:
        for suffix in ("", ".pdf", ".pptx", ".png", ".html"):
            call(f"roadbook{suffix or '.md'}", "GET", f"/api/v1/trips/{fixture_id}/roadbook{suffix}", timeout=90)
        call("fixture.recommendations.hotels", "GET", f"/api/v1/trips/{fixture_id}/recommendations", params={"category": "hotels"})

    print("label\tstatus\tresult\tms\tsummary")
    for label, status, result, elapsed, detail in rows:
        print(f"{label}\t{status}\t{result}\t{elapsed}\t{detail}")
    failures = [row for row in rows if row[2] == "FAIL"]
    print(f"TOTAL={len(rows)} FAILURES={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
