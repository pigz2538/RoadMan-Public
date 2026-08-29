# 发布前复核清单（2026-08-29）

本文记录当前发布批次的复核结论。它只保留仍有价值的维护项，不把历史上已经修复的缺陷继续当成待办。最新代码、接口和运行命令以根目录 [`project.md`](../project.md) 与 [`README.md`](../README.md) 为准。

## 已关闭的发布阻断项

- **预检澄清丢失或重复提问**：前端提交澄清答案时使用稳定快照；后端在云端需求抽取返回部分字段时保留用户明确写出的地点、日期和时间。日期顺序、跨海方式和不可能时间窗的回答会在后续语义复核中继续生效。
- **云端语义复核重新打开已回答约束**：回答时间窗、日期顺序或跨海方式后，等价字段（出发/返回时间、日期、交通偏好）会被视为已处理，避免模型因换了字段名而再次阻断。
- **交通/地点事实缺失**：公共交通阶段保留线路和上下车站；铁路/航班/轮船无真实编号时显示“暂未返回”，不再伪造通用编号。景点、餐饮和住宿保留营业、票务、预约、停车、图片、链接、来源数和核验时间。
- **导出与任务闭环**：只有完成的 Trip 才可导出；HTML/PDF/PPTX/PNG/Markdown 均经过真实容器接口冒烟验收。DeepSeek 官方接口的需求预检、语义编辑和附件提取已在本轮通过，不存在关键词冒充语义理解。
- **前端品牌文案**：用户界面的进度、阶段和来源文案使用“智能体、在线地图、旅行信息服务”等人性化名称；技术 provider ID 仅保留在接口、审计和文档中。
- **跨城酒店污染**：住宿搜索可携带目的地坐标与范围，过滤供应商返回的异地卡片；过滤为空时保留明确的无结果状态并交给其他来源降级。
- **自驾返程越过截止时刻**：明确的返程到达时间会在排程前预留路程、补能和驾驶休息缓冲；最终返程阶段使用有效最晚出发时刻，避免深度驾驶拆分后跨到次日。
- **删除行程后的后台竞态**：规划任务启动期间若行程已被删除，工作进程以 `TRIP_DELETED` 取消任务，不再把竞态写成失败规划或残留错误快照。
- **连续 SOC 与补能计算**：补能后电量按实测能量或站点功率 × 停留时间计算，跨阶段继承充前/充后/到达 SOC，不再固定重置为 80%；12 条模拟回放建立了能耗、等效续航和 SOC 误差基线。
- **规划页地图空间**：左侧日程、右侧行程助理、底部阶段详情可独立折叠并记住状态；底栏折叠后只保留阶段序号和前后箭头，双桌面尺寸 E2E 已覆盖。
- **3D 车辆离线解码**：McLaren 模型的 Draco 解码器在构建时从锁定依赖复制进前端镜像，演示运行不再依赖 gstatic CDN；E2E 主动阻断该 CDN 后车辆仍可加载。
- **行程级联硬删除**：删除 Trip 同步清理消息、规划状态、版本、任务、行程 Skill 调用、附件元数据和安全上传目录内实体文件。

## 当前验收基线

```text
后端单元/集成测试：131 passed, 1 skipped
续航误差基线：12 样本；能耗 MAPE 8.442%，等效续航 MAPE 9.302%，SOC MAE 2.799 个百分点
安全/降级评测：5/5 命中预期；路线可执行率 80%（估算补能位置按诚实降级不计可执行）
API 冒烟：58/58 passed（含地图、天气、地点、住宿、交通、车型、编辑、文件和五种导出）
前端单元测试：2 passed
前端 E2E：17 passed, 6 skipped（跳过项需显式打开真实云端/真实行程专项）
Docker：backend/worker/frontend/postgres/redis 均已重建并健康
真实智能体/API：DeepSeek 需求预检、语义编辑、附件提取均通过；本轮 10 次调用记录官方 Token 16,313；全部外部能力和五种导出通过
```

## 非阻断维护项

1. **真实服务授权**：提交或公开部署前确认地图、旅行信息、开放数据和图片来源的展示、缓存与再利用条款；密钥只通过 `.env`/容器环境注入。
2. **视觉回归**：每次调整导出模板后，用真实完成行程生成 HTML/PDF/PPTX/PNG 并人工检查图片比例、地图背景、中文字体和分页；E2E 截图只用于验收，不纳入运行时数据。
3. **大型前端 chunk**：`model-viewer` 当前是独立的大 chunk，生产构建会给出体积提示但不影响运行；若继续优化首屏，再考虑动态加载或按路由拆包。
4. **外部服务波动**：旅行搜索、班次、天气和实时地图可能超时或无结果。系统必须保留“未知/待核验”状态，不得把缓存或示意地图伪装成实时数据；发布前可用 `deploy/api_smoke.py` 检查当前配额和凭据。

5. **云端智能体授权诊断**：`DEEPSEEK_API_KEY` 通过官方 `https://api.deepseek.com/chat/completions` 调用；模型固定为 `deepseek-v4-flash`，思考模式与 `reasoning_effort=max` 开启。请求失败时需求预检、语义编辑和目的地研究按设计暂停，不打开关键词兜底。

## 复现命令

```powershell
docker compose up -d --build
$env:PYTHONPATH = 'backend'
conda run --no-capture-output -n roadman python -m pytest backend/tests -q
conda run --no-capture-output -n roadman python evaluation/run_evals.py
conda run --no-capture-output -n roadman python evaluation/range_accuracy.py
conda run --no-capture-output -n roadman python evaluation/safety_scenarios.py
conda run --no-capture-output -n roadman python deploy/api_smoke.py
conda run --no-capture-output -n roadman python deploy/full_journey_acceptance.py --timeout 600
cd frontend
npm test -- --run
npm run build
npm run test:e2e
# 可选：真实云端智能体浏览器验收
$env:ROADMAN_RUN_LIVE_AGENT_E2E = '1'
npm run test:e2e -- tests/e2e/planning-agent.spec.ts
```

本次审计的逐项记录（代码范围、风险、命令和已知外部阻断）见
[`docs/repo-audit-2026-08-28.md`](repo-audit-2026-08-28.md)。
