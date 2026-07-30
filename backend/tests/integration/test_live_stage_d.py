import os
from pathlib import Path

import pytest

from app.core.config import Settings
from app.planning.graph import build_planning_graph
from app.services.registry_factory import build_skill_registry


pytestmark = pytest.mark.skipif(
    os.getenv("ROADMAN_LIVE_TESTS") != "1",
    reason="set ROADMAN_LIVE_TESTS=1 to call Ollama Cloud, AMap and Open-Meteo",
)


@pytest.mark.asyncio
async def test_live_five_day_multimodal_planning():
    amap_key = (
        Path(__file__).resolve().parents[3] / "Skills" / "amap-lbs" / "apipkey.txt"
    ).read_text(encoding="utf-8").strip()
    settings = Settings(
        amap_webservice_key=amap_key,
        load_local_skill_credentials=False,
        enable_job_queue=False,
    )
    assert settings.ollama_api_key, "OLLAMA_API_KEY is required"
    assert settings.amap_webservice_key, "AMAP_WEBSERVICE_KEY is required"

    registry = build_skill_registry(settings)
    try:
        result = await build_planning_graph(registry, settings).ainvoke(
            {
                "trip_id": "live_stage_d_five_days",
                "raw_input": (
                    "下周六从武汉去庐山，五天四夜，"
                    "希望包含公共交通、步行和骑行接驳，并关注到达时天气与路况"
                ),
                "trip_request": {
                    "raw_text": (
                        "下周六从武汉去庐山，五天四夜，"
                        "希望包含公共交通、步行和骑行接驳，并关注到达时天气与路况"
                    )
                },
                "clarification_round": 0,
            }
        )
    finally:
        await registry.close()

    assert result["missing_fields"] == []
    assert result["verification_result"]["passed"] is True
    assert len(result["day_plans"]) == 5
    stages = [stage for day in result["day_plans"] for stage in day["stages"]]
    requested_local_modes = {
        item["route"]["data"]["requested_mode"] for item in result["local_routes"]
    }
    selected_modes = {stage["mode"] for stage in stages}
    activities = [
        activity for day in result["day_plans"] for activity in day["activities"]
    ]
    print(
        {
            "days": len(result["day_plans"]),
            "stages": len(stages),
            "requested_local_modes": sorted(requested_local_modes),
            "selected_modes": sorted(selected_modes),
            "activities": sorted({item["type"] for item in activities}),
            "risk_levels": sorted({stage["risk_level"] for stage in stages}),
        }
    )
    assert len(stages) >= 6
    assert {"transit", "walking", "riding"} <= requested_local_modes
    assert "driving" in selected_modes
    assert any(stage["mode"] != "driving" for stage in stages)
    assert all(stage["route_segments"][0]["coordinates"] for stage in stages)
    assert all(stage["weather_summary"] for stage in stages)
    driving = [stage for stage in stages if stage["mode"] == "driving"]
    assert all(stage["traffic_summary"] for stage in driving)
    assert all(stage["energy_estimate"]["estimated"] for stage in driving)
    assert any(item["type"] in {"rest", "charging"} for item in activities)
    assert all(stage_services.keys() >= {"rest", "charging", "fueling", "parking", "meal", "hospital", "toilet"}
               for stage_services in result["service_pois"].values())
