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
const modelLoaded = ref(false)
const modelError = ref(false)
const voiceReserved = ref(false)
const planning = ref(false)
const preflightChecking = ref(false)
const planningError = ref('')
const preflight = ref<PreflightResult | null>(null)
const isFirefox = ref(false)
const vehicleMotionAttributes = computed(() => (
  isFirefox.value ? {} : { 'auto-rotate': '' }
))

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
    ModelViewerElement.minimumRenderScale = 0.25
  })
})

async function startPlanning() {
  if (planning.value || preflightChecking.value || !prompt.value.trim()) return
  planningError.value = ''
  try {
    preflightChecking.value = true
    preflight.value = await preflightTrip(prompt.value.trim())
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
          :is="'model-viewer'"
          class="vehicle-model"
          :class="{ loaded: modelLoaded }"
          src="/models/car-concept-optimized.glb"
          alt="可旋转的 RoadMan 3D 车辆模型"
          camera-controls
          v-bind="vehicleMotionAttributes"
          auto-rotate-delay="1200"
          rotation-per-second="12deg"
          camera-orbit="35deg 70deg 275%"
          min-camera-orbit="auto auto 55%"
          max-camera-orbit="auto auto 450%"
          field-of-view="28deg"
          min-field-of-view="26deg"
          max-field-of-view="38deg"
          variant-name="Pearly Swirly"
          :shadow-intensity="isFirefox ? 0.45 : 0.8"
          shadow-softness="1"
          :exposure="isFirefox ? 0.9 : 1"
          environment-image="neutral"
          interaction-prompt="none"
          @load="modelLoaded = true"
          @error="modelError = true"
        />
        <div v-if="!modelLoaded && !modelError" class="vehicle-loading" role="status">
          <i />
          <span>正在加载车辆模型…</span>
        </div>
        <div v-else-if="modelError" class="vehicle-loading error" role="status">
          车辆模型加载失败，请刷新重试
        </div>
        <span class="sr-only">{{ modelLoaded ? '3D 模型已加载' : modelError ? '3D 模型加载失败' : '正在加载 3D 模型' }}</span>
      </div>
    </section>

    <section class="planner-area">
      <div class="section-title">
        <span>从一句话开始</span>
        <h1>我是您的自驾游规划 <em>Agent</em></h1>
      </div>
      <section v-if="preflight && !preflight.ready" class="preflight-panel glass-card" aria-live="polite">
        <strong>规划前还需要确认</strong>
        <p>请直接在下方输入框补充或修正这些信息，确认无误后才会开始生成行程。</p>
        <ul>
          <li
            v-for="issue in preflight.issues"
            :key="`${issue.code}-${issue.message}`"
            :class="{ error: issue.severity === 'error' }"
          >
            {{ issue.message }}
          </li>
        </ul>
      </section>
      <div class="planner-box glass-card">
        <div class="agent-orb">AI</div>
        <div class="prompt-wrap">
          <label for="trip-prompt">告诉我您想去哪里</label>
          <div class="prompt-composer">
            <input
              id="trip-prompt"
              v-model="prompt"
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
              <button class="primary-button" :disabled="planning || preflightChecking" @click="startPlanning">
                <Send :size="20" />
                {{ planning ? '正在启动…' : preflightChecking ? '正在检查…' : preflight && !preflight.ready ? '重新检查' : '开始规划' }}
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
