# 运行依赖

RoadMan 支持两种运行方式：Docker Compose（推荐，生产风格）与本地 Conda（开发调试）。两套依赖表的权威来源是 `docker-compose.yml`、`backend/Dockerfile`、`frontend/Dockerfile`、`backend/requirements.txt`、`backend/node-requirements.txt`、`environment.yml` 与 `.env.example`。

## 核心运行时

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Python | 3.11（`python:3.11-slim`；Conda `python=3.11`） | 后端 FastAPI 与 ARQ worker |
| Node.js | 22（`node:22-slim`；Conda 要求 `nodejs>=20`） | 前端 Vite 构建；承载 FlyAI CLI |
| PostgreSQL | 17（`postgres:17-alpine`） | 生产数据库 |
| Redis | 7.4（`redis:7.4-alpine`） | ARQ 任务队列 |
| Nginx | 1.27（`nginx:1.27-alpine`） | 前端静态托管与统一 Web 入口 |

## Python 依赖（backend/requirements.txt）

后端核心：FastAPI（≥0.115）、uvicorn、Pydantic 2（≥2.10）、pydantic-settings、SQLAlchemy 2.0 async、Alembic、aiosqlite（本地 SQLite）、asyncpg（生产 PostgreSQL）、redis、arq（worker）、httpx、structlog、jsonschema、langgraph。文档与导出：pypdf、python-docx、openpyxl、reportlab、python-pptx、Pillow。开发与测试：pytest、pytest-asyncio。

`backend/Dockerfile` 的 Python 镜像安装中文字体 `fonts-noto-cjk` 与 `fonts-arphic-ukai`，供导出 PDF/长图等场景正确渲染中文。

## Node 依赖

- 前端：`frontend/package.json`，`npm ci` 安装，`npm run build` 用 Vite 生产构建。
- FlyAI CLI：`backend/node-requirements.txt` 固定 `@fly-ai/flyai-cli@1.0.16`。Docker 后端镜像从一个 `node:22-slim` 阶段全局安装该 CLI，并把 `node`、`flyai` 与 `@fly-ai` 模块复制进 Python 镜像，供 travel/hotel 等 Skill 调用。本地开发如需 FlyAI 能力，单独安装 CLI 并配置 key。

## 外部能力与凭据

所有凭据以环境变量注入，权威键名与默认值见 `.env.example` 与 `docker-compose.yml`。

| 变量 | 用途 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 需求理解、目的地研究、语义编辑等云端智能体 | 必需；默认模型 `DEEPSEEK_MODEL=deepseek-v4-flash` |
| `DEEPSEEK_REASONING_EFFORT` | 云端智能体思考深度 | 默认 `max` |
| `DEEPSEEK_THINKING` | 是否启用思考模式 | 默认 `true` |
| `DEEPSEEK_API_URL` | DeepSeek 官方 Chat Completions 地址 | 默认 `https://api.deepseek.com/chat/completions` |
| `AMAP_WEBSERVICE_KEY` | 真实驾车/步行/骑行/公交路线、地理编码、POI | 必需；缺失则上述能力降级 |
| `FLYAI_API_KEY` | 旅行搜索、住宿、餐饮补充 | 推荐 |
| `OPENTRIPMAP_API_KEY` | 国际/开放景点数据补充 | 可选 |
| `VITE_AMAP_JSAPI_KEY` | 浏览器真实地图 | 构建时注入，改动后需重建 frontend |
| `VITE_AMAP_SECURITY_JS_CODE` | 浏览器地图安全密钥 | 构建时注入 |
| `VITE_AMAP_SERVICE_HOST` | 高德 JS API 加载器的同源代理主机 | 可选 |
| `ROADMAN_HTTP_PROXY` | 容器访问外网所需的宿主机代理 | 可选；Docker Desktop 用 `http://host.docker.internal:<port>` |
| `CORS_ORIGINS` | 允许的跨域 origin，逗号分隔 | 默认 `http://localhost:8080` |
| `POSTGRES_*` | Compose 数据库账号/库名 | 生产需覆盖默认密码 |

缺少非必需 Key 时对应能力自动降级（如无浏览器地图 Key 时前端退回 Mock 地图），不影响主流程。`LOAD_LOCAL_SKILL_CREDENTIALS` 控制是否从本地读取 Skill 凭据文件；Compose 中设为 `false`（`LOAD_LOCAL_SKILL_CREDENTIALS: "false"`），数据一律走注入的环境变量。

## Docker 运行（推荐）

`docker-compose.yml` 定义五类服务：

1. `postgres`（PostgreSQL 17，named volume `roadman_postgres`，含健康检查）
2. `redis`（Redis 7.4，开启 AOF，volume `roadman_redis`）
3. `backend`（FastAPI；启动命令先 `alembic upgrade head` 再 uvicorn；仅回环暴露 `127.0.0.1:8000:8000` 供本地工具与 Playwright 使用）
4. `worker`（`arq app.workers.main.WorkerSettings`，异步规划执行）
5. `frontend`（Nginx 托管构建产物，`0.0.0.0:8080:80` 作为统一 Web 入口）

服务依赖关系保证顺序启动并健康后才拉起下游：backend 依赖 postgres+redis，worker 依赖 backend+postgres+redis，frontend 依赖 backend。后端与 worker 共享 `UPLOAD_DIR=/app/data/uploads` 卷与 `FILE_RETENTION_DAYS`。

```powershell
Copy-Item .env.example .env   # 至少填入 DEEPSEEK_API_KEY 与 AMAP_WEBSERVICE_KEY
docker compose up -d --build
docker compose ps
```

详细启动、健康检查、备份恢复与排障见 [operations.md](operations.md)。

## Conda 本地运行（开发）

`environment.yml` 创建名为 `roadman` 的 Conda 环境：Python 3.11 + pip + `nodejs>=20`，并通过 pip 读取 `-r backend/requirements.txt`。本地默认用 SQLite（`.env.example` 中 `DATABASE_URL=sqlite+aiosqlite:///./roadman.db`）。

```powershell
conda env create -f environment.yml
conda activate roadman
pip install -r requirements.txt
$env:PYTHONPATH = 'backend'
alembic -c backend/alembic.ini upgrade head
uvicorn app.main:app --reload --port 8000
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

异步规划依赖 Redis 与 worker，因此本地调试建议用 Compose 只拉起依赖服务，后端/前端跑在宿主机：

```powershell
docker compose up -d postgres redis worker
```
