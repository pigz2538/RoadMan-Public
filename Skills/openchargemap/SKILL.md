---
name: openchargemap
description: 查询全球最大的电动汽车充电桩开放数据注册库 Open Charge Map。获取充电站点 (POI) 列表、地址、连接器、功率、运营状态、运营商、费用、评论与照片，以及参考数据 ID 表。Use when user says "find EV charging stations"、"充电桩"、"电动汽车充电站"、"附近充电站"、"charging stations near me"、"查充电桩"、"快充站"、"充电站详情"，或需要按国家/坐标/边界框/运营商/连接类型过滤充电站点、获取参考数据、提交充电站评论/签到/照片时。
license: CC-BY-SA-4.0
metadata:
  version: "3.1"
  author: Open Charge Map
  category: ev-charging
  homepage: https://www.openchargemap.org
  base-url: https://api.openchargemap.io/v3
---

# Open Charge Map API Skill

> 一个可供 AI 直接调用的 Skill：查询全球最大的电动汽车充电桩开放数据注册库 (Open Charge Map)。
> 完整原始 OpenAPI 3.1 规范保留在 `references/ocm-openapi-spec.yaml`，本文件为浓缩可执行版本。

## 元信息

| 项 | 值 |
|---|---|
| Skill 名称 | `openchargemap` |
| 版本 | 3.1 |
| Base URL | `https://api.openchargemap.io/v3` |
| 数据许可 | Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0) |
| 服务条款 | https://openchargemap.org/site/about/terms |
| 输出格式 | `json`（默认，保真度最高）/ `geojson` / `xml` / `csv` |
| 仓库来源 | https://www.openchargemap.org/develop/api#/ |

## 认证（必需）

调用任何端点都必须提供 API Key，两种方式二选一：

1. **请求头**（推荐）：`X-API-Key: YOUR_KEY`（大小写敏感）
2. **URL 参数**：`?key=YOUR_KEY`

**获取 Key**：登录 https://openchargemap.org → 我的资料 → 我的应用 → 注册应用。

> ⚠️ 安全提醒：调用方应将 API Key 存于环境变量或密钥管理器，不要硬编码或提交进版本库。

附加请求头建议：设置自定义 `User-Agent` 以标识你的应用，便于 OCM 识别你的客户端。

## 公平使用政策（必读）

- 基础 API 为免费服务，无 SLA / 无保修。
- **不要重复调用重复查询**。对请求做 debounce / throttle，尽量减小服务器负载。
- OCM 管理员有权对过度或无差别调用方进行（自动）封禁。
- 高频大量查询请自建 API 镜像或导入数据到自有服务。

## 端点总览

| 端点 | 方法 | 鉴权 | 用途 |
|---|---|---|---|
| `/poi` | GET | API Key | 查询充电站点 (POI) 列表 —— **主端点** |
| `/referencedata` | GET | API Key | 获取参考数据 (连接类型、运营商、国家等 ID 表) |
| `/profile/authenticate` | POST | 无（换取 token） | 用户登录，返回 JWT |
| `/comment` | POST | UserAuthentication (JWT) | 提交评论或签到 |
| `/mediaitem` | POST | UserAuthentication (JWT) | 上传充电点照片 |
| `/openapi` | GET | 无 | 获取当前 OpenAPI 定义（YAML） |

鉴权方案：
- `APIKeyQueryString`：query 参数 `key`
- `APIKeyHeader`：header `X-API-Key`
- `UserAuthentication`：HTTP Bearer (JWT)，由 `/profile/authenticate` 换取

---

## 端点详解

### 1. GET `/poi` — 查询充电站点（主端点）

在地理范围内或某经纬度附近查询 POI（站点）列表。多数应用消费数据的核心方法。

#### 查询参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `output` | string | `json` | 输出格式：`json`/`geojson`/`xml`/`csv`，JSON 保真度最高 |
| `client` | string | — | 标识客户端应用的字符串，推荐设置以区别于其它爬虫 |
| `maxresults` | integer | 100 | 返回结果上限 |
| `countrycode` | string | — | 2 字符 ISO 国家代码，筛选某国 |
| `countryid` | array(string) | — | 数字国家 ID 精确匹配，逗号分隔（如 `1,2`）|
| `latitude` | number | — | 纬度，用于距离计算与筛选 |
| `longitude` | number | — | 经度，用于距离计算与筛选 |
| `distance` | number | — | 距 lat/lng 的最大距离，配合 `distanceunit` |
| `distanceunit` | string | `Miles` | `miles` 或 `km` |
| `operatorid` | array | — | 运营商 ID 精确匹配，逗号分隔 |
| `connectiontypeid` | array | — | 连接类型 ID 精确匹配，逗号分隔 |
| `levelid` | array | — | 充电等级 ID (1-3) 匹配，**已弃用** |
| `usagetypeid` | array | — | 使用类型 ID 匹配，逗号分隔 |
| `statustypeid` | array | — | 状态类型 ID 匹配，逗号分隔 |
| `dataproviderid` | array | — | 数据提供者 ID 匹配，逗号分隔 |
| `opendata` | boolean | — | `true` 仅返回 OCM 自有"开放"数据 |
| `includecomments` | boolean | false | `true` 包含用户评论与媒体项 |
| `verbose` | boolean | true | `false` 返回更小结果（移除 null 字段）|
| `compact` | boolean | false | `true` 移除参考数据对象，仅返回 ID |
| `camelcase` | boolean | false | `true` 返回 camelCase 属性名 |
| `chargepointid` | string | — | OCM POI ID 精确匹配，逗号分隔 |
| `boundingbox` | array | — | 边界框 `(lat,lng),(lat2,lng2)` |
| `polygon` | string | — | encoded polyline 多边形过滤，自动闭合 |
| `polyline` | string | — | 沿 encoded polyline 过滤，配合 `distance` |
| `sortby` | string | — | `modified_asc` / `id_asc`（默认空间索引排序）|
| `modifiedsince` | string | — | 只返回该日期之后修改的结果 |
| `greaterthanid` | string | — | ID 大于该值的结果 |

#### 响应

`200` → `POI[]` 数组（见下方 schema）。

---

### 2. GET `/referencedata` — 核心参考数据

返回用于 ID 查找的核心参考数据：连接类型、运营商、国家等。

#### 查询参数

| 参数 | 类型 | 说明 |
|---|---|---|
| `countryid` | array | 可选，按国家 ID 过滤 |

#### 响应

`200` → `CoreReferenceData` 对象，包含以下数组：

- `ChargerTypes` (LevelType[])
- `ConnectionTypes` (ConnectionType[])
- `CheckinStatusTypes` (CheckinStatusType[])
- `Countries` (Country[])
- `CurrentTypes` (SupplyType[])
- `DataProviders` (DataProvider[])
- `Operators` (OperatorInfo[])
- `StatusTypes` (StatusType[])
- `SubmissionStatusTypes` (SubmissionStatusType[])
- `UsageTypes` (UsageType[])
- `UserCommentTypes` (UserCommentType[])
- `DataTypes`、`MetadataGroups`（扩展字段）

**用途**：UI 编辑系统，或将 `compact` 模式的 POI 结果重新水合为完整对象。

---

### 3. POST `/profile/authenticate` — 用户认证

用邮箱+密码换取 JWT Bearer Token，用于后续需鉴权的端点。

#### 请求体 (application/json)

```json
{
  "emailaddress": "user@example.com",
  "password": "string"
}
```

#### 响应

```json
{
  "Data": {
    "UserProfile": { "ID": 1000, "Username": "...", /* 见 UserProfile schema */ },
    "access_token": "<JWT_BEARER_TOKEN>"
  },
  "Metadata": { "StatusCode": 200 }
}
```

`access_token` 作为 `Authorization: Bearer <token>` 用于 `/comment`、`/mediaitem`。

---

### 4. POST `/comment` — 提交评论 / 签到

**鉴权**：UserAuthentication (JWT)

#### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `chargePointID` | integer | ✅ | 有效的 OCM POI ID |
| `commentTypeID` | integer | — | 评论类型 ID（见 CoreReferenceData.UserCommentTypes），null=通用评论 |
| `userName` | string (≤100) | — | 关联名称；已登录用户用其 profile 用户名 |
| `comment` | string (≤4000) | — | 描述充电体验 |
| `rating` | integer | — | 1=最差 ~ 5=最好 |
| `relatedURL` | string (≤500) | — | 相关网址 |
| `checkinStatusTypeID` | integer | — | CheckStatusTypeID，如 `10`=充电成功 |

#### 响应

```json
{ "status": "OK", "description": "OK" }
```

`400` = Bad Request。

---

### 5. POST `/mediaitem` — 上传充电点照片

**鉴权**：UserAuthentication (JWT)

#### 请求体

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `chargePointID` | integer | ✅ | 关联的 OCM POI ID |
| `imageDataBase64` | string | ✅ | Base64 编码图片数据，如 `data:image/jpeg;base64,...` |
| `comment` | string | — | 图片描述 |

#### 响应

```json
{ "status": "OK", "description": "OK" }
```

---

### 6. GET `/openapi` — 获取 OpenAPI 定义

返回当前 API 的 YAML OpenAPI 定义，可用于文档工具、mock、测试、客户端生成。

---

## 核心数据模型 (Schema)

### POI（充电站点）

POI = Point of Interest，也称 `Site` 或 `ChargePoint`。OCM ID 在 UI 中常显示为 `OCM-12345`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `ID` | integer | OCM POI 唯一 ID（如 148527）|
| `UUID` | string(uuid) | 通用唯一标识，提交更新时须保留 |
| `UserComments` | UserComment[] | 用户评论/签到列表 |
| `MediaItems` | MediaItem[] | 用户上传照片列表 |
| `IsRecentlyVerified` | boolean | 动态计算，近期是否有确认活动 |
| `DateLastVerified` | datetime | 动态计算的最近确认时间 |
| `ParentChargePointID` | integer | 父 POI（一般消费者无需关注）|
| `DataProviderID` | integer | 数据提供者 ID |
| `DataProvidersReference` | string | 数据提供者自有键 |
| `OperatorID` | integer | 运营商 ID（默认 0）|
| `OperatorsReference` | string | 运营商自有站点/设备编号 |
| `UsageTypeID` | integer | 使用类型 ID，0=未知 |
| `UsageCost` | string | 费用描述（自由文本）|
| `AddressInfo` | AddressInfo | 地址与坐标 |
| `Connections` | ConnectionInfo[] | 设备/连接器信息列表 |
| `NumberOfPoints` | integer | 可同时使用的车位/桩数 |
| `GeneralComments` | string | 附加事实信息 |
| `DatePlanned` | datetime | 计划投运时间 |
| `DateLastConfirmed` | datetime | 上次确认时间 |
| `StatusTypeID` | integer | 运营状态 ID，0=未知 |
| `DateLastStatusUpdate` | datetime | 上次状态更新时间 |
| `MetadataValues` | array | 元数据值（数据归属等）|
| `DataQualityLevel` | integer | 质量等级 1-5（5 最好）|
| `DateCreated` | datetime | 入库时间 |
| `SubmissionStatusTypeID` | integer | 提交状态 ID |
| `DataProvider` | DataProvider | 展开对象（verbose 模式）|
| `OperatorInfo` | OperatorInfo | 展开对象（verbose 模式）|
| `UsageType` | UsageType | 展开对象 |
| `StatusType` | StatusType | 展开对象 |
| `SubmissionStatus` | SubmissionStatusType | 展开对象 |

> **Verbose 模式**（默认 true）：包含展开的参考对象；`compact=true` 时仅返回 ID。
> **AddressInfo** 始终填充，其它对象在 compact 模式下仅返回 ID（如 `UsageTypeID`）。

### AddressInfo

| 字段 | 类型 | 说明 |
|---|---|---|
| `ID` | integer | 地址 ID |
| `Title` | string | 位置标题 |
| `AddressLine1` / `AddressLine2` | string | 地址行 |
| `Town` | string | 城镇 |
| `StateOrProvince` | string | 州/省 |
| `Postcode` | string | 邮编 |
| `CountryID` | integer | 国家 ID |
| `Country` | Country | 国家对象（ISOCode、ContinentCode、ID、Title）|
| `Latitude` / `Longitude` | number | 十进制度 |
| `ContactTelephone1` / `ContactTelephone2` | string | 联系电话 |
| `ContactEmail` | string | 联系邮箱 |
| `AccessComments` | string | 使用/寻找设备指引 |
| `RelatedURL` | string | 相关网址 |
| `Distance` | number | 距搜索点的距离 |
| `DistanceUnit` | integer | 1=Miles, 2=KM |

### ConnectionInfo（设备/连接器）

| 字段 | 类型 | 说明 |
|---|---|---|
| `ID` | integer | 连接信息 ID |
| `ConnectionTypeID` | integer | 连接类型 ID |
| `ConnectionType` | ConnectionType | 展开对象（FormalName、IsDiscontinued、IsObsolete、ID、Title）|
| `Reference` | string | 运营商自有编号 |
| `StatusTypeID` | integer | 状态 ID，0=未知 |
| `StatusType` | StatusType | 展开对象 |
| `LevelID` | integer | 充电等级 ID，**已弃用**，改用 PowerKW |
| `Level` | LevelType | 展开对象 |
| `Amps` | integer | 最大电流 (A) |
| `Voltage` | number | 电压 (V) |
| `PowerKW` | number | 峰值功率 (kW) |
| `CurrentTypeID` | integer | 供电类型 ID |
| `CurrentType` | SupplyType | 展开对象 |
| `Quantity` | integer | 该规格设备数量 |
| `Comments` | string | 备注 |

### ConnectionType

| 字段 | 说明 |
|---|---|
| `FormalName` | 正式标准名（如 `IEC 62196-2 Type 2`）|
| `IsDiscontinued` | 是否已停产 |
| `IsObsolete` | 是否已淘汰 |
| `ID` | ID（如 25）|
| `Title` | 显示名（如 `Type 2 (Socket Only)`）|

### LevelType（充电等级，已弃用）

| 字段 | 说明 |
|---|---|
| `ID` | 1/2/3 |
| `Title` | 如 `Level 2 : Medium (Over 2kW)` |
| `Comments` | 说明 |
| `IsFastChargeCapable` | 是否快充 |

### SupplyType（供电类型）

| 字段 | 说明 |
|---|---|
| `ID` | 10=AC 单相 / 20=AC 三相 / 30=DC |
| `Title` | 显示名 |

### StatusType

| 字段 | 说明 |
|---|---|
| `IsOperational` | 是否运营中 |
| `IsUserSelectable` | 是否用户可选 |
| `ID` | 如 50=Operational |
| `Title` | 显示名 |

### UsageType

| 字段 | 说明 |
|---|---|
| `IsPayAtLocation` | 现场付费 |
| `IsMembershipRequired` | 需会员 |
| `IsAccessKeyRequired` | 需物理钥匙（**已弃用**）|
| `ID` | 使用类型 ID |
| `Title` | 如 `Public - Membership Required` |

### DataProvider

| 字段 | 说明 |
|---|---|
| `WebsiteURL` | 提供者网站 |
| `Comments` | 备注 |
| `DataProviderStatusType` | `{IsProviderEnabled, ID, Title}` |
| `IsRestrictedEdit` | 编辑受限 |
| `IsOpenDataLicensed` | 是否开放数据许可 |
| `IsApprovedImport` | 是否批准的导入 |
| `License` | 许可摘要（如 CC BY-SA 4.0）|
| `DateLastImported` | 上次导入时间 |
| `ID` | 提供者 ID |
| `Title` | 显示名 |

### OperatorInfo（运营商）

| 字段 | 说明 |
|---|---|
| `WebsiteURL` | 网站 |
| `PhonePrimaryContact` / `PhoneSecondaryContact` | 联系电话 |
| `IsPrivateIndividual` | 是否个人（**已弃用**）|
| `AddressInfo` | 运营商地址 |
| `BookingURL` | 预订 URL |
| `ContactEmail` | 联系邮箱 |
| `FaultReportEmail` | 故障报告邮箱 |
| `IsRestrictedEdit` | 编辑受限 |
| `ID` | 运营商 ID |
| `Title` | 显示名 |

### UserComment

| 字段 | 说明 |
|---|---|
| `ID` | 评论 ID |
| `ChargePointID` | 关联 POI ID |
| `CommentTypeID` | 评论类型 ID |
| `CommentType` | UserCommentType 对象 |
| `UserName` | 用户名 |
| `Comment` | 内容 |
| `RelatedURL` | 相关 URL |
| `DateCreated` | 创建时间 |
| `User` | UserInfo 对象 |
| `CheckinStatusTypeID` | 签到状态 ID |
| `CheckinStatusType` | CheckinStatusType 对象 |

### MediaItem

| 字段 | 说明 |
|---|---|
| `ID` | 媒体 ID |
| `ChargePointID` | 关联 POI ID |
| `ItemURL` | 图片 URL |
| `ItemThumbnailURL` | 缩略图 URL |
| `Comment` | 描述 |
| `IsEnabled` | 是否启用 |
| `IsVideo` | 是否视频 |
| `IsFeaturedItem` | 是否精选 |
| `IsExternalResource` | 是否外部资源 |
| `User` | UserInfo 对象 |
| `DateCreated` | 创建时间 |

### CheckinStatusType

| 字段 | 说明 |
|---|---|
| `ID` | ID |
| `Title` | 显示名 |
| `IsAutomatedCheckin` | 是否自动签到 |
| `IsPositive` | 是否正面 |

### SubmissionStatusType

| 字段 | 说明 |
|---|---|
| `ID` | 如 200=已发布 |
| `Title` | 显示名 |
| `IsLive` | 是否上线（非草稿/下架）|

### UserProfile

| 字段 | 说明 |
|---|---|
| `ID` | 用户 ID |
| `Username` | 用户名 |
| `Profile` | 个人简介 |
| `Location` | 位置 |
| `WebsiteURL` | 网站 |
| `ReputationPoints` | 声誉点数 |
| `Permissions` | 权限 JSON |
| `DateCreated` / `DateLastLogin` | 时间戳 |
| `IsProfilePublic` | 资料是否公开 |
| `Latitude` / `Longitude` | 坐标 |
| `EmailAddress` | 邮箱（非公开字段）|
| `ProfileImageURL` | 头像 URL |

### UserInfo（公开摘要）

| 字段 | 说明 |
|---|---|
| `ID` | 用户 ID |
| `Username` | 用户名 |
| `ReputationPoints` | 声誉点数 |
| `ProfileImageURL` | 头像 URL |

### Country

| 字段 | 说明 |
|---|---|
| `ID` | 国家 ID |
| `ISOCode` | ISO 代码（如 GB）|
| `ContinentCode` | 洲代码（如 EU）|
| `Title` | 国家名 |

---

## 常用调用示例

```bash
# 查询美国充电站（前 10 条）
curl "https://api.openchargemap.io/v3/poi/?countrycode=US&maxresults=10&key=YOUR_KEY"

# 按坐标搜索 10 英里内的站点
curl "https://api.openchargemap.io/v3/poi/?latitude=51.5&longitude=-0.13&distance=10&distanceunit=miles&key=YOUR_KEY"

# 获取参考数据（连接类型、运营商等 ID 表）
curl "https://api.openchargemap.io/v3/referencedata/?key=YOUR_KEY"

# 仅返回开放数据，带用户评论
curl "https://api.openchargemap.io/v3/poi/?countrycode=GB&opendata=true&includecomments=true&key=YOUR_KEY"

# 紧凑模式（仅 ID，不带展开对象），结果更小
curl "https://api.openchargemap.io/v3/poi/?countrycode=GB&compact=true&verbose=false&key=YOUR_KEY"

# 按 OCM POI ID 精确查询
curl "https://api.openchargemap.io/v3/poi/?chargepointid=148527&key=YOUR_KEY"

# 边界框过滤
curl "https://api.openchargemap.io/v3/poi/?boundingbox=(51.5,-0.13),(51.3,-0.01)&key=YOUR_KEY"

# 自上次同步后增量更新
curl "https://api.openchargemap.io/v3/poi/?countrycode=GB&modifiedsince=2024-01-01&sortby=modified_asc&key=YOUR_KEY"

# 用户认证换取 JWT
curl -X POST "https://api.openchargemap.io/v3/profile/authenticate" \
  -H "Content-Type: application/json" \
  -d '{"emailaddress":"user@example.com","password":"secret"}'

# 提交签到（需 JWT）
curl -X POST "https://api.openchargemap.io/v3/comment" \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{"chargePointID":12345,"commentTypeID":10,"comment":"充电成功，很方便","rating":5,"checkinStatusTypeID":10}'
```

---

## AI 调用指南

### Instructions

#### Step 1: 确认 API Key 来源
从环境变量 `OCM_API_KEY` 或用户提供的 Key 读取。若用户未提供，提示其到 openchargemap.org 注册应用获取。

#### Step 2: 查询 POI
用 `/poi` 端点，根据用户意图拼参数（国家、坐标、距离、运营商、连接类型等）。

#### Step 3: 解析 ID
当结果中只有 ID（compact 模式或非 verbose），调用 `/referencedata` 水合为可读名称。

#### Step 4: 写入操作（评论/签到/上传照片）
需先 POST `/profile/authenticate` 拿 JWT，再带 `Authorization: Bearer` 调用 `/comment` 或 `/mediaitem`。

#### Step 5: 节流
对重复查询做 debounce/throttle，遵守公平使用政策。

### 何时使用本 Skill

- 用户要查询/搜索电动汽车充电站（按国家、坐标、边界框、运营商、连接类型等过滤）
- 用户要获取充电桩的详细信息（地址、连接器、功率、状态、运营商、费用、评论、照片）
- 用户要获取参考数据（连接类型、运营商、国家等 ID 映射表）
- 用户要提交充电站评论/签到或上传照片（需先认证）
- 用户要做充电桩数据的批量/增量同步

### 调用流程

1. **确认 API Key 来源**：从环境变量 `OCM_API_KEY` 或用户提供的 Key 读取。若用户未提供，提示其到 openchargemap.org 注册应用获取。
2. **查询 POI**：用 `/poi` 端点，根据用户意图拼参数（国家、坐标、距离、运营商、连接类型等）。
3. **解析 ID**：当结果中只有 ID（compact 模式或非 verbose），调用 `/referencedata` 水合为可读名称。
4. **写入操作**：评论/签到/上传照片需先 POST `/profile/authenticate` 拿 JWT，再带 `Authorization: Bearer` 调用。
5. **节流**：对重复查询做 debounce/throttle，遵守公平使用政策。

### 参数选择决策

| 用户意图 | 推荐参数 |
|---|---|
| "查某国充电站" | `countrycode=XX` |
| "我附近的充电站" | `latitude=..&longitude=..&distance=..&distanceunit=km` |
| "某区域内" | `boundingbox=(lat,lng),(lat2,lng2)` |
| "沿路线" | `polyline=<encoded>&distance=..` |
| "只要快充" | `connectiontypeid=<快充类型ID>`（从 referencedata 查）|
| "只要运营中" | `statustypeid=50` |
| "只要开放数据" | `opendata=true` |
| "带评论" | `includecomments=true` |
| "省流量" | `compact=true&verbose=false` |
| "增量同步" | `modifiedsince=<ISO日期>&sortby=modified_asc` |
| "分页" | `greaterthanid=<最后一条ID>` + `sortby=id_asc` |

### 输出呈现建议

- 地图展示：`output=geojson` 直接喂给地图库
- 列表展示：默认 JSON，按 `AddressInfo.Title` / `Town` / `OperatorInfo.Title` 分组
- 功率信息：优先取 `Connections[].PowerKW`，退化用 `Level.Title`
- 连接器类型：用 `ConnectionType.Title` 展示，`FormalName` 作为技术标准补充
- 状态：`StatusType.Title`（如 Operational）
- 费用：`UsageCost` 自由文本，不一定可靠

### 常见陷阱

- `levelid` 已弃用，改用 `connectiontypeid` + `PowerKW`。
- `IsAccessKeyRequired`（UsageType）已弃用。
- `compact=true` 时 AddressInfo 仍填充，但 OperatorInfo / UsageType / StatusType 等仅返回 ID，需 `/referencedata` 水合。
- `ParentChargePointID` 非 null 表示该 POI 数据取代另一 POI，一般消费者可忽略。
- 计划投运 (`DatePlanned` 在未来) 的 POI 不应呈现给终端用户，直到确认运营。
- 数据许可为 CC BY-SA 4.0，使用数据须署名并按相同许可共享。

## Troubleshooting

- **401 / 鉴权失败**：未提供 API Key。用请求头 `X-API-Key` 或 query 参数 `key` 传入（二选一），Key 大小写敏感。
- **compact 模式下字段只剩 ID**：调用 `/referencedata` 获取连接类型/运营商/国家等 ID 映射表进行水合。
- **评论/照片接口 401**：需先 POST `/profile/authenticate` 用邮箱+密码换取 JWT，再带 `Authorization: Bearer <token>`。
- **被封禁 / 请求被拒**：违反公平使用政策（重复无差别查询）。对请求做 debounce/throttle，高频大量查询请自建镜像。
- **快充过滤无效**：`levelid` 已弃用。改用 `connectiontypeid`（从 `/referencedata` 查快充类型 ID）配合 `PowerKW` 判断。
- **计划投运站点误展示**：`DatePlanned` 在未来的 POI 不应呈现给终端用户，确认运营后再展示。

---

## 参考文档

- **完整 OpenAPI 3.1 规范**：`references/ocm-openapi-spec.yaml`（2129 行，机器可读，可喂给 AI 或导入 Postman/Swagger）
- 官网开发页：https://www.openchargemap.org/develop/api#/
- 服务条款：https://openchargemap.org/site/about/terms