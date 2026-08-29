# RoadMan 可复现评测

本目录将“能运行”与“效果如何”分开验证：`run_evals.py` 负责需求理解与完整旅程验收，`range_accuracy.py` 负责预测能耗、预测续航和到达 SOC 相对观测值的误差。

## 续航误差基线

```powershell
python evaluation/range_accuracy.py
```

输出写入 `evaluation/results/range-accuracy-baseline.json`，包含逐样本误差、能耗 MAPE、等效续航 MAPE、SOC 平均绝对误差和阈值结论。默认数据 `range_observations.simulated.json` 明确标记为 `simulation_sensor_replay`，只证明计算和评测链路可复现，不冒充真实道路遥测，也不构成车辆续航承诺。

接入真实路测时保留相同字段，将 `data_kind` 改为 `vehicle_telemetry`，并记录车辆配置、路线、天气、载荷、出发/到达 SOC、仪表或充电记录来源以及脱敏方式。评审材料应分别展示模拟基线和真实样本，不能混写。

当前门槛：能耗 MAPE ≤ 12%，等效续航 MAPE ≤ 12%，到达 SOC MAE ≤ 5 个百分点。低温样本允许单例误差高于总体门槛，用于明确当前基础模型尚未完整建模温度和载荷修正的边界。
