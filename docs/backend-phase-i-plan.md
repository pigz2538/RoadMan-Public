# 阶段 I：工程化、可观测和部署（首轮）

## 本轮已实现

- `GET /api/v1/ops/metrics`：返回进程请求计数、状态码、平均延迟、热点路由，以及 Skill 调用成功率、缓存命中、失败数和延迟；
- `GET /api/v1/skills/metrics`：独立查看各 Adapter 调用量和当前可用的成本计量字段；
- 每个请求生成/透传 `X-Request-ID` 与 `X-Trace-ID`，结构化日志只记录方法、路径、状态码和耗时，不记录请求体或密钥；
- 默认启用按客户端 IP 的滑动窗口限流，超过限制返回统一 `RATE_LIMITED` 错误和 `Retry-After`；
- 导出依赖和生成器已纳入 Docker/Conda requirements，保留统一快照作为输入；
- 新增 46 个后端测试覆盖导出格式和监控接口。

## 配置

```text
ENABLE_RATE_LIMIT=true
RATE_LIMIT_PER_MINUTE=600
```

## 下一步

- 文件/导出临时文件自动清理和保留策略；
- PostgreSQL/SQLite 备份脚本与恢复演练；
- Nginx HTTPS/内网穿透部署模板；
- 压力测试报告和监控页面；
- 外部 API 失败重试的统一退避与告警。
