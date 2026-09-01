# 外部数据接入与凭据清单

本文说明 RoadMan 依赖的每一项外部能力：能力标识、环境变量、是否必需、失败降级策略以及凭据存放规则。凭据只写入本机未跟踪的 `.env` 或本地 `Skills/` 凭据文件，绝不进入 git、日志或导出物。

## 凭据存放总则

- 后端读取的 key 一律先放进项目根目录未跟踪的 `.env`，`docker compose up` 时注入 backend/worker 容器。
- `backend/app/core/config.py` 通过 pydantic-settings 读取 `Settings`，缺 key 时不会使服务崩溃，而是进入对应降级路径。
- `LOAD_LOCAL_SKILL_CREDENTIALS=true` 时，后端会从 `Skills/amap-lbs/apipkey.txt`、`Skills/opentripmap/apikey.txt` 读取本地凭据作为开发兜底；这些文件已加入忽略规则，不要提交。
- 浏览器侧通过形如 `VITE_` 的变量在构建期注入，无法在运行时读取；`VITE_` 变量只出现在前端构建参数中。
- 任何调用都经 Skill Registry 统一超时、重试、缓存和审计；审计日志只记录 provider、耗时、来源与错误码，不记录 key、Cookie 或请求体内的敏感字段。
- 每个 Skill 的 `health_check` 返回 `configured`/`ready`/`degraded`，健康检查与指标接口不暴露凭据。

## 外部能力清单

### 高德 Web Service（`AMAP_WEBSERVICE_KEY`）

- 已注册 Skill：`amap.geocode`、`amap.driving`、`amap.route`、`amap.poi`。
- 用途：地理编码、POI 搜索、驾车/公交/步行/骑行路线、路况。
- 环境变量：`AMAP_WEBSERVICE_KEY`（`Settings.amap_webservice_key`）。
- 是否必需：真实地图与路线推荐必需；未配置时相关 Skill 返回 `SKILL_NOT_CONFIGURED`，规划走 Mock 或已有数据降级。
- 失败降级：未配置或调用失败时返回结构化失败，不伪造道路点列。

### 高德 JSAPI（`VITE_AMAP_JSAPI_KEY`、`VITE_AMAP_SECURITY_JS_CODE`）

- 用途：浏览器端地图渲染与交互，构建期通过 `frontend/Dockerfile` 的 `ARG`/`ENV` 注入。
- 环境变量：`VITE_AMAP_JSAPI_KEY`、`VITE_AMAP_SECURITY_JS_CODE`（及可选的 `VITE_AMAP_SERVICE_HOST`）。
- 是否必需：浏览器真实地图必需；无 key 时前端降级到 `MockRouteMap`，并显示“已切换 Mock 地图”提示。
- 失败降级：前端检测到高德加载失败即切换到 Mock 底图，不阻断其他功能。

### Open-Meteo（无需 key）

- 已注册 Skill：`open_meteo.forecast`。
- 用途：实时天气与逐日预报。
- 环境变量：无（`OPEN_METEO_URL = https://api.open-meteo.com/v1/forecast`）。
- 是否必需：否；用于行程天气适配，缺省时跳过天气信息。
- 失败降级：适配器超时/网络失败时规划继续，天气区间标记为待更新；来源记录标注 `license=CC BY 4.0`。

### FlyAI / 飞猪 CLI（`FLYAI_API_KEY`）

- 已注册 Skill：`flyai.train`、`flyai.flight`、`flyai.ferry`、`flyai.hotel`、`flyai.poi`、`flyai.keyword_search`、`flyai.ai_search`。
- 用途：跨城班次、酒店、餐饮、POI 与旅行语义搜索；通过 `flyai` 命令行的子进程执行。
- 环境变量：`FLYAI_API_KEY`（适配器用 `os.getenv` 读取；`_flyai_process_env` 负责把代理与凭据环境传给 Node 子进程）。
- 是否必需：否；作为旅行搜索与候选补充。
- 失败降级：未安装 `flyai` CLI 或未配置 key 时返回 `SKILL_NOT_CONFIGURED`；命令返回不可解析 JSON 时返回 `FLYAI_INVALID_RESPONSE`；超时由 Skill Registry 统一处理。

### OpenTripMap（`OPENTRIPMAP_API_KEY`）

- 已注册 Skill：`opentripmap.nearby`。
- 用途：基于 OpenStreetMap/Wikidata/Wikipedia 的开放地点候选。
- 环境变量：`OPENTRIPMAP_API_KEY`（`Settings.opentripmap_api_key`）。
- 是否必需：否；作为景点候选补充。
- 失败降级：未配置 key 返回 `SKILL_NOT_CONFIGURED`；无结果返回 `OPENTRIPMAP_NO_RESULTS`；只作候选，保留来源。

### 车型目录服务（CarInfo，无需 key）

- 已注册 Skill：`carinfo.catalog`、`carinfo.demo`。
- 用途：品牌/车系/车型目录搜索；`CARINFO_API_URL = https://tool.bitefu.net/car/` 为公开接口。缺少具体年款详情时，适配器会并行动态查询 AutoSeeker、OpenEV Data、AppByte Fleet Catalog 以及 CarNewsChina 公开配置页，按用户输入匹配具体版本，补齐可核验的续航、电池、能耗、充电、油箱和尺寸字段。
- 环境变量：无。
- 是否必需：否；所有外部资料均为公开补充，规划只使用明确返回的字段；没有来源的续航/能耗不猜测，并通过 `specs_missing` 提示用户确认。
- 失败降级：目录服务不可用时车辆数据降级，行程以已有车辆上下文继续。

### DeepSeek 官方 Chat Completions（`DEEPSEEK_API_KEY`）

- 落地位置：`backend/app/planning/llm.py` 各智能体（需求提取、需求校验、编辑、POI 策展、目的地研究、POI 排序、POI 适配、特殊事件研究）。
- 用途：语义理解、目的地研究、候选排序/策展/适配、自然语言编辑与附件理解。
- 环境变量：`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`（默认 `deepseek-v4-flash`）、`DEEPSEEK_API_URL`（默认 `https://api.deepseek.com/chat/completions`）、`DEEPSEEK_REASONING_EFFORT`（默认 `low`）、`DEEPSEEK_THINKING`（默认 `false`）。需要深度推理时可在单次评测环境显式打开，不应让生产默认等待思考链。
- 是否必需：Agent 能力建议配置；未配置 `DEEPSEEK_API_KEY` 时需求提取回退到离线结构化解析，只保留硬性结构字段，不做语义猜测。
- 失败降级：模型服务不可达时，需求提取走 `extract_structural_constraints` 与 `extract_explicit_location_constraints` 的确定性兜底；各智能体结构化失败时由下游校验拦截。模型私有思维链不落库、不入日志。

## 凭据验证

可运行：

```powershell
docker compose ps
python deploy/api_smoke.py
```

验证结果只显示 provider 的健康状态、响应时间和错误码，不打印 API key、Cookie 或请求头。若某个 provider 无额度或网络不可用，规划仍可使用已有证据并明确降级原因。相关接口见 `backend/app/services/registry_factory.py` 的 Skill 注册与 `deploy/api_smoke.py` 的 provider 检查。
