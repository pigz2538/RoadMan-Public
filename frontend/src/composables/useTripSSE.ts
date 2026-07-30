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
    'planning_completed',
  ]

  function connect(tripId: string) {
    source?.close()
    source = new EventSource(`/api/v1/trips/${tripId}/planning/events`)
    eventNames.forEach((name) => {
      source?.addEventListener(name, (event) => onEvent(JSON.parse((event as MessageEvent).data)))
    })
    source.onerror = () => source?.close()
  }

  onBeforeUnmount(() => source?.close())
  return { connect }
}
