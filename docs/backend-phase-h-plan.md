# 阶段 H：附件、版本与导出

## 当前已实现

### 附件解析确认

- 上传白名单扩展到 PNG/JPEG/WebP、PDF、DOCX、Markdown、XLSX；
- 继续校验扩展名、声明 MIME、文件签名或 OOXML ZIP 内部结构；
- PDF 使用 `pypdf`，Word 使用 `python-docx`，Excel 使用 `openpyxl`；
- Markdown 直接按 UTF-8 解析；
- 图片和订单截图在配置 Ollama Cloud 时发送图像进行结构化抽取；
- 输出地点、酒店、日期、订单号、文字预览与警告；
- 解析结果先保存为 `preview`，不会写入正式 Trip；
- 用户提交允许列表中的地点后才进入 `TripRequest.must_visit`。

接口：

- `POST /api/v1/files/{file_id}/extract`
- `POST /api/v1/files/{file_id}/confirm`

### 行程版本

- 用户主动命名保存；
- 快照包含 Trip、规划 State 和 Markdown；
- 可列出历史版本并恢复；
- 不实现复杂 Diff。

接口：

- `POST/GET /api/v1/trips/{trip_id}/versions`
- `POST /api/v1/trips/{trip_id}/versions/{version_id}/restore`

### 导出

- Markdown 行程安排支持浏览器下载；
- 前端规划页提供“保存版本”和导出入口；
- 从同一份冻结 Trip Snapshot 生成 PDF、PPTX 和长图 PNG，避免不同格式内容漂移；
- 导出未就绪时返回结构化 `ROADBOOK_NOT_READY`，不会生成空文件。

## 新增依赖

均已固定在 `backend/requirements.txt`：

- `pypdf`
- `python-docx`
- `openpyxl`

## 已补齐

- `GET /api/v1/trips/{trip_id}/roadbook.pdf`；
- `GET /api/v1/trips/{trip_id}/roadbook.pptx`；
- `GET /api/v1/trips/{trip_id}/roadbook.png`；
- 前端规划页提供 PDF、PPT、长图下载按钮；

## 尚待完成

- 专用地图导出页与 Playwright 地图截图嵌入 PPT/PDF；
- 订单截图真实模型兼容性与字段召回测试；
- 导出任务异步化及文件自动清理。
