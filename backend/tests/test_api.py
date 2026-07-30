import pytest

from app.domain.models import SSEEvent
from app.services.sse import sse_manager


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_trip_crud(client):
    payload = {
        "title": "测试行程",
        "request": {
            "raw_text": "周六从武汉到庐山",
            "origin": {"name": "武汉"},
            "destination": {"name": "庐山"},
        },
    }
    created = await client.post("/api/v1/trips", json=payload)
    assert created.status_code == 201
    trip_id = created.json()["id"]

    fetched = await client.get(f"/api/v1/trips/{trip_id}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "测试行程"

    updated = await client.patch(f"/api/v1/trips/{trip_id}", json={"title": "更新后"})
    assert updated.json()["title"] == "更新后"

    deleted = await client.delete(f"/api/v1/trips/{trip_id}")
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_preflight_blocks_temporal_and_cross_sea_conflicts(client):
    response = await client.post(
        "/api/v1/trips/preflight",
        json={
            "raw_text": (
                "2026-08-02从上海出发跨海去海岛，2026-08-01返回，"
                "下午3点出发到下午3点抵达"
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    codes = {item["code"] for item in body["issues"]}
    assert body["ready"] is False
    assert "INVALID_DATE_ORDER" in codes
    assert "CROSS_SEA_MODE_REQUIRED" in codes
    assert "IMPOSSIBLE_TIME_WINDOW" in codes


@pytest.mark.asyncio
async def test_preflight_understands_departure_then_arrival_clock_order(client):
    response = await client.post(
        "/api/v1/trips/preflight",
        json={
            "raw_text": (
                "2026年8月2日下午3点从武汉出发，下午4点到北京，"
                "2026年8月3日返回"
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["extracted"]["origin_name"] == "武汉"
    assert body["extracted"]["destination_name"] == "北京"
    assert "IMPOSSIBLE_TIME_WINDOW" in {
        item["code"] for item in body["issues"]
    }


@pytest.mark.asyncio
async def test_mock_trip(client):
    response = await client.get("/api/v1/trips/mock/wuhan-lushan")
    assert response.status_code == 200
    assert response.json()["days"][0]["stages"]


@pytest.mark.asyncio
async def test_amap_missing_key_degrades_cleanly(client):
    response = await client.post("/api/v1/skills/amap/geocode", json={"address": "武汉大学"})
    assert response.status_code == 200
    assert response.json()["error_code"] == "SKILL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_validation_errors_use_unified_contract(client):
    response = await client.post("/api/v1/trips", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert isinstance(body["error"]["details"], list)


@pytest.mark.asyncio
async def test_request_id_is_preserved_and_404_is_unified(client):
    response = await client.get(
        "/api/v1/not-found",
        headers={"X-Request-ID": "roadman-test-request"},
    )

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "roadman-test-request"
    assert response.json() == {
        "error": {
            "code": "HTTP_404",
            "message": "Not Found",
            "details": None,
            "request_id": "roadman-test-request",
        }
    }


@pytest.mark.asyncio
async def test_vehicle_crud(client):
    payload = {
        "brand": "RoadMan",
        "series": "Explorer",
        "model": "纯电 SUV",
        "power_type": "electric",
        "rated_range_km": 560,
        "current_energy_percent": 80,
        "battery_kwh": 82,
        "consumption_per_100km": 18,
    }
    created = await client.post("/api/v1/vehicles", json=payload)
    assert created.status_code == 201
    vehicle_id = created.json()["id"]

    listed = await client.get("/api/v1/vehicles")
    assert any(item["id"] == vehicle_id for item in listed.json())

    updated = await client.patch(
        f"/api/v1/vehicles/{vehicle_id}",
        json={"current_energy_percent": 65},
    )
    assert updated.json()["current_energy_percent"] == 65

    deleted = await client.delete(f"/api/v1/vehicles/{vehicle_id}")
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_file_upload_metadata_and_download(client):
    uploaded = await client.post(
        "/api/v1/files",
        files={"upload": ("route.png", b"\x89PNG\r\n\x1a\nroadman", "image/png")},
    )
    assert uploaded.status_code == 201
    record = uploaded.json()
    assert record["original_name"] == "route.png"
    assert record["size_bytes"] > 0

    metadata = await client.get(f"/api/v1/files/{record['id']}")
    assert metadata.json()["stored_name"] == record["stored_name"]

    content = await client.get(f"/api/v1/files/{record['id']}/content")
    assert content.status_code == 200
    assert content.content.startswith(b"\x89PNG")

    rejected = await client.post(
        "/api/v1/files",
        files={"upload": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert rejected.status_code == 415
    assert rejected.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_job_create_get_and_cancel(client):
    created = await client.post(
        "/api/v1/jobs",
        json={"kind": "skill_probe", "payload": {"adapter": "carinfo.demo"}},
    )
    assert created.status_code == 202
    job_id = created.json()["id"]

    fetched = await client.get(f"/api/v1/jobs/{job_id}")
    assert fetched.json()["status"] == "queued"

    cancelled = await client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_requested"] is True


@pytest.mark.asyncio
async def test_skill_endpoints_and_multimodal_contract(client):
    carinfo = await client.post(
        "/api/v1/skills/carinfo/search",
        json={"power_type": "electric"},
    )
    assert carinfo.status_code == 200
    assert carinfo.json()["success"] is True
    assert carinfo.json()["data"]["items"][0]["rated_range_km"] == 560

    route = await client.post(
        "/api/v1/skills/amap/route",
        json={
            "origin": {"longitude": 114.365248, "latitude": 30.536325, "city": "武汉"},
            "destination": {"longitude": 115.982811, "latitude": 29.556769, "city": "九江"},
            "preferred_mode": "driving",
        },
    )
    assert route.status_code == 200
    assert route.json()["error_code"] == "SKILL_NOT_CONFIGURED"

    calls = await client.get("/api/v1/skills/calls")
    adapters = [item["adapter"] for item in calls.json()]
    assert "carinfo.demo" in adapters
    assert "amap.route" in adapters


@pytest.mark.asyncio
async def test_sse_supports_last_event_id(client):
    initial = await client.get(
        "/api/v1/trips/trip_wuhan_lushan_demo/planning/events",
    )
    assert initial.status_code == 200
    assert "id: 1" in initial.text
    assert "id: 6" in initial.text

    resumed = await client.get(
        "/api/v1/trips/trip_wuhan_lushan_demo/planning/events",
        headers={"Last-Event-ID": "3"},
    )
    assert "id: 3" not in resumed.text
    assert "id: 4" in resumed.text
    assert "id: 6" in resumed.text


@pytest.mark.asyncio
async def test_real_trip_sse_uses_only_published_progress(client):
    created = await client.post(
        "/api/v1/trips",
        json={
            "title": "真实进度测试",
            "request": {
                "raw_text": "从武汉到庐山两天",
                "origin": {"name": "武汉"},
                "destination": {"name": "庐山"},
            },
        },
    )
    trip_id = created.json()["id"]
    await sse_manager.publish(
        SSEEvent(
            event="planning_started",
            trip_id=trip_id,
            node="load_context",
            label="真实规划开始",
            progress=3,
        )
    )
    await sse_manager.publish(
        SSEEvent(
            event="planning_completed",
            trip_id=trip_id,
            node="persist_trip",
            label="规划完成",
            progress=100,
        )
    )

    response = await client.get(f"/api/v1/trips/{trip_id}/planning/events")

    assert response.status_code == 200
    assert "真实规划开始" in response.text
    assert "规划完成" in response.text
    assert "正在查询真实道路路线" not in response.text


@pytest.mark.asyncio
async def test_worker_completes_persisted_job(client):
    from app.workers.main import execute_job

    created = await client.post(
        "/api/v1/jobs",
        json={"kind": "skill_probe", "payload": {"value": 7}},
    )
    job_id = created.json()["id"]
    result = await execute_job({}, job_id)
    assert result["accepted"] is True

    fetched = await client.get(f"/api/v1/jobs/{job_id}")
    assert fetched.json()["status"] == "completed"
    assert fetched.json()["progress"] == 100


@pytest.mark.asyncio
async def test_cancelled_planning_job_pauses_trip(client):
    from app.workers.main import execute_job

    trip_response = await client.post(
        "/api/v1/trips",
        json={
            "title": "待取消规划",
            "request": {"raw_text": "周六从武汉去庐山，两天一夜"},
        },
    )
    trip_id = trip_response.json()["id"]
    job_response = await client.post(
        "/api/v1/jobs",
        json={"kind": "planning", "trip_id": trip_id, "payload": {"trip_id": trip_id}},
    )
    job_id = job_response.json()["id"]
    await client.post(f"/api/v1/jobs/{job_id}/cancel")

    result = await execute_job({}, job_id)
    assert result["cancelled"] is True
    trip = await client.get(f"/api/v1/trips/{trip_id}")
    assert trip.json()["status"] == "paused"
