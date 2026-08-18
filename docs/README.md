# RoadMan 文档索引

> 交通与地点信息的结构化契约、来源追溯和未知状态处理见 [mobility-and-poi-data-contract.md](mobility-and-poi-data-contract.md)。

本文档索引 `docs/` 目录下的维护文档。所有文档以当前代码、接口与配置为准，均不描述代码中不存在的能力。项目入口与实现边界见根目录 [project.md](../project.md)；快速上手、配置与验证命令见根目录 [README.md](../README.md)。

## 基线文档（8 份）

| 文档 | 用途 |
| --- | --- |
| [api-contract.md](api-contract.md) | HTTP、SSE、统一错误格式与导出条件 |
| [domain-model.md](domain-model.md) | Trip/DayPlan/MovementStage/Activity/PlanPatch 等领域模型与 JSON Schema 契约 |
| [routing-fallback-design.md](routing-fallback-design.md) | 高德真实路线、交通方式降级、跨城班次与返程闭环校验 |
| [runtime-requirements.md](runtime-requirements.md) | Python/Node/PostgreSQL/Redis 依赖、Conda 与 Docker 两种运行方式 |
| [operations.md](operations.md) | 部署、健康检查、验收脚本、备份恢复、Nginx/HTTPS、排障与凭据管理 |
| [carinfo-catalog.md](carinfo-catalog.md) | 车型目录搜索、确定性回退与前端车辆抽屉接入 |
| [mobility-and-poi-data-contract.md](mobility-and-poi-data-contract.md) | 景点事实、票务预约、停车、图片、公共交通与跨城班次的数据契约和未知状态 |
| [README.md](README.md) | 本文档索引 |

## 评审与规划文档（4 份）

| 文档 | 用途 |
| --- | --- |
| [competition-readiness.md](competition-readiness.md) | 按《GOAI 无界应用参赛手册》8.1–8.6 对仓库能力的逐项达标审计 |
| [current-repair-plan.md](current-repair-plan.md) | 当前仍存在的问题与改进清单（P0/P1/P2），附代码位置与建议动作 |
| [external-data-credentials.md](external-data-credentials.md) | 外部能力标识、环境变量、必需性、降级策略与凭据存放规则 |
| [external-repo-review-and-poi-roadmap.md](external-repo-review-and-poi-roadmap.md) | POI 研究、候选聚合、身份校验、证据留存与图片补全的设计与边界 |

## 阅读指引

- 新部署或排查运行时与环境问题，先读 [runtime-requirements.md](runtime-requirements.md) 与 [operations.md](operations.md)。
- 理解数据与接口结构，读 [domain-model.md](domain-model.md)、[api-contract.md](api-contract.md)。
- 涉及路线/地图/交通降级，读 [routing-fallback-design.md](routing-fallback-design.md)。
- 提交参赛或对外交付前，用 [competition-readiness.md](competition-readiness.md) 核对能力，并按 [current-repair-plan.md](current-repair-plan.md) 处理已知差距。
