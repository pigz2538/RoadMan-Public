# RoadMan 领域模型 v0.1

本版本冻结第一批实现任务需要的领域契约。权威实现位于
`backend/app/domain/models.py`，可分发契约位于 `shared/schemas/`。

## 核心层级

```text
Trip
└─ DayPlan
   ├─ MovementStage
   │  └─ RouteSegment
   └─ Activity
```

- `TripRequest` 保存原始自然语言、结构化需求以及可见默认值。
- `MovementStage` 只表达移动，游览、住宿、休息和补能均使用 `Activity`。
- `DayPlan.items` 是时间轴引用；`stages` 和 `activities` 保存实体。
- 所有外部数据通过 `SourceRecord` 追溯。
- 所有估算数据必须带 `estimated=true`。
- Agent 修改以 `PlanPatch(preview)` 表达，确认前不得改写正式 Trip。

## ID 规则

ID 使用带领域前缀的字符串，例如 `trip_`、`day_`、`stage_`、
`activity_`、`patch_`。ID 是不透明标识，不编码业务含义。

## 时间和金额

- API 时间使用带时区的 ISO 8601。
- 日期使用 `YYYY-MM-DD`。
- 金额使用 `MoneyRange`，默认币种为 CNY。
- Mock 中无法核实的路线、价格、耗能和开放时间均标为估算或待确认。

## Schema 更新

在项目根目录执行：

```powershell
$env:PYTHONPATH='backend'
python backend/scripts/export_schemas.py
```

Schema 变化必须同步更新示例并通过测试。
