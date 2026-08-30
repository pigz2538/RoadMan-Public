import httpx
import pytest

from app.domain.models import SkillResult
from app.skills.base import SkillAdapter, SkillContext
from app.skills.cache import MemorySkillCache, RedisFallbackSkillCache
from app.skills.registry import SkillRegistry


class CountingAdapter(SkillAdapter):
    name = "test.counting"
    category = "test"
    max_retries = 1
    cache_ttl_seconds = 60

    def __init__(self, fail_once: bool = False):
        self.calls = 0
        self.fail_once = fail_once

    async def execute(self, payload, context):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise httpx.ConnectError("temporary")
        return SkillResult(success=True, provider="test", data=payload)

    async def health_check(self):
        return {"status": "ready"}


class EmptyCollectionAdapter(SkillAdapter):
    name = "test.empty-collection"
    category = "test"

    async def execute(self, payload, context):
        return SkillResult(success=True, provider="test", data={"items": []})

    async def health_check(self):
        return {"status": "ready"}


class UnconfiguredKeyedAdapter(CountingAdapter):
    name = "test.unconfigured-keyed"

    def __init__(self):
        super().__init__()
        self.api_key = ""


@pytest.mark.asyncio
async def test_registry_caches_and_audits():
    audits = []

    async def audit(name, result, context):
        audits.append((name, result.cache_hit, context.request_id))

    adapter = CountingAdapter()
    registry = SkillRegistry(cache=MemorySkillCache(), audit_sink=audit)
    registry.register(adapter)
    context = SkillContext(request_id="registry-test")

    first = await registry.execute(adapter.name, {"b": 2, "a": 1}, context)
    second = await registry.execute(adapter.name, {"a": 1, "b": 2}, context)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert adapter.calls == 1
    assert audits == [
        ("test.counting", False, "registry-test"),
        ("test.counting", True, "registry-test"),
    ]


@pytest.mark.asyncio
async def test_registry_retries_transport_errors_only():
    adapter = CountingAdapter(fail_once=True)
    registry = SkillRegistry(cache=MemorySkillCache())
    registry.register(adapter)

    result = await registry.execute(adapter.name, {"value": 1})

    assert result.success is True
    assert adapter.calls == 2


@pytest.mark.asyncio
async def test_registry_does_not_cache_empty_provider_collections():
    adapter = EmptyCollectionAdapter()
    registry = SkillRegistry(cache=MemorySkillCache())
    registry.register(adapter)

    first = await registry.execute(adapter.name, {"query": "hotel"})
    second = await registry.execute(adapter.name, {"query": "hotel"})

    assert first.success is True
    assert second.success is True
    assert first.cache_hit is False
    assert second.cache_hit is False


@pytest.mark.asyncio
async def test_registry_does_not_read_stale_cache_for_unconfigured_keyed_adapter():
    adapter = UnconfiguredKeyedAdapter()
    cache = MemorySkillCache()
    registry = SkillRegistry(cache=cache)
    registry.register(adapter)
    key = registry._cache_key(adapter.name, adapter.version, {"query": "cached"})
    await cache.set(
        key,
        SkillResult(success=True, provider="stale", data={"cached": True}),
        60,
    )

    result = await registry.execute(adapter.name, {"query": "cached"})

    assert result.cache_hit is False
    assert result.success is True
    assert result.provider == "test"
    assert adapter.calls == 1


class FakeRedis:
    def __init__(self, fail: bool = False):
        self.values = {}
        self.fail = fail

    async def get(self, key):
        if self.fail:
            raise ConnectionError
        return self.values.get(key)

    async def set(self, key, value, ex):
        if self.fail:
            raise ConnectionError
        self.values[key] = value

    async def ping(self):
        if self.fail:
            raise ConnectionError
        return True

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_redis_cache_and_memory_fallback():
    cache = RedisFallbackSkillCache("redis://invalid", "test", 0.01)
    cache.redis = FakeRedis()
    value = SkillResult(success=True, provider="cache-test", data={"value": 1})
    await cache.set("key", value, 60)
    assert (await cache.get("key")).data == {"value": 1}
    assert (await cache.health())["backend"] == "redis"

    fallback = RedisFallbackSkillCache("redis://invalid", "test", 0.01)
    fallback.redis = FakeRedis(fail=True)
    await fallback.set("key", value, 60)
    assert (await fallback.get("key")).data == {"value": 1}
    assert (await fallback.health())["backend"] == "memory-fallback"
