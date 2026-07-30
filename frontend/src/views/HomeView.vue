<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bell, ChevronDown, CircleUserRound, CloudSun, Grid2X2, Paperclip,
  Mic, Route, Send, Settings, SlidersHorizontal, UserRound, CarFront,
} from '@lucide/vue'

const router = useRouter()
const prompt = ref('周六早上从武汉出发，去庐山两天一夜，周日晚八点前回来，喜欢自然景观')
const activeMenu = ref('账户设置')
const drawerOpen = ref(false)
const accountMenuOpen = ref(false)
const modelLoaded = ref(false)
const modelError = ref(false)
const voiceReserved = ref(false)

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

onMounted(() => import('@google/model-viewer'))

function startPlanning() {
  router.push('/trips/trip_wuhan_lushan_demo/plan')
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
          src="/models/car-concept.glb"
          poster="/car-suv.svg"
          alt="可旋转的 RoadMan 3D 车辆模型"
          camera-controls
          auto-rotate
          auto-rotate-delay="1200"
          rotation-per-second="12deg"
          camera-orbit="35deg 70deg 75%"
          field-of-view="28deg"
          min-field-of-view="20deg"
          max-field-of-view="45deg"
          variant-name="Pearly Swirly"
          shadow-intensity="1.2"
          shadow-softness=".8"
          exposure="1.05"
          environment-image="neutral"
          interaction-prompt="none"
          @load="modelLoaded = true"
          @error="modelError = true"
        />
        <span class="sr-only">{{ modelLoaded ? '3D 模型已加载' : modelError ? '3D 模型加载失败' : '正在加载 3D 模型' }}</span>
      </div>
    </section>

    <section class="planner-area">
      <div class="section-title">
        <span>从一句话开始</span>
        <h1>我是您的自驾游规划 <em>Agent</em></h1>
      </div>
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
              <button class="primary-button" @click="startPlanning">
                <Send :size="20" /> 开始规划
              </button>
            </div>
          </div>
        </div>
      </div>

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
