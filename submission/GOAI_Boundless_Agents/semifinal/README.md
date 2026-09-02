# RoadMan 复赛优化材料索引

本目录对应评委提出的四个优化方向。材料只陈述仓库中已经实现或已经具备复现实验入口的能力；团队已独立开放受控公网 Demo，访问地址由参赛入口提供，仓库材料仍以可本地复现的代码、命令和机器可读证据为准，不以截图替代可运行证据。

## 四份专项文档

1. [续航能力真实化与可量化](01_续航能力真实化与可量化.md)
2. [可复现 Demo 与技术细节完善](02_可复现Demo与技术细节完善.md)
3. [差异化与商业化价值](03_差异化与商业化价值.md)
4. [安全边界与诚实降级展示](04_安全边界与诚实降级展示.md)

## 可复现证据

- `evaluation/results/range-accuracy-baseline.json`：预测值与观测值误差明细。
- `evaluation/results/safety-scenarios-baseline.json`：12 个异常与降级场景逐项结果。
- `evaluation/results/semifinal-readiness.json`：复赛检查机器可读汇总。
- `evaluation/results/semifinal-readiness.md`：复赛检查人类可读摘要。
- `deploy/semifinal-check.ps1`：一键构建、后端测试、评测、前端单测与生产构建。
- `deploy/semifinal_evidence.mjs`：生成 2560×1440 产品与指标证据图。
- `assets/screenshot-manifest.json`：截图尺寸、来源与生成时间清单。

## 一键复核

```powershell
.\deploy\semifinal-check.ps1

# 已启动本地 Compose 时，追加容器/API 健康探测
.\deploy\semifinal-check.ps1 -Live

# 只重新生成高清证据图
node deploy/semifinal_evidence.mjs
```

当前基线：续航模拟回放 12 条，能耗 MAPE 8.442%，等效续航 MAPE 9.302%，到达 SOC MAE 2.799 个百分点；异常与降级矩阵 12/12 符合预期。上述续航数据明确属于 `simulation_sensor_replay`，不代表真实道路续航承诺。
