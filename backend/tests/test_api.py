import pytest

from app.db import SessionLocal
from datetime import date, datetime, timedelta, timezone

from app.domain.models import Activity, DayItemRef, DayPlan, Trip
from app.domain.models import SSEEvent
from app.repositories import TripRepository
from app.services.sse import sse_manager


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_operational_metrics_expose_request_and_skill_summary(client):
    await client.get("/health")
    response = await client.get("/api/v1/ops/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["service"]["requests"] >= 1
    assert "total_calls" in body["skills"]


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
    assert (await client.get(f"/api/v1/trips/{trip_id}")).status_code == 404
    assert all(item["id"] != trip_id for item in (await client.get("/api/v1/trips")).json())


@pytest.mark.asyncio
async def test_exports_are_hidden_server_side_until_planning_completes(client):
    created = await client.post(
        "/api/v1/trips",
        json={"title": "规划中行程", "request": {"raw_text": "周六从武汉去庐山"}},
    )
    trip_id = created.json()["id"]

    for suffix in ("roadbook", "roadbook.pdf", "roadbook.pptx", "roadbook.png"):
        response = await client.get(f"/api/v1/trips/{trip_id}/{suffix}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PLANNING_NOT_COMPLETED"


@pytest.mark.asyncio
async def test_trip_version_save_and_restore(client):
    created = await client.post(
        "/api/v1/trips",
        json={
            "title": "版本原稿",
            "request": {"raw_text": "武汉周末游"},
        },
    )
    trip_id = created.json()["id"]
    saved = await client.post(
        f"/api/v1/trips/{trip_id}/versions",
        json={"name": "初版", "note": "用户主动保存"},
    )
    assert saved.status_code == 201

    await client.patch(f"/api/v1/trips/{trip_id}", json={"title": "修改后"})
    versions = await client.get(f"/api/v1/trips/{trip_id}/versions")
    assert versions.json()[0]["name"] == "初版"

    restored = await client.post(
        f"/api/v1/trips/{trip_id}/versions/{saved.json()['id']}/restore",
    )
    assert restored.status_code == 200
    assert restored.json()["title"] == "版本原稿"


@pytest.mark.asyncio
async def test_trip_recommendations_returns_ranked_persisted_candidates(client):
    created = await client.post(
        "/api/v1/trips",
        json={
            "title": "候选测试",
            "request": {
                "raw_text": "从武汉去庐山",
                "origin": {"name": "武汉"},
                "destination": {"name": "庐山"},
            },
        },
    )
    trip = Trip.model_validate(created.json())
    async with SessionLocal() as session:
        await TripRepository(session).save_planning_result(
            trip,
            {
                "tourism_candidates": {
                    "attractions": [
                        {
                            "candidate_id": "attractions:amap:1",
                            "rank": 1,
                            "score": 88.5,
                            "place": {"name": "庐山风景区"},
                        }
                    ]
                }
            },
            None,
        )
    response = await client.get(
        f"/api/v1/trips/{trip.id}/recommendations",
        params={"category": "attractions"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["rank"] == 1


@pytest.mark.asyncio
async def test_map_point_endpoint_creates_preview_only(client):
    created = await client.post(
        "/api/v1/trips",
        json={"title": "地图选点", "request": {"raw_text": "武汉周末游"}},
    )
    trip = Trip.model_validate(created.json())
    trip.days = [
        DayPlan(
            id="day_map",
            day_index=1,
            date=date(2026, 8, 2),
            title="第 1 天",
        )
    ]
    async with SessionLocal() as session:
        await TripRepository(session).save_planning_result(trip, {}, None)

    response = await client.post(
        f"/api/v1/trips/{trip.id}/patches/preview-map-point",
        json={
            "day_id": "day_map",
            "category": "attractions",
            "name": "地图选择的观景台",
            "address": "庐山风景区",
            "longitude": 115.982,
            "latitude": 29.57,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "preview"
    unchanged = await client.get(f"/api/v1/trips/{trip.id}")
    assert unchanged.json()["days"][0]["activities"] == []


@pytest.mark.asyncio
async def test_candidate_patch_requires_preview_before_apply(client, monkeypatch):
    created = await client.post(
        "/api/v1/trips",
        json={
            "title": "备选修改测试",
            "request": {
                "raw_text": "从武汉去庐山",
                "origin": {"name": "武汉"},
                "destination": {"name": "庐山"},
            },
        },
    )
    trip = Trip.model_validate(created.json())
    meal = Activity(
        id="activity_meal",
        day_id="day_patch",
        sequence=0,
        type="meal",
        place={"name": "原餐厅"},
        planned_start=datetime(2026, 8, 2, 12, tzinfo=timezone.utc),
        planned_end=datetime(2026, 8, 2, 13, tzinfo=timezone.utc),
        duration_minutes=60,
    )
    trip.days = [
        DayPlan(
            id="day_patch",
            day_index=1,
            date=date(2026, 8, 2),
            title="测试日",
            activities=[meal],
            items=[DayItemRef(type="activity", id=meal.id)],
        )
    ]
    state = {
        "tourism_candidates": {
            "attractions": [
                {
                    "candidate_id": "attractions:amap:1",
                    "rank": 1,
                    "score": 88.5,
                    "place": {"name": "庐山风景区"},
                    "ticket_or_price": {
                        "currency": "CNY",
                        "minimum": 160,
                        "maximum": 160,
                        "estimated": False,
                    },
                }
            ]
        }
    }
    async with SessionLocal() as session:
        await TripRepository(session).save_planning_result(trip, state, None)

    preview = await client.post(
        f"/api/v1/trips/{trip.id}/patches/preview",
        json={
            "candidate_id": "attractions:amap:1",
            "category": "attractions",
            "day_id": "day_patch",
            "operation": "add",
        },
    )
    assert preview.status_code == 200
    assert preview.json()["status"] == "preview"
    unchanged = await client.get(f"/api/v1/trips/{trip.id}")
    assert len(unchanged.json()["days"][0]["activities"]) == 1

    applied = await client.post(
        f"/api/v1/trips/{trip.id}/patches/{preview.json()['id']}/apply",
    )
    assert applied.status_code == 200
    assert applied.json()["patch"]["status"] == "applied"
    assert [
        item["place"]["name"]
        for item in applied.json()["trip"]["days"][0]["activities"]
    ] == ["庐山风景区", "原餐厅"]
    rolled_back = await client.post(
        f"/api/v1/trips/{trip.id}/patches/{preview.json()['id']}/rollback",
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["patch"]["status"] == "rolled_back"
    assert [
        item["place"]["name"]
        for item in rolled_back.json()["trip"]["days"][0]["activities"]
    ] == ["原餐厅"]

    async def fake_edit_interpret(self, message, trip_context):
        return {
            "intent": "delete",
            "day_id": "day_patch",
            "target_activity_id": "activity_meal",
            "reply": "已生成删除预览。",
        }

    # Keep this API test deterministic and credential-free.  Live semantic
    # interpretation is covered by the Ollama contract tests; this endpoint
    # test only verifies preview/apply/rollback orchestration.
    monkeypatch.setattr(
        "app.api.trips.OllamaTripEditAgent.interpret",
        fake_edit_interpret,
    )
    delete_intent = await client.post(
        f"/api/v1/trips/{trip.id}/editing/interpret",
        json={
            "message": "把当前这个餐厅删除",
            "current_day_id": "day_patch",
            "current_target_id": "activity_meal",
        },
    )
    assert delete_intent.status_code == 200
    assert "删除" in delete_intent.json()["message"]
    delete_patch = delete_intent.json()["patch"]
    deleted = await client.post(
        f"/api/v1/trips/{trip.id}/patches/{delete_patch['id']}/apply",
    )
    assert deleted.status_code == 200
    assert deleted.json()["trip"]["days"][0]["activities"] == []


@pytest.mark.asyncio
async def test_preflight_blocks_temporal_and_cross_sea_conflicts(client):
    start_date = date.today() + timedelta(days=2)
    invalid_end_date = start_date - timedelta(days=1)
    corrected_end_date = start_date + timedelta(days=1)
    raw_text = (
        f"{start_date.isoformat()}从上海出发跨海去海岛，{invalid_end_date.isoformat()}返回，"
        "下午3点出发到下午3点抵达"
    )
    response = await client.post(
        "/api/v1/trips/preflight",
        json={
            "raw_text": raw_text,
            "previous_extracted": {
                "origin_name": "上海",
                "destination_name": "海岛",
                "start_date": start_date.isoformat(),
                "end_date": invalid_end_date.isoformat(),
                "cross_sea_required": True,
                "cross_sea_mode": None,
                "time_window_minutes": 30,
                "preferences": [],
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    codes = {item["code"] for item in body["issues"]}
    assert body["ready"] is False
    assert "INVALID_DATE_ORDER" in codes
    assert "CROSS_SEA_MODE_REQUIRED" in codes
    assert "IMPOSSIBLE_TIME_WINDOW" in codes
    cross_sea = next(
        item for item in body["issues"] if item["code"] == "CROSS_SEA_MODE_REQUIRED"
    )
    assert cross_sea["answer_type"] == "choice"
    assert cross_sea["options"] == ["轮渡", "飞机", "跨海大桥"]

    answers = {
        "INVALID_DATE_ORDER:end_date": corrected_end_date.isoformat(),
        "CROSS_SEA_MODE_REQUIRED:preferences": "轮渡",
        "IMPOSSIBLE_TIME_WINDOW:time_window": "取消原到达限制，按合理车程安排",
    }
    reviewed = await client.post(
        "/api/v1/trips/preflight",
        json={
            "raw_text": raw_text,
            "previous_extracted": {
                "origin_name": "上海",
                "destination_name": "海岛",
                "start_date": start_date.isoformat(),
                "end_date": invalid_end_date.isoformat(),
                "cross_sea_required": True,
                "cross_sea_mode": None,
                "time_window_minutes": 30,
                "preferences": [],
            },
            "answers": answers,
            "semantic_checked": True,
        },
    )
    reviewed_body = reviewed.json()
    assert reviewed_body["ready"] is False
    assert reviewed_body["confirmation_required"] is True
    assert reviewed_body["issues"] == []
    assert reviewed_body["extracted"]["end_date"] == corrected_end_date.isoformat()
    assert reviewed_body["extracted"]["cross_sea_mode"] == "轮渡"

    confirmed = await client.post(
        "/api/v1/trips/preflight",
        json={
            "raw_text": raw_text,
            "previous_extracted": {
                "origin_name": "上海",
                "destination_name": "海岛",
                "start_date": start_date.isoformat(),
                "end_date": invalid_end_date.isoformat(),
                "cross_sea_required": True,
                "cross_sea_mode": "ferry",
                "time_window_minutes": 30,
                "preferences": [],
            },
            "answers": answers,
            "semantic_checked": True,
            "confirmed": True,
        },
    )
    assert confirmed.json()["ready"] is True
    assert confirmed.json()["confirmation_required"] is False


@pytest.mark.asyncio
async def test_preflight_resolves_chinese_weekday_return_without_clarification(client):
    """A weekday range in the original request must survive Agent/offline parsing."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    if monday < today:
        monday += timedelta(days=7)
    friday = monday + timedelta(days=4)
    response = await client.post(
        "/api/v1/trips/preflight",
        json={
            "raw_text": "周一早上从武汉出发，去九宫山，周五晚上八点回来，喜欢自然景观",
            "previous_extracted": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["extracted"]["origin_name"] == "武汉"
    assert body["extracted"]["destination_name"] == "九宫山"
    assert body["extracted"]["start_date"] == monday.isoformat()
    assert body["extracted"]["end_date"] == friday.isoformat()
    assert not {
        (item["code"], item.get("field"))
        for item in body["issues"]
        if item["code"] == "MISSING_FIELD"
    }


@pytest.mark.asyncio
async def test_preflight_understands_departure_then_arrival_clock_order(client):
    response = await client.post(
        "/api/v1/trips/preflight",
        json={
            "raw_text": (
                "2026年8月2日下午3点从武汉出发，下午4点到北京，"
                "2026年8月3日返回"
            ),
            "previous_extracted": {
                "origin_name": "武汉",
                "destination_name": "北京",
                "start_date": "2026-08-02",
                "end_date": "2026-08-03",
                "time_window_minutes": 30,
                "preferences": [],
            },
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
async def test_attachment_requires_preview_and_confirmation_before_trip_update(client, monkeypatch):
    async def fake_attachment_extract(path, mime_type, text, settings):
        return {
            "hotels": ["测试酒店"],
            "places": [],
            "dates": [],
            "order_numbers": [],
            "summary": "附件中的酒店候选",
        }

    monkeypatch.setattr(
        "app.services.attachments._extract_with_ollama",
        fake_attachment_extract,
    )
    monkeypatch.setattr("app.api.files.settings.ollama_api_key", "test-key")
    created = await client.post(
        "/api/v1/trips",
        json={"title": "附件测试", "request": {"raw_text": "武汉出发"}},
    )
    trip_id = created.json()["id"]
    uploaded = await client.post(
        "/api/v1/files",
        data={"trip_id": trip_id},
        files={
            "upload": (
                "攻略.md",
                "# 庐山攻略\n计划游览庐山风景区\n入住牯岭街云上酒店\n日期 2026-08-02",
                "text/markdown",
            )
        },
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["id"]
    before = await client.get(f"/api/v1/trips/{trip_id}")
    assert before.json()["request"]["must_visit"] == []

    preview = await client.post(f"/api/v1/files/{file_id}/extract")
    assert preview.status_code == 200
    assert preview.json()["status"] == "preview"
    assert preview.json()["hotels"]
    still_unchanged = await client.get(f"/api/v1/trips/{trip_id}")
    assert still_unchanged.json()["request"]["must_visit"] == []

    selected_name = preview.json()["hotels"][0]
    confirmed = await client.post(
        f"/api/v1/files/{file_id}/confirm",
        json={"accepted_places": [selected_name]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    updated = await client.get(f"/api/v1/trips/{trip_id}")
    assert updated.json()["request"]["must_visit"][0]["name"] == selected_name


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

    resumed_query = await client.get(
        "/api/v1/trips/trip_wuhan_lushan_demo/planning/events?after=3",
    )
    assert "id: 3" not in resumed_query.text
    assert "id: 4" in resumed_query.text


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
