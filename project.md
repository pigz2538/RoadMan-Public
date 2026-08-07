# RoadMan 项目说明（当前版）

本文是代码仓库的维护入口，描述当前实现而不是历史开发日志。产品原始范围和阶段验收标准仍以 [RoadMan_分阶段实施总规划.md](RoadMan_分阶段实施总规划.md) 为准。

## 1. 当前状态

RoadMan 已形成从“自然语言需求”到“可确认、可追踪、可编辑、可导出的行程”的后端闭环，前端提供首页、规划进度、详情地图、阶段卡片、候选 POI 和 Agent 编辑面板。

已落地的主线能力：

- Trip 数据按 `天 → MovementStage / Activity` 组织，使用 PostgreSQL（生产/Docker）或 SQLite（测试/本地）和 Alembic 迁移。
- `preflight` 先做语义需求提取、日期/地点/交通约束校验和必要追问；确认前不会开始规划。
- 明确的“出发—抵达”时钟窗口会作为硬约束复核；返程目标允许 15 分钟静默误差、半天内只给 warning，超过半天才阻断。
- 跨海只在用户明确写出跨海语义时保留安全约束，轮渡/飞机/桥梁方式不会由模型凭空猜定，需用户确认或明示。
- LangGraph 规划工作流会执行路线、目的地研究、POI、天气、补能/服务、每日复核和持久化节点，并通过 ARQ + Redis 异步运行。
- Skill Registry 统一接入高德、Open-Meteo、FlyAI、OpenTripMap、车辆目录和 Ollama；每次调用可审计、可缓存并可降级。
- 高德路线优先使用真实道路 geometry；驾车不可用时按策略尝试骑行、步行或公共交通，全部失败才返回 `ROUTE_UNAVAILABLE`，前端灰色虚线只表示不可用提示。
- 当前阶段路线高亮，其他路线灰显；地图支持平移/缩放、阶段切换和点选；路线几何跟随地图变换。
- 每日复核覆盖移动时间、活动冲突、餐饮/住宿、天气和季节适配；发现可由系统修复的问题时由编排 Agent 自动重排并循环复核（最多 4 轮），不会第一次失败就把修复责任推给用户；只有仍无法满足的硬约束才需要用户介入。
- 编辑采用 `preview → apply/reject → rollback` 的 PlanPatch 两阶段流程，支持自然语言修改、地图选点、候选景点/酒店/餐饮加入或替换。
- 规划完成后才能导出 Markdown、HTML、PDF、PPTX、PNG；历史行程从数据库恢复，不重新播放规划动画。
- 车辆管理支持 CRUD，并提供 `carinfo.catalog` 真实车型搜索；本地车型字段由用户确认后保存。
- 请求 ID、追踪 ID、速率限制、Skill 调用记录、服务指标和 Docker 健康检查已接入。

阶段对应关系：代码覆盖总规划中 A–I 的主要基线（骨架、前端工作台、真实 Skill、规划闭环、自驾深度、旅游 POI、局部编辑、附件/导出、工程化部署）；J 是后续增强范围，不把未验证的外部商业能力标成已完成。

## 2. 目录职责

```text
backend/app/
  api/          FastAPI 路由与请求边界
  domain/       Pydantic/SQLAlchemy 领域模型
  planning/     LangGraph、需求提取、研究、编排、复核、编辑
  skills/       第三方能力 Adapter 与 Registry
  repositories/ 持久化与版本读写
  services/     队列、SSE、导出、审计和观测
frontend/src/
  views/        首页、规划/详情页、运维页
  components/   地图、阶段卡片、活动列表、Agent 面板
  stores/       Pinia 行程状态与历史恢复
shared/
  schemas/      对外/跨进程 JSON Schema
docs/           当前接口、模型、运行和降级说明
Skills/         provider skill 指南与本地开发参考（不放密钥）
```

## 3. 核心运行流程

### 3.1 需求到规划

1. 前端提交 `POST /api/v1/trips/preflight`。
2. Requirement Agent 解释地点、日期、人数、交通方式、偏好和特殊活动；结构化解析只负责明确日期/数字/显式地点等安全底线。
3. 系统研究命名活动（例如天文事件）并返回来源、观测窗口和待确认项。
4. 所有问题解决后，用户确认；前端创建 Trip 并调用 `/{trip_id}/planning/start`。
5. Worker 执行 LangGraph，阶段进度写入 PlanningState/Job，并通过 `planning/events` SSE 推送。
6. 行程逐步持久化；每日复核和最终验证通过后才将 Trip 标为 `completed`。

### 3.2 编辑到重算

自然语言编辑先由 Editing Agent 解释为候选操作。后端创建 PlanPatch 预览并计算影响日期/阶段、时间变化和路线重算范围。只有用户确认 `apply` 才写入 canonical Trip；失败不写入，并保留可回滚版本。

### 3.3 路线降级

`amap.route` 接受 `preferred_mode` 和 `allowed_fallback_modes`。真实 geometry 优先；无 geometry 的结果不作为道路使用。跨城驾车失败时不会错误地改成市内公交，只有请求允许且起终点同城才尝试公共交通；所有模式失败返回结构化错误。

## 4. 接口概览

详细字段以 [docs/api-contract.md](docs/api-contract.md) 和 OpenAPI 为准。

| 领域 | 主要接口 |
| --- | --- |
| 行程 | `POST/GET /api/v1/trips`、`GET/PATCH/DELETE /api/v1/trips/{id}` |
| 预检 | `POST /api/v1/trips/preflight` |
| 规划 | `POST /api/v1/trips/{id}/planning/start`、`GET /planning`、`POST /planning/clarifications`、`GET /planning/events` |
| 编辑 | `/editing/interpret`、`/patches/preview*`、`/patches/{patch_id}/apply|reject|rollback` |
| 导出 | `/roadbook`、`.html`、`.pdf`、`.pptx`、`.png`（仅 completed） |
| 资源 | `/recommendations`、`/risks`、`/services`、附件与版本接口 |
| Skill | `/api/v1/skills/health`、`/calls`、`/metrics` 及 amap/weather/flyai/opentripmap/carinfo 路由 |
| 车辆 | `POST/GET /api/v1/vehicles`、`GET/PATCH/DELETE /api/v1/vehicles/{id}` |

统一错误格式含 `code`、`message`、`details`、`request_id`；响应会回传 `X-Request-ID` 和 `X-Trace-ID`。

## 5. Provider 与凭据

| 能力 | Adapter | 凭据/降级 |
| --- | --- | --- |
| 高德地理/路线/POI | `amap.*` | `AMAP_WEBSERVICE_KEY`；无 key 返回 `SKILL_NOT_CONFIGURED` |
| 高德浏览器地图 | AMap JSAPI | 构建参数 `VITE_AMAP_*`；无 key 使用 Mock 地图 |
| 天气 | `open_meteo.forecast` | 无需 key；失败保留明确估算/不可用标记 |
| 旅游搜索 | `flyai.*` | `FLYAI_API_KEY` 和 CLI；失败降级为其他来源，不伪造结果 |
| 景点补充 | `opentripmap.nearby` | `OPENTRIPMAP_API_KEY`；结果需来源追溯 |
| 车型目录 | `carinfo.catalog` | 远端目录失败时返回可解释的空结果 |
| 语义 Agent | Ollama cloud | `OLLAMA_API_KEY`、`OLLAMA_MODEL`；未配置时使用安全的离线解析/默认值 |

所有凭据都通过环境变量注入。`Skills/**/apikey.txt` 等文件是本地参考文件，已从 Git 索引和 Docker context 移除；历史提交可能仍有旧 key，部署前必须轮换。

## 6. 数据、队列和部署

- PostgreSQL 保存 Trip、版本、规划状态、任务、技能调用和上传文件元数据。
- Redis + ARQ 执行异步规划；API 与 Worker 共用 PlanningState。
- 上传内容在 `UPLOAD_DIR`，数据库只保存安全文件名和元数据；默认扩展名/MIME 白名单见配置。
- Compose 入口是 `8080`，后端容器内部 `8000`；前端 Nginx 反代 `/api` 到后端。
- 通过 `CORS_ORIGINS`、`POSTGRES_*`、`ROADMAN_HTTP_PROXY` 等变量覆盖部署参数。

运行细节和 provider 冒烟命令见 [docs/operations.md](docs/operations.md)。

## 7. 验证清单

提交前至少执行：

```powershell
$env:PYTHONPATH = 'backend'
python -m compileall -q backend/app backend/tests
pytest backend/tests -q
cd frontend
npm run build
npm run test:e2e
```

需要真实 provider 时，再执行 Docker 健康检查和对应 Skill API 冒烟。没有凭据的环境也必须能通过测试，并返回可解释的降级结果。

## 8. 维护原则

1. 不把 provider 原始密钥、模型私有思维或第三方响应中的敏感字段写入日志、SSE 或导出文件。
2. 任何外部数据都带来源和时间；估算值必须显式标记，不能冒充实时结果。
3. 真实路线缺失时显示不可用，不用直线 geometry 冒充道路。
4. 先 preview 后 apply；编辑失败不得破坏 canonical Trip。
5. 文档只保留当前契约和可复现操作；阶段草稿、重复审计和一次性截图不再作为运行依据。
