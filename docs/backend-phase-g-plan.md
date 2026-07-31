# 阶段 G：局部编辑与重算

## 已实现闭环

正式行程的修改统一经过 `PlanPatch`：

1. `preview` 保存原值、建议值、影响范围、时间/费用变化；
2. 预览阶段不会改写 Trip；
3. 用户选择 `apply` 后才修改；
4. 替换景点会定位名称匹配的前后接驳阶段，重新调用 `amap.route`；
5. 删除活动会在避开固定移动阶段的前提下，自动提前后续活动；
6. 修改后重新运行旅游排程冲突与全程路线闭环校验；
7. 校验失败不会保存；
8. 每次成功应用保留应用前快照，可通过 `rollback` 恢复。

前端的景点/住宿/餐饮备选、替换和活动移除都进入同一预览卡片。预览明确显示
影响日期、时间变化以及“正式行程尚未修改”；应用后出现“撤销上次修改”。

## API

- `POST /api/v1/trips/{trip_id}/patches/preview`
- `POST /api/v1/trips/{trip_id}/editing/interpret`
- `POST /api/v1/trips/{trip_id}/patches/preview-delete`
- `GET /api/v1/trips/{trip_id}/patches/{patch_id}`
- `POST /api/v1/trips/{trip_id}/patches/{patch_id}/apply`
- `POST /api/v1/trips/{trip_id}/patches/{patch_id}/reject`
- `POST /api/v1/trips/{trip_id}/patches/{patch_id}/rollback`

`apply` 响应同时返回最终 Patch 与最新 Trip，前端立即更新活动列表和地图。影响计算
保存在 `impact_scope`，局部校验结果和实际重算的阶段 ID 保存在规划状态的
`verification_result`。

`editing/interpret` 使用当前日期和选中节点理解明确的删除、替换和日期修改指令：
删除/替换只返回预览；修改日期明确返回需要全局重排，不把它伪装成局部编辑。

## 当前重算边界

- 替换景点：重算所有以旧景点为起点或终点的当天阶段；
- 加入景点/酒店/餐厅：确定性选择当天空闲时间窗；
- 删除活动：移除后提前后续活动，并避让固定移动阶段；
- 修改日期属于全局重排，不伪装为局部修改，应重新发起规划；
- 应用后的天气摘要沿用原阶段采样；距离、时长、道路和路费来自新路线。天气与车辆
  的再次实时采样会在后续增强中独立触发。

## 验收证据

- 后端测试验证：预览前后正式 Trip 不变，应用后新增，回滚后完全恢复；
- 删除 Patch 验证：确认后才移除；
- 双邻接路线测试：替换景点后去程终点和返程起点同时更新，两个阶段均重新计算，
  全程仍然闭环；
- 前端 E2E 验证：备选列表、排序分、修改预览和确认按钮可见。
