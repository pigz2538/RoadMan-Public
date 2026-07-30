# RoadMan 分阶段实施总规划

> 文档用途：作为 RoadMan 项目的**唯一实施总规范**，供多个开发 Agent 分工实现、代码审查、联调和验收使用。
>
> 重要说明：
>
> 1. **产品中的行程仍按“天 → 阶段 → 活动”组织**，这是用户查看和编辑旅行计划的方式。
> 2. **研发实施计划不按天排期，而按阶段推进**。每个阶段都有明确输入、任务、产物、验收标准和禁止扩展范围。
> 3. 本规划优先保证“自然语言需求 → 可执行自驾路书 → 地图展示 → 局部修改 → 重新验证 → 导出”的完整闭环；酒店、飞机、火车、门票和多人协作均作为后续能力分阶段接入。

---

## 1. 项目定位

### 1.1 产品名称

**RoadMan：面向自驾旅行的智能路书规划 Agent**

### 1.2 一句话定义

RoadMan 面向年轻人周末自驾和中短途旅行场景，根据用户自然语言描述、车辆参数、日期、同行人员、兴趣偏好和风险约束，自动生成按天和阶段组织的可执行路书，并支持景点、住宿、餐饮、充电、停车、公共交通和天气风险的统一规划、局部编辑、重新验证与导出。

### 1.3 首要用户

第一优先用户：**年轻人周末自驾用户**。

兼容用户：

- 新能源车主；
- 自驾与公共交通混合出行用户；
- 需要酒店、餐饮、景点一体化编排的自由行用户；
- 后续版本再扩展家庭老人儿童、长距离跨省和飞机/火车联运。

### 1.4 核心交付物

RoadMan 的交付物不是一条导航路线，而是一份完整路书，至少包含：

- 旅行总览；
- 每日安排；
- 每个移动阶段的起终点、途经点、路线、距离、时间、费用和天气；
- 每个活动的类型、地点、开始结束时间、停留时长和来源；
- 景点、餐饮、住宿、停车、充电、加油、服务区、公共交通等信息；
- 风险与冲突；
- 默认值、估算值和待确认项；
- 用户修改后的局部重算结果；
- Markdown、PDF、PPT和长图导出。

### 1.5 第一版明确不做

以下内容不得进入第一阶段核心开发范围：

- 真实酒店、机票、火车票或门票支付；
- 真实打车下单；
- 12306 自动登录、余票爬取或自动购票；
- 真实车辆控制；
- 自动驾驶决策；
- 大规模商业网站爬虫；
- 多人实时协作；
- 地图节点自由拖拽；
- 全国充电桩实时占用预测；
- 复杂推荐模型训练；
- 完整移动端编辑体验。

---

## 2. 产品原则

### 2.1 自驾为主，其他交通为辅

RoadMan 的交通决策优先级为：

1. 自驾主干路线；
2. 景区、城区或最后一公里公共交通；
3. 步行和打车估算；
4. 当自驾明显不适合或用户主动要求时，再比较飞机和火车。

飞机、火车不能默认占据主界面，也不能让产品退化为泛旅行比价平台。

### 2.2 规则和工具优先，LLM负责理解和解释

路线、距离、时间、价格、天气、坐标和能耗不得由 LLM 随意生成。

- LLM负责：意图理解、追问、需求抽取、候选语义筛选、修改意图理解、报告表达。
- 确定性程序负责：路线、距离、时间、费用、能耗、时间冲突、续航校验、排序和评分。
- 工具无结果时允许估算，但必须在数据中设置 `estimated=true`，前端显示“估算”。

### 2.3 默认值可用，但必须可见

用户信息不完整时系统可以使用默认值，例如：

- 最大连续驾驶时间；
- 景点默认停留时间；
- 续航安全系数；
- 默认预算；
- 默认休息间隔。

所有默认值必须在需求确认页和最终路书中醒目标记，允许用户一键修改。

### 2.4 Agent 不得直接篡改正式行程

Agent 对行程的所有修改，必须先生成 `PlanPatch` 修改建议卡片，显示：

- 修改对象；
- 原值；
- 新值；
- 影响范围；
- 时间变化；
- 费用变化；
- 风险变化；
- 是否需要后续重排。

只有用户确认后才能应用。

### 2.5 来源必须可追溯

外部数据必须保留：

- 数据提供方；
- 原始 ID；
- 查询时间；
- 原始链接或接口标识；
- 是否缓存；
- 是否估算；
- 是否需要用户复核。

---

## 3. 关键用户流程

## 3.1 首页启动流程

1. 用户进入首页；
2. 系统加载匿名配置或登录用户配置；
3. 系统展示默认白色车辆模型；
4. 用户直接输入自然语言需求，可以附加图片、PDF或其他资料；
5. 用户点击“发送/规划”；
6. 前端创建 Trip 草稿；
7. 后端启动 LangGraph 需求理解流程；
8. 系统生成结构化需求摘要；
9. 缺失必要信息时最多追问 2—3 轮；
10. 用户确认需求和默认值；
11. 系统进入正式规划；
12. 页面平滑切换到深度规划工作台；
13. SSE 持续显示规划进度；
14. 规划完成后展示地图、阶段和活动。

## 3.2 深度规划流程

深度规划页以三个区域为主：

- 左侧：已选行程节点和当前阶段附近推荐；
- 中间：地图、路线、阶段切换；
- 右侧：全局 Agent 对话和当前阶段上下文。

用户可以：

- 切换天；
- 切换阶段；
- 查看当前阶段附近景点、住宿、餐饮和服务；
- 将候选地点加入当前天；
- 删除或替换节点；
- 修改停留时间；
- 拖拽左侧卡片调整活动顺序；
- 选择节点后向 Agent 发出局部修改指令；
- 确认 PlanPatch 后只重算受影响阶段；
- 主动保存版本；
- 导出路书。

## 3.3 修改流程

1. 用户先在左侧或地图选择一个节点或阶段；
2. 右侧 Agent 自动获得当前选择上下文；
3. 用户输入“换成一个步行更少的景点”；
4. Editing Agent 理解修改意图；
5. 系统搜索替代候选；
6. 生成 PlanPatch；
7. 前端显示修改预览；
8. 用户确认；
9. 后端应用修改；
10. 重算当前阶段或当天受影响部分；
11. Verification Agent 重新检查；
12. 前端更新地图、列表和时间轴。

---

## 4. 行程领域模型

产品展示按“天和阶段”组织，但研发实施计划按阶段推进。

### 4.1 核心层级

```text
Trip
├── DayPlan
│   ├── MovementStage
│   │   ├── RouteSegment
│   │   ├── Waypoint
│   │   └── StageMetrics
│   ├── Activity
│   ├── MovementStage
│   └── Activity
└── DayPlan
```

### 4.2 Trip

字段建议：

```python
class Trip:
    id: str
    user_id: str | None
    title: str
    status: str
    start_date: date | None
    end_date: date | None
    origin: PlaceRef | None
    destination: PlaceRef | None
    selected_vehicle_id: str | None
    request: TripRequest
    days: list[DayPlan]
    warnings: list[PlanWarning]
    sources: list[SourceRecord]
    created_at: datetime
    updated_at: datetime
```

### 4.3 DayPlan

```python
class DayPlan:
    id: str
    day_index: int
    date: date
    title: str
    items: list[DayItemRef]
    total_distance_km: float
    total_drive_minutes: int
    total_walk_minutes: int
    estimated_cost: MoneyRange | None
    weather_summary: WeatherSummary | None
    warnings: list[str]
```

`items` 只能引用 `MovementStage` 或 `Activity`，顺序即时间轴顺序。

### 4.4 MovementStage

Stage 仅表示移动，不同时承担景点游览或住宿。

```python
class MovementStage:
    id: str
    day_id: str
    sequence: int
    mode: str  # driving/transit/walking/taxi/flight/train
    origin: PlaceRef
    destination: PlaceRef
    waypoints: list[PlaceRef]
    route_segments: list[RouteSegment]
    planned_start: datetime
    planned_end: datetime
    distance_km: float
    duration_minutes: int
    toll_fee: MoneyRange | None
    energy_estimate: EnergyEstimate | None
    weather_samples: list[WeatherSample]
    status: str
    warnings: list[PlanWarning]
    source_records: list[SourceRecord]
```

### 4.5 Activity

```python
class Activity:
    id: str
    day_id: str
    sequence: int
    type: str  # attraction/meal/hotel/rest/charging/fueling/parking/service
    place: PlaceRef
    planned_start: datetime
    planned_end: datetime
    duration_minutes: int
    locked: bool
    required: bool
    backup: bool
    user_note: str | None
    ticket_or_price: MoneyRange | None
    opening_hours: OpeningHours | None
    source_records: list[SourceRecord]
    warnings: list[PlanWarning]
```

### 4.6 PlanPatch

```python
class PlanPatch:
    id: str
    trip_id: str
    target_type: str
    target_id: str
    operation: str
    original_value: dict
    proposed_value: dict
    impact_scope: list[str]
    time_delta_minutes: int
    cost_delta: MoneyRange | None
    risk_delta: str | None
    requires_replan: bool
    status: str  # preview/accepted/rejected/applied
```

### 4.7 VerificationIssue

```python
class VerificationIssue:
    code: str
    severity: str  # info/warning/error/blocker
    title: str
    description: str
    affected_ids: list[str]
    source: str
    user_confirmation_required: bool
    auto_fix_available: bool
```

---

## 5. 前端总体架构

### 5.1 技术栈

- Vue 3；
- TypeScript；
- Vite；
- Vue Router；
- Pinia；
- `@tanstack/vue-query`；
- Naive UI；
- Tailwind CSS；
- 高德地图 JSAPI 2.0；
- `<model-viewer>`；
- ECharts；
- EventSource/SSE；
- Vitest；
- Playwright。

### 5.2 页面路由

```text
/
/login
/home
/vehicles
/trips
/trips/:tripId
/trips/:tripId/plan
/trips/:tripId/export
/settings
```

### 5.3 前端目录

```text
frontend/
├── src/
│   ├── api/
│   │   ├── auth.ts
│   │   ├── vehicles.ts
│   │   ├── trips.ts
│   │   ├── planning.ts
│   │   ├── editing.ts
│   │   └── exports.ts
│   ├── assets/
│   ├── components/
│   │   ├── common/
│   │   ├── vehicle/
│   │   ├── map/
│   │   ├── trip/
│   │   ├── agent/
│   │   ├── recommendation/
│   │   └── export/
│   ├── composables/
│   │   ├── useTripSSE.ts
│   │   ├── useAmap.ts
│   │   ├── useSelectedContext.ts
│   │   └── usePlanPatch.ts
│   ├── stores/
│   │   ├── user.ts
│   │   ├── vehicle.ts
│   │   ├── trip.ts
│   │   ├── planning.ts
│   │   └── ui.ts
│   ├── types/
│   ├── views/
│   │   ├── HomeView.vue
│   │   ├── TripPlanView.vue
│   │   ├── VehicleView.vue
│   │   └── ExportView.vue
│   ├── router/
│   └── styles/
└── tests/
```

---

## 6. 首页详细设计

### 6.1 顶部栏

左侧：

- 头像；
- 用户称呼；
- 下拉箭头。

右侧：

- 当前所在地天气；
- 当前车辆可用里程；
- 通知入口。

下拉菜单：

- 账号设置；
- 车型管理/选择；
- 用户设置；
- 系统设置；
- 历史行程；
- 退出登录。

### 6.2 左侧设置菜单

保持原型的垂直菜单，点击后以抽屉或局部页面打开，不强制跳转：

- 账号设置；
- 车型管理/选择；
- 用户设置；
- 系统设置；
- 其他设置。

### 6.3 车辆区域

第一版只提供一辆默认白色 SUV 模型，使用 GLB 模型和 `<model-viewer>`：

- 支持鼠标旋转；
- 支持缩放；
- 支持自动旋转；
- 支持重置视角；
- 加载失败时降级为 WebP/PNG；
- 不为每个车型制作独立 3D 模型。

车辆切换只更新文本参数和规划能力，不强制替换 3D 外观。

### 6.4 车辆参数

车型库选择为主，手工输入为兜底。保存字段：

- 品牌；
- 车系；
- 车型；
- 年款；
- 动力类型；
- 额定续航；
- 当前电量或油量；
- 电池容量；
- 百公里电耗或油耗；
- 最大充电功率；
- 车高；
- 车宽；
- 座位数；
- 车牌所在地；
- ETC；
- 山路适配；
- 非铺装路适配。

顶部里程显示规则：

- 未选择车辆：显示“未设置”；
- 选择车辆但未设置当前电量：显示额定续航；
- 设置当前电量：显示系统估算可用续航；
- 估算值必须显示“估算”。

### 6.5 自然语言规划入口

支持：

- 文本；
- 语音转文字；
- 图片；
- PDF；
- Word；
- Markdown；
- Excel；
- 12306 截图；
- 酒店订单截图；
- 门票截图。

但实际开发按阶段开放格式，第一阶段只实现文本、图片和 PDF。

### 6.6 首页快捷入口

第一版建议替换为：

- 周边单日短途推荐；
- 沿途风景路线探索；
- 新能源补能规划；
- 天气变化重规划。

“车辆保养预约”和“自驾新闻”暂不进入主路径。

---

## 7. 需求确认与追问界面

### 7.1 追问原则

最多 2—3 轮，只问会显著影响规划的字段：

- 出发地；
- 目的地或兴趣范围；
- 日期和时长；
- 返程约束；
- 车辆；
- 人员；
- 必去地点；
- 预算；
- 驾驶强度。

### 7.2 需求确认卡片

前端显示结构化结果：

```text
出发地：武汉
目的地：庐山
出发：周六 08:00
返程：周日 20:00 前
车辆：纯电 SUV
当前电量：80%
同行：3 名成人
偏好：自然景观、少排队
预算：默认 2000 元
最大连续驾驶：默认 2 小时
```

默认字段使用醒目标签，允许直接编辑。

### 7.3 状态

- `collecting`：正在收集信息；
- `clarification_required`：等待用户回答；
- `ready_to_plan`：信息足够；
- `planning`：正式规划中；
- `paused`：用户暂停；
- `completed`：完成；
- `failed`：失败。

---

## 8. 深度规划页详细设计

### 8.1 页面布局

桌面端采用三栏：

- 左栏 320—360 px；
- 中间地图自适应；
- 右栏 320—380 px；
- 顶部保留返回、标题、分享、导出；
- 小屏时右栏折叠为抽屉。

### 8.2 左栏

顶部：

- 当前天选择；
- 当天摘要；
- 标签：景点、住宿、餐饮、服务。

内容分为：

1. 已加入行程；
2. 附近推荐。

已加入卡片操作：

- 删除；
- 替换；
- 调整停留时间；
- 查看详情；
- 拖拽排序；
- 锁定；
- 问 Agent。

推荐卡片操作：

- 加入行程；
- 加入备选；
- 查看详情；
- 在地图定位；
- 查看来源；
- 问 Agent。

### 8.3 中央地图

视觉规则：

- 当前阶段：高亮蓝色、粗线、发光；
- 当前天其他阶段：浅蓝；
- 其他天：灰色；
- 当前节点：脉冲标记；
- 已选活动：实心图标；
- 推荐候选：空心图标；
- 风险：橙色或红色。

第一版不允许直接拖动地图节点，只允许通过卡片操作。

地图图层：

- 自驾路线；
- 公交路线；
- 景点；
- 酒店；
- 餐饮；
- 充电；
- 加油；
- 停车；
- 服务区；
- 医院；
- 厕所；
- 天气风险。

### 8.4 阶段导航

地图下方显示阶段序列：

```text
城市出发 → 高速路段 → 服务区休息 → 山区道路 → 抵达酒店
```

当前阶段卡片显示：

- 阶段名称；
- 交通方式；
- 距离；
- 时间；
- 费用；
- 天气；
- 能耗；
- 风险。

### 8.5 右侧 Agent

采用“全局会话 + 当前选择上下文”。

系统上下文包括：

- Trip 总目标；
- 当前天；
- 当前阶段；
- 当前节点；
- 用户车辆；
- 用户偏好；
- 当前风险。

用户必须先选择目标节点，才允许执行“删除它”“换一个”“缩短停留”等模糊修改。

### 8.6 Agent 修改卡片

修改卡片必须显示：

- 修改前；
- 修改后；
- 时间变化；
- 费用变化；
- 影响范围；
- 风险变化；
- 是否需要重排后续。

按钮：

- 取消；
- 应用修改；
- 查看替代方案。

---

## 9. 前端状态管理

### 9.1 Pinia Store

`tripStore`：

- 当前 Trip；
- 当前 Day；
- 当前 Stage；
- 当前选中节点；
- 当前草稿；
- 当前已保存版本。

`planningStore`：

- 规划任务状态；
- SSE 事件；
- 当前进度；
- 等待用户回答的问题；
- 当前错误；
- 是否暂停。

`vehicleStore`：

- 车辆列表；
- 当前车辆；
- 可用续航。

`uiStore`：

- 左栏标签；
- 地图图层；
- 右栏展开状态；
- 当前弹窗；
- 主题。

### 9.2 服务端状态与本地状态边界

服务端负责：

- 正式 Trip；
- 规划状态；
- 版本；
- Agent 消息；
- 修改建议；
- 文件；
- 导出任务。

本地负责：

- 当前视图；
- 当前图层；
- 未提交表单；
- 临时排序；
- 地图缩放和中心点。

---

## 10. 后端总体架构

### 10.1 技术栈

- Python 3.11；
- FastAPI；
- LangGraph；
- Pydantic v2；
- SQLAlchemy 2.x；
- PostgreSQL；
- Redis；
- ARQ；
- HTTPX；
- structlog；
- Docker Compose；
- Nginx；
- Ollama 作为模型 API 故障降级。

### 10.2 后端目录

```text
backend/
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── auth.py
│   │   ├── vehicles.py
│   │   ├── trips.py
│   │   ├── planning.py
│   │   ├── editing.py
│   │   ├── files.py
│   │   └── exports.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── logging.py
│   │   └── errors.py
│   ├── domain/
│   │   ├── trip.py
│   │   ├── stage.py
│   │   ├── activity.py
│   │   ├── patch.py
│   │   └── provenance.py
│   ├── graph/
│   │   ├── state.py
│   │   ├── planning_graph.py
│   │   ├── editing_graph.py
│   │   ├── export_graph.py
│   │   └── nodes/
│   ├── agents/
│   ├── skills/
│   │   ├── registry.py
│   │   ├── base.py
│   │   ├── amap/
│   │   ├── flyai/
│   │   ├── weather/
│   │   ├── openchargemap/
│   │   ├── opentripmap/
│   │   └── carinfo/
│   ├── services/
│   │   ├── route_service.py
│   │   ├── schedule_service.py
│   │   ├── vehicle_service.py
│   │   ├── verification_service.py
│   │   ├── recommendation_service.py
│   │   └── export_service.py
│   ├── repositories/
│   ├── models/
│   ├── schemas/
│   └── workers/
└── tests/
```

---

## 11. LangGraph 架构

### 11.1 总体原则

采用“Supervisor + 专业 Agent + 确定性服务”的混合模式：

- Supervisor 负责流程路由；
- 专业 Agent 负责语义任务；
- Service 负责确定性计算；
- Skill Registry 负责外部能力；
- Verification Agent 统一验收。

### 11.2 RoadManState

```python
class RoadManState(TypedDict, total=False):
    trip_id: str
    user_id: str | None
    raw_input: str
    attachments: list[dict]

    user_profile: dict
    vehicle_profile: dict | None
    trip_request: dict

    missing_fields: list[str]
    clarification_round: int
    clarification_question: str | None
    clarification_answers: list[dict]

    day_plans: list[dict]
    route_candidates: list[dict]
    selected_route: dict | None
    poi_candidates: list[dict]
    hotel_candidates: list[dict]
    travel_product_candidates: list[dict]
    weather_results: list[dict]

    warnings: list[dict]
    verification_result: dict | None
    pending_patch: dict | None

    current_day_id: str | None
    current_stage_id: str | None
    current_target_id: str | None

    progress: dict
    sources: list[dict]
    plan_markdown: str | None
    error: dict | None
```

### 11.3 初次规划 Graph

```text
START
  ↓
load_user_and_vehicle
  ↓
parse_attachments
  ↓
extract_trip_request
  ↓
apply_visible_defaults
  ↓
validate_required_fields
  ↓
missing?
  ├─ yes → generate_clarification → interrupt
  └─ no
       ↓
select_planning_strategy
       ↓
build_base_driving_route
       ↓
parallel_enrichment
  ├─ fetch_weather
  ├─ fetch_pois
  ├─ fetch_charging_or_fueling
  ├─ fetch_vehicle_constraints
  └─ fetch_travel_products
       ↓
split_into_days
       ↓
build_movement_stages
       ↓
build_activities
       ↓
optimize_schedule
       ↓
verify_plan
       ↓
blocker?
  ├─ yes → repair_plan → verify_plan
  └─ no
       ↓
render_markdown
       ↓
persist_trip
       ↓
END
```

### 11.4 编辑 Graph

```text
START
  ↓
load_selected_context
  ↓
understand_edit_intent
  ↓
validate_target_selected
  ↓
generate_patch_candidates
  ↓
calculate_patch_impact
  ↓
persist_patch_preview
  ↓
interrupt_for_confirmation
  ↓
accepted?
  ├─ no → END
  └─ yes
       ↓
apply_patch
       ↓
recompute_affected_stage
       ↓
shift_following_activity_times
       ↓
verify_affected_day
       ↓
persist_draft
       ↓
END
```

### 11.5 导出 Graph

```text
START
  ↓
load_trip
  ↓
freeze_export_snapshot
  ↓
prepare_map_images
  ↓
prepare_text_and_tables
  ↓
render_markdown
  ↓
render_html
  ↓
render_pdf
  ↓
render_ppt
  ↓
render_long_image
  ↓
persist_files
  ↓
END
```

### 11.6 暂停与恢复

第一版不实现完整 LangGraph Checkpointer，但必须支持短期暂停：

- Redis 保存任务状态；
- 追问时保存中间 State；
- 用户补充后恢复；
- 用户可以取消；
- 服务器重启后的任意节点恢复放到后续阶段。

---

## 12. Agent 划分

### 12.1 Requirement Agent

输入：自然语言、附件摘要、用户和车辆信息。

输出：结构化 `TripRequest`、缺失字段、默认值。

禁止：直接规划路线。

### 12.2 Route Agent

输入：TripRequest、车辆、起终点、途经点。

输出：路线候选和最终选定路线。

依赖：高德路线、公交、步行和距离服务。

禁止：由 LLM 生成距离、时间或坐标。

### 12.3 POI Agent

输入：当前区域、阶段、用户偏好和时间预算。

输出：景点、餐饮、酒店、服务设施候选。

依赖：高德、OpenTripMap、FlyAI。

### 12.4 Vehicle Agent

输入：车辆参数、路线、天气、海拔和当前电量。

输出：可用续航、能耗估算、补能建议和车辆限制。

### 12.5 Weather Risk Agent

五天内：沿路线按预计到达时间和位置采样。

五天后：按阶段和城市级别采样。

输出：降水、风速、能见度、温度、空气质量和风险标签。

### 12.6 Schedule Agent

输入：路线、候选活动、开放时间、用户约束。

输出：DayPlan、MovementStage、Activity 顺序和时间。

算法：先贪心，复杂任务再调用 OR-Tools。

### 12.7 Verification Agent

检查：

- 续航；
- 连续驾驶；
- 时间窗；
- 景点开放；
- 步行；
- 住宿；
- 预算；
- 公交末班；
- 数据缺失；
- API 冲突。

### 12.8 Editing Agent

只生成 PlanPatch，不直接修改 Trip。

### 12.9 Report Agent

负责报告结构、文字压缩、PPT页结构和来源展示。

### 12.10 酒店与票务 Agent

第一版不独立建设，在 POI Agent 和 FlyAI Adapter 内完成；后续再拆分。

---

## 13. Skill Registry

### 13.1 统一抽象

```python
class SkillAdapter(ABC):
    name: str
    version: str
    category: str

    async def validate_input(self, payload: dict) -> dict:
        ...

    async def execute(self, payload: dict, context: SkillContext) -> SkillResult:
        ...

    async def health_check(self) -> HealthStatus:
        ...
```

### 13.2 SkillResult

```python
class SkillResult:
    success: bool
    provider: str
    data: dict | list | None
    warnings: list[str]
    sources: list[SourceRecord]
    estimated: bool
    cache_hit: bool
    latency_ms: int
    error_code: str | None
```

### 13.3 已有 Skill 的定位

#### amap-jsapi

只用于前端地图展示和代码规范，不作为后端运行时路线数据源。

#### amap-lbs

国内核心数据源：

- 地理编码；
- POI；
- 驾车；
- 公交；
- 步行；
- 距离；
- 旅游路径。

#### flyai

作为外部旅行子 Agent：

- 航班；
- 酒店；
- 火车；
- 景点和门票；
- 旅行套餐。

其返回内容必须经过 Adapter 解析、Schema 校验、去重和来源标记，不能直接写入正式计划。

#### weather

使用 Open-Meteo，负责预报、历史、季节、空气、海况、海拔和集合预报。

#### openchargemap

高德 POI 为国内主源，OpenChargeMap 作为补充和国外主源。

#### opentripmap

国外景点主源之一，保留英文原名，同时生成中文翻译。

#### carinfo

仅用于 Demo 车型选择和参数参考，必须标记第三方来源和非商用边界。

### 13.4 缓存策略

- 地理编码：30 天；
- POI：1—7 天；
- 路线：按请求哈希缓存 30 分钟；
- 天气：30—60 分钟；
- 酒店和票务：短缓存或不缓存；
- 车辆基础信息：30 天；
- 景点详情：7 天。

### 13.5 熔断与降级

每个 Adapter 必须支持：

- 超时；
- 重试；
- 限流；
- 熔断；
- 缓存；
- 备用数据源；
- `estimated` 标记。

---

## 14. 路线与排程算法

### 14.1 路线评分

```text
总分 =
时间适配 × W1
+ 费用适配 × W2
+ 驾驶舒适 × W3
+ 补能便利 × W4
+ 风景匹配 × W5
+ 风险控制 × W6
```

第一版前端提供权重滑块，但后端使用合理默认权重。

### 14.2 续航估算

```text
可用续航 =
额定续航
× 当前电量比例
× 温度系数
× 高速系数
× 载重系数
× 海拔系数
× 安全余量
```

参数均为可解释规则，不得声称为车辆官方预测。

### 14.3 休息规划

规则：

- 达到最大连续驾驶时长前插入休息；
- 补能和休息尽量合并；
- 中午优先选择有餐饮的服务区；
- 夜间和恶劣天气降低驾驶上限；
- 用户可以覆盖默认规则。

### 14.4 景点排程

步骤：

1. 根据用户偏好评分；
2. 过滤开放时间不明且无法确认的关键景点；
3. 根据地理位置聚类；
4. 根据预计游览时长和当天剩余时间筛选；
5. 贪心插入；
6. 复杂情况调用 OR-Tools；
7. Verification Agent 检查。

### 14.5 删除活动后的行为

按用户决策：删除活动后自动提前后续节点。

额外规则：如果提前超过 30 分钟，前端询问是否重新优化当天剩余行程。

---

## 15. 数据库存储

用户倾向第一版统一用 Markdown 记录，但地图和局部重算不能只依赖 Markdown，因此采用：

- `state_jsonb`：程序真实状态；
- `plan_markdown`：用户阅读和导出；
- 少量核心表，不拆成过多业务表。

### 15.1 最小表

```text
users
vehicles
trips
trip_versions
messages
files
jobs
skill_calls
plan_patches
exports
```

### 15.2 trips

```text
id
user_id
title
status
request_text
state_jsonb
plan_markdown
created_at
updated_at
```

### 15.3 本地文件目录

```text
data/
├── uploads/{user_id}/{trip_id}/
├── exports/{trip_id}/{export_id}/
├── maps/{trip_id}/
├── cache/
└── temp/
```

必须做：

- 文件名去危险字符；
- 文件大小限制；
- MIME 检查；
- 临时文件清理；
- 禁止将用户文件路径直接传给 Shell。

---

## 16. API 设计

### 16.1 车辆

```http
GET    /api/v1/vehicles
POST   /api/v1/vehicles
GET    /api/v1/vehicles/{vehicle_id}
PATCH  /api/v1/vehicles/{vehicle_id}
DELETE /api/v1/vehicles/{vehicle_id}
POST   /api/v1/vehicles/{vehicle_id}/select
```

### 16.2 Trip

```http
POST   /api/v1/trips
GET    /api/v1/trips
GET    /api/v1/trips/{trip_id}
PATCH  /api/v1/trips/{trip_id}
DELETE /api/v1/trips/{trip_id}
```

### 16.3 规划

```http
POST /api/v1/trips/{trip_id}/planning/start
POST /api/v1/trips/{trip_id}/planning/answer
POST /api/v1/trips/{trip_id}/planning/pause
POST /api/v1/trips/{trip_id}/planning/resume
POST /api/v1/trips/{trip_id}/planning/cancel
GET  /api/v1/trips/{trip_id}/planning/events
```

### 16.4 编辑

```http
POST /api/v1/trips/{trip_id}/patches/preview
POST /api/v1/trips/{trip_id}/patches/{patch_id}/apply
POST /api/v1/trips/{trip_id}/patches/{patch_id}/reject
```

### 16.5 推荐

```http
GET /api/v1/trips/{trip_id}/days/{day_id}/recommendations
GET /api/v1/trips/{trip_id}/stages/{stage_id}/recommendations
```

### 16.6 版本

```http
POST /api/v1/trips/{trip_id}/versions
GET  /api/v1/trips/{trip_id}/versions
POST /api/v1/trips/{trip_id}/versions/{version_id}/restore
```

### 16.7 文件和导出

```http
POST /api/v1/files
GET  /api/v1/files/{file_id}
POST /api/v1/trips/{trip_id}/exports
GET  /api/v1/trips/{trip_id}/exports/{export_id}
```

---

## 17. SSE 事件协议

### 17.1 事件类型

```text
planning_started
node_started
tool_started
tool_completed
node_completed
clarification_required
progress
warning
patch_preview_ready
planning_paused
planning_resumed
planning_completed
planning_failed
```

### 17.2 示例

```json
{
  "event": "tool_started",
  "trip_id": "trip_123",
  "node": "weather_risk_agent",
  "tool": "open_meteo_forecast",
  "label": "正在分析沿途天气",
  "progress": 42,
  "timestamp": "2026-08-01T10:20:00+08:00"
}
```

### 17.3 前端展示原则

展示：

- 当前节点；
- 当前工具；
- 数据来源；
- 进度；
- 警告；
- 是否降级。

不展示：

- 模型私有推理；
- 内部长链思考；
- 未过滤的工具返回全文。

---

## 18. 多模型路由

### 18.1 角色

- 结构化抽取模型；
- 主规划模型；
- 编辑理解模型；
- 报告生成模型；
- 本地 Ollama 降级模型。

### 18.2 路由规则

- JSON 抽取优先选结构化输出稳定的模型；
- 长上下文附件使用支持长上下文的模型；
- 简单分类不使用昂贵模型；
- 外部 API 全部失败时才使用 Ollama 生成降级解释；
- 降级模型不能生成未经验证的路线数值。

### 18.3 统一模型接口

```python
class ModelRouter:
    async def invoke(self, task_type: str, messages: list, schema=None):
        ...
```

---

## 19. 导出系统

### 19.1 统一渲染源

所有导出均从冻结的 Trip Snapshot 生成：

```text
Trip Snapshot
├── Markdown
├── HTML
├── PDF
├── PPT
└── Long Image
```

### 19.2 PPT 结构

1. 封面；
2. 行程总览；
3. 全程地图；
4. 每日概览；
5. 每个阶段详情；
6. 住宿与餐饮；
7. 费用；
8. 天气和风险；
9. 数据来源和注意事项。

每个阶段页包含：

- 地图截图；
- 路线；
- 起终点；
- 时间；
- 里程；
- 交通方式；
- 天气；
- 停靠点；
- 景点图片；
- 餐饮；
- 住宿；
- 充电；
- 风险；
- Agent 建议；
- 数据来源。

### 19.3 地图导出

优先方案：

- 使用专用导出页面；
- Playwright 打开页面；
- 等待地图和路线渲染完成；
- 截取地图区域；
- 生成 PNG；
- 写入 PDF/PPT。

---

## 20. 安全与合规

### 20.1 API Key

- 只通过环境变量；
- 高德 JS 安全密钥通过安全代理；
- 前端不得暴露 WebService Key；
- 日志不得记录完整密钥。

### 20.2 附件

- 限制大小；
- 白名单扩展名；
- MIME 校验；
- 禁止执行附件脚本；
- 文档中的 Prompt Injection 只作为内容，不作为系统指令。

### 20.3 网站内容

- 优先官方 API；
- 只对允许访问的政府、景区或开放网站做低频采集；
- 保存来源和抓取时间；
- 不抓取携程、美团、去哪儿、12306等非公开交易页面；
- 开放时间无法确认时不自动作为强制节点。

### 20.4 高风险建议

- 续航、天气和道路风险仅作出行辅助；
- 不替代车辆仪表和官方交通信息；
- 对极端天气、封路、景区开放时间明确提示用户复核。

---

# 21. 分阶段实施计划

以下是整个项目的正式研发阶段。每个阶段可以交给不同 Agent 实现。不得跳过前置阶段直接开发后续功能。

---

## 阶段 A：规范冻结与项目骨架

### 目标

建立所有 Agent 共用的契约，避免各模块自行定义数据结构。

### 前置输入

- 本文档；
- 两张前端设计图；
- `preplan.txt`；
- `project(2).md`；
- `answer.txt`。

### 工作内容

#### 领域契约

定义并冻结：

- TripRequest；
- VehicleProfile；
- PlaceRef；
- DayPlan；
- MovementStage；
- Activity；
- RouteSegment；
- PlanPatch；
- VerificationIssue；
- SourceRecord；
- SkillResult；
- SSEEvent。

#### 仓库

建立 monorepo：

```text
roadman/
├── frontend/
├── backend/
├── shared/
│   ├── schemas/
│   └── examples/
├── skills/
├── deploy/
├── docs/
└── tests/
```

#### 基础设施

- Docker Compose；
- PostgreSQL；
- Redis；
- Nginx；
- 环境变量模板；
- 健康检查；
- JSON 日志。

#### Mock 数据

创建固定测试 Trip：武汉到庐山，两天一夜。

### 产物

- `docs/domain-model.md`；
- `docs/api-contract.md`；
- `shared/schemas/*.json`；
- 可启动的前后端空项目；
- Mock Trip JSON；
- Docker Compose。

### 验收标准

- 前端和后端均能启动；
- 数据 Schema 能互相校验；
- 可创建空 Trip；
- SSE 可以发送模拟事件；
- PostgreSQL 和 Redis 可连接；
- 不包含任何真实规划功能。

### 禁止扩展

- 不接入高德；
- 不开发 Agent；
- 不做导出；
- 不做 3D 精细动画。

---

## 阶段 B：前端静态工作台与 Mock 交互

### 目标

在没有真实后端的情况下完整还原两张设计图，并用 Mock 数据验证交互。

### 工作内容

#### 首页

- 顶部用户栏；
- 左侧菜单；
- 白色车辆模型；
- 里程和天气；
- 输入框；
- 附件入口；
- 快捷入口；
- 车辆抽屉。

#### 规划页

- 三栏布局；
- 天选择；
- 左侧已选和推荐；
- 中央地图占位或真实底图；
- 阶段卡片；
- 当前阶段高亮；
- Agent 对话；
- 修改预览卡片；
- 分享和导出按钮。

#### Mock 交互

- 切换天；
- 切换阶段；
- 左侧标签；
- 选择节点；
- 模拟删除；
- 模拟修改确认；
- 模拟 SSE 进度。

### 产物

- 页面组件；
- 设计 Token；
- Mock Store；
- 组件 Story 或演示页；
- Playwright 截图测试。

### 验收标准

- 两张原型的布局和主要视觉一致；
- 当前阶段高亮，当前天其他阶段浅蓝，其他天灰色；
- 右侧 Agent 能显示当前选中上下文；
- 在 1366×768 和 1920×1080 下正常；
- 不依赖真实后端也可演示。

### 禁止扩展

- 不实现真实规划；
- 不实现拖动地图点；
- 不做移动端完整适配。

---

## 阶段 C：后端核心、Skill Registry与真实地图能力

### 目标

建立真实 API 调用和统一 Skill Adapter，但不启动复杂多 Agent 规划。

### 工作内容

#### 后端核心

- FastAPI；
- ORM；
- Trip、Vehicle、File、Job表；
- 统一错误格式；
- 文件上传；
- SSE 管理器；
- ARQ 任务队列。

#### Skill Registry

实现：

- Adapter 基类；
- 注册；
- 输入校验；
- 超时；
- 重试；
- 缓存；
- 来源记录；
- 健康检查。

#### 首批 Adapter

- 高德地理编码；
- 高德驾车路线；
- 高德 POI；
- Open-Meteo；
- CarInfo Demo。

#### 前端

- 接入高德 JSAPI；
- 用后端返回的路线绘制 Polyline；
- Marker 分类；
- 路线阶段高亮。

### 产物

- Skill Registry；
- Adapter 测试；
- 真实路线接口；
- 真实天气接口；
- 真实地图渲染；
- 来源记录。

### 验收标准

- 输入武汉大学和庐山能返回路线；
- 地图可绘制路线；
- POI 可查询；
- 天气可返回；
- API失败返回统一错误；
- 缓存和超时生效。

### 禁止扩展

- 不做酒店比价；
- 不做 FlyAI；
- 不做完整 LangGraph。

---

## 阶段 D：基础 LangGraph 规划闭环

### 目标

实现最小完整闭环：自然语言 → 追问 → 自驾路线 → 天和阶段 → 路书。

### 工作内容

#### LangGraph

实现节点：

- load_context；
- extract_trip_request；
- apply_defaults；
- validate_required_fields；
- generate_clarification；
- build_base_route；
- split_into_days；
- build_stages；
- render_markdown；
- persist_trip。

#### 前端

- 需求确认卡片；
- 追问；
- 规划进度；
- 页面切换；
- 地图和阶段展示；
- Markdown路书。

#### 数据

- 保存 `state_jsonb`；
- 保存 `plan_markdown`；
- 保存 Agent 消息和 Skill 调用摘要。

### 产物

- 基础 Planning Graph；
- 追问接口；
- SSE进度；
- 第一条端到端路书。

### 验收标准

用户输入“周六从武汉去庐山，两天一夜”后：

- 系统最多追问 2—3 轮；
- 默认值可见；
- 生成真实驾车路线；
- 按天和阶段展示；
- 当前阶段可切换；
- 生成 Markdown；
- 任务可以取消和短期暂停。

### 禁止扩展

- 不做景点复杂排程；
- 不做局部编辑；
- 不做导出。

---

## 阶段 E：自驾深度能力

### 目标

形成区别于普通导航的自驾规划能力。

### 工作内容

#### Vehicle Agent

- 额定续航；
- 当前电量；
- 能耗估算；
- 安全余量；
- 充电/加油需求；
- 山路、车高等限制。

#### Weather Risk Agent

- 五天内时空采样；
- 五天后阶段采样；
- 路线风险标记；
- 风险降级。

#### POI

- 服务区；
- 停车；
- 充电；
- 加油；
- 餐饮；
- 医院；
- 厕所。

#### Schedule

- 最大连续驾驶；
- 自动休息；
- 补能和休息合并；
- 午餐节点；
- 夜间风险。

#### Verification

- 续航；
- 连续驾驶；
- 时间；
- 数据缺失。

### 产物

- Vehicle Agent；
- Weather Risk Agent；
- Verification Agent；
- 沿途服务；
- 风险页面。

### 验收标准

- 新能源路线能插入补能点；
- 连续驾驶过长能插入休息；
- 路线天气按预计到达时间采样；
- 关键风险显示在地图和阶段卡片；
- 估算值均有标记；
- API失败有降级。

---

## 阶段 F：旅游活动与酒店能力

### 目标

将自驾路线扩展为可执行旅游行程。

### 工作内容

#### POI Agent

- 景点；
- 餐饮；
- 住宿；
- 推荐排序；
- 备选地点；
- 来源融合。

#### Schedule Agent

- Activity 建模；
- 景点停留；
- 开放时间；
- 每日活动排序；
- 贪心调度；
- OR-Tools入口预留。

#### FlyAI

- 酒店；
- 景点和门票；
- 火车；
- 航班；
- 只搜索，不做真实支付。

#### OpenTripMap

- 国外景点；
- 中英双语；
- 保留原始来源。

### 产物

- 景点和酒店卡片；
- Activity 时间轴；
- FlyAI Adapter；
- 推荐和来源展示。

### 验收标准

- 能生成具体景点和餐饮活动；
- 能推荐具体酒店并展示价格来源；
- 开放时间未知时标记待确认；
- 酒店和景点能加入或替换；
- 飞机和火车只在用户主动要求或自驾明显不适合时出现。

---

## 阶段 G：局部编辑与重算

### 目标

实现 RoadMan 最重要的差异化能力：用户可以修改任意阶段，系统局部重算，而不是重新生成整份行程。

### 工作内容

#### Editing Agent

- 当前选择上下文；
- 修改意图；
- PlanPatch；
- 影响分析；
- 确认中断。

#### 局部重算

- 修改途经点：重算当前 Stage；
- 删除 Activity：后续时间提前；
- 替换景点：重算相关 Stage 和活动；
- 修改酒店：重算当天后续；
- 修改日期：提示全局重排。

#### 前端

- 选择状态；
- 修改卡片；
- 确认；
- 回滚；
- 更新地图；
- 更新SSE。

### 产物

- Editing Graph；
- PlanPatch API；
- 修改预览卡片；
- 局部重算服务。

### 验收标准

- Agent 不直接改正式计划；
- 所有修改先预览；
- 用户拒绝后无状态变化；
- 替换景点后路线和时间正确更新；
- 删除活动后后续自动提前；
- Verification Agent 重新检查。

---

## 阶段 H：附件解析、版本和导出

### 目标

支持用户输入现实材料，并生成可传播的最终路书。

### 工作内容

#### 附件

按顺序接入：

1. 图片；
2. PDF；
3. Word/Markdown；
4. Excel；
5. 订单截图。

提取结果必须先展示给用户确认，不能自动写入行程。

#### 版本

- 用户主动保存版本；
- 版本名称和备注；
- 恢复；
- 不做复杂 Diff。

#### 导出

- Markdown；
- PDF；
- PPT；
- 长图；
- 地图截图。

### 产物

- File Parser；
- 版本API；
- Export Graph；
- 导出模板。

### 验收标准

- PDF攻略可提取地点候选；
- 订单截图可提取酒店名称和日期；
- 用户确认后才加入行程；
- 可保存和恢复版本；
- PPT、PDF、Markdown和长图可下载；
- 地图截图清晰且带来源。

---

## 阶段 I：工程化、可观测和部署

### 目标

让系统可以稳定演示、部署和复现。

### 工作内容

- 完整 Docker Compose；
- Nginx；
- HTTPS；
- 内网穿透兼容；
- Skill 健康检查；
- 模型和 API 成本统计；
- 结构化日志；
- Trace；
- 失败重试；
- 限流；
- 缓存；
- 数据清理；
- 备份；
- 端到端测试；
- 压力测试；
- README；
- 部署文档。

### 产物

- 部署包；
- 测试报告；
- Demo账号；
- 运行监控页；
- API和Skill状态页。

### 验收标准

- 新服务器按README可启动；
- 关键Demo重复运行稳定；
- 外部API失败时不崩溃；
- SSE断线可重新连接；
- 上传和导出文件自动清理；
- 日志不泄露密钥。

---

## 阶段 J：后续扩展

不属于第一版验收：

- 完整 LangGraph Checkpointer；
- MCP Server；
- 多人协作；
- 地图拖拽；
- 多方案并列比较；
- 完整 OR-Tools；
- 手机编辑；
- 真实预订；
- 真实车机数据；
- 实时路况再规划；
- 个性化长期记忆；
- 推荐模型训练。

---

# 22. 多 Agent 分工方案

用户将让多个 Agent 负责实现，因此必须按模块拆分，不允许多人同时修改同一核心文件。

## 实现 Agent 1：领域模型与共享契约

负责：

- Pydantic Schema；
- JSON Schema；
- API模型；
- 示例数据；
- 不写业务逻辑。

验收：所有前后端类型可生成并校验。

## 实现 Agent 2：首页前端

负责：

- HomeView；
- 顶栏；
- 侧栏；
- 车辆模型；
- 输入框；
- 附件卡片；
- 车辆抽屉。

禁止修改规划页。

## 实现 Agent 3：规划工作台前端

负责：

- TripPlanView；
- 左侧列表；
- 地图；
- 阶段卡片；
- Agent面板；
- 修改预览；
- SSE显示。

禁止修改后端 Schema。

## 实现 Agent 4：后端基础设施

负责：

- FastAPI；
- DB；
- Redis；
- ARQ；
- 文件；
- SSE；
- 错误处理。

禁止写 LangGraph 业务节点。

## 实现 Agent 5：Skill Registry与Adapters

负责：

- Registry；
- 高德；
- Weather；
- FlyAI；
- OpenChargeMap；
- OpenTripMap；
- CarInfo；
- 缓存和降级。

## 实现 Agent 6：LangGraph初次规划

负责：

- State；
- Requirement；
- Route；
- Schedule；
- Verification；
- Planning Graph。

只通过 Registry 调用外部Skill。

## 实现 Agent 7：编辑与局部重算

负责：

- Editing Agent；
- PlanPatch；
- 影响分析；
- 重算；
- 回滚。

## 实现 Agent 8：导出与附件

负责：

- 文件解析；
- Markdown；
- PDF；
- PPT；
- 长图；
- 地图截图。

## 实现 Agent 9：测试与部署

负责：

- 单元测试；
- 集成测试；
- Playwright；
- Docker；
- Nginx；
- README；
- Demo数据。

---

# 23. 集成顺序

严格按以下顺序：

1. 领域模型；
2. 后端基础设施；
3. 前端Mock；
4. Skill Registry；
5. 高德和天气；
6. 基础 LangGraph；
7. 前后端端到端；
8. 自驾深度能力；
9. 旅游和酒店；
10. 编辑；
11. 附件和导出；
12. 工程化。

任何 Agent 在前置契约未合并前，不得自行创建同名数据结构。

---

# 24. Definition of Done

一个功能只有同时满足以下条件才算完成：

- 有代码；
- 有类型和 Schema；
- 有错误处理；
- 有日志；
- 有测试；
- 有接口文档；
- 有Mock或真实演示数据；
- 前端有加载、空、错误和降级状态；
- 外部数据有来源；
- 估算值有标记；
- 不泄露密钥；
- 不破坏既有端到端测试。

---

# 25. 核心验收案例

## 案例 1：基础周末自驾

输入：

> 周六早上从武汉出发，去庐山两天一夜，周日晚八点前回来，喜欢自然景观。

预期：

- 追问车辆、人数和出发时间；
- 生成路线；
- 按天和阶段显示；
- 插入必要休息；
- 推荐景点和酒店；
- 输出路书。

## 案例 2：新能源补能

输入：

> 纯电车，额定续航 420 公里，当前电量 60%。

预期：

- 计算估算可用续航；
- 检查路线；
- 插入补能点；
- 显示安全余量；
- 标注需出发前复核充电站状态。

## 案例 3：天气重规划

输入：

> 下午大雨时不想去户外景点。

预期：

- Weather Agent识别风险；
- 推荐室内替代；
- 生成PlanPatch；
- 用户确认后更新活动和路线。

## 案例 4：局部替换

操作：选择某景点，输入“换一个步行少一点的”。

预期：

- 用户必须先选择目标；
- Agent给出替代候选；
- 显示影响；
- 确认后局部重算；
- 后续时间更新。

## 案例 5：自驾不适合

输入：

> 单人跨省长距离出行，最大驾驶 4 小时。

预期：

- 系统提示自驾不满足约束；
- 在用户确认后比较火车或飞机；
- 不直接进入购票；
- 保留官方渠道确认提示。

---

# 26. 优先级

## P0：必须完成

- 自然语言输入；
- 2—3轮追问；
- 车辆；
- 高德路线；
- Day/Stage/Activity；
- 地图；
- 当前阶段高亮；
- SSE；
- 天气；
- POI；
- 续航和休息；
- Verification；
- 局部编辑；
- Markdown导出。

## P1：应完成

- 酒店；
- FlyAI；
- 公交/步行/打车估算；
- PDF和PPT；
- 图片/PDF附件；
- 用户主动版本。

## P2：后续

- 飞机和火车深度比价；
- Excel和订单截图；
- OR-Tools；
- 多路线方案；
- 多人协作；
- MCP；
- 完整Checkpoint。

---

# 27. 第一批可以立即下发给实现 Agent 的任务

1. 实现共享 Pydantic Schema 和 JSON 示例；
2. 搭建 monorepo 和 Docker Compose；
3. 复刻首页静态界面；
4. 复刻规划页静态界面；
5. 实现高德地图绘制 Mock 路线；
6. 实现 FastAPI Trip CRUD；
7. 实现 SSE 模拟事件；
8. 实现 Skill Registry 基类；
9. 实现高德地理编码和驾车路线 Adapter；
10. 实现武汉—庐山固定端到端 Mock。

这十项完成后，才进入 LangGraph 正式规划开发。
