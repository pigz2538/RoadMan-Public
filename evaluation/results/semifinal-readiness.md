# RoadMan 复赛可复现验收摘要

生成时间：2026-08-29T09:34:17.922016+00:00  
代码提交：`4fabe8fe04466daa6e4a1e3ad97c6e303fe881a4`  
总结果：**PASS**

## 自动检查

| 检查项 | 结果 |
|---|---|
| range_baseline_passed | 通过 |
| range_claim_labeled_non_real | 通过 |
| degradation_matrix_passed | 通过 |
| all_required_files_present | 通过 |
| live_probe_passed_when_requested | 通过 |

## 续航误差证据

- 数据类型：`simulation_sensor_replay`（不是实车道路数据）
- 样本数：12
- 能耗 MAPE：8.442%
- 能耗 RMSE：2.605 kWh
- 能耗 P95 绝对百分比误差：15.707%
- 等效续航 MAPE：9.302%
- 到达 SOC MAE：2.799 个百分点

声明边界：用于验证误差计算、SOC 连续性与评测流程，不代表真实道路续航承诺；真实车辆数据可按相同字段替换。

## 异常与降级证据

- 场景数：12
- 预期行为完成率：100%
- 降级处理成功率：100%
- 可直接执行路线比例：91.67%
- P95 本地规则执行时延：0.512 ms

“可直接执行路线比例”故意不等于 100%：当补能服务中断且只有路线估算点时，系统继续生成方案但明确要求出发前确认，不把估算位置伪装为真实可用充电桩。
