<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ChevronLeft, ChevronRight, Crosshair, Download, Paperclip, Share2 } from '@lucide/vue'
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
  type PlanningSnapshot,
} from '../api/trips'
import { useTripStore } from '../stores/trip'
import { useTripSSE } from '../composables/useTripSSE'
import AmapRouteMap from '../components/map/AmapRouteMap.vue'
import ActivityList from '../components/trip/ActivityList.vue'
import AgentPanel from '../components/agent/AgentPanel.vue'
import mockTrip from '../../../shared/examples/wuhan-lushan-trip.json'

const router = useRouter()
const route = useRoute()
const store = useTripStore()
const loading = ref(true)
const degraded = ref(false)
const stageTrack = ref<HTMLElement | null>(null)
const planningSnapshot = ref<PlanningSnapshot | null>(null)
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
let pollingTimer: number | undefined
const stageDrag = { active: false, moved: false, startX: 0, startScrollLeft: 0, pointerId: -1 }
const categories = ['景点', '住宿', '餐饮', '服务'] as const
const { connect } = useTripSSE((event) => {
  store.addPlanningEvent(event)
  if (
    event.event === 'plan_updated'
    || event.event === 'planning_completed'
    || event.event === 'clarification_required'
    || event.event === 'planning_failed'
  ) {
    void refreshPlanning()
  }
})

const planningComplete = computed(() =>
  planningSnapshot.value?.status === 'completed' || store.trip?.status === 'completed',
)

const filteredActivities = computed(() => {
  const typeMap: Record<string, string[]> = {
    景点: ['attraction'],
    住宿: ['hotel'],
    餐饮: ['meal'],
    服务: ['rest', 'charging', 'fueling', 'parking', 'service'],
  }
  return (store.currentDay?.activities ?? []).filter((item) => typeMap[store.category].includes(item.type))
})
const allStages = computed(() =>
  (store.trip?.days ?? []).flatMap((day, dayIndex) =>
    day.stages.map((stage) => ({ stage, dayIndex, dayIndexLabel: day.day_index })),
  ),
)
const currentGlobalStageIndex = computed(() =>
  allStages.value.findIndex((item) => item.stage.id === store.currentStageId),
)
const riskStages = computed(() =>
  allStages.value.filter(({ stage }) =>
    stage.risk_level === 'high'
    || stage.risk_level === 'moderate'
    || stage.warnings.length > 0,
  ),
)

async function load() {
  const tripId = String(route.params.tripId)
  store.resetPlanningEvents()
  try {
    if (tripId === 'trip_wuhan_lushan_demo') {
      store.trip = await fetchMockTrip()
    } else {
      store.trip = await fetchTrip(tripId)
      connect(tripId)
      await refreshPlanning()
    }
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
  if (pollingTimer) window.clearTimeout(pollingTimer)
  try {
    const snapshot = await fetchPlanning(tripId)
    planningSnapshot.value = snapshot
    if (snapshot.status === 'completed') {
      store.trip = await fetchTrip(tripId)
      if (!store.currentDay) store.setDay(0)
      await nextTick()
      centerCurrentStage('auto')
      return
    }
    if (snapshot.status === 'failed') {
      planningError.value = snapshot.verification_result?.issues?.[0]?.description || '规划失败，请修改需求后重试。'
      return
    }
    if (snapshot.status !== 'clarification_required') {
      const partialTrip = await fetchTrip(tripId)
      const hadNoDay = !store.currentDay
      store.trip = partialTrip
      if (hadNoDay && partialTrip.days.length) store.setDay(0)
      pollingTimer = window.setTimeout(() => void refreshPlanning(), 900)
    }
  } catch (error) {
    planningError.value = error instanceof Error ? error.message : '无法读取规划进度'
  }
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
    mapPickMessage.value = `已选中“${point.name}”，请在右侧 Agent 面板确认是否加入行程。`
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
    pollingTimer = window.setTimeout(() => void refreshPlanning(), 500)
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

function exportSnapshot(format: 'pdf' | 'pptx' | 'png') {
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
  }).format(new Date(value))
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

async function retryPlanning() {
  planningError.value = ''
  try {
    planningSnapshot.value = await startPlanning(String(route.params.tripId))
    pollingTimer = window.setTimeout(() => void refreshPlanning(), 500)
  } catch (error) {
    planningError.value = error instanceof Error ? error.message : '规划任务无法重新启动'
  }
}

function nameClass(value: string) {
  return {
    'text-long': value.length > 4,
    'text-very-long': value.length > 7,
  }
}

function roadNames(stage: NonNullable<typeof store.currentStage>) {
  return [...new Set(stage.route_segments.map((item) => item.road_name).filter(Boolean))].join(' / ') || '以高德实时规划为准'
}

function modeEmoji(mode: string, transitType?: string) {
  if (mode === 'transit') {
    return { bus: '🚌', subway: '🚇', shuttle: '🚐' }[transitType ?? ''] ?? '🚌'
  }
  return {
    driving: '🚙',
    walking: '🚶',
    riding: '🚲',
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
})
watch(
  () => store.currentStageId,
  () => nextTick(() => centerCurrentStage()),
)
</script>

<template>
  <main class="plan-shell">
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

    <div v-if="loading" class="page-state">正在加载武汉—庐山行程…</div>
    <section v-else-if="planningSnapshot && !store.currentDay" class="planning-live-layout">
      <section class="planning-state glass-card">
      <span class="eyebrow">ROADMAN AGENTS 正在协作</span>
      <h2>{{ planningSnapshot.status === 'failed' ? '这次行程需要调整后再规划' : planningSnapshot.clarification_question || '正在把行程一项一项加入详情页' }}</h2>
      <div v-if="planningSnapshot.status !== 'failed'" class="planning-event-list">
        <article v-for="(event, index) in store.planningEvents.slice(-7)" :key="`${event.event}-${index}`">
          <i :class="{ active: index === store.planningEvents.slice(-7).length - 1 }" />
          <div><strong>{{ event.label }}</strong><span>{{ event.progress }}% · {{ event.tool || event.node || '规划 Agent' }}</span></div>
        </article>
        <article v-if="!store.planningEvents.length">
          <i class="active" /><div><strong>正在建立行程上下文</strong><span>Requirement Agent</span></div>
        </article>
      </div>
      <div v-if="planningSnapshot.defaults_applied.length" class="visible-defaults">
        <strong>已采用的可见默认值</strong>
        <span v-for="item in planningSnapshot.defaults_applied" :key="item">{{ humanizeDefault(item) }}</span>
      </div>
      <div v-if="planningSnapshot.status === 'failed'" class="planning-recovery">
        <p class="planning-error-detail">
          {{ planningSnapshot.verification_result?.issues?.[0]?.description || '请调整时间、交通方式或停留安排后重新规划。' }}
        </p>
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
      <AgentPanel />
    </section>
    <template v-else-if="store.trip && store.currentDay">
      <div v-if="degraded" class="degraded-banner">后端暂不可用，正在加载本地行程数据。</div>
      <section v-if="!planningComplete" class="planning-live-strip glass-card">
        <span class="planning-pulse" />
        <div>
          <strong>{{ store.planningEvent?.label || 'Agent 正在继续完善行程' }}</strong>
          <small>地图、阶段、景点、用餐、住宿和补能安排会继续逐项出现</small>
        </div>
        <b>{{ store.planningEvent?.progress || planningSnapshot?.progress.value || 1 }}%</b>
      </section>
      <section class="plan-grid">
        <aside class="trip-sidebar glass-card">
          <select :value="store.currentDayIndex" @change="store.setDay(Number(($event.target as HTMLSelectElement).value))">
            <option v-for="(day, index) in store.trip.days" :key="day.id" :value="index">
              第 {{ day.day_index }} 天 · {{ day.date.slice(5) }}
            </option>
          </select>
          <div class="day-summary">
            <strong>{{ store.currentDay.title }}</strong>
            <span>{{ store.currentDay.total_distance_km }} km · {{ formatDuration(store.currentDay.total_drive_minutes) }}</span>
            <span>{{ store.currentDay.weather_summary }}</span>
          </div>
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

        <section class="map-workspace">
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
              <Crosshair />{{ mapPickMode ? '请点击地图位置' : '地图选点加入' }}
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
          <div class="stage-nav glass-card">
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
                <dl>
                  <div><dt>道路</dt><dd>{{ roadNames(item.stage) }}</dd></div>
                  <div><dt>路况</dt><dd>{{ item.stage.traffic_summary || '以高德实时路况为准' }}</dd></div>
                  <div><dt>天气</dt><dd>{{ item.stage.weather_summary || store.trip?.days[item.dayIndex]?.weather_summary || '出发前更新' }}</dd></div>
                </dl>
                <footer>
                  <span>{{ item.stage.distance_km }} km</span>
                  <span>{{ formatDuration(item.stage.duration_minutes) }}</span>
                  <span v-if="item.stage.toll_fee">路费约 ¥{{ item.stage.toll_fee.minimum }}–{{ item.stage.toll_fee.maximum }}</span>
                  <span v-if="item.stage.energy_estimate">预计 {{ item.stage.energy_estimate.amount }} {{ item.stage.energy_estimate.unit }}</span>
                </footer>
                <small v-if="item.stage.warnings[0]">⚠ {{ item.stage.warnings[0].message }}</small>
              </button>
            </div>
            <button
              class="stage-arrow"
              aria-label="下一个阶段"
              :disabled="currentGlobalStageIndex >= allStages.length - 1"
              @click="moveStage(1)"
            ><ChevronRight /></button>
          </div>
        </section>

        <AgentPanel />
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
      <details v-if="planningSnapshot?.plan_markdown" class="roadbook-card glass-card">
        <summary>查看 Markdown 行程安排</summary>
        <pre>{{ planningSnapshot.plan_markdown }}</pre>
      </details>
    </template>
    <div v-else class="page-state error">{{ planningError || '行程加载失败，请稍后重试。' }}</div>
  </main>
</template>
