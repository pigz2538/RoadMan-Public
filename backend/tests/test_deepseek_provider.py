import pytest

from app.core.config import Settings
from app.planning.llm import llm_complete


def test_llm_settings_are_external_and_canonical_values_win():
    settings = Settings(
        llm_provider="acme_gateway",
        llm_api_url="https://acme.example/v1/chat/completions",
        llm_api_key="canonical-key",
        llm_model="acme-model",
        llm_api_style="openai",
        ollama_api_url="https://ollama.example/api/generate",
        ollama_api_key="legacy-key",
        ollama_model="legacy-model",
    )

    assert settings.llm_provider == "acme_gateway"
    assert settings.llm_api_url == "https://acme.example/v1/chat/completions"
    assert settings.llm_api_key == "canonical-key"
    assert settings.llm_model == "acme-model"
    assert settings.llm_api_style == "openai"
    # Compatibility fields mirror the effective configuration rather than
    # changing which endpoint the adapter calls.
    assert settings.ollama_api_key == "canonical-key"
    assert settings.deepseek_api_url == settings.llm_api_url

    # A caller may intentionally select a DeepSeek-compatible gateway through
    # the canonical fields; the migration guard only ignores stale legacy
    # DEEPSEEK_API_URL values and never rewrites an explicit LLM_API_URL.
    deepseek_gateway = Settings(
        llm_provider="deepseek_gateway",
        llm_api_url="https://api.deepseek.com/chat/completions",
        llm_api_key="canonical-key",
        llm_model="deepseek-chat",
    )
    assert deepseek_gateway.llm_api_url == "https://api.deepseek.com/chat/completions"


@pytest.mark.asyncio
async def test_explicit_native_style_accepts_ollama_response(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"ok":true}'}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return FakeResponse()

    monkeypatch.setattr("app.planning.llm.httpx.AsyncClient", FakeClient)
    settings = Settings(
        llm_provider="ollama_local",
        llm_api_url="http://ollama.local/api/generate",
        llm_api_key="test-key",
        llm_model="local-model",
        llm_api_style="ollama_generate",
    )
    result = await llm_complete(settings, "Return JSON", agent_name="native")

    assert result == '{"ok":true}'
    assert captured["url"] == "http://ollama.local/api/generate"
    assert captured["json"]["prompt"] == "Return JSON"
    assert captured["json"]["format"] == "json"
    assert "messages" not in captured["json"]


@pytest.mark.asyncio
async def test_configured_provider_uses_openai_chat_contract(monkeypatch):
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
    # Provider, endpoint, model and thinking are all supplied as configuration
    # rather than selected by an agent-specific code path.
    settings = Settings(
        llm_provider="test_gateway",
        llm_api_url="https://llm.example/v1/chat/completions",
        llm_api_key="test-key",
        llm_model="roadman-test",
        llm_api_style="openai",
        llm_thinking=True,
        llm_max_tokens=4096,
    )
    audited = {}

    async def fake_audit(agent_name, **kwargs):
        audited["agent_name"] = agent_name
        audited.update(kwargs)

    monkeypatch.setattr("app.planning.llm._audit_deepseek_call", fake_audit)
    result = await llm_complete(settings, "Return JSON", timeout=12, agent_name="requirement")

    assert result == '{"ok":true}'
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["json"]["model"] == "roadman-test"
    assert captured["json"]["messages"] == [{"role": "user", "content": "Return JSON"}]
    assert captured["json"]["max_tokens"] == 4096
    assert captured["json"]["temperature"] == 0
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert "thinking" not in captured["json"]
    assert "reasoning_effort" not in captured["json"]
    assert "reasoning_content" not in result
    assert audited["agent_name"] == "requirement"
    assert audited["usage"]["total_tokens"] == 18
    assert audited["success"] is True
