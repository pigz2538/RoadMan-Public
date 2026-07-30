# RoadMan 后端阶段 C 完成记录

更新日期：2026-07-30

状态：已完成，允许进入阶段 D

## 已交付

- PostgreSQL/SQLite 异步 ORM：Trip、Vehicle、File、Job、SkillCall。
- Alembic 首个基线迁移，可在空 PostgreSQL 和 SQLite 数据库建表。
- 统一业务、校验、HTTP 与未知异常响应；请求 ID 与结构化日志贯穿请求。
- Redis 跨进程 Skill 缓存，故障时回退进程内缓存。
- Adapter 版本化缓存键、输入校验、限定网络错误重试和 SkillCall 来源审计。
- 高德地理编码、驾车、统一多模态路线和 POI Adapter。
- Open-Meteo 天气 Adapter 与固定车型参数 Adapter。
- Vehicle CRUD、文件安全上传/下载、Job 创建/查询/取消。
- Redis + ARQ Worker；Job 状态支持 queued/running/completed/failed/cancelled。
- 带事件 ID 和 `Last-Event-ID` 续传的 SSE 管理器。
- Docker Compose 中 PostgreSQL、Redis、Backend、Worker、Frontend/Nginx 完整运行。

## 验收结果

- Alembic：容器 PostgreSQL 为 `20260730_0001 (head)`；空 SQLite 迁移成功。
- 自动测试：后端 `15 passed`。
- Schema：成功导出 16 个共享 JSON Schema。
- 真实高德：武汉大学到庐山驾车约 254 km，返回 3244 个道路点；步行、骑行、
  公交路径也均返回真实点列。
- 真实 POI：如琴湖查询成功；Open-Meteo 返回逐小时天气。
- 缓存：同一车型查询第二次 `cache_hit=true`；Redis 健康状态为 `ready`。
- 异步任务：ARQ Worker 消费任务并更新至 `completed / 100%`。
- SSE：携带 `Last-Event-ID: 3` 重连后从事件 4 继续。
- 审计：可查询 amap.poi、amap.route、carinfo.demo、open_meteo.forecast 调用记录，
  且不保存请求载荷和密钥。

## 安全与降级

- API Key 只从环境变量或本地 Skill 凭据读取，不写入仓库、日志或审计表。
- Redis 不可用时 Skill 缓存回退内存；Job 入队失败返回可识别状态。
- 高德真实交通方式全部失败时返回 `ROUTE_UNAVAILABLE`；前端才可画灰色虚线提示。
- 文件上传同时校验扩展名、声明 MIME、真实文件签名、大小和随机化存储名。

阶段 D 将用 LangGraph 把需求抽取、澄清、规划、验证和修复连成可恢复的规划闭环。
