import pytest

from app.domain.models import SkillResult, SourceRecord
from app.planning.destination_research import research_destination


class _ResearchRegistry:
    def __init__(self):
        self.calls = []

    def names(self):
        return ["flyai.ai_search", "flyai.keyword_search"]

    async def execute(self, name, payload, context):
        self.calls.append((name, payload, context))
        return SkillResult(
            success=True,
            provider="FlyAI / 飞猪",
            data={
                "items": [{
                    "title": "夫子庙",
                    "snippet": "南京代表性景点",
                    "image_url": "https://example.test/poi.jpg",
                }]
            },
            sources=[SourceRecord(
                provider="FlyAI / 飞猪",
                title="目的地搜索",
                url="https://example.test/source",
            )],
        )


@pytest.mark.asyncio
async def test_destination_research_combines_web_and_both_flyai_modes(monkeypatch):
    async def fake_web_sources(destination):
        return ([{
            "provider": "Web Destination Research",
            "title": f"{destination}官方旅游指南",
            "url": "https://example.test/guide",
            "category_hint": "attractions",
        }], [f"{destination} 必去景点", f"{destination} 必吃美食"])

    monkeypatch.setattr(
        "app.planning.destination_research._web_sources",
        fake_web_sources,
    )
    registry = _ResearchRegistry()

    result = await research_destination(registry, "南京", "trip_research")

    assert result["status"] == "researched"
    assert result["providers"]["web"] is True
    assert result["providers"]["flyai_keyword_search"] is True
    assert result["providers"]["flyai_ai_search"] is True
    assert len(result["flyai_items"]) == 2
    assert {item[0] for item in registry.calls} == {
        "flyai.keyword_search",
        "flyai.ai_search",
    }
    assert all(call[2].metadata["purpose"] == "destination_research" for call in registry.calls)


@pytest.mark.asyncio
async def test_destination_research_does_not_block_dry_registry():
    class EmptyRegistry:
        def names(self):
            return []

    result = await research_destination(EmptyRegistry(), "北京", "trip_dry")

    assert result["status"] == "needs_review"
    assert result["providers"]["flyai_errors"] == ["SEARCH_ADAPTERS_UNAVAILABLE"]
