<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Bot, RefreshCw, Send, Sparkles } from '@lucide/vue'
import {
  applyPlanPatch,
  fetchRecommendations,
  fetchTrip,
  interpretTripEdit,
  previewCandidatePatch,
  rejectPlanPatch,
  rollbackPlanPatch,
  type RecommendationCandidate,
} from '../../api/trips'
import { useTripStore } from '../../stores/trip'

const store = useTripStore()
const emit = defineEmits<{ 'replan-requested': [] }>()
const message = ref('')
const messages = ref<{ side: 'ai' | 'user', text: string }[]>([])
const recommendationOpen = ref(false)
const recommendationLoading = ref(false)
const recommendationError = ref('')
const recommendations = ref<RecommendationCandidate[]>([])

const categoryMap = {
  景点: 'attractions',
  住宿: 'hotels',
  餐饮: 'meals',
} as const
const currentCategory = computed(() => categoryMap[store.category as keyof typeof categoryMap])
const planningActive = computed(() => store.trip?.status === 'planning')
const recentPlanningEvents = computed(() => store.planningEvents.slice(-10))
const selectedActivity = computed(() =>
  store.currentDay?.activities.find((item) => item.id === store.selectedNodeId),
)
const canReplace = computed(() => {
  const expectedType = {
    attractions: 'attraction',
    hotels: 'hotel',
    meals: 'meal',
  }[currentCategory.value ?? 'attractions']
  return Boolean(selectedActivity.value && selectedActivity.value.type === expectedType)
})
const contextText = computed(() => {
  if (selectedActivity.value) return `已选安排：${selectedActivity.value.place.name}`
  if (store.currentStage) return `当前阶段：${store.currentStage.title}`
  return '请先在地图或阶段栏选择目标'
})

const editExamples = [
  '第2天加一家酒店',
  '在返程服务区安排午饭',
  '第1天再加一个景点，少逛一会也可以',
]

function candidatePrice(candidate: RecommendationCandidate) {
  const price = candidate.ticket_or_price
  const minimum = price?.minimum ?? candidate.price_min_cny
  const maximum = price?.maximum ?? candidate.price_max_cny
  if (minimum === undefined && maximum === undefined) return '价格待查询'
  return minimum === maximum || maximum === undefined
    ? `¥${minimum}`
    : `¥${minimum}–${maximum}`
}

async function loadRecommendations() {
  if (!store.trip || !currentCategory.value) return
  recommendationOpen.value = true
  recommendationLoading.value = true
  recommendationError.value = ''
  store.pendingPatch = null
  try {
    const result = await fetchRecommendations(store.trip.id, currentCategory.value)
    recommendations.value = result.items
  } catch (error) {
    recommendationError.value = error instanceof Error ? error.message : '备选方案加载失败'
  } finally {
    recommendationLoading.value = false
  }
}

async function preview(
  candidate: RecommendationCandidate,
  operation: 'add' | 'replace',
) {
  if (!store.trip || !store.currentDay || !currentCategory.value) return
  recommendationError.value = ''
  try {
    store.pendingPatch = await previewCandidatePatch(store.trip.id, {
      candidate_id: candidate.candidate_id,
      category: currentCategory.value,
      day_id: store.currentDay.id,
      operation,
      target_activity_id: operation === 'replace' ? selectedActivity.value?.id : undefined,
    })
  } catch (error) {
    recommendationError.value = error instanceof Error ? error.message : '无法生成修改预览'
  }
}

async function decidePatch(apply: boolean) {
  if (!store.trip || !store.pendingPatch) return
  recommendationError.value = ''
  try {
    if (apply) {
      const patchName = displayPatchName(store.pendingPatch)
      const result = await applyPlanPatch(store.trip.id, store.pendingPatch.id)
      // Hydrate once more after the commit so deletion -> add/replacement
      // sequences cannot resurrect a stale activity list from the preview.
      try {
        store.trip = await fetchTrip(store.trip.id)
      } catch {
        // The apply response is already canonical; a transient refresh error
        // must not make a committed edit look like it failed.
        store.trip = result.trip
      }
      store.lastAppliedPatchId = result.patch.id
      recommendations.value = recommendations.value.filter((candidate) =>
        !store.trip?.days.some((day) => day.activities.some((activity) =>
          activity.place.name === candidate.place.name && activity.type === ({ attractions: 'attraction', hotels: 'hotel', meals: 'meal' }[currentCategory.value ?? 'attractions']),
        )),
      )
      messages.value.push({
        side: 'ai',
        text: `已应用：${patchName}`,
      })
    } else {
      await rejectPlanPatch(store.trip.id, store.pendingPatch.id)
    }
    store.pendingPatch = null
  } catch (error) {
    recommendationError.value = error instanceof Error ? error.message : '修改处理失败'
  }
}

function displayPatchName(patch: NonNullable<typeof store.pendingPatch>) {
  return patch.proposed_value.candidate?.place.name
    || (patch.original_value.place as { name?: string } | undefined)?.name
    || '当前安排'
}

async function undoLastPatch() {
  if (!store.trip || !store.lastAppliedPatchId) return
  recommendationError.value = ''
  try {
    const result = await rollbackPlanPatch(store.trip.id, store.lastAppliedPatchId)
    store.trip = result.trip
    store.lastAppliedPatchId = null
    messages.value.push({ side: 'ai', text: '已撤销上一次修改。' })
  } catch (error) {
    recommendationError.value = error instanceof Error ? error.message : '撤销失败'
  }
}

async function send() {
  const text = message.value.trim()
  if (!text || !store.trip) return
  messages.value.push({ side: 'user', text })
  message.value = ''
  recommendationError.value = ''
  try {
    const result = await interpretTripEdit(store.trip.id, {
      message: text,
      current_day_id: store.currentDay?.id,
      current_target_id: store.selectedNodeId ?? undefined,
    })
    messages.value.push({ side: 'ai', text: result.message })
    if (result.patch) store.pendingPatch = result.patch
    if (result.global_replan_required) {
      // A global edit (for example “换成两天行程”) is not a patch preview:
      // it must restart the planning graph so every day, route and meal is
      // recalculated. The parent owns the live progress/SSE lifecycle.
      emit('replan-requested')
    }
  } catch (error) {
    messages.value.push({
      side: 'ai',
      text: error instanceof Error ? error.message : '暂时无法理解这条修改要求',
    })
  }
}

watch(() => store.category, () => {
  recommendationOpen.value = false
  recommendations.value = []
  store.pendingPatch = null
})
watch(() => store.pendingPatch?.id, (patchId) => {
  if (patchId) recommendationOpen.value = true
})
</script>

<template>
  <aside class="agent-panel glass-card">
    <header><Bot /><strong>Agent 行程助理</strong><span class="online-dot" /></header>
    <div class="context-chip">{{ contextText }}</div>

    <div v-if="recommendationOpen" class="recommendation-panel">
      <header>
        <div><Sparkles /><strong>{{ store.category }}备选</strong></div>
        <button aria-label="刷新备选" @click="loadRecommendations"><RefreshCw /></button>
      </header>
      <div v-if="recommendationLoading" class="recommendation-state">正在查询并排序…</div>
      <div v-else-if="recommendationError" class="recommendation-state error">{{ recommendationError }}</div>
      <div v-else-if="!recommendations.length" class="recommendation-state">暂时没有可用备选</div>
      <div v-else class="recommendation-list">
        <article v-for="candidate in recommendations" :key="candidate.candidate_id">
          <img
            v-if="candidate.image_url"
            class="recommendation-photo"
            :src="candidate.image_url"
            :alt="candidate.place.name"
            loading="lazy"
          />
          <header>
            <b>#{{ candidate.rank }} {{ candidate.place.name }}</b>
            <span>{{ candidate.score.toFixed(1) }} 分</span>
          </header>
          <p>{{ candidate.agent_reason || candidate.recommendation_reasons?.join(' · ') || candidate.place.address || '综合距离与偏好排序' }}</p>
          <p v-if="candidate.description" class="recommendation-description">{{ candidate.description }}</p>
          <a
            v-if="candidate.detail_url || candidate.source_records?.find((item) => item.url)"
            class="recommendation-detail"
            :href="candidate.detail_url || candidate.source_records?.find((item) => item.url)?.url"
            target="_blank"
            rel="noreferrer"
          >查看详细介绍与数据来源</a>
          <footer>
            <span>{{ candidatePrice(candidate) }}</span>
            <button @click="preview(candidate, 'add')">加入</button>
            <button v-if="canReplace" @click="preview(candidate, 'replace')">替换所选</button>
          </footer>
        </article>
      </div>
      <div v-if="store.pendingPatch" class="patch-card">
        <span>修改预览</span>
        <strong>
          {{ store.pendingPatch.operation === 'replace' ? '替换为' : store.pendingPatch.operation === 'delete' ? '删除' : '加入' }}
          {{ displayPatchName(store.pendingPatch) }}
        </strong>
        <dl>
          <div><dt>影响日期</dt><dd>第 {{ store.currentDay?.day_index }} 天</dd></div>
          <div><dt>时间变化</dt><dd>{{ store.pendingPatch.time_delta_minutes > 0 ? '+' : '' }}{{ store.pendingPatch.time_delta_minutes }} 分钟</dd></div>
          <div><dt>正式行程</dt><dd class="good">尚未修改</dd></div>
        </dl>
        <div>
          <button @click="decidePatch(false)">放弃</button>
          <button class="apply" @click="decidePatch(true)">确认应用</button>
        </div>
      </div>
    </div>

    <div v-else class="chat-stream">
      <div v-if="planningActive && !recentPlanningEvents.length" class="message ai">
        我正在拆解路线、核对真实道路，并为每天安排景点、用餐、住宿、休息和补能。
      </div>
      <div v-for="(event, index) in recentPlanningEvents" :key="`${event.event}-${event.node}-${index}`" class="message ai planning-message">
        <strong>{{ event.progress }}%</strong>{{ event.label }}
      </div>
      <div v-for="(item, index) in messages" :key="index" :class="['message', item.side]">
        {{ item.text }}
      </div>
      <div v-if="!planningActive && !recentPlanningEvents.length && !messages.length" class="message ai">
        行程安排已经生成。选择地图、阶段或活动后，我可以继续为您比较和调整方案。
      </div>
    </div>
    <div class="suggestions">
      <button v-if="currentCategory" @click="loadRecommendations">查看{{ store.category }}备选</button>
      <button @click="recommendationOpen = false">行程对话</button>
      <button v-if="store.lastAppliedPatchId" @click="undoLastPatch">撤销上次修改</button>
      <button @click="message = '当前阶段有什么需要注意的？'">阶段提示</button>
      <button v-for="example in editExamples" :key="example" @click="message = example">{{ example }}</button>
    </div>
    <form class="chat-input" @submit.prevent="send">
      <input v-model="message" placeholder="输入调整需求…" />
      <button aria-label="发送"><Send /></button>
    </form>
  </aside>
</template>
