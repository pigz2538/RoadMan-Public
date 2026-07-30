---
name: opentripmap
description: 全球景点兴趣点 (POI) 数据库 REST API，基于 OpenStreetMap/Wikidata/Wikipedia 等开放数据，覆盖超 1000 万个旅游景点与设施。提供地名坐标查询、按区域/半径检索 POI、搜索建议、POI 详情获取。Use when user says "景点信息"、"附近 POI"、"旅游景点"、"查景点"、"places of interest"、"things to do"、"attractions near me"、"city points of interest"、"坐标查询"、"地点搜索建议"、"POI 详情"，或需要按矩形区域/圆心半径检索旅游景点并获取详情（含 Wikipedia 摘录、图片、评分）时。
license: ODbL
metadata:
  version: "0.1"
  author: OpenTripMap
  category: tourism-poi
  homepage: https://opentripmap.com
  base-url: https://api.opentripmap.com/0.1
---

# OpenTripMap API Skill

全球景点兴趣点 (POI) 数据库 REST API。基于 OpenStreetMap、Wikidata、Wikipedia、俄罗斯文化部与自然资源部等开放数据，涵盖超过 1000 万个旅游景点与设施。对象类型按层级结构组织。

## 元信息

| 项 | 值 |
|---|---|
| Base URL | `https://api.opentripmap.com/0.1` |
| 认证方式 | URL 参数 `?apikey=<KEY>` |
| API Key 文件 | `apikey.txt`（与本 SKILL.md 同目录） |
| 支持语言 | `en`（英语）、`ru`（俄语），路径参数 `{lang}` |
| 输出格式 | `json` / `geojson`（默认）/ `count`（仅返回数量） |
| 类目层级 | https://dev.opentripmap.org/catalog |
| 许可证 | ODbL（Open Data Commons Open Database License） |
| 使用条款 | https://dev.opentripmap.org/legal/short-offer |
| 官方文档 | https://dev.opentripmap.org/docs |
| 完整 OpenAPI 规范 | `openapi.en.json`（与本 SKILL.md 同目录，OpenAPI 3.0） |

### 许可与使用自由度（ODbL）

不同于 Google Places 等服务，OpenTripMap 不施加以下限制：
- 可在任何地图上展示 API 结果
- 可预取、索引、存储、缓存数据
- 可在展示前修改数据
- 可在任何地区无限制使用

## 端点总览

| 方法 | 端点 | 说明 | 关键参数 |
|---|---|---|---|
| GET | `/{lang}/places/geoname` | 按地名获取坐标（城市、村镇等） | `name`(必填), `country` |
| GET | `/{lang}/places/bbox` | 按矩形区域获取 POI 列表 | `lon_min`/`lon_max`/`lat_min`/`lat_max`(必填) |
| GET | `/{lang}/places/radius` | 按圆心+半径获取 POI 列表 | `radius`/`lon`/`lat`(必填) |
| GET | `/{lang}/places/autosuggest` | 按名称片段+位置搜索建议 | `name`/`radius`/`lon`/`lat`(必填) |
| GET | `/{lang}/places/xid/{xid}` | 获取单个 POI 的详细信息 | `xid`(路径必填) |

## 公共参数

| 参数 | 位置 | 说明 |
|---|---|---|
| `lang` | path | 语言代码：`en` 或 `ru` |
| `apikey` | query | API Key（必填，所有请求） |
| `kinds` | query | 景点类别过滤，逗号分隔为 OR 逻辑，如 `churches,museums`。默认 `interesting_places`。完整类目见 https://dev.opentripmap.org/catalog |
| `rate` | query | 最低评分：`1`/`2`/`3`，加 `h` 表示文化遗产如 `2h`。枚举：`1,2,3,1h,2h,3h` |
| `src_geom` | query | 几何数据来源，枚举：`osm,wikidata,snow,cultura.ru,rosnedra` |
| `src_attr` | query | 属性数据来源，逗号分隔，枚举：`osm,wikidata,snow,cultura.ru,rosnedra,user` |
| `format` | query | 输出格式：`json`/`geojson`（默认）/`count` |
| `limit` | query | 最大返回数（bbox/radius 默认 500，autosuggest 默认 10） |
| `offset` | query | 分页偏移量（bbox/radius） |
| `name` | query | 名称前缀过滤，bbox/radius 最少 3 字符；autosuggest 为搜索词（最少 3 字符） |
| `props` | query | 仅 autosuggest：`base`（默认，仅搜标题）/`address`（同时搜地址） |
| `country` | query | 仅 geoname：ISO-3166 两字母国家代码，缩小地名歧义 |

## 端点详解

### 1. geoname — 按地名获取坐标

`GET /{lang}/places/geoname?name=<地名>&apikey=<KEY>`

基于 GeoNames 数据库返回与搜索字符串最相似的地名坐标。

**参数**：`name`(必填, string)、`country`(可选, ISO-3166 两字母)

**响应**（`Geoname`）：
```json
{
  "name": "Moscow",
  "country": "RU",
  "lon": 37.61556,
  "lat": 55.75222,
  "timezone": "Europe/Moscow",
  "population": 10381222,
  "partial_match": false
}
```
- `partial_match`：是否未精确匹配
- 状态：200 成功，404 未找到

### 2. bbox — 按矩形区域获取 POI 列表

`GET /{lang}/places/bbox?lon_min=&lat_min=&lon_max=&lat_max=&apikey=<KEY>`

返回矩形框内 POI，仅含基础信息：`xid, name, kinds, osm, wikidata, point`。

**必填**：`lon_min`, `lon_max`, `lat_min`, `lat_max`（number/double）
**可选**：`src_geom, src_attr, kinds, name, rate, format, limit`

### 3. radius — 按圆心+半径获取 POI 列表

`GET /{lang}/places/radius?radius=<米>&lon=&lat=&apikey=<KEY>`

返回距选定点最近的一批 POI，按距离排序，含 `dist` 字段（距选定点米数）。

**必填**：`radius`(米, double), `lon`, `lat`(double)
**可选**：`src_geom, src_attr, kinds, name, rate, format, limit`

### 4. autosuggest — 搜索建议

`GET /{lang}/places/autosuggest?name=<片段>&radius=&lon=&lat=&apikey=<KEY>`

返回与搜索词最接近的 POI 建议，含 `highlighted_name`（搜索词高亮的名称）。

**必填**：`name`(≥3 字符), `radius`(米), `lon`, `lat`
**可选**：`src_geom, src_attr, kinds, rate, format, props, limit`

### 5. xid — 获取 POI 详情

`GET /{lang}/places/xid/{xid}?apikey=<KEY>`

返回单个对象的完整属性，信息丰富度因对象而异。

**必填**：`xid`(路径参数，OpenTripMap 唯一标识，如 `Q372040`、`R4682064`、`W286786280`)
**状态**：200 成功，404 未找到

## Instructions

典型调用流程（地名 → 坐标 → POI 列表 → 详情）：

### Step 1: 地理编码（地名 → 坐标）
`GET /{lang}/places/geoname?name=<地名>` 得到 `lon, lat`。
> 注意：中文城市名需用拼音或直接传坐标。

### Step 2: 坐标 → POI 列表
`GET /{lang}/places/radius?radius=1000&lon=&lat=&rate=2&format=count` 先看数量，再 `format=json` 取列表（按距离排序，含 `dist` 字段）。或用 `bbox` 按矩形区域检索。

### Step 3: 列表项 → 详情
对每个 `item.xid` 调 `GET /{lang}/places/xid/{xid}` 取 `preview`、`wikipedia_extracts.html`、`info.descr`、`image` 等丰富字段。

### Step 4: 分页与过滤
`offset` 递增分页，`limit` 控制每页（radius/bbox 默认上限 500）。`kinds` 按类目过滤，`rate` 按最低评分过滤（`h` 后缀=文化遗产）。

## 响应数据结构

### SimpleFeature（bbox/radius 的 json 数组项）

```json
{
  "xid": "R4682064",
  "name": "Oakland City Hall",
  "kinds": "architecture,other_buildings_and_structures,historic_architecture,interesting_places",
  "osm": "relation/4682064",
  "wikidata": "Q932794",
  "dist": 123.45,
  "point": { "lon": -122.272705, "lat": 37.80513 }
}
```

### SimpleSuggestFeature（autosuggest 的 json 数组项）

比 SimpleFeature 多 `highlighted_name` 字段（搜索词被 `<b>` 包裹）。

### Places（xid 详情响应，字段按对象可有可无）

```json
{
  "xid": "W286786280",
  "name": "Bellfry",
  "kinds": "architecture,towers,interesting_places,bell_towers",
  "osm": "way/286786280",
  "wikidata": "Q4228276",
  "rate": "3h",
  "image": "https://data.opentripmap.com/images/.../original.jpg",
  "preview": { "source": "<缩略图URL>", "width": 100, "height": 100 },
  "wikipedia": "https://ru.wikipedia.org/wiki/...",
  "wikipedia_extracts": { "title": "...", "text": "纯文本摘录", "html": "受限HTML摘录" },
  "voyage": "<WikiVoyage 链接>",
  "url": "<对象官网>",
  "otm": "https://opentripmap.com/ru/card/W286786280",
  "info": { "descr": "对象描述", "src": "...", "src_id": 13 },
  "sources": {
    "geometry": "osm",
    "attributes": ["osm", "user", "wikidata"]
  },
  "bbox": { "lon_min": ..., "lon_max": ..., "lat_min": ..., "lat_max": ... },
  "point": { "lon": 38.366169, "lat": 59.857269 }
}
```

`rate` 枚举：`0,1,2,3,1h,2h,3h`（h 后缀=文化遗产）

### FeatureCollection（geojson 格式）

标准 GeoJSON FeatureCollection，`features[].properties` 含 `xid, name, kinds, osm, wikidata`，`features[].geometry` 为 Point。

## 调用示例

### curl

```bash
APIKEY=$(cat apikey.txt)

# 1. 查询地名坐标
curl "https://api.opentripmap.com/0.1/en/places/geoname?name=London&apikey=$APIKEY"

# 2. 按矩形区域查景点（前 10 条）
curl "https://api.opentripmap.com/0.1/en/places/bbox?lon_min=-0.13&lat_min=51.5&lon_max=-0.11&lat_max=51.52&limit=10&apikey=$APIKEY"

# 3. 按半径查（500 米内 rated>=2）
curl "https://api.opentripmap.com/0.1/en/places/radius?radius=500&lon=-0.13&lat=51.5&rate=2&limit=10&apikey=$APIKEY"

# 4. 搜索建议
curl "https://api.opentripmap.com/0.1/en/places/autosuggest?name=big&radius=1000&lon=-0.13&lat=51.5&apikey=$APIKEY"

# 5. 获取 POI 详情
curl "https://api.opentripmap.com/0.1/en/places/xid/Q372040?apikey=$APIKEY"

# 6. 仅获取数量
curl "https://api.opentripmap.com/0.1/en/places/bbox?lon_min=-0.13&lat_min=51.5&lon_max=-0.11&lat_max=51.52&format=count&apikey=$APIKEY"
```

### JavaScript（fetch，来自官方示例）

```javascript
const apiKey = ""; // 填入你的 API Key

function apiGet(method, query) {
  return new Promise((resolve, reject) => {
    let url = `https://api.opentripmap.com/0.1/en/places/${method}?apikey=${apiKey}`;
    if (query !== undefined) url += `&${query}`;
    fetch(url)
      .then(r => r.json())
      .then(data => resolve(data))
      .catch(err => console.log("Fetch Error:", err));
  });
}

// 典型流程：地名 → 坐标 → radius 取列表 → xid 取详情
apiGet("geoname", "name=London").then(data => {
  if (data.status !== "OK") return;
  const { lon, lat } = data;
  // 先取数量
  return apiGet("radius", `radius=1000&limit=5&offset=0&lon=${lon}&lat=${lat}&rate=2&format=count`);
}).then(countData => {
  // 再取列表
  return apiGet("radius", `radius=1000&limit=5&offset=0&lon=${lon}&lat=${lat}&rate=2&format=json`);
}).then(list => {
  // 对每个 item 用 xid 取详情
  return apiGet("xid/" + list[0].xid);
}).then(detail => {
  // detail 包含 preview、wikipedia_extracts、info.descr 等
  console.log(detail);
});
```

## 典型调用流程

1. **地名 → 坐标**：`geoname?name=<地名>` 得到 `lon, lat`
2. **坐标 → POI 列表**：`radius?radius=1000&lon=&lat=&rate=2&format=count` 先看数量，再 `format=json` 取列表
3. **列表项 → 详情**：对每个 `item.xid` 调 `xid/{xid}` 取 `preview`、`wikipedia_extracts.html`、`info.descr`

分页：`offset` 递增，`limit` 控制每页（radius/bbox 默认上限 500）。

## 注意事项

- 返回数据语言依赖 `lang` 路径参数；若对象无对应语言数据，回退到英语或其他可用语言
- `xid` 前缀反映来源类型：`Q`=Wikidata、`R`=OSM relation、`N`=OSM node、`W`=OSM way
- `kinds` 是逗号分隔的类目列表（层级结构），完整树见 https://dev.opentripmap.org/catalog
- 详情字段稀疏：不同对象类型返回字段差异大，缺失字段应优雅处理
- 完整机器可读规范见同目录 `openapi.en.json`（OpenAPI 3.0，可直接导入 Postman/Swagger）

## Troubleshooting

- **geoname 返回 404**：地名未找到。尝试拼音、英文名或加 `country` 参数（ISO-3166 两字母）缩小歧义。
- **radius/bbox 无结果**：`rate` 或 `kinds` 过滤过严。放宽 `rate` 或扩大 `radius`，或移除 `kinds` 用默认 `interesting_places`。
- **详情字段稀疏/缺失图片**：不同对象类型返回字段差异大。对缺失字段优雅降级，不假设字段必填。
- **autosuggest 无建议**：`name` 少于 3 字符。搜索词至少 3 字符，且需提供 `radius`/`lon`/`lat`。
- **中文地名无法地理编码**：API 基于 GeoNames，不支持中文。改用拼音或直接传坐标。