# RoadMan API 契约 v0.1

开发地址：`http://localhost:8000`。交互式文档：`/docs`。

错误统一为：

```json
{
  "error": {
    "code": "TRIP_NOT_FOUND",
    "message": "行程不存在",
    "details": {"trip_id": "trip_x"}
  }
}
```

## Trip

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/trips` | 创建空白/收集中 Trip |
| GET | `/api/v1/trips` | 查询 Trip 列表 |
| GET | `/api/v1/trips/{trip_id}` | 查询 Trip |
| PATCH | `/api/v1/trips/{trip_id}` | 更新标题、状态或所选车辆 |
| DELETE | `/api/v1/trips/{trip_id}` | 删除 Trip |
| GET | `/api/v1/trips/mock/wuhan-lushan` | 固定验收 Mock |

## 规划 Mock 与 SSE

- `POST /api/v1/trips/{trip_id}/planning/start`
- `GET /api/v1/trips/{trip_id}/planning/events`

SSE 使用命名事件，`data` 是 `SSEEvent` JSON。当前版本依次发出启动、
需求抽取、工具调用、阶段构建和完成事件；不包含模型私有推理。

## Skill Registry

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/skills/health` | Adapter 健康状态 |
| POST | `/api/v1/skills/amap/geocode` | 高德地理编码 |
| POST | `/api/v1/skills/amap/driving` | 高德驾车路线 |

未配置 `AMAP_WEBSERVICE_KEY` 时接口返回合法 `SkillResult`，
`success=false` 且 `error_code=SKILL_NOT_CONFIGURED`，不会泄露密钥。

驾车失败后切换骑行、步行或同城公交的后端统一编排接口仍处于设计阶段，详见
[`routing-fallback-design.md`](routing-fallback-design.md)，当前不能视为已上线 API。

`MovementStage.mode` 支持驾车、步行、骑行与公共交通等移动方式；当
`mode=transit` 时可通过 `transit_type` 标记 `bus`、`subway` 或 `shuttle`。
景点、餐厅、酒店等活动节点之间发生位移时也必须返回独立 `MovementStage`。
