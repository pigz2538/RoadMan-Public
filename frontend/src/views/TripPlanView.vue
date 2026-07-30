<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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

async function load() {
  try {
    store.trip = await fetchMockTrip()
  } catch {
    store.trip = mockTrip as unknown as typeof store.trip
    degraded.value = true
  } finally {
    loading.value = false
  }
}

function demoProgress() {
  if (store.trip) connect(store.trip.id)
}

function selectStageById(stageId: string) {
  const stage = store.currentDay?.stages.find((item) => item.id === stageId)
  if (stage) store.setStage(stage)
}

onMounted(load)
</script>

<template>
  <main class="plan-shell">
    <header class="plan-top">
      <button class="ghost-button" @click="router.push('/home')"><ArrowLeft />返回主页</button>
      <div>
        <span class="eyebrow">ROADMAN · MOCK TRIP</span>
        <h1>{{ store.trip?.title || '自驾游深度规划页' }}</h1>
      </div>
      <div class="top-actions">
        <button class="ghost-button"><Share2 />分享</button>
        <button class="ghost-button"><Download />导出</button>
      </div>
    </header>

    <div v-if="loading" class="page-state">正在加载武汉—庐山行程…</div>
    <template v-else-if="store.trip && store.currentDay">
      <div v-if="degraded" class="degraded-banner">后端未启动，正在使用浏览器内置 Mock 数据演示。</div>
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
            <button class="stage-arrow"><ChevronLeft /></button>
            <button
              v-for="stage in store.currentDay.stages"
              :key="stage.id"
              :class="['stage-card', { active: stage.id === store.currentStageId }]"
              @click="store.setStage(stage)"
            >
              <span>{{ stage.mode === 'driving' ? '🚙' : '📍' }} {{ stage.title }}</span>
              <strong>{{ stage.distance_km }} km · {{ Math.floor(stage.duration_minutes / 60) }}h{{ stage.duration_minutes % 60 }}m</strong>
              <small v-if="stage.warnings[0]">{{ stage.warnings[0].message }}</small>
            </button>
            <button class="stage-arrow"><ChevronRight /></button>
          </div>
          <button class="demo-sse" @click="demoProgress">演示 SSE 规划进度</button>
        </section>

        <AgentPanel />
      </section>
    </template>
    <div v-else class="page-state error">行程加载失败，请稍后重试。</div>
  </main>
</template>
