import { onBeforeUnmount } from 'vue'
import type { PlanningEvent } from '../types/trip'

export function useTripSSE(onEvent: (event: PlanningEvent) => void) {
  let source: EventSource | null = null
  const eventNames = [
    'planning_started',
    'node_started',
    'tool_started',
    'tool_completed',
    'progress',
    'plan_updated',
    'planning_completed',
    'planning_failed',
    'planning_paused',
    'clarification_required',
  ]

  function connect(tripId: string) {
    source?.close()
    source = new EventSource(`/api/v1/trips/${tripId}/planning/events`)
    eventNames.forEach((name) => {
      source?.addEventListener(name, (event) => onEvent(JSON.parse((event as MessageEvent).data)))
    })
    // EventSource reconnects automatically after transient network failures.
    // Closing it here made the progressive plan appear permanently stalled.
    source.onerror = () => undefined
  }

  onBeforeUnmount(() => source?.close())
  return { connect }
}
