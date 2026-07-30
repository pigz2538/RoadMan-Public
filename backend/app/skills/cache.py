from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from ..domain.models import SkillResult


class SkillCache(Protocol):
    async def get(self, key: str) -> SkillResult | None: ...

    async def set(self, key: str, value: SkillResult, ttl_seconds: int) -> None: ...

    async def health(self) -> dict: ...

    async def close(self) -> None: ...


@dataclass
class MemoryEntry:
    expires_at: float
    value: SkillResult


class MemorySkillCache:
    def __init__(self) -> None:
        self._values: dict[str, MemoryEntry] = {}

    async def get(self, key: str) -> SkillResult | None:
        entry = self._values.get(key)
        if not entry:
            return None
        if entry.expires_at <= time.monotonic():
            self._values.pop(key, None)
            return None
        return entry.value

    async def set(self, key: str, value: SkillResult, ttl_seconds: int) -> None:
        self._values[key] = MemoryEntry(
            expires_at=time.monotonic() + ttl_seconds,
            value=value,
        )

    async def health(self) -> dict:
        return {"status": "ready", "backend": "memory", "entries": len(self._values)}

    async def close(self) -> None:
        self._values.clear()


class RedisFallbackSkillCache:
    def __init__(
        self,
        redis_url: str,
        prefix: str,
        connect_timeout_seconds: float,
    ) -> None:
        self.prefix = prefix
        self.memory = MemorySkillCache()
        self.redis: Redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=connect_timeout_seconds,
            socket_timeout=connect_timeout_seconds,
        )
        self.redis_available: bool | None = None

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    async def get(self, key: str) -> SkillResult | None:
        try:
            raw = await self.redis.get(self._key(key))
            self.redis_available = True
            if raw:
                return SkillResult.model_validate_json(raw)
        except Exception:
            self.redis_available = False
        return await self.memory.get(key)

    async def set(self, key: str, value: SkillResult, ttl_seconds: int) -> None:
        await self.memory.set(key, value, ttl_seconds)
        try:
            await self.redis.set(
                self._key(key),
                value.model_dump_json(),
                ex=ttl_seconds,
            )
            self.redis_available = True
        except Exception:
            self.redis_available = False

    async def health(self) -> dict:
        try:
            await self.redis.ping()
            self.redis_available = True
        except Exception:
            self.redis_available = False
        memory = await self.memory.health()
        return {
            "status": "ready" if self.redis_available else "degraded",
            "backend": "redis" if self.redis_available else "memory-fallback",
            "memory_entries": memory["entries"],
        }

    async def close(self) -> None:
        await self.memory.close()
        await self.redis.aclose()
