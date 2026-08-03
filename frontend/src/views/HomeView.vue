<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bell, ChevronDown, CircleUserRound, CloudSun, Grid2X2, Paperclip,
  History, Mic, Route, Send, Settings, SlidersHorizontal, UserRound, CarFront, Trash2,
} from '@lucide/vue'
import {
  createTrip,
  deleteTrip,
  fetchWeatherForecast,
  listTrips,
  preflightTrip,
  startPlanning as startPlanningRequest,
  type PreflightResult,
} from '../api/trips'
import type { Trip } from '../types/trip'

const router = useRouter()
const PROMPT_STORAGE_KEY = 'roadman:last-trip-prompt'
const promptSuggestion = '周六早上从武汉出发，去庐山两天一夜，周日晚八点前回来，喜欢自然景观'
const prompt = ref('')
const historyOpen = ref(false)
const historyTrips = ref<Trip[]>([])
const historyDeletingId = ref<string | null>(null)
const weather = ref({ temperature: '--', condition: '晴', location: '武汉' })
const activeMenu = ref('账户设置')
const drawerOpen = ref(false)
const accountMenuOpen = ref(false)
const modelEnabled = ref(true)
const modelLoaded = ref(false)
const modelError = ref(false)
const voiceReserved = ref(false)
const planning = ref(false)
const preflightChecking = ref(false)
const planningError = ref('')
const planningServiceError = ref('')
const preflight = ref<PreflightResult | null>(null)
const clarificationAnswers = ref<Record<string, string>>({})
const clarificationIndex = ref(0)
const isFirefox = ref(false)
// Keep the model enabled by default; the render target is bounded by CSS and
// model-viewer's adaptive scale so the real 3D vehicle remains available
// without recreating the previous oversized canvas.
const vehicleMotionAttributes = computed(() => ({}))
const activeClarification = computed(() => preflight.value?.issues[clarificationIndex.value])

const menus = [
  { label: '账户设置', icon: CircleUserRound },
  { label: '车型管理/选择', icon: CarFront },
  { label: '用户设置', icon: UserRound },
  { label: '系统设置', icon: Settings },
  { label: '其他设置', icon: Grid2X2 },
]
const quickActions = [
  ['🚙', '周边单日短途推荐'],
  ['🏞️', '沿途风景路线探索'],
  ['⚡', '新能源补能规划'],
  ['🌦️', '天气变化重规划'],
]

onMounted(() => {
  prompt.value = window.sessionStorage.getItem(PROMPT_STORAGE_KEY) || ''
  isFirefox.value = /firefox/i.test(navigator.userAgent)
  void loadHistory()
  void loadHomeWeather()
  void import('@google/model-viewer').then(({ ModelViewerElement }) => {
    // The model is intentionally loaded, but keep adaptive rendering bounded.
    // model-viewer clamps this value to a safe minimum of 0.25.
    ModelViewerElement.minimumRenderScale = 0.25
  }).catch(() => {
    modelError.value = true
  })
})

async function loadHistory() {
  try {
    const trips = await listTrips()
    historyTrips.value = trips
      .filter((trip) => trip.id !== 'trip_wuhan_lushan_demo')
      .sort((left, right) => right.id.localeCompare(left.id))
      .slice(0, 12)
  } catch {
    historyTrips.value = []
  }
}

function weatherCondition(code: number | null | undefined) {
  if (code === undefined || code === null) return '晴'
  if ([1, 2, 3].includes(code)) return '多云'
  if ([45, 48].includes(code)) return '雾'
  if (code >= 51 && code <= 67) return '小雨'
  if (code >= 71 && code <= 86) return '降雪'
  if (code >= 95) return '雷雨'
  return '晴'
}

function browserCoordinates() {
  const fallback = { latitude: 30.5928, longitude: 114.3055, location: '武汉' }
  if (!navigator.geolocation) return Promise.resolve(fallback)
  return new Promise<typeof fallback>((resolve) => {
    let settled = false
    const finish = (value: typeof fallback) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    const timer = window.setTimeout(() => finish(fallback), 1400)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        window.clearTimeout(timer)
        finish({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          location: '当前位置',
        })
      },
      () => {
        window.clearTimeout(timer)
        finish(fallback)
      },
      { enableHighAccuracy: false, maximumAge: 10 * 60 * 1000, timeout: 1200 },
    )
  })
}

async function loadHomeWeather() {
  const coordinates = await browserCoordinates()
  try {
    const result = await fetchWeatherForecast(coordinates.latitude, coordinates.longitude)
    const current = result.data?.current
    const temperature = current?.temperature_2m
    const code = current?.weather_code
    weather.value = {
      temperature: typeof temperature === 'number' ? String(Math.round(temperature)) : '--',
      condition: weatherCondition(typeof code === 'number' ? code : null),
      location: coordinates.location,
    }
  } catch {
    weather.value = { temperature: '--', condition: '天气待更新', location: coordinates.location }
  }
}

function openHistoryTrip(trip: Trip) {
  historyOpen.value = false
  if (trip.status === 'collecting' || trip.status === 'planning') {
    router.push(`/trips/${trip.id}/plan?planning=1`)
  } else {
    router.push(`/trips/${trip.id}/plan`)
  }
}

async function removeHistoryTrip(trip: Trip) {
  if (!window.confirm(`确定删除“${trip.title}”吗？删除后无法从历史规划恢复。`)) return
  historyDeletingId.value = trip.id
  try {
    await deleteTrip(trip.id)
    // Re-read the server list so a stale client cache or a failed transaction
    // cannot make a deleted row look gone when it still exists remotely.
    await loadHistory()
    if (historyTrips.value.some((item) => item.id === trip.id)) {
      throw new Error('服务器仍返回这条历史规划')
    }
  } catch {
    window.alert('删除失败，请稍后重试。')
  } finally {
    historyDeletingId.value = null
  }
}

watch(prompt, (value) => {
  if (typeof window === 'undefined') return
  if (value.trim()) window.sessionStorage.setItem(PROMPT_STORAGE_KEY, value)
  else window.sessionStorage.removeItem(PROMPT_STORAGE_KEY)
})

function handleModelLoad(event: Event) {
  modelLoaded.value = true
  const viewer = event.currentTarget as HTMLElement
  const canvas = viewer.shadowRoot?.querySelector('canvas')
  canvas?.addEventListener('webglcontextlost', (contextEvent) => {
    contextEvent.preventDefault()
    modelLoaded.value = false
    modelError.value = true
    modelEnabled.value = false
  }, { once: true })
}

function issueKey(issue: PreflightResult['issues'][number]) {
  return `${issue.code}:${issue.field || ''}`
}

function resetPreflight() {
  preflight.value = null
  clarificationAnswers.value = {}
  clarificationIndex.value = 0
  planningError.value = ''
  planningServiceError.value = ''
}

async function checkPreflight(confirmed = false) {
  if (planning.value || preflightChecking.value || !prompt.value.trim()) return
  planningError.value = ''
  try {
    preflightChecking.value = true
    preflight.value = await preflightTrip(
      prompt.value.trim(),
      clarificationAnswers.value,
      confirmed,
      preflight.value?.extracted,
      preflight.value?.semantic_checked,
    )
    clarificationIndex.value = 0
    if (!preflight.value.ready) return
    planning.value = true
    const trip = await createTrip(prompt.value.trim(), preflight.value.extracted)
    await startPlanningRequest(trip.id)
    await router.push(`/trips/${trip.id}/plan?planning=1`)
  } catch (error) {
    planningServiceError.value = error instanceof Error ? error.message : '规划服务暂不可用'
  } finally {
    preflightChecking.value = false
    planning.value = false
  }
}

async function startPlanning() {
  await checkPreflight(false)
}

async function submitClarification() {
  const issue = activeClarification.value
  if (!issue) return
  const key = issueKey(issue)
  if (!clarificationAnswers.value[key]?.trim()) {
    planningError.value = '请先回答当前问题。'
    return
  }
  planningError.value = ''
  if (clarificationIndex.value < (preflight.value?.issues.length ?? 1) - 1) {
    clarificationIndex.value += 1
    return
  }
  await checkPreflight(false)
}

async function confirmAndPlan() {
  await checkPreflight(true)
}

function activate(label: string) {
  activeMenu.value = label
  accountMenuOpen.value = false
  drawerOpen.value = true
}
</script>

<template>
  <main class="home-shell">
    <header class="home-top glass-card">
      <button class="profile" aria-label="打开账户菜单" @click="accountMenuOpen = !accountMenuOpen">
        <div class="avatar">陈</div>
        <div>
          <span class="eyebrow">ROADMAN 驾驶员</span>
          <strong>您好，陈先生！</strong>
        </div>
        <ChevronDown :size="20" :class="{ rotated: accountMenuOpen }" />
      </button>
      <div class="top-stats">
        <span><CloudSun class="sun-icon" /> {{ weather.temperature }}°C&nbsp; {{ weather.condition }} <small>{{ weather.location }}</small></span>
        <span><CarFront class="mint-icon" /> <strong>450</strong> km <small>估算</small></span>
        <button class="history-button" type="button" @click="historyOpen = !historyOpen">
          <History :size="20" />历史规划<span>{{ historyTrips.length }}</span>
        </button>
        <button class="icon-button notification" aria-label="通知"><Bell /><i /></button>
      </div>
    </header>

    <Transition name="menu">
      <aside v-if="historyOpen" class="history-popover glass-card" aria-label="历史规划">
        <header><strong>历史规划</strong><button type="button" @click="loadHistory">刷新</button></header>
        <p v-if="!historyTrips.length" class="history-empty">还没有保存的规划，完成一次规划后会自动保存在这里。</p>
        <div
          v-for="trip in historyTrips"
          :key="trip.id"
          class="history-item"
          role="button"
          tabindex="0"
          @click="openHistoryTrip(trip)"
          @keydown.enter="openHistoryTrip(trip)"
        >
          <span class="history-item-main">
            <strong>{{ trip.title }}</strong>
            <span>{{ trip.status === 'completed' ? '规划完成' : trip.status === 'planning' ? '正在规划' : '待继续' }} · {{ trip.days.length }} 天</span>
          </span>
          <span class="history-item-actions">
            <button
              type="button"
              aria-label="删除历史规划"
              title="删除"
              :disabled="historyDeletingId === trip.id"
              :aria-busy="historyDeletingId === trip.id"
              @click.stop="removeHistoryTrip(trip)"
            ><Trash2 :size="15" /></button>
          </span>
        </div>
      </aside>
    </Transition>

    <Transition name="menu">
      <nav v-if="accountMenuOpen" class="account-dropdown glass-card" aria-label="账户菜单">
        <button
          v-for="menu in menus"
          :key="menu.label"
          :class="{ active: activeMenu === menu.label }"
          @click="activate(menu.label)"
        >
          <component :is="menu.icon" :size="20" />
          {{ menu.label }}
        </button>
      </nav>
    </Transition>

    <section class="home-content">
      <div class="vehicle-stage">
        <component
          v-if="modelEnabled"
          :is="'model-viewer'"
          class="vehicle-model"
          :class="{ loaded: modelLoaded }"
          src="/models/car-concept-white.glb"
          alt="可旋转的 RoadMan 3D 车辆模型"
          camera-controls
          v-bind="vehicleMotionAttributes"
          camera-orbit="35deg 70deg 85%"
          min-camera-orbit="auto auto 60%"
          max-camera-orbit="auto auto 260%"
          field-of-view="28deg"
          min-field-of-view="26deg"
          max-field-of-view="38deg"
          :shadow-intensity="0.3"
          shadow-softness="0.85"
          :exposure="0.85"
          interaction-prompt="none"
          @load="handleModelLoad"
          @error="modelError = true; modelEnabled = false"
        />
        <div v-if="modelEnabled && !modelLoaded && !modelError" class="vehicle-loading" role="status">
          <i />
          <span>正在加载车辆模型…</span>
        </div>
        <div v-else-if="modelError" class="vehicle-loading error" role="status">
          <span>白模渲染失败，已暂停渲染以保护显卡</span>
          <button type="button" class="secondary-button" @click="modelError = false; modelEnabled = true">重新加载白模</button>
        </div>
        <span class="sr-only">{{ modelLoaded ? '3D 模型已加载' : modelError ? '3D 模型加载失败' : '正在加载 3D 模型' }}</span>
      </div>
    </section>

    <section class="planner-area">
      <div class="section-title">
        <span>从一句话开始</span>
        <h1>我是您的自驾游规划 <em>Agent</em></h1>
      </div>
      <section
        v-if="preflight && !preflight.ready"
        class="preflight-panel glass-card"
        role="dialog"
        aria-modal="false"
        aria-live="polite"
        aria-label="规划前需求确认"
      >
        <template v-if="activeClarification">
          <div class="preflight-heading">
            <span>Agent 需要向您确认</span>
            <b>{{ clarificationIndex + 1 }} / {{ preflight.issues.length }}</b>
          </div>
          <strong>{{ activeClarification.message }}</strong>
          <p>回答后会重新检查全部条件；所有问题解决前不会开始规划。</p>
          <div v-if="activeClarification.answer_type === 'choice'" class="preflight-options">
            <button
              v-for="option in activeClarification.options"
              :key="option"
              :class="{ selected: clarificationAnswers[issueKey(activeClarification)] === option }"
              @click="clarificationAnswers[issueKey(activeClarification)] = option"
            >
              {{ option }}
            </button>
          </div>
          <input
            v-else
            v-model="clarificationAnswers[issueKey(activeClarification)]"
            class="preflight-answer"
            :type="activeClarification.answer_type === 'date' ? 'date' : 'text'"
            :placeholder="activeClarification.answer_type === 'time'
              ? '例如：取消原到达限制，按合理车程安排'
              : '请输入修正或补充信息'"
            @keyup.enter="submitClarification"
          />
          <div class="preflight-actions">
            <button
              class="secondary-button"
              :disabled="clarificationIndex === 0"
              @click="clarificationIndex -= 1"
            >
              上一个
            </button>
            <button class="primary-button" :disabled="preflightChecking" @click="submitClarification">
              {{ clarificationIndex < preflight.issues.length - 1 ? '下一个问题' : '重新检查全部条件' }}
            </button>
          </div>
        </template>
        <template v-else-if="preflight.confirmation_required">
          <div class="preflight-heading">
            <span>最终确认</span>
            <b>检查通过</b>
          </div>
          <strong>请确认以下需求，确认后才会开始规划</strong>
          <dl class="preflight-summary">
            <div><dt>路线</dt><dd>{{ preflight.summary.origin_name }} → {{ preflight.summary.destination_name }}</dd></div>
            <div><dt>日期</dt><dd>{{ preflight.summary.start_date }} 至 {{ preflight.summary.end_date }}</dd></div>
            <div><dt>人数</dt><dd>{{ preflight.summary.travelers ?? '待确认' }}{{ preflight.summary.travelers ? ' 人' : '' }}</dd></div>
            <div v-if="preflight.summary.max_days"><dt>行程上限</dt><dd>最多 {{ preflight.summary.max_days }} 天</dd></div>
            <div v-if="preflight.summary.clarifications?.length">
              <dt>已确认</dt><dd>{{ preflight.summary.clarifications.join('；') }}</dd>
            </div>
          </dl>
          <div class="preflight-actions">
            <button class="secondary-button" @click="resetPreflight">返回修改</button>
            <button class="primary-button" :disabled="preflightChecking || planning" @click="confirmAndPlan">
              确认无误，开始规划
            </button>
          </div>
        </template>
        <template v-else>
          <div class="preflight-heading">
            <span>检查结果需要刷新</span>
            <b>可恢复</b>
          </div>
          <strong>没有收到需要补充的具体问题，请重新检查一次。</strong>
          <p>这不会创建行程，也不会丢失输入内容。</p>
          <div class="preflight-actions">
            <button class="secondary-button" @click="resetPreflight">返回修改</button>
            <button class="primary-button" :disabled="preflightChecking" @click="checkPreflight(false)">
              重新检查
            </button>
            </div>
          </template>
          <section v-if="preflight.special_event_research?.length" class="preflight-event-research" aria-label="特殊活动检索结果">
            <header><strong>Agent 已核对特殊活动</strong><small>请根据来源中的窗口选择日期</small></header>
            <article v-for="item in preflight.special_event_research" :key="item.event">
              <strong>{{ item.event }}</strong>
              <span v-if="item.facts?.peak_start_date">极大期：{{ item.facts.peak_start_date }}{{ item.facts.peak_end_date && item.facts.peak_end_date !== item.facts.peak_start_date ? ` 至 ${item.facts.peak_end_date}` : '' }}</span>
              <span v-if="item.facts?.peak_time_local">北京时间 {{ item.facts.peak_time_local }}</span>
              <span v-else-if="item.facts?.peak_time_utc">UTC {{ item.facts.peak_time_utc }}</span>
              <span v-else-if="item.facts?.peak_time_label">来源时间：{{ item.facts.peak_time_label }}</span>
              <p>{{ item.facts?.summary || '已找到公开资料，出发前仍需复核天气与现场可见性。' }}</p>
              <nav v-if="item.sources?.length">
                <a v-for="(source, index) in item.sources.slice(0, 2)" :key="source.url || index" :href="source.url" target="_blank" rel="noreferrer">{{ source.title || `来源 ${index + 1}` }}</a>
              </nav>
            </article>
          </section>
       </section>
      <div class="planner-box glass-card">
        <div class="agent-orb">AI</div>
        <div class="prompt-wrap">
          <label for="trip-prompt">告诉我您想去哪里</label>
          <div class="prompt-composer">
            <input
              id="trip-prompt"
              v-model="prompt"
              @input="resetPreflight"
              @keyup.enter="startPlanning"
              aria-label="旅行需求"
            />
            <div class="composer-actions">
              <button class="composer-icon" aria-label="添加附件" title="添加附件（预留）">
                <Paperclip />
              </button>
              <button
                class="composer-icon"
                :class="{ active: voiceReserved }"
                :aria-pressed="voiceReserved"
                aria-label="语音输入"
                title="语音输入（功能预留）"
                @click="voiceReserved = !voiceReserved"
              >
                <Mic />
              </button>
              <button
                class="primary-button"
                :disabled="planning || preflightChecking || !prompt.trim() || Boolean(preflight && !preflight.ready)"
                @click="startPlanning"
              >
                <Send :size="20" />
                {{ planning ? '正在启动…' : preflightChecking ? '正在检查…' : preflight && !preflight.ready ? '请先完成确认' : '开始规划' }}
              </button>
            </div>
          </div>
          <div v-if="!prompt.trim()" class="prompt-suggestion">
            <span>可以这样描述你的出行时间、目的地、同行人和偏好：</span>
            <button type="button" @click="prompt = promptSuggestion">使用示例</button>
            <em>{{ promptSuggestion }}</em>
          </div>
        </div>
      </div>

      <div class="quick-grid">
        <button v-for="[icon, label] in quickActions" :key="label" @click="prompt = String(label)">
          <span>{{ icon }}</span>{{ label }}<Route :size="17" />
        </button>
      </div>
    </section>

    <Transition name="failure-modal">
      <div
        v-if="planningServiceError"
        class="failure-dialog-backdrop"
        role="presentation"
        @click.self="planningServiceError = ''"
      >
        <section class="failure-dialog glass-card" role="alertdialog" aria-modal="true" aria-labelledby="home-failure-title">
          <div class="failure-dialog-icon" aria-hidden="true">!</div>
          <div>
            <span class="failure-dialog-kicker">暂时无法开始规划</span>
            <h2 id="home-failure-title">需求还需要调整</h2>
            <p>{{ planningServiceError }}</p>
            <div class="failure-dialog-hint">保留你的原始输入，你可以关闭提示后补充时间、目的地或交通方式。</div>
          </div>
          <div class="failure-dialog-actions">
            <button class="secondary-button" @click="planningServiceError = ''">返回修改</button>
            <button class="primary-button" @click="planningServiceError = ''; checkPreflight(false)">重新检查</button>
          </div>
        </section>
      </div>
    </Transition>

    <Transition name="drawer">
      <aside v-if="drawerOpen" class="vehicle-drawer glass-card">
        <button class="drawer-close" @click="drawerOpen = false">×</button>
        <SlidersHorizontal :size="22" />
        <h2>{{ activeMenu }}</h2>
        <template v-if="activeMenu === '车型管理/选择'">
          <span class="status-pill">当前车辆</span>
          <h3>RoadMan 纯电 SUV</h3>
          <dl>
            <div><dt>额定续航</dt><dd>560 km</dd></div>
            <div><dt>当前电量</dt><dd>80%</dd></div>
            <div><dt>估算可用</dt><dd>450 km</dd></div>
            <div><dt>座位数</dt><dd>5</dd></div>
          </dl>
        </template>
        <p v-else>该模块已保留静态交互入口，将在对应阶段接入正式设置表单。</p>
      </aside>
    </Transition>
  </main>
</template>
