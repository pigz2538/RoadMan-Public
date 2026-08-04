import { onBeforeUnmount } from 'vue'
import type { PlanningEvent } from '../types/trip'

export function useTripSSE(onEvent: (event: PlanningEvent) => void) {
  let source: EventSource | null = null
  let lastEventId = 0
  let connectedTripId = ''
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
    if (connectedTripId && connectedTripId !== tripId) lastEventId = 0
    connectedTripId = tripId
    const cursor = lastEventId ? `?after=${lastEventId}` : ''
    source = new EventSource(`/api/v1/trips/${tripId}/planning/events${cursor}`)
    eventNames.forEach((name) => {
      source?.addEventListener(name, (event) => {
        const message = event as MessageEvent
        const parsedId = Number(message.lastEventId)
        if (Number.isFinite(parsedId) && parsedId > 0) lastEventId = parsedId
        onEvent(JSON.parse(message.data))
      })
    })
    // EventSource reconnects automatically after transient network failures.
    // Closing it here made the progressive plan appear permanently stalled.
    source.onerror = () => undefined
  }

  onBeforeUnmount(() => source?.close())
  return { connect }
}
