# 运行依赖

## Python/Conda

```powershell
conda env create -f environment.yml
conda activate roadman
pip install -r requirements.txt
```

`environment.yml` 固定 Python 3.11，并安装 `backend/requirements.txt` 中的 FastAPI、SQLAlchemy、Alembic、ARQ、LangGraph、导出和测试依赖。

## Node

需要 Node.js 20+（Docker 使用 Node 22）。

```powershell
cd frontend
npm install
npm run build
```

## 外部能力

- Ollama cloud：设置 `OLLAMA_API_KEY`，默认模型 `deepseek-v4-flash:0731-cloud`。
- FlyAI：Docker backend 已包含 CLI；本地可执行 `npm install -g @fly-ai/flyai-cli`，并设置 `FLYAI_API_KEY` 或运行 `flyai config`。
- AMap Web Service/OpenTripMap：分别设置 `AMAP_WEBSERVICE_KEY`、`OPENTRIPMAP_API_KEY`。
- AMap JSAPI：构建 frontend 时设置 `VITE_AMAP_JSAPI_KEY`、`VITE_AMAP_SECURITY_JS_CODE`；没有 key 使用 Mock 地图。

## Docker

Compose 提供 PostgreSQL 17、Redis 7、FastAPI backend、ARQ worker 和 Nginx frontend。详细启动/健康检查/故障排查见 [operations.md](operations.md)。

所有 key 都是环境变量；`Skills/**/apikey.txt` 等仅为本地凭据并被 Git/Docker 忽略。
