# RoadMan 真实闭环验收记录（2026-08-31）

本记录只统计“从输入到最终结果”的可复现链路，不把进度条到 100% 视为成功。完整行程必须同时满足：行程状态为 `completed`、持久化校验 `verification_result.passed=true`、每日基本结构可读、必去地点存在，并且 HTML 导出返回有效文档。

## 1. 用户反馈的北京案例

输入：

> 周五晚上从武汉出发，去北京，周日晚八点前回来，帮我规划一个行程，情侣出游舒适为主

执行命令：

```powershell
python -u deploy/weird_prompt_acceptance.py `
  --base-url http://127.0.0.1:8000 `
  --scenarios evaluation/weird_live_scenarios_v2.json `
  --case-id v2-beijing-couple-default-transport `
  --full-limit 1 --force-full --timeout 900 `
  --output evaluation/results/beijing-final-regression-20260831.json
```

结果：

- `1/1 passed`，`full=1`，模式为非思考模式；
- 后台状态从 `queued` 经过需求校验、跨城交通、目的地检索、路线编排、服务复核，最终到 `completed`；
- `verification_result.passed=true`，没有 blocker；
- HTML 导出成功（约 2.9 MB）；
- 天气降级和代表性景点未完全覆盖仅作为 warning，不会阻断可用行程，也不会被伪装成无风险。

新镜像重启后再次执行的证据文件：[beijing-final-container-regression-20260831.json](../evaluation/results/beijing-final-container-regression-20260831.json)。

## 2. 多场景真实闭环

执行命令：

```powershell
python -u deploy/weird_prompt_acceptance.py `
  --base-url http://127.0.0.1:8000 `
  --scenarios evaluation/weird_live_scenarios_v2.json `
  --full-limit 12 --force-full --timeout 900 `
  --output evaluation/results/weird-v2-full-after-confirmed-fix-20260831.json `
  --keep-trips
```

结果：`11/11 passed`，其中 9 条走完整行程和导出，2 条按预期进入澄清（缺少目的地或时间窗口不可行），没有把澄清误判为成功行程，也没有把失败路线算作通过。

覆盖内容包括：跨城周末、下班后出发、英文和中文混合、长途自驾跨天、跨海接驳、多人出游、两座城市、天气和特殊事件、模糊需求，以及北京情侣案例。

证据文件：[weird-v2-full-after-confirmed-fix-20260831.json](../evaluation/results/weird-v2-full-after-confirmed-fix-20260831.json)

## 3. 编辑后重排 E1–E10

每一条都执行“编辑解释 → 修改预览 → 用户确认应用 → 单次后台重排 → 轮询到最终状态 → 校验 → 回滚复原”。

```powershell
python -u deploy/edit_replan_acceptance.py `
  --base-url http://127.0.0.1:8000 `
  --trip-id trip_0f8a69f98447 `
  --start-index 1 --end-index 10 `
  --output evaluation/results/edit-replan-live-after-confirmed-fix2-20260831.json
```

结果：`10/10` 通过。地图添加的餐饮/住宿不会再污染必去景点；用户确认项会持久化；删除项不会在重排中复活；重复点击不会创建竞争中的规划任务或造成进度倒退。

证据文件：[edit-replan-live-after-confirmed-fix2-20260831.json](../evaluation/results/edit-replan-live-after-confirmed-fix2-20260831.json)

## 4. API、容器和前端回归

| 检查 | 结果 |
| --- | --- |
| `backend/tests` | 158 passed, 1 skipped |
| `npm run test` | 2 passed |
| `npm run build` | 成功 |
| Playwright desktop-1366 | 8 passed, 3 skipped |
| Playwright desktop-1920 | 8 passed, 3 skipped |
| Playwright Firefox 3D | 1 passed |
| `deploy/api_smoke.py` | 58 个接口检查，0 failures |
| Docker Compose | backend healthy、worker running、postgres/redis healthy、frontend running |

桌面端 E2E 固定单 worker：规划页同时加载地图画布和 3D 模型时，多个浏览器 worker 会争用渲染队列，可能造成导航超时；串行配置保证验收结果稳定且不改变产品运行时并发能力。

## 5. 当前仍需诚实展示的外部状态

外部服务（天气、交通、旅行检索、车辆目录）仍可能返回限流、风险控制或无结果。系统会保留 provider、错误码和估算标记，并将非关键天气/检索问题降级为 warning；只有路线、时间、必去地点等核心约束未解决时才失败。验收报告中的 warning 不应被误解为供应商实时数据保证。
