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
type TravelMode = 'driving' | 'riding' | 'walking' | 'transit'
type PlannedRoute = { path: any[]; mode: TravelMode | 'direct'; fallback: boolean }
const routePathCache = shallowRef(new Map<string, PlannedRoute>())
const loading = shallowRef(true)
const routeLoading = shallowRef(false)
const routeUnavailable = shallowRef(false)
const failed = shallowRef(false)
const amapKey = (import.meta.env.VITE_AMAP_JSAPI_KEY || localAmapKey).trim()
const securityCode = (import.meta.env.VITE_AMAP_SECURITY_JS_CODE || localSecurityCode).trim()
const serviceHost = (import.meta.env.VITE_AMAP_SERVICE_HOST || '').trim()
const hasCredentials = computed(() => Boolean(amapKey))

function extractServicePath(mode: TravelMode, result: any): any[] {
  if (mode === 'transit') {
    return (result?.plans?.[0]?.segments ?? []).flatMap((segment: any) => [
      ...(segment.walking?.path ?? []),
      ...(segment.transit?.path ?? []),
    ])
  }
  const route = result?.routes?.[0]
  if (mode === 'riding') return (route?.rides ?? []).flatMap((ride: any) => ride.path ?? [])
  return (route?.steps ?? []).flatMap((step: any) => step.path ?? [])
}

function searchByMode(
  mode: TravelMode,
  start: { longitude: number; latitude: number },
  end: { longitude: number; latitude: number },
  city?: string,
  waypoints: Array<{ longitude: number; latitude: number }> = [],
): Promise<any[]> {
  return new Promise((resolve) => {
    const Service = AMap.value?.[
      { driving: 'Driving', riding: 'Riding', walking: 'Walking', transit: 'Transfer' }[mode]
    ]
    if (!Service || (mode === 'transit' && !city)) {
      resolve([])
      return
    }
    let settled = false
    const finish = (path: any[]) => {
      if (settled) return
      settled = true
      window.clearTimeout(timeout)
      resolve(path.length >= 2 ? path : [])
    }
    const timeout = window.setTimeout(() => finish([]), 6_000)
    const options = mode === 'driving'
      ? {
          policy: AMap.value.DrivingPolicy.LEAST_TIME,
          extensions: 'base',
          hideMarkers: true,
          showTraffic: false,
        }
      : mode === 'transit'
        ? { city, policy: AMap.value.TransferPolicy.LEAST_TIME, hideMarkers: true }
        : { hideMarkers: true }
    const service = new Service(options)
    const origin = new AMap.value.LngLat(start.longitude, start.latitude)
    const destination = new AMap.value.LngLat(end.longitude, end.latitude)
    const callback = (status: string, result: any) => {
      const servicePath = status === 'complete' ? extractServicePath(mode, result) : []
      finish(servicePath.length >= 2 ? [origin, ...servicePath, destination] : [])
    }
    if (mode === 'driving') {
      service.search(
        origin,
        destination,
        {
          waypoints: waypoints.map((point) => new AMap.value.LngLat(point.longitude, point.latitude)),
        },
        callback,
      )
    } else {
      service.search(origin, destination, callback)
    }
  })
}

async function routeWithFallback(
  cacheKey: string,
  start: { longitude: number; latitude: number },
  end: { longitude: number; latitude: number },
  city?: string,
  waypoints: Array<{ longitude: number; latitude: number }> = [],
  preferredMode: TravelMode = 'driving',
): Promise<PlannedRoute> {
  const cached = routePathCache.value.get(cacheKey)
  if (cached) return cached
  const modes = [
    preferredMode,
    ...(['driving', 'riding', 'walking', 'transit'] as TravelMode[]),
  ].filter((mode, index, items) => items.indexOf(mode) === index)
  for (const mode of modes) {
    const path = await searchByMode(mode, start, end, city, mode === 'driving' ? waypoints : [])
    if (path.length >= 2) {
      const result: PlannedRoute = { path, mode, fallback: mode !== preferredMode }
      routePathCache.value.set(cacheKey, result)
      return result
    }
  }
  const result: PlannedRoute = {
    path: [[start.longitude, start.latitude], [end.longitude, end.latitude]],
    mode: 'direct',
    fallback: true,
  }
  routePathCache.value.set(cacheKey, result)
  return result
}

function stagePath(stage: NonNullable<typeof props.day>['stages'][number]): Promise<PlannedRoute> {
  const start = stage.origin.coordinates
  const end = stage.destination.coordinates
  if (!start || !end) return Promise.resolve({ path: [], mode: 'direct', fallback: true })
  const sameCity = stage.origin.city && stage.origin.city === stage.destination.city
    ? stage.origin.city
    : undefined
  return routeWithFallback(
    stage.id,
    start,
    end,
    sameCity,
    stage.waypoints.flatMap((place) => place.coordinates ? [place.coordinates] : []),
    (['driving', 'riding', 'walking', 'transit'].includes(stage.mode)
      ? stage.mode
      : 'driving') as TravelMode,
  )
}

function itineraryConnectors() {
  if (!props.day) return []
  const stageById = new Map(props.day.stages.map((stage) => [stage.id, stage]))
  const activityById = new Map(props.day.activities.map((activity) => [activity.id, activity]))
  const pairs: Array<{
    id: string
    start: { longitude: number; latitude: number }
    end: { longitude: number; latitude: number }
    city?: string
  }> = []
  let previous: { name: string; city?: string; coordinates?: { longitude: number; latitude: number } } | undefined
  for (const item of props.day.items ?? []) {
    if (item.type === 'stage') {
      const stage = stageById.get(item.id)
      if (!stage) continue
      const start = stage.origin
      if (previous?.coordinates && start.coordinates) {
        const distance = Math.hypot(
          previous.coordinates.longitude - start.coordinates.longitude,
          previous.coordinates.latitude - start.coordinates.latitude,
        )
        if (distance > 0.0001) {
          pairs.push({
            id: `connector-${previous.name}-${start.name}`,
            start: previous.coordinates,
            end: start.coordinates,
            city: previous.city === start.city ? start.city : undefined,
          })
        }
      }
      previous = stage.destination
    } else {
      const activity = activityById.get(item.id)
      const place = activity?.place
      if (!place?.coordinates) continue
      if (previous?.coordinates) {
        const distance = Math.hypot(
          previous.coordinates.longitude - place.coordinates.longitude,
          previous.coordinates.latitude - place.coordinates.latitude,
        )
        if (distance > 0.0001) {
          pairs.push({
            id: `connector-${previous.name}-${place.name}`,
            start: previous.coordinates,
            end: place.coordinates,
            city: previous.city === place.city ? place.city : undefined,
          })
        }
      }
      previous = place
    }
  }
  return pairs
}

let renderVersion = 0
async function renderRoutes() {
  if (!map.value || !AMap.value || !props.day) return
  const version = ++renderVersion
  routeLoading.value = true
  const plannedPaths = await Promise.all(
    props.day.stages.map(async (stage) => ({ stage, route: await stagePath(stage) })),
  )
  const plannedConnectors = await Promise.all(
    itineraryConnectors().map(async (connector) => ({
      connector,
      route: await routeWithFallback(
        connector.id,
        connector.start,
        connector.end,
        connector.city,
      ),
    })),
  )
  if (version !== renderVersion || !map.value) return
  routeUnavailable.value = [...plannedPaths, ...plannedConnectors].some(({ route }) => route.mode === 'direct')
  map.value.remove([
    ...routeOverlays.value.map((item) => item.polyline),
    ...otherOverlays.value,
  ])
  routeOverlays.value = []
  otherOverlays.value = []

  const modeColor: Record<PlannedRoute['mode'], string> = {
    driving: '#1677ff',
    riding: '#f08a18',
    walking: '#22a66f',
    transit: '#7758d8',
    direct: '#9aa5b4',
  }
  for (const { stage, route } of plannedPaths) {
    const unavailable = route.mode === 'direct'
    const start = stage.origin.coordinates
    const end = stage.destination.coordinates
    const displayPath = unavailable && start && end && route.path.length < 2
      ? [[start.longitude, start.latitude], [end.longitude, end.latitude]]
      : route.path
    if (displayPath.length < 2) continue
    const active = stage.id === props.activeStageId
    const risky = stage.risk_level === 'high' || stage.risk_level === 'moderate'
    const polyline = new AMap.value.Polyline({
      path: displayPath,
      isOutline: active && !unavailable,
      outlineColor: risky ? '#ffe2b5' : '#c7e2ff',
      borderWeight: 3,
      strokeColor: unavailable ? '#9aa5b4' : risky ? '#f08a18' : active ? modeColor[route.mode] : '#8d98a8',
      strokeOpacity: unavailable ? 0.72 : active || risky ? 0.94 : 0.58,
      strokeWeight: unavailable ? 3 : active || risky ? 6 : 4,
      strokeStyle: unavailable ? 'dashed' : 'solid',
      strokeDasharray: unavailable ? [8, 8] : undefined,
      lineJoin: 'round',
      lineCap: 'round',
      showDir: !unavailable,
      zIndex: active ? 80 : risky ? 70 : 50,
      extData: { stageId: stage.id, unavailable, mode: route.mode, riskLevel: stage.risk_level },
    })
    polyline.on('click', () => emit('selectStage', stage.id))
    routeOverlays.value.push({ stageId: stage.id, polyline })
  }

  for (const { connector, route } of plannedConnectors) {
    if (route.path.length < 2) continue
    const unavailable = route.mode === 'direct'
    const polyline = new AMap.value.Polyline({
      path: route.path,
      strokeColor: modeColor[route.mode],
      strokeOpacity: unavailable ? 0.72 : 0.9,
      strokeWeight: unavailable ? 3 : 5,
      strokeStyle: unavailable ? 'dashed' : 'solid',
      strokeDasharray: unavailable ? [8, 8] : undefined,
      lineJoin: 'round',
      lineCap: 'round',
      showDir: !unavailable,
      zIndex: 70,
      extData: { connectorId: connector.id, unavailable, mode: route.mode },
    })
    routeOverlays.value.push({ stageId: connector.id, polyline })
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
      zIndex: 1000 + index,
    })
    otherOverlays.value.push(marker)
  }

  const overlays = [
    ...routeOverlays.value.map((item) => item.polyline),
    ...otherOverlays.value,
  ]
  map.value.add(overlays)
  const activeRoute = routeOverlays.value.find((item) => item.stageId === props.activeStageId)?.polyline
  if (activeRoute) {
    const [fitZoom, fitCenter] = map.value.getFitZoomAndCenterByOverlays(
      [activeRoute],
      [110, 110, 110, 110],
      15,
    )
    map.value.setZoomAndCenter(Math.max(3, fitZoom - 1.1), fitCenter, false, 1000)
  } else if (overlays.length) {
    const [fitZoom, fitCenter] = map.value.getFitZoomAndCenterByOverlays(
      overlays,
      [80, 80, 80, 80],
      9,
    )
    map.value.setZoomAndCenter(fitZoom, fitCenter, false, 1000)
  }
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
      plugins: [
        'AMap.Scale',
        'AMap.ToolBar',
        'AMap.Driving',
        'AMap.Riding',
        'AMap.Walking',
        'AMap.Transfer',
      ],
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
