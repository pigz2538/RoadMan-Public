from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..domain.models import SkillResult


@dataclass
class SkillContext:
    trip_id: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillAdapter(ABC):
    name: str
    version: str = "1.0.0"
    category: str
    timeout_seconds: float = 8.0
    max_retries: int = 1
    cache_ttl_seconds: int = 1800

    async def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    @abstractmethod
    async def execute(self, payload: dict[str, Any], context: SkillContext) -> SkillResult:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        raise NotImplementedError
