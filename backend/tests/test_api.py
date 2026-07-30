import pytest


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
