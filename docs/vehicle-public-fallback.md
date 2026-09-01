# 车型公共数据降级

车型抽屉不再把某个车型写死为唯一降级结果。`carinfo.catalog` 先查主目录；主目录缺失或只返回模糊后缀时，按用户输入动态匹配以下公开来源：

| 优先级 | 来源 | 覆盖与字段 | 失败处理 |
| --- | --- | --- | --- |
| 1 | [AutoSeeker JSON](https://autoseeker.eu/data/models.json) | 纯电、混动、燃油车型；WLTP 续航、油耗、电池、座位、尺寸、功率、直流充电功率 | 超时或无匹配进入下一层 |
| 2 | [OpenEV Data](https://gaia-charge.github.io/evdb/v1/vehicles.json) | 电动车版本、电池、WLTP/实测续航、直流充电功率、性能、尺寸 | 超时或无匹配进入下一层 |
| 3 | [AppByte Fleet Catalog](https://fleetcatalog.disturbingbyte.pt) | 按品牌→车系→版本返回燃油/混动油耗、油箱、电池、动力、尺寸与年款 | 接口超时或无可匹配版本时继续走网页来源 |
| 4 | [CarNewsChina suggestion](https://data.carnewschina.com/suggest) 及公开车型页 | 公开车型/年款页面中的参数表 | 页面限流时保留已获得的结构化结果 |
| 最后保障 | SU7 公开页面缓存 | 仅三条已标记来源的 SU7 年款记录 | 只在网络完全不可用时使用，不扩展为车型白名单 |

匹配过程使用用户输入、品牌/车系/年款字段和版本词，不维护 SU7、SU7 Max 或其他车型的代码列表。中文品牌会参与匹配，避免“小鹏 G6”命中金杯 G6P；`Max`、`Pro`、`Performance` 等版本词有精确记录时优先返回精确记录。

结果中的 `rated_range_km`、`battery_kwh`、`consumption_per_100km`、`max_charge_kw`、`seats`、`height_m` 和 `width_m` 只来自公开字段。缺失字段保持 `null`，并列在 `specs_missing`；WLTP 等跨工况续航列入 `estimated_fields`。响应会给出 `fallback_used`、`fallback_provider`、`catalog_source` 以及可追溯的 `source_url`，方便用户核对。

适配器版本：`1.8.12`。公共查询与主目录并发执行，单个来源有独立超时；成功结果缓存 6 小时，空结果不写入缓存。该降级只负责车型资料展示和续航规划参数，不替代车辆合格证或厂商最终配置。
