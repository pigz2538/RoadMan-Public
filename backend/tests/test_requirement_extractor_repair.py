import pytest

from app.core.config import Settings
from app.planning.llm import DeepSeekRequirementExtractor


@pytest.mark.asyncio
async def test_requirement_agent_retries_missing_origin_semantically(monkeypatch):
    """A partial model response must trigger an LLM repair, never a POI fallback."""

    calls: list[str] = []

    async def fake_complete(settings, prompt, *, timeout=None, json_output=True, agent_name=""):
        calls.append(agent_name)
        if agent_name == "requirement_extractor":
            return (
                '{"origin_name":null,"destination_name":"三亚",'
                '"destination_names":["三亚"],"destination_scope":"city",'
                '"travel_intents":[],"start_date":"2026-10-01",'
                '"end_date":"2026-10-04","travelers":3,"max_days":null,'
                '"preferences":[],"transport_modes":["flight"],'
                '"special_events":[],"must_visit_names":["蜈支洲岛"],'
                '"cross_sea_required":false,"cross_sea_mode":null,'
                '"past_return_requested":false,"time_window_minutes":null,'
                '"stay_only_at_destination":false}'
            )
        assert agent_name == "requirement_repair"
        return (
            '{"origin_name":"武汉","destination_name":null,'
            '"destination_names":[],"destination_scope":"unknown"}'
        )

    monkeypatch.setattr("app.planning.llm.deepseek_complete", fake_complete)
    settings = Settings(
        deepseek_api_key="test-key",
        enable_llm_requirement_extraction=True,
        deepseek_thinking=False,
    )

    result = await DeepSeekRequirementExtractor(settings).extract(
        "10月1日到10月4日，一家三口先从武汉坐飞机到三亚，想去蜈支洲岛。",
        today=__import__("datetime").date(2026, 8, 31),
    )

    assert calls == ["requirement_extractor", "requirement_repair"]
    assert result["origin_name"] == "武汉"
    assert result["destination_name"] == "三亚"
    assert result["destination_names"] == ["三亚"]
    assert result["_intent_status"] == "ok"
    assert result["_intent_source"] == "deepseek_repair"
    assert result["_intent_repair_attempted"] is True


@pytest.mark.asyncio
async def test_requirement_agent_does_not_overwrite_existing_anchor(monkeypatch):
    calls: list[str] = []

    async def fake_complete(settings, prompt, *, timeout=None, json_output=True, agent_name=""):
        calls.append(agent_name)
        if agent_name == "requirement_extractor":
            return '{"origin_name":"武汉","destination_name":"北京","destination_names":["北京"],"destination_scope":"city"}'
        raise AssertionError("no semantic repair should be needed")

    monkeypatch.setattr("app.planning.llm.deepseek_complete", fake_complete)
    settings = Settings(deepseek_api_key="test-key", deepseek_thinking=False)
    result = await DeepSeekRequirementExtractor(settings).extract(
        "周五从武汉去北京，周日回来。",
        today=__import__("datetime").date(2026, 8, 31),
    )

    assert calls == ["requirement_extractor"]
    assert result["origin_name"] == "武汉"
    assert result["destination_name"] == "北京"
    assert result["_intent_source"] == "deepseek"
