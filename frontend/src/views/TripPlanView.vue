<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, ChevronLeft, ChevronRight, Download, Share2 } from '@lucide/vue'
import { fetchMockTrip } from '../api/trips'
import { useTripStore } from '../stores/trip'
import { useTripSSE } from '../composables/useTripSSE'
import AmapRouteMap from '../components/map/AmapRouteMap.vue'
import ActivityList from '../components/trip/ActivityList.vue'
import AgentPanel from '../components/agent/AgentPanel.vue'
import mockTrip from '../../../shared/examples/wuhan-lushan-trip.json'

const router = useRouter()
const store = useTripStore()
const loading = ref(true)
const degraded = ref(false)
const stageTrack = ref<HTMLElement | null>(null)
const stageDrag = { active: false, moved: false, startX: 0, startScrollLeft: 0, pointerId: -1 }
const categories = ['景点', '住宿', '餐饮', '服务'] as const
const { connect } = useTripSSE((event) => (store.planningEvent = event))

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

async function load() {
  try {
    store.trip = await fetchMockTrip()
  } catch {
    store.trip = mockTrip as unknown as typeof store.trip
    degraded.value = true
  } finally {
    loading.value = false
    await nextTick()
    centerCurrentStage('auto')
  }
}

function demoProgress() {
  if (store.trip) connect(store.trip.id)
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
        <button class="ghost-button"><Share2 />分享</button>
        <button class="ghost-button"><Download />导出</button>
      </div>
    </header>

    <div v-if="loading" class="page-state">正在加载武汉—庐山行程…</div>
    <template v-else-if="store.trip && store.currentDay">
      <div v-if="degraded" class="degraded-banner">后端暂不可用，正在加载本地行程数据。</div>
      <div v-if="store.planningEvent" class="progress-banner">
        <span>{{ store.planningEvent.label }}</span>
        <div><i :style="{ width: `${store.planningEvent.progress}%` }" /></div>
        <b>{{ store.planningEvent.progress }}%</b>
      </div>

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
            @remove="store.removeActivity"
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
          <button class="demo-sse" @click="demoProgress">查看规划进度</button>
        </section>

        <AgentPanel />
      </section>
    </template>
    <div v-else class="page-state error">行程加载失败，请稍后重试。</div>
  </main>
</template>
