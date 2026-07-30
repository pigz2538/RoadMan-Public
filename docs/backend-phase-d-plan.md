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
8. `build_stages`
9. `verify_plan`
10. `repair_plan`（最多一次）
11. `render_markdown`
12. `persist_trip`

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

- 自动测试：后端 19 项通过。
- 前端 TypeScript/Vite 生产构建通过。
- Ollama Cloud 连通性和 Requirement 抽取通过。
- “周六从武汉去庐山，两天一夜”生成真实高德往返道路、2 天阶段和 Markdown。
- 缺少出发地的请求进入一次追问；补充“武汉”后恢复并完成。
- ARQ Job、Redis SSE、PostgreSQL `state_json`/`plan_markdown` 全链路通过。
- Playwright 等待高德加载后确认地图、2 张阶段卡和 Markdown 入口，控制台无错误。

验收页面（运行中的本机数据）：
`http://127.0.0.1:8080/trips/trip_bec9b7756e21/plan`
