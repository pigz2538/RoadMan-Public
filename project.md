# RoadMan 当前项目说明

更新日期：2026-07-30

当前版本：`0.2.0`

当前里程碑：总规划阶段 C 已完成，下一步进入阶段 D LangGraph 规划闭环

## 已完成功能

### 前端

- Vue 3 首页、账户下拉设置、自然语言规划入口和快捷规划入口。
- 无外框 3D 车辆背景，支持旋转、拖动、滚轮缩放和加载状态。
- 高德 JSAPI 地图拖动/缩放、真实道路点列、当前/其他阶段配色和失败虚线提示。
- 跨天阶段卡片、三卡左右窗口、拖拽平移、点击聚焦、长名称自适应。
- 驾车、步行、骑行、公交/地铁/接驳与景点、酒店、餐厅间移动均以
  `MovementStage` 展示。
- 武汉—庐山两天一夜演示及后端不可用时的前端 Mock。

### 后端与接口

- FastAPI、Pydantic v2、SQLAlchemy 2 Async、Alembic。
- Trip、Vehicle、File、Job、SkillCall 五类 ORM 数据及 Repository。
- Trip 与 Vehicle CRUD；安全文件上传/下载；Job 创建、查询和取消。
- Redis + ARQ Worker，异步任务状态和进度持久化。
- 带单调事件 ID、支持 `Last-Event-ID` 续传的 SSE。
- 统一 `{error:{code,message,details,request_id}}` 错误与结构化 request-id 日志。
- PostgreSQL 生产数据库，SQLite 本地/测试兼容。

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

驾驶没有路线时按距离和同城条件尝试骑行、步行或公交；全部方式失败才返回
`ROUTE_UNAVAILABLE`，前端使用灰色虚线作无导航含义的提示。详见
[`docs/routing-fallback-design.md`](docs/routing-fallback-design.md)。

## 配置

| 环境变量 | 作用 |
|---|---|
| `DATABASE_URL` | SQLAlchemy 异步数据库 |
| `REDIS_URL` | Skill 缓存和 ARQ 队列 |
| `AMAP_WEBSERVICE_KEY` | 后端高德 WebService |
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

- 后端 pytest：15 项通过。
- Alembic：空 SQLite 与 Docker PostgreSQL 迁移到 `20260730_0001 (head)`。
- 共享 Schema：16 个成功导出。
- Docker：PostgreSQL、Redis、Backend、Worker、Frontend 全部健康。
- 高德真实驾车/步行/骑行/公交、POI 与 Open-Meteo 实际请求通过。
- Redis 跨请求缓存、ARQ Job 完成、SSE 断点续传和 SkillCall 审计通过。
- 前端 TypeScript/Vite 构建与现有浏览器测试通过。

阶段 C 的详细验收证据见
[`docs/backend-phase-c-plan.md`](docs/backend-phase-c-plan.md)。

## 当前边界与后续

- 当前自然语言入口仍进入固定演示，尚未由 LLM 生成任意 Trip。
- SSE 传输已工程化，但规划事件仍等待阶段 D LangGraph 节点接入。
- 语音识别、真实酒店价格/库存、充电动态和完整导出将在后续阶段实现。
- 总规划只定义到阶段 J；本轮把“阶段 K”解释为 D–J 完成后的全链路验收、
  文档冻结和发布检查，不虚构额外产品范围。
