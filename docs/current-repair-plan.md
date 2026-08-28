# 发布前复核清单（2026-08-28）

本文记录当前发布批次的复核结论。它只保留仍有价值的维护项，不把历史上已经修复的缺陷继续当成待办。最新代码、接口和运行命令以根目录 [`project.md`](../project.md) 与 [`README.md`](../README.md) 为准。

## 已关闭的发布阻断项

- **预检澄清丢失或重复提问**：前端提交澄清答案时使用稳定快照；后端在云端需求抽取返回部分字段时保留用户明确写出的地点、日期和时间。日期顺序、跨海方式和不可能时间窗的回答会在后续语义复核中继续生效。
- **云端语义复核重新打开已回答约束**：回答时间窗、日期顺序或跨海方式后，等价字段（出发/返回时间、日期、交通偏好）会被视为已处理，避免模型因换了字段名而再次阻断。
- **交通/地点事实缺失**：公共交通阶段保留线路和上下车站；铁路/航班/轮船无真实编号时显示“暂未返回”，不再伪造通用编号。景点、餐饮和住宿保留营业、票务、预约、停车、图片、链接、来源数和核验时间。
- **导出与任务闭环**：只有完成的 Trip 才可导出；HTML/PDF/PPTX/PNG/Markdown 均经过接口冒烟验收。完整闭环脚本的规划、校验阶段已验证；语义编辑阶段仍依赖有效的 Ollama 云端凭据。
- **前端品牌文案**：用户界面的进度、阶段和来源文案使用“智能体、在线地图、旅行信息服务”等人性化名称；技术 provider ID 仅保留在接口、审计和文档中。
- **跨城酒店污染**：住宿搜索可携带目的地坐标与范围，过滤供应商返回的异地卡片；过滤为空时保留明确的无结果状态并交给其他来源降级。
- **自驾返程越过截止时刻**：明确的返程到达时间会在排程前预留路程、补能和驾驶休息缓冲；最终返程阶段使用有效最晚出发时刻，避免深度驾驶拆分后跨到次日。
- **删除行程后的后台竞态**：规划任务启动期间若行程已被删除，工作进程以 `TRIP_DELETED` 取消任务，不再把竞态写成失败规划或残留错误快照。

## 当前验收基线

```text
后端单元/集成测试：126 passed, 1 skipped
需求理解评测：当前 Ollama 云端 Key 返回 HTTP 403，12 条均标记为外部凭据阻断（不是解析结果通过）
API 冒烟：58/58 passed（含地图、天气、地点、住宿、交通、车型、编辑、文件和五种导出）
前端单元测试：2 passed
前端 E2E：15 passed, 6 skipped（4 个真实云端智能体用例需设置 ROADMAN_RUN_LIVE_AGENT_E2E=1；2 个真实行程展示用例需提供 ROADMAN_E2E_TRIP_ID）
Docker：backend/worker/frontend/postgres/redis 均已重建并健康
完整闭环：规划与自动校验通过；当前语义编辑/导出闭环被 Ollama Key 的 HTTP 403 阻断，编辑接口按契约返回 503，不使用关键词伪造意图
```

## 非阻断维护项

1. **真实服务授权**：提交或公开部署前确认地图、旅行信息、开放数据和图片来源的展示、缓存与再利用条款；密钥只通过 `.env`/容器环境注入。
2. **视觉回归**：每次调整导出模板后，用真实完成行程生成 HTML/PDF/PPTX/PNG 并人工检查图片比例、地图背景、中文字体和分页；E2E 截图只用于验收，不纳入运行时数据。
3. **大型前端 chunk**：`model-viewer` 当前是独立的大 chunk，生产构建会给出体积提示但不影响运行；若继续优化首屏，再考虑动态加载或按路由拆包。
4. **外部服务波动**：旅行搜索、班次、天气和实时地图可能超时或无结果。系统必须保留“未知/待核验”状态，不得把缓存或示意地图伪装成实时数据；发布前可用 `deploy/api_smoke.py` 检查当前配额和凭据。

5. **云端智能体授权诊断**：`DEEPSEEK_API_KEY` 通过官方 `https://api.deepseek.com/chat/completions` 做最小 JSON 请求；模型固定为 `deepseek-v4-flash`，思考模式与 `reasoning_effort=max` 开启。请求失败时需求预检、语义编辑和目的地研究按设计暂停，不打开关键词兜底。

## 复现命令

```powershell
docker compose up -d --build
$env:PYTHONPATH = 'backend'
conda run --no-capture-output -n roadman python -m pytest backend/tests -q
conda run --no-capture-output -n roadman python evaluation/run_evals.py
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
