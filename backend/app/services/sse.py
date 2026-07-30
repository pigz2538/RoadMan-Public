import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass

from ..domain.models import SSEEvent


@dataclass(frozen=True)
class StoredEvent:
    id: int
    payload: SSEEvent


class SSEManager:
    def __init__(self, max_events_per_trip: int = 200) -> None:
        self._events: dict[str, deque[StoredEvent]] = defaultdict(
            lambda: deque(maxlen=max_events_per_trip),
        )
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def publish(self, payload: SSEEvent) -> StoredEvent:
        async with self._locks[payload.trip_id]:
            events = self._events[payload.trip_id]
            event_id = events[-1].id + 1 if events else 1
            stored = StoredEvent(id=event_id, payload=payload)
            events.append(stored)
            return stored

    async def after(self, trip_id: str, last_event_id: int = 0) -> list[StoredEvent]:
        async with self._locks[trip_id]:
            return [
                event
                for event in self._events.get(trip_id, ())
                if event.id > last_event_id
            ]

    async def seed_planning_demo(self, trip_id: str) -> None:
        async with self._locks[trip_id]:
            if self._events.get(trip_id):
                return
        templates = [
            ("planning_started", "正在建立行程上下文", 5, "load_context", None),
            ("node_started", "正在识别出发地与目的地", 20, "extract_trip_request", None),
            ("tool_started", "正在查询武汉—庐山路线", 42, "build_base_route", "amap.route"),
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


sse_manager = SSEManager()
