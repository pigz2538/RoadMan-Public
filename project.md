# RoadMan 当前项目说明

更新日期：2026-07-30

当前版本：`0.5.0-dev`

当前里程碑：总规划阶段 E 已完成，阶段 F 旅游活动与酒店能力实施中

本轮稳定性与交互修复：

- 首页 3D 车辆继续保留真实加载和完整交互；新增 Firefox 兼容优化模型，将 GLB
  从约 11.8 MB 降至约 6.3 MB，并降低 Firefox 的渲染比例、阴影和曝光负载。
  Firefox 仍支持拖动旋转和滚轮缩放，只关闭持续自动旋转以避免显卡长时间满载。
- 新增首页规划前预检：Agent 一次性在输入框上方列出缺失字段、日期倒置、过去时间、
  跨海方式和明显不可能的时间窗口；所有问题解决后才创建行程和启动规划任务。
- 步行、骑行和公共交通阶段增加距离/时长可行性检查；不合理方式自动尝试公交、
  驾车或骑行，校验器会阻断超长步行、阶段时间矛盾和每日三餐缺失。
- 长途驾车按真实道路走廊上的服务区、休息点或充电站拆成独立
  `MovementStage`，每段都有独立起终点、道路点列和休息/补能间隔，不再把停靠点
  仅作为一条超长阶段中的附注。
- 规划进度在图计算结束后停留于 96%“正在保存并核对行程安排”；只有数据库保存成功、
  行程可立即读取后才发布 100%“规划完成”。
- 真实规划 SSE 改为持续事件流，进度单调递增；不再向真实行程注入演示事件，终态为“规划完成”并立即清除进度 UI。
- 页面统一使用“行程/行程安排”文案，移除完成后的悬浮进度条和“查看规划进度”按钮。
- 每个目的地游览日增加返回核心区阶段，最后返回总出发点；Verification Agent 会阻断阶段断链和未闭环路线。
- 高德路线采用后端持久化真实道路点列；驾车为蓝色、公交为绿色、步行/骑行为黄色，失败时才使用灰色虚线直连。
- 地图默认留出更大视野；充电、加油、餐饮、住宿、停车、景点及服务点使用语义标记，不再与起终点混用蓝色序号。
- 阶段卡片加宽并保留拖动、箭头、点击聚焦；Docker 后端以只读方式加载 `Skills/amap-lbs/apipkey.txt`。

## 已完成功能

### 前端

- Vue 3 首页、账户下拉设置、自然语言规划入口和快捷规划入口。
- 无外框 3D 车辆背景，支持旋转、拖动、滚轮缩放和加载状态。
- 首页输入框上方的集中需求预检与修正提示，预检通过前不会跳转规划页。
- 高德 JSAPI 地图拖动/缩放、真实道路点列、当前/其他阶段配色和失败虚线提示。
- 跨天阶段卡片、三卡左右窗口、拖拽平移、点击聚焦、长名称自适应。
- 驾车、步行、骑行、公交/地铁/接驳与景点、酒店、餐厅间移动均以
  `MovementStage` 展示。
- 武汉—庐山两天一夜演示及后端不可用时的前端 Mock。

### 后端与接口

- FastAPI、Pydantic v2、SQLAlchemy 2 Async、Alembic。
- Trip、Vehicle、File、Job、SkillCall 五类 ORM 数据及 Repository。
- Trip 与 Vehicle CRUD；安全文件上传/下载；Job 创建、查询和取消。
- `POST /api/v1/trips/preflight`：仅理解和校验需求，不创建 Trip、不投递 Job；
  返回 `ready`、全部 `issues` 与可复用的结构化 `extracted`。
- Redis + ARQ Worker，异步任务状态和进度持久化。
- 带单调事件 ID、支持 `Last-Event-ID` 续传的 SSE。
- 统一 `{error:{code,message,details,request_id}}` 错误与结构化 request-id 日志。
- PostgreSQL 生产数据库，SQLite 本地/测试兼容。
- LangGraph 初次规划图：需求抽取、默认值、追问、真实路线、拆天、阶段、校验、
  一次自动修复、Markdown 和持久化。
- 多日行程会在目的地周边生成真实公交/地铁、步行和骑行接驳；无法使用首选方式时
  按统一降级顺序切换，并保存实际采用的方式。
- 天气按每个阶段的预计到达坐标与时刻匹配 Open-Meteo 小时预报；超出 16 天预报
  范围时明确提示临近出发复核。
- 驾车阶段解析高德道路 `tmcs` 分段实时路况；未来计划只标记为当前路况参考，
  不伪装成未来拥堵预测。
- Ollama Cloud Requirement Agent；严格 JSON 解析失败时使用确定性中文解析回退。
- 追问 State/Agent 消息持久化，澄清接口恢复；Job 取消后 Trip 进入短期暂停。
- Vehicle Agent：采用用户选中车辆或显式估算车型，计算逐段能耗、安全余量、
  山路/车高限制并插入必要补能。
- Weather Risk Agent：按阶段预计到达时刻匹配温度、降水、能见度和风速，
  形成路线风险等级与标签。
- Schedule Agent：限制最大连续驾驶，合并补能与休息，并安排午餐和夜间风险。
- Verification Agent：阻断无法满足的续航/休息要求，对天气或非关键 POI 失败降级。
- 七类沿途服务与 `/risks`、`/services` API；风险路线和阶段卡已接入前端。

核心 API 见 [`docs/api-contract.md`](docs/api-contract.md)。

### Skill Registry 与真实 Adapter

- Pydantic 输入校验、版本化缓存键、Redis/内存回退缓存。
- 仅对网络传输错误和超时重试；参数错误与无结果不重试。
- SkillCall 审计 provider、adapter、耗时、缓存、成功状态、错误码和来源摘要。
- `amap.geocode`：高德地理编码。
- `amap.driving`：高德驾车路线。
- `amap.route`：驾车、骑行、步行、公交统一编排与真实 geometry。
- `amap.poi`：关键字、城市、类型和中心范围 POI 查询。
- `open_meteo.forecast`：坐标天气预报。
- `carinfo.demo`：固定车型续航与能耗样本。
- `flyai.hotel`：按目的地和入住日期搜索飞猪酒店，返回坐标、星级、实时价格区间
  与详情来源；不可用时自动降级为高德住宿 POI。

驾驶没有路线时按距离和同城条件尝试骑行、步行或公交；全部方式失败才返回
`ROUTE_UNAVAILABLE`，前端使用灰色虚线作无导航含义的提示。详见
[`docs/routing-fallback-design.md`](docs/routing-fallback-design.md)。

## 配置

| 环境变量 | 作用 |
|---|---|
| `DATABASE_URL` | SQLAlchemy 异步数据库 |
| `REDIS_URL` | Skill 缓存和 ARQ 队列 |
| `AMAP_WEBSERVICE_KEY` | 后端高德 WebService |
| `OLLAMA_API_KEY` | Ollama Cloud Requirement Agent |
| `OLLAMA_MODEL` | Ollama Cloud 模型，默认 `deepseek-v4-flash:cloud` |
| `VITE_AMAP_JSAPI_KEY` | 前端高德 JSAPI |
| `VITE_AMAP_SECURITY_JS_CODE` | 前端高德安全码 |
| `UPLOAD_DIR` | 上传内容目录 |
| `MAX_UPLOAD_BYTES` | 单文件大小上限 |
| `ENABLE_JOB_QUEUE` | 是否向 ARQ 投递 Job |

环境变量优先于本地 Skill 文件。密钥不写入数据库审计、响应或日志。

## 运行

Docker：

```powershell
docker compose up --build
```

统一入口：`http://localhost:8080`。

Conda：

```powershell
conda activate roadman
pip install -r requirements.txt
$env:PYTHONPATH='backend'
alembic -c backend/alembic.ini upgrade head
uvicorn app.main:app --reload --port 8000
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

## 已验证

- 后端 pytest：37 项通过，1 个真实接口集成用例默认跳过。
- Alembic：Docker PostgreSQL 迁移到 `20260730_0002 (head)`。
- 共享 Schema：18 个成功导出。
- Docker：PostgreSQL、Redis、Backend、Worker、Frontend 全部健康。
- 高德真实驾车/步行/骑行/公交、POI 与 Open-Meteo 实际请求通过。
- Redis 跨请求缓存、ARQ Job 完成、SSE 断点续传和 SkillCall 审计通过。
- 前端 TypeScript/Vite 构建通过；Firefox 真实 WebGL 车辆加载、拖动/滚轮交互测试
  通过；两种桌面宽度的首页预检与真实行程 Playwright 测试共 5 项通过。
- 真实 Agent 输入“下周六从武汉去庐山，五天四夜”完成 5 天、8 阶段路线，
  最终采用驾车、公交、步行和骑行四种交通方式；8 个阶段均包含天气与路况摘要。
- 容器 API 与 Playwright 页面验收通过，最新真实验收行程为
  `trip_109dcaef18d1`；往返长途均按真实充电点拆段，每日三餐完整、路线闭环，
  SSE 进度为单调递增并以 `96 → 持久化 → 100` 收尾。

阶段 D 的详细验收证据见
[`docs/backend-phase-d-plan.md`](docs/backend-phase-d-plan.md)。
阶段 E 的详细规则与验收证据见
[`docs/backend-phase-e-plan.md`](docs/backend-phase-e-plan.md)。

## 阶段 F 当前进度

- 已完成高德景点/餐饮/住宿候选融合、景点真实接驳、景点停留、每日三餐和过夜酒店
  的确定性时间窗排程。
- 已完成 FlyAI 酒店 Adapter、容器运行依赖、酒店价格/来源展示和高德降级。
- 餐食、景点、酒店与移动阶段出现时间重叠时会阻断规划；长途拆段后会顺延后续阶段。
- 详细设计与验收记录见
  [`docs/backend-phase-f-plan.md`](docs/backend-phase-f-plan.md)。

## 当前边界与后续

- 阶段 E 已覆盖自驾深度能力；景点复杂排程、开放时间、酒店库存和局部编辑仍留在
  后续阶段。
- 语音识别、真实酒店价格/库存、充电动态和完整导出将在后续阶段实现。
- 总规划只定义到阶段 J；本轮把“阶段 K”解释为 D–J 完成后的全链路验收、
  文档冻结和发布检查，不虚构额外产品范围。
