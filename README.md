# RoadMan

RoadMan 是面向周末自驾与中短途旅行的智能路书工作台。本仓库当前完成
总规划第 27 节的第一批骨架：共享契约、前后端 Mock、Trip CRUD、SSE、
Skill Registry、高德 Adapter、真实高德 JSAPI 沿路网渲染、交互式 3D 车辆
和武汉—庐山固定演示。

## 本地启动

推荐使用规划指定的 Python 3.11：

```powershell
conda env create -f environment.yml
conda activate roadman
$env:PYTHONPATH='backend'
uvicorn app.main:app --reload --port 8000
```

另开终端启动前端：

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`。后端未启动时，行程数据会自动降级为浏览器
内置 Mock；地图仍会使用 `Skills/amap-jsapi` 中的本地开发凭据加载高德底图
与驾车道路形状。环境变量优先级更高，生产环境应配置代理，不应将安全码
直接交付给浏览器。

## Docker

```powershell
docker compose up --build
```

打开 `http://localhost:8080`。Compose 会启动 PostgreSQL、Redis、FastAPI
与 Nginx/Vue。

## 验证

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests
python backend/scripts/export_schemas.py
cd frontend
npm run build
npx playwright install chromium
npm run test:e2e
```

## 当前边界

- 高德 Adapter 已真实实现，无 Key 时返回统一降级结果。
- 规划页已接入高德 JSAPI；每个 Stage 使用 `AMap.Driving` 获取完整道路形状，
  当前阶段蓝色、同日其他阶段灰色；服务未返回真实道路点列时才使用灰色虚线
  直连起终点，并明确显示降级提示。
- 首页使用 `<model-viewer>` 加载 Khronos Car Concept GLB，支持旋转与缩放；
  资产来源和 CC BY 4.0 署名见 `frontend/public/models/LICENSE.md`。
- SSE 为定时模拟事件，尚未启动 LangGraph。
- 酒店、价格、天气与耗能演示值均标记为估算或待确认。

完整项目现状与接口说明见根目录 [`project.md`](project.md)。
