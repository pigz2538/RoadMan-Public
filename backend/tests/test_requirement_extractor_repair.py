import pytest

from app.core.config import Settings
from app.planning.llm import DeepSeekRequirementExtractor


@pytest.mark.asyncio
async def test_requirement_agent_preserves_unusually_long_duration_for_policy_guard(monkeypatch):
    async def fake_complete(settings, prompt, *, timeout=None, json_output=True, agent_name=""):
        assert agent_name == "requirement_extractor"
        assert "277天" in prompt
        return (
            '{"origin_name":"北京","destination_name":"上海",'
            '"destination_names":["上海"],"destination_scope":"city",'
            '"travel_intents":[],"start_date":null,"end_date":null,'
            '"departure_time":null,"return_time":null,"departure_period":null,'
            '"travelers":null,"max_days":277,"preferences":[],'
            '"transport_modes":["driving"],"special_events":[],'
            '"cross_sea_required":false,"cross_sea_mode":null,'
            '"past_return_requested":false,"time_window_minutes":null,'
            '"stay_only_at_destination":false,"must_visit_names":[]}'
        )

    monkeypatch.setattr("app.planning.llm.deepseek_complete", fake_complete)
    result = await DeepSeekRequirementExtractor(
        Settings(deepseek_api_key="test-key", deepseek_thinking=False)
    ).extract(
        "北京—上海277天自驾",
        today=__import__("datetime").date(2026, 9, 5),
    )

    assert result["origin_name"] == "北京"
    assert result["destination_name"] == "上海"
    assert result["max_days"] == 277


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
    assert result["_intent_source"] == "llm_repair"
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
    assert result["_intent_source"] == "llm"


@pytest.mark.asyncio
async def test_requirement_agent_adjudicates_parent_city_destination(monkeypatch):
    """A province plus explicit city must resolve to the city anchor."""

    calls: list[str] = []

    async def fake_complete(settings, prompt, *, timeout=None, json_output=True, agent_name=""):
        calls.append(agent_name)
        if agent_name == "requirement_extractor":
            return (
                '{"origin_name":"武汉","destination_name":"河南",'
                '"destination_names":["河南","郑州"],"destination_scope":"province",'
                '"start_date":"2026-09-07","end_date":"2026-09-10",'
                '"travelers":2,"transport_modes":["driving"],"preferences":["自然景观"]}'
            )
        assert agent_name == "destination_entity_adjudicator"
        return (
            '{"destination_name":"郑州","destination_names":["郑州"],'
            '"destination_scope":"city"}'
        )

    monkeypatch.setattr("app.planning.llm.deepseek_complete", fake_complete)
    result = await DeepSeekRequirementExtractor(
        Settings(deepseek_api_key="test-key", deepseek_thinking=False)
    ).extract(
        "周一早上从武汉出发，去河南郑州附近，周四晚八点前回来，情侣出游，自驾，喜欢自然景观",
        today=__import__("datetime").date(2026, 9, 1),
    )

    assert calls == ["requirement_extractor", "destination_entity_adjudicator"]
    assert result["destination_name"] == "郑州"
    assert result["destination_names"] == ["郑州"]
    assert result["destination_scope"] == "city"
    assert result["_destination_adjudication_used"] is True
