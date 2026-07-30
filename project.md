# RoadMan 当前项目说明

更新日期：2026-07-30

当前版本：`0.1.0`

当前阶段：总规划第 27 节首批工程骨架与武汉—庐山演示已完成

## 1. 项目定位

RoadMan 是面向周末自驾和中短途旅行的智能路书工作台。当前版本已经打通：

1. Vue 3 前端首页与行程规划页。
2. FastAPI 后端、Trip 持久化和统一领域模型。
3. 武汉—庐山两天一夜固定 Mock 行程。
4. 高德 WebService Adapter 与高德 JSAPI 地图。
5. 模拟规划进度 SSE。
6. Skill Registry、缓存、超时和重试骨架。
7. 可旋转、缩放的 3D 车辆模型。
8. PostgreSQL、Redis、Nginx 和 Docker Compose 部署骨架。

当前并不是完整 AI Agent 产品。LangGraph、多轮澄清、真实酒店/天气/充电服务、
路线动态重规划等属于后续阶段。

## 2. 已完成的前端内容

### 2.1 首页

- 驾驶员信息、天气、车辆估算续航和通知入口。
- 账户区域为下拉菜单，设置项打开对应侧边抽屉。
- 默认加载 3D 车辆，支持鼠标/触摸旋转、拖动观察和滚轮缩放。
- 模型下载期间仅显示“正在加载车辆模型”，不使用 SVG 车辆替代。
- 3D 画布作为背景上方的最低内容层，宽高均为视口的 1.8 倍；
  保留滚轮缩放、旋转和拖动，同时降低车辆靠近画布边缘时的裁切概率。
- 自然语言行程输入组件。
- 输入框、附件入口、语音输入预留按钮和开始规划按钮使用统一容器。
- 四个快捷规划入口。
- 输入内容后进入固定武汉—庐山规划演示。

### 2.2 规划页

- 按天切换行程。
- 按 Stage 切换路线方案。
- 景点、住宿、餐饮、服务分类切换。
- 行程节点选择与 Agent 消息联动。
- 高德地图支持拖动、滚轮缩放和地图控件。
- 正常情况下调用 `AMap.Driving` 获取真实驾车道路点列。
- 当前路线为较细蓝色道路轨迹，其他路线为较暗灰色轨迹。
- 路线是高德地图地理覆盖物，随地图平移、缩放，屏幕线宽保持不变。
- 选择阶段后地图用 1 秒缓动聚焦该阶段，并额外缩小一级保留周边路线语境；
  路线点列显式接回原始 POI 起终点，避免道路吸附点与 Marker 视觉断开。
- 高德未返回真实道路点列时，用灰色虚线直连起终点并显示降级提示。
- 驾车无路线时，前端依次尝试骑行、步行和同城公交；Stage 与 Activity 之间也会
  请求真实连接路线，避免行程节点在地图上断开。
- 阶段卡展示起终点、预计出发/抵达时间、道路、路况、天气、里程、耗时、
  路费、能耗和风险提示。
- 底部阶段栏按跨天时间顺序展示全部移动段，支持左右按钮、鼠标拖拽和触控横移，
  当前阶段自动居中，两侧内容在箭头下方渐隐。
- 景点、餐厅、酒店等活动节点之间的步行、骑行、公交/地铁/接驳也必须建模为
  `MovementStage` 并生成独立卡片，不能只在地图上临时补线。示例现含驾车、
  步行、骑行和景区公交阶段。
- 后端未启动时可读取前端内置 Mock，方便单独验收页面。

## 3. 已完成的后端内容

技术栈：

- FastAPI
- Pydantic v2
- SQLAlchemy 2 异步接口
- SQLite 本地默认数据库
- PostgreSQL Docker 环境
- Redis 配置预留
- HTTPX 外部请求
- Structlog 日志

后端入口：`backend/app/main.py`

默认服务地址：`http://localhost:8000`

Swagger UI：`http://localhost:8000/docs`

OpenAPI JSON：`http://localhost:8000/openapi.json`

## 4. API 接口总览

### 4.1 服务健康检查

#### `GET /health`

返回 API 状态、运行环境和已注册 Skill 名称。

响应示例：

```json
{
  "status": "ok",
  "service": "roadman-api",
  "environment": "development",
  "skills": ["amap.geocode", "amap.driving"]
}
```

### 4.2 Trip 接口

所有 Trip 接口前缀为 `/api/v1/trips`。

| 方法 | 路径 | 请求 | 响应 | 当前状态 |
|---|---|---|---|---|
| POST | `/api/v1/trips` | `TripCreate` | `Trip`，HTTP 201 | 已实现 |
| GET | `/api/v1/trips` | 无 | `Trip[]` | 已实现 |
| GET | `/api/v1/trips/{trip_id}` | 路径参数 | `Trip` | 已实现 |
| PATCH | `/api/v1/trips/{trip_id}` | `TripUpdate` | `Trip` | 已实现 |
| DELETE | `/api/v1/trips/{trip_id}` | 路径参数 | HTTP 204 | 已实现 |
| GET | `/api/v1/trips/mock/wuhan-lushan` | 无 | 固定武汉—庐山 `Trip` | 已实现 |

#### 创建 Trip

`POST /api/v1/trips`

最小请求示例：

```json
{
  "title": "武汉—庐山两天一夜",
  "request": {
    "raw_text": "周六从武汉出发去庐山，周日晚八点前回来"
  },
  "selected_vehicle_id": "vehicle_demo_ev"
}
```

#### 更新 Trip

`PATCH /api/v1/trips/{trip_id}`

请求体字段均可选：

```json
{
  "title": "新的行程名称",
  "status": "planning",
  "selected_vehicle_id": "vehicle_demo_ev"
}
```

可用状态：

- `collecting`
- `clarification_required`
- `ready_to_plan`
- `planning`
- `paused`
- `completed`
- `failed`

### 4.3 规划与 SSE

#### `POST /api/v1/trips/{trip_id}/planning/start`

将已有 Trip 状态切换为 `planning` 并返回更新后的 `Trip`。当前只负责启动模拟规划，
尚未接入 LangGraph。

#### `GET /api/v1/trips/{trip_id}/planning/events`

返回 `text/event-stream`。当前依次模拟：

1. `planning_started`
2. `node_started`
3. `tool_started`
4. `tool_completed`
5. `progress`
6. `planning_completed`

单条事件格式：

```text
event: progress
data: {"event":"progress","trip_id":"trip_x","node":"build_stages","tool":null,"label":"正在拆分天和阶段","progress":84,"timestamp":"..."}
```

事件只输出可展示的执行进度，不包含模型私有推理内容。

### 4.4 Skill 接口

所有 Skill 接口前缀为 `/api/v1/skills`。

| 方法 | 路径 | 用途 | 当前状态 |
|---|---|---|---|
| GET | `/api/v1/skills/health` | 返回 Adapter 健康状态 | 已实现 |
| POST | `/api/v1/skills/amap/geocode` | 高德地理编码 | 真实 API |
| POST | `/api/v1/skills/amap/driving` | 高德驾车路线规划 | 真实 API |

#### 高德地理编码

`POST /api/v1/skills/amap/geocode`

请求：

```json
{
  "address": "武汉大学",
  "city": "武汉"
}
```

成功结果的 `data` 包含：

- `formatted_address`
- `location`
- `province`
- `city`
- `district`
- `adcode`

地理编码结果默认缓存 30 天。

#### 高德驾车路线

`POST /api/v1/skills/amap/driving`

请求：

```json
{
  "origin": "114.365248,30.537860",
  "destination": "115.983503,29.555963",
  "strategy": 0
}
```

成功结果的 `data` 包含：

- `origin`
- `destination`
- `distance_km`
- `duration_minutes`
- `tolls_cny`
- `polyline`
- `steps`

驾车结果默认缓存 30 分钟。

### 4.5 SkillResult 统一响应

Skill Adapter 统一返回：

```json
{
  "success": true,
  "provider": "高德地图",
  "data": {},
  "warnings": [],
  "sources": [],
  "estimated": false,
  "cache_hit": false,
  "latency_ms": 120,
  "error_code": null
}
```

未配置 WebService Key 时不会抛出不透明异常，而是返回：

```json
{
  "success": false,
  "provider": "高德地图",
  "warnings": ["未配置 AMAP_WEBSERVICE_KEY"],
  "error_code": "SKILL_NOT_CONFIGURED"
}
```

### 4.6 统一业务错误

不存在的 Trip 返回：

```json
{
  "error": {
    "code": "TRIP_NOT_FOUND",
    "message": "行程不存在",
    "details": {
      "trip_id": "trip_x"
    }
  }
}
```

请求字段校验错误继续使用 FastAPI/Pydantic 的 HTTP 422 响应。

## 5. 领域模型

已经建立并导出 JSON Schema 的主要模型：

- `Trip`
- `TripRequest`
- `DayPlan`
- `MovementStage`
- `MovementStage.mode` 当前支持 `driving/transit/walking/riding/taxi/flight/train`；
  公共交通可通过 `transit_type=bus/subway/shuttle` 细分。
- `Activity`
- `VehicleProfile`
- `PlaceRef`
- `RouteSegment`
- `PlanPatch`
- `VerificationIssue`
- `SkillResult`
- `SSEEvent`

Schema 位于 `shared/schemas/`，可用以下命令重新生成：

```powershell
$env:PYTHONPATH='backend'
conda run -n roadman python backend/scripts/export_schemas.py
```

固定验收数据位于：

```text
shared/examples/wuhan-lushan-trip.json
```

## 6. 数据与持久化

- 本地默认数据库：`sqlite+aiosqlite:///./roadman.db`
- Docker 数据库：PostgreSQL
- Trip 当前以完整 JSON 文档方式持久化，Repository 对外提供 CRUD。
- 启动 FastAPI 时自动创建当前所需数据表。
- Redis URL 已纳入配置；当前 Skill Registry 使用进程内缓存，尚未切换 Redis。

## 7. 配置项

复制 `.env.example` 为 `.env` 后可配置：

| 环境变量 | 作用 |
|---|---|
| `APP_ENV` | 运行环境 |
| `DATABASE_URL` | SQLAlchemy 异步数据库地址 |
| `REDIS_URL` | Redis 地址 |
| `AMAP_WEBSERVICE_KEY` | 后端高德 WebService Key |
| `LOAD_LOCAL_SKILL_CREDENTIALS` | 是否允许本地读取 Skill 凭据 |
| `CORS_ORIGINS` | 允许访问后端的前端源 |
| `VITE_AMAP_JSAPI_KEY` | 前端高德 JSAPI Key |
| `VITE_AMAP_SECURITY_JS_CODE` | 前端高德安全密钥 |
| `VITE_AMAP_SERVICE_HOST` | 可选的高德安全代理地址 |

环境变量优先于本地 Skill 凭据。生产环境应使用环境变量或代理服务管理密钥。

## 8. 运行方式

### 8.1 Conda 本地运行

首次安装：

```powershell
conda env create -f environment.yml
conda activate roadman
pip install -r requirements.txt
cd frontend
npm install
```

启动后端：

```powershell
conda activate roadman
$env:PYTHONPATH='backend'
uvicorn app.main:app --reload --port 8000
```

另开终端启动前端：

```powershell
cd frontend
npm run dev
```

访问：

- 前端：`http://localhost:5173`
- 后端健康检查：`http://localhost:8000/health`
- Swagger：`http://localhost:8000/docs`

### 8.2 Docker 运行

```powershell
docker compose up --build
```

访问 `http://localhost:8080`。

## 9. 测试状态

当前已通过：

- 后端 pytest：4 项。
- Playwright：2 个桌面分辨率、共 4 项。
- 前端 TypeScript 与 Vite 生产构建。
- npm 高危依赖审计，当前 0 个漏洞。

测试命令：

```powershell
$env:PYTHONPATH='backend'
conda run -n roadman pytest backend/tests -q

cd frontend
npm run build
npx playwright test
npm audit --audit-level=high
```

浏览器测试覆盖：

- 首页核心入口、账户下拉菜单和 3D 模型加载。
- 规划页天/阶段/节点选择。
- 高德真实道路状态。
- 地图拖动与缩放后标记随地图坐标移动。

## 10. 当前限制与后续事项

- SSE 当前是定时 Mock，未接 LangGraph 工作流。
- 首页自然语言输入暂时进入固定演示，不会生成任意真实行程。
- 语音输入只有 UI 预留按钮，尚未接录音和识别。
- 附件按钮只有 UI 入口。
- 天气、酒店、景点票价、充电站与车辆耗能尚未接入完整实时数据链路。
- Redis、鉴权、用户系统、分享和导出尚未完成。
- 高德 JSAPI 加载受网络、Key 配置、配额和域名白名单影响；真实道路失败时前端
  使用灰色虚线直连，并明确标识为降级显示。
- 后端多交通方式统一编排接口尚未上线，设计见
  `docs/routing-fallback-design.md`。
