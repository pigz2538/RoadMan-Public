# RoadMan 当前项目说明

更新日期：2026-07-31

当前版本：`0.7.0-dev`

当前里程碑：总规划阶段 A–I 首版验收项已实现，阶段 J 按原规划保留为后续扩展

本轮稳定性与交互修复：

- 用户确认需求后立即进入详情页；LangGraph 每完成一个节点就保存可用快照，阶段和活动逐条出现，右侧 Agent 同步解释正在查询、融合、排程和校验的内容，不再用单一进度条假装完成。
- 规划未完成时前后端都禁止导出；完成后由 Report Agent 从冻结快照生成 Markdown、PDF、PPTX 和长图，后三种格式包含真实道路点列路线图及景点、餐饮、住宿详情图卡。
- POI Agent 同时融合高德、FlyAI 与 OpenTripMap/OpenStreetMap，负责同地点合并和中文显示名；活动卡与候选卡保留图片、介绍、详情链接和来源。
- Editing Agent 支持当前选中 Stage 或 Activity 的语义上下文，例如选中返程阶段后输入“从庐山返程到服务区吃个饭”会生成局部 PlanPatch 预览。
- 规划地图支持切换景点、住宿或餐饮类型后直接选点；高德逆地理编码生成地点信息，再交由右侧 Agent 展示 PlanPatch 预览，用户确认后才加入正式行程。地图上的既有活动也可直接选中，再用“删除这个”或“替换为……”进行语义修改；明确说出候选地点名时 Editing Agent 会优先匹配该地点。
- 地图接驳线按真实时间线生成，只在阶段与活动之间补画必要接驳；阶段本身已有道路点列时不会再额外画跨天/跨城市虚线。候选检索提高到高德景点 25、餐饮 20、住宿 20，并保留带坐标的 FlyAI 独立景点。
- Agent 可理解“在返程服务区安排午饭”“第2天加一家酒店”“第1天多加一个景点，顺路短停”等语句；加入景点时会根据“多逛/短停/深度”等措辞使用弹性停留时长，预览确认卡固定在右侧底部。

- 首页 3D 车辆继续保留真实加载和完整交互；新增 Firefox 兼容优化模型，将 GLB
  从约 11.8 MB 降至约 6.3 MB，并降低 Firefox 的渲染比例、阴影和曝光负载。
  Firefox 仍支持拖动旋转和滚轮缩放，只关闭持续自动旋转以避免显卡长时间满载。
- 首页规划前预检改为逐题澄清：覆盖缺失字段、日期倒置、过去时间、跨海方式和
  不可能时间窗，并由 Requirement Guard 补充语义矛盾检查；回答后重新校验，
  屏蔽已解决问题的重复问法，最后必须确认结构化摘要才创建行程和启动任务。
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
- 高德路线采用后端持久化真实道路点列；仅当前阶段按交通方式着色，其他阶段统一
  灰色；当前线宽 6 px，阶段切换使用 2.4 秒缓动，失败时才灰色虚线直连。
- 地图默认留出更大视野；充电、加油、餐饮、住宿、停车、景点及服务点使用语义标记，不再与起终点混用蓝色序号。
- 阶段卡片加宽并保留拖动、箭头、点击聚焦；Docker 后端以只读方式加载 `Skills/amap-lbs/apipkey.txt`。

## 已完成功能

### 前端

- Vue 3 首页、账户下拉设置、自然语言规划入口和快捷规划入口。
- 无外框 3D 车辆背景，支持旋转、拖动、滚轮缩放和加载状态。
- 首页输入框上方的逐题需求澄清、选项/日期输入和最终摘要确认，确认通过前不会
  创建 Trip 或跳转规划页。
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
  接收 `answers`、`previous_extracted`、`semantic_checked` 与 `confirmed`，
  返回 `ready`、`confirmation_required`、逐题 `issues`、摘要和可复用的
  `extracted`；重复轮次不再次调用需求抽取模型。
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
- `flyai.poi`：按城市和关键词搜索景点与门票，支持 `¥2x` 一类脱敏价格解析。
- `opentripmap.nearby`：境外坐标周边景点，保留英文/原始名称、距离、评分和来源。

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

## 阶段 F 完成情况

- 已完成高德景点/餐饮/住宿候选融合、景点真实接驳、景点停留、每日三餐和过夜酒店
  的确定性时间窗排程。
- 已完成 FlyAI 酒店 Adapter、容器运行依赖、酒店价格/来源展示和高德降级。
- 餐食、景点、酒店与移动阶段出现时间重叠时会阻断规划；长途拆段后会顺延后续阶段。
- 已完成 FlyAI 景点门票、OpenTripMap 境外景点、评分/距离/预算/偏好综合排序。
- 已完成备选列表与 `PlanPatch` 加入/替换：预览不修改正式行程，确认后才应用。
- 详细设计与验收记录见
  [`docs/backend-phase-f-plan.md`](docs/backend-phase-f-plan.md)。

## 阶段 G 完成情况

- 所有加入、替换和删除均先生成 `PlanPatch`，预览阶段正式 Trip 不变。
- 支持从高德地图选点添加景点、住宿和餐饮；支持点选地图上的既有活动作为 Agent 编辑上下文，并通过自然语言加入已检索候选或删除当前活动。
- 替换景点会重算当天所有连接旧地点的相邻阶段，并重新检查路线闭环。
- 删除活动会避让固定移动阶段并提前后续活动；校验失败不会保存。
- 应用前快照支持回滚；前端 Agent 面板支持预览、确认、拒绝和撤销上次修改。
- 详细设计与验收记录见
  [`docs/backend-phase-g-plan.md`](docs/backend-phase-g-plan.md)。

## 阶段 H 当前进度

- 图片、PDF、DOCX、Markdown、XLSX 均通过文件签名/结构校验后上传。
- PDF/Word/Markdown/Excel 可提取地点、酒店、日期、订单号；图片在已配置
  Ollama Cloud 时使用多模态抽取。
- 所有附件结果先预览，用户确认后才写入必去地点。
- 行程版本可命名保存、列出与恢复；Markdown 可从规划页直接下载。
- 导出统一从冻结快照生成 PDF、PPTX 与长图 PNG；规划页已提供对应下载入口。
- 导出未就绪时返回结构化 `ROADBOOK_NOT_READY`，不生成空文件。
- 规划页已接入附件上传、解析预览和地点勾选确认，确认后才写入行程必去地点。
- 阶段 I 首轮新增 `/api/v1/ops/metrics` 和 `/api/v1/skills/metrics`，并记录请求/Skill 延迟、失败与缓存命中。
- 请求统一透传 `X-Request-ID`、`X-Trace-ID`，默认启用滑动窗口限流，日志不记录请求体和密钥。
- 新依赖已写入 `backend/requirements.txt`，并安装进 `roadman` Conda 环境。
- 详细进度见 [`docs/backend-phase-h-plan.md`](docs/backend-phase-h-plan.md)。

## 当前边界与后续

- A–I 逐项审计和真实验收证据见 [`docs/plan-completion-audit.md`](docs/plan-completion-audit.md)。
- 阶段 I 已补充 30 天上传文件自动清理、PostgreSQL 备份/恢复脚本、HTTPS Nginx 模板、`/ops` 运行监控页和无额外依赖的压力冒烟脚本。
- 外部供应商没有返回图片、价格、开放时间、库存或充电动态时必须明确显示未知/估算和来源，不允许 Agent 编造。
- 总规划的阶段 J 明确不属于第一版验收；完整 Checkpointer、MCP、多方案并列、多人协作、真实预订和车机实时数据继续作为后续扩展。
- 最新编辑一致性：候选补丁应用后服务端重新读取已提交 Trip，前端再做一次 canonical hydrate；删除景点后立即加入新景点不会从旧预览快照复活。回归覆盖 delete → add 顺序。
- POI 详情 Agent：规划阶段对排名靠前的景点、住宿、餐饮做可超时的百度百科元信息补充，合并简介、OG 图片、百科链接与来源记录；网页不可访问时保留高德、FlyAI、OpenTripMap 结果，不阻断规划。
- 地图工具栏改为“地图加点”分段选择（景点、住宿、餐饮），明确先选类型再点地图；候选和已加入活动统一显示中文小时、分钟格式，不再出现 0.8h、1.5h。
- 规划展示与后台执行解耦：Worker 直接持久化阶段和活动，前端以 680ms 队列逐项播放，后台完成后等待展示队列自然收尾；报告与路线 Agent 名称统一中文展示。
- 地图长距离未返回道路点列时不再绘制跨城市虚线；阶段直连超过 35 km 会隐藏，活动接驳仅保留 12 km 内的局部虚线并保留不可用提示。
- Report Agent 新增统一 HTML 报告模板（路线图、精选卡片、逐日阶段与详情），并提供 `/roadbook.html`；PDF、PPTX、PNG 继续使用同一份 Trip 快照和卡片数据生成。
- Requirement Agent 会根据语义判断同行人数（例如情侣/夫妻通常为 2 人），明确人数优先；离线确定性解析器不再硬编码关系表达。它同时会把“乌镇及其周边/附近”等范围修饰词从地理编码名称中剥离，并保留“目的地周边”偏好；对短地名的歧义高德结果会用出发点附近 POI 二次校正，避免跨省误定位。
- 驾车阶段统一显示“当前路况”；步行、骑行阶段在开启 `ENABLE_ROUTE_ELEVATION=true` 时通过 Open-Meteo 高程采样计算总爬升，并在路线卡片显示“路线起伏”。
- 规划校验失败改为中性灰色弹窗，展示具体原因、当前偏好和可执行的调整建议；首页网络失败也不会覆盖用户输入。
- 首页白模默认镜头调整为更近的安全范围（170%），保留滚轮缩放和 WebGL context lost 保护。

## 本轮行程体验更新（2026-08-01）

- 首页输入框默认保持空值；灰色示例提示移到输入框外，用户输入会写入 sessionStorage，返回首页后仍保留上次需求。
- Requirement Agent 新增 `departure_time` / `return_time`。显式日期（如 `8.11`、`8/14`）由确定性解析器保护，避免模型臆造年份；“中午出发”会传递为 `12:00`，不再被默认的 08:00 覆盖。
- 舒适型多日行程会按每天 2–4 个候选景点目标编排，并在活动空档安排早餐、午餐、晚餐和过夜住宿；晚出发日会避开与首段移动重叠的用餐时间。
- 规划页新增全天时间线，把移动、景点、三餐和住宿按时间顺序逐项展示；后台完成后，前端以阶段/活动队列渐进补齐，避免进度条满后整页瞬间替换。
- 首页白模画布扩大到约 50vh，默认镜头调整到 85% 以提升可见尺寸，仍保留滚轮缩放边界和 WebGL context lost 保护。
- 新增回归覆盖：点号日期、午间出发、情侣同行人数、晚出发用餐避让，以及前端 TypeScript/Vite 构建。
- 本轮实测输入已生成 4 天行程：第 1 天 2 个景点、第 2/3 天各 4 个景点；每天 3 餐，第 1–3 天包含住宿，移动时间从 12:00 出发正确开始。后端回归为 63 passed / 1 skipped，桌面 E2E 为 9 passed / 1 skipped。
- 本轮新增“每日复核 Agent”：首轮行程编排后再次检查每一天的上午、下午、晚间、三餐和住宿；确实没有可安全插入的候选时，会显式加入可调整的“自由活动 / 休息”时间块，避免页面出现空白时段。
- 详情页轮询增加并发保护，并在每次 SSE `plan_updated` 后重新读取 canonical Trip，阶段、活动和全天时间线会持续刷新，不需要手动刷新浏览器。
- 首页新增历史规划下拉入口，直接读取后端 `GET /api/v1/trips` 的持久化 Trip；天气默认武汉，浏览器允许定位时优先使用当前位置并调用天气接口，定位失败自动回退武汉。
