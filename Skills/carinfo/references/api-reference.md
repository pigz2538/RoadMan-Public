# CarApi 接口参考文档

> Base URL: `https://tool.bitefu.net/car/`
> 请求方式: GET / POST（HTTP 与 HTTPS 均支持）
> 认证: 无需 API Key
> 数据来源: 汽车之家、易车网、瓜子二手车、58 二手车、淘车网

## 通用响应格式

所有端点返回 JSON：

```json
{
  "status": 1,           // 1=成功，0=失败
  "info": [...] | "...", // 成功为数据数组/对象，失败为错误说明字符串
  "total": "648"          // 成功时为记录总数（字符串），部分端点无此字段
}
```

失败时：
```json
{ "info": "没有查询到数据", "status": 0 }
```

---

## 端点 1: `type=brand` — 品牌列表

获取全部汽车品牌。

### 请求
```
GET https://tool.bitefu.net/car/?type=brand
```

无必填参数。

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 品牌唯一 ID |
| `name` | string | 品牌名称（如 "大众"、"丰田"、"AUDI"） |
| `img` | string | 品牌 Logo 图片地址（`//` 开头，需补全 `https:`） |
| `firstletter` | string | 品牌名首字母（A-Z，用于字母索引） |
| `addtime` | string | 入库时间戳 |
| `updatetime` | string | 更新时间戳 |

### 响应示例
```json
{
  "status": 1,
  "info": [
    {
      "id": "696", "name": "AIVA",
      "img": "//car2.autoimg.cn/cardfs/series/g33/M05/C8/2B/autohomecar__ChxpVmon-8eAd6ylAACARBZgrao331.png",
      "firstletter": "A", "addtime": "1781011070", "updatetime": "1781011070"
    },
    {
      "id": "33", "name": "大众",
      "img": "//car2.autoimg.cn/cardfs/series/g26/M0B/AE/B3/autohomecar__wKgHEVs9u5WAV441AAAKdxZGE4U148.png",
      "firstletter": "D", "addtime": "1675738779", "updatetime": "1675738779"
    }
  ],
  "total": "648"
}
```

### 数据规模
- 汽车之家源: 648 品牌
- 易车网源: 458 品牌
- 瓜子二手车源: 209 品牌
- 58 二手车源: 364 品牌
- 淘车网源: 382 品牌

---

## 端点 2: `type=series_group` — 车系列表（按品牌）

获取某品牌下的车系分组列表。

### 请求
```
GET https://tool.bitefu.net/car/?type=series_group&id={brand_id}
```

### 参数

| 参数 | 位置 | 必填 | 说明 |
|------|------|------|------|
| `type` | query | 是 | 固定 `series_group` |
| `id` | query | 是 | 品牌 ID（来自 `type=brand` 的 `id`） |

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 车系组记录 ID |
| `group_id` | string | 关联的品牌 ID |
| `brand_id` | string | 关联品牌 ID（可能与 group_id 不同，因多源合并） |
| `name` | string | 车系名称（如 "奇瑞汽车"） |
| `firstletter` | string | 首字母 |
| `addtime` | string | 入库时间戳 |
| `updatetime` | string | 更新时间戳 |

### 响应示例
```json
{
  "status": 1,
  "info": [
    {
      "id": "758", "group_id": "33", "brand_id": "690",
      "name": "奇瑞汽车", "firstletter": "Q",
      "addtime": "1781011424", "updatetime": "1781011424"
    }
  ],
  "total": "9"
}
```

---

## 端点 3: `type=series` — 车系详情

获取单个车系的详细信息。

### 请求
```
GET https://tool.bitefu.net/car/?type=series&id={series_id}
```

### 参数

| 参数 | 位置 | 必填 | 说明 |
|------|------|------|------|
| `type` | query | 是 | 固定 `series` |
| `id` | query | 是 | 车系 ID（来自 `type=series_group` 的 `id`） |

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 车系 ID |
| `group_id` | string | 车系组 ID |
| `brand_id` | string | 品牌 ID |
| `full_name` | string | 车系全名（如 "现代领翔"） |
| `name` | string | 车系简称（如 "领翔"） |
| `firstletter` | string | 首字母 |
| `seriesstate` | string | 车系状态码（如 "40"） |
| `seriesorder` | string | 排序值 |
| `img` | string | 车系图片地址 |
| `logo` | string | 车系 Logo 地址 |
| `minprice` | string | 最低指导价（单位：元） |
| `maxprice` | string | 最高指导价（单位：元） |
| `seriesplace` | string | 产地类型（"合资"/"国产"/"进口"） |
| `has_info` | string | 是否有车型数据（"1"=有） |
| `addtime` | string | 入库时间戳 |
| `updatetime` | string | 更新时间戳 |

### 响应示例
```json
{
  "status": 1,
  "info": [
    {
      "id": "690", "group_id": "27", "brand_id": "12",
      "full_name": "现代领翔", "name": "领翔",
      "firstletter": "B", "seriesstate": "40", "seriesorder": "4",
      "img": "https://car1.autoimg.cn/upload/spec/4612/4612186222841.jpg",
      "logo": "https://car2.autoimg.cn/cardfs/series/g25/M03/27/F3/autohomecar__wKgHIlqnQA6AFIZwAATobl4sC9g698.png",
      "minprice": "155800", "maxprice": "228800",
      "seriesplace": "合资", "has_info": "1",
      "addtime": "1675738779", "updatetime": "1675738779"
    }
  ],
  "total": "1"
}
```

---

## 端点 4: `type=info` — 车型列表

获取车系下的所有车型（含报价、年款）。

### 请求
```
GET https://tool.bitefu.net/car/?type=info&id={series_id}
```

### 参数

| 参数 | 位置 | 必填 | 说明 |
|------|------|------|------|
| `type` | query | 是 | 固定 `info` |
| `id` | query | 是 | 车系 ID（返回该车系全部车型）或车型 ID（返回单条车型） |

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 车型 ID（用于 infoyear / detail 查询） |
| `brand_id` | string | 品牌 ID |
| `group_id` | string | 车系组 ID |
| `series_id` | string | 车系 ID |
| `full_name` | string | 车型全名（如 "江铃福顺2026款 2.0T 6MT柴油短轴中高顶..."） |
| `name` | string | 车型名（含年款，如 "2026款 2.0T 6MT..."） |
| `brand_name` | string | 品牌名 |
| `group_name` | string | 车系组名 |
| `series_name` | string | 车系名 |
| `state` | string | 车型状态码（如 "20"=在售，"0"=停售） |
| `minprice` | string | 最低价（单位：元） |
| `maxprice` | string | 最高价（单位：元） |
| `year` | string | 年款（如 "2026"） |
| `addtime` | string | 入库时间戳 |
| `updatetime` | string | 更新时间戳 |

### 响应示例
```json
{
  "status": 1,
  "info": [
    {
      "id": "1024232", "brand_id": "119", "group_id": "307", "series_id": "6838",
      "full_name": "江铃福顺2026款 2.0T 6MT柴油短轴中高顶物流通勤版9座（3/3/3）-无后空调",
      "name": "2026款 2.0T 6MT柴油短轴中高顶物流通勤版9座（3/3/3）-无后空调",
      "brand_name": "江铃", "group_name": "江铃汽车", "series_name": "江铃福顺",
      "state": "20", "minprice": "128600", "maxprice": "128600", "year": "2026",
      "addtime": "1781014176", "updatetime": "1781014176"
    }
  ]
}
```

---

## 端点 5: `type=infoyear` — 年款列表

获取某车型可用的年款。

### 请求
```
GET https://tool.bitefu.net/car/?type=infoyear&id={info_id}
```

### 参数

| 参数 | 位置 | 必填 | 说明 |
|------|------|------|------|
| `type` | query | 是 | 固定 `infoyear` |
| `id` | query | 是 | 车型 ID（来自 `type=info` 的 `id`） |

### 响应字段

`info` 为字符串数组，每个元素为年款字符串。

### 响应示例
```json
{
  "status": 1,
  "info": ["2026"],
  "total": 1
}
```

多年款示例：
```json
{ "status": 1, "info": ["2025", "2024", "2023"], "total": 3 }
```

---

## 端点 6: `type=detail` — 车型详情

获取车型的配置、车身颜色、内饰颜色、选装包等详情。

### 请求
```
GET https://tool.bitefu.net/car/?type=detail&id={car_spec_id}
```

### 参数

| 参数 | 位置 | 必填 | 说明 |
|------|------|------|------|
| `type` | query | 是 | 固定 `detail` |
| `id` | query | 是 | 车型 ID（来自 `type=info` 的 `id`） |

### 响应字段

`info` 为对象，包含以下子字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `color` | array | 车身颜色列表 |
| `innercolor` | array | 内饰颜色列表 |
| `configbag` | array | 选装包配置 |
| `param` | object \| null | 车辆参数（部分车型为 null） |
| `config` | object \| null | 配置项（部分车型为 null） |
| `search` | object \| null | 搜索辅助数据（部分为 null） |
| `configtips` | object \| null | 配置提示 |

#### color / innercolor 结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `specid` | integer | 关联车型 ID |
| `seriesid` | integer | 关联车系 ID |
| `coloritems` | array | 颜色项列表 |

#### coloritems 结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 颜色 ID |
| `name` | string | 颜色名（如 "冰川白"、"星空黑"） |
| `value` | string | 颜色色值（HEX，如 "#F0F0F0"） |
| `picnum` | integer | 该颜色图片数量 |
| `clubpicnum` | integer | 俱乐部图片数量 |

#### configbag 结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 选装包名称 |
| `bagitems` | array | 选装包项目列表 |

### 响应示例
```json
{
  "status": 1,
  "info": {
    "param": null,
    "config": null,
    "configbag": [
      {
        "name": "选装包",
        "bagitems": [{ "specid": 43558, "valueitems": [] }]
      }
    ],
    "search": null,
    "color": [
      {
        "specid": 43558, "seriesid": 5575,
        "coloritems": [
          { "id": 2086, "name": "冰川白", "value": "#F0F0F0", "picnum": 123, "clubpicnum": 0 },
          { "id": 6743, "name": "星空黑", "value": "#000000", "picnum": 0, "clubpicnum": 0 },
          { "id": 7450, "name": "旭日红", "value": "#82011F", "picnum": 0, "clubpicnum": 0 }
        ]
      }
    ],
    "innercolor": [
      {
        "specid": 43558, "seriesid": 5575,
        "coloritems": [
          { "id": 553, "name": "黑色", "value": "#000000", "picnum": 160, "clubpicnum": 0 }
        ]
      }
    ],
    "configtips": null,
    "specitems": []
  },
  "total": 1
}
```

---

## ID 层级速查

| 查询目标 | `type` | `id` 传入 | id 来源 |
|---------|--------|----------|--------|
| 全部品牌 | `brand` | 无 | — |
| 品牌下车系 | `series_group` | 品牌 id | `brand` 的 `id` |
| 车系详情 | `series` | 车系 id | `series_group` 的 `id` |
| 车型列表 | `info` | 车系 id | `series_group` 的 `id` 或 `series` 的 `id` |
| 年款 | `infoyear` | 车型 id | `info` 的 `id` |
| 车型详情 | `detail` | 车型 id | `info` 的 `id` |

---

## 数据源说明

| 数据源 | 状态 | 品牌数 | 车系数 | 车型数 |
|--------|------|--------|--------|--------|
| 汽车之家 | 主源 | 648 | 7099 | 92468 |
| 易车网 | 限时免费 | 458 | 4880 | 89541 |
| 瓜子二手车 | 限时免费 | 209 | 1959 | 47448 |
| 58 二手车 | 限时免费 | 364 | 2989 | 58138 |
| 淘车网 | 限时免费 | 382 | 3385 | 63965 |

> 汽车之家数据源更新最及时，推荐作为主数据源使用。