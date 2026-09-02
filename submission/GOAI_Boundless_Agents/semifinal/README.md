# RoadMan 复赛材料

评委提出了四项修改意见。本目录逐项说明已经完成的修改、可复现方法和当前限制。团队已经开放受控公网 Demo，访问方式由参赛入口提供；仓库同时保留本地启动命令、评测数据和运行记录，截图只用于说明界面。

## 专项说明

1. [续航能力真实化与可量化](01_续航能力真实化与可量化.md)
2. [可复现 Demo 与技术细节完善](02_可复现Demo与技术细节完善.md)
3. [差异化与商业化价值](03_差异化与商业化价值.md)
4. [安全边界与诚实降级展示](04_安全边界与诚实降级展示.md)

## 直接证据

| 文件或入口 | 可核对内容 |
|---|---|
| `evaluation/results/range-accuracy-baseline.json` | 12 条模拟传感器回放及逐样本续航误差 |
| `evaluation/results/safety-scenarios-baseline.json` | 12 个异常场景的输入、预期和结果 |
| `evaluation/results/semifinal-readiness.json` | 复赛检查的机器可读汇总 |
| `evaluation/results/semifinal-readiness.md` | 同一检查的人类可读摘要 |
| `deploy/semifinal-check.ps1` | 镜像构建、后端测试、评测、前端单测和生产构建 |
| `deploy/semifinal_evidence.mjs` | 生成 2560 x 1440 产品截图与指标图 |
| `assets/screenshot-manifest.json` | 截图尺寸、来源页面和生成时间 |

## 推荐阅读顺序

先看 [RoadMan 复赛项目介绍](RoadMan_复赛项目介绍.pdf)，前两页直接回答评委意见并概括 2026 年 8 月 16 日之后的更新。需要核对计算、架构或数据边界时，再查对应专项文档和 JSON 结果。

```powershell
# 完整复赛检查
.\deploy\semifinal-check.ps1

# 已启动容器的运行验收
.\deploy\semifinal-check.ps1 -Live
python deploy/api_smoke.py
python deploy/full_journey_acceptance.py
python deploy/edit_replan_acceptance.py
```

当前续航误差基线来自模拟传感器回放，不是实车道路遥测。公网 Demo 也仍是受控评审环境，不能据此推定已达到生产级账号、租户隔离或 SLA 要求。
