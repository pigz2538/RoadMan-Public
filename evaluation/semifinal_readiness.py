"""Build a reproducible semifinal evidence bundle from deterministic checks.

This script does not claim that simulated range replays are real road tests.
It combines the range and degradation evaluators, records the current source
test inventory, and can optionally probe a running local deployment.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from range_accuracy import DEFAULT_INPUT, evaluate_dataset
from safety_scenarios import evaluate_all


ROOT = Path(os.environ.get("ROADMAN_REPO_ROOT") or Path(__file__).resolve().parents[1])
DEFAULT_JSON = Path(__file__).with_name("results") / "semifinal-readiness.json"
DEFAULT_MARKDOWN = Path(__file__).with_name("results") / "semifinal-readiness.md"


def _git_commit() -> str | None:
    explicit = os.environ.get("ROADMAN_GIT_COMMIT")
    if explicit:
        return explicit.strip()
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5
        ).strip()
    except (OSError, subprocess.SubprocessError):
        head = ROOT / ".git" / "HEAD"
        try:
            value = head.read_text(encoding="utf-8").strip()
            if value.startswith("ref: "):
                return (ROOT / ".git" / value[5:]).read_text(encoding="utf-8").strip()
            return value or None
        except OSError:
            return None


def _count_tests(folder: Path, patterns: tuple[str, ...]) -> int:
    count = 0
    for pattern in patterns:
        for path in folder.rglob(pattern):
            if "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            count += text.count("def test_") + text.count("test(")
    return count


def _probe_live(base_url: str | None) -> dict[str, Any]:
    if not base_url:
        return {"requested": False, "available": None, "note": "未要求在线探测"}
    url = f"{base_url.rstrip('/')}/health"
    try:
        with urlopen(url, timeout=10) as response:  # noqa: S310 - caller selects local URL
            body = response.read().decode("utf-8", errors="replace")
            return {
                "requested": True,
                "available": response.status == 200,
                "status_code": response.status,
                "url": url,
                "response_excerpt": body[:300],
            }
    except (OSError, URLError) as error:
        return {"requested": True, "available": False, "url": url, "error": str(error)}


def build_report(base_url: str | None = None) -> dict[str, Any]:
    range_payload = json.loads(DEFAULT_INPUT.read_text(encoding="utf-8"))
    range_result = evaluate_dataset(range_payload)
    safety_result = evaluate_all()
    required_files = [
        "README.md",
        "PROJECT.md",
        "docker-compose.yml",
        "docs/safety-and-data-boundary.md",
        "deploy/api_smoke.py",
        "deploy/full_journey_acceptance.py",
        "evaluation/range_accuracy.py",
        "evaluation/safety_scenarios.py",
    ]
    file_checks = {name: (ROOT / name).is_file() for name in required_files}
    live = _probe_live(base_url)
    checks = {
        "range_baseline_passed": range_result["passed"],
        "range_claim_labeled_non_real": not range_result["is_real_road_data"],
        "degradation_matrix_passed": safety_result["passed"],
        "all_required_files_present": all(file_checks.values()),
        "live_probe_passed_when_requested": live["available"] is not False,
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "checks": checks,
        "passed": all(checks.values()),
        "range_evaluation": {
            "dataset_id": range_result["dataset_id"],
            "data_kind": range_result["data_kind"],
            "claim_boundary": range_result["claim_boundary"],
            "metrics": range_result["metrics"],
            "thresholds": range_result["thresholds"],
        },
        "degradation_evaluation": {
            key: safety_result[key]
            for key in (
                "dataset_id", "sample_count", "task_completion_rate",
                "route_executability_rate", "degradation_handled_rate",
                "average_latency_ms", "p95_latency_ms", "passed",
            )
        },
        "source_test_inventory": {
            "backend_and_evaluation_test_functions": _count_tests(
                ROOT, ("test_*.py",)
            ),
            "frontend_test_declarations": _count_tests(
                ROOT / "frontend" / "tests", ("*.spec.ts", "*.test.ts")
            ),
            "note": "源码静态计数仅用于规模索引，最终通过数以测试命令输出为准。",
        },
        "required_file_checks": file_checks,
        "live_probe": live,
    }


def render_markdown(report: dict[str, Any]) -> str:
    range_metrics = report["range_evaluation"]["metrics"]
    degradation = report["degradation_evaluation"]
    rows = "\n".join(
        f"| {name} | {'通过' if passed else '未通过'} |"
        for name, passed in report["checks"].items()
    )
    return f"""# RoadMan 复赛可复现验收摘要

生成时间：{report['generated_at']}  
代码提交：`{report.get('git_commit') or 'unknown'}`  
总结果：**{'PASS' if report['passed'] else 'FAIL'}**

## 自动检查

| 检查项 | 结果 |
|---|---|
{rows}

## 续航误差证据

- 数据类型：`{report['range_evaluation']['data_kind']}`（不是实车道路数据）
- 样本数：{range_metrics['sample_count']}
- 能耗 MAPE：{range_metrics['energy_mape_percent']}%
- 能耗 RMSE：{range_metrics['energy_rmse_kwh']} kWh
- 能耗 P95 绝对百分比误差：{range_metrics['energy_p95_absolute_error_percent']}%
- 等效续航 MAPE：{range_metrics['range_mape_percent']}%
- 到达 SOC MAE：{range_metrics['soc_mae_percentage_points']} 个百分点

声明边界：{report['range_evaluation']['claim_boundary']}

## 异常与降级证据

- 场景数：{degradation['sample_count']}
- 预期行为完成率：{degradation['task_completion_rate']:.0%}
- 降级处理成功率：{degradation['degradation_handled_rate']:.0%}
- 可直接执行路线比例：{degradation['route_executability_rate']:.2%}
- P95 本地规则执行时延：{degradation['p95_latency_ms']} ms

“可直接执行路线比例”故意不等于 100%：当补能服务中断且只有路线估算点时，系统继续生成方案但明确要求出发前确认，不把估算位置伪装为真实可用充电桩。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="optional running deployment, e.g. http://127.0.0.1:8000")
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    report = build_report(args.base_url)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(f"[semifinal] {'PASS' if report['passed'] else 'FAIL'} -> {args.json_output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
