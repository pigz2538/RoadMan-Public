# RoadMan 后端阶段 C 实施计划

更新日期：2026-07-30
依据：`RoadMan_分阶段实施总规划.md` 阶段 C 与集成顺序

## 1. 当前基线

已经具备：

- FastAPI 生命周期、CORS、健康检查和统一 `AppError` 雏形；
- SQLAlchemy Async 与 Trip JSON 文档持久化；
- Trip CRUD、固定武汉—庐山行程和 SSE 模拟事件；
- Skill Adapter/Registry 基类；
- Registry 内存级输入校验、超时、重试、缓存和健康检查；
- 高德地理编码、驾车路线 Adapter；
- 前端高德 JSAPI 与多交通方式路线展示。

阶段 C 尚未完成，不能直接进入 LangGraph：

- Vehicle、File、Job、SkillCall 数据表和 API；
- Alembic 数据库迁移；
- Redis 缓存与 ARQ 队列；
- Pydantic 校验错误、未知异常和第三方错误的统一响应；
- Skill 调用结构化日志、来源审计、敏感字段过滤；
- 高德统一多交通方式路线、POI；
- Open-Meteo、CarInfo；
- 可断线续传的 SSE 管理器；
- 阶段 C 集成测试。

## 2. 实施顺序

### C1：运行底座与数据表

1. 加入 Alembic 和 ARQ 依赖，保留 SQLite 本地开发兼容。
2. 拆分 ORM 表：Trip、Vehicle、File、Job、SkillCall。
3. 为 PostgreSQL 建立首个迁移；测试使用 SQLite 临时库。
4. 统一 `AppError`、请求校验错误、404 和未知异常响应。
5. 增加 request id 与结构化日志，日志不得包含 API Key。

验收：

- 新数据库可由迁移创建；
- 现有 Trip CRUD 不回归；
- 五类表可读写；
- 所有错误都返回 `{error:{code,message,details,request_id}}`。

### C2：Skill Registry 工程化

1. 将缓存抽象为 `SkillCache`，提供内存和 Redis 两种实现。
2. 缓存键包含 Adapter 版本和规范化参数。
3. 只重试网络错误、超时和 5xx；参数错误与无结果不重试。
4. 写入 SkillCall 审计：provider、adapter、耗时、缓存命中、成功状态、
   错误码、来源摘要；不记录密钥。
5. 健康检查区分 `ready/degraded/down`。

验收：

- Redis 可用时跨进程命中缓存；
- Redis 不可用时降级到内存缓存；
- 超时、重试和缓存有确定性测试；
- 日志与数据库均不出现完整密钥。

### C3：首批真实 Adapter

按以下顺序实现：

1. `amap.route`：尊重首选方式，支持 driving/riding/walking/transit，
   驾车无结果时按距离和同城规则降级；全部失败返回
   `ROUTE_UNAVAILABLE`，不把直线伪装成 geometry。
2. `amap.poi`：关键字、城市、类型、中心点和半径查询。
3. `open_meteo.forecast`：按坐标和时间返回天气样本。
4. `carinfo.demo`：从固定车型样本返回续航和能耗参数。

详细路线契约见 `routing-fallback-design.md`。

验收：

- 武汉大学到庐山可返回驾车道路点列；
- 同城短距离可返回步行、骑行或公交；
- POI 与天气结果保留 `SourceRecord`；
- 无结果、限流、超时和密钥缺失均为统一错误。

### C4：业务 API、任务和 SSE

1. Vehicle CRUD；
2. 文件上传元数据、大小/MIME/扩展名校验和安全存储；
3. Job 创建、状态查询、取消；
4. ARQ Worker 与 Redis；
5. SSE 事件存储最近事件 id，支持 `Last-Event-ID` 续传；
6. 接口集成测试与 Docker Compose 验收。

## 3. 阶段 D 准入门槛

只有以下条件全部满足才开始 LangGraph：

- 阶段 C 的真实路线、POI、天气和车辆 Adapter 通过集成测试；
- Job 可取消，SSE 可重连；
- PostgreSQL/Redis 正常与降级路径均被测试；
- `project.md` 和 API 文档同步；
- 前端现有 E2E 不回归。

## 4. 第一开发切片

首个提交范围限定为：

- Alembic/ARQ 依赖与配置；
- 统一错误响应和 request id；
- Vehicle、File、Job、SkillCall ORM 表；
- 对应迁移及单元测试。

这一切片不加入 LangGraph、不改前端交互、不实现真实文件解析。
