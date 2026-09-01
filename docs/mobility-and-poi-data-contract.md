# 实时地点与交通信息契约

这份文档记录 RoadMan 当前对“景点资料”和“可执行交通班次”的处理边界。所有外部结果都带来源和抓取时间；拿不到的字段展示为“暂未返回/出发前复核”，不会用一个看起来像真实数据的默认值代替。

## 景点、餐饮、住宿

规划阶段会先从目的地研究和候选检索得到候选，再对实际排入行程的项目做一次精确核验：

1. 地图地点检索负责坐标、地址、行政区和地图链接。
2. 地图 POI 详情负责营业时间、价格/门票、停车提示、电话、官网和图片。
3. 旅行信息服务负责景点门票名称、价格、免费状态、详情/预约链接和图片。
4. 公开网页/百科只作为描述和补充来源，不会把百科推测标记成“已确认营业时间”。

`Activity` 新增以下字段：

- `ticket_name`、`ticket_status`、`ticket_note`：门票的结构化状态；未知时为 `unknown`。
- `opening_hours`：文本、是否由地图/官方来源确认、来源数、核验日期和链接。
- `parking_note`、`parking_or_price`：停车说明与可解析的停车费区间；无法确认时保留未知状态。
- `reservation_status`、`reservation_note`：预约/实名/购票要求；只有来源明确时才标为“建议/必须预约”。
- `information_status`：`complete`（至少两个独立来源且关键字段可用）、`partial`、`unavailable`。
- `information_sources_count`、`information_checked_at`：用于界面和导出追溯。

前端活动卡片展示门票、营业时间、停车、信息完整度和可打开的来源链接。未知字段不会被隐藏，也不会被硬编码成免费/全天开放。

## 公共交通

`amap.route` 的公共交通结果保留 `transit_legs`，每一段包含：线路名/线路编号、公交或地铁类型、上车站、下车站、首末站时间（若接口返回）、站数、距离、耗时和票价。阶段卡片优先显示“2号线 汉口站 → 江汉路站”这样的可执行信息；如果高德没有返回线路号，则显示“线路号暂未返回，出发前复核”。

## 火车、飞机、轮船

旅行信息服务的结构化搜索端点：

- `POST /api/v1/skills/flyai/train`
- `POST /api/v1/skills/flyai/flight`
- `POST /api/v1/skills/flyai/ferry`

火车和飞机阶段保留真实的 `service_number`（如 G11、CA123）、运营方、出发/到达站或机场、座席、出发/到达时间、价格和详情链接。多段联程会以 `A123 / B456` 保留全部编号。接口没有返回编号时，`service_status=unavailable`，不会把“高铁”或“航班”伪装成车次号。

## 公开备选查询

主旅行信息服务失败或返回空集合时，规划器会自动按以下顺序降级：

1. 火车/高铁：`flyai.train` + `mcp12306.train` + `freeapi.train` 并行查询、按车次号与时间去重。`mcp12306.train` 通过可选的 Streamable HTTP MCP 服务查询铁路实时数据；所有结果仍需通过日期、起终点、到发时间和车次号校验，无法解析时返回不可用，不生成假班次。
2. 航班：`flyai.flight` + `sixapi.flight` + `aviationstack.flight`并行查询，再按航班号与时间去重。后两者分别需要 `FLIGHT_FALLBACK_API_KEY` 与 `AVIATIONSTACK_API_KEY`；未配置的备选源不影响其它已确认班次，但三个数据源都无结果时不会生成占位航班。
3. 油价：`freeapi.oil` 是规划上下文的可选查询，不参与路线可行性判定。没有 `OIL_APP_ID/OIL_APP_SECRET` 时跳过，并在能力健康检查中标为未配置。

每次降级都会写入技能调用审计、来源 URL 和 `TRANSPORT_FALLBACK_USED` 警告。接口文档：[铁路实时备用服务](https://github.com/drfccv/mcp-server-12306)、[公开车次查询](https://www.free-api.com/doc/675)、[公开油价查询](https://www.free-api.com/doc/592)、[航班备选服务](https://www.6api.net/api/flight/)。铁路 MCP 项目声明仅供学习研究，正式商业部署前需单独确认许可边界。

轮船目前来自语义检索，时间和船名必须标记为 `estimated`，并在阶段警告出发前向船公司核实；系统不会伪造船班号。

## 对外 API 与审计

新增 `POST /api/v1/skills/amap/poi-detail`。所有 Skill 调用仍写入 Skill Registry 审计，响应包含 `sources`，来源记录包含 `source_type`、`confidence` 和结构化 `facts`。Docker 冒烟脚本覆盖地图、天气、车辆、景点、酒店、火车、飞机、轮船和目的地搜索端点。

## 失败与降级

外部服务超时、额度耗尽或返回空结果时，规划仍可继续，但只会留下结构化的未知状态和风险提示；只有路线本身不可执行、时间冲突或用户硬约束无法满足时才阻断规划。出发前应重新核对预约、营业时间、价格和班次。
