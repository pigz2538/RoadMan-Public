# RoadMan

RoadMan 是一个面向自驾与中短途旅行的智能行程规划工作台。它把自然语言需求、地图/天气/旅游搜索、路线降级、日程校验、人工确认和导出整合在一个可追踪的规划流程中。

## 快速开始

推荐使用 Docker Compose：

```powershell
Copy-Item .env.example .env
# 在 .env 中填写需要使用的 provider key
docker compose up -d --build
```

打开 <http://localhost:8080>；后端 OpenAPI 在 <http://localhost:8000/docs>，健康检查在 <http://localhost:8080/health>。

本地 Conda 开发：

```powershell
conda env create -f environment.yml
conda activate roadman
pip install -r requirements.txt
$env:PYTHONPATH = 'backend'
alembic -c backend/alembic.ini upgrade head
uvicorn app.main:app --reload --port 8000
```

另开终端启动前端：

```powershell
cd frontend
npm install
npm run dev
```

## 重要配置

- 后端 key：`AMAP_WEBSERVICE_KEY`、`OPENTRIPMAP_API_KEY`、`FLYAI_API_KEY`、`OLLAMA_API_KEY`。
- Agent 模型默认是 `deepseek-v4-flash:0731-cloud`，可用 `OLLAMA_MODEL` 覆盖。
- 浏览器高德 JSAPI 只能通过 `VITE_AMAP_JSAPI_KEY`、`VITE_AMAP_SECURITY_JS_CODE` 在构建时注入。
- `Skills/**/apikey.txt`、`secretkey.txt`、`apipkey.txt` 只允许作为本地凭据，已加入忽略规则，不要提交。

完整变量模板见 [.env.example](.env.example)。

## 常用验证

```powershell
$env:PYTHONPATH = 'backend'
pytest backend/tests -q
cd frontend
npm run build
npm run test:e2e
```

Docker 运维、局域网访问和 provider 验证见 [docs/operations.md](docs/operations.md)。

## 文档入口

- [project.md](project.md)：当前实现、边界、接口和维护状态。
- [docs/README.md](docs/README.md)：精简后的文档索引。
- [docs/api-contract.md](docs/api-contract.md)：HTTP/SSE 接口契约。
- [docs/domain-model.md](docs/domain-model.md)：Trip、阶段、活动和补丁模型。
- [RoadMan_分阶段实施总规划.md](RoadMan_分阶段实施总规划.md)：原始总规划（只读基线）。

## 安全提醒

历史版本曾把 provider key 文件纳入 Git。当前版本已停止跟踪并从构建流程移除，但旧提交仍可能包含这些凭据；部署前请在各 provider 控制台轮换曾使用过的 key，并检查远端仓库的访问权限。
