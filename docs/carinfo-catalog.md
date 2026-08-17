# 车型目录

车型搜索能力由 `backend/app/skills/carinfo.py` 的 `CarInfoCatalogAdapter`（注册名 `carinfo.catalog`）提供，经 Skill Registry 暴露为：

```text
POST /api/v1/skills/carinfo/search
```

`backend/app/api/skills.py` 的 `carinfo_search` 端点按入参决定走哪个 Adapter：`query` 非空时路由到真实的 `carinfo.catalog`（具体车型库），`query` 为空时路由到确定性的 `carinfo.demo`（用于旧规划调用里只按动力类型过滤的场景）。

## 请求

```json
{ "query": "特斯拉 Model 3", "limit": 12 }
```

- `query`：必填，2–80 字符的品牌/车系/车型关键词。
- `limit`：可选，默认 12，范围 1–30。

## 目录响应

适配器调用车型库 `https://tool.bitefu.net/car/`（`type=info&keyword=...`），把返回的每个条目规整成可落档的车型项，含：`brand`/`series`/`model`/`year`、按名称推断的 `power_type`（electric/hybrid/fuel，含对特斯拉、蔚来、小鹏等纯电品牌名的启发）、`state` 与 `state_label`（在售/停售在库/进口·其他/历史款）、价格区间 `price_min_cny`/`price_max_cny`、来源链接 `source_url`，以及 `specs_missing` 提示清单。

目录端点只提供车型身份、年款与价格，不承诺可靠的按配置续航/能耗/电池——因此标回 `rated_range_km`、`battery_kwh`、`consumption_per_100km`、`max_charge_kw`、`height_m`、`width_m` 等字段置空，让用户在保存前按自己的具体配置确认，而不是静默继承演示 SUV 的数值。

目录服务访问失败或没有匹配时返回结构化的 `error_code=CARINFO_NO_RESULTS`（带可解释 warning）或空结果，绝不伪造续航/能耗等规格。成功响应 `success=true` 并携带来源 `SourceRecord`，缓存 `cache_ttl_seconds=3600`。

## 确定性回退（carinfo.demo）

`CarInfoDemoAdapter`（`carinfo.demo`）返回内置的 RoadMan 固定样本：`Explorer 纯电 SUV` 与 `Tourer 混动 SUV`（均标 `estimated=true`）。它只按可选的 `brand`/`power_type` 过滤，用于规划侧按动力类型取样，不用于新建真实车型。

## 前端接入（HomeView.vue 车辆抽屉）

`frontend/src/views/HomeView.vue` 的车辆抽屉先搜索再确认：

- `searchVehicleModels()` 调 `searchVehicleCatalog(query)` 拉目录结果；空 query（或不足 2 字符）提示输入，无结果提示换关键词。
- `applyVehicleCatalogItem(item)` 把选中条目回填到 `VehicleDraft`：身份字段（brand/series/model/year/power_type）直接写入；目录缺失的技术规格仅在**编辑已有车辆**时保留原值，新建车辆则留空，避免用户误以为演示 SUV 的数值属于当前车型。
- 回填后若 `specs_missing` 非空，提示「所需规格需按具体配置确认」。

车辆资源（增删改查、选择进行程）由 `/api/v1/vehicles` 提供，模型为 `VehicleProfile`（见 [domain-model.md](domain-model.md)）。凭据与目录响应不写入日志。