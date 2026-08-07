# API 契约

开发地址：`http://localhost:8000`；Docker 统一入口：`http://localhost:8080`；交互式 OpenAPI：`/docs`。

## 通用约定

- JSON 字段使用 `snake_case`；日期为 `YYYY-MM-DD`，带时间的值使用 ISO 8601 和时区。
- 所有响应带 `X-Request-ID` 和 `X-Trace-ID`；客户端可传入同名请求头。
- 错误统一为：

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

- provider 未配置或暂时不可用返回结构化 `SkillResult`，不要把空结果伪装成实时数据。

## 行程与预检

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/trips` | 创建已确认的 Trip |
| `GET` | `/api/v1/trips` | 历史行程列表 |
| `GET/PATCH/DELETE` | `/api/v1/trips/{trip_id}` | 读取、更新、删除行程 |
| `POST` | `/api/v1/trips/preflight` | 需求语义提取、日期/地点校验、追问和确认 |
| `GET` | `/api/v1/trips/mock/wuhan-lushan` | 离线前端验收样例 |

`preflight` 请求核心字段：

```json
{
  "raw_text": "周五下午从武汉出发，周日晚上返回，情侣出游",
  "answers": {},
  "previous_extracted": {},
  "confirmed": false
}
```

响应会给出 `ready`、`confirmation_required`、`issues`、`extracted`、`summary`、`defaults_applied` 和特殊活动研究结果。确认前不创建 Trip、不投递规划任务。

日期与返程时间校验遵循同一套结构化规则：出发时刻必须早于抵达时刻；返程目标时间允许 `15` 分钟以内的静默误差，超过后但不超过半天返回可调整的 `RETURN_WINDOW_FLEXIBLE` 警告，超过半天才返回阻断问题 `RETURN_DEADLINE_UNACHIEVABLE`。警告不会阻止用户确认，阻断问题需要修改日期、时间窗口或交通方式。

## 规划、SSE 与导出

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/trips/{trip_id}/planning/start` | 创建 ARQ 规划任务，返回 `202` |
| `GET` | `/api/v1/trips/{trip_id}/planning` | 读取状态、进度、追问、校验和 Markdown 快照 |
| `POST` | `/api/v1/trips/{trip_id}/planning/clarifications` | 回答规划中的追问并重新排队 |
| `GET` | `/api/v1/trips/{trip_id}/planning/events` | SSE 进度流；支持 `Last-Event-ID`/`after` |
| `GET` | `/api/v1/trips/{trip_id}/roadbook` | Markdown（仅 completed） |
| `GET` | `/api/v1/trips/{trip_id}/roadbook.html` | HTML 报告 |
| `GET` | `/api/v1/trips/{trip_id}/roadbook.pdf` | PDF 报告 |
| `GET` | `/api/v1/trips/{trip_id}/roadbook.pptx` | PPTX 报告 |
| `GET` | `/api/v1/trips/{trip_id}/roadbook.png` | 长图 PNG |

所有导出在 Trip 不是 `completed` 时返回 `409 PLANNING_NOT_COMPLETED`。SSE 事件只传递可展示的阶段、进度和状态，不包含模型私有推理或 provider key。

最终验证发现可修复问题时，规划图会自动执行最多 4 轮“行程编排 → 每日复核 → 验证”闭环；SSE 会逐轮展示自动重排进度。`verification_result.auto_repair_exhausted=true` 仅表示自动修复轮次耗尽，前端此时才显示人工调整入口。

## 编辑、候选和版本

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/trips/{trip_id}/recommendations` | 当前阶段景点/住宿/餐饮候选 |
| `POST` | `/api/v1/trips/{trip_id}/editing/interpret` | Editing Agent 将自然语言转成候选操作 |
| `POST` | `/api/v1/trips/{trip_id}/patches/preview` | 加入/替换候选，生成 PlanPatch |
| `POST` | `/api/v1/trips/{trip_id}/patches/preview-map-point` | 地图点选生成候选补丁 |
| `POST` | `/api/v1/trips/{trip_id}/patches/preview-delete` | 预览删除活动 |
| `GET` | `/api/v1/trips/{trip_id}/patches/{patch_id}` | 查看补丁 |
| `POST` | `/api/v1/trips/{trip_id}/patches/{patch_id}/apply` | 确认应用并重算受影响阶段 |
| `POST` | `/api/v1/trips/{trip_id}/patches/{patch_id}/reject` | 放弃补丁 |
| `POST` | `/api/v1/trips/{trip_id}/patches/{patch_id}/rollback` | 回滚已应用补丁 |
| `POST/GET` | `/api/v1/trips/{trip_id}/versions` | 保存/列出版本 |
| `POST` | `/api/v1/trips/{trip_id}/versions/{version_id}/restore` | 恢复版本 |

`preview` 不修改 canonical Trip；`apply` 失败时事务回滚。前端应用后应重新 hydrate Trip，避免恢复旧活动。

## Skill、任务、车辆和附件

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/skills/health` | Registry 与缓存健康 |
| `GET` | `/api/v1/skills/calls` | Skill 调用审计 |
| `GET` | `/api/v1/skills/metrics` | Skill 成功率/缓存/延迟 |
| `POST` | `/api/v1/skills/amap/geocode` | 高德地理编码 |
| `POST` | `/api/v1/skills/amap/route` | 驾车/骑行/步行/公共交通统一路线 |
| `POST` | `/api/v1/skills/amap/poi` | 高德 POI |
| `POST` | `/api/v1/skills/weather/forecast` | Open-Meteo 天气 |
| `POST` | `/api/v1/skills/flyai/{poi,hotel,keyword-search,ai-search}` | FlyAI 旅游搜索 |
| `POST` | `/api/v1/skills/opentripmap/nearby` | OpenTripMap 周边景点 |
| `POST` | `/api/v1/skills/carinfo/search` | 车型目录搜索；有 `query` 时使用 `carinfo.catalog` |
| `POST/GET` | `/api/v1/vehicles` | 车型 CRUD |
| `GET/PATCH/DELETE` | `/api/v1/vehicles/{vehicle_id}` | 单车型 CRUD |
| `POST` | `/api/v1/files` | 上传并校验附件 |
| `GET` | `/api/v1/files/{file_id}[ /content]` | 元数据/内容 |
| `POST` | `/api/v1/files/{file_id}/extract` | 提取待确认需求 |
| `POST` | `/api/v1/files/{file_id}/confirm` | 确认写入需求 |
| `POST/GET` | `/api/v1/jobs`、`/api/v1/jobs/{job_id}` | 异步任务创建/查询 |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | 取消任务 |
| `GET` | `/api/v1/ops/metrics` | 服务请求指标 |

## 路线响应最低要求

`amap.route` 的 `data` 至少包含 `requested_mode`、`selected_mode`、`fallback_used`、`distance_km`、`duration_minutes`、`geometry`、`steps`、`transfers` 和来源信息。没有真实 geometry 时 `success=false`、`error_code=ROUTE_UNAVAILABLE`，前端只能绘制灰色提示线。
