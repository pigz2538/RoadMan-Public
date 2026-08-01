<script setup lang="ts">
import AMapLoader from '@amap/amap-jsapi-loader'
import { computed, nextTick, onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import localAmapKey from '../../../../Skills/amap-jsapi/apikey.txt?raw'
import localSecurityCode from '../../../../Skills/amap-jsapi/secretkey.txt?raw'
import type { DayPlan } from '../../types/trip'
import MockRouteMap from './MockRouteMap.vue'

const props = defineProps<{ day?: DayPlan; activeStageId?: string; pickEnabled?: boolean }>()
const emit = defineEmits<{
  selectStage: [stageId: string]
  selectActivity: [activityId: string]
  pointSelected: [point: { name: string; address?: string; longitude: number; latitude: number }]
}>()

const containerId = `roadman-amap-${Math.random().toString(36).slice(2)}`
const map = shallowRef<any>(null)
const AMap = shallowRef<any>(null)
const routeOverlays = shallowRef<Array<{ stageId: string; polyline: any }>>([])
const otherOverlays = shallowRef<any[]>([])
const pickedMarker = shallowRef<any>(null)
type TravelMode = 'driving' | 'riding' | 'walking' | 'transit'
type PlannedRoute = { path: any[]; mode: TravelMode | 'direct'; fallback: boolean }
type MarkerKind = 'start' | 'end' | 'attraction' | 'charging' | 'fueling' | 'meal' | 'rest' | 'hotel' | 'parking' | 'service'
const routePathCache = shallowRef(new Map<string, PlannedRoute>())
const loading = shallowRef(true)
const routeLoading = shallowRef(false)
const routeUnavailable = shallowRef(false)
const failed = shallowRef(false)
const amapKey = (import.meta.env.VITE_AMAP_JSAPI_KEY || localAmapKey).trim()
const securityCode = (import.meta.env.VITE_AMAP_SECURITY_JS_CODE || localSecurityCode).trim()
const serviceHost = (import.meta.env.VITE_AMAP_SERVICE_HOST || '').trim()
const hasCredentials = computed(() => Boolean(amapKey))

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;',
  })[character] ?? character)
}

function activityMarkerKind(type: string): MarkerKind {
  const normalized = type.toLowerCase()
  if (normalized.includes('charg')) return 'charging'
  if (normalized.includes('fuel') || normalized.includes('gas')) return 'fueling'
  if (normalized.includes('meal') || normalized.includes('food') || normalized.includes('restaurant')) return 'meal'
  if (normalized.includes('hotel') || normalized.includes('lodg')) return 'hotel'
  if (normalized.includes('park')) return 'parking'
  if (normalized.includes('rest') || normalized.includes('toilet')) return 'rest'
  if (normalized.includes('attraction') || normalized.includes('scenic')) return 'attraction'
  return 'service'
}

function markerIcon(kind: MarkerKind) {
  return {
    start: '起',
    end: '终',
    attraction: '景',
    charging: '⚡',
    fueling: '⛽',
    meal: '餐',
    rest: '休',
    hotel: '宿',
    parking: 'P',
    service: '服',
  }[kind]
}

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
  const persistedPath = stage.route_segments.flatMap((segment) =>
    segment.coordinates.map((point) => [point.longitude, point.latitude]),
  )
  if (persistedPath.length >= 2) {
    return Promise.resolve({
      path: persistedPath,
      mode: (['driving', 'riding', 'walking', 'transit'].includes(stage.mode)
        ? stage.mode
        : 'driving') as TravelMode,
      // A two-point estimated segment is the explicit direct-line fallback;
      // never ask JSAPI to redraw it and accidentally create another long
      // cross-city dashed connector.
      fallback: persistedPath.length === 2 && stage.route_segments.every((segment) => segment.estimated),
    })
  }
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
  type TimelinePoint = {
    kind: 'stage' | 'activity'
    id: string
    name: string
    city?: string
    coordinates?: { longitude: number; latitude: number }
    plannedStart: string
  }
  const timeline: TimelinePoint[] = [
    ...props.day.stages.map((stage) => ({
      kind: 'stage' as const,
      id: stage.id,
      name: stage.destination.name,
      city: stage.destination.city,
      coordinates: stage.destination.coordinates,
      plannedStart: stage.planned_start,
    })),
    ...props.day.activities.map((activity) => ({
      kind: 'activity' as const,
      id: activity.id,
      name: activity.place.name,
      city: activity.place.city,
      coordinates: activity.place.coordinates,
      plannedStart: activity.planned_start || '',
    })),
  ].sort((left, right) => {
    const timeDelta = left.plannedStart.localeCompare(right.plannedStart)
    return timeDelta || (left.kind === 'stage' ? -1 : 1)
  })
  const pairs: Array<{
    id: string
    start: { longitude: number; latitude: number }
    end: { longitude: number; latitude: number }
    city?: string
    distanceKm: number
  }> = []
  for (let index = 1; index < timeline.length; index += 1) {
    const previous = timeline[index - 1]
    const current = timeline[index]
    // MovementStage already owns its real road geometry. Connectors are only
    // needed around activities inserted between stages; this prevents the
    // old stage-first/activity-last ordering from drawing a false Lushan→Wuhan
    // dashed line across the whole trip.
    if (previous.kind === 'stage' && current.kind === 'stage') continue
    if (!previous.coordinates || !current.coordinates) continue
    const distance = Math.hypot(
      previous.coordinates.longitude - current.coordinates.longitude,
      previous.coordinates.latitude - current.coordinates.latitude,
    )
    if (distance <= 0.0001) continue
    const distanceKm = distance * 92
    // A connector is only a local transfer between an activity and a stage.
    // A long gap is a missing stage route, not something to draw as a dashed
    // straight line across the whole trip.
    // Keep dashed fallbacks only for a short local transfer. A failed
    // cross-city lookup must not become a misleading diagonal across the
    // whole map; the corresponding stage card still explains that routing
    // data is unavailable.
    if (distanceKm > 12) continue
    const id = `connector-${previous.id}-${current.id}`
    if (pairs.some((item) => item.id === id)) continue
    pairs.push({
      id,
      start: previous.coordinates,
      end: current.coordinates,
      city: previous.city === current.city ? current.city : undefined,
      distanceKm,
    })
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
    riding: '#f2a51a',
    walking: '#f2a51a',
    transit: '#18a66a',
    direct: '#9aa5b4',
  }
  const inactiveRouteColor = '#98a3b2'
  for (const { stage, route } of plannedPaths) {
    const unavailable = route.mode === 'direct'
    if (unavailable && stage.distance_km > 35) continue
    const start = stage.origin.coordinates
    const end = stage.destination.coordinates
    const displayPath = unavailable && start && end && route.path.length < 2
      ? [[start.longitude, start.latitude], [end.longitude, end.latitude]]
      : route.path
    if (displayPath.length < 2) continue
    const active = stage.id === props.activeStageId
    const polyline = new AMap.value.Polyline({
      path: displayPath,
      isOutline: active && !unavailable,
      outlineColor: '#dbe9ff',
      borderWeight: 3,
      strokeColor: active && !unavailable ? modeColor[route.mode] : inactiveRouteColor,
      strokeOpacity: active ? 0.96 : 0.78,
      strokeWeight: active && !unavailable ? 6 : 3,
      strokeStyle: unavailable ? 'dashed' : 'solid',
      strokeDasharray: unavailable ? [8, 8] : undefined,
      lineJoin: 'round',
      lineCap: 'round',
      showDir: active && !unavailable,
      zIndex: active ? 80 : 50,
      extData: { stageId: stage.id, unavailable, mode: route.mode },
    })
    polyline.on('click', () => emit('selectStage', stage.id))
    routeOverlays.value.push({ stageId: stage.id, polyline })
  }

  for (const { connector, route } of plannedConnectors) {
    if (route.path.length < 2) continue
    const unavailable = route.mode === 'direct'
    const polyline = new AMap.value.Polyline({
      path: route.path,
      strokeColor: inactiveRouteColor,
      strokeOpacity: unavailable ? 0.72 : 0.78,
      strokeWeight: 3,
      strokeStyle: unavailable ? 'dashed' : 'solid',
      strokeDasharray: unavailable ? [8, 8] : undefined,
      lineJoin: 'round',
      lineCap: 'round',
      showDir: false,
      zIndex: 45,
      extData: { connectorId: connector.id, unavailable, mode: route.mode },
    })
    routeOverlays.value.push({ stageId: connector.id, polyline })
  }

  const places = new Map<string, {
    place: NonNullable<typeof props.day>['stages'][number]['origin']
    kind: MarkerKind
    activityId?: string
  }>()
  const firstStage = props.day.stages[0]
  const lastStage = props.day.stages.at(-1)
  if (firstStage?.origin.coordinates) {
    places.set(firstStage.origin.name, { place: firstStage.origin, kind: 'start' })
  }
  for (const stage of props.day.stages.slice(0, -1)) {
    if (stage.destination.coordinates && !places.has(stage.destination.name)) {
      places.set(stage.destination.name, { place: stage.destination, kind: 'attraction' })
    }
  }
  for (const activity of props.day.activities) {
    if (activity.place.coordinates) {
      places.set(activity.place.name, {
        place: activity.place,
        kind: activityMarkerKind(activity.type),
        activityId: activity.id,
      })
    }
  }
  if (lastStage?.destination.coordinates) {
    places.set(lastStage.destination.name, { place: lastStage.destination, kind: 'end' })
  }
  for (const [index, { place, kind, activityId }] of [...places.values()].entries()) {
    if (!place.coordinates) continue
    const terminal = kind === 'start' || kind === 'end'
    const marker = new AMap.value.Marker({
      position: [place.coordinates.longitude, place.coordinates.latitude],
      title: place.name,
      content: `<div class="${terminal ? 'amap-terminal-marker' : 'amap-poi-marker'} amap-marker-${kind}"><b>${markerIcon(kind)}</b><span>${escapeHtml(place.name)}</span></div>`,
      offset: new AMap.value.Pixel(-16, -38),
      zIndex: 1000 + index,
    })
    if (activityId) marker.on('click', () => emit('selectActivity', activityId))
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
      [80, 80, 80, 80],
      16,
    )
    map.value.setZoomAndCenter(Math.max(3, fitZoom - 0.2), fitCenter, false, 2400)
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
        'AMap.Geocoder',
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
    const geocoder = new instance.Geocoder({ radius: 800, extensions: 'all' })
    map.value.on('click', (event: any) => {
      if (!props.pickEnabled) return
      const longitude = Number(event.lnglat.getLng())
      const latitude = Number(event.lnglat.getLat())
      if (pickedMarker.value) map.value.remove(pickedMarker.value)
      pickedMarker.value = new instance.Marker({
        position: [longitude, latitude],
        content: '<div class="amap-picked-marker"><span>+</span></div>',
        offset: new instance.Pixel(-18, -36),
        zIndex: 3000,
      })
      map.value.add(pickedMarker.value)
      geocoder.getAddress([longitude, latitude], (status: string, result: any) => {
        const regeocode = status === 'complete' ? result?.regeocode : undefined
        const poiName = regeocode?.pois?.[0]?.name
        const address = regeocode?.formattedAddress
        emit('pointSelected', {
          name: poiName || address || `地图选点 ${longitude.toFixed(5)},${latitude.toFixed(5)}`,
          address,
          longitude,
          latitude,
        })
      })
    })
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
watch(() => props.pickEnabled, (enabled) => {
  if (!enabled && pickedMarker.value && map.value) {
    map.value.remove(pickedMarker.value)
    pickedMarker.value = null
  }
})
onMounted(initMap)
onBeforeUnmount(() => {
  map.value?.destroy()
  map.value = null
  AMap.value = null
})
</script>

<template>
  <div class="amap-shell" :class="{ 'is-picking': pickEnabled }">
    <div v-show="!failed" :id="containerId" class="amap-container" />
    <MockRouteMap v-if="failed" :day="day" :active-stage-id="activeStageId" />
    <div v-if="loading" class="map-loading"><i />正在加载高德地图…</div>
    <div v-if="failed" class="map-fallback-badge">高德地图不可用 · 已切换 Mock 地图</div>
    <div v-else-if="!loading" class="map-live-badge" :class="{ warning: routeUnavailable }">
      {{ routeLoading ? '高德道路点列计算中…' : routeUnavailable ? '部分路段未返回道路点列 · 仅局部虚线直连' : '高德 JSAPI · 真实道路轨迹' }}
    </div>
  </div>
</template>
