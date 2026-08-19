# POI 数据增强设计

本文描述 RoadMan 当前的地点（POI）研究、候选聚合、身份校验、证据留存与图片补全实现及其设计边界。以 `backend/app/planning/poi_enrichment.py`、`destination_research.py`、`recommendations.py`、`llm.py` 及 `backend/app/planning/tourism.py` 的策展/研究智能体为唯一依据，不描述代码中不存在的能力。

## 1. 总体原则

规划以高德、FlyAI、OpenTripMap 为地点身份的权威来源，把对外部网页和搜索的抓取限制为“尽力而为”的增强层：

1. 候选召回与最终采用分离：先召回多重来源的地点候选，再由策展/排序智能体决定哪些可信、可安排。
2. 事实与文案分离：地图、开放数据、旅行平台分别作为证据；模型只综合证据，不凭空生成开放时间、预约、评分或价格。
3. 研究过程可追踪：每次搜索、详情读取、过滤、合并与降级都有来源记录，前端可逐项展示来源。
4. 失败不伪造：超时、被阻断或无来源时不阻塞主规划，相关字段标记为待更新，不用直线或猜测冒充真实数据。

## 2. 多源候选聚合

地点候选由多层来源汇入，最终进入 `tourism_candidates`：

- **地图/POI**：`amap.geocode`、`amap.poi`（`backend/app/skills/amap.py`）提供地理编码与 POI，是地点身份的权威来源。
- **目的地研究**：`destination_research.py` 的 `research_destination` 并行发起公开网页搜索（DuckDuckGo HTML）与 FlyAI 的 `keyword_search`/`ai_search`，产出 `web_sources` 与 `flyai_items`，保留查询（`queries`）、来源（`sources`）与各 provider 成功/失败状态（`providers`）。搜索层不下决定，只收集带来源的本地亮点。
- **行政区与多目的地流程**：需求理解智能体返回 `destination_names`、`destination_scope` 和一个规范路线锚点；省份/城市/多目的地会分别建立证据包。目的地研究智能体先从来源中筛选著名地标/代表性美食，随后 `OllamaDestinationPlanAgent` 输出分区、每日主轴、三餐和住宿区域的计划单，路线工具只执行这份经过语义筛选的计划。数组字符串、餐馆/校园误替代等非法形态会再次交给需求智能体修复。
- **开放地点**：`opentripmap.nearby`（`backend/app/skills/opentripmap.py`）在坐标半径内召回开放位置，保留 `xid`、名称、坐标、`detail_url` 与距离。
- **旅行候选**：FlyAI 的各适配器提供交通、酒店、餐饮与搜索候选。
- **模型研究建议**：`llm.py` 的 `OllamaDestinationResearchAgent` 产出 `agent_recommendations`，经 `recommendations.py` 的 `_research_recommendations` 提取，作为对既有候选的来源背书，而非新增无来源地点。

来源为空时 `research_destination` 返回 `status="needs_review"`，规划不因研究失败而中断。

## 3. 地点身份校验

研究建议与地图实体之间的绑定必须落到具体候选，避免把一句“推荐某地”凭空插进行程：

- `recommendations.py` 的 `_match_research_recommendation` 把研究建议名称与候选点名称规范化（去空白、分隔符后统一大小写）后做包含/相等匹配，仅当命中才把该建议的 `importance`、`area`、`suggested_minutes`、`best_time` 写入对应候选。
- 只有成功绑定到具体候选的研究建议才会影响排序；`destination_research_priority`、`destination_research_name`、`reason` 只写在匹配到的候选上。
- `_research_recommendations` 按 `category` 与名称过滤，缺名称或类别不符的建议被丢弃。
- `destination_research.py` 的 `_clean` 剥离 HTML 标签、`_url` 还原 DuckDuckGo 转发链接为真实目标 URL，保证名称与研究键的来源一致。

身份校验是“建议绑定到实体”而非意图关键词解析：`_normalise_research_name` 明确只处理标点、空白与大小写，避免把需求关键词当作地点身份。

## 4. 证据层与来源留存

每条事实都尽量带来源，`SourceRecord` 统一保存 `provider`、`title`、`url` 与抓取时间：

- 天气来源 `Open-Meteo` 标注 `license=CC BY 4.0`（`backend/app/skills/weather.py`）。
- FlyAI、OpenTripMap、网页研究都会把来源（`source_records`）随候选与活动一起持久化。
- `poi_enrichment.py` 的 `enrich_tourism_candidates` 为网页来源追加百度百科的 `source_records`，并先检查是否已存在同名 provider，避免重复记录。
- 后端保留一个候选 ID，`candidate_id`（`recommendations.py`）由 `category:provider:source_id` 构成，作为身份去重与排序回写的锚点。

`SourceRecord.license` 是可选字段，当前只有天气来源填写了许可证。其余来源的授权/条款版本与可导出性仍需在交付前逐项登记。

## 5. 图片补全

图片补全是有来源的公开图片补充，不做无来源抓取：

- `poi_enrichment.py` 的 `_fetch_candidate` 解析百度百科页面的 `og:image`（或 `twitter:image`）预览图作为候选图。
- 只有当候选尚未具备 `image_url` 时才回填（`if meta.get("image_url") and not candidate.get("image_url")`），已有来源图不会被覆盖，避免重复或替换权威来源图。
- OpenTripMap 的方向订阅按 `xid` 保留 `detail_url`，SKILL 描述会依 `xid` 拉取含 Wikipedia 摘录与图片的详情字段；后端当前只使用列表候选身份，详情图片以 `detail_url` 保留来源链接。
- 图片与来源留存在 `frontend/src/api/trips.ts` 的 `RecommendationCandidate.source_records` 中透出，前端卡片展示来源链接。

未授权再利用或无来源的图片不会写入公开导出。

## 6. 策展与排序智能体

`llm.py` 的 POI 系列智能体在证据之上做决策，不新增无来源事实：

- `OllamaPoiCurator`（`OllamaPoiCurator`）负责候选策展与说明。
- `OllamaPoiRanker` 通过 `apply_agent_ranking`（`recommendations.py`）只按已验证的 `candidate_id` 回写 `score`/`agent_reason`，未知 ID 不改动数据。
- `OllamaPoiSuitabilityAgent` 通过 `apply_agent_suitability` 附着 `suitable`、`confidence` 与 `weather/terrain/personal` 三向理由，不删除备选。
- 排序在来源算法（`rank_tourism_candidates`）与 Agent 评分之间分层：先给每个候选算来源/距离/评分/价格/研究优先级的基础分，再用 `apply_agent_ranking` 叠加 Agent 分，二者都保留独立字段。

`plan_attraction_coverage` 按研究智能体的 `area` 标签或坐标网格把来源背书的高优先级候选分到各天，保持地理集群在单日，避免酒店边界成为观光边界。

## 7. 证据与文案的边界

- 地点身份与坐标以地图/开放地点 provider 为准；用户明确说出的地点作为必须项，外部检索只补全信息，不用近似地点替换。
- 开放时间、预约、票价与停车等可变字段仅从公开页面解析的文本中提取结构（`poi_enrichment.py` 的 `_extract_public_facts`），并把确认态标为 `confirmed=False`，前端与导出按待核验展示。
- 无来源的模型补全只作为建议文案，不写入“已确认”字段。
- 相邻且坐标相距不超过约 3 km 的同一接驳区域会归一为同一逻辑站点，减少重复阶段与重复请求。

## 8. 失败与降级边界

- 网页研究（`_web_sources`）、百科补全（`_fetch_candidate`）与 OpenTripMap 任一层失败都返回非阻塞结果，规划继续。
- 候选身份缺失或搜索为空时返回结构化状态（如 `needs_review`、`SKILL_NOT_CONFIGURED`），不伪造“已搜索成功”。
- 外部 provider 故障可解释降级；相关实现见 `backend/app/skills/registry.py` 的统一超时、重试、缓存与审计。
