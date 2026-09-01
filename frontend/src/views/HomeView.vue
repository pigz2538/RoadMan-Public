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
import {
  createVehicle,
  deleteVehicle,
  listVehicles,
  updateVehicle,
  type Vehicle,
  type VehicleCatalogItem,
  type VehicleInput,
  searchVehicleCatalog,
} from '../api/vehicles'
import type { Trip } from '../types/trip'

const router = useRouter()
const PROMPT_STORAGE_KEY = 'roadman:last-trip-prompt'
const promptSuggestion = '周六早上从武汉出发，去庐山两天一夜，周日晚八点前回来，喜欢自然景观'
const prompt = ref('')
const historyOpen = ref(false)
const historyTrips = ref<Trip[]>([])
const historyDeletingId = ref<string | null>(null)
const historySelectedIds = ref<string[]>([])
const historyBulkDeleting = ref(false)
const weather = ref({ temperature: '--', condition: '晴', location: '武汉' })
const activeMenu = ref('账户设置')
const drawerOpen = ref(false)
const accountMenuOpen = ref(false)
const vehicles = ref<Vehicle[]>([])
const currentVehicleId = ref('')
const vehicleBusy = ref(false)
const vehicleError = ref('')
const vehicleFormOpen = ref(false)
const vehicleEditingId = ref<string | null>(null)
const vehicleCatalogQuery = ref('')
const vehicleCatalogResults = ref<VehicleCatalogItem[]>([])
const vehicleCatalogBusy = ref(false)
const vehicleCatalogError = ref('')
const VEHICLE_STORAGE_KEY = 'roadman:current-vehicle-id'
const modelEnabled = ref(true)
const modelLoaded = ref(false)
const modelError = ref(false)
// Use the user-provided McLaren asset as the default hero model.  The white
// concept car is kept in public/models only as a compatibility asset; it must
// never silently replace the selected model in the normal home experience.
const modelSource = ref('/models/mclaren.glb')
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
const allHistorySelected = computed(() =>
  historyTrips.value.length > 0
  && historyTrips.value.every((trip) => historySelectedIds.value.includes(trip.id)),
)
const currentVehicle = computed(() => vehicles.value.find((vehicle) => vehicle.id === currentVehicleId.value) || vehicles.value[0])
const availableRange = computed(() => {
  const vehicle = currentVehicle.value
  if (!vehicle?.rated_range_km || vehicle.current_energy_percent == null) return 450
  return Math.max(0, Math.round(vehicle.rated_range_km * vehicle.current_energy_percent / 100))
})

function catalogSpec(item: VehicleCatalogItem, ...labels: string[]): string | null {
  const specs = item.specifications || []
  const match = specs.find((spec) => labels.some((label) => spec.name.includes(label)))
  return match ? `${match.name} ${match.value}` : null
}

function catalogSummary(item: VehicleCatalogItem): string {
  const summary = [
    item.rated_range_km
      ? `${item.estimated_fields?.includes('rated_range_km') ? '车型标称续航' : '续航'} ${item.rated_range_km} km`
      : catalogSpec(item, '续航'),
    item.consumption_per_100km ? `能耗 ${item.consumption_per_100km}/100km` : catalogSpec(item, '耗电', '油耗'),
    item.battery_kwh ? `电池 ${item.battery_kwh} kWh` : catalogSpec(item, '电池能量', '电池容量'),
    item.dc_charge_time_hours ? `直流快充 30-80% ${item.dc_charge_time_hours} h` : catalogSpec(item, '直流快充', 'DC charging', 'Charging time'),
    item.seats ? `${item.seats} 座` : catalogSpec(item, '车身结构', '座位数'),
  ].filter(Boolean)
  return summary.length
    ? summary.join(' · ')
    : '该年款详情源暂未返回续航、电池与能耗，选择后需补充确认'
}

function newVehicleDraft(): VehicleInput {
  return {
    brand: 'RoadMan',
    series: '纯电 SUV',
    model: 'RoadMan 纯电 SUV',
    power_type: 'electric',
    rated_range_km: 560,
    current_energy_percent: 80,
    battery_kwh: 82,
    consumption_per_100km: 18,
    max_charge_kw: 180,
    seats: 5,
    has_etc: true,
    mountain_ready: true,
    unpaved_ready: false,
    safe_energy_reserve_percent: 15,
  }
}

function blankVehicleDraft(): VehicleInput {
  return {
    ...newVehicleDraft(),
    brand: '',
    series: '',
    model: '',
    rated_range_km: undefined,
    battery_kwh: undefined,
    consumption_per_100km: undefined,
    max_charge_kw: undefined,
    specifications: [],
  }
}

const vehicleDraft = ref<VehicleInput>(newVehicleDraft())

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
  void loadVehicles()
  void loadHomeWeather()
  // McLaren uses Draco mesh compression. Keep its decoder inside the frontend
  // image so the 3D car does not depend on gstatic/CDN availability at runtime.
  const modelViewerGlobal = window as typeof window & {
    ModelViewerElement?: { dracoDecoderLocation?: string }
  }
  modelViewerGlobal.ModelViewerElement = {
    ...modelViewerGlobal.ModelViewerElement,
    dracoDecoderLocation: '/draco/',
  }
  void import('@google/model-viewer').then(({ ModelViewerElement }) => {
    // The model is intentionally loaded, but keep adaptive rendering bounded.
    // model-viewer clamps this value to a safe minimum of 0.25.
    ModelViewerElement.minimumRenderScale = 0.25
  }).catch(() => {
    modelError.value = true
  })
})

async function loadVehicles() {
  try {
    let result = await listVehicles()
    if (!result.length) {
      // Keep the drawer useful on a new installation while still persisting a
      // real profile for the planning backend to consume.
      result = [await createVehicle(newVehicleDraft())]
    }
    vehicles.value = result
    const remembered = window.localStorage.getItem(VEHICLE_STORAGE_KEY)
    currentVehicleId.value = result.some((vehicle) => vehicle.id === remembered)
      ? remembered || result[0].id
      : result[0].id
    window.localStorage.setItem(VEHICLE_STORAGE_KEY, currentVehicleId.value)
  } catch (error) {
    // The home page can still be used when the API is offline; editing will
    // surface the concrete request error instead of hiding the drawer.
    vehicles.value = [{ id: 'local-default', ...newVehicleDraft() } as Vehicle]
    currentVehicleId.value = 'local-default'
    vehicleError.value = error instanceof Error ? error.message : '车辆列表暂不可用'
  }
}

function selectVehicle(vehicleId: string) {
  currentVehicleId.value = vehicleId
  window.localStorage.setItem(VEHICLE_STORAGE_KEY, vehicleId)
}

function beginAddVehicle() {
  vehicleEditingId.value = null
  vehicleDraft.value = blankVehicleDraft()
  vehicleError.value = ''
  resetVehicleCatalog()
  vehicleFormOpen.value = true
}

function beginEditVehicle(vehicle: Vehicle) {
  // The offline placeholder is not persisted; editing it creates the first
  // real server profile instead of issuing a PATCH for a synthetic id.
  vehicleEditingId.value = vehicle.id === 'local-default' ? null : vehicle.id
  vehicleDraft.value = vehicle.id === 'local-default'
    ? { ...vehicle, id: undefined }
    : { ...vehicle }
  vehicleError.value = ''
  resetVehicleCatalog()
  vehicleFormOpen.value = true
}

function resetVehicleCatalog() {
  vehicleCatalogQuery.value = ''
  vehicleCatalogResults.value = []
  vehicleCatalogBusy.value = false
  vehicleCatalogError.value = ''
}

function cancelVehicleForm() {
  vehicleFormOpen.value = false
  vehicleEditingId.value = null
  vehicleDraft.value = newVehicleDraft()
  resetVehicleCatalog()
}

async function searchVehicleModels() {
  const query = vehicleCatalogQuery.value.trim()
  if (query.length < 2) {
    vehicleCatalogError.value = '请输入至少两个字符，例如“特斯拉 Model 3”或“比亚迪”'
    vehicleCatalogResults.value = []
    return
  }
  if (vehicleCatalogBusy.value) return
  vehicleCatalogBusy.value = true
  vehicleCatalogError.value = ''
  try {
    const result = await searchVehicleCatalog(query)
    vehicleCatalogResults.value = result.items
    if (!result.items.length) vehicleCatalogError.value = '没有找到具体车型，请换一个品牌、车系或车型关键词'
  } catch (error) {
    vehicleCatalogResults.value = []
    vehicleCatalogError.value = error instanceof Error ? error.message : '车型数据库暂不可用'
  } finally {
    vehicleCatalogBusy.value = false
  }
}

function applyVehicleCatalogItem(item: VehicleCatalogItem) {
  const existing = vehicleDraft.value
  const editing = Boolean(vehicleEditingId.value)
  const sameCatalogRecord = editing && existing.source_id === item.source_id
  vehicleDraft.value = {
    ...existing,
    brand: item.brand,
    series: item.series,
    model: item.model,
    year: item.year ?? undefined,
    power_type: item.power_type,
    // The catalog does not promise trim-specific technical specs. Preserve
    // verified values while editing; a new vehicle leaves them blank so the
    // user cannot mistake a demo SUV's values for this model's values.
    rated_range_km: item.rated_range_km ?? (sameCatalogRecord ? existing.rated_range_km : undefined),
    battery_kwh: item.battery_kwh ?? (sameCatalogRecord ? existing.battery_kwh : undefined),
    consumption_per_100km: item.consumption_per_100km ?? (sameCatalogRecord ? existing.consumption_per_100km : undefined),
    max_charge_kw: item.max_charge_kw ?? (sameCatalogRecord ? existing.max_charge_kw : undefined),
    dc_charge_time_hours: item.dc_charge_time_hours ?? (sameCatalogRecord ? existing.dc_charge_time_hours : undefined),
    height_m: item.height_m ?? (sameCatalogRecord ? existing.height_m : undefined),
    width_m: item.width_m ?? (sameCatalogRecord ? existing.width_m : undefined),
    seats: item.seats ?? existing.seats ?? 5,
    current_energy_percent: item.current_energy_percent ?? existing.current_energy_percent ?? 80,
    source_id: item.source_id,
    source_url: item.source_url,
    detail_source_url: item.detail_source_url,
    price_min_cny: item.price_min_cny,
    price_max_cny: item.price_max_cny,
    specifications: item.specifications || (sameCatalogRecord ? existing.specifications : []) || [],
  }
  vehicleCatalogError.value = item.specs_missing?.length
    ? `已填入 ${item.brand} ${item.model}。${item.specs_missing.join('、')}需按具体配置确认。`
    : `已填入 ${item.brand} ${item.model}，请确认车辆参数后保存。`
}

async function addVehicleFromCatalog(item: VehicleCatalogItem) {
  if (vehicleBusy.value) return
  applyVehicleCatalogItem(item)
  await saveVehicle()
}

async function saveVehicle() {
  if (vehicleBusy.value) return
  vehicleBusy.value = true
  vehicleError.value = ''
  try {
    const saved = vehicleEditingId.value
      ? await updateVehicle(vehicleEditingId.value, vehicleDraft.value)
      : await createVehicle(vehicleDraft.value)
    const existingIndex = vehicles.value.findIndex((vehicle) => vehicle.id === saved.id)
    if (existingIndex >= 0) vehicles.value.splice(existingIndex, 1, saved)
    else vehicles.value.unshift(saved)
    selectVehicle(saved.id)
    cancelVehicleForm()
  } catch (error) {
    vehicleError.value = error instanceof Error ? error.message : '保存车辆失败'
  } finally {
    vehicleBusy.value = false
  }
}

async function removeVehicle(vehicle: Vehicle) {
  if (vehicleBusy.value || !window.confirm(`确定删除“${vehicle.brand} ${vehicle.model}”吗？`)) return
  vehicleBusy.value = true
  vehicleError.value = ''
  try {
    if (vehicle.id !== 'local-default') await deleteVehicle(vehicle.id)
    vehicles.value = vehicles.value.filter((item) => item.id !== vehicle.id)
    if (!vehicles.value.length) {
      vehicles.value = [{ id: 'local-default', ...newVehicleDraft() } as Vehicle]
    }
    if (currentVehicleId.value === vehicle.id) selectVehicle(vehicles.value[0].id)
    if (vehicleEditingId.value === vehicle.id) cancelVehicleForm()
  } catch (error) {
    vehicleError.value = error instanceof Error ? error.message : '删除车辆失败'
  } finally {
    vehicleBusy.value = false
  }
}

async function loadHistory() {
  try {
    const trips = await listTrips()
    historyTrips.value = trips
      .filter((trip) => trip.id !== 'trip_wuhan_lushan_demo')
      .sort((left, right) => right.id.localeCompare(left.id))
    historySelectedIds.value = historySelectedIds.value.filter((id) => historyTrips.value.some((trip) => trip.id === id))
  } catch {
    historyTrips.value = []
    historySelectedIds.value = []
  }
}

function toggleHistorySelection(id: string) {
  historySelectedIds.value = historySelectedIds.value.includes(id)
    ? historySelectedIds.value.filter((item) => item !== id)
    : [...historySelectedIds.value, id]
}

function toggleSelectAllHistory() {
  historySelectedIds.value = allHistorySelected.value ? [] : historyTrips.value.map((trip) => trip.id)
}

async function deleteHistoryIds(ids: string[], confirmation: string) {
  const uniqueIds = [...new Set(ids)].filter((id) => historyTrips.value.some((trip) => trip.id === id))
  if (!uniqueIds.length || historyBulkDeleting.value) return
  if (!window.confirm(confirmation)) return
  historyBulkDeleting.value = true
  try {
    for (const id of uniqueIds) await deleteTrip(id)
    historySelectedIds.value = historySelectedIds.value.filter((id) => !uniqueIds.includes(id))
    await loadHistory()
  } catch {
    window.alert('批量删除失败，请稍后重试。')
    await loadHistory()
  } finally {
    historyBulkDeleting.value = false
  }
}

async function clearSelectedHistory() {
  await deleteHistoryIds(
    historySelectedIds.value,
    `确定删除选中的 ${historySelectedIds.value.length} 条历史规划吗？删除后无法恢复。`,
  )
}

async function clearAllHistory() {
  await deleteHistoryIds(
    historyTrips.value.map((trip) => trip.id),
    `确定清空全部 ${historyTrips.value.length} 条历史规划吗？删除后无法恢复。`,
  )
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
  const historyQuery = 'history=1'
  if (trip.status === 'collecting' || trip.status === 'planning') {
    router.push(`/trips/${trip.id}/plan?planning=1&${historyQuery}`)
  } else {
    router.push(`/trips/${trip.id}/plan?${historyQuery}`)
  }
}

async function removeHistoryTrip(trip: Trip) {
  if (historyBulkDeleting.value) return
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

async function checkPreflight(
  confirmed = false,
  answersOverride?: Record<string, string>,
) {
  if (planning.value || preflightChecking.value || !prompt.value.trim()) return
  planningError.value = ''
  try {
    preflightChecking.value = true
    // Snapshot the answers before awaiting the network request.  A cloud
    // extraction round can resolve quickly enough to update the panel while
    // the user is still on the same question; passing a stable copy prevents
    // that render from dropping the answer that triggered this check.
    const answers = { ...(answersOverride ?? clarificationAnswers.value) }
    preflight.value = await preflightTrip(
      prompt.value.trim(),
      answers,
      confirmed,
      preflight.value?.extracted,
      preflight.value?.semantic_checked,
    )
    clarificationIndex.value = 0
    if (!preflight.value.ready) return
    planning.value = true
    const trip = await createTrip(
      prompt.value.trim(),
      preflight.value.extracted,
      currentVehicleId.value && currentVehicleId.value !== 'local-default' ? currentVehicleId.value : undefined,
    )
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
  const answer = clarificationAnswers.value[key]?.trim() || ''
  if (!answer) {
    planningError.value = '请先回答当前问题。'
    return
  }
  planningError.value = ''
  if (clarificationIndex.value < (preflight.value?.issues.length ?? 1) - 1) {
    clarificationIndex.value += 1
    return
  }
  await checkPreflight(false, {
    ...clarificationAnswers.value,
    [key]: answer,
  })
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
  <main class="home-shell" :class="{ 'home-confirming': Boolean(preflight && !preflight.ready) }">
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
        <span><CarFront class="mint-icon" /> <strong>{{ availableRange }}</strong> km <small>估算可用</small></span>
        <button class="history-button" type="button" @click="historyOpen = !historyOpen">
          <History :size="20" />历史规划<span>{{ historyTrips.length }}</span>
        </button>
        <button class="icon-button notification" aria-label="通知"><Bell /><i /></button>
      </div>
    </header>

    <Transition name="menu">
      <aside v-if="historyOpen" class="history-popover glass-card" aria-label="历史规划">
        <header>
          <strong>历史规划</strong>
          <div class="history-header-actions">
            <button type="button" @click="loadHistory">刷新</button>
            <button
              type="button"
              :disabled="!historyTrips.length || historyBulkDeleting"
              @click="clearAllHistory"
            >清空全部</button>
          </div>
        </header>
        <div v-if="historyTrips.length" class="history-bulk-actions">
          <button type="button" @click="toggleSelectAllHistory">
            {{ allHistorySelected ? '取消全选' : '全选' }}
          </button>
          <button
            type="button"
            :disabled="!historySelectedIds.length || historyBulkDeleting"
            @click="clearSelectedHistory"
          >删除选中{{ historySelectedIds.length ? `（${historySelectedIds.length}）` : '' }}</button>
        </div>
        <p v-if="!historyTrips.length" class="history-empty">还没有保存的规划，完成一次规划后会自动保存在这里。</p>
        <div
          v-for="trip in historyTrips"
          :key="trip.id"
          :class="['history-item', { selected: historySelectedIds.includes(trip.id) }]"
          role="button"
          tabindex="0"
          @click="openHistoryTrip(trip)"
          @keydown.enter="openHistoryTrip(trip)"
        >
          <input
            class="history-select"
            type="checkbox"
            :checked="historySelectedIds.includes(trip.id)"
            :disabled="historyBulkDeleting"
            :aria-label="`选择历史规划：${trip.title}`"
            @click.stop
            @change="toggleHistorySelection(trip.id)"
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
              :disabled="historyDeletingId === trip.id || historyBulkDeleting"
              :aria-busy="historyDeletingId === trip.id || historyBulkDeleting"
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
           :src="modelSource"
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
          <span>车辆模型渲染失败，已暂停渲染以保护显卡</span>
          <button type="button" class="secondary-button" @click="modelError = false; modelLoaded = false; modelEnabled = true">重新加载车辆模型</button>
        </div>
        <span class="sr-only">{{ modelLoaded ? '3D 模型已加载' : modelError ? '3D 模型加载失败' : '正在加载 3D 模型' }}</span>
      </div>
    </section>

    <section class="planner-area">
      <div class="section-title">
        <span>从一句话开始</span>
        <h1>我是您的自驾游规划 <em>智能体</em></h1>
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
          <span>智能体需要向您确认</span>
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
            <div>
              <dt>路线</dt>
              <dd>
                {{ preflight.summary.origin_name }} →
                {{ preflight.summary.destination_names?.length
                  ? preflight.summary.destination_names.join('、')
                  : preflight.summary.destination_name }}
              </dd>
            </div>
            <div v-if="preflight.summary.destination_scope && preflight.summary.destination_scope !== 'unknown'"><dt>目的地范围</dt><dd>{{ preflight.summary.destination_scope === 'province' ? '省域策划' : preflight.summary.destination_scope === 'city' ? '城市策划' : preflight.summary.destination_scope === 'multi_destination' ? '多目的地策划' : '地点策划' }}</dd></div>
            <div v-if="preflight.summary.travel_intents?.length"><dt>出行目的</dt><dd>{{ preflight.summary.travel_intents.join('、') }}</dd></div>
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
            <header><strong>智能体已核对特殊活动</strong><small>请根据来源中的窗口选择日期</small></header>
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
          <div class="vehicle-drawer-heading">
            <span class="status-pill">当前车辆</span>
            <button type="button" class="text-button" @click="beginAddVehicle">添加车型</button>
          </div>
          <p v-if="vehicleError" class="vehicle-error">{{ vehicleError }}</p>
          <div class="vehicle-list">
            <article
              v-for="vehicle in vehicles"
              :key="vehicle.id"
              :class="['vehicle-item', { selected: vehicle.id === currentVehicle?.id }]"
              @click="selectVehicle(vehicle.id)"
            >
              <button type="button" class="vehicle-item-main" @click.stop="selectVehicle(vehicle.id)">
                <strong>{{ vehicle.brand }} {{ vehicle.model }}</strong>
                <small>{{ vehicle.power_type === 'electric' ? '纯电' : vehicle.power_type === 'hybrid' ? '混动' : '燃油' }} · {{ vehicle.seats }} 座</small>
              </button>
              <div class="vehicle-item-actions">
                <button type="button" title="编辑车型" @click.stop="beginEditVehicle(vehicle)">编辑</button>
                <button type="button" title="删除车型" @click.stop="removeVehicle(vehicle)">删除</button>
              </div>
            </article>
          </div>
          <template v-if="currentVehicle">
            <h3>{{ currentVehicle.brand }} {{ currentVehicle.model }}</h3>
            <dl>
              <div><dt>额定续航</dt><dd>{{ currentVehicle.rated_range_km || '--' }} km</dd></div>
              <div><dt>当前电量</dt><dd>{{ currentVehicle.current_energy_percent ?? '--' }}%</dd></div>
              <div><dt>估算可用</dt><dd>{{ availableRange }} km</dd></div>
              <div><dt>座位数</dt><dd>{{ currentVehicle.seats }}</dd></div>
            </dl>
            <div v-if="currentVehicle.specifications?.length" class="vehicle-specifications" aria-label="已获取的车型参数">
              <span v-for="spec in currentVehicle.specifications.slice(0, 12)" :key="`${spec.name}-${spec.value}`">
                {{ spec.name }}：{{ spec.value }}
              </span>
            </div>
          </template>
          <form v-if="vehicleFormOpen" class="vehicle-form" @submit.prevent="saveVehicle">
            <strong>{{ vehicleEditingId ? '编辑车型' : '添加车型' }}</strong>
            <section class="vehicle-catalog" aria-label="车型数据库搜索">
              <div class="vehicle-catalog-head">
                <strong>一键搜索具体车型</strong>
                <small>车型资料智能体 · 汽车品牌/车系/年款数据库</small>
              </div>
              <div class="vehicle-catalog-search-row">
                <input
                  v-model="vehicleCatalogQuery"
                  type="search"
                  placeholder="搜索品牌、车系或具体车型，例如 特斯拉 Model 3"
                  aria-label="搜索具体车型"
                  @keydown.enter.prevent="searchVehicleModels"
                >
                <button type="button" :disabled="vehicleCatalogBusy" @click="searchVehicleModels">
                  {{ vehicleCatalogBusy ? '查询中…' : '搜索' }}
                </button>
              </div>
              <small class="vehicle-catalog-note">选择结果会自动填入品牌、车系、动力和年款；续航、能耗等配置以你的具体版本为准。</small>
              <div v-if="vehicleCatalogResults.length" class="vehicle-catalog-results-head">
                <span>找到 {{ vehicleCatalogResults.length }} 个具体年款</span>
                <small>在下方区域滚动查看更多</small>
              </div>
              <div
                v-if="vehicleCatalogResults.length"
                class="vehicle-catalog-results"
                role="listbox"
                tabindex="0"
                aria-label="车型搜索结果，可上下滚动"
              >
                <div
                  v-for="item in vehicleCatalogResults"
                  :key="item.id"
                  class="vehicle-catalog-result"
                >
                  <button type="button" class="vehicle-catalog-result-main" @click="applyVehicleCatalogItem(item)">
                    <span>
                    <strong>{{ item.brand }} · {{ item.series }}</strong>
                    <small>{{ item.model }} · {{ item.year || '年款待核实' }} · {{ item.state_label || '状态待核实' }}</small>
                    <small class="vehicle-catalog-specs">{{ catalogSummary(item) }}</small>
                    <small v-if="item.catalog_source" class="vehicle-catalog-source">资料：{{ item.catalog_source }}</small>
                    </span>
                    <em>填入</em>
                  </button>
                  <button type="button" class="vehicle-catalog-result-add" :disabled="vehicleBusy" @click="addVehicleFromCatalog(item)">
                    {{ vehicleEditingId ? '更新' : '直接添加' }}
                  </button>
                </div>
              </div>
              <small v-if="vehicleCatalogError" class="vehicle-catalog-error">{{ vehicleCatalogError }}</small>
            </section>
            <div class="vehicle-form-grid">
              <label class="vehicle-field"><span>品牌</span><input v-model="vehicleDraft.brand" required placeholder="例如：小鹏汽车"></label>
              <label class="vehicle-field"><span>车系</span><input v-model="vehicleDraft.series" required placeholder="例如：小鹏 P7"></label>
              <label class="vehicle-field"><span>具体车型 / 年款</span><input v-model="vehicleDraft.model" required placeholder="请选择搜索结果"></label>
              <label class="vehicle-field"><span>动力类型</span><select v-model="vehicleDraft.power_type">
                  <option value="electric">纯电</option>
                  <option value="hybrid">混动</option>
                  <option value="fuel">燃油</option>
                </select></label>
              <label class="vehicle-field"><span>额定续航（km）</span><input v-model.number="vehicleDraft.rated_range_km" type="number" min="1" placeholder="由具体年款自动填入"></label>
              <label class="vehicle-field"><span>电池容量（kWh）</span><input v-model.number="vehicleDraft.battery_kwh" type="number" min="1" placeholder="由具体年款自动填入"></label>
              <label class="vehicle-field"><span>当前电量（%）</span><input v-model.number="vehicleDraft.current_energy_percent" type="number" min="0" max="100" placeholder="80"></label>
              <label class="vehicle-field"><span>百公里能耗</span><input v-model.number="vehicleDraft.consumption_per_100km" type="number" min="1" placeholder="电耗 kWh / 油耗 L"></label>
              <label class="vehicle-field"><span>直流快充 30-80%（小时）</span><input v-model.number="vehicleDraft.dc_charge_time_hours" type="number" min="0.01" step="0.01" placeholder="公开年款有数据时自动填入"></label>
              <label class="vehicle-field"><span>座位数</span><input v-model.number="vehicleDraft.seats" type="number" min="1" max="20" placeholder="5"></label>
              <label class="vehicle-field"><span>安全余量（%）</span><input v-model.number="vehicleDraft.safe_energy_reserve_percent" type="number" min="5" max="40" placeholder="15"></label>
            </div>
            <label class="vehicle-check"><input v-model="vehicleDraft.has_etc" type="checkbox"> 已办理 ETC</label>
            <label class="vehicle-check"><input v-model="vehicleDraft.mountain_ready" type="checkbox"> 适合山路</label>
            <div class="vehicle-form-actions">
              <button type="button" class="secondary-button" @click="cancelVehicleForm">取消</button>
              <button type="submit" class="primary-button" :disabled="vehicleBusy">{{ vehicleBusy ? '保存中…' : '保存车型' }}</button>
            </div>
          </form>
        </template>
        <p v-else>该模块已保留静态交互入口，将在对应阶段接入正式设置表单。</p>
      </aside>
    </Transition>
  </main>
</template>
