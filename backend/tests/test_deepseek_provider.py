import pytest

from app.core.config import Settings
from app.planning.llm import deepseek_complete


@pytest.mark.asyncio
async def test_deepseek_chat_contract_uses_official_model_and_max_thinking(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                "choices": [
                    {
                        "message": {
                            "content": '{"ok":true}',
                            "reasoning_content": "private reasoning must not be exposed",
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs["headers"]
            captured["json"] = kwargs["json"]
            return FakeResponse()

    monkeypatch.setattr("app.planning.llm.httpx.AsyncClient", FakeClient)
    settings = Settings(deepseek_api_key="test-key")
    audited = {}

    async def fake_audit(agent_name, **kwargs):
        audited["agent_name"] = agent_name
        audited.update(kwargs)

    monkeypatch.setattr("app.planning.llm._audit_deepseek_call", fake_audit)
    result = await deepseek_complete(settings, "Return JSON", timeout=12, agent_name="requirement")

    assert result == '{"ok":true}'
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert captured["json"]["messages"] == [{"role": "user", "content": "Return JSON"}]
    assert captured["json"]["thinking"] == {"type": "enabled"}
    assert captured["json"]["reasoning_effort"] == "max"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert "reasoning_content" not in result
    assert audited["agent_name"] == "requirement"
    assert audited["usage"]["total_tokens"] == 18
    assert audited["success"] is True
