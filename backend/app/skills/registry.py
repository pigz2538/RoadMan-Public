import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from ..domain.models import SkillResult
from .base import SkillAdapter, SkillContext


@dataclass
class CacheEntry:
    expires_at: float
    value: SkillResult


class SkillRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, SkillAdapter] = {}
        self._cache: dict[str, CacheEntry] = {}

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
        if name not in self._adapters:
            return SkillResult(success=False, provider=name, error_code="SKILL_NOT_FOUND")
        adapter = self._adapters[name]
        validated = await adapter.validate_input(payload)
        cache_key = self._cache_key(name, validated)
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > time.monotonic():
            return cached.value.model_copy(update={"cache_hit": True})

        result: SkillResult | None = None
        for attempt in range(adapter.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    adapter.execute(validated, context or SkillContext()),
                    timeout=adapter.timeout_seconds,
                )
                break
            except TimeoutError:
                result = SkillResult(
                    success=False,
                    provider=name,
                    warnings=[f"第 {attempt + 1} 次调用超时"],
                    error_code="SKILL_TIMEOUT",
                )
            except Exception as exc:
                result = SkillResult(
                    success=False,
                    provider=name,
                    warnings=[str(exc)],
                    error_code="SKILL_EXECUTION_FAILED",
                )
        assert result is not None
        if result.success:
            self._cache[cache_key] = CacheEntry(
                expires_at=time.monotonic() + adapter.cache_ttl_seconds,
                value=result,
            )
        return result

    async def health(self) -> dict[str, Any]:
        checks = await asyncio.gather(
            *(adapter.health_check() for adapter in self._adapters.values()),
            return_exceptions=True,
        )
        return {
            name: check if not isinstance(check, Exception) else {"status": "down", "reason": str(check)}
            for name, check in zip(self._adapters, checks, strict=True)
        }

    @staticmethod
    def _cache_key(name: str, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return f"{name}:{hashlib.sha256(raw.encode()).hexdigest()}"
