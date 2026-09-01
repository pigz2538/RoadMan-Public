# API 契约

## 交通与 POI 事实接口（新增）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/v1/skills/amap/poi-detail` | 按 POI ID 查询营业时间、价格、停车、电话、官网和图片 |
| POST | `/api/v1/skills/flyai/train` | 返回具体车次号、站点、时间、座席、价格和详情链接 |
| POST | `/api/v1/skills/transport/train-fallback` | 主车次服务失败时查询公开车次备选，返回同一 `data.items` 合同 |
| POST | `/api/v1/skills/transport/flight-fallback` | 配置备用密钥后查询公开航班备选，未配置时明确返回不可用 |
| POST | `/api/v1/skills/travel/oil-price` | 可选查询省份今日油价，不参与路线可行性判定 |
| POST | `/api/v1/skills/flyai/flight` | 返回具体航班号、机场、时间、座席、价格和详情链接 |
| POST | `/api/v1/skills/flyai/ferry` | 返回轮船语义候选，时间和班次明确标记为估算 |

行程阶段将上述结果映射到 `service_number`、`service_operator`、`departure_terminal`、`arrival_terminal`、`service_departure_at`、`service_arrival_at`、`service_seat_class`、`service_price`、`service_status`；公共交通映射到 `transit_legs`，每段包含线路名、上下车站、站数、耗时和票价。景点/餐饮/住宿卡片同时保留 `opening_hours`、`ticket_or_price`、`parking_or_price`、`reservation_status`、`information_status` 和来源核验时间；缺失字段明确返回未知状态。

后端 ASGI 入口为 `http://localhost:8000`（本地 uvicorn），Docker 统一 Web 入口为 `http://localhost:8080`，交互式 OpenAPI 文档在 `/docs`。前端在运行时仅与同一宿主下的 API 通信，生产走 Nginx 反代。

## 通用约定

- JSON 字段使用 `snake_case`；日期为 `YYYY-MM-DD`，带时间的值使用 ISO 8601 并保留时区。
- 每个请求都会携带 `X-Request-ID` 和 `X-Trace-ID` 响应头；客户端可在请求头中传入同名值以复用追踪 ID，未传时由中间件生成。
- 错误统一形如：

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

  `code` 为机器可读的错误码，`details` 携带上下文，`request_id` 关联日志。请求参数校验失败返回 `REQUEST_VALIDATION_ERROR`（422），未处理异常返回 `INTERNAL_SERVER_ERROR`（500）。

- 开启 `ENABLE_RATE_LIMIT` 时按客户端 IP 限流（默认 600 次/分钟），超限返回 `429 RATE_LIMITED` 并带 `Retry-After: 60`；`/health` 不计入限流。
- provider 未配置或暂时不可用时，Skill 端点返回结构化 `SkillResult`，不要把它包装成实时数据。`SkillResult` 结构见 [domain-model.md](domain-model.md)。

## 行程与预检

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/trips` | 创建行程，返回 `201` 与 `Trip` |
| `GET` | `/api/v1/trips` | 行程列表 |
| `POST` | `/api/v1/trips/preflight` | 需求语义提取、日期/地点校验、追问与确认 |
| `GET` | `/api/v1/trips/mock/wuhan-lushan` | 离线前端验收样例行程 |
| `GET` | `/api/v1/trips/{trip_id}` | 读取单个行程 |
| `PATCH` | `/api/v1/trips/{trip_id}` | 更新标题、状态或所选车辆 |
| `DELETE` | `/api/v1/trips/{trip_id}` | 删除行程，返回 `204` |

`preflight` 请求核心字段：

```json
{
  "raw_text": "周五下午从武汉出发，周日晚上返回，情侣出游",
  "answers": {},
  "previous_extracted": {},
  "semantic_checked": false,
  "confirmed": false
}
```

需求地点字段由云端需求理解智能体产生：响应 `extracted.destination_names` 保存完整目的地顺序，
`extracted.destination_scope` 标记 `poi`、`city`、`province`、`region` 或
`multi_destination`。行政区和多目的地会先进入目的地研究/策划智能体，再进入路线规划；模型返回数组字符串、
餐馆/校园替代行政区等非法形态时，接口会请求智能体修复或暂停，不使用地点关键词兜底。

响应给出 `ready`、`confirmation_required`、`semantic_checked`、`issues`、`extracted`、`summary` 和 `special_event_research`。确认前不创建 Trip、不投递规划任务。

日期与返程时间校验遵循同一套规则：出发必须早于返回；返程目标时间允许 15 分钟以内的静默误差，超出但不超过半天时返回可调整的 `RETURN_WINDOW_FLEXIBLE` 警告，超过半天返回阻断问题 `RETURN_DEADLINE_UNACHIEVABLE`。警告不阻止确认，阻断问题需要修改日期、时间窗口或交通方式。

## 规划、SSE 与导出

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/trips/{trip_id}/planning/start` | 投递规划任务，返回 `202` |
| `GET` | `/api/v1/trips/{trip_id}/planning` | 读取状态、进度、追问、校验和 Markdown 快照 |
| `POST` | `/api/v1/trips/{trip_id}/planning/clarifications` | 回答规划中的追问并重新排队 |
| `GET` | `/api/v1/trips/{trip_id}/planning/events` | SSE 进度流，支持 `Last-Event-ID` 与 `after` |
| `GET` | `/api/v1/trips/{trip_id}/roadbook` | Markdown，仅 completed |
| `GET` | `/api/v1/trips/{trip_id}/roadbook.html` | HTML 报告 |
| `GET` | `/api/v1/trips/{trip_id}/roadbook.pdf` | PDF 报告 |
| `GET` | `/api/v1/trips/{trip_id}/roadbook.pptx` | PPTX 报告 |
| `GET` | `/api/v1/trips/{trip_id}/roadbook.png` | 长图 PNG |

所有导出只在行程状态为 `completed` 时开放，否则返回 `409 PLANNING_NOT_COMPLETED`；快照尚未生成时返回 `409 ROADBOOK_NOT_READY`。

规划图在最终校验发现可修复问题时，会自动执行最多 3 轮 `verify_plan ⇄ repair_plan` 闭环并逐轮通过 SSE 展示重排进度。只有验证通过才会发送 100%；`verification_result.auto_repair_exhausted=true` 表示三轮自动修复仍未解决，此时才向用户展示人工调整入口与可读的冲突摘要。

### SSE 事件协议

SSE 通道在 `/api/v1/trips/{trip_id}/planning/events`，`media_type=text/event-stream`。帧形如：

```
id: 42
event: node_started
data: {"event":"node_started","trip_id":"trip_x","node":"build_stages","tool":null,"label":"正在拆分天和阶段","progress":84,"timestamp":"..."}
```

`SSEEvent` 的 `event` 字段取值：

`planning_started`、`node_started`、`tool_started`、`tool_completed`、`node_completed`、`clarification_required`、`progress`、`warning`、`plan_updated`、`patch_preview_ready`、`planning_paused`、`planning_resumed`、`planning_completed`、`planning_failed`。

通道只传递可展示的阶段名 `node`、工具名 `tool`、文案 `label` 和进度 `progress`（0-100），不包含模型私有推理或 provider key。以 `planning_completed`、`planning_failed`、`planning_paused`、`clarification_required` 结尾时流终止。重连时可用 `Last-Event-ID` 或 `after` 事件序号续读；没有新事件时每 0.45 秒发送一行 `: keep-alive`。

## 编辑、候选与版本

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/trips/{trip_id}/recommendations?category=attractions\|hotels\|meals` | 当前阶段景点/住宿/餐饮候选 |
| `POST` | `/api/v1/trips/{trip_id}/editing/interpret` | 编辑 Agent 将自然语言转成候选操作 |
| `POST` | `/api/v1/trips/{trip_id}/editing/confirm-replan` | 确认需要全量重排的会话编辑 |
| `POST` | `/api/v1/trips/{trip_id}/patches/preview` | 加入/替换候选，生成 PlanPatch |
| `POST` | `/api/v1/trips/{trip_id}/patches/preview-map-point` | 地图点选生成候选补丁 |
| `POST` | `/api/v1/trips/{trip_id}/patches/preview-delete` | 预览删除活动 |
| `GET` | `/api/v1/trips/{trip_id}/patches/{patch_id}` | 查看补丁 |
| `POST` | `/api/v1/trips/{trip_id}/patches/{patch_id}/apply` | 应用并重算受影响阶段 |
| `POST` | `/api/v1/trips/{trip_id}/patches/{patch_id}/reject` | 放弃补丁 |
| `POST` | `/api/v1/trips/{trip_id}/patches/{patch_id}/rollback` | 回滚已应用补丁 |
| `POST` | `/api/v1/trips/{trip_id}/versions` | 保存版本 |
| `GET` | `/api/v1/trips/{trip_id}/versions` | 列出版本 |
| `POST` | `/api/v1/trips/{trip_id}/versions/{version_id}/restore` | 恢复版本 |
| `GET` | `/api/v1/trips/{trip_id}/risks` | 风险汇总（高风险/中风险阶段与警告） |
| `GET` | `/api/v1/trips/{trip_id}/services` | 沿途服务 POI 与已选停留 |

`preview` 不修改 canonical Trip；`apply` 时才写回并触发受影响阶段的重算，`rollback` 通过补丁前备份恢复整段行程。应用或回滚后前端应重新加载 Trip，避免沿用旧的活动快照。

## Skill、任务、车辆与附件

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/skills/health` | Skill Registry 与缓存健康 |
| `GET` | `/api/v1/skills/calls?limit=N` | Skill 调用审计 |
| `GET` | `/api/v1/skills/metrics` | Skill 成功率/缓存/延迟汇总 |
| `POST` | `/api/v1/skills/amap/geocode` | 高德地理编码 |
| `POST` | `/api/v1/skills/amap/driving` | 高德驾车路线 |
| `POST` | `/api/v1/skills/amap/route` | 驾车/骑行/步行/公共交通统一路线 |
| `POST` | `/api/v1/skills/amap/poi` | 高德 POI |
| `POST` | `/api/v1/skills/weather/forecast` | Open-Meteo 天气 |
| `POST` | `/api/v1/skills/carinfo/search` | 车型目录搜索；有 `query` 时走 `carinfo.catalog` |
| `POST` | `/api/v1/skills/flyai/poi` | FlyAI POI |
| `POST` | `/api/v1/skills/flyai/hotel` | FlyAI 酒店 |
| `POST` | `/api/v1/skills/flyai/keyword-search` | FlyAI 关键词搜索 |
| `POST` | `/api/v1/skills/flyai/ai-search` | FlyAI 语义搜索 |
| `POST` | `/api/v1/skills/opentripmap/nearby` | OpenTripMap 周边景点 |
| `POST` | `/api/v1/vehicles` | 创建车型，返回 `201` |
| `GET` | `/api/v1/vehicles` | 车型列表 |
| `GET/PATCH/DELETE` | `/api/v1/vehicles/{vehicle_id}` | 单车型读/改/删 |
| `POST` | `/api/v1/files` | 上传并校验附件（图片/PDF/DOCX/Markdown/XLSX） |
| `GET` | `/api/v1/files/{file_id}` | 附件元数据 |
| `GET` | `/api/v1/files/{file_id}/content` | 附件内容下载 |
| `POST` | `/api/v1/files/{file_id}/extract` | 提取附件中的待确认需求 |
| `POST` | `/api/v1/files/{file_id}/confirm` | 确认提取结果并写入需求 |
| `POST` | `/api/v1/jobs` | 创建异步任务，返回 `202` |
| `GET` | `/api/v1/jobs/{job_id}` | 查询任务 |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | 取消任务 |
| `GET` | `/api/v1/ops/metrics` | 服务请求指标与 Skill 汇总 |

车型目录的搜索与表单接入见 [carinfo-catalog.md](carinfo-catalog.md)。当主目录未覆盖具体车系时，响应 `data.fallback_used=true`，并在条目中返回 `catalog_source`、`source_url` 和 `detail_source_url`，前端应显示来源并提示用户核对年款配置。

## 路线响应最低要求

`amap.route` 的 `data` 至少包含 `requested_mode`、`selected_mode`、`fallback_used`、`distance_km`、`duration_minutes`、`geometry`、`steps`、`transfers` 和来源信息；降级时还带 `fallback_reason` 与 `attempted_modes`。没有真实 geometry 时 `success=false`、`error_code=ROUTE_UNAVAILABLE`，前端只能绘制灰色虚线提示，不做导航计算。完整规则见 [routing-fallback-design.md](routing-fallback-design.md)。
