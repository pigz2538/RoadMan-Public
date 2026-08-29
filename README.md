# RoadMan

RoadMan 是面向自驾与中短途旅行的多智能体行程工作台。用自然语言描述出发地、目的地、日期、同行人和偏好，系统会自动完成需求核对、目的地研究、分区计划单、真实路线查询、每日食宿行编排、车辆补能与驾驶休息安排、自动复核修复，并输出可导出的完整路书。

地点理解由云端需求智能体负责。它会把“新疆”“南京”“西藏和新疆”等行政区/城市/多目的地保留为结构化范围，先检索目的地的著名地标与代表性美食，再由目的地策划智能体生成每日计划单，最后才调用地图和交通工具。模型不可用或返回数组字符串等非法地点结构时，系统会暂停并提示重试，不会用关键词把目的地猜成餐馆、校园或酒店。

- 规划在后台持续执行，地图、阶段、景点、餐饮、住宿和进度实时逐步呈现
- 支持自然语言修改和地图选点，先预览影响、确认后重算，可回滚
- 行程可导出为 HTML、PDF、PPTX、长图和 Markdown
- 长途自驾自动按每日驾驶上限拆分，安排服务区休息、充电/加油和沿途过夜住宿，次日早晨继续行驶

## 部署（Docker，推荐）

前置要求：安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/) 并启动。

**第一步：准备配置**

```powershell
Copy-Item .env.example .env
```

用编辑器打开 `.env`，至少填入两项（其余按需，完整说明见下方配置表）：

```text
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_REASONING_EFFORT=max
DEEPSEEK_THINKING=true
AMAP_WEBSERVICE_KEY=你的高德 WebService Key
```

如需浏览器内显示真实地图，再填入 `VITE_AMAP_JSAPI_KEY` 和 `VITE_AMAP_SECURITY_JS_CODE`。

**第二步：启动**

```powershell
docker compose up -d --build
```

首次启动会构建镜像并初始化数据库，耗时几分钟。

**第三步：验证**

```powershell
python deploy/api_smoke.py
```

冒烟脚本通过即表示容器、数据库、队列、接口契约和当前能访问的外部能力已完成逐项检查。它不会把“旅行信息服务合法无库存”误判成故障；需求理解/语义编辑会用有效的 DeepSeek Key 单独验证。

模型 Key 的最小验证（只检查状态，不打印 Key）：

```powershell
Invoke-RestMethod https://api.deepseek.com/chat/completions -Method Post -Headers @{ Authorization = "Bearer $env:DEEPSEEK_API_KEY"; "Content-Type" = "application/json" } -Body (@{ model = "deepseek-v4-flash"; messages = @(@{ role = "user"; content = "return JSON: { ok: true }" }); response_format = @{ type = "json_object" }; thinking = @{ type = "enabled" }; reasoning_effort = "max" } | ConvertTo-Json -Depth 5)
```

如果 Chat Completions 请求返回 401/403，说明账号授权或额度不可用；RoadMan 会明确暂停语义步骤，不会用关键词猜地点。

**第四步：使用**

- Web 工作台：<http://localhost:8080>
- 局域网内其他设备：`http://本机局域网IP:8080`
- 后端接口文档：<http://localhost:8000/docs>

**常用运维命令**

```powershell
docker compose ps                 # 查看服务状态
docker compose logs -f backend    # 跟踪后端日志
docker compose down               # 停止全部服务
docker compose up -d --build      # 更新代码后重新构建
```

数据库备份与恢复、HTTPS、局域网与排障详见 [docs/operations.md](docs/operations.md)。

## 配置项

所有配置写入根目录 `.env` 文件，完整模板与注释见 [.env.example](.env.example)。

| 变量 | 用途 | 是否必需 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 需求理解、目的地研究、语义编辑等云端智能体 | 是 |
| `AMAP_WEBSERVICE_KEY` | 地理编码、POI、真实路线查询 | 是 |
| `VITE_AMAP_JSAPI_KEY` | 浏览器端真实地图（构建时注入，改动后需重新构建） | 推荐 |
| `VITE_AMAP_SECURITY_JS_CODE` | 浏览器地图安全密钥 | 推荐 |
| `FLYAI_API_KEY` | 旅行搜索、住宿、餐饮补充 | 推荐 |
| `OPENTRIPMAP_API_KEY` | 国际/开放景点数据补充 | 可选 |
| `DEEPSEEK_MODEL` | 官方模型，默认 `deepseek-v4-flash` | 可选 |
| `DEEPSEEK_REASONING_EFFORT` | 思考深度，默认 `max` | 可选 |
| `DEEPSEEK_THINKING` | 是否启用思考模式，默认 `true` | 可选 |
| `DEEPSEEK_API_URL` | 官方 Chat Completions 地址 | 可选 |
| `ROADMAN_HTTP_PROXY` | 容器访问外网所需的宿主机代理，如 `http://host.docker.internal:7890` | 可选 |

缺少非必需 Key 时对应能力自动降级（例如无浏览器地图 Key 时使用简化地图视图），不影响主流程。

DeepSeek 接口采用官方 OpenAI 兼容 Chat Completions 协议：请求使用
`messages`、`response_format=json_object`、`thinking=enabled` 与
`reasoning_effort=max`，响应读取 `choices[0].message.content`；模型私有思维链不保存。
详见 [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/) 与
[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/) 官方文档。

## 本地开发（Conda）

后端：

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

本地开发默认使用 SQLite。异步规划依赖 Redis 与 worker，建议用 Compose 跑 PostgreSQL、Redis 和 worker，只在宿主机启动需要调试的服务：

```powershell
docker compose up -d postgres redis worker
```

## 测试与验收

```powershell
$env:PYTHONPATH = 'backend'
pytest backend/tests -q                  # 后端测试

cd frontend
npm run test                             # 前端单元测试
npm run build                            # 前端生产构建
npm run test:e2e                         # 前端 E2E（Playwright）

python deploy/api_smoke.py               # 运行中容器 API 冒烟
python deploy/full_journey_acceptance.py # 完整旅程验收（建行程→规划→修改→导出）
python evaluation/run_evals.py           # 需求理解评测（12 条场景）
```

真实云端智能体的浏览器验收默认不阻断离线回归；配置有效 Key 后显式开启：

```powershell
$env:ROADMAN_RUN_LIVE_AGENT_E2E = '1'
npm run test:e2e --prefix frontend -- tests/e2e/planning-agent.spec.ts
```

## 项目结构

```text
backend/     FastAPI + LangGraph 后端、ARQ worker、数据库迁移、测试
frontend/    Vue 3 + Vite 前端、E2E 测试
shared/      前后端共享的 JSON Schema 契约与示例行程
Skills/      外部能力技能包（高德、天气、旅行搜索、车型、景点）
deploy/      冒烟与验收脚本、Nginx 配置、备份恢复
evaluation/  需求理解评测场景与打分脚本
docs/        接口契约、领域模型、部署运维等文档
submission/  参赛方案书与生成审计工具
```

## 文档

- [project.md](project.md)：系统架构、规划工作流、接口与数据边界
- [docs/README.md](docs/README.md)：全部维护文档索引
- [docs/api-contract.md](docs/api-contract.md)：HTTP/SSE 接口契约
- [docs/mobility-and-poi-data-contract.md](docs/mobility-and-poi-data-contract.md)：地点事实、票务预约、停车、公共交通与跨城班次的数据契约
- [docs/operations.md](docs/operations.md)：部署运维、备份恢复与排障
- [submission/GOAI_Boundless_Agents/RoadMan_赛道二参赛方案书.md](submission/GOAI_Boundless_Agents/RoadMan_赛道二参赛方案书.md)：参赛方案书
- [docs/repo-audit-2026-08-28.md](docs/repo-audit-2026-08-28.md)：本次全仓库审计与可复现实测记录

## 使用边界

路线、天气、开放时间、票务、班次、路况与价格可能变化。RoadMan 会记录来源、时间和降级状态，但导出结果不能替代景区、交通运营方、道路管理部门或车辆厂商的实时信息。出发前请再次核对预约、封路、恶劣天气、补能可用性及交通班次。

RoadMan 不连接或控制车辆。天气、补能与路线服务失败时会显示可解释降级，不把估算点或示意路线冒充实时事实；数据收集、30 天附件保留和行程级联删除范围见 [安全、降级与数据边界](docs/safety-and-data-boundary.md)。
