---
name: carinfo
description: 汽车品牌、车系、车型信息查询技能，基于 bitefu CarApi（汽车之家/易车网等数据源）。获取品牌列表、车系列表、车型配置与报价、年款、车身颜色与内饰颜色等数据。Use when user says "查汽车品牌"、"车型查询"、"车系列表"、"某品牌有哪些车"、"车型配置"、"汽车报价"、"车身颜色"、"内饰颜色"、"某车年款"、"查大众"、"丰田车系"、"car brands"、"car models"、"car series"、"vehicle specifications"、"car pricing"，或需要查询汽车品牌→车系→车型→配置颜色等层级信息时。无需 API Key。
license: MIT
metadata:
  version: "1.0"
  author: bitefu / CarApi
  category: car-info
  homepage: https://tool.bitefu.net/car/
  data-sources: [汽车之家, 易车网, 瓜子二手车, 58二手车, 淘车网]
  base-url: https://tool.bitefu.net/car/
---

# CarInfo — 汽车品牌车系车型查询 Skill

基于 [bitefu CarApi](https://tool.bitefu.net/car/) 的免费汽车信息查询技能。数据来源涵盖汽车之家、易车网、瓜子二手车、58 二手车、淘车网，收录 648+ 品牌、7000+ 车系、90000+ 车型。

> **无需 API Key**，支持 HTTP/HTTPS 的 GET 和 POST 请求。
> **注意**：本接口调用量极大，不建议正式商用；商用请到 [Gitee 仓库](https://gitee.com/web/CarApi) 下载开源版本部署到自有服务器。大量调用需联系作者添加 IP 白名单。

## 核心能力（6 个查询类型）

| 类型 | `type=` | 用途 | 关键参数 |
|------|---------|------|---------|
| 品牌列表 | `brand` | 获取全部汽车品牌 | 无（可分页） |
| 车系列表 | `series_group` | 按品牌获取车系分组 | `id={brand_id}` |
| 车系详情 | `series` | 获取单个车系信息 | `id={series_id}` |
| 车型列表 | `info` | 获取车系下的车型列表 | `id={series_id}` 或 `id={info_id}` |
| 年款列表 | `infoyear` | 获取车型可用年款 | `id={info_id}` |
| 车型详情 | `detail` | 获取配置/颜色/内饰等详情 | `id={car_spec_id}` |

完整参数表、字段定义、响应格式见 [`references/api-reference.md`](references/api-reference.md)。

## Instructions

数据按 **品牌 → 车系 → 车型 → 详情** 四级层级组织。按以下流程组合查询：

### Step 1: 获取品牌列表
```
GET https://tool.bitefu.net/car/?type=brand
```
返回全部品牌（648 个），每条含 `id`、`name`、`img`、`firstletter`（首字母）。
从结果中找到目标品牌的 `id`（如 大众 id=33、丰田 id=120）。

### Step 2: 获取品牌下的车系
```
GET https://tool.bitefu.net/car/?type=series_group&id={brand_id}
```
返回该品牌的车系分组列表，每条含 `id`（车系组 id）、`group_id`、`brand_id`、`name`（车系名）、`firstletter`。

> 若已知车系 id，可直接跳到 Step 3 用 `type=series&id={series_id}` 取车系详情。

### Step 3: 获取车系详情（可选）
```
GET https://tool.bitefu.net/car/?type=series&id={series_id}
```
返回单个车系完整信息：`full_name`、`minprice`/`maxprice`（指导价区间）、`seriesplace`（合资/国产/进口）、`has_info`（是否有车型数据）、`img`、`logo`。

### Step 4: 获取车型列表
```
GET https://tool.bitefu.net/car/?type=info&id={series_id}
```
返回该车系下所有车型，每条含 `id`（车型 id）、`full_name`、`name`、`brand_name`、`group_name`、`series_name`、`minprice`/`maxprice`、`year`、`state`。

> `id` 既可传 `series_id`（返回该车系全部车型），也可传具体 `info_id`（返回单条车型）。

### Step 5: 获取年款（可选）
```
GET https://tool.bitefu.net/car/?type=infoyear&id={info_id}
```
返回该车型可用的年款字符串数组（如 `["2026","2025"]`）。

### Step 6: 获取车型详情
```
GET https://tool.bitefu.net/car/?type=detail&id={car_spec_id}
```
返回车型配置与颜色详情：
- `color` — 车身颜色列表（`name`、`value` 色值、`picnum` 图片数）
- `innercolor` — 内饰颜色列表
- `configbag` — 选装包配置
- `param` / `config` — 参数与配置（部分车型可能为 null）

## 调用示例

### curl
```bash
# 1. 获取全部品牌
curl "https://tool.bitefu.net/car/?type=brand"

# 2. 获取大众（id=33）的车系
curl "https://tool.bitefu.net/car/?type=series_group&id=33"

# 3. 获取车系详情（series_id=690）
curl "https://tool.bitefu.net/car/?type=series&id=690"

# 4. 获取车系下的车型列表
curl "https://tool.bitefu.net/car/?type=info&id=690"

# 5. 获取车型可用年款
curl "https://tool.bitefu.net/car/?type=infoyear&id=1024232"

# 6. 获取车型详情（颜色/配置）
curl "https://tool.bitefu.net/car/?type=detail&id=43558"
```

### PowerShell
```powershell
Invoke-RestMethod -Uri "https://tool.bitefu.net/car/?type=brand"
Invoke-RestMethod -Uri "https://tool.bitefu.net/car/?type=series_group&id=33"
```

### JavaScript（fetch）
```javascript
const API = "https://tool.bitefu.net/car/";

// 典型流程：品牌 → 车系 → 车型 → 详情
async function queryCar(brandName) {
  // 1. 取品牌列表，找到目标品牌 id
  const brands = await fetch(`${API}?type=brand`).then(r => r.json());
  const brand = brands.info.find(b => b.name === brandName);
  if (!brand) throw new Error("品牌未找到");

  // 2. 取该品牌车系
  const series = await fetch(`${API}?type=series_group&id=${brand.id}`).then(r => r.json());

  // 3. 取第一个车系的车型
  const models = await fetch(`${API}?type=info&id=${series.info[0].id}`).then(r => r.json());

  // 4. 取第一个车型详情
  const detail = await fetch(`${API}?type=detail&id=${models.info[0].id}`).then(r => r.json());
  return { brand, series: series.info, models: models.info, detail: detail.info };
}
```

## 典型调用流程

1. **查某品牌有哪些车**：`type=brand` 找品牌 id → `type=series_group&id={brand_id}` 取车系列表
2. **查某车系的所有车型与报价**：`type=info&id={series_id}` 取车型列表（含 minprice/maxprice）
3. **查某车型颜色与配置**：`type=detail&id={car_spec_id}` 取颜色/内饰/选装包
4. **查某车型有哪些年款**：`type=infoyear&id={info_id}` 取年款数组

## 数据层级与 ID 关系

```
brand (品牌)                  type=brand           → id=品牌id
  └─ series_group (车系组)     type=series_group    → id=品牌id → 返回车系列表
      └─ series (车系详情)     type=series          → id=车系id → 返回车系详情
          └─ info (车型)       type=info            → id=车系id → 返回车型列表
              ├─ infoyear      type=infoyear        → id=车型id → 返回年款
              └─ detail        type=detail          → id=车型id → 返回颜色/配置
```

> **ID 作用域**：每个 `type` 的 `id` 含义不同。`series_group` 的 id 是品牌 id；`series` 的 id 是车系 id；`info` 的 id 是车系 id 或车型 id；`infoyear`/`detail` 的 id 是车型 id。层级 id 不可混用，否则返回"没有查询到数据"。

## 注意事项

- **免费无 Key**：直接 HTTP/HTTPS 调用，GET/POST 均可
- **数据时效**：汽车之家数据源更新最及时（2026-06-09 更新），其它源限时免费且更新较旧
- **调用频率**：本站有防火墙处理，大量调用需联系作者添加 IP 白名单（每个 IP 100 元）
- **商用建议**：不建议正式商用，商用请下载开源版本部署到自有服务器
- **响应编码**：返回 JSON，UTF-8 编码（中文字符正常）
- **状态字段**：`status=1` 成功，`status=0` 失败（`info` 字段为错误说明，如"没有查询到数据"）
- **图片地址**：品牌/车系 `img` 字段为 `//` 开头的协议相对路径，使用时需补全 `https:`
- **价格单位**：`minprice`/`maxprice` 单位为元（如 128600 = 12.86 万元）
- **部分字段可能为空**：`detail` 中的 `param`/`config`/`search` 部分车型为 null，需优雅处理

## Troubleshooting

- **返回 `{"info":"没有查询到数据","status":0}`**：id 类型传错。检查当前 `type` 对应的 id 含义（见"数据层级与 ID 关系"），层级 id 不可混用。
- **`series` 用品牌 id 查询无结果**：`type=series` 的 id 是车系 id，不是品牌 id。查品牌下的车系请用 `type=series_group&id={brand_id}`。
- **`infoyear`/`detail` 返回空**：id 必须是车型 id（来自 `type=info` 返回的 `id`），不能用品牌或车系 id。
- **被防火墙拦截**：调用频率过高。联系作者添加 IP 白名单，或下载开源版本自部署。
- **图片不显示**：`img` 字段为 `//` 开头，需补全为 `https://img...`。
- **中文乱码**：响应为 UTF-8，确保客户端按 UTF-8 解码（PowerShell 控制台默认 GBK，需用 `[System.IO.File]::WriteAllText` 或 `Invoke-RestMethod` 正确处理）。
- **价格读不懂**：`minprice`/`maxprice` 单位是元，除以 10000 得万元。

## 相关链接

- [CarApi 在线接口](https://tool.bitefu.net/car/)
- [Gitee 仓库](https://gitee.com/web/CarApi)
- [接口文档 Wiki](https://gitee.com/web/CarApi/wikis)
- [免费下载源码/数据库](https://gitee.com/web/CarApi/wikis/%E5%85%8D%E8%B4%B9%E4%B8%8B%E8%BD%BD)