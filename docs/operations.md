# 运行与运维

## Docker Compose

```powershell
Copy-Item .env.example .env
# 编辑 .env，至少填写需要的 AMAP/FLYAI/OLLAMA key
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://127.0.0.1:8080/health
```

局域网访问时使用运行 Docker 主机的局域网地址，例如 `http://192.168.1.20:8080`。如从其他 origin 访问 API，设置 `CORS_ORIGINS` 为逗号分隔列表。不要把 PostgreSQL 默认密码用于生产环境，生产部署应覆盖 `POSTGRES_PASSWORD`。

停止服务：

```powershell
docker compose down
```

仅删除容器不会删除 named volume；需要清理开发数据时再显式执行 `docker compose down -v`。

## 本地 Conda

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

## Provider 配置与验证

后端读取：`AMAP_WEBSERVICE_KEY`、`OPENTRIPMAP_API_KEY`、`FLYAI_API_KEY`、`OLLAMA_API_KEY`、`OLLAMA_MODEL`。
前端构建读取：`VITE_AMAP_JSAPI_KEY`、`VITE_AMAP_SECURITY_JS_CODE`、`VITE_AMAP_SERVICE_HOST`。

FlyAI CLI（本地需要时）：

```powershell
npm install -g @fly-ai/flyai-cli
flyai --help
flyai config set FLYAI_API_KEY "<local-key>"
flyai keyword-search --query "南京必去景点"
```

不配置 provider 时，系统应返回 `SKILL_NOT_CONFIGURED` 或空候选并继续可解释的降级流程；不要在文档、日志或测试输出中打印 key。

Skill 冒烟：

```powershell
$body = @{ query = 'Model 3'; limit = 5 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8080/api/v1/skills/carinfo/search -Method Post -ContentType 'application/json' -Body $body
Invoke-RestMethod http://127.0.0.1:8080/api/v1/skills/health
Invoke-RestMethod http://127.0.0.1:8080/api/v1/skills/metrics
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

## 常见故障

- **地图显示 Mock**：检查 `VITE_AMAP_JSAPI_KEY` 是否在构建时传入；修改后必须重新构建 frontend 镜像。
- **路线为空**：查看 `/api/v1/skills/calls` 和 `/api/v1/skills/metrics`，确认高德 key、坐标和允许的交通方式；全部模式失败时灰色虚线只是提示，不是导航路线。
- **规划停在队列**：检查 `docker compose logs worker`、Redis 健康状态和 `/api/v1/jobs/{job_id}`。
- **天气/旅游候选缺失**：检查对应 Skill 的 `success/error_code`，不要把 provider 未配置误判为没有景点。
- **数据库迁移失败**：确认 Postgres 健康后重启 backend；不要删除 volume 作为首选修复。

## 凭据安全

本地 `Skills/**/apikey.txt`、`secretkey.txt`、`apipkey.txt` 已被 `.gitignore` 和 `.dockerignore` 排除。前端不再读取这些文件，Docker 也不再挂载它们。旧 Git 历史可能含有曾经的 key，部署前必须轮换并限制远端仓库访问权限。
