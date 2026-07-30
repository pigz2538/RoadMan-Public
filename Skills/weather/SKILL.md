---
name: weather
description: 旅游规划天气助手，基于 Open-Meteo 免费 API 提供天气预报、历史同期、季节趋势、空气质量、海况、海拔、集合概率预报。Use when user says "天气预报"、"未来 7 天天气"、" Tokyo weather"、"trip planning weather"、"历史同期天气"、"季节趋势"、"空气质量"、"海浪海温"、"海拔查询"、"出行天气建议"、"best time to travel"、"rain probability"、"UV index"，或为旅行规划、出行决策、行程安排查询天气相关数据时。无需 API Key。
license: ODbL
metadata:
  version: "1.0"
  author: Open-Meteo
  category: weather-travel
  homepage: https://open-meteo.com
  data-sources: [ECMWF, NOAA, DWD, CAMS, Copernicus]
---

# Weather Skill — 旅游规划天气助手

基于 [Open-Meteo](https://open-meteo.com/) 免费 API 的天气技能，专为**旅游规划（trip planning）**场景设计。提供天气预报、历史同期、季节趋势、空气质量、海况、海拔等全方位数据，帮助 AI 为用户做出靠谱的出行决策。

> **无需 API Key**（非商用免费），无注册，直接 HTTP 调用。

---

## Instructions

针对出行规划场景，按以下流程组合调用 8 个 API（详见 `旅游规划工作流` 章节）：

1. **地理编码**（必做起点）：`search_location` 得到坐标
2. **主预报**（核心）：`get_forecast` 取逐时/逐日预报
3. **历史同期**（参考基线）：`get_historical_weather` 判断今年是否异常
4. **季节趋势**（长线规划，>16 天）：`get_seasonal_forecast` 看偏暖/偏冷趋势
5. **场景化补充**（按需）：空气质量 / 海况 / 海拔 / 集合概率预报
6. **综合建议**：汇总输出最佳出行窗口、装备建议、风险提示、备选方案

## 核心能力（8 个 API）

| # | API | 用途 | 端点 |
|---|-----|------|------|
| 1 | **Geocoding** | 城市名→坐标转换（起点） | `geocoding-api.open-meteo.com/v1/search` |
| 2 | **Weather Forecast** | 7-16 天逐时/逐日预报 | `api.open-meteo.com/v1/forecast` |
| 3 | **Historical Weather** | 1940 至今历史天气（同期对比） | `archive-api.open-meteo.com/v1/archive` |
| 4 | **Seasonal Forecast** | 7 个月季节趋势（长线规划） | `seasonal-api.open-meteo.com/v1/seasonal` |
| 5 | **Air Quality** | 空气质量/花粉预报 | `air-quality-api.open-meteo.com/v1/air-quality` |
| 6 | **Marine Weather** | 海浪/海温/洋流（海滨游） | `marine-api.open-meteo.com/v1/marine` |
| 7 | **Elevation** | 海拔查询（高海拔活动） | `api.open-meteo.com/v1/elevation` |
| 8 | **Ensemble** | 集合概率预报（不确定性评估） | `ensemble-api.open-meteo.com/v1/ensemble` |

完整参数表、变量定义、响应格式见 [`docs/`](docs/) 目录下对应文件。

---

## 三种使用方式

### 方式一：Python SDK（推荐，类型安全）

```python
from open_meteo_skill import OpenMeteoClient

client = OpenMeteoClient()

# 1. 地理编码
loc = client.search_location(name="Tokyo", count=1, language="zh")
lat, lon = loc.results[0].latitude, loc.results[0].longitude

# 2. 7 天预报
forecast = client.get_forecast(
    latitude=lat, longitude=lon,
    daily=["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "weather_code"],
    current=["temperature_2m", "weather_code"],
    timezone="auto",
)

client.close()
```

支持同步/异步（`aget_forecast`）、上下文管理器、Pydantic 模型校验。详见 [`src/open_meteo_skill/`](src/open_meteo_skill/)。

### 方式二：OpenWebUI 工具（聊天集成）

直接导入 [`open-meteo-tool.py`](open-meteo-tool.py)，在 OpenWebUI 中获得 5 个对话工具：

| 工具 | 用法示例 |
|------|---------|
| `get_current_weather` | "现在东京天气怎么样？" |
| `get_weather_forecast` | "北京未来 7 天预报" |
| `get_air_quality` | "上海空气质量如何？" |
| `search_location` | "查找巴黎的坐标" |
| `get_elevation` | "珠穆朗玛峰海拔多少？" |

### 方式三：直接 HTTP（参考 docs/）

按 [`docs/`](docs/) 中的参数表直接构造 URL：

```
https://api.open-meteo.com/v1/forecast?latitude=35.68&longitude=139.65&daily=temperature_2m_max,temperature_2m_min&timezone=auto
```

---

## 旅游规划工作流

针对出行规划场景，按以下流程组合调用 8 个 API：

### Step 1: 地理编码（必做起点）
```
search_location(name="<目的地>", language="zh", count=1)
→ 获取 latitude, longitude, timezone
```
**注意：** 中文城市名需用拼音（"苏州"→"Suzhou"），或直接用坐标。

### Step 2: 主预报（核心）
```
get_forecast(latitude, longitude,
    daily=["temperature_2m_max","temperature_2m_min","precipitation_sum",
           "precipitation_probability_max","weather_code","wind_speed_10m_max",
           "sunrise","sunset","uv_index_max"],
    current=["temperature_2m","apparent_temperature","weather_code","wind_speed_10m"],
    hourly=["temperature_2m","precipitation_probability"],  # 出发当日逐时
    forecast_days=<行程天数，最多16>, timezone="auto")
```

### Step 3: 历史同期（参考基线）
查询过去几年同一日期段的天气，判断今年是否异常：
```
get_historical_weather(latitude, longitude,
    start_date="2023-07-01", end_date="2023-07-15",  # 去年同期
    daily=["temperature_2m_mean","precipitation_sum","weather_code"],
    models=["era5"], timezone="auto")
```
可查 1940 至今，对比多年均值判断出行时机。

### Step 4: 季节趋势（长线规划，>16 天）
行程在 16 天之后？用季节预报看趋势（非精准值，看偏高/偏低）：
```
get_seasonal_forecast(latitude, longitude,
    daily=["temperature_2m_max","temperature_2m_min","precipitation_sum"],
    models=["ecmwf_seasonal_seamless"], timezone="auto")
```
关注 `anomaly` 字段：正值=偏暖/偏湿，负值=偏冷/偏干。

### Step 5: 场景化补充（按需）

**空气质量敏感人群 / 城市游：**
```
get_air_quality(latitude, longitude,
    current=["pm2_5","pm10","us_aqi","european_aqi","uv_index"],
    hourly=["pm2_5","us_aqi"], forecast_days=5)
```
US AQI ≤50 良好，>150 不适宜户外活动。

**海滨/海岛游：**
```
get_marine_forecast(latitude, longitude,
    hourly=["wave_height","sea_surface_temperature","ocean_current_velocity"],
    daily=["wave_height_max"], timezone="auto")
```
浪高 >2m 不宜下水，海温 <18°C 较冷。

**高海拔/登山/徒步：**
```
get_elevation([latitude], [longitude])  # 确认海拔
get_forecast(latitude, longitude, hourly=["temperature_80m","wind_speed_80m","freezing_level_height"])
```
海拔每升 100m 降温约 0.6°C；冰冻线高度判断是否需防寒。

**关键决策（高成本行程）：**
```
get_ensemble_forecast(latitude, longitude,
    hourly=["temperature_2m","precipitation"],
    models=["icon_seamless_eps"], forecast_days=7)
```
集合预报给出概率范围，判断降雨/极端天气可信度。

### Step 6: 综合建议
汇总以上数据，输出：
- **最佳出行窗口**（温度适宜、降水概率低、空气质量好）
- **每日天气概览表**（日期/天气/温度区间/降水/日出日落）
- **装备建议**（防晒/雨具/保暖/泳装等）
- **风险提示**（极端天气、空气污染、海况危险、高海拔反应）
- **备选方案**（室内活动日、改期建议）

---

## 关键变量速查

### 天气预报常用变量
| 变量 | 含义 | 单位 |
|------|------|------|
| `temperature_2m` | 2 米气温 | °C |
| `apparent_temperature` | 体感温度 | °C |
| `precipitation` | 降水（雨+雪） | mm |
| `precipitation_probability` | 降水概率 | % |
| `weather_code` | WMO 天气代码 | 见下表 |
| `wind_speed_10m` | 10 米风速 | km/h |
| `uv_index` | 紫外线指数 | 0-11+ |
| `sunrise`/`sunset` | 日出/日落 | ISO8601 |

### WMO 天气代码
| 码 | 含义 | | 码 | 含义 |
|----|------|---|----|------|
| 0 | 晴 | | 61/63/65 | 小/中/大雨 |
| 1/2/3 | 多云/阴 | | 71/73/75 | 小/中/大雪 |
| 45/48 | 雾 | | 80/81/82 | 阵雨 |
| 51/53/55 | 毛毛雨 | | 95 | 雷暴 |

完整代码表见 [`docs/weather-forecast-api.md`](docs/weather-forecast-api.md)。

### 空气质量阈值（US AQI）
| 范围 | 等级 | 户外建议 |
|------|------|---------|
| 0-50 | 良好 | 任意活动 |
| 51-100 | 中等 | 敏感人群注意 |
| 101-150 | 敏感人群不健康 | 减少户外 |
| 151-200 | 不健康 | 避免户外 |
| >200 | 非常不健康/危险 | 室内活动 |

---

## 安装

```bash
# 作为 Python 包
pip install -e .

# 或仅用 OpenWebUI 工具
# 复制 open-meteo-tool.py 到 OpenWebUI Tools
```

依赖：`httpx>=0.27.0`、`pydantic>=2.0.0`，Python ≥3.10。

---

## 项目结构

```
weather/
├── SKILL.md                          # 本文件（AI 入口指令）
├── README.md                         # 项目说明
├── pyproject.toml                    # 包配置
├── open-meteo-tool.py                # OpenWebUI 工具
├── docs/                             # 8 个 API 完整参考文档
│   ├── weather-forecast-api.md
│   ├── historical-weather-api.md
│   ├── geocoding-api.md
│   ├── seasonal-forecast-api.md
│   ├── air-quality-api.md
│   ├── marine-weather-api.md
│   ├── elevation-api.md
│   └── ensemble-api.md
├── src/open_meteo_skill/             # Python SDK
│   ├── __init__.py
│   ├── client.py                     # 主客户端
│   ├── constants.py                  # 常量与枚举
│   ├── exceptions.py                 # 异常类
│   └── models.py                     # Pydantic 模型
└── examples/
    └── basic_usage.py                # 使用示例
```

---

## 注意事项

- **免费额度**：非商用每日 10,000 次调用，无需 API Key
- **时区**：务必传 `timezone=auto`，否则返回 GMT 时间易误读
- **中文城市名**：地理编码不支持中文，用拼音或直接传坐标
- **季节预报**：非精准值，仅反映偏暖/偏冷/偏湿/偏干趋势
- **历史数据**：最近 5 天可能未入库，用 Forecast API 的 `past_days` 替代
- **集合预报**：返回多个成员值，需自行统计均值/极值/概率

## Troubleshooting

- **中文城市名查询无结果**：Geocoding API 基于 GeoNames，不支持中文。改用拼音（"苏州"→"Suzhou"）或直接传坐标。
- **返回时间与当地不符**：未传 `timezone=auto`，默认返回 GMT。务必带上 `timezone=auto`。
- **最近 5 天历史数据缺失**：历史库入库有延迟。改用 Forecast API 的 `past_days` 参数获取近期数据。
- **集合预报难以解读**：返回多成员值，需自行计算均值/极值/降水概率，非单一确定值。
- **超出每日 10,000 次调用**：非商用免费额度上限。商用需购买 Open-Meteo 商业套餐或自建缓存。

## 致谢

数据源：[Open-Meteo](https://open-meteo.com/)，基于 ECMWF、NOAA、DWD、CAMS、Copernicus 等机构模型。非商用需标注来源。