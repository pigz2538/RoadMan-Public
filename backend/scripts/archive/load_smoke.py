"""Small dependency-free concurrency smoke test for health and monitoring APIs."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.request


def request(url: str) -> tuple[int, float]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, (time.perf_counter() - started) * 1000
    except Exception:
        return 0, (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    url = f"{args.base_url.rstrip('/')}/health"
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda _: request(url), range(args.requests)))
    latencies = [latency for _, latency in results]
    report = {
        "url": url,
        "requests": len(results),
        "successes": sum(status == 200 for status, _ in results),
        "average_ms": round(statistics.mean(latencies), 2),
        "p95_ms": round(sorted(latencies)[max(0, int(len(latencies) * .95) - 1)], 2),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["successes"] == report["requests"] else 1)


if __name__ == "__main__":
    main()
