<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bell, ChevronDown, CircleUserRound, CloudSun, Grid2X2, Paperclip,
  Mic, Route, Send, Settings, SlidersHorizontal, UserRound, CarFront,
} from '@lucide/vue'
import {
  createTrip,
  preflightTrip,
  startPlanning as startPlanningRequest,
  type PreflightResult,
} from '../api/trips'

const router = useRouter()
const prompt = ref('周六早上从武汉出发，去庐山两天一夜，周日晚八点前回来，喜欢自然景观')
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
  isFirefox.value = /firefox/i.test(navigator.userAgent)
  void import('@google/model-viewer').then(({ ModelViewerElement }) => {
    // The model is intentionally loaded, but keep adaptive rendering bounded.
    // model-viewer clamps this value to a safe minimum of 0.25.
    ModelViewerElement.minimumRenderScale = 0.25
  }).catch(() => {
    modelError.value = true
  })
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
    planningError.value = error instanceof Error ? error.message : '规划服务暂不可用'
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
        <span><CloudSun class="sun-icon" /> 22°C&nbsp; 晴</span>
        <span><CarFront class="mint-icon" /> <strong>450</strong> km <small>估算</small></span>
        <button class="icon-button notification" aria-label="通知"><Bell /><i /></button>
      </div>
    </header>

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
          camera-orbit="35deg 70deg 275%"
          min-camera-orbit="auto auto 55%"
          max-camera-orbit="auto auto 450%"
          field-of-view="28deg"
          min-field-of-view="26deg"
          max-field-of-view="38deg"
          :shadow-intensity="0"
          shadow-softness="1"
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
            <div><dt>人数</dt><dd>{{ preflight.summary.travelers || 1 }} 人</dd></div>
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
              placeholder="输入目的地，或描述您的旅行设想…"
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
                :disabled="planning || preflightChecking || Boolean(preflight && !preflight.ready)"
                @click="startPlanning"
              >
                <Send :size="20" />
                {{ planning ? '正在启动…' : preflightChecking ? '正在检查…' : preflight && !preflight.ready ? '请先完成确认' : '开始规划' }}
              </button>
            </div>
          </div>
        </div>
      </div>
      <p v-if="planningError" class="planning-error">{{ planningError }}</p>

      <div class="quick-grid">
        <button v-for="[icon, label] in quickActions" :key="label" @click="prompt = String(label)">
          <span>{{ icon }}</span>{{ label }}<Route :size="17" />
        </button>
      </div>
    </section>

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
