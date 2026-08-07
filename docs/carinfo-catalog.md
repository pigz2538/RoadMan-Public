# 车型目录

车型管理的搜索能力由 `carinfo.catalog` Adapter 提供，接口为：

```text
POST /api/v1/skills/carinfo/search
```

请求示例：

```json
{"query": "Tesla Model 3", "limit": 12}
```

响应返回品牌、车系、具体年款/车型、动力类型、在售状态、价格区间和来源链接。目录服务无法访问时返回结构化失败或空结果，不伪造续航/能耗。

前端添加车型时应先搜索并让用户确认具体车型，再把可确认字段写入 `VehicleProfile`；不把演示 SUV 的默认值复制到新车型。用户仍可手动补充目录缺失字段。

车型 CRUD：`/api/v1/vehicles`。凭据和目录响应不写入日志。
