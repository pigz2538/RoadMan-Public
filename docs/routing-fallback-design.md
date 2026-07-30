# RoadMan 多交通方式路线降级设计

状态：后端与前端均已实现
更新日期：2026-07-30

## 1. 目标

当高德驾车规划无法返回道路轨迹时，RoadMan 不应立即使用直线冒充道路，而应根据
距离、城市和道路可达性继续尝试骑行、步行或公共交通。所有真实交通方式都失败后，
后端返回明确的 `ROUTE_UNAVAILABLE`；前端才使用灰色虚线连接起终点作为视觉提示。

## 2. 交通方式决策

第一请求始终尊重行程 Stage 的首选方式。驾驶 Stage 的默认顺序：

1. `driving`
2. `riding`
3. `walking`
4. `transit`
5. `direct_hint`，仅为前端灰色虚线，不是可执行路线

后端根据直线距离调整候选顺序：

| 条件 | 驾车失败后的候选 |
|---|---|
| 0–3 km，同城 | 步行 → 骑行 → 公交 |
| 3–30 km，同城 | 骑行 → 公交 → 步行 |
| 30 km 以上，同城 | 公交；失败后要求用户调整节点 |
| 跨城市 | 不尝试市内公交；保留跨城驾车失败原因并要求调整 |
| 山区小路或机动车不可达 | 骑行 → 步行 |

高德公交换乘要求城市参数且不支持跨城市规划，因此只有起终点同城时才能进入
`transit` 分支。

## 3. 建议统一接口

已上线：

```text
POST /api/v1/skills/amap/route
```

请求：

```json
{
  "origin": {
    "longitude": 115.978,
    "latitude": 29.573,
    "city": "九江"
  },
  "destination": {
    "longitude": 115.967,
    "latitude": 29.572,
    "city": "九江"
  },
  "preferred_mode": "driving",
  "allowed_fallback_modes": ["riding", "walking", "transit"],
  "waypoints": [],
  "departure_time": "2026-08-01T12:10:00+08:00"
}
```

成功响应继续使用 `SkillResult`，其中 `data` 建议为：

```json
{
  "requested_mode": "driving",
  "selected_mode": "walking",
  "fallback_used": true,
  "fallback_reason": "AMAP_DRIVING_NO_RESULT",
  "distance_km": 1.8,
  "duration_minutes": 28,
  "geometry": [
    {"longitude": 115.978, "latitude": 29.573}
  ],
  "steps": [],
  "transfers": [],
  "fare_cny": null
}
```

全部失败：

```json
{
  "success": false,
  "provider": "高德地图",
  "warnings": ["驾车、骑行、步行和公共交通均未返回可执行路线"],
  "error_code": "ROUTE_UNAVAILABLE",
  "data": {
    "attempted_modes": ["driving", "riding", "walking", "transit"]
  }
}
```

失败结果不得返回起终点直线作为 `geometry`，防止调用方误认为它是真实道路。

## 4. Adapter 设计

Skill Registry 中由统一 Adapter 编排以下交通方式：

- `amap.driving`
- `amap.riding`
- `amap.walking`
- `amap.transit`
- `amap.route`，负责策略编排，不直接访问第三方 API

`amap.route` 负责：

1. 校验起终点、城市、途经点和允许方式。
2. 根据距离和同城条件生成候选方式。
3. 按顺序调用具体 Adapter。
4. 记录每次失败的可展示错误码，不暴露密钥或第三方原始敏感字段。
5. 选择第一条可执行路线，标记 `fallback_used` 和 `fallback_reason`。
6. 统一 geometry、距离、时长、费用、换乘和来源格式。

## 5. 缓存、超时与重试

- 缓存键必须包含：起点、终点、途经点、交通方式、策略、城市和出发时间桶。
- 驾车和骑行建议缓存 30 分钟。
- 步行静态路网可缓存 6 小时。
- 公交应包含出发时间，建议缓存 10 分钟。
- 单个方式超时建议 5–6 秒。
- 参数错误、无结果不重试；网络超时最多重试一次。
- 整个编排应有总超时，避免四种方式串行阻塞过久。

## 6. 前端表现

- 驾车：蓝色实线。
- 骑行：橙色实线。
- 步行：绿色实线或短虚线。
- 公交：紫色实线，并可在后续显示换乘站。
- 景点、餐厅、酒店等连续活动节点之间的移动也生成正式 `MovementStage` 和阶段卡，
  并使用 `transit_type` 区分公交、地铁或景区接驳；地图补线不能替代领域数据。
- 全部失败：灰色虚线直连，明确标注“路线不可用”，不提供导航含义。
- 地图上的所有真实路线必须使用高德返回的地理点列，随地图平移和缩放。

## 7. 当前实现状态

前端 `AmapRouteMap.vue` 已加载 Driving、Riding、Walking 和 Transfer 插件并支持
真实路线降级。后端 `/api/v1/skills/amap/route` 已实现同一策略，返回统一 geometry、
steps、transfers、来源和失败原因；日内 Stage 与 Activity 之间的移动可作为正式
`MovementStage` 消费该接口。
