# 真实路线与交通方式降级

## 原则

RoadMan 只把第三方返回的真实道路/步行/骑行/公交 geometry 当作可执行路线。provider 没有返回点列时，系统不把起终点直线冒充道路：后端直接标记路线不可用，前端用灰虚线段仅作视觉提示，且显式标记为降级，不参与距离、时长或导航计算。

路线查询的统一入口是 Skill Registry 的 `amap.route`（`backend/app/skills/amap.py` 的 `AmapRouteAdapter`），HTTP 端点 `POST /api/v1/skills/amap/route`。前端地图组件依据保存在 `MovementStage.route_segments[].coordinates` 里的真实点列绘制，而不是重新调用路径服务。

## 交通方式候选与降级

`AmapRouteAdapter` 按 `UnifiedRouteInput` 解析：`preferred_mode`（默认 driving）、`allowed_fallback_modes`（默认 riding/walking/transit）、`waypoints`、`strategy`。执行时先取起终点 Haversine 距离并判断是否同城，再构造候选方式：

- 首选永远是用户/规划指定的 `preferred_mode`。
- `preferred_mode=driving` 时按距离与是否同城决定 fallback 顺序：
  - 同城且距离 ≤ 3 km：walking、riding、transit；
  - 同城且距离 ≤ 30 km：riding、transit、walking；
  - 同城更远：仅 transit。
- 跨城不把市内公交当作高铁/飞机的替代；`transit` 仅在起终点同城且都有 city 时才尝试，否则返回 `AMAP_TRANSIT_REQUIRES_SAME_CITY`。

系统按候选顺序逐个调用高德对应端点（driving/walking 用 `/v3/direction/{mode}` 且 driving 带 `strategy` 与 `waypoints`，riding 用 `/v4/direction/bicycling`，transit 用 `/v3/direction/transit/integrated` 且需 city）。首个返回非空点列的候选即被选中，并记录 `requested_mode`、`selected_mode`、`fallback_used`、`fallback_reason`、`attempted_modes`、`distance_km`、`duration_minutes`、`geometry`、`steps`、`transfers`、`traffic_summary` 与来源。全部失败时返回结构化 `error_code=ROUTE_UNAVAILABLE`，附尝试过的方式与失败原因列表。

高德各端点解析出的 `geometry` 来自步骤 `polyline` 拼接（driving/riding/walking），transit 拼接步行与公交线路点列并提取 `transfers` 换乘。若高德返回点列为空，该候选视为失败，不会构造虚假直线。

## 自驾优先与市内接驳继承

在规划图 `build_base_route`（`backend/app/planning/graph.py`）中，`resolve_leg` 决定每一段腿（去程/返程）走何种方式：

- 无跨城班次显式信号时，`driving` 是跨城与市内的唯一机动交通方式；驾车查询失败也不会自动改成飞机、火车或市内公交。长途自驾只通过跨天拆段、休息、住宿与补能修复可执行性。
- 用户/需求 Agent 显式给出本地方式（transit/walking/riding）时，按其顺序再兜底 driving。
- 用户/需求 Agent 显式给出城际班次（train/flight/ferry）时，逐个查询所有允许方式，用 `_intercity_route_quality` 综合抵达时刻、到达期限与方式优先级选取最优班次，避免「首个提供者在凌晨抵达」却输给白天更舒适方案的情况。
- 返程腿按到到达期限（`arrival_deadline`，来自 `return_time`）选择班次。

城际班次之外，规划会为每个班次段补充同城的市内接驳，使终点到景点、酒店与出发站的短途移动仍走真实步行/公交/驾车路线。

## 火车/飞机/轮渡跨城阶段

跨城班次落为 `MovementStage`，`mode` 为 `train`/`flight`/`ferry`，可带 `service_number`、`service_operator`（车次/航班/船班）、`departure_terminal`、`arrival_terminal`、`service_detail_url` 等班次字段。主旅行信息服务无结果时，火车会顺序尝试公开车次备选，航班会在配置备用密钥后尝试公开航班服务；所有备选都保留来源、服务状态和失败原因，不把“高铁/航班”写成虚构编号。跨海语义（船/渡轮/跨海大桥/飞机等）在 `build_base_route` 归一化为 ferry/flight/bridge 决策，驱动是否启用跨城班次。

## 返程闭环与坐标容差

`verify_plan`（`backend/app/planning/graph.py`）调用 `_verify_route_closure` 做闭环校验：

- 相邻阶段之间，如果前一个阶段的 `destination` 与后一个阶段的 `origin` 不是同一地点，产生 blocker `ROUTE_DISCONTINUITY`（阶段不连续）。
- 如果首个阶段的 `origin` 与最后一个阶段的 `destination` 不是同一地点，产生 blocker `ROUTE_NOT_CLOSED`（行程终点未回到出发点）。

`_same_place` 判定两个地点是否同一：名称完全一致即视为同一；否则以 Haversine 距离计算，距离 ≤ 1 km 视为同一点。返程抵达死线（`return_time`）单独校验，超过 `RETURN_DEADLINE_SILENT_TOLERANCE_MINUTES=15` 分钟的迟到才报告问题。

## 自动复核修复

复核不是 LLM 协作协议，而是图内确定的 verify ⇄ repair 确定性循环（`backend/app/planning/graph.py`）。`verify_plan` 汇总三类校验：`verify_deep_drive_plan`（能耗、驾驶休息、天气、阶段计时、步行/骑行上限、三餐覆盖）、`verify_tourism_plan`（景点/住宿/餐饮与时间窗）与 `_verify_route_closure`（返程闭环）。只要存在 `blocker` 级别问题且自动修复次数 `repair_attempts` 未达到上限 `MAX_AUTO_REPAIR_ATTEMPTS=3`，就走 `repair_plan`：重跑 `schedule_tourism_activities`、`review_daily_schedule` 与时间重叠规整 `_repair_activity_stage_overlaps`，然后回到 `verify_plan` 再次校验。最多迭代 3 轮；3 轮后仍有 blocker 则记为 `auto_repair_exhausted`，交前端展示可执行约束。

景区型目的地（需求智能体返回 `destination_scope=poi`，或地理编码显示为兴趣点）采用 50 km 的目的地聚焦半径；明确说“几天都在这里/不去其他地方”时收紧到 35 km。城市、省域和多目的地请求不套用该边界，仍由目的地研究智能体覆盖全域知名地标。长途自驾没有真实服务区名称时，系统使用道路名与前/中/后段生成可解释候选，并明确提示出发前确认具体站点，不再使用编号占位。

每一日的确定性复核 `review_daily_schedule`（`backend/app/planning/tourism.py`）检查上午/下午/晚间安排、三餐与住宿覆盖、可用时间窗和天气/季节适配，产出核查意见并入 warnings。

## 前端绘制与降级

- `AmapRouteMap.vue` 用高德 JSAPI 2.0 加载真实地图。stage 优先使用 `route_segments[].coordinates` 持久化点列直接 overlay；仅当没有持久化点列时才按 mode 调度 `searchByMode`（Driving/Riding/Walking/Transfer），携带 `waypoints` 与同城 city。浏览器端凭据在构建时注入 `VITE_AMAP_JSAPI_KEY`/`VITE_AMAP_SECURITY_JS_CODE`，不读本地 key 文件。
- 前端自己的降级顺序为 preferredMode 后再遍历 driving/riding/walking/transit；全部失败则用起终点两点直接连一条 `direct` 灰虚线，并标 `fallback=true`。两点且 `estimated` 的持久化线段同样直接判为灰虚降级，不再让 JSAPI 重绘，避免意外生成另一条跨城虚线连接线。
- 无高德 JSAPI key 或加载失败时，`AmapRouteMap` 整体降级为 `MockRouteMap.vue`（静态 SVG 示意图，仅展示武汉—庐山示例路线与阶段高亮、风险点，不表示真实路况），并显示「高德地图不可用 · 已切换 Mock 地图」徽标。
- Intracity 行程由「行程连接线」把同日 stage 与 activity 按时间顺序连成可视化轨迹；阶段与活动坐标缺失时先从同行 route_segments 端点到名字匹配回填，不臆造几何。
- 颜色只表达路线状态：当前阶段高亮，当日其他阶段灰显；风险阶段带风险标记（见 `routes.css` 中 active-route/day-route/risk-route 语义）。

## 缓存

`AmapRouteAdapter.cache_ttl_seconds = 1800`，按 payload 归一化后的 `UnifiedRouteInput` 命中缓存；缓存键覆盖起终点、途经点、方式与城市。参数错误/无结果显示不重试；网络异常由 `base.py` 的 `max_retries` 控制（路由适配器为 1 次短重试）。

## Recent itinerary quality safeguards

- Stage endpoints within 3 km are treated as one connected place. The browser
  map draws that short continuity connector too, so small geocoding drift does
  not leave a visual gap between cards.
- If an online route has no geometry, the map keeps an explicitly dashed,
  estimated direct segment and labels the degradation; it never presents that
  line as turn-by-turn navigation. The fallback map has a slow, looping route
  pulse instead of a static image.
- Long-drive breaks use provider-returned service-area names. If no named
  record is available, the generated label describes the road corridor and
  position and asks for confirmation; numbered placeholders are not emitted.
- Scenic candidates normally reserve a 180–240 minute visit block. Only a
  source-marked compact venue may use a shorter window. Ordinary city days
  schedule at most two principal scenic stops (three only when the researched
  highlight set and time window support it); an anchor destination keeps one
  relaxed stop per day.
- Forecast cards consistently use “forecast weather reference”. Missing
  forecast data is a warning/degradation signal, not a blocker by itself.
