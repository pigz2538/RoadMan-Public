# 车型数据库接入

车型管理使用 `Skills/carinfo` 的 Bitefu CarApi 车型目录：

- 后端适配器：`carinfo.catalog`
- 接口：`POST /api/v1/skills/carinfo/search`
- 请求：`{"query":"特斯拉 Model 3","limit":12}`
- 返回：品牌、车系、具体年款、动力类型、在售状态、价格区间和来源链接

目录接口不保证每个具体配置的续航、能耗、电池容量、车身尺寸等参数。搜索结果会明确标记缺失字段；前端一键填入/直接添加时不会把演示车型的数值复制到新车型，用户可以在表单中确认后补充。

不需要新增 API Key 或 Python 依赖；适配器使用现有 `httpx` 和 Skill Registry 的超时、缓存、审计机制。
