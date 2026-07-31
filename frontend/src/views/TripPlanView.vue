<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ChevronLeft, ChevronRight, Download, Share2 } from '@lucide/vue'
import {
  answerClarification,
  createTripVersion,
  downloadTripMarkdown,
  fetchMockTrip,
  fetchPlanning,
  fetchTrip,
  previewDeletePatch,
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
let pollingTimer: number | undefined
const stageDrag = { active: false, moved: false, startX: 0, startScrollLeft: 0, pointerId: -1 }
const categories = ['景点', '住宿', '餐饮', '服务'] as const
const { connect } = useTripSSE((event) => {
  store.planningEvent = event
  if (event.event === 'planning_completed' || event.event === 'clarification_required') {
    void refreshPlanning()
  }
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
      store.setDay(0)
      store.planningEvent = null
      await nextTick()
      centerCurrentStage('auto')
      return
    }
    if (snapshot.status === 'failed') {
      planningError.value = snapshot.verification_result?.issues?.[0]?.description || '规划失败，请修改需求后重试。'
      return
    }
    if (snapshot.status !== 'clarification_required') {
      pollingTimer = window.setTimeout(() => void refreshPlanning(), 900)
    }
  } catch (error) {
    planningError.value = error instanceof Error ? error.message : '无法读取规划进度'
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

function selectStageById(stageId: string) {
  const item = allStages.value.find((entry) => entry.stage.id === stageId)
  if (item) selectJourneyStage(item)
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
  return `${Math.floor(minutes / 60)}小时${minutes % 60 ? `${minutes % 60}分钟` : ''}`
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
        <button class="ghost-button" @click="exportMarkdown"><Download />导出 Markdown</button>
      </div>
    </header>
    <div v-if="versionMessage" class="degraded-banner">{{ versionMessage }}</div>

    <div v-if="loading" class="page-state">正在加载武汉—庐山行程…</div>
    <section v-else-if="planningSnapshot && !store.currentDay" class="planning-state glass-card">
      <span class="eyebrow">LANGGRAPH PLANNING</span>
      <h2>{{ planningSnapshot.clarification_question || '正在生成您的真实路线与行程安排…' }}</h2>
      <div class="planning-meter">
        <i :style="{ width: `${store.planningEvent?.progress || planningSnapshot.progress.value || 3}%` }" />
      </div>
      <p>{{ store.planningEvent?.label || planningSnapshot.progress.label || '规划任务已进入队列' }}</p>
      <div v-if="planningSnapshot.defaults_applied.length" class="visible-defaults">
        <strong>已采用的可见默认值</strong>
        <span v-for="item in planningSnapshot.defaults_applied" :key="item">{{ item }}</span>
      </div>
      <form
        v-if="planningSnapshot.status === 'clarification_required'"
        class="clarification-form"
        @submit.prevent="submitClarification"
      >
        <input v-model="clarificationAnswer" autofocus placeholder="输入补充信息…" />
        <button class="primary-button">继续规划</button>
      </form>
      <p v-if="planningError" class="planning-error">{{ planningError }}</p>
    </section>
    <template v-else-if="store.trip && store.currentDay">
      <div v-if="degraded" class="degraded-banner">后端暂不可用，正在加载本地行程数据。</div>
      <section class="plan-grid">
        <aside class="trip-sidebar glass-card">
          <select :value="store.currentDayIndex" @change="store.setDay(Number(($event.target as HTMLSelectElement).value))">
            <option v-for="(day, index) in store.trip.days" :key="day.id" :value="index">
              第 {{ day.day_index }} 天 · {{ day.date.slice(5) }}
            </option>
          </select>
          <div class="day-summary">
            <strong>{{ store.currentDay.title }}</strong>
            <span>{{ store.currentDay.total_distance_km }} km · {{ Math.floor(store.currentDay.total_drive_minutes / 60) }}h{{ store.currentDay.total_drive_minutes % 60 }}m</span>
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
          <AmapRouteMap
            :day="store.currentDay"
            :active-stage-id="store.currentStageId"
            @select-stage="selectStageById"
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
