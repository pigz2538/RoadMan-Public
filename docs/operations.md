# 运行与运维

权威依据：`docker-compose.yml`、`deploy/`、`deploy/nginx/` 与 `.env.example`。

## Docker Compose 部署

前置：安装并启动 Docker Desktop。

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
# 编辑 .env，至少填写 LLM_API_KEY 与 AMAP_WEBSERVICE_KEY
docker compose up -d --build
docker compose ps
```

`backend` 与 `worker` 通过 Compose 的 `env_file: .env` 读取项目目录中的 `LLM_*` 配置；不会把某个供应商或模型写死在智能体代码里。两者启动后应使用同一组 `LLM_PROVIDER`、`LLM_API_URL`、`LLM_API_KEY`、`LLM_MODEL` 与 `LLM_API_STYLE`。`.env` 已被 `.gitignore` 忽略，禁止提交到仓库或写入日志。

Compose 提供五类服务：`postgres`（PostgreSQL 17）、`redis`（7.4）、`backend`（FastAPI，入容器先 `alembic upgrade head` 再 uvicorn）、`worker`（ARQ）、`frontend`（Nginx）。`frontend` 以 `0.0.0.0:8080:80` 绑定宿主机所有网卡，作为统一 Web 入口；`backend` 仅回环暴露 `127.0.0.1:8000:8000` 供本地工具与 Playwright 使用，浏览器请求全部经 Nginx `/api/` 反向代理到后端。

局域网访问使用运行 Docker 主机的局域网地址，例如 `http://192.168.1.20:8080`。从其他 origin 访问 API 时设置 `CORS_ORIGINS` 为逗号分隔列表。生产环境必须覆盖默认的 `POSTGRES_PASSWORD`。

停止服务：

```powershell
docker compose down
```

仅 `down` 不删除 named volume（`roadman_postgres`、`roadman_redis`、`roadman_uploads`）；需要清理开发数据时才显式 `docker compose down -v`。

## 健康检查与验证

| 端点 | 用途 |
| --- | --- |
| `GET /health` | 服务基本存活，返回 `status`、`environment` 与已注册 Skill 名列表 |
| `GET /api/v1/skills/health` | 各 Skill/Adapter 的配置与降级状态 |
| `GET /api/v1/ops/metrics` | 运行指标：`service`（请求指标快照）+ `skills`（Skill 调用汇总） |
| `GET /api/v1/skills/calls?limit=50` | 最近 Skill 调用审计记录 |
| `GET /api/v1/skills/metrics` | Skill 调用的成功/缓存/延迟/错误汇总 |

Compose 为 postgres、redis、backend 都配置了 healthcheck，服务按依赖顺序在健康后才拉起下游。运行中容器可通过 8000 端口验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/skills/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ops/metrics
```

也可用 Web 入口统一验证：`http://127.0.0.1:8080/health`（经 Nginx 代理）。

## 验收脚本

`deploy/` 下提供三档验证脚本（均运行在宿主机 Python，需 `requests`）：

- `python deploy/api_smoke.py`：对运行中容器做 API 冒烟。覆盖健康检查、各外部能力、创建临时行程/车辆做增删改、上传文件抽取、Job 队列，以及 completed fixture 的五格式导出（Markdown/PDF/PPTX/PNG/HTML）。对地理编码、路线、POI、天气、车型等必须有成功结果的探针使用 `require_success`；住宿/轮船等允许合法“无库存/无班次”，脚本仍检查响应契约但不会误报。脚本只打印状态/汇总字段，绝不打印凭据或响应 payload，并在结束时清理临时 trip 与 vehicle。
- `python deploy/full_journey_acceptance.py`：完整旅程验收，比冒烟更强。等待新行程规划完成，检查面向旅客的完整性，请行程助手做一次语义替换并确认预览，核对重算后的快照，并下载全部导出格式。支持 `--base-url` 指向远程 API。
- `python deploy/edit_replan_acceptance.py`：语义编辑与重规划验收，覆盖编辑预览→确认/驳回→重算链路。

这些脚本不打印「N passed」形式的测试统计，而是以 `PASS/FAIL` 标记逐项结果，任何 FAIL 都会返回非零退出码。

## 备份与恢复

`deploy/scripts/backup.ps1` 通过 `pg_dump --clean --if-exists` 把 PostgreSQL 导出为带时间戳的 SQL（默认输出到 `./backups`）：

```powershell
.\deploy\scripts\backup.ps1
.\deploy\scripts\backup.ps1 -OutputDirectory 'D:\backups'
```

`deploy/scripts/restore.ps1` 用 `psql -v ON_ERROR_STOP=1` 恢复，恢复会覆盖当前数据库，须显式确认：

```powershell
.\deploy\scripts\restore.ps1 -BackupFile '.\backups\roadman-20260816-120000.sql' -ConfirmRestore
```

流程：恢复前先停止或隔离前端写入（`docker compose stop backend worker`），恢复后重启并确认 `/health` 与 `messages_json` 等服务一致。

## Nginx 与 HTTPS

`deploy/nginx/default.conf`（打包进 frontend 镜像）把 `/api/` 与 `/health` 反代到 `backend:8000`，`proxy_buffering off` 保证 SSE 事件流不被缓冲；`/assets/` 走不可变长缓存，其余 SPA 路由回退 `index.html` 并禁止缓存。

生产 HTTPS 使用 `deploy/nginx/https.conf.template`：它把 80 端口 301 跳转到 443，HTTP/2 + TLS1.2/1.3，主题变量 `ROADMAN_SERVER_NAME`，证书路径 `/etc/nginx/certs/fullchain.pem` 与 `privkey.pem`。启用方式：把模板渲染为 `default.conf`（替换 `ROADMAN_SERVER_NAME`），并把证书挂载到 frontend 容器的 `/etc/nginx/certs/`，重建 frontend。

## 本地 Conda 运行

```powershell
conda env create -f environment.yml
conda activate roadman
pip install -r requirements.txt
$env:PYTHONPATH = 'backend'
alembic -c backend/alembic.ini upgrade head
uvicorn app.main:app --reload --port 8000
```

前端（另开终端）：

```powershell
cd frontend
npm install
npm run dev
```

本地默认 SQLite。异步规划依赖 Redis 与 worker，建议仅用 Compose 拉起依赖：

```powershell
docker compose up -d postgres redis worker
```

## Provider 配置与验证

后端环境变量：`AMAP_WEBSERVICE_KEY`、`OPENTRIPMAP_API_KEY`、`FLYAI_API_KEY`、`LLM_PROVIDER`、`LLM_API_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_API_STYLE`、`LLM_THINKING`、`LLM_MAX_TOKENS`、`LLM_TIMEOUT_SECONDS`。前端构建变量：`VITE_AMAP_JSAPI_KEY`、`VITE_AMAP_SECURITY_JS_CODE`、`VITE_AMAP_SERVICE_HOST`。

可用 `deploy/sync-local-secrets.ps1` 把本地位于 `Skills/**/apikey.txt`、`secretkey.txt`、`apipkey.txt` 的本地凭据同步进被 git 忽略的 `.env`（脚本不打印值）。若让 `LOAD_LOCAL_SKILL_CREDENTIALS=true`，后端会读取本地 Skill 凭据文件；Docker Compose 默认设为 `false`，数据一律走注入的环境变量。

不配置某 provider 时，对应 Skill 返回 `SKILL_NOT_CONFIGURED` 或空候选并继续可解释的降级流程；不要把 key 打印进文档、日志、测试输出或导出物。

Skill 冒烟示例：

```powershell
$body = @{ query = 'Model 3'; limit = 5 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8080/api/v1/skills/carinfo/search -Method Post -ContentType 'application/json' -Body $body
Invoke-RestMethod http://127.0.0.1:8080/api/v1/skills/health
Invoke-RestMethod http://127.0.0.1:8080/api/v1/ops/metrics
```

FlyAI CLI（本地需要时）：

```powershell
npm install -g @fly-ai/flyai-cli
flyai --help
flyai config set FLYAI_API_KEY "<local-key>"
```

## 测试与构建

```powershell
$env:PYTHONPATH = 'backend'
python -m compileall -q backend/app backend/tests
pytest backend/tests -q
cd frontend
npm run build
npm run test:e2e
```

`npm run build` 会执行 `vue-tsc` 类型检查并使用构建时注入的 `VITE_AMAP_*`。e2e 用 Playwright 对打包产物跑真实浏览器验收。

## 常见故障

- **地图显示 Mock**：`VITE_AMAP_JSAPI_KEY` / `VITE_AMAP_SECURITY_JS_CODE` 必须在构建时传入，修改后须 `docker compose up -d --build` 重建 frontend 镜像。
- **路线为空**：查看 `/api/v1/skills/calls` 与 `/api/v1/skills/metrics`，确认高德 key、坐标与允许的交通方式；全部模式失败时前端灰虚线只是提示，不是导航路线。
- **规划停在队列**：检查 `docker compose logs worker`、Redis 健康状态与 `/api/v1/jobs/{job_id}`。
- **天气/旅游候选缺失**：查对应 Skill 的 `success/error_code`，不要把 provider 未配置误判为没有景点。
- **数据库迁移失败**：确认 Postgres 健康后重启 backend；不要以删除 volume 作为首选修复，备份见上文。
- **SSE 不更新**：确认 Nginx 对 `/api/` 已 `proxy_buffering off`；若自定义 Nginx，勿对事件流缓冲。

## 凭据安全

生产凭据只允许写入被 git 忽略的 `.env`，并作为环境变量注入容器；Skill 调用审计（`/api/v1/skills/calls`）只记录 adapter/provider/success/cache/latency/error_code 等元数据，SKILL 响应与 key 不落日志。`Skills/**/apikey.txt` 等本地凭据文件被 `.gitignore` 与容器策略排除。若旧 Git 历史曾含凭据，部署前须轮换并收紧远端仓库访问权限。导出的路书与冒烟日志同样不得包含 provider key 或原始响应。
