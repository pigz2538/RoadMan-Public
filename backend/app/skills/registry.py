import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import ValidationError

from ..domain.models import SkillResult
from .base import SkillAdapter, SkillContext
from .cache import MemorySkillCache, SkillCache

AuditSink = Callable[[str, SkillResult, SkillContext], Awaitable[None]]


class SkillRegistry:
    def __init__(
        self,
        cache: SkillCache | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._adapters: dict[str, SkillAdapter] = {}
        self._cache = cache or MemorySkillCache()
        self._audit_sink = audit_sink

    def register(self, adapter: SkillAdapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"Skill already registered: {adapter.name}")
        self._adapters[adapter.name] = adapter

    def names(self) -> list[str]:
        return sorted(self._adapters)

    async def execute(
        self,
        name: str,
        payload: dict[str, Any],
        context: SkillContext | None = None,
    ) -> SkillResult:
        call_context = context or SkillContext()
        if name not in self._adapters:
            result = SkillResult(success=False, provider=name, error_code="SKILL_NOT_FOUND")
            await self._audit(name, result, call_context)
            return result
        adapter = self._adapters[name]
        try:
            validated = await adapter.validate_input(payload)
        except ValidationError as exc:
            result = SkillResult(
                success=False,
                provider=name,
                warnings=["输入参数校验失败"],
                data={"issues": exc.errors(include_url=False, include_context=False)},
                error_code="SKILL_INPUT_INVALID",
            )
            await self._audit(name, result, call_context)
            return result

        cache_key = self._cache_key(name, adapter.version, validated)
        cached = await self._cache.get(cache_key)
        if cached:
            result = cached.model_copy(update={"cache_hit": True})
            await self._audit(name, result, call_context)
            return result

        result: SkillResult | None = None
        for attempt in range(adapter.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    adapter.execute(validated, call_context),
                    timeout=adapter.timeout_seconds,
                )
                break
            except (TimeoutError, httpx.TimeoutException):
                result = SkillResult(
                    success=False,
                    provider=name,
                    warnings=[f"第 {attempt + 1} 次调用超时"],
                    error_code="SKILL_TIMEOUT",
                )
            except httpx.TransportError:
                result = SkillResult(
                    success=False,
                    provider=name,
                    warnings=[f"第 {attempt + 1} 次网络调用失败"],
                    error_code="SKILL_NETWORK_ERROR",
                )
            except Exception:
                result = SkillResult(
                    success=False,
                    provider=name,
                    warnings=["Adapter 执行失败"],
                    error_code="SKILL_EXECUTION_FAILED",
                )
                break
        assert result is not None
        if result.success:
            await self._cache.set(cache_key, result, adapter.cache_ttl_seconds)
        await self._audit(name, result, call_context)
        return result

    async def health(self) -> dict[str, Any]:
        names = list(self._adapters)
        checks = await asyncio.gather(
            *(adapter.health_check() for adapter in self._adapters.values()),
            return_exceptions=True,
        )
        result = {
            name: check if not isinstance(check, Exception) else {"status": "down"}
            for name, check in zip(names, checks, strict=True)
        }
        result["_cache"] = await self._cache.health()
        return result

    async def close(self) -> None:
        await self._cache.close()

    async def _audit(
        self,
        name: str,
        result: SkillResult,
        context: SkillContext,
    ) -> None:
        if not self._audit_sink:
            return
        try:
            await self._audit_sink(name, result, context)
        except Exception:
            # Audit failure must never make a provider result unavailable.
            return

    @staticmethod
    def _cache_key(name: str, version: str, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return f"{name}:{version}:{hashlib.sha256(raw.encode()).hexdigest()}"
