# 车型搜索与入库

`POST /api/v1/skills/carinfo/search` 由 `CarInfoCatalogAdapter` 提供车型检索。输入用户自然语言中的品牌、车系、车型或年款，例如：

```json
{ "query": "小鹏 G6", "limit": 12 }
```

## 查询链路

1. 先查询主车型目录，获取品牌、车系、年款、在售状态、价格和参数详情。
2. 对品牌搜索按 `brand_id` 展开车系，对具体车型按 `series_id` 查询年款；详情请求并发执行并限制数量，避免单个失效接口拖住页面。
3. 主目录没有覆盖请求车型时，动态并行查询公开数据源：
   - [AutoSeeker JSON](https://autoseeker.eu/data/models.json)：纯电、混动和燃油车型的续航/油耗、电池、座位、尺寸、功率和充电功率等；CC BY 4.0。
   - [OpenEV Data](https://gaia-charge.github.io/evdb/v1/vehicles.json)：电动车版本、电池、WLTP/实测续航、直流充电功率和性能；CC BY-SA 4.0。
   - [AppByte Fleet Catalog](https://fleetcatalog.disturbingbyte.pt)：公开 REST 车型目录，按品牌、车系和具体版本返回燃油/混动油耗、油箱、电池、动力、尺寸和年款。
   - [CarNewsChina suggestion](https://data.carnewschina.com/suggest)：公开车型页和具体年款参数，作为网页级补充。
4. 每个结果保留 `source_id`、`source_url`、`detail_source_url`、`catalog_source`、`fallback_used` 和原始 `specifications`，前端可以查看依据。

车型匹配完全基于用户输入和公开数据的品牌/车系/年款字段，不维护 SU7、Model 3 或其他车型白名单。中文品牌提示会参与匹配，避免把“小鹏 G6”误返回成其他品牌的 G6P；具体版本词（Max、Pro、Performance 等）会优先选择确实包含该版本的记录。

## 字段和诚实降级

规划使用的字段映射为 `rated_range_km`、`battery_kwh`、`consumption_per_100km`、`max_charge_kw`、`dc_charge_time_hours`、`height_m`、`width_m` 和 `seats`。公开来源没有给出的字段保持 `null`，同时写入 `specs_missing`，不会用默认值冒充真实数据。WLTP 等非中国工况续航会标记在 `estimated_fields` 中，规划时应按保守余量使用。

SU7 Standard/Pro/Max 的固定缓存仍保留，但仅在公开网络完全不可用时作为最后保障，并带有原始公开页面链接；它不是通用车型列表，也不会覆盖动态数据源的结果。

适配器版本当前为 `1.8.12`。版本变化会使旧的“只有车名”缓存失效；成功结果缓存 6 小时，空结果不会写入缓存。

## 前端入库

`frontend/src/views/HomeView.vue` 的车型抽屉支持搜索、查看来源和参数、直接添加、编辑、删除以及切换当前车型。保存到 `VehicleProfile` 的 JSON 文档中，无需数据库迁移；`dc_charge_time_hours` 与 `specifications` 会一并保存。

## 本地验证

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/skills/carinfo/search `
  -Method Post -ContentType 'application/json' `
  -Body '{"query":"小鹏 G6","limit":5}'
```

验收时应看到真实品牌、年款、续航、电池、能耗/充电参数和非空 `specifications`。当某一来源不可用时，响应仍应返回其他来源的结果或 `CARINFO_NO_RESULTS`，并在 `fallback_provider`/`specs_missing` 中说明原因。
