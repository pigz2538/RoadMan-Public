# RoadMan 阶段 D 完成记录

更新日期：2026-07-30

状态：已完成，允许进入阶段 E

## 规划闭环

基础 LangGraph 已实现以下节点：

1. `load_context`
2. `extract_trip_request`
3. `apply_defaults`
4. `validate_required_fields`
5. `generate_clarification`
6. `build_base_route`
7. `split_into_days`
8. `build_local_routes`
9. `build_stages`
10. `sample_weather`
11. `verify_plan`
12. `repair_plan`（最多一次）
13. `render_markdown`
14. `persist_trip`

Requirement Agent 使用 Ollama Cloud `deepseek-v4-flash:cloud`。由于 Ollama Cloud
当前不支持原生 structured outputs，RoadMan 使用严格 JSON 提示、白名单字段解析、
Pydantic 校验和确定性中文解析回退；模型不能直接调用路线或写数据库。

## 暂停、恢复和任务

- 缺少出发地、目的地或日期时，Trip 进入 `clarification_required`。
- 中间 `state_json`、Agent 消息和可见默认值持久化到 Trip。
- 用户通过澄清接口补充后，ARQ 重新投递任务并从已保存 State 继续。
- 最多显示 3 轮澄清；最后一轮提示一次补齐全部字段。
- Job 取消在 LangGraph 节点边界生效，Trip 进入 `paused`。
- Worker 通过 Redis 写入 SSE 事件，Web 进程可用 `Last-Event-ID` 跨进程续传。

## API

- `POST /api/v1/trips/{trip_id}/planning/start`
- `GET /api/v1/trips/{trip_id}/planning`
- `POST /api/v1/trips/{trip_id}/planning/clarifications`
- `GET /api/v1/trips/{trip_id}/planning/events`
- `GET /api/v1/trips/{trip_id}/roadbook`

## 验收

- 自动测试：后端 23 项通过，1 个真实接口集成用例默认跳过。
- 前端 TypeScript/Vite 生产构建通过。
- Ollama Cloud 连通性和 Requirement 抽取通过。
- 离线 2 天与 5 天场景覆盖多阶段、驾车、公交、步行、骑行、逐时天气和路况。
- 真实接口用例调用 Ollama Cloud、高德和 Open-Meteo，生成 5 天、8 阶段行程；
  请求和最终采用的方式均覆盖公交、步行与骑行。
- 驾车路线解析高德 `tmcs` 路况分段；未来阶段标记为“当前路况参考”，
  高德未返回分段状态时明确降级。
- 缺少出发地的请求进入一次追问；补充“武汉”后恢复并完成。
- ARQ Job、Redis SSE、PostgreSQL `state_json`/`plan_markdown` 全链路通过。
- Docker API 验证 5 天、8 阶段、四种交通方式、8 份天气/路况摘要和 Markdown。
- Playwright 等待高德加载后确认真实道路、8 张跨天阶段卡、Markdown 入口，
  控制台无错误。

验收页面（运行中的本机数据）：
`http://127.0.0.1:8080/trips/trip_5a45692d7085/plan`
