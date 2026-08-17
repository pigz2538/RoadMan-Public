# 当前待办与修复计划

本文是 RoadMan 当前存在的问题与改进清单，按影响排序为 P0/P1/P2。只列出当前仍存在的差距与改进项，每项给出代码位置和建议动作。验收以代码行为为准，不把“接口返回 200”当作功能完成。

## P0：测试与代码不同步

以下两处测试引用已被删除的符号，导致测试与实现不一致，需要先修复或同步。

1. **`frontend/src/stores/trip.spec.ts` 引用已移除的 `agentDialogue`**。测试在第 43 行写入 `store.agentDialogue`，并在第 59 行断言其被清空。`frontend/src/stores/trip.ts` 已不含 `agentDialogue` 字段。建议把该用例改写为断言当前存在的协作/规划状态字段，或删除这段陈旧的协作协议测试。
2. **`backend/tests/test_daily_review_agent.py` 导入已移除的符号**。测试从 `app.planning.llm` 导入 `OllamaDailyPlanReviewer` 和 `OllamaItineraryRepairAgent`，这两个类在当前 `backend/app/planning/llm.py` 中已不存在。每日复核当前由 `backend/app/planning/tourism.py` 的确定性 `review_daily_schedule` 承担，修复循环是 `graph.py` 的 `verify_plan` ⇄ `repair_plan`（最多 `MAX_AUTO_REPAIR_ATTEMPTS = 4` 轮）。建议把该测试改为针对 `review_daily_schedule` 与 verify⇄repair 迭代的回归用例，或删除陈旧的 LLM 复核类测试。

## P1：前端构建与依赖不一致

1. **TanStack VueQuery 已注册但零使用**。`frontend/package.json` 声明了 `@tanstack/vue-query`，但 `frontend/src/` 下没有代码 import 该库。保留依赖会静默拖大打包体积。建议确认是否计划使用；短期不用则从依赖中移除，避免无主依赖。
2. **`VITE_API_BASE_URL` 未在 `frontend/src/vite-env.d.ts` 声明**。`frontend/src/api/trips.ts` 通过 `import.meta.env.VITE_API_BASE_URL` 读取，但 `vite-env.d.ts` 的 `ImportMetaEnv` 只声明了 `VITE_AMAP_JSAPI_KEY`、`VITE_AMAP_SECURITY_JS_CODE`、`VITE_AMAP_SERVICE_HOST`。补齐类型声明可让编辑器校验环境变量拼写。
3. **`VITE_API_BASE_URL` 未作为构建参数传入 `frontend/Dockerfile`**。`frontend/Dockerfile` 通过 `ARG`/`ENV` 传入高德 JSAPI 相关的三个变量，但没有传入 `VITE_API_BASE_URL`。容器内前端由 nginx 把 `/api/` 代理到后端，空默认值可用；但若需要在前端构建期显式指定 API 基址（例如非同源部署），该值从 `.env` 传不过来。建议在需要非默认基址时补上 `ARG`/`ENV`。

## P2：版本库与工程化

1. **新增文件未纳入 git 跟踪**。`evaluation/`、`submission/`、`deploy/` 下的部分脚本、`frontend/src/utils/`、`frontend/src/stores/trip.spec.ts` 以及本 `docs/` 目录下 4 份评审/规划文档仍处于未跟踪状态。`deploy/` 下的 `api_smoke.py`、`full_journey_acceptance.py`、`edit_replan_acceptance.py` 是评审与验收入口，应纳入版本库以保证可复现。请在确认内容后分拣提交，避免把临时产物混入。
2. **本地凭据同步脚本**。`deploy/sync-local-secrets.ps1` 负责把 `Skills/` 下的本地凭据同步到 `.env`，属于开发辅助；确认其行为后纳入跟踪，并在文档中说明用途，防止被误当作线上部署步骤。
3. **评测场景纳入跟踪**。`evaluation/scenarios.json` 与 `run_evals.py` 是需求理解评测的固定输入，应纳入版本库并随需求变更同步维护。

## 依赖与验收命令

```powershell
docker compose up -d --build backend worker frontend
python deploy/api_smoke.py
python deploy/edit_replan_acceptance.py
python deploy/full_journey_acceptance.py --keep-trip
cd frontend; npm run build
```

外部服务配额、网络和登录状态属于运行依赖。验收报告记录调用成功/降级、来源数量、规划批次号和最终校验结果，不记录 API Key、Cookie 或模型私有思维链。