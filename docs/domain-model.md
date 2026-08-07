# 领域模型

权威实现位于 `backend/app/domain/models.py`；跨进程/导出 Schema 位于 `shared/schemas/`。

```text
Trip
├─ TripRequest / preferences / source_records
├─ DayPlan[]
│  ├─ MovementStage[]  （只表示移动）
│  └─ Activity[]       （景点、餐饮、住宿、补能、休息等）
├─ warnings[]
└─ versions / PlanningState
```

## 实体职责

- **Trip**：一次完整行程，保存原始自然语言、结构化需求、状态、日期、同行人数和 canonical 日程。
- **DayPlan**：一个自然日，含日期、阶段和活动时间轴；`items` 的顺序是展示顺序。
- **MovementStage**：两个地点之间的真实移动，保存起终点、交通方式、路线 geometry、距离、时长、天气、费用、风险和来源。景点游览本身不放在 Stage 中。
- **Activity**：景点、餐饮、酒店、充电、加油、停车、服务区、休息等非移动安排，带 `planned_start/end`、地点、来源和可选图片/详情链接。
- **RouteSegment**：Stage 的路线细节，可保存 steps、transfers、`elevation_gain_m` 和 provider 原始摘要。
- **PlanPatch**：编辑预览，包含原值、建议值、影响范围、时间/费用变化和是否需要重规划；确认前不写 Trip。
- **SourceRecord**：所有外部事实的来源、provider、URL、抓取时间和是否估算。
- **VehicleProfile**：车型、动力、续航、电量、座位、ETC/山路能力和安全余量。

## 约束

1. 日期使用 `YYYY-MM-DD`，带时间使用带时区的 ISO 8601；金额使用 CNY 的 `MoneyRange`。
2. 估算数据必须显式标记 `estimated=true`；没有路线 geometry 不得当作道路。
3. 连续活动之间的位移必须有 MovementStage；驾车、公共交通、骑行、步行和接驳统一使用该模型。
4. 每日复核必须检查时间冲突、可用时间窗、餐饮/住宿覆盖、天气/季节适配和闭环约束。
5. 外部名称可做中文展示，但保留原文和来源，避免同名地点误合并。

## Schema 更新

```powershell
$env:PYTHONPATH = 'backend'
python backend/scripts/export_schemas.py
```

模型变更必须同步示例、API 测试和前端类型；不要把 provider 原始响应直接暴露给客户端。
