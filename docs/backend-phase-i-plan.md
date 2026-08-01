# 阶段 I：工程化、可观测和部署

## 本轮已实现

- `GET /api/v1/ops/metrics`：返回进程请求计数、状态码、平均延迟、热点路由，以及 Skill 调用成功率、缓存命中、失败数和延迟；
- `GET /api/v1/skills/metrics`：独立查看各 Adapter 调用量和当前可用的成本计量字段；
- 每个请求生成/透传 `X-Request-ID` 与 `X-Trace-ID`，结构化日志只记录方法、路径、状态码和耗时，不记录请求体或密钥；
- 默认启用按客户端 IP 的滑动窗口限流，超过限制返回统一 `RATE_LIMITED` 错误和 `Retry-After`；
- 导出依赖和生成器已纳入 Docker/Conda requirements，保留统一快照作为输入；
- 上传文件由 ARQ 定时任务按保留天数自动清理，删除前校验解析路径必须位于上传目录；
- `deploy/scripts` 提供 PostgreSQL 备份与显式确认恢复脚本；
- `deploy/nginx/https.conf.template` 提供 TLS、反向代理和 SSE 长连接配置；
- `/ops` 提供运行监控页面；`backend/scripts/load_smoke.py` 提供无额外依赖的并发压力冒烟；
- 48 个后端测试覆盖规划、编辑、导出、监控、接口和降级路径。

## 配置

```text
ENABLE_RATE_LIMIT=true
RATE_LIMIT_PER_MINUTE=600
FILE_RETENTION_DAYS=30
```

## 运维命令

```powershell
# 备份
.\deploy\scripts\backup.ps1

# 恢复（会覆盖当前数据库，必须显式确认）
.\deploy\scripts\restore.ps1 -BackupFile .\backups\roadman-时间.sql -ConfirmRestore

# 压力冒烟
python .\backend\scripts\load_smoke.py --requests 100 --concurrency 10
```

监控页面：`http://localhost:8080/ops`。
