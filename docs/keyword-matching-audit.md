# 语义关键词匹配审计

本次审计的原则是：用户自然语言的意图、地点、体验、同行关系、交通偏好和修改动作由规划 Agent 判断；代码只负责协议解析、结构校验、候选 ID 精确查找和安全约束。

## 已移除的语义关键词路径

- `backend/app/planning/editing.py`
  - 删除了按“添加/删除/替换/吃饭/酒店/多逛”等词扫描原始消息的逻辑。
  - 修改 Agent 必须返回 `intent`、`day_id`、阶段/活动 ID、候选 ID、分类和停留时长；后端只按 ID 或完整名称精确核对，不再做名称子串猜测。
  - 未配置 Agent 时返回 `EDIT_AGENT_REQUIRED`，不会退回关键词工作流。
- `backend/app/services/attachments.py`
  - 删除了从附件文本按“景区/公园/酒店/山/湖”等词提取地点的逻辑。
  - 配置多模态/文本 Agent 时完全采用结构化 Agent 结果；未配置时只保留预览并提示，不猜测。
- `backend/app/api/trips.py`
  - 删除了从原始需求扫描“跨海/昨天/出发/到达”和时钟的预检逻辑。
  - 跨海、过去返回和时间窗口只读取 Requirement Agent 的结构化字段；用户在确认界面提交的答案仍按字段协议写入。
- `backend/app/planning/llm.py`
  - Requirement Agent 负责地点、周几、相对日期、人数、偏好、特殊活动和交通语义。
  - `deterministic_extract` 仍只保留明确数字日历字面量；预检使用的 `extract_structural_constraints` 另外解析今天/明天和周一至周五这类日历结构，用于校验或模型不可用时的安全兜底，不产生地点、人数、偏好或交通结论。
- `backend/app/planning/llm.py` 与 `backend/app/planning/graph.py`
  - OpenStreetMap/OpenTripMap 与高德候选的合并、翻译和新增只接受 POI Curator Agent 返回的候选 ID 决策。
  - Agent 不可用时不再用名称子串兜底；Agent 返回的合并目标在后端仅做规范化后的完整名称精确核对。
- `backend/app/planning/deep_drive.py`
  - 山路能耗和车辆能力校验改为读取路线海拔累计爬升（默认阈值 300 m），不再从地名字符推断山路。

## 保留但不属于语义判断的匹配

- JSON/URL/MIME/时间格式正则：协议和安全校验。
- 高德 POI 的 `keywords` 参数：由规划 Agent 已确定目的地/类别后向地图服务发起检索，不读取原始用户文本做分类。
- 前端类别标签、SSE 事件名称映射：展示层转换，不参与需求理解。
- 候选 ID、阶段 ID、活动 ID 的精确相等比较：防止把相似名称误改成错误行程。

## 运行约束

生产环境应配置 `OLLAMA_API_KEY` 和 `OLLAMA_MODEL`。没有模型时，系统会明确要求配置 Agent 或让用户补充结构化字段，不会静默采用旧关键词推断。
