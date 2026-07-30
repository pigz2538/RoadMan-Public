<script setup lang="ts">
import AMapLoader from '@amap/amap-jsapi-loader'
import { computed, nextTick, onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import localAmapKey from '../../../../Skills/amap-jsapi/apikey.txt?raw'
import localSecurityCode from '../../../../Skills/amap-jsapi/secretkey.txt?raw'
import type { DayPlan } from '../../types/trip'
import MockRouteMap from './MockRouteMap.vue'

const props = defineProps<{ day?: DayPlan; activeStageId?: string }>()
const emit = defineEmits<{ selectStage: [stageId: string] }>()

const containerId = `roadman-amap-${Math.random().toString(36).slice(2)}`
const map = shallowRef<any>(null)
const AMap = shallowRef<any>(null)
const routeOverlays = shallowRef<Array<{ stageId: string; polyline: any }>>([])
const otherOverlays = shallowRef<any[]>([])
const routePathCache = shallowRef(new Map<string, any[]>())
const loading = shallowRef(true)
const routeLoading = shallowRef(false)
const routeUnavailable = shallowRef(false)
const failed = shallowRef(false)
const amapKey = (import.meta.env.VITE_AMAP_JSAPI_KEY || localAmapKey).trim()
const securityCode = (import.meta.env.VITE_AMAP_SECURITY_JS_CODE || localSecurityCode).trim()
const serviceHost = (import.meta.env.VITE_AMAP_SERVICE_HOST || '').trim()
const hasCredentials = computed(() => Boolean(amapKey))

function drivingPath(stage: NonNullable<typeof props.day>['stages'][number]): Promise<any[]> {
  const cached = routePathCache.value.get(stage.id)
  if (cached) return Promise.resolve(cached)
  const start = stage.origin.coordinates
  const end = stage.destination.coordinates
  if (!start || !end || !AMap.value?.Driving) return Promise.resolve([])

  return new Promise((resolve) => {
    let settled = false
    const finish = (path: any[]) => {
      if (settled) return
      settled = true
      const value = path.length >= 2 ? path : []
      routePathCache.value.set(stage.id, value)
      resolve(value)
    }
    const timeout = window.setTimeout(() => finish([]), 12_000)
    const driving = new AMap.value.Driving({
      policy: AMap.value.DrivingPolicy.LEAST_TIME,
      extensions: 'base',
      hideMarkers: true,
      showTraffic: false,
    })
    const waypoints = stage.waypoints
      .filter((place) => place.coordinates)
      .map((place) => new AMap.value.LngLat(
        place.coordinates!.longitude,
        place.coordinates!.latitude,
      ))
    driving.search(
      new AMap.value.LngLat(start.longitude, start.latitude),
      new AMap.value.LngLat(end.longitude, end.latitude),
      { waypoints },
      (status: string, result: any) => {
        window.clearTimeout(timeout)
        if (status !== 'complete' || !result?.routes?.[0]?.steps) {
          finish([])
          return
        }
        finish(result.routes[0].steps.flatMap((step: any) => step.path ?? []))
      },
    )
  })
}

let renderVersion = 0
async function renderRoutes() {
  if (!map.value || !AMap.value || !props.day) return
  const version = ++renderVersion
  routeLoading.value = true
  const plannedPaths = await Promise.all(
    props.day.stages.map(async (stage) => ({ stage, path: await drivingPath(stage) })),
  )
  if (version !== renderVersion || !map.value) return
  routeUnavailable.value = plannedPaths.some(({ path }) => path.length < 2)
  map.value.remove([
    ...routeOverlays.value.map((item) => item.polyline),
    ...otherOverlays.value,
  ])
  routeOverlays.value = []
  otherOverlays.value = []

  for (const { stage, path } of plannedPaths) {
    const unavailable = path.length < 2
    const start = stage.origin.coordinates
    const end = stage.destination.coordinates
    const displayPath = unavailable && start && end
      ? [[start.longitude, start.latitude], [end.longitude, end.latitude]]
      : path
    if (displayPath.length < 2) continue
    const active = stage.id === props.activeStageId
    const polyline = new AMap.value.Polyline({
      path: displayPath,
      isOutline: active && !unavailable,
      outlineColor: '#c7e2ff',
      borderWeight: 3,
      strokeColor: unavailable ? '#9aa5b4' : active ? '#1677ff' : '#8d98a8',
      strokeOpacity: unavailable ? 0.72 : active ? 0.94 : 0.58,
      strokeWeight: unavailable ? 3 : active ? 6 : 4,
      strokeStyle: unavailable ? 'dashed' : 'solid',
      strokeDasharray: unavailable ? [8, 8] : undefined,
      lineJoin: 'round',
      lineCap: 'round',
      showDir: !unavailable,
      zIndex: active ? 80 : 50,
      extData: { stageId: stage.id, unavailable },
    })
    polyline.on('click', () => emit('selectStage', stage.id))
    routeOverlays.value.push({ stageId: stage.id, polyline })
  }

  const places = [
    props.day.stages[0]?.origin,
    ...props.day.stages.map((stage) => stage.destination),
    ...props.day.activities.map((activity) => activity.place),
  ].filter((place, index, items) =>
    place?.coordinates && items.findIndex((item) => item?.name === place.name) === index,
  )
  for (const [index, place] of places.entries()) {
    if (!place?.coordinates) continue
    const marker = new AMap.value.Marker({
      position: [place.coordinates.longitude, place.coordinates.latitude],
      title: place.name,
      content: `<div class="amap-number-marker"><b>${index + 1}</b><span>${place.name}</span></div>`,
      offset: new AMap.value.Pixel(-16, -38),
      zIndex: 100 + index,
    })
    otherOverlays.value.push(marker)
  }

  const overlays = [
    ...routeOverlays.value.map((item) => item.polyline),
    ...otherOverlays.value,
  ]
  map.value.add(overlays)
  if (overlays.length) map.value.setFitView(overlays, false, [65, 65, 65, 65], 9)
  routeLoading.value = false
}

async function initMap() {
  if (!hasCredentials.value) {
    loading.value = false
    failed.value = true
    return
  }
  try {
    window._AMapSecurityConfig = serviceHost
      ? { serviceHost }
      : { securityJsCode: securityCode }
    const instance = await AMapLoader.load({
      key: amapKey,
      version: '2.0',
      plugins: ['AMap.Scale', 'AMap.ToolBar', 'AMap.Driving'],
    })
    if (typeof instance.getConfig === 'function') {
      instance.getConfig().appname = 'roadman-amap-jsapi'
    }
    AMap.value = instance
    await nextTick()
    map.value = new instance.Map(containerId, {
      viewMode: '3D',
      zoom: 8,
      center: [115.22, 30.04],
      pitch: 16,
      mapStyle: 'amap://styles/whitesmoke',
      dragEnable: true,
      zoomEnable: true,
      scrollWheel: true,
      doubleClickZoom: true,
    })
    map.value.addControl(new instance.Scale())
    map.value.addControl(new instance.ToolBar({ position: { right: '12px', top: '12px' } }))
    map.value.on('complete', () => {
      loading.value = false
      void renderRoutes()
    })
  } catch (error) {
    console.warn(
      'RoadMan 高德地图初始化失败：',
      error instanceof Error ? error.message : '未知错误',
    )
    loading.value = false
    failed.value = true
  }
}

watch(
  () => [props.day?.id, props.activeStageId],
  () => void renderRoutes(),
)
onMounted(initMap)
onBeforeUnmount(() => {
  map.value?.destroy()
  map.value = null
  AMap.value = null
})
</script>

<template>
  <div class="amap-shell">
    <div v-show="!failed" :id="containerId" class="amap-container" />
    <MockRouteMap v-if="failed" :day="day" :active-stage-id="activeStageId" />
    <div v-if="loading" class="map-loading"><i />正在加载高德地图…</div>
    <div v-if="failed" class="map-fallback-badge">高德地图不可用 · 已切换 Mock 地图</div>
    <div v-else-if="!loading" class="map-live-badge" :class="{ warning: routeUnavailable }">
      {{ routeLoading ? '高德驾车路线计算中…' : routeUnavailable ? '部分路线不可用 · 灰色虚线直连' : '高德 JSAPI · 真实道路轨迹' }}
    </div>
    <div v-if="!failed && !loading" class="map-legend amap-live-legend">
      <span><i class="active-route" />当前阶段</span>
      <span><i class="day-route" />当日其他阶段</span>
      <span><i class="risk-route" />风险</span>
    </div>
  </div>
</template>
