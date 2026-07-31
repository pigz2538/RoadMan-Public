from __future__ import annotations

from collections import Counter, deque
from time import monotonic


class RequestMetrics:
    def __init__(self) -> None:
        self.started_at = monotonic()
        self.requests = 0
        self.errors = 0
        self.statuses: Counter[str] = Counter()
        self.routes: Counter[str] = Counter()
        self.latency_total_ms = 0.0

    def record(self, path: str, status_code: int, latency_ms: float) -> None:
        self.requests += 1
        self.errors += int(status_code >= 500)
        self.statuses[str(status_code)] += 1
        self.routes[path] += 1
        self.latency_total_ms += latency_ms

    def snapshot(self) -> dict:
        return {
            "uptime_seconds": round(monotonic() - self.started_at, 2),
            "requests": self.requests,
            "server_errors": self.errors,
            "average_latency_ms": round(self.latency_total_ms / self.requests, 2) if self.requests else 0,
            "status_counts": dict(self.statuses),
            "top_routes": dict(self.routes.most_common(20)),
        }


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = monotonic()
        bucket = self._requests.setdefault(key, deque())
        while bucket and now - bucket[0] >= self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        if len(self._requests) > 2000:
            self._requests = {name: values for name, values in self._requests.items() if values}
        return True
