# 领域模型

权威实现位于 `backend/app/domain/models.py`，全部为 Pydantic 模型。跨进程与前端共享的 JSON Schema 位于 `shared/schemas/`，由 `backend/scripts/export_schemas.py` 从同一份 Pydantic 模型生成（见下文「Schema 契约」）。

```text
Trip
├─ request: TripRequest / preferences / source_records
├─ days: DayPlan[]
│  ├─ items[]（展示顺序，引用 stage 或 activity）
│  ├─ stages: MovementStage[]  （只表示移动）
│  └─ activities: Activity[]   （景点、餐饮、住宿、补能等）
├─ warnings[] / sources[]
└─ status: TripStatus
```

## 实体职责

- **Trip**：一次完整行程。保存用户提供的原始自然语言 `request.raw_text`、结构化 `request`（`TripRequest`）、`start_date`、`end_date`、起终点、选中的车 `selected_vehicle_id`、`status`（`TripStatus`）、canonical 日程 `days` 以及汇总层面的 `warnings` 和 `sources`。
- **TripRequest**：从自然语言提取的结构化需求。含 `origin`/`destination`、`destination_names`（完整目的地序列）、`destination_scope`（poi/city/province/region/multi_destination/unknown）、`travel_intents`、`start_date`/`end_date`/`departure_time`/`return_time`/`return_before`、`travelers`、`preferences`、`transport_modes`（语义交通偏好，路线执行理解城际班次模式 train/flight/ferry 与高德本地模式）、`special_events`、`must_visit`、`max_days`、`budget`、`max_continuous_drive_minutes`、`max_daily_drive_minutes` 与 `defaults_applied`。`max_continuous_drive_minutes` 控制连续驾驶休息上限；`max_daily_drive_minutes` 控制长途驾驶每日上限，跨日时自动插入次日 08:00 出发的过夜住宿。`stay_only_at_destination` 用于阻止一趟语义编辑（如「三天只在九宫山」）复用其它城市或邻近区域的陈旧候选。
- **DayPlan**：一个自然日。含 `day_index`、`date`、`title`、`items`（`DayItemRef`，仅引用 stage 或 activity 的 id 并按展示顺序排布）、`stages`、`activities`、当天汇总 `total_distance_km`、`total_drive_minutes`、`total_walk_minutes`、`estimated_cost` 与 `weather_summary`。
- **MovementStage**：两个地点之间的真实移动，只表示移动，景点游览本身不放进 Stage。含起终点 `PlaceRef`、`waypoints`、交通方式 `mode`（driving/transit/walking/riding/taxi/flight/train/ferry）、`transit_type`、`route_segments`、`planned_start/end`、`distance_km`、`duration_minutes`、`elevation_gain_m`、`traffic_summary`、`weather_summary`、`toll_fee`、`energy_estimate`、`weather_samples`、班次字段（`service_number`、`service_operator`、`departure_terminal`、`arrival_terminal`、`service_detail_url`）、`risk_level`/`risk_tags`、`status`、`warnings` 与 `source_records`。
- **Activity**：景点、餐饮、酒店、休息、充电、加油、停车、服务区等非移动安排。`type` 为 attraction/meal/hotel/rest/charging/fueling/parking/service；带 `planned_start/end`、`duration_minutes`、`place`、`required`、`backup`、`user_note`、`description`、`image_url`、`detail_url`、`opening_hours`、`ticket_or_price`、`parking_or_price`、预约状态、风险与 `source_records`。长途火车、飞机、轮渡或服务区停靠阶段内的餐食可标记 `in_transit=true`，表示它属于途中用餐，不是一条会与移动相冲突的第二条路线。
- **RouteSegment**：Stage 的路线细节。保存 `coordinates` 点列、`distance_km`、`duration_minutes`、`road_name`、`toll`、`estimated` 以及可选的 `elevation_gain_m`。
- **PlanPatch**：编辑预览。记录 `target_type`/`target_id`/`operation`、`original_value`、`proposed_value`、`impact_scope`、`time_delta_minutes`、`cost_delta`、`risk_delta`、`requires_replan` 与 `status`（preview/accepted/rejected/applied/rolled_back）。确认前不写入 Trip，前端先相对比影响确认后再应用。
- **SourceRecord**：每条外部事实的来源。含 `provider`、`title`、`url`、`retrieved_at` 与可选 `license`。
- **VerificationIssue**：每日复核产生的检查结果。含 `code`、`severity`（info/warning/error/blocker）、`title`、`description`、`affected_ids`、`source`、`user_confirmation_required`、`auto_fix_available`。
- **VehicleProfile**：车型档案。含 `brand`/`series`/`model`/`year`、`power_type`（electric/hybrid/fuel）、`rated_range_km`、`current_energy_percent`、`battery_kwh`、`consumption_per_100km`、`max_charge_kw`、`height_m`/`width_m`、`seats`、`plate_region`、`has_etc`、`mountain_ready`、`unpaved_ready` 与 `safe_energy_reserve_percent`。
- **SSEEvent**：规划过程的渐进式推送事件。事件类型包括 `planning_started`、`node_started`、`tool_started`、`tool_completed`、`node_completed`、`clarification_required`、`progress`、`warning`、`plan_updated`、`patch_preview_ready`、`planning_paused`、`planning_resumed`、`planning_completed`、`planning_failed`。事件带 `trip_id`、`node`、`tool`、`label`、`progress`（0-100）与可选 `batch_id`。

## 存储

Trip 作为一张名为 `trips` 的 SQLAlchemy 表（`backend/app/db.py` 中的 `TripRow`）存储。该表只保留少数标量列（`id`、`title`、`status`、两个时间戳），整个领域对象以 JSON 文档形式序列化进 `document` 列（`trip.model_dump_json()`），读取时再 `Trip.model_validate_json()` 还原。

除 `document` 外，`TripRow` 还保存运行期附产物：`state_json`（规划状态快照）、`plan_markdown`（生成的 Markdown 计划）与 `messages_json`（规划过程消息），供快照与导出复用。配套表包括 `trip_versions`（行程版本，各存一份完整 `trip_document`）、`vehicles`、`files`、`jobs` 与 `skill_calls`（技能调用审计）。

## 约束

1. 纯日期使用 `YYYY-MM-DD`，带时间使用带时区的 ISO 8601；金额使用默认 CNY 的 `MoneyRange`（`minimum`/`maximum`/`estimated`，且 `maximum >= minimum`）。
2. 估算数据必须显式标记 `estimated=true`；没有路线 geometry 不得把起终点直线当道路处理。
3. 连续活动之间的位移必须有 `MovementStage`；驾驶、公共交通、骑行、步行、出租车、火车、飞机、轮渡统一复用该模型。
4. 每日复核检查时间冲突、可用时间窗、餐饮/住宿覆盖、天气/季节适配与闭环约束，产出 `VerificationIssue`。
5. 外部名称可做中文展示，但保留原文与来源，避免同名地点误合并。
6. 不要把 provider 原始响应直接暴露给客户端，经领域模型规整后再返回。

## Schema 契约

`shared/schemas/*.schema.json` 不是手工维护的副本，而是从 `backend/app/domain/models.py` 的 Pydantic 模型用 `model_json_schema()` 生成的 JSON Schema，前端类型与跨进程契约都以此对齐。

```powershell
$env:PYTHONPATH = 'backend'
python backend/scripts/export_schemas.py
```

脚本输出 18 份 Schema 到 `shared/schemas/`：Trip、TripRequest、DayPlan、MovementStage、Activity、PlanPatch、PlanningSnapshot、VerificationIssue、SourceRecord、PlaceRef、VehicleProfile、ClarificationAnswer、FileRecord、JobCreate、JobRecord、SkillResult、SkillCallRecord、SSEEvent。模型变更后须重跑脚本，并同步 API 测试与前端类型。
