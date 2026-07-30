# RoadMan

RoadMan 是面向周末自驾与中短途旅行的智能路书工作台。当前已完成总规划阶段 D：
自然语言需求经 Ollama Requirement Agent 和 LangGraph 进入可追问、可取消、可恢复的
规划闭环，并生成真实高德往返路线、跨天阶段与 Markdown 路书。

## 一键运行

```powershell
docker compose up --build
```

访问 `http://localhost:8080`。Compose 会启动 PostgreSQL、Redis、FastAPI、ARQ
Worker 和 Nginx/Vue；Backend 启动时先执行 Alembic 迁移。

## Conda 本地开发

```powershell
conda env create -f environment.yml
conda activate roadman
pip install -r requirements.txt
$env:PYTHONPATH='backend'
alembic -c backend/alembic.ini upgrade head
uvicorn app.main:app --reload --port 8000
```

另开终端：

```powershell
cd frontend
npm install
npm run dev
```

前端为 `http://localhost:5173`，API 文档为 `http://localhost:8000/docs`。

## 验证

```powershell
$env:PYTHONPATH='backend'
conda run -n roadman pytest backend/tests -q
conda run -n roadman python backend/scripts/export_schemas.py
cd frontend
npm run build
npm run test:e2e
```

密钥应通过环境变量注入。开发环境可使用 `Skills/` 下的本地凭据，但密钥不会写入
Git、日志、API 响应或 SkillCall 审计。

Agent 使用：

```powershell
$env:OLLAMA_API_KEY='...'
$env:OLLAMA_MODEL='deepseek-v4-flash:cloud'
```

当前状态、完整接口和阶段验收分别见：

- [`project.md`](project.md)
- [`docs/api-contract.md`](docs/api-contract.md)
- [`docs/backend-phase-c-plan.md`](docs/backend-phase-c-plan.md)
- [`docs/backend-phase-d-plan.md`](docs/backend-phase-d-plan.md)
