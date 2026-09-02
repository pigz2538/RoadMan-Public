<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Crosshair, Download, Paperclip, Share2 } from '@lucide/vue'
import {
  answerClarification,
  createTripVersion,
  downloadTripMarkdown,
  downloadTripExport,
  confirmTripAttachment,
  extractTripAttachment,
  uploadTripAttachment,
  type AttachmentExtraction,
  fetchMockTrip,
  fetchPlanning,
  fetchTrip,
  startPlanning,
  previewDeletePatch,
  previewMapPointPatch,
  type SpecialEventResearch,
  type PlanningSnapshot,
} from '../api/trips'
import { useTripStore } from '../stores/trip'
import { useTripSSE } from '../composables/useTripSSE'
import AmapRouteMap from '../components/map/AmapRouteMap.vue'
import ActivityList from '../components/trip/ActivityList.vue'
import AgentPanel from '../components/agent/AgentPanel.vue'
import { humanizeDisplayText } from '../utils/displayLabels'
import type { Trip } from '../types/trip'
import mockTrip from '../../../shared/examples/wuhan-lushan-trip.json'

const router = useRouter()
const route = useRoute()
const store = useTripStore()
const loading = ref(true)
const degraded = ref(false)
const stageTrack = ref<HTMLElement | null>(null)
const planningSnapshot = ref<PlanningSnapshot | null>(null)
const contentHydrating = ref(false)
const clarificationAnswer = ref('')
const planningError = ref('')
const versionMessage = ref('')
const attachmentInput = ref<HTMLInputElement | null>(null)
const attachmentPreview = ref<AttachmentExtraction | null>(null)
const attachmentSelection = ref<string[]>([])
const attachmentMessage = ref('')
const mapPickMode = ref(false)
const mapPickCategory = ref<'attractions' | 'hotels' | 'meals'>('attractions')
const mapPickMessage = ref('')
const planningRestarting = ref(false)
const activePlanningBatchId = ref<string | null>(null)
const historyView = computed(() => route.query.history === '1')
const panelStorageKeys = {
  left: 'roadman-plan-left-collapsed',
  right: 'roadman-plan-right-collapsed',
  bottom: 'roadman-plan-bottom-collapsed',
} as const
function storedPanelState(key: string) {
  return typeof window !== 'undefined' && window.localStorage.getItem(key) === '1'
}
const leftPanelCollapsed = ref(storedPanelState(panelStorageKeys.left))
const rightPanelCollapsed = ref(storedPanelState(panelStorageKeys.right))
const bottomPanelCollapsed = ref(storedPanelState(panelStorageKeys.bottom))
let pollingTimer: number | undefined
let refreshInFlight = false
let refreshQueued = false
let lastRefreshStartedAt = 0
const stageDrag = { active: false, moved: false, startX: 0, startScrollLeft: 0, pointerId: -1 }
const categories = ['景点', '住宿', '餐饮', '服务'] as const
const { connect } = useTripSSE((event) => {
  if (activePlanningBatchId.value && event.batch_id && event.batch_id !== activePlanningBatchId.value) return
  store.addPlanningEvent(event)
  if (
    event.event === 'plan_updated'
    || event.event === 'planning_completed'
    || event.event === 'clarification_required'
    || event.event === 'planning_failed'
  ) {
    queuePlanningRefresh()
  }
})

const planningComplete = computed(() =>
  (planningSnapshot.value?.status === 'completed' || store.trip?.status === 'completed')
  && store.planningPresentationIdle
  && !contentHydrating.value,
)
const planningBusy = computed(() => {
  const status = planningSnapshot.value?.status || store.trip?.status
  return planningRestarting.value
    || (loading.value && route.query.planning === '1')
    || (['planning', 'collecting'].includes(status || '') && !planningComplete.value)
})
const planningProgress = computed(() => {
  const value = store.planningEvent?.progress ?? planningSnapshot.value?.progress.value ?? 1
  return Math.max(0, Math.min(100, Number(value) || 1))
})
const planningScenes = [
  { key: 'understand', until: 29, title: '正在理解这次旅行', caption: '扫描同行人、日期、偏好与必去地点，建立可行边界' },
  { key: 'intercity', until: 59, title: '正在搜索出发与抵达', caption: '检索城市、机场车站和沿途接驳，先把大方向连起来' },
  { key: 'discover', until: 69, title: '正在筛选目的地候选', caption: '逐个核对景点、餐饮和住宿的开放与适配信息' },
  { key: 'connect', until: 78, title: '正在让智能体协作', caption: '路线、地点与天气智能体共享候选并相互校验' },
  { key: 'context', until: 89, title: '正在补齐旅行上下文', caption: '检查天气、开放信息、服务设施与适配性' },
  { key: 'schedule', until: 94, title: '正在编排全天节奏', caption: '安排三餐、停留、休息、住宿与返程时间' },
  { key: 'verify', until: 100, title: '正在做最后复核', caption: '逐段核验时间重叠、路线连续和必去地点' },
] as const
const planningScene = computed(() => {
  const index = planningScenes.findIndex((scene) => planningProgress.value <= scene.until)
  const sceneIndex = index < 0 ? planningScenes.length - 1 : index
  return { ...planningScenes[sceneIndex], index: sceneIndex + 1 }
})
const visiblePlanningEvents = computed(() => store.planningEvents.slice(-7))
function humanizePlanningIssue(issue: { code: string; description: string }) {
  const description = String(issue.description || '').trim()
  const messages: Record<string, string> = {
    STAGE_OVERLAP: `同一天的移动时间发生冲突：${description.replace(/与前一移动阶段时间重叠$/, '没有在上一段结束后再出发')}`,
    ACTIVITY_TIME_OVERLAP: `行程时间线存在重叠：${description}`,
    ROUTE_DISCONTINUITY: `路线中间出现断点：${description.replace(/^阶段/, '在阶段')}`,
    ROUTE_NOT_CLOSED: '返程没有正确接回最初的出发点，当前路线还未闭合。',
    REQUIRED_PLACES_UNSCHEDULED: description,
    OVERNIGHT_HOTEL_MISSING: `住宿没有完整落到日程中：${description}`,
    RETURN_DEADLINE_UNACHIEVABLE: description,
    DRIVING_STAGE_CALENDAR_MISMATCH: `一段移动跨出了当天可用时间：${description}`,
    EMPTY_DAY_STAGES: `有一天没有形成可执行路线：${description}`,
  }
  return messages[issue.code] || description || '规划结果仍有一项未解决的约束。'
}
const planningFailure = computed(() => {
  if (planningSnapshot.value?.status !== 'failed') return null
  const issues = planningSnapshot.value.verification_result?.issues ?? []
  // A missing forecast is a non-blocking warning.  When another constraint
  // fails, show that actionable blocker first instead of misleadingly
  // presenting the weather warning as the reason the plan stopped.
  const blockers = issues.filter((item) => item.severity === 'blocker')
  const visibleIssues = (blockers.length ? blockers : issues).slice(0, 4)
  const details = [...new Set(visibleIssues.map(humanizePlanningIssue))]
  const repairAttempts = planningSnapshot.value.verification_result?.auto_repair_attempts ?? 0
  const plannerDefectCodes = new Set([
    'STAGE_OVERLAP', 'ACTIVITY_TIME_OVERLAP', 'ROUTE_DISCONTINUITY', 'ROUTE_NOT_CLOSED',
    'REQUIRED_PLACES_UNSCHEDULED', 'OVERNIGHT_HOTEL_MISSING', 'DRIVING_STAGE_CALENDAR_MISMATCH',
    'EMPTY_DAY_STAGES',
  ])
  const plannerDefect = blockers.some((item) => plannerDefectCodes.has(item.code))
  const preferences = store.trip?.request?.preferences ?? []
  const hint = plannerDefect
    ? '这是规划器内部仍未消解的路线或时间冲突，不代表你的需求不合理。你可以重试；系统不会要求你为内部排程错误放宽需求。'
    : preferences.length
      ? `已保留你的偏好：${preferences.slice(0, 3).join('、')}。上面的具体限制仍无法同时满足。`
      : '上面的具体限制仍无法同时满足，可返回需求页调整对应时间或交通条件。'
  const description = repairAttempts
    ? `系统已自动重排 ${repairAttempts} 次，仍有 ${blockers.length || visibleIssues.length} 项没有解决。`
    : '行程在最终复核中发现了尚未解决的问题。'
  return { description, details, hint, repairAttempts }
})

const filteredActivities = computed(() => {
  const typeMap: Record<string, string[]> = {
    景点: ['attraction'],
    住宿: ['hotel'],
    餐饮: ['meal'],
    服务: ['rest', 'charging', 'fueling', 'parking', 'service'],
  }
  return (store.currentDay?.activities ?? []).filter((item) => typeMap[store.category].includes(item.type))
})
const dayTimeline = computed(() => {
  const day = store.currentDay
  if (!day) return []
  return [
    ...day.stages.map((stage) => ({
      id: stage.id,
      kind: 'stage' as const,
      title: `${stage.origin.name} → ${stage.destination.name}`,
      label: stage.title,
      start: stage.planned_start,
      end: stage.planned_end,
      icon: modeEmoji(stage.mode, stage.transit_type),
    })),
    ...day.activities.map((activity) => ({
      id: activity.id,
      kind: 'activity' as const,
      title: activity.place.name,
      label: activity.type === 'hotel'
        ? '住宿'
        : activity.type === 'meal'
          ? '用餐'
          : activity.type === 'rest'
            ? '自由活动 / 休息'
            : '景点停留',
      start: activity.planned_start || '',
      end: activity.planned_end || '',
      icon: activity.type === 'hotel'
        ? '🏨'
        : activity.type === 'meal'
          ? '🍜'
          : activity.type === 'rest'
            ? '☕'
            : '🏞️',
    })),
  ].filter((item) => item.start).sort((left, right) => left.start.localeCompare(right.start))
})
const allStages = computed(() =>
  (store.trip?.days ?? []).flatMap((day, dayIndex) =>
    day.stages.map((stage) => ({ stage, dayIndex, dayIndexLabel: day.day_index })),
  ),
)
const currentGlobalStageIndex = computed(() =>
  allStages.value.findIndex((item) => item.stage.id === store.currentStageId),
)
const currentStagePosition = computed(() => currentGlobalStageIndex.value >= 0 ? currentGlobalStageIndex.value + 1 : 0)
const riskStages = computed(() =>
  allStages.value.filter(({ stage }) =>
    stage.risk_level === 'high'
    || stage.risk_level === 'moderate'
    || stage.warnings.length > 0,
  ),
)
const degradationNotices = computed(() => {
  const warnings = allStages.value.flatMap(({ stage }) => stage.warnings ?? [])
  const codes = new Set(warnings.map((warning) => warning.code))
  const notices: string[] = []
  if (codes.has('WEATHER_DATA_DEGRADED')) notices.push('天气服务暂不可用：已按基础风险继续，出发前需复核')
  if (codes.has('ENERGY_STOP_ESTIMATED') || codes.has('CHARGING_POWER_ESTIMATED')) {
    notices.push('补能数据不完整：位置、功率或时长含保守估算，出发前需确认可用性')
  }
  if (warnings.some((warning) => warning.estimated && !codes.has('WEATHER_DATA_DEGRADED'))) {
    notices.push('部分路线或服务使用估算值，不能直接作为实时导航依据')
  }
  return [...new Set(notices)]
})

async function load() {
  const tripId = String(route.params.tripId)
  store.resetPlanningEvents()
  try {
    if (tripId === 'trip_wuhan_lushan_demo') {
      store.trip = await fetchMockTrip()
    } else {
      store.trip = await fetchTrip(tripId)
      const status = store.trip.status
      const isActivePlanning = status === 'collecting' || status === 'planning'
      // Opening a saved completed/failed trip is read-only. Do not subscribe
      // to the old SSE backlog or replay the progressive planning animation.
      // A historical task that is genuinely still running remains live.
      planningSnapshot.value = await fetchPlanning(tripId)
      store.setRouteReplanRequired(Boolean(planningSnapshot.value.route_replan_required))
      activePlanningBatchId.value = planningSnapshot.value.planning_batch_id || null
      if (isActivePlanning) {
        connect(tripId, { live: historyView.value })
        await refreshPlanning()
      }
    }
    ensureCurrentSelection()
  } catch {
    if (tripId === 'trip_wuhan_lushan_demo') {
      store.trip = mockTrip as unknown as typeof store.trip
      degraded.value = true
    } else {
      planningError.value = '行程加载失败，请返回首页重试。'
    }
  } finally {
    loading.value = false
    await nextTick()
    centerCurrentStage('auto')
  }
}

async function refreshPlanning() {
  const tripId = String(route.params.tripId)
  if (tripId === 'trip_wuhan_lushan_demo') return
  if (refreshInFlight) {
    refreshQueued = true
    return
  }
  refreshInFlight = true
  lastRefreshStartedAt = Date.now()
  if (pollingTimer) window.clearTimeout(pollingTimer)
  pollingTimer = undefined
  try {
    const snapshot = await fetchPlanning(tripId)
    planningSnapshot.value = snapshot
    store.setRouteReplanRequired(Boolean(snapshot.route_replan_required))
    if (snapshot.status === 'completed') {
      await hydrateTripProgressively(await fetchTrip(tripId))
      ensureCurrentSelection()
      await nextTick()
      centerCurrentStage('auto')
      return
    }
    if (snapshot.status === 'failed') {
      planningError.value = ''
      return
    }
    if (snapshot.status !== 'clarification_required') {
      const partialTrip = await fetchTrip(tripId)
      store.trip = partialTrip
      ensureCurrentSelection()
      await nextTick()
      centerCurrentStage('smooth')
      queuePlanningRefresh(900)
    }
  } catch (error) {
    planningError.value = error instanceof Error ? error.message : '无法读取规划进度'
  } finally {
    refreshInFlight = false
    if (refreshQueued && planningSnapshot.value?.status !== 'completed' && planningSnapshot.value?.status !== 'failed') {
      refreshQueued = false
      queuePlanningRefresh(120)
    }
  }
}

function queuePlanningRefresh(delay = 0) {
  if (pollingTimer) return
  const elapsed = Date.now() - lastRefreshStartedAt
  const wait = Math.max(delay, Math.max(0, 650 - elapsed))
  pollingTimer = window.setTimeout(() => {
    pollingTimer = undefined
    void refreshPlanning()
  }, wait)
}

function ensureCurrentSelection() {
  if (!store.trip?.days.length) return
  const day = store.currentDay
  if (!day) {
    store.setDay(0)
    return
  }
  if (!day.stages.some((stage) => stage.id === store.currentStageId)) {
    store.setStage(day.stages[0])
  }
}

function pause(ms: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, ms))
}

async function hydrateTripProgressively(nextTrip: Trip) {
  const existing = store.trip
  const currentDays = nextTrip.days.map((day) => {
    const previous = existing?.days.find((item) => item.id === day.id)
    return previous
      ? { ...day, stages: [...previous.stages], activities: [...previous.activities], items: [...previous.items] }
      : { ...day, stages: [], activities: [], items: [] }
  })
  const stageIds = new Set(currentDays.flatMap((day) => day.stages.map((stage) => stage.id)))
  const activityIds = new Set(currentDays.flatMap((day) => day.activities.map((activity) => activity.id)))
  const missing = nextTrip.days.reduce(
    (count, day) => count
      + day.stages.filter((stage) => !stageIds.has(stage.id)).length
      + day.activities.filter((activity) => !activityIds.has(activity.id)).length,
    0,
  )
  if (!missing) {
    store.trip = nextTrip
    return
  }
  contentHydrating.value = true
  let working: Trip = { ...nextTrip, days: currentDays }
  store.trip = working
  for (let dayIndex = 0; dayIndex < nextTrip.days.length; dayIndex += 1) {
    const target = nextTrip.days[dayIndex]
    for (const stage of target.stages) {
      if (working.days[dayIndex].stages.some((item) => item.id === stage.id)) continue
      working = {
        ...working,
        days: working.days.map((item, index) => index === dayIndex
          ? { ...item, stages: [...item.stages, stage], items: [...item.items, { type: 'stage', id: stage.id }] }
          : item),
      }
      store.trip = working
      await pause(105)
    }
    for (const activity of target.activities) {
      if (working.days[dayIndex].activities.some((item) => item.id === activity.id)) continue
      working = {
        ...working,
        days: working.days.map((item, index) => index === dayIndex
          ? { ...item, activities: [...item.activities, activity], items: [...item.items, { type: 'activity', id: activity.id }] }
          : item),
      }
      store.trip = working
      await pause(115)
    }
  }
  store.trip = nextTrip
  contentHydrating.value = false
}

async function handleMapPoint(point: { name: string; address?: string; longitude: number; latitude: number }) {
  if (!store.trip || !store.currentDay) return
  mapPickMessage.value = `正在为“${point.name}”生成修改预览…`
  try {
    store.pendingPatch = await previewMapPointPatch(store.trip.id, {
      day_id: store.currentDay.id,
      category: mapPickCategory.value,
      ...point,
    })
    store.selectedNodeId = store.currentStageId
    mapPickMode.value = false
    mapPickMessage.value = `已选中“${point.name}”，请在右侧行程助理确认是否加入行程。`
  } catch (error) {
    mapPickMessage.value = error instanceof Error ? error.message : '地图选点暂时无法生成修改预览'
  }
}

async function submitClarification() {
  const answer = clarificationAnswer.value.trim()
  if (!answer) return
  planningError.value = ''
  try {
    planningSnapshot.value = await answerClarification(String(route.params.tripId), answer)
    clarificationAnswer.value = ''
    queuePlanningRefresh(500)
  } catch (error) {
    planningError.value = error instanceof Error ? error.message : '提交补充信息失败'
  }
}

async function requestActivityRemoval(activityId: string) {
  if (!store.trip || !store.currentDay) return
  planningError.value = ''
  try {
    store.pendingPatch = await previewDeletePatch(store.trip.id, {
      day_id: store.currentDay.id,
      activity_id: activityId,
    })
  } catch (error) {
    planningError.value = error instanceof Error ? error.message : '无法生成删除预览'
  }
}

async function saveVersion() {
  if (!store.trip || store.trip.id === 'trip_wuhan_lushan_demo') return
  const name = window.prompt('版本名称', `行程版本 ${new Date().toLocaleString('zh-CN')}`)?.trim()
  if (!name) return
  try {
    const version = await createTripVersion(store.trip.id, name)
    versionMessage.value = `已保存版本：${version.name}`
    window.setTimeout(() => { versionMessage.value = '' }, 3000)
  } catch (error) {
    versionMessage.value = error instanceof Error ? error.message : '版本保存失败'
  }
}

function exportMarkdown() {
  if (!store.trip || store.trip.id === 'trip_wuhan_lushan_demo') return
  downloadTripMarkdown(store.trip.id)
}

function exportSnapshot(format: 'pdf' | 'pptx' | 'png' | 'html') {
  if (!store.trip || store.trip.id === 'trip_wuhan_lushan_demo') return
  downloadTripExport(store.trip.id, format)
}

function openAttachmentPicker() {
  attachmentInput.value?.click()
}

async function onAttachmentSelected(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file || !store.trip || store.trip.id === 'trip_wuhan_lushan_demo') return
  attachmentMessage.value = '正在解析附件…'
  try {
    const uploaded = await uploadTripAttachment(store.trip.id, file)
    attachmentPreview.value = await extractTripAttachment(uploaded.id)
    attachmentSelection.value = [...attachmentPreview.value.places]
    attachmentMessage.value = `已解析：${uploaded.original_name}`
  } catch (error) {
    attachmentMessage.value = error instanceof Error ? error.message : '附件解析失败'
  } finally {
    if (attachmentInput.value) attachmentInput.value.value = ''
  }
}

async function confirmAttachment() {
  if (!attachmentPreview.value) return
  try {
    await confirmTripAttachment(attachmentPreview.value.file_id, attachmentSelection.value)
    attachmentPreview.value = null
    attachmentMessage.value = '附件地点已确认，重新规划时会纳入行程'
  } catch (error) {
    attachmentMessage.value = error instanceof Error ? error.message : '附件确认失败'
  }
}

function selectStageById(stageId: string) {
  const item = allStages.value.find((entry) => entry.stage.id === stageId)
  if (item) selectJourneyStage(item)
}

function selectActivityById(activityId: string) {
  const activity = store.currentDay?.activities.find((item) => item.id === activityId)
  if (activity) store.selectActivity(activity)
}

function selectJourneyStage(item: (typeof allStages.value)[number]) {
  if (store.currentDayIndex !== item.dayIndex) store.setDay(item.dayIndex)
  store.setStage(item.stage)
}

function moveStage(delta: number) {
  const target = allStages.value[currentGlobalStageIndex.value + delta]
  if (target) selectJourneyStage(target)
}

function centerCurrentStage(behavior: ScrollBehavior = 'smooth') {
  const target = stageTrack.value?.querySelector<HTMLElement>(`[data-stage-id="${store.currentStageId}"]`)
  target?.scrollIntoView({ behavior, block: 'nearest', inline: 'center' })
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    // Planning timestamps are persisted as Asia/Shanghai instants.  Do not
    // let the browser/OS timezone silently move a card to the previous day.
    timeZone: 'Asia/Shanghai',
  }).format(new Date(value))
}

function planningAgentName(event: { tool?: string; node?: string }) {
  const tools: Record<string, string> = {
    'flyai.keyword_search': '目的地检索智能体',
    'flyai.ai_search': '语义旅行检索智能体',
    'web.destination_research': '目的地研究智能体',
    'amap.route': '在线地图路线智能体',
    'amap.poi': '在线地图地点智能体',
    'flyai.poi': '旅行信息搜索智能体',
    'flyai.hotel': '住宿搜索智能体',
    'amap.poi/amap.route': '地图路线智能体',
    'web.poi_research': '公开信息检索智能体',
    'deepseek.destination_plan': '目的地分区计划智能体',
    'deepseek.poi_ranker': '候选排序智能体',
    'deepseek.poi_suitability': '候选适配智能体',
    'deepseek.poi_curator': '地点策展智能体',
    'ollama.poi_curator': '地点策展智能体',
    'open_meteo.forecast': '天气智能体',
  }
  // Provider-neutral audit IDs are emitted by the backend.  Keep the former
  // deepseek/ollama keys above readable for historical records.
  tools['llm.destination_plan'] = tools['deepseek.destination_plan'] || '目的地分区规划智能体'
  tools['llm.poi_ranker'] = tools['deepseek.poi_ranker'] || '候选排序智能体'
  tools['llm.poi_suitability'] = tools['deepseek.poi_suitability'] || '候选适配智能体'
  tools['llm.poi_curator'] = tools['deepseek.poi_curator'] || '地点策展智能体'
  const nodes: Record<string, string> = {
    destination_research: '目的地研究智能体',
    review_tourism_suitability: '候选适配智能体',
    review_tourism_suitability_wait: '候选适配智能体',
    review_tourism_suitability_finalize: '候选适配智能体',
    load_context: '上下文智能体',
    extract_trip_request: '需求理解智能体',
    apply_defaults: '需求校验智能体',
    build_base_route: '路线智能体',
    split_into_days: '日程拆分智能体',
    discover_tourism: '地点策展智能体',
    enrich_poi_details: '地点详情智能体',
    build_local_routes: '接驳路线智能体',
    discover_services: '补能服务智能体',
    enrich_deep_drive: '驾驶安全智能体',
    schedule_tourism: '行程编排智能体',
    review_daily_schedule: '每日复核智能体',
    verify_plan: '行程验证智能体',
    render_markdown: '报告整理智能体',
    persist_trip: '报告整理智能体',
  }
  return (event.tool && tools[event.tool]) || (event.node && nodes[event.node]) || '规划智能体'
}

function planningEventLabel(event: { label?: string; tool?: string; node?: string }) {
  const label = String(event.label || '').trim()
  // Older persisted events may contain the raw graph node (for example
  // "render markdown"). Keep those labels readable even when the backend
  // event was emitted before the localized label map was introduced.
  const normalized = label.toLowerCase().replace(/[_-]+/g, ' ')
  if (normalized.includes('render markdown')) return '报告整理智能体正在整理最终行程安排'
  if (normalized.includes('persist trip')) return '报告整理智能体正在保存并核对行程安排'
  if (normalized.includes('build base route')) return '路线智能体已完成跨城主路线'
  if (normalized.includes('discover tourism')) return '地点策展智能体已完成景点、餐饮与住宿候选'
  if (normalized.includes('build local routes')) return '接驳路线智能体正在补齐本地交通'
  if (normalized.includes('review daily schedule')) return '每日复核智能体正在检查全天时间覆盖'
  if (normalized.includes('verify plan')) return '行程验证智能体正在核验时间、闭环与安全'
  if (normalized.includes('agent') && /[a-z]{3,}/i.test(label)) {
    return `${planningAgentName(event)} 正在处理当前步骤`
  }
  return humanizeDisplayText(label) || `${planningAgentName(event)} 正在处理当前步骤`
}

function planningEventKey(event: { event: string; progress: number; node?: string; tool?: string }) {
  return `${event.node || event.event}:${event.progress}:${event.tool || ''}`
}

function formatDuration(minutes: number) {
  const rounded = Math.max(0, Math.round(minutes))
  const hours = Math.floor(rounded / 60)
  const rest = rounded % 60
  if (hours && rest) return `${hours}小时${rest}分钟`
  if (hours) return `${hours}小时`
  return `${rest}分钟`
}

function humanizeDefault(value: string) {
  const match = value.match(/^travelers=(\d+)$/)
  if (match) return `按 ${match[1]} 人规划`
  if (value.includes('=')) return '已应用规划默认值'
  return value
}

function forecastWeatherLabel(value: string | null | undefined) {
  const text = humanizeDisplayText(value) || '出发前更新'
  // Trips saved before the wording update may still contain the old snapshot
  // label. Normalize at render time so history pages do not tell travellers
  // that a past/current observation is the forecast for their travel date.
  return text
    .replace(/当前天气参考/g, '预报天气参考')
    .replace(/当前天气数据暂不可用/g, '预报天气暂不可用')
}

function eventResearchDetails(item: SpecialEventResearch) {
  const facts = item.facts
  const details: string[] = []
  if (facts?.peak_start_date && facts?.peak_end_date && facts.peak_start_date !== facts.peak_end_date) {
    details.push(`极大期：${facts.peak_start_date} 至 ${facts.peak_end_date}`)
  } else if (facts?.peak_start_date) {
    details.push(`极大期：${facts.peak_start_date}`)
  }
  if (facts?.peak_time_local) details.push(`北京时间 ${facts.peak_time_local}`)
  else if (facts?.peak_time_utc) details.push(`UTC ${facts.peak_time_utc}`)
  else if (facts?.peak_time_label) details.push(`来源时间：${facts.peak_time_label}`)
  if (facts?.observation_window_local) details.push(`观测窗口：${facts.observation_window_local}`)
  if (facts?.zhr != null) details.push(`预计 ZHR ${facts.zhr}`)
  return details.join(' · ') || '正在整理来源中的具体时间与观测窗口'
}

async function retryPlanning() {
  planningError.value = ''
  planningRestarting.value = true
  store.setRouteReplanRequired(false)
  store.resetPlanningEvents()
  try {
    store.resetPlanningEvents()
    planningSnapshot.value = await startPlanning(String(route.params.tripId))
    activePlanningBatchId.value = planningSnapshot.value.planning_batch_id || null
    // The SSE stream closes after every terminal planning event. Reconnect so
    // a chat-triggered global replan receives a fresh progressive timeline.
    connect(String(route.params.tripId))
    queuePlanningRefresh(500)
  } catch (error) {
    planningError.value = error instanceof Error ? error.message : '规划任务无法重新启动'
  } finally {
    planningRestarting.value = false
  }
}

function nameClass(value: string) {
  return {
    'text-long': value.length > 4,
    'text-very-long': value.length > 7,
  }
}

function roadNames(stage: NonNullable<typeof store.currentStage>) {
  return [...new Set(stage.route_segments.map((item) => item.road_name).filter(Boolean))].join(' / ') || '以在线地图实时规划为准'
}

function stageConditionLabel(mode: string) {
  return mode === 'walking' || mode === 'riding'
    ? '路线起伏'
    : ['transit', 'train', 'flight', 'ferry'].includes(mode)
      ? '班次'
      : '路况'
}

function stageCondition(stage: NonNullable<typeof store.currentStage>) {
  if (stage.traffic_summary) {
    return stage.traffic_summary.replace('当前路况参考（非未来预测）', '当前路况')
  }
  if (stage.mode === 'walking' || stage.mode === 'riding') {
    return stage.elevation_gain_m != null
      ? `总爬升约 ${Math.round(stage.elevation_gain_m)} m`
      : '高程数据暂不可用'
  }
  if (stage.mode === 'transit') return '按实时公共交通班次规划'
  if (stage.mode === 'train') return '已按实时铁路班次规划'
  if (stage.mode === 'flight') return '已按实时航班规划'
  if (stage.mode === 'ferry') return '已按轮船候选规划（出发前确认班次）'
  return '当前路况：在线地图暂无分段实时数据'
}

function stageServiceText(stage: NonNullable<typeof store.currentStage>) {
  if (stage.mode === 'train' || stage.mode === 'flight' || stage.mode === 'ferry') {
    const label = stage.mode === 'train' ? '车次' : stage.mode === 'flight' ? '航班' : '船班'
    const number = stage.service_number || `${label}号暂未返回`
    const terminals = [stage.departure_terminal, stage.arrival_terminal].filter(Boolean).join(' → ')
    const seat = stage.service_seat_class ? ` · ${stage.service_seat_class}` : ''
    const price = stage.service_price
      ? ` · ¥${stage.service_price.minimum}${stage.service_price.maximum !== stage.service_price.minimum ? `-${stage.service_price.maximum}` : ''}`
      : ''
    return `${label} ${number}${stage.service_operator ? ` · ${stage.service_operator}` : ''}${terminals ? ` · ${terminals}` : ''}${seat}${price}`
  }
  if (stage.mode === 'transit' && stage.transit_legs?.length) {
    return stage.transit_legs
      .map((leg) => `${leg.line_name || (leg.mode === 'subway' ? '地铁' : '公交')} ${leg.departure_stop || '上车'} → ${leg.arrival_stop || '下车'}`)
      .join('；')
  }
  return ''
}

function modeEmoji(mode: string, transitType?: string) {
  if (mode === 'transit') {
    return { bus: '🚌', subway: '🚇', shuttle: '🚐', ferry: '⛴️' }[transitType ?? ''] ?? '🚌'
  }
  return {
    driving: '🚙',
    walking: '🚶',
    riding: '🚲',
    train: '🚄',
    flight: '✈️',
    ferry: '⛴️',
  }[mode] ?? '📍'
}

function startStageDrag(event: PointerEvent) {
  if (!stageTrack.value || event.pointerType === 'touch') return
  stageDrag.active = true
  stageDrag.moved = false
  stageDrag.startX = event.clientX
  stageDrag.startScrollLeft = stageTrack.value.scrollLeft
  stageDrag.pointerId = event.pointerId
  stageTrack.value.classList.add('dragging')
}

function moveStageDrag(event: PointerEvent) {
  if (!stageDrag.active || !stageTrack.value) return
  const delta = event.clientX - stageDrag.startX
  if (Math.abs(delta) > 5 && !stageDrag.moved) {
    stageDrag.moved = true
    stageTrack.value.setPointerCapture(stageDrag.pointerId)
  }
  stageTrack.value.scrollLeft = stageDrag.startScrollLeft - delta
}

function endStageDrag(_event: PointerEvent) {
  if (!stageDrag.active || !stageTrack.value) return
  stageDrag.active = false
  stageTrack.value.classList.remove('dragging')
  if (stageTrack.value.hasPointerCapture(stageDrag.pointerId)) stageTrack.value.releasePointerCapture(stageDrag.pointerId)
}

function selectStageFromCard(item: (typeof allStages.value)[number]) {
  if (stageDrag.moved) {
    stageDrag.moved = false
    return
  }
  selectJourneyStage(item)
}

onMounted(load)
onUnmounted(() => {
  if (pollingTimer) window.clearTimeout(pollingTimer)
  pollingTimer = undefined
  refreshQueued = false
})
watch(
  () => store.currentStageId,
  () => nextTick(() => centerCurrentStage()),
)
watch(leftPanelCollapsed, (collapsed) => window.localStorage.setItem(panelStorageKeys.left, collapsed ? '1' : '0'))
watch(rightPanelCollapsed, (collapsed) => window.localStorage.setItem(panelStorageKeys.right, collapsed ? '1' : '0'))
watch(bottomPanelCollapsed, (collapsed) => window.localStorage.setItem(panelStorageKeys.bottom, collapsed ? '1' : '0'))
</script>

<template>
  <main class="plan-shell">
    <Transition name="planning-overlay">
      <div v-if="planningBusy" class="planning-overlay" role="status" aria-live="polite">
        <section class="planning-wait-dialog glass-card">
          <div class="planning-map-animation" :data-scene="planningScene.key" aria-hidden="true">
            <div class="planning-focus-visual">
              <div class="planning-focus-panel focus-radar-panel">
                <span class="focus-radar-ring ring-outer" />
                <span class="focus-radar-ring ring-middle" />
                <span class="focus-radar-ring ring-inner" />
                <span class="focus-radar-sweep" />
                <span class="focus-radar-center" />
                <i class="focus-radar-ping ping-one" />
                <i class="focus-radar-ping ping-two" />
                <i class="focus-radar-ping ping-three" />
                <b class="focus-panel-label">正在扫描旅行约束</b>
                <small>同行人 · 时间 · 偏好 · 必去地点</small>
              </div>
              <div class="planning-focus-panel focus-search-panel">
                <span class="focus-search-crosshair"><i /><b /></span>
                <span class="focus-search-scanline" />
                <svg class="focus-search-route" viewBox="0 0 520 230" preserveAspectRatio="none">
                  <path d="M42 174 C126 154 146 74 248 91 S360 168 470 48" />
                  <circle cx="42" cy="174" r="5" />
                  <circle cx="248" cy="91" r="5" />
                  <circle cx="470" cy="48" r="5" />
                </svg>
                <span class="focus-search-tag tag-origin">出发地</span>
                <span class="focus-search-tag tag-destination">目的地</span>
                <span class="focus-search-tag tag-transfer">接驳点</span>
                <b class="focus-panel-label">正在搜索地点与交通</b>
                <small>地图地点 · 车站机场 · 沿途候选</small>
              </div>
              <div class="planning-focus-panel focus-candidates-panel">
                <span class="focus-candidate-dot candidate-one"><i /></span>
                <span class="focus-candidate-dot candidate-two"><i /></span>
                <span class="focus-candidate-dot candidate-three"><i /></span>
                <span class="focus-candidate-card candidate-card-one"><b>景</b><i>自然风景</i></span>
                <span class="focus-candidate-card candidate-card-two"><b>餐</b><i>附近餐饮</i></span>
                <span class="focus-candidate-card candidate-card-three"><b>住</b><i>舒适住宿</i></span>
                <div class="focus-candidate-stream">
                  <i v-for="index in 6" :key="`candidate-stream-${index}`">
                    <b>{{ index }}</b><em />
                  </i>
                </div>
                <span class="focus-candidate-verify"><i>✓</i><b>逐项核对中</b></span>
                <b class="focus-panel-label">正在筛选目的地候选</b>
                <small>开放时间 · 预约 · 季节适配</small>
              </div>
              <div class="planning-focus-panel focus-agents-panel">
                <span class="focus-agent-link link-route" />
                <span class="focus-agent-link link-place" />
                <span class="focus-agent-link link-weather" />
                <span class="focus-agent-node agent-route"><i>路</i><b>路线智能体</b></span>
                <span class="focus-agent-node agent-place"><i>点</i><b>地点智能体</b></span>
                <span class="focus-agent-node agent-weather"><i>天</i><b>天气智能体</b></span>
                <span class="focus-agent-hub"><i>AI</i><b>协作中</b></span>
                <b class="focus-panel-label">多个智能体正在协作</b>
                <small>共享候选 · 相互校验 · 汇合成路线</small>
              </div>
            </div>
            <span class="planning-map-grid" />
            <span v-for="index in 5" :key="`road-${index}`" :class="`planning-ambient-road road-${index}`" />
            <svg viewBox="0 0 520 230" preserveAspectRatio="none">
              <path class="planning-map-route route-shadow" d="M31 183 C90 180 112 84 181 105 S280 190 355 119 S442 39 493 63" />
              <path id="planning-route-path" class="planning-map-route route-main" d="M31 183 C90 180 112 84 181 105 S280 190 355 119 S442 39 493 63" />
              <path class="planning-map-route route-shadow route-shadow-branch" d="M181 105 C213 49 273 45 315 79 C333 91 346 106 355 119" />
              <path class="planning-map-route route-shadow route-shadow-return" d="M355 119 C319 161 249 191 188 171 C132 160 78 171 31 183" />
              <path class="planning-map-route route-glow" d="M31 183 C90 180 112 84 181 105 S280 190 355 119 S442 39 493 63" />
              <path class="planning-map-route route-glow route-glow-branch" d="M181 105 C213 49 273 45 315 79 C333 91 346 106 355 119" />
              <path class="planning-map-route route-glow route-glow-return" d="M355 119 C319 161 249 191 188 171 C132 160 78 171 31 183" />
              <path class="planning-map-route route-branch" d="M181 105 C213 49 273 45 315 79 C333 91 346 106 355 119" />
              <path class="planning-map-route route-return" d="M355 119 C319 161 249 191 188 171 C132 160 78 171 31 183" />
              <circle class="planning-map-flow-dot flow-dot-one" r="4">
                <animateMotion dur="12s" begin="-2s" repeatCount="indefinite" rotate="auto" path="M31 183 C90 180 112 84 181 105 S280 190 355 119 S442 39 493 63" />
              </circle>
              <circle class="planning-map-flow-dot flow-dot-two" r="3">
                <animateMotion dur="12s" begin="-7s" repeatCount="indefinite" rotate="auto" path="M31 183 C90 180 112 84 181 105 S280 190 355 119 S442 39 493 63" />
              </circle>
              <circle class="planning-map-flow-dot flow-dot-branch" r="3.5">
                <animateMotion dur="9s" begin="-4s" repeatCount="indefinite" rotate="auto" path="M181 105 C213 49 273 45 315 79 C333 91 346 106 355 119" />
              </circle>
              <circle class="planning-map-flow-dot flow-dot-return" r="3">
                <animateMotion dur="10s" begin="-6s" repeatCount="indefinite" rotate="auto" path="M355 119 C319 161 249 191 188 171 C132 160 78 171 31 183" />
              </circle>
            </svg>
            <span class="planning-map-route-pulse pulse-main"><i /></span>
            <span class="planning-map-route-pulse pulse-branch"><i /></span>
            <span class="planning-map-route-pulse pulse-return"><i /></span>
            <span v-for="index in 3" :key="`spark-main-${index}`" :class="`planning-map-spark spark-main-${index}`"><i /></span>
            <span v-for="index in 2" :key="`spark-branch-${index}`" :class="`planning-map-spark spark-branch-${index}`"><i /></span>
            <span v-for="index in 3" :key="`bead-main-${index}`" :class="`planning-map-bead bead-main-${index}`"><i /></span>
            <span v-for="index in 2" :key="`bead-return-${index}`" :class="`planning-map-bead bead-return-${index}`"><i /></span>
            <span class="planning-map-sheen" />
            <span v-for="index in 6" :key="index" :class="`planning-map-pin pin-${index}`"><i /></span>
            <span class="planning-map-origin">起</span>
            <span class="planning-map-destination">终</span>
            <span class="planning-map-traveller"><i /><b /></span>
            <span class="planning-map-scan" />
            <span class="planning-map-radar"><i /><b /></span>
            <span class="planning-map-orbit"><i /><b /><em /></span>
            <span class="planning-map-cloud cloud-one" /><span class="planning-map-cloud cloud-two" />
            <span class="planning-map-card card-hotel"><b>住</b><i>住宿已匹配</i></span>
            <span class="planning-map-card card-meal"><b>餐</b><i>餐饮已匹配</i></span>
            <span class="planning-map-card card-weather"><b>晴</b><i>天气已复核</i></span>
            <span class="planning-itinerary-stack">
              <i v-for="index in 4" :key="`step-${index}`"><b>{{ index }}</b><em /></i>
            </span>
            <span class="planning-check-badge"><i>✓</i><b>路线连续</b></span>
            <span class="planning-stage-beacons"><i /><i /><i /></span>
          </div>
          <div class="planning-scene-copy">
            <span>{{ planningScene.index }}/{{ planningScenes.length }} · {{ planningAgentName(store.planningEvent || {}) }}</span>
            <strong>{{ planningRestarting ? '正在重新规划整段行程' : planningScene.title }}</strong>
            <small>{{ planningScene.caption }}</small>
          </div>
          <div class="planning-overlay-meter" aria-hidden="true"><i :style="{ width: `${planningProgress}%` }" /></div>
          <b>{{ planningProgress }}%</b>
        </section>
      </div>
    </Transition>
    <header class="plan-top">
      <button class="ghost-button" @click="router.push('/home')"><ArrowLeft />返回主页</button>
      <div>
        <span class="eyebrow">ROADMAN · 智能行程</span>
        <h1>{{ store.trip?.title || '自驾游深度规划页' }}</h1>
      </div>
      <div class="top-actions">
        <button class="ghost-button" @click="saveVersion"><Share2 />保存版本</button>
        <template v-if="planningComplete">
          <button class="ghost-button" @click="exportMarkdown"><Download />导出 Markdown</button>
          <button class="ghost-button export-small" @click="exportSnapshot('pdf')">PDF</button>
          <button class="ghost-button export-small" @click="exportSnapshot('pptx')">PPT</button>
          <button class="ghost-button export-small" @click="exportSnapshot('png')">长图</button>
          <button class="ghost-button export-small" @click="exportSnapshot('html')">HTML</button>
        </template>
        <button class="ghost-button export-small" @click="openAttachmentPicker"><Paperclip />附件</button>
        <input ref="attachmentInput" class="sr-only" type="file" accept=".png,.jpg,.jpeg,.webp,.pdf,.docx,.md,.xlsx" @change="onAttachmentSelected">
      </div>
    </header>
    <div v-if="versionMessage" class="degraded-banner">{{ versionMessage }}</div>
    <section v-if="attachmentPreview || attachmentMessage" class="attachment-preview glass-card">
      <div class="attachment-preview-heading"><strong>附件解析预览</strong><span>{{ attachmentMessage }}</span></div>
      <div v-if="attachmentPreview" class="attachment-place-list">
        <label v-for="place in attachmentPreview.places" :key="place">
          <input v-model="attachmentSelection" type="checkbox" :value="place">{{ place }}
        </label>
        <p v-if="attachmentPreview.warnings.length">{{ attachmentPreview.warnings.join('；') }}</p>
        <button class="primary-button" @click="confirmAttachment">确认选中地点</button>
      </div>
    </section>

    <section v-if="planningSnapshot?.special_event_research?.length" class="special-event-research glass-card">
      <div class="special-event-heading">
        <div><span class="eyebrow">活动核验智能体</span><strong>已核对的特殊活动</strong></div>
        <small>事实来自公开来源，日期未确定时不会擅自代填</small>
      </div>
      <article v-for="item in planningSnapshot.special_event_research" :key="item.event" class="special-event-item">
        <div>
          <strong>{{ item.event }}</strong>
          <span>{{ eventResearchDetails(item) }}</span>
          <p v-if="item.facts?.summary">{{ item.facts.summary }}</p>
          <p v-else-if="item.status !== 'researched'">暂未取得足够来源，出发前需要重新查询。</p>
        </div>
        <nav v-if="item.sources?.length" aria-label="活动来源">
          <a v-for="(source, index) in item.sources.slice(0, 3)" :key="source.url || index" :href="source.url" target="_blank" rel="noreferrer">{{ source.title || `来源 ${index + 1}` }}</a>
        </nav>
      </article>
    </section>

    <div v-if="loading" class="page-state">正在加载武汉—庐山行程…</div>
    <section v-else-if="planningSnapshot && !store.currentDay" class="planning-live-layout">
      <section class="planning-state glass-card">
      <span class="eyebrow">RoadMan 智能体正在协作</span>
      <h2>{{ planningSnapshot.status === 'failed' ? '这次行程需要调整后再规划' : planningSnapshot.clarification_question || '正在把行程一项一项加入详情页' }}</h2>
      <TransitionGroup v-if="planningSnapshot.status !== 'failed' && visiblePlanningEvents.length" name="planning-event" tag="div" class="planning-event-list">
        <article v-for="(event, index) in visiblePlanningEvents" :key="planningEventKey(event)">
          <i :class="{ active: index === visiblePlanningEvents.length - 1 }" />
          <div><strong>{{ planningEventLabel(event) }}</strong><span>{{ event.progress }}% · {{ planningAgentName(event) }}</span></div>
        </article>
      </TransitionGroup>
      <div v-else-if="planningSnapshot.status !== 'failed'" class="planning-event-list">
        <article>
          <i class="active" /><div><strong>正在建立行程上下文</strong><span>需求理解智能体</span></div>
        </article>
      </div>
      <div v-if="planningSnapshot.defaults_applied.length" class="visible-defaults">
        <strong>已采用的可见默认值</strong>
        <span v-for="item in planningSnapshot.defaults_applied" :key="item">{{ humanizeDefault(item) }}</span>
      </div>
      <div v-if="planningSnapshot.status === 'failed'" class="planning-recovery">
        <p class="planning-error-detail">{{ planningFailure?.description || '行程仍有未解决的问题。' }}</p>
        <ul v-if="planningFailure?.details.length" class="planning-failure-details">
          <li v-for="detail in planningFailure.details" :key="detail">{{ detail }}</li>
        </ul>
        <div class="preflight-actions">
          <button class="secondary-button" @click="router.push('/home')">返回修改需求</button>
          <button class="primary-button" @click="retryPlanning">重新规划</button>
        </div>
      </div>
      <form
        v-if="planningSnapshot.status === 'clarification_required'"
        class="clarification-form"
        @submit.prevent="submitClarification"
      >
        <input v-model="clarificationAnswer" autofocus placeholder="输入补充信息…" />
        <button class="primary-button">继续规划</button>
      </form>
      <p v-if="planningError && planningSnapshot.status !== 'failed'" class="planning-error">{{ planningError }}</p>
      </section>
      <AgentPanel @replan-requested="retryPlanning" />
    </section>
    <template v-else-if="store.trip && store.currentDay">
      <div v-if="degraded" class="degraded-banner">后端暂不可用，正在加载本地行程数据。</div>
      <section class="safety-boundary-banner" aria-label="安全与降级状态">
        <div><strong>规划辅助，不连接或控制车辆</strong><small>实时导航、道路公告、运营方信息与驾驶员判断具有最终优先级</small></div>
        <span v-for="notice in degradationNotices" :key="notice">{{ notice }}</span>
      </section>
      <section v-if="!planningComplete" class="planning-live-strip glass-card">
        <span class="planning-pulse" />
        <div class="planning-live-copy">
          <strong>{{ humanizeDisplayText(store.planningEvent?.label) || '智能体正在继续完善行程' }}</strong>
          <small>地图、阶段、景点、用餐、住宿和补能安排会继续逐项出现</small>
          <div
            class="planning-meter"
            role="progressbar"
            aria-label="规划进度"
            :aria-valuenow="planningProgress"
            aria-valuemin="0"
            aria-valuemax="100"
          ><i :style="{ width: `${planningProgress}%` }" /></div>
        </div>
        <b>{{ planningProgress }}%</b>
      </section>
      <section :class="['plan-grid', { 'left-panel-collapsed': leftPanelCollapsed, 'right-panel-collapsed': rightPanelCollapsed }]">
        <aside v-show="!leftPanelCollapsed" class="trip-sidebar glass-card">
          <select :value="store.currentDayIndex" @change="store.setDay(Number(($event.target as HTMLSelectElement).value))">
            <option v-for="(day, index) in store.trip.days" :key="day.id" :value="index">
              第 {{ day.day_index }} 天 · {{ day.date.slice(5) }}
            </option>
          </select>
          <div class="day-summary">
            <strong>{{ store.currentDay.title }}</strong>
            <span>{{ store.currentDay.total_distance_km }} km · {{ formatDuration(store.currentDay.total_drive_minutes) }}</span>
            <span>{{ forecastWeatherLabel(store.currentDay.weather_summary) }}</span>
          </div>
          <section class="day-timeline" aria-label="全天时间线">
            <header><strong>全天安排</strong><span>{{ dayTimeline.length }} 项</span></header>
            <div v-if="dayTimeline.length" class="day-timeline-list">
              <button v-for="item in dayTimeline" :key="item.id" type="button" @click="item.kind === 'stage' ? selectStageById(item.id) : selectActivityById(item.id)">
                <time>{{ item.start.slice(11, 16) }}</time>
                <i>{{ item.icon }}</i>
                <span><b>{{ item.label }}</b><small>{{ item.title }}</small></span>
              </button>
            </div>
            <p v-else>智能体正在补齐当天安排…</p>
          </section>
          <div class="category-tabs">
            <button
              v-for="item in categories"
              :key="item"
              :class="{ active: store.category === item }"
              @click="store.category = item"
            >{{ item }}</button>
          </div>
          <div class="list-label"><strong>已加入行程</strong><span>{{ filteredActivities.length }} 项</span></div>
          <ActivityList
            :activities="filteredActivities"
            :selected-id="store.selectedNodeId"
            @select="store.selectActivity"
            @remove="requestActivityRemoval"
          />
        </aside>

        <section :class="['map-workspace', { 'bottom-panel-collapsed': bottomPanelCollapsed }]">
          <button
            type="button"
            class="panel-collapse-toggle panel-collapse-left"
            :aria-label="leftPanelCollapsed ? '展开左侧行程信息' : '收起左侧行程信息'"
            :aria-expanded="!leftPanelCollapsed"
            :title="leftPanelCollapsed ? '展开左侧行程信息' : '收起左侧行程信息'"
            @click="leftPanelCollapsed = !leftPanelCollapsed"
          ><ChevronRight v-if="leftPanelCollapsed" /><ChevronLeft v-else /></button>
          <button
            type="button"
            class="panel-collapse-toggle panel-collapse-right"
            :aria-label="rightPanelCollapsed ? '展开右侧行程助理' : '收起右侧行程助理'"
            :aria-expanded="!rightPanelCollapsed"
            :title="rightPanelCollapsed ? '展开右侧行程助理' : '收起右侧行程助理'"
            @click="rightPanelCollapsed = !rightPanelCollapsed"
          ><ChevronLeft v-if="rightPanelCollapsed" /><ChevronRight v-else /></button>
          <div class="map-pick-toolbar glass-card">
            <div class="map-pick-heading"><strong>地图加点</strong><small>先选类型，再点地图位置</small></div>
            <select v-model="mapPickCategory" aria-label="地图选点类型">
              <option value="attractions">景点</option>
              <option value="hotels">住宿</option>
              <option value="meals">餐饮</option>
            </select>
            <div class="map-pick-categories" role="tablist" aria-label="地图加点类型">
              <button type="button" role="tab" :class="{ selected: mapPickCategory === 'attractions' }" :aria-selected="mapPickCategory === 'attractions'" @click="mapPickCategory = 'attractions'">景点</button>
              <button type="button" role="tab" :class="{ selected: mapPickCategory === 'hotels' }" :aria-selected="mapPickCategory === 'hotels'" @click="mapPickCategory = 'hotels'">住宿</button>
              <button type="button" role="tab" :class="{ selected: mapPickCategory === 'meals' }" :aria-selected="mapPickCategory === 'meals'" @click="mapPickCategory = 'meals'">餐饮</button>
            </div>
            <button type="button" :class="{ active: mapPickMode }" @click="mapPickMode = !mapPickMode">
              <Crosshair />{{ mapPickMode ? '请点击地图位置' : '地图点选' }}
            </button>
            <span v-if="mapPickMessage">{{ mapPickMessage }}</span>
          </div>
          <AmapRouteMap
            :day="store.currentDay"
            :active-stage-id="store.currentStageId"
            :pick-enabled="mapPickMode"
            @select-stage="selectStageById"
            @select-activity="selectActivityById"
            @point-selected="handleMapPoint"
          />
          <div class="stage-nav-shell">
            <button
              type="button"
              class="bottom-panel-toggle"
              :aria-label="bottomPanelCollapsed ? '展开阶段详情' : '收起阶段详情'"
              :aria-expanded="!bottomPanelCollapsed"
              :title="bottomPanelCollapsed ? '展开阶段详情' : '收起阶段详情'"
              @click="bottomPanelCollapsed = !bottomPanelCollapsed"
            ><ChevronUp v-if="bottomPanelCollapsed" /><ChevronDown v-else /></button>
            <div v-show="!bottomPanelCollapsed" class="stage-nav glass-card">
            <button
              class="stage-arrow"
              aria-label="上一个阶段"
              :disabled="currentGlobalStageIndex <= 0"
              @click="moveStage(-1)"
            ><ChevronLeft /></button>
            <div
              ref="stageTrack"
              class="stage-track"
              @pointerdown="startStageDrag"
              @pointermove="moveStageDrag"
              @pointerup="endStageDrag"
              @pointercancel="endStageDrag"
            >
              <button
                v-for="item in allStages"
                :key="item.stage.id"
                :data-stage-id="item.stage.id"
                :class="['stage-card', { active: item.stage.id === store.currentStageId }]"
                :style="{ '--item-index': item.stage.sequence }"
                @click="selectStageFromCard(item)"
              >
                <header>
                  <span :class="nameClass(item.stage.title)" :title="item.stage.title">{{ item.stage.title }}</span>
                  <b>第 {{ item.dayIndexLabel }} 天</b>
                </header>
                <div
                  v-if="item.stage.risk_tags?.length"
                  :class="['stage-risk-tags', item.stage.risk_level]"
                >
                  <span v-for="tag in item.stage.risk_tags.slice(0, 3)" :key="tag">{{ tag }}</span>
                </div>
                <div class="stage-route">
                  <div class="stage-stop">
                    <small>起点</small>
                    <strong :class="nameClass(item.stage.origin.name)" :title="item.stage.origin.name">{{ item.stage.origin.name }}</strong>
                  </div>
                  <i class="stage-journey-flow" aria-hidden="true">
                    <span>{{ modeEmoji(item.stage.mode, item.stage.transit_type) }}</span>
                    <b></b>
                  </i>
                  <div class="stage-stop stage-stop-end">
                    <small>终点</small>
                    <strong :class="nameClass(item.stage.destination.name)" :title="item.stage.destination.name">{{ item.stage.destination.name }}</strong>
                  </div>
                </div>
                <div class="stage-time">
                  <span><b>{{ formatTime(item.stage.planned_start) }}</b> 预计出发</span>
                  <span><b>{{ formatTime(item.stage.planned_end) }}</b> 预计抵达</span>
                </div>
                <p v-if="stageServiceText(item.stage)" class="stage-service-detail">
                  {{ stageServiceText(item.stage) }}
                  <a v-if="item.stage.service_detail_url" :href="item.stage.service_detail_url" target="_blank" rel="noreferrer" @click.stop>查看班次</a>
                </p>
                <dl>
                  <div><dt>道路</dt><dd>{{ roadNames(item.stage) }}</dd></div>
                  <div><dt>{{ stageConditionLabel(item.stage.mode) }}</dt><dd>{{ stageCondition(item.stage) }}</dd></div>
                  <div><dt>天气</dt><dd>{{ forecastWeatherLabel(item.stage.weather_summary || store.trip?.days[item.dayIndex]?.weather_summary) }}</dd></div>
                </dl>
                <footer>
                  <span>{{ item.stage.distance_km }} km</span>
                  <span>{{ formatDuration(item.stage.duration_minutes) }}</span>
                  <span v-if="item.stage.toll_fee">路费约 ¥{{ item.stage.toll_fee.minimum }}–{{ item.stage.toll_fee.maximum }}</span>
                  <span v-if="item.stage.energy_estimate">预计消耗 {{ item.stage.energy_estimate.amount }} {{ item.stage.energy_estimate.unit }}</span>
                  <span v-if="item.stage.energy_estimate?.replenished_amount">补能 {{ item.stage.energy_estimate.replenished_amount }} {{ item.stage.energy_estimate.replenished_unit }}</span>
                  <span v-if="item.stage.energy_estimate?.remaining_percent !== undefined">预计剩余 {{ item.stage.energy_estimate.remaining_percent }}%</span>
                </footer>
                 <small v-if="item.stage.warnings[0]">⚠ {{ forecastWeatherLabel(item.stage.warnings[0].message) }}</small>
              </button>
            </div>
            <button
              class="stage-arrow"
              aria-label="下一个阶段"
              :disabled="currentGlobalStageIndex >= allStages.length - 1"
              @click="moveStage(1)"
            ><ChevronRight /></button>
            </div>
            <nav v-show="bottomPanelCollapsed" class="compact-stage-nav glass-card" aria-label="简化阶段切换">
              <button
                type="button"
                aria-label="上一个阶段"
                :disabled="currentGlobalStageIndex <= 0"
                @click="moveStage(-1)"
              ><ChevronLeft /></button>
              <strong>第 {{ currentStagePosition }} / {{ allStages.length }} 段</strong>
              <button
                type="button"
                aria-label="下一个阶段"
                :disabled="currentGlobalStageIndex >= allStages.length - 1"
                @click="moveStage(1)"
              ><ChevronRight /></button>
            </nav>
          </div>
        </section>

        <AgentPanel v-show="!rightPanelCollapsed" @replan-requested="retryPlanning" />
      </section>
      <details v-if="riskStages.length" class="roadbook-card risk-card glass-card">
        <summary>
          风险与自驾校验
          <b>{{ riskStages.filter((item) => item.stage.risk_level === 'high').length }} 项高风险</b>
        </summary>
        <div class="risk-grid">
          <article
            v-for="item in riskStages"
            :key="item.stage.id"
            :class="['risk-item', item.stage.risk_level]"
          >
            <header><strong>第 {{ item.dayIndexLabel }} 天 · {{ item.stage.title }}</strong></header>
            <p>{{ item.stage.origin.name }} → {{ item.stage.destination.name }}</p>
            <div>
              <span v-for="tag in item.stage.risk_tags" :key="tag">{{ tag }}</span>
            </div>
            <small v-for="warning in item.stage.warnings" :key="`${warning.code}-${warning.message}`">
              {{ warning.message }}{{ warning.estimated ? '（估算）' : '' }}
            </small>
          </article>
        </div>
      </details>
    </template>
    <div v-else class="page-state error">{{ planningError || '行程加载失败，请稍后重试。' }}</div>
    <Transition name="failure-modal">
      <div v-if="planningFailure" class="failure-dialog-backdrop" role="presentation">
        <section class="failure-dialog glass-card" role="alertdialog" aria-modal="true" aria-labelledby="planning-failure-title">
          <div class="failure-dialog-icon" aria-hidden="true">!</div>
          <div>
            <span class="failure-dialog-kicker">规划校验未通过</span>
            <h2 id="planning-failure-title">这次安排需要调整</h2>
            <p>{{ planningFailure.description }}</p>
            <ul class="planning-failure-details">
              <li v-for="detail in planningFailure.details" :key="detail">{{ detail }}</li>
            </ul>
            <div class="failure-dialog-hint">{{ planningFailure.hint }}</div>
          </div>
          <div class="failure-dialog-actions">
            <button class="secondary-button" @click="router.push('/home')">返回修改需求</button>
            <button class="primary-button" @click="retryPlanning">重新规划</button>
          </div>
        </section>
      </div>
    </Transition>
  </main>
</template>
