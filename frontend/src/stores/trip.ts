import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { Activity, Category, PlanningEvent, Stage, Trip } from '../types/trip'
import type { PlanPatch } from '../api/trips'

export const useTripStore = defineStore('trip', () => {
  const trip = ref<Trip | null>(null)
  const currentDayIndex = ref(0)
  const currentStageId = ref('stage_2')
  const selectedNodeId = ref<string | null>(null)
  const category = ref<Category>('景点')
  const planningEvent = ref<PlanningEvent | null>(null)
  const planningEvents = ref<PlanningEvent[]>([])
  const queuedPlanningEvents = ref<PlanningEvent[]>([])
  const planningAnimationActive = ref(false)
  let planningAnimationTimer: ReturnType<typeof setTimeout> | undefined
  const patchVisible = ref(false)
  const pendingPatch = ref<PlanPatch | null>(null)
  const lastAppliedPatchId = ref<string | null>(null)

  const currentDay = computed(() => trip.value?.days[currentDayIndex.value])
  const currentStage = computed(() =>
    currentDay.value?.stages.find((stage) => stage.id === currentStageId.value) ?? currentDay.value?.stages[0],
  )

  function setDay(index: number) {
    currentDayIndex.value = index
    currentStageId.value = trip.value?.days[index]?.stages[0]?.id ?? ''
    selectedNodeId.value = null
  }

  function setStage(stage: Stage) {
    currentStageId.value = stage.id
    selectedNodeId.value = stage.id
  }

  function selectActivity(activity: Activity) {
    selectedNodeId.value = activity.id
  }

  function removeActivity(id: string) {
    if (!currentDay.value) return
    currentDay.value.activities = currentDay.value.activities.filter((item) => item.id !== id)
    if (selectedNodeId.value === id) selectedNodeId.value = null
  }

  function addPlanningEvent(event: PlanningEvent) {
    const previous = planningEvents.value.at(-1)
    const duplicate = (item: PlanningEvent | undefined) => Boolean(
      item?.event === event.event
      && item?.node === event.node
      && item?.label === event.label
      && item?.progress === event.progress,
    )
    if (duplicate(previous) || duplicate(queuedPlanningEvents.value.at(-1))) return
    queuedPlanningEvents.value = [...queuedPlanningEvents.value, event]
    playNextPlanningEvent()
  }

  function playNextPlanningEvent() {
    if (planningAnimationActive.value || !queuedPlanningEvents.value.length) return
    const [next, ...rest] = queuedPlanningEvents.value
    queuedPlanningEvents.value = rest
    planningAnimationActive.value = true
    planningEvent.value = next
    planningEvents.value = [...planningEvents.value, next].slice(-40)
    planningAnimationTimer = setTimeout(() => {
      planningAnimationActive.value = false
      planningAnimationTimer = undefined
      playNextPlanningEvent()
    }, 680)
  }

  function resetPlanningEvents() {
    if (planningAnimationTimer) clearTimeout(planningAnimationTimer)
    planningAnimationTimer = undefined
    planningEvent.value = null
    planningEvents.value = []
    queuedPlanningEvents.value = []
    planningAnimationActive.value = false
  }

  return {
    trip,
    currentDayIndex,
    currentDay,
    currentStage,
    currentStageId,
    selectedNodeId,
    category,
    planningEvent,
    planningEvents,
    planningAnimationActive,
    planningPresentationIdle: computed(() => !planningAnimationActive.value && !queuedPlanningEvents.value.length),
    patchVisible,
    pendingPatch,
    lastAppliedPatchId,
    setDay,
    setStage,
    selectActivity,
    removeActivity,
    addPlanningEvent,
    resetPlanningEvents,
  }
})
