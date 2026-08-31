# 车型目录与车辆入库

车型搜索由 `backend/app/skills/carinfo.py` 的 `CarInfoCatalogAdapter` 提供，接口为：

```text
POST /api/v1/skills/carinfo/search
```

请求示例：

```json
{ "query": "特斯拉 Model 3", "limit": 12 }
```

## 查询链路

1. 先调用公开车型目录的 `type=info&keyword=...`，取得品牌、车系、具体年款、在售状态和价格区间。
2. 对候选年款并发调用 `type=detail&id=...`，解析供应商返回的参数分组，保留原始参数名称和值，并映射续航、电池、能耗、快充功率、尺寸和座位数。
3. 按“有可核验详情优先、在售和年款优先”排序，再返回用户请求的数量。每条记录都带 `source_id`、`source_url`、`detail_source_url` 和 `specifications`，前端可以追溯详情来源。
4. 供应商搜索对“品牌 + 车型”有时过于严格。例如“特斯拉 Model 3”可能搜不到而“Model 3”可以命中。适配器会在原词失败时最多尝试两个安全的后缀变体，并在响应的 `provider_query` 中披露实际命中的词，不会改变用户的原始 `query`。

详情接口并非覆盖所有年款：部分新款、进口款或旧款没有参数记录。此时保留身份信息，`specifications` 为空并填写 `specs_missing`，绝不伪造续航或电池数据；用户可以换一个有详情的具体年款或手动补充配置。这是上游数据缺失，不是前端丢字段。

## 前端选择与入库

`frontend/src/views/HomeView.vue` 的车型抽屉支持：

- 搜索品牌、车系或具体车型；点击结果会把身份和已核验配置回填表单。
- “直接添加”会调用 `POST /api/v1/vehicles`；编辑保存调用 `PATCH`，删除调用 `DELETE`，当前车型选择保存在本地存储。
- 已核验的参数在当前车型卡片中展示；缺失参数明确提示确认，不会把演示车型的默认值混入真实车型。

`VehicleProfile` 将来源和参数存入现有 JSON 文档字段，无需数据库迁移：
`source_id`、`source_url`、`detail_source_url`、`price_min_cny`、`price_max_cny`、`specifications`。

## 失败与缓存策略

- `carinfo.catalog` 的适配器版本为 `1.5.0`。版本变化会使旧的“只有车名”的缓存失效，避免升级后继续显示旧结果；详情查询最多并发 12 条，较大的 `limit` 仍会返回完整数量而不会拖垮接口。
- 没有命中返回 `CARINFO_NO_RESULTS`；网络或详情单条失败不会丢弃其他车型。
- 详情请求是尽力而为，搜索仍可返回身份记录；规划端只有在用户保存了可靠续航/能耗后才使用这些数值。
- 不带 `query` 的内部兼容调用仍走确定性的 `carinfo.demo`，用于旧规划测试，不会出现在真实车型搜索结果中。

## 本地验证

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/skills/carinfo/search `
  -Method Post -ContentType 'application/json' `
  -Body '{"query":"特斯拉 Model 3","limit":2}'
```

验收应看到 `rated_range_km`、`battery_kwh`、`consumption_per_100km` 和非空 `specifications`（具体年款以上游详情覆盖为准），随后用返回条目创建、读取、修改、删除一条车型，确认参数在 CRUD 往返中保持不变。
