import pytest

from app.planning.event_research import event_research_summary, research_special_events


@pytest.mark.asyncio
async def test_event_research_attaches_agent_facts_and_unwraps_source(monkeypatch):
    class FakeResponse:
        text = (
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fperseids">'
            '2026 流星雨观测资料</a>'
            '<a class="result__snippet">8月12日至13日达到极大期</a>'
        )

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.planning.event_research.httpx.AsyncClient", FakeClient)

    async def facts(event, year, sources):
        assert event == "英仙座流星雨"
        assert year == 2026
        assert sources[0]["url"] == "https://example.com/perseids"
        return {
            "peak_start_date": "2026-08-12",
            "peak_end_date": "2026-08-13",
            "peak_time_local": "22:30",
            "confidence": "high",
            "summary": "来源明确给出极大期日期。",
        }

    result = await research_special_events(
        ["英仙座流星雨"], year=2026, destination="九宫山", fact_agent=facts
    )

    assert result[0]["facts"]["peak_start_date"] == "2026-08-12"
    assert "22:30" in event_research_summary(result[0])


@pytest.mark.asyncio
async def test_event_research_keeps_explicit_window_when_fact_agent_returns_nothing(monkeypatch):
    class FakeResponse:
        text = (
            '<a class="result__a" href="https://example.com/leonids">2026年狮子座流星雨观测指南</a>'
            '<a class="result__snippet">狮子座流星雨将在2026年11月17日至18日达到极大，'
            '黑暗天空下每小时最多可见15颗流星。</a>'
        )

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.planning.event_research.httpx.AsyncClient", FakeClient)

    async def no_facts(*_args):
        return {}

    result = await research_special_events(
        ["狮子座流星雨"], year=2026, destination="九宫山", fact_agent=no_facts
    )

    facts = result[0]["facts"]
    assert facts["peak_start_date"] == "2026-11-17"
    assert facts["peak_end_date"] == "2026-11-18"
    assert facts["zhr"] == 15
    assert facts["evidence_source_indexes"] == [0]
    assert "2026-11-17 至 2026-11-18" in facts["summary"]


@pytest.mark.asyncio
async def test_event_research_does_not_invent_window_from_generic_source(monkeypatch):
    class FakeResponse:
        text = (
            '<a class="result__a" href="https://example.com/event">流星雨观测介绍</a>'
            '<a class="result__snippet">请关注天气、月光和现场光污染情况。</a>'
        )

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.planning.event_research.httpx.AsyncClient", FakeClient)

    result = await research_special_events(["未知流星雨"], year=2026, fact_agent=None)

    assert "facts" not in result[0]
    assert "请关注天气" in event_research_summary(result[0])
