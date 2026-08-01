# RoadMan API 契约 v0.4

开发地址：`http://localhost:8000`，交互式文档：`/docs`。Docker 统一入口为
`http://localhost:8080`。

错误统一为：

```json
{
  "error": {
    "code": "TRIP_NOT_FOUND",
    "message": "行程不存在",
    "details": {"trip_id": "trip_x"},
    "request_id": "req_x"
  }
}
```

响应头 `X-Request-ID` 可用于串联日志。服务不会在响应、日志或 SkillCall 审计中
记录第三方 API Key。

## 业务资源

| 方法 | 路径 | 说明 |
|---|---|---|
| POST/GET | `/api/v1/trips` | 创建、列出 Trip |
| GET/PATCH/DELETE | `/api/v1/trips/{trip_id}` | 查询、更新、删除 Trip |
| GET | `/api/v1/trips/mock/wuhan-lushan` | 固定验收行程 |
| GET | `/api/v1/trips/{trip_id}/recommendations` | 景点/住宿/餐饮排序备选 |
| POST | `/api/v1/trips/{trip_id}/editing/interpret` | 结合当前选择理解局部修改意图 |
| POST | `/api/v1/trips/{trip_id}/patches/preview` | 预览加入/替换，不修改 Trip |
| POST | `/api/v1/trips/{trip_id}/patches/preview-map-point` | 将地图选点转为加入活动的预览补丁，不修改 Trip |
| POST | `/api/v1/trips/{trip_id}/patches/preview-delete` | 预览删除活动，不修改 Trip |
| GET | `/api/v1/trips/{trip_id}/patches/{patch_id}` | 查询修改预览 |
| POST | `/api/v1/trips/{trip_id}/patches/{patch_id}/apply` | 确认并应用修改 |
| POST | `/api/v1/trips/{trip_id}/patches/{patch_id}/reject` | 放弃修改 |
| POST | `/api/v1/trips/{trip_id}/patches/{patch_id}/rollback` | 撤销已应用修改 |
| POST/GET | `/api/v1/vehicles` | 创建、列出车辆 |
| GET/PATCH/DELETE | `/api/v1/vehicles/{vehicle_id}` | 车辆 CRUD |
| POST | `/api/v1/files` | 校验大小、扩展名、MIME 和文件签名后上传 |
| GET | `/api/v1/files/{file_id}` | 文件元数据 |
| GET | `/api/v1/files/{file_id}/content` | 下载文件内容 |
| POST | `/api/v1/files/{file_id}/extract` | 解析附件并返回待确认预览 |
| POST | `/api/v1/files/{file_id}/confirm` | 用户确认后写入地点需求 |
| POST/GET | `/api/v1/trips/{trip_id}/versions` | 保存、列出行程版本 |
| POST | `/api/v1/trips/{trip_id}/versions/{version_id}/restore` | 恢复版本 |
| POST | `/api/v1/jobs` | 创建异步任务并投递 ARQ |
| GET | `/api/v1/jobs/{job_id}` | 查询任务状态与进度 |
| POST | `/api/v1/jobs/{job_id}/cancel` | 取消排队中或执行中的任务 |

## LangGraph 规划与 SSE

- `POST /api/v1/trips/preflight`：在创建 Trip 前逐轮理解、校验并确认自然语言需求。
  请求可携带 `answers`、`previous_extracted`、`semantic_checked`、`confirmed`；
  响应返回 `ready`、`confirmation_required`、`semantic_checked`、逐题 `issues`、
  最终 `summary` 和结构化 `extracted`。该接口不创建行程、不投递 Job。
- `POST /api/v1/trips/{trip_id}/planning/start`：投递 Planning Job，返回 202。
- `GET /api/v1/trips/{trip_id}/planning`：需求、默认值、追问、校验和 Markdown 快照。
- `POST /api/v1/trips/{trip_id}/planning/clarifications`：补充答案并恢复规划。
- `GET /api/v1/trips/{trip_id}/planning/events`：跨进程 SSE 进度。
- `GET /api/v1/trips/{trip_id}/roadbook`：`text/markdown` 路书。
- `GET /api/v1/trips/{trip_id}/roadbook.pdf`：冻结快照 PDF 导出。
- `GET /api/v1/trips/{trip_id}/roadbook.pptx`：冻结快照 PPTX 导出。
- `GET /api/v1/trips/{trip_id}/roadbook.png`：冻结快照长图 PNG 导出。
- `GET /api/v1/trips/{trip_id}/risks`：按阶段返回风险等级、标签、警告和汇总。
- `GET /api/v1/trips/{trip_id}/services`：返回七类沿途 POI 清单与已选停靠。

SSE 使用命名事件，每条事件包含单调递增的 `id:`。客户端断线重连时传
`Last-Event-ID`，服务只续发其后的保留事件。真实 Planning Worker 在每个 LangGraph
节点写入可展示进度；事件不包含模型私有推理。

正常首页流程必须先通过 `preflight`。日期倒置、过去返回时间、跨海方式未指定及
明显不可能的移动时间窗口会在输入框上方逐题询问；Requirement Guard 还会检查
规则库之外的语义矛盾。后续轮次复用 `previous_extracted`，已回答问题不会因换一种
说法重复出现。所有问题解决后接口返回 `confirmation_required=true`，只有用户携带
`confirmed=true` 再次请求并获得 `ready=true` 才能创建 Trip。图执行结束
先发布 96% 的保存核对状态，Trip 和规划快照持久化成功后才发布 100% 终态。

## Skill Registry

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/skills/health` | Adapter 与 Redis/内存缓存健康状态 |
| GET | `/api/v1/skills/calls` | 最近 SkillCall 审计记录 |
| GET | `/api/v1/skills/metrics` | Skill 调用量、成功率、缓存命中和延迟统计 |
| GET | `/api/v1/ops/metrics` | 服务请求与 Skill 聚合监控指标 |
| POST | `/api/v1/skills/amap/geocode` | 高德地理编码 |
| POST | `/api/v1/skills/amap/driving` | 高德驾车路线 |
| POST | `/api/v1/skills/amap/route` | 驾车/骑行/步行/公交统一路线编排 |
| POST | `/api/v1/skills/amap/poi` | 高德 POI 检索 |
| POST | `/api/v1/skills/weather/forecast` | Open-Meteo 坐标天气预报 |
| POST | `/api/v1/skills/carinfo/search` | 固定车型样本与能耗参数 |
| POST | `/api/v1/skills/flyai/poi` | FlyAI / 飞猪景点与门票搜索 |
| POST | `/api/v1/skills/opentripmap/nearby` | OpenTripMap 境外周边景点 |

所有 Adapter 返回统一 `SkillResult`。缓存键包含 Adapter 版本与规范化参数；Redis
不可用时自动降级到进程内缓存。只有网络传输错误和超时会重试，参数错误与无结果
不会重试。

阶段 E 的沿途服务按驾车道路中部搜索服务区、充电、加油、停车、餐饮、医院和
公共厕所。第三方查询失败不会伪造 POI；仅在往返阶段中心相距不超过约 60 km 时
复用同一走廊已成功返回的真实 POI，并写入估算 Warning。

`amap.route` 尊重 `preferred_mode`。驾车无结果时按距离和同城条件尝试骑行、步行或
公共交通；全部真实方式失败时返回 `ROUTE_UNAVAILABLE`，不把直线伪装成道路点列。
详细契约见 [`routing-fallback-design.md`](routing-fallback-design.md)。

未配置第三方凭据时返回合法的失败 `SkillResult`，例如
`error_code=SKILL_NOT_CONFIGURED`，不会抛出不透明异常。

候选修改遵循 `PlanPatch` 两阶段契约：`preview` 只保存原值、建议值、影响范围、
时间/费用变化和是否需要重规划；正式 Trip 保持不变。只有 `apply` 会写入，
`reject` 仅把补丁标记为拒绝。已处理的补丁不可重复应用。
地图选点请求携带 `day_id`、`category`、名称、地址和经纬度；服务端将点位写入候选池并
生成 `add` 类型预览。地图点击本身不直接修改行程。Editing Agent 会优先识别消息中明确
提到的候选地点名称；选择既有活动后可用“删除这个”生成删除预览。
`CandidatePatchRequest.duration_minutes` 可选，用于 Agent 根据“短停/多逛/深度”等语义
调整新增景点或餐饮的停留长度；未提供时使用分类默认值。
替换地点会重算匹配的相邻路线；删除活动会提前后续安排。应用后重新检查活动冲突和
路线闭环，失败时不持久化。成功应用保存恢复快照，允许一次明确回滚。

## 数据与运行约束

- PostgreSQL 结构由 Alembic 管理；本地测试保留 SQLite 兼容。
- Job Worker 使用 Redis + ARQ，Web API 与 Worker 共享任务状态。
- 文件内容存储在 `UPLOAD_DIR`，数据库只保存安全文件名和元数据。
- JSON Schema 位于 `shared/schemas/`，由 `backend/scripts/export_schemas.py` 生成。
- POI 候选会在规划阶段由 `baidu.baike` 做最佳努力的详情补充：返回 `description`、`image_url`、`detail_url` 和 `source_records` 中的百科来源。该查询有短超时，失败只降级为已有地图或旅行平台来源。
- 补丁应用接口在事务提交后重新读取 canonical Trip；前端也会重新 hydrate，保证连续删除、加入、替换操作不会恢复旧活动。
- `GET /api/v1/trips/{trip_id}/roadbook.html`：统一 HTML 报告模板，包含路线图、景点/餐饮/住宿图片卡片和逐日阶段详情。
- `MovementStage` 与 `RouteSegment` 支持可选 `elevation_gain_m`；步行/骑行阶段可展示路线总爬升。Docker 默认开启 `ENABLE_ROUTE_ELEVATION`，高程服务失败时降级为“高程数据暂不可用”。
- 配置 Ollama Agent 后，`POST /api/v1/trips/preflight` 会始终经过 Requirement Agent，由 Agent 语义判断同行人数；确定性解析器只负责离线兜底和明确数字，不对“情侣”等关系词做本地人数推断。

### 近期字段与展示约定

- `TripRequest` / `PreflightResponse.summary` 支持可选 `departure_time`、`return_time`（`HH:MM`）。需求中明确的点号/斜杠日期优先于模型输出；“中午/下午”等自然语言时间会标准化后传给阶段编排。
- 规划编排会优先填充每日 2–4 个景点（受候选数据和时间窗口约束），并为每天生成三餐与住宿活动。餐饮活动的 `user_note` 使用早餐/午餐/晚餐标记，前端按全天时间线展示。
- 详情页的最终数据采用渐进 hydration：后台持久化阶段和活动后，客户端按阶段、活动顺序逐项加入视图；这只是展示节奏，不改变服务端 canonical Trip。
- 详情页在 `plan_updated` SSE 后会重新读取 canonical Trip；客户端轮询带并发保护，避免重复请求导致阶段/活动显示停滞。
- `GET /api/v1/trips` 返回已持久化的历史规划，首页历史规划下拉可直接恢复进行中或已完成行程。
- `POST /api/v1/skills/weather/forecast` 接受浏览器定位或武汉回退坐标，首页展示当前温度、天气和位置来源。
- LangGraph 的 `review_daily_schedule` 节点是首轮旅游编排后的第二次日程检查；它核验每日时间覆盖和三餐住宿，并在没有可安全插入候选时写入可调整的 `rest` 活动。
