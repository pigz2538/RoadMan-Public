# 真实路线与交通降级

## 原则

RoadMan 只把第三方返回的真实道路/步行/骑行/公交 geometry 当作可执行路线。高德没有返回点列时，不把起终点直线冒充道路；前端可以用灰色虚线提示“路线不可用”，但必须显式标记。

## 选择顺序

Stage 首选永远尊重用户指定的交通方式：

1. `preferred_mode`（通常是 `driving`）。
2. 请求明确允许的 fallback：`riding`、`walking`、`transit`。
3. 同城且距离合理时才尝试公共交通；跨城不把市内公交当成高铁/飞机替代。
4. 全部失败返回 `ROUTE_UNAVAILABLE`，由上层决定是否改成火车、飞机、轮船或询问用户。

前端颜色只表达当前路线状态：驾车蓝色、公共交通绿色、骑行/步行黄色；当前阶段高亮，其他可用路线灰显。

## 统一接口

`POST /api/v1/skills/amap/route`：

```json
{
  "origin": {"longitude": 115.978, "latitude": 29.573, "city": "九江"},
  "destination": {"longitude": 115.967, "latitude": 29.572, "city": "九江"},
  "preferred_mode": "driving",
  "allowed_fallback_modes": ["riding", "walking", "transit"],
  "waypoints": [],
  "departure_time": "2026-08-08T12:10:00+08:00"
}
```

成功响应至少含 `requested_mode`、`selected_mode`、`fallback_used`、`distance_km`、`duration_minutes`、`geometry`、`steps`、`transfers`、`source`。失败响应含尝试过的方式和可解释 `error_code`。

## 缓存、重试与地图行为

- 缓存键必须包含起终点、途经点、方式、城市和出发时间窗口。
- 参数错误/无结果不重试；网络超时最多短重试一次，并受总规划超时限制。
- geometry 由地图 SDK overlay 绘制，跟随平移/缩放；不要用固定在容器上的 SVG 或 canvas 盖层。
- 真实路线缺失时，灰色虚线只用于视觉提示，不参与距离、时长或导航计算。
