import asyncio
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import redis.asyncio as redis

from ..core.config import get_settings
from ..domain.models import SSEEvent


@dataclass(frozen=True)
class StoredEvent:
    id: int
    payload: SSEEvent


class SSEManager:
    def __init__(self, max_events_per_trip: int = 200) -> None:
        self._max_events = max_events_per_trip
        self._events: dict[str, deque[StoredEvent]] = defaultdict(
            lambda: deque(maxlen=max_events_per_trip),
        )
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._redis_down_until = 0.0

    async def publish(self, payload: SSEEvent) -> StoredEvent:
        stored = await self._publish_redis(payload)
        if stored:
            return stored
        async with self._locks[payload.trip_id]:
            events = self._events[payload.trip_id]
            event_id = events[-1].id + 1 if events else 1
            stored = StoredEvent(id=event_id, payload=payload)
            events.append(stored)
            return stored

    async def after(self, trip_id: str, last_event_id: int = 0) -> list[StoredEvent]:
        stored = await self._after_redis(trip_id, last_event_id)
        if stored is not None:
            return stored
        async with self._locks[trip_id]:
            return [
                event
                for event in self._events.get(trip_id, ())
                if event.id > last_event_id
            ]

    async def seed_planning_demo(self, trip_id: str) -> None:
        if await self.after(trip_id):
            return
        templates = [
            ("planning_started", "正在建立行程上下文", 5, "load_context", None),
            ("node_started", "正在识别出发地与目的地", 20, "extract_trip_request", None),
            ("tool_started", "正在查询真实道路路线", 42, "build_base_route", "amap.route"),
            ("tool_completed", "路线查询已返回", 68, "build_base_route", "amap.route"),
            ("progress", "正在拆分天和阶段", 84, "build_stages", None),
            ("planning_completed", "路书已生成", 100, "persist_trip", None),
        ]
        for event, label, progress, node, tool in templates:
            await self.publish(
                SSEEvent(
                    event=event,
                    trip_id=trip_id,
                    node=node,
                    tool=tool,
                    label=label,
                    progress=progress,
                )
            )

    async def _publish_redis(self, payload: SSEEvent) -> StoredEvent | None:
        if time.monotonic() < self._redis_down_until:
            return None
        client = self._client()
        try:
            event_id = int(await client.incr(self._counter_key(payload.trip_id)))
            stored = StoredEvent(event_id, payload)
            encoded = json.dumps(
                {"id": event_id, "payload": payload.model_dump(mode="json")},
                ensure_ascii=False,
            )
            async with client.pipeline(transaction=True) as pipeline:
                pipeline.rpush(self._events_key(payload.trip_id), encoded)
                pipeline.ltrim(self._events_key(payload.trip_id), -self._max_events, -1)
                pipeline.expire(self._events_key(payload.trip_id), 24 * 60 * 60)
                pipeline.expire(self._counter_key(payload.trip_id), 24 * 60 * 60)
                await pipeline.execute()
            return stored
        except Exception:
            self._redis_down_until = time.monotonic() + 5
            return None
        finally:
            await client.aclose()

    async def _after_redis(
        self,
        trip_id: str,
        last_event_id: int,
    ) -> list[StoredEvent] | None:
        if time.monotonic() < self._redis_down_until:
            return None
        client = self._client()
        try:
            items = await client.lrange(self._events_key(trip_id), 0, -1)
            result: list[StoredEvent] = []
            for encoded in items:
                value = json.loads(encoded)
                if int(value["id"]) > last_event_id:
                    result.append(
                        StoredEvent(
                            id=int(value["id"]),
                            payload=SSEEvent.model_validate(value["payload"]),
                        )
                    )
            return result
        except Exception:
            self._redis_down_until = time.monotonic() + 5
            return None
        finally:
            await client.aclose()

    def _client(self):
        settings = get_settings()
        return redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
        )

    @staticmethod
    def _events_key(trip_id: str) -> str:
        return f"roadman:sse:{trip_id}:events"

    @staticmethod
    def _counter_key(trip_id: str) -> str:
        return f"roadman:sse:{trip_id}:counter"


sse_manager = SSEManager()
