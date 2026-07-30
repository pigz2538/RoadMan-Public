# RoadMan Skills — 项目总览

本仓库是一组遵循 **OpenClaw / Claude Agent Skills** 开放标准的技能集合。每个 Skill 是一个独立文件夹，包含必需的 `SKILL.md`（YAML frontmatter + Markdown 指令）与可选的 `references/`、`scripts/`、`assets/` 等子目录。

> 格式规范来源：《The Complete Guide to Building Skills for Claude》。每个 Skill 均遵循渐进式披露（Progressive Disclosure）三层结构：frontmatter 常驻 → SKILL.md 正文按需加载 → references/ 深度按需加载。

---

## 技能清单

| # | 技能 | 名称 | 类别 | 需 API Key | 核心能力 |
|---|------|------|------|-----------|---------|
| 1 | `amap-jsapi` | 高德地图 JSAPI | maps-frontend | 是 (`AMAP_JSAPI_KEY` + `AMAP_SECURITY_JS_CODE`) | 前端地图代码生成 |
| 2 | `amap-lbs` | 高德地图综合服务 | maps-services | 是 (`AMAP_WEBSERVICE_KEY`) | POI 搜索 / 路径规划 / 旅游规划 / 热力图 |
| 3 | `flyai` | FlyAI 旅行搜索 | travel-search | 否（可选 `FLYAI_API_KEY`） | 航班 / 酒店 / 景点 / 火车 / 门票搜索预订 |
| 4 | `openchargemap` | Open Charge Map | ev-charging | 是 (`OCM_API_KEY`) | 电动汽车充电桩查询 |
| 5 | `opentripmap` | OpenTripMap | tourism-poi | 是 (`apikey`) | 全球旅游景点 POI 查询 |
| 6 | `weather` | Open-Meteo 天气 | weather-travel | 否 | 旅游规划天气预报 |
| 7 | `carinfo` | 汽车信息查询 | car-info | 否 | 汽车品牌 / 车系 / 车型 / 报价 / 颜色配置查询 |

---

## 统一格式规范

所有 Skill 均按以下规范统一编写：

### 文件结构
```
skill-name/                 # kebab-case 文件夹名
├── SKILL.md                # 必需，精确大小写
├── references/             # 可选，深度参考文档
├── scripts/                # 可选，可执行脚本
└── assets/                 # 可选，模板/字体/图标
```
- 文件夹名与 `name` 字段一致，均为 kebab-case
- **不含 `README.md`**（规范要求所有文档放在 `SKILL.md` 或 `references/`）
- `SKILL.md` 大小写精确

### YAML Frontmatter（必需字段）
```yaml
---
name: skill-name            # kebab-case，与文件夹同名
description: 做什么 + 何时使用 + 触发短语（< 1024 字符，无尖括号）
---
```

### 可选字段
- `license`：开源许可（MIT / CC-BY-SA-4.0 / ODbL 等）
- `metadata`：自定义键值对（version、author、category、homepage 等）
- `metadata.openclaw`：OpenClaw 运行时依赖（环境变量、二进制、安装项）

### 正文结构（推荐模板）
```
# 技能标题
## Instructions / 快速开始     # 核心步骤
## 场景 / 核心能力             # 分场景指令
## Examples / 调用示例         # 真实示例
## Troubleshooting             # 常见错误与解决方案
## 相关链接 / 参考文档
```

### 安全限制
- frontmatter 中禁止 XML 尖括号 `< >`
- 技能名禁止以 `claude` 或 `anthropic` 开头（保留字）
- 敏感凭据通过环境变量传入，禁止硬编码

---

## 各技能详解

### 1. amap-jsapi — 高德地图 JSAPI v2.0 前端开发

**用途**：生成符合高德官方规范的 Web 地图前端代码。涵盖地图生命周期管理、强制安全配置、3D 视图控制、覆盖物绘制及 LBS 服务集成。

**触发场景**：创建高德地图、集成 amap、画地图标记、地图组件、AMapLoader、地图可视化、高德 JSAPI。

**依赖**：
- 环境变量：`AMAP_JSAPI_KEY`、`AMAP_SECURITY_JS_CODE`
- 生产环境需 `serviceHost` 代理转发安全密钥

**目录结构**：
```
amap-jsapi/
├── SKILL.md                  # 主指令文件
└── references/
    ├── api/                  # 18 个 API 类别文档（Map、Markers、Routing 等）
    ├── security.md           # 安全策略
    ├── map-init.md           # 地图初始化
    ├── marker.md             # 点标记
    ├── vector-graphics.md    # 矢量图形
    ├── layers.md             # 图层管理
    ├── geocoder.md           # 地理编码
    ├── routing.md            # 路径规划
    ├── search.md             # POI 搜索
    └── ...                   # 共 13 个场景文档 + api/ 子目录
```

**关键特性**：
- v2.0 强制安全密钥配置
- WebGL 3D 视图
- 海量标记避让（LabelMarker）
- 按需加载插件减少首屏体积
- 资源释放（`map.destroy()`）

---

### 2. amap-lbs — 高德地图综合服务

**用途**：执行 POI 搜索、周边搜索、路径规划（步行/驾车/骑行/公交）、智能旅游规划与热力图可视化。

**触发场景**：搜美食、找酒店、天安门在哪、西直门周边美食、规划驾车路线、北京一日游、生成热力图。

**依赖**：
- 环境变量：`AMAP_WEBSERVICE_KEY`
- 二进制：`node`、`python3`
- npm 包：`axios`

**目录结构**：
```
amap-lbs/
├── SKILL.md                  # 主指令文件（7 个场景）
├── index.js                  # Node.js 入口模块
├── gaode_skill.py            # Python 导航/搜索脚本
├── package.json
├── config.example.json
├── apipkey.txt
└── scripts/
    ├── poi-search.js         # POI 搜索脚本
    ├── route-planning.js     # 路径规划脚本
    └── travel-planner.js     # 旅游规划脚本
```

**七个场景**：
1. 明确关键词搜索（直接拼搜索链接，无需 Key）
2. 基于位置的周边搜索（地理编码 + 坐标搜索）
3. 热力图展示（数据可视化链接）
4. POI 详细搜索（Web 服务 API）
5. 路径规划（步行/驾车/骑行/公交）
6. 智能旅游规划（自动搜兴趣点 + 规划路线）
7. Python 脚本导航与搜索

**遥测说明**：每次操作前向 `restapi.amap.com/v3/log/init` 发送匿名统计请求（不含个人信息或 Key）。

---

### 3. flyai — FlyAI 旅行搜索与预订

**用途**：通过 FlyAI CLI 调用 Fliggy MCP 服务，搜索航班、酒店、景点、火车、万豪选项及旅行套餐，支持自然语言与结构化查询。

**触发场景**：search hotels、find flights、airfare、things to do in {city}、itinerary、trip planning、visa、car rental、cruise、attraction tickets、搜酒店、查机票、景点门票、旅游攻略、行程规划、蜜月旅行、亲子游。

**依赖**：
- 二进制：`node`
- npm 全局包：`@fly-ai/flyai-cli`
- 可选：`FLYAI_API_KEY`

**目录结构**：
```
flyai/
├── SKILL.md                  # 主指令文件
├── _meta.json                # 发布元数据
├── skill-card.md             # ClawHub 技能卡片
└── references/
    ├── keyword-search.md     # 关键词搜索
    ├── ai-search.md          # AI 语义搜索
    ├── search-flight.md      # 航班搜索
    ├── search-hotel.md       # 酒店搜索
    ├── search-train.md       # 火车搜索
    ├── search-poi.md         # 景点搜索
    ├── search-marriott-hotel.md
    └── search-marriott-package.md
```

**核心能力**：
- `keyword-search`：跨酒店/航班/门票/事件的自然语言搜索
- `ai-search`：语义搜索，理解复杂意图
- `search-flight/hotel/poi/train`：结构化深度比价
- `search-marriott-hotel/package`：万豪集团专属搜索

**输出规范**：所有命令输出单行 JSON；最终回复须为 Markdown，图片先于预订链接，平台提示置于末尾。

**元数据**：frontmatter 中 `metadata.intents` 与 `metadata.patterns` 保留 11 个意图标签与 18 条中英文触发正则，供运行时匹配。

---

### 4. openchargemap — 电动汽车充电桩查询

**用途**：查询全球最大的电动汽车充电桩开放数据注册库 Open Charge Map。获取充电站点列表、地址、连接器、功率、运营状态、运营商、费用、评论与照片。

**触发场景**：find EV charging stations、充电桩、电动汽车充电站、附近充电站、charging stations near me、查充电桩、快充站、充电站详情。

**依赖**：
- 环境变量：`OCM_API_KEY`
- Base URL：`https://api.openchargemap.io/v3`

**目录结构**：
```
openchargemap/
├── SKILL.md                  # 主指令文件（端点/参数/schema/示例）
└── references/
    └── ocm-openapi-spec.yaml # 完整 OpenAPI 3.1 规范（2129 行）
```

**六个端点**：
1. `GET /poi` — 查询充电站点（主端点，支持国家/坐标/边界框/运营商/连接类型过滤）
2. `GET /referencedata` — 核心参考数据 ID 表
3. `POST /profile/authenticate` — 用户认证换取 JWT
4. `POST /comment` — 提交评论/签到（需 JWT）
5. `POST /mediaitem` — 上传充电点照片（需 JWT）
6. `GET /openapi` — 获取 OpenAPI 定义

**数据许可**：CC BY-SA 4.0（使用须署名并按相同许可共享）

**公平使用**：免费无 SLA，禁止重复无差别查询，过度调用会被封禁。

---

### 5. opentripmap — 全球旅游景点 POI 查询

**用途**：基于 OpenStreetMap/Wikidata/Wikipedia 等开放数据的全球景点兴趣点数据库，覆盖超 1000 万个旅游景点与设施。提供地名坐标查询、按区域/半径检索 POI、搜索建议、POI 详情获取。

**触发场景**：景点信息、附近 POI、旅游景点、查景点、places of interest、things to do、attractions near me、坐标查询、地点搜索建议、POI 详情。

**依赖**：
- API Key：`apikey`（文件 `apikey.txt`）
- Base URL：`https://api.opentripmap.com/0.1`

**目录结构**：
```
opentripmap/
├── SKILL.md                  # 主指令文件
├── openapi.en.json           # 完整 OpenAPI 3.0 规范
└── apikey.txt                # API Key 文件
```

**五个端点**：
1. `GET /{lang}/places/geoname` — 按地名获取坐标
2. `GET /{lang}/places/bbox` — 按矩形区域获取 POI 列表
3. `GET /{lang}/places/radius` — 按圆心+半径获取 POI 列表
4. `GET /{lang}/places/autosuggest` — 搜索建议（含高亮名称）
5. `GET /{lang}/places/xid/{xid}` — 获取单个 POI 详情（含 Wikipedia 摘录、图片、评分）

**典型流程**：地名 → 坐标 → radius 取列表 → xid 取详情

**数据许可**：ODbL（Open Data Commons Open Database License）——可预取、索引、缓存、修改、在任何地图与地区无限制使用。

**支持语言**：`en`（英语）、`ru`（俄语）

---

### 6. weather — 旅游规划天气助手

**用途**：基于 Open-Meteo 免费 API，专为旅游规划场景设计。提供天气预报、历史同期、季节趋势、空气质量、海况、海拔、集合概率预报，帮助做出靠谱的出行决策。

**触发场景**：天气预报、未来 7 天天气、trip planning weather、历史同期天气、季节趋势、空气质量、海浪海温、海拔查询、出行天气建议、best time to travel、rain probability。

**依赖**：
- **无需 API Key**（非商用免费，每日 10,000 次调用）
- Python ≥ 3.10，`httpx>=0.27.0`、`pydantic>=2.0.0`

**目录结构**：
```
weather/
├── SKILL.md                  # 主指令文件
├── pyproject.toml            # Python 包配置
├── open-meteo-tool.py        # OpenWebUI 工具（5 个对话工具）
├── docs/                     # 8 个 API 完整参考文档
│   ├── weather-forecast-api.md
│   ├── historical-weather-api.md
│   ├── geocoding-api.md
│   ├── seasonal-forecast-api.md
│   ├── air-quality-api.md
│   ├── marine-weather-api.md
│   ├── elevation-api.md
│   └── ensemble-api.md
├── src/open_meteo_skill/     # Python SDK
│   ├── __init__.py
│   ├── client.py             # 主客户端（同步/异步）
│   ├── constants.py          # 常量与枚举
│   ├── exceptions.py         # 异常类
│   └── models.py             # Pydantic 模型
└── examples/
    └── basic_usage.py
```

**8 个 API**：
1. **Geocoding** — 城市名→坐标转换
2. **Weather Forecast** — 7-16 天逐时/逐日预报
3. **Historical Weather** — 1940 至今历史天气（同期对比）
4. **Seasonal Forecast** — 7 个月季节趋势（长线规划）
5. **Air Quality** — 空气质量/花粉预报
6. **Marine Weather** — 海浪/海温/洋流（海滨游）
7. **Elevation** — 海拔查询（高海拔活动）
8. **Ensemble** — 集合概率预报（不确定性评估）

**三种使用方式**：
- Python SDK（推荐，类型安全，Pydantic 校验）
- OpenWebUI 工具（聊天集成，5 个对话工具）
- 直接 HTTP（参考 docs/）

**旅游规划工作流**：地理编码 → 主预报 → 历史同期 → 季节趋势 → 场景化补充 → 综合建议

---

### 7. carinfo — 汽车品牌车系车型查询

**用途**：基于 bitefu CarApi（汽车之家/易车网等数据源）查询汽车品牌、车系、车型信息。获取品牌列表、车系列表、车型配置与报价、年款、车身颜色与内饰颜色等数据。

**触发场景**：查汽车品牌、车型查询、车系列表、某品牌有哪些车、车型配置、汽车报价、车身颜色、内饰颜色、某车年款、查大众、丰田车系、car brands、car models、car series、vehicle specifications、car pricing。

**依赖**：
- **无需 API Key**（免费，支持 HTTP/HTTPS 的 GET 和 POST）
- Base URL：`https://tool.bitefu.net/car/`

**目录结构**：
```
carinfo/
├── SKILL.md                       # 主指令文件（6 个查询类型 + 层级流程）
└── references/
    └── api-reference.md           # 完整端点/参数/响应 schema 文档
```

**6 个查询类型**：
1. `type=brand` — 获取全部汽车品牌（648+ 品牌）
2. `type=series_group` — 按品牌获取车系列表
3. `type=series` — 获取单个车系详情（含指导价区间、产地类型）
4. `type=info` — 获取车系下的车型列表（含报价、年款）
5. `type=infoyear` — 获取车型可用年款
6. `type=detail` — 获取车型配置/车身颜色/内饰颜色/选装包详情

**数据层级**：品牌 → 车系组 → 车系详情 → 车型 → 年款/详情（四级 ID 体系，层级 id 不可混用）

**数据规模**：648 品牌 / 7099 车系 / 92468 车型（汽车之家主源）

**注意**：本接口调用量大不建议商用，商用请到 Gitee 下载开源版本自部署；大量调用需联系作者加 IP 白名单。

---

## 技能间协作

这些技能围绕 **RoadMan（路书/行程规划）** 主题形成互补能力矩阵：

```
旅游规划 / 出行决策请求
    │
    ├─ 地图前端展示 ──────── amap-jsapi（前端代码生成）
    │
    ├─ 地点搜索 / 路线 ───── amap-lbs（POI/路线/旅游规划，国内）
    │                    └ opentripmap（全球景点 POI，开放数据）
    │
    ├─ 旅行产品搜索预订 ──── flyai（航班/酒店/门票/火车）
    │
    ├─ 出行天气决策 ──────── weather（预报/历史/季节/空气/海况）
    │
    ├─ 电动汽车出行 ──────── openchargemap（充电桩查询）
    │
    └─ 车辆信息查询 ──────── carinfo（品牌/车系/车型/报价/颜色）
```

**典型组合场景**：
- **自驾游规划**：carinfo（选车/车型配置）+ weather（天气）+ amap-lbs（路线规划）+ openchargemap（沿途充电桩）
- **城市旅游**：opentripmap（景点）+ flyai（门票/酒店）+ weather（出行天气）+ amap-jsapi（地图展示）
- **周边探索**：amap-lbs（周边搜索）+ opentripmap（景点详情）+ weather（当日天气）
- **购车决策**：carinfo（品牌车系车型报价）+ openchargemap（电车充电配套）+ weather（出行天气参考）

