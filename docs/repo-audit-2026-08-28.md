# 仓库审计记录（2026-08-28）

这份记录对应当前 `main` 工作区，目的是把“代码已修复”和“外部服务暂不可用”分开，避免把配置故障误报成规划算法故障。

## 范围

- `backend/app`：API、LangGraph 排程、编辑、导出、Skill Adapter、持久化与队列。
- `frontend/src`：首页预检、车型抽屉、规划页、地图、SSE 和编辑面板。
- `shared/schemas`：跨前后端 JSON 契约。
- `deploy/`、`evaluation/`、`backend/tests/`、`frontend/tests/`：运行验收和回归。
- `docs/`、`README.md`、`project.md`：当前能力、配置、边界与验收命令。

## 本次修复

### 1. 自驾返程截止时间

返程时间按“到达出发地的最晚时间”解释。排程阶段会在最后一天提前扣除返程道路时长，并为自驾预留补能/休息缓冲；最终返程阶段使用这个有效最晚出发时刻。这样深度驾驶拆分后不会从 16:30 出发、插入休息后又越过 20:00 到达。自动修复遇到 `RETURN_DEADLINE_UNACHIEVABLE` 时会重新构建路线，而不是重复套用同一份坏阶段。

回归用例：`test_return_deadline_trims_last_day_before_long_drive`，验证补能/休息插入后返程阶段仍在同一日且不晚于截止时间。

### 2. 住宿候选的目的地锚定

住宿查询可携带目的地中心坐标、目的地范围和最大距离。旅行信息服务返回异地酒店卡片时会过滤并写出可解释 warning；过滤后为空交给地图/公开网页等来源降级。省域或多目的地使用更宽的检索半径，但不把它当作景点规划半径，不会把用户未提出的“全程只在某地”硬编码进去。

回归用例：`test_flyai_hotel_adapter_filters_cross_city_results`。

### 3. 易失效测试样例

接口冒烟和日期回归不再使用已过期的固定预约日期，改为相对今天的未来窗口。住宿“无库存”是合法业务结果，不再被冒烟脚本误报成接口挂掉；只有 HTTP、协议或适配器错误才计为失败。

### 4. 云端智能体测试边界

需求理解和语义编辑坚持使用模型结构化输出；模型不可用时接口明确返回 `REQUIREMENT_AGENT_UNAVAILABLE`/`EDIT_AGENT_REQUIRED`，不回退到关键词猜测。浏览器中依赖真实云端的 4 个测试默认跳过，设置 `ROADMAN_RUN_LIVE_AGENT_E2E=1` 后才运行。当前环境的 Ollama `/api/generate` 返回 HTTP 403，因此需求评测为 0/12（外部授权阻断），不是把失败样例伪装成通过。

### 5. 删除行程与后台任务竞态

删除历史行程时，已经排队的规划任务可能仍在后台启动。工作进程现在把找不到行程视为可预期取消（`TRIP_DELETED`），清理任务状态并停止写入，不再把已删除行程记录成失败规划或输出误导性的堆栈日志。
回归用例：`test_deleted_trip_planning_job_is_discarded`。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| Python 编译 | `compileall-ok` |
| 后端 | `126 passed, 1 skipped` |
| 前端单测 | `2 passed` |
| 前端构建 | 通过；仅有 `model-viewer` 大 chunk 提示 |
| 前端 E2E | `15 passed, 6 skipped`；跳过项均有显式原因 |
| API 冒烟 | `58/58` |
| Docker | 五个 Compose 服务已重建；backend、postgres、redis healthy，worker/frontend running |
| 需求评测 | `0/12`，12 条均因当前 Ollama Key 的 403 无法取得语义结果 |
| 完整旅程 | 规划与确定性校验已完成；编辑阶段因同一 403 按契约返回 503 |

## 外部配置恢复后

1. 更新被授权的 `OLLAMA_API_KEY`（不要提交到仓库或日志）。
2. 用最小生成请求验证 Key，不要只检查模型列表。
3. 运行 `python evaluation/run_evals.py`，目标为 12/12。
4. 设置 `ROADMAN_RUN_LIVE_AGENT_E2E=1` 后运行真实预检浏览器用例。
5. 运行 `python deploy/full_journey_acceptance.py --timeout 600`，确认语义编辑、路线重建和五种导出均完成。

## 未作为阻断的维护项

- `model-viewer` 生产 chunk 约 1 MB；当前有自适应渲染比例和 Firefox 回归，后续可按路由动态加载。
- 真实地图没有浏览器 Key 时显示示例底图并标明状态；不能把示例轨迹当作实时道路。
- 景点预约、开放时间、停车费和班次属于会变化的外部事实，导出时保留来源/核验时间，出发前仍需人工复核。
