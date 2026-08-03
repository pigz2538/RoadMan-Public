# RoadMan 仓库清理候选审计

> 审计日期：2026-08-03
> 结论：已按用户确认清理高置信度生成物；`前端1.png`、`前端2.png` 和所有待确认资产均保留。

## 1. 审计范围与方法

扫描了根目录、`backend`、`frontend`、`shared`、`deploy`、`Skills` 和 `docs`，并结合以下信息判断：

- `git status --short --ignored`：区分已跟踪文件、未跟踪文件和构建/运行时忽略文件；
- `rg`：搜索组件、脚本、资源和 Skill 的导入、构建、Docker、测试及文档引用；
- `git ls-files`：确认候选是否已经进入版本库；
- 逐项检查 `package.json`、Dockerfile、`.gitignore`、`.dockerignore` 和现有阶段文档。

“没有搜到引用”只代表当前静态引用证据不足，不等于可以立即删除。二进制模型、Skill 参考资料和部署脚本仍需产品确认或一次构建/运行验证。

## 2. 已按确认清理的高置信度生成物

这些是本地生成、缓存或运行时数据，已在本轮确认没有需要保留的本地产物后清理；它们不会影响版本库中的源代码。

| 路径/模式 | 证据 | 建议 |
| --- | --- | --- |
| `.history/` | 编辑器历史目录，已被忽略 | 已清理 |
| `.pytest_cache/`、`backend/.pytest_cache/`、`frontend/.pytest_cache/` | pytest 缓存，已被忽略 | 已清理；本轮测试后重新生成的根缓存也已再次清理 |
| `**/__pycache__/`、`*.pyc` | Python 字节码，已被忽略 | 已清理；测试后生成的后端缓存也已再次清理 |
| `Skills/.codegraph/codegraph.db`、`Skills/.codegraph/source.json` | Codegraph 生成索引，已被忽略 | 已清理，需要索引时重新生成 |
| `frontend/dist/` | Vite 构建输出，已被忽略 | 已清理，用 `npm run build` 重建 |
| `frontend/node_modules/` | npm 安装目录，已被忽略 | 已清理，用 `npm install` 重建 |
| `frontend/test-results/` | Playwright 运行产物，已被忽略 | 已清理 |
| `frontend/tsconfig.app.tsbuildinfo`、`frontend/tsconfig.node.tsbuildinfo` | TypeScript 增量构建缓存 | 已清理 |
| `backend/roadman.db`、根目录 `roadman.db` | 本地 SQLite 运行数据，已被忽略 | 已清理；运行时会重新建库 |

## 3. 已跟踪、但需要确认的候选项

这些文件不能直接按“未被导入”删除，建议在确认后选择“删除”或“归档到 `docs/archive`”。

| 路径 | 当前证据 | 风险/建议 |
| --- | --- | --- |
| `frontend/public/car-suv.svg` | 没有搜到运行时引用；首页实际使用 `/models/car-concept-white.glb` | 可能是旧的静态占位图。确认不再需要旧首页后可删 |
| `frontend/public/models/car-concept.glb` | 仅在 `LICENSE.md` 中作为原始模型名称出现；当前页面和 E2E 使用白模 | 可能是原始素材。保留许可证和来源记录后再决定删除/归档 |
| `frontend/public/models/car-concept-optimized.glb` | 没有搜到页面或测试引用 | 可能是中间压缩产物。先比较与白模的用途和许可证，再删 |
| `frontend/scripts/make-white-glb.mjs` | 手工资源处理脚本，不在 npm scripts 中 | 不是运行时依赖；若白模已稳定，可归档而非直接删 |
| `frontend/scripts/sanitize-glb.mjs` | 手工资源清洗脚本，不在 npm scripts 中 | 同上，删除前确认后续模型更新不再复用 |
| `Skills/openchargemap/references/ocm-openapi-spec.yaml` | 当前后端没有搜到 Open Charge Map 适配器调用；规划文档仍把它列为电车路线能力 | 这是未接入的接口参考，不建议现在删；若改用其他充电数据源可归档 |
| `Skills/The-Complete-Guide-to-Building-Skill-for-Claude.pdf` | 仅作为 Skill 编写参考资料，无代码引用 | 可移出产品仓库或归档，删除前确认团队仍不需要它 |
| 根目录 `前端1.png`、`前端2.png` | 设计参考截图，无代码引用 | 属于设计资产，不是运行垃圾；确认设计评审结束后再归档 |

## 4. 证实必须保留的文件

以下文件虽然有些是 fallback、脚本或文档，但当前有明确用途，不纳入删除候选：

- `frontend/public/models/car-concept-white.glb`：`HomeView.vue`、Firefox 兼容测试和 E2E 测试直接引用；
- `frontend/src/components/map/MockRouteMap.vue`：由 `AmapRouteMap.vue` 在高德加载失败时作为 fallback 渲染；
- `backend/scripts/load_smoke.py`：被 `docs/backend-phase-i-plan.md` 明确作为并发冒烟脚本；
- `Skills/amap-lbs/index.js`、`gaode_skill.py` 和 `scripts/*`：Skill 运行说明、Docker 挂载和后端配置仍有引用；
- `Skills/amap-jsapi/*`：前端高德 JSAPI Key/安全密钥和地图接入说明，页面地图加载使用；
- `Skills/carinfo`：本次新增的车型管理实施计划和下一阶段车辆能力接入依赖；
- `Skills/weather`、`Skills/opentripmap`、`shared/examples`、`shared/schemas`：天气、景点、示例数据和前后端契约仍属于规划链路；
- `docs/backend-phase-*.md`、`docs/plan-completion-audit.md`、`docs/routing-fallback-design.md`：阶段实施和验收依据；
- `frontend/tests/e2e` 及快照：验证地图、模型和规划流程，不能因为是截图就当作垃圾。

## 5. 敏感文件：不删除、不提交、不展示内容

发现本地存在以下 Key 文件（本报告不记录其内容）：

- `Skills/amap-jsapi/apikey.txt`
- `Skills/amap-jsapi/secretkey.txt`
- `Skills/amap-lbs/apipkey.txt`
- `Skills/opentripmap/apikey.txt`

它们当前由本机忽略规则/工作区状态排除在 Git 变更之外，但建议补充明确的仓库忽略规则并在 CI 中做 secret scan。若这些 Key 曾被提交、截图或日志暴露，应立即轮换；清理候选审计不等于删除密钥，也不替代轮换。

## 6. 建议的下一次清理顺序

1. 已完成高置信度缓存、构建产物、测试产物和本地数据库清理；`前端1.png`、`前端2.png` 未触碰。
2. 对三份 GLB 和 SVG 做一次资源清单/许可证确认，只保留白模和必要的原始素材。
3. 决定是否把 Open Charge Map 参考资料和 Skill 编写 PDF 归档到 `docs/archive`。
4. 补充敏感文件忽略规则，运行 secret scan，再确认 `git status` 只剩预期源文件。
5. 后续若确认中风险资产删除，应单独提交“资源归档/清理”提交，避免和功能开发混在一起。

本轮新增的车型计划见 [`vehicle-management-plan.md`](./vehicle-management-plan.md)。
