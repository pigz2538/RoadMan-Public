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
