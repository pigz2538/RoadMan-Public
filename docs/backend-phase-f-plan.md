# 阶段 F：旅游活动与酒店能力

## 已接入链路

规划图在基础路线完成后组织三类候选：

- 高德 POI：景点、餐饮、住宿兜底；
- FlyAI / 飞猪：按目的地、入住和离店日期搜索实时酒店；
- FlyAI / 飞猪景点：按城市和关键词补充门票名称与实时/脱敏价格；
- OpenTripMap：在境外目的地周边补充英文名、原始名称、距离、评分与来源；
- 已选景点继续调用高德真实步行、骑行、公交或驾车路线。

`Schedule Agent` 使用确定性空闲时间窗安排每日三餐、景点停留、每晚酒店入住和
次日退房，并在长途驾驶拆段后顺延后续活动。餐食、景点和酒店不得与移动阶段或
彼此重叠。

酒店有 FlyAI 结果时保存价格区间和 `FlyAI / 飞猪` 来源；FlyAI 不可用或没有结果
时降级为高德住宿 POI，不伪造价格。FlyAI 返回 `¥3xx` 一类脱敏价格时保存为
`¥300–399` 估算区间，不错误展示成 `¥3`。

## 运行依赖

Python 依赖仍在 `backend/requirements.txt`。FlyAI CLI 版本锁定在
`backend/node-requirements.txt`；后端 Dockerfile 用 Node 构建阶段安装 CLI，只将
Node 运行时和 FlyAI 包复制进 Python 镜像。

候选按评分、与目的地的距离、预算、用户偏好和来源完整度形成可解释综合分。
`rank`、`score`、`recommendation_reasons` 和 `backup` 会持久化在规划状态中。

## 当前接口与数据

- Skill 名称：`flyai.hotel`、`flyai.poi`、`opentripmap.nearby`
- 输入：目的地、附近 POI、入住日期、离店日期、最高价、排序
- 输出：酒店/景点名称、坐标、地址、星级、评分、门票或酒店价格区间、图片和详情链接
- Activity 展示：计划时间、开放/入住说明、价格和数据来源
- `GET /api/v1/trips/{trip_id}/recommendations`：按景点、住宿、餐饮读取已排序备选。
- `POST /api/v1/trips/{trip_id}/patches/preview`：只生成 `PlanPatch`，不修改正式行程。
- `POST .../patches/{patch_id}/apply|reject`：用户确认后应用，或明确放弃。

前端 Agent 面板可按当前分类查看备选，并在“加入”或“替换所选”后先展示影响日期、
时长、候选名称和“正式行程尚未修改”。只有点击“确认应用”才写入 Trip。

## 验收证据

- 真实高德排程：`trip_58a26bb45236`，三天每天三餐、两处景点、两晚酒店，
  无时间重叠且路线闭环；
- 真实 FlyAI 酒店：`trip_5808dbda8640`，容器内 FlyAI 返回酒店、飞猪来源和
  `¥300–399` 脱敏价格区间，规划状态为完成。
- 真实 FlyAI 景点：北京“故宫”返回 10 个景点，珍宝馆和钟表馆门票脱敏价格正确
  解析为 `¥20–29`。
- 真实 OpenTripMap：伦敦中心 3 km 范围返回 5 个带英文名、距离和评分的景点。
- 相关后端测试 30 项通过；候选补丁测试确认 preview 前后 Trip 不变，apply 后才
  新增活动。

## 阶段结论

阶段 F 范围已完成。应用候选后对受影响路线、天气、能耗和冲突进行增量重算属于
阶段 G 的局部重规划能力；F 已通过 `requires_replan` 与 `impact_scope` 明确标记
受影响范围。
