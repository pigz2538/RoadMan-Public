# RoadMan 真实回归验收记录

运行日期：2026-08-30（Asia/Shanghai）

## 奇怪输入回归

- `weird_live_scenarios_v3.json` 需求预检：10/10 通过。
- 其中 7 条进入完整规划，均完成“新建 → 工具调用 → 分日 → 路线 → 自动校验 → HTML 导出”。
- 3 条信息不足或约束互相冲突，均按预期停在澄清，不编造地点或日期。
- 厦门轮渡案例在路线服务返回空几何时再次完整通过；系统保留端点估算并显示路线数据降级警告，不让无效几何触发模型校验崩溃。

证据文件：

- `weird-live-v3-preflight-post-edit.json`
- `weird-live-v3-full-final.json`
- `weird-live-v3-xiamen-post-edit.json`

## E1–E10 编辑重排

验收链路统一为：候选/地图选点 → 修改预览 → 用户确认应用 → 后台完整重排 → 路线与时间校验 → 活动持久化断言 → 回滚恢复基线。

| 用例 | 场景 | 结果 |
| --- | --- | --- |
| E01 | 地图新增景点 | 通过 |
| E02 | 地图新增餐饮 | 通过 |
| E03 | 地图新增住宿 | 通过 |
| E04 | 候选池新增景点 | 通过 |
| E05 | 候选池新增餐饮 | 通过 |
| E06 | 直接删除原有景点 | 通过 |
| E07 | 自然语言删除景点 | 通过 |
| E08 | 自然语言替换景点 | 修复后通过 |
| E09 | 批量新增后删除再统一重排 | 修复后通过 |
| E10 | 自然语言新增景点 | 修复后通过 |

证据文件：

- `edit-replan-live-verified.json`（E01–E10 汇总：10/10）
- `edit-replan-live-final.json`（首轮 E01–E07 通过；E08–E10 的乱码测试输入已废弃）
- `edit-replan-live-e8-regression.json`
- `edit-replan-live-e9-e10-regression.json`

## 本轮修复

1. 替换活动会把旧地点写入持久化排除清单，并从 `must_visit` 中移除；新一轮供应商搜索不能再把旧地点复活。
2. 路线服务成功但返回少于两个几何点时，生成端点估算路线并标记 `ROUTE_GEOMETRY_UNAVAILABLE`；端点也缺失时返回可读的路线错误，不构造非法空 `RouteSegment`。
3. 缺少路线段时，跨天驾驶拆分和休息点选择不再索引空对象，避免后台在 99% 保存阶段崩溃。
4. 凭据型技能未配置时跳过共享缓存读取，避免高德旧成功结果掩盖 `SKILL_NOT_CONFIGURED` 降级。
5. 编辑验收脚本的自然语言用例改为真实中文，并支持按 E 编号回归，避免乱码输入把模型误导成不可行行程。

## 容器测试

使用刚构建的 `roadman-backend` 镜像，临时挂载 `backend/tests` 与 `evaluation`，并清空高德 Key、关闭本地凭据加载：

```powershell
docker compose run --rm --no-deps `
  -e PYTHONPATH=/app `
  -e AMAP_WEBSERVICE_KEY= `
  -e LOAD_LOCAL_SKILL_CREDENTIALS=false `
  -v "${PWD}/backend/tests:/app/tests:ro" `
  -v "${PWD}/evaluation:/evaluation:ro" `
  --entrypoint pytest backend -q /app/tests
```

结果：**146 passed，1 skipped，0 failed（43.09 秒）**。`docker compose ps` 显示 backend healthy、worker running、frontend running。

跳过项是需要外部实时服务的集成标记，不影响确定性测试；真实供应商调用由上面的 live acceptance 脚本单独验证并记录来源/降级状态。
