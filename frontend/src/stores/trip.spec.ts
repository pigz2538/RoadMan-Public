import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useTripStore } from './trip'

describe('trip planning presentation state', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('deduplicates repeated worker events and reveals queued events in order', () => {
    const store = useTripStore()
    const first = {
      event: 'plan_updated',
      node: 'review_daily_schedule',
      label: '每日复核智能体正在检查行程',
      progress: 94,
      tool: 'daily_review_agent',
    }
    const second = {
      event: 'plan_updated',
      node: 'verify_plan',
      label: '行程验证智能体正在复核',
      progress: 95,
    }

    store.addPlanningEvent(first)
    store.addPlanningEvent(first)
    store.addPlanningEvent(second)

    expect(store.planningEvents).toEqual([first])
    vi.advanceTimersByTime(680)
    expect(store.planningEvents).toEqual([first, second])
  })

  it('clears collaboration dialogue together with a new planning presentation', () => {
    const store = useTripStore()
    store.agentDialogue = [
      {
        id: 'agent_turn_1',
        round: 0,
        sender: '每日复核智能体',
        recipients: ['行程编排智能体'],
        kind: 'revision_request',
        status: 'open',
        summary: '需要补齐晚餐',
        issue_codes: ['DAILY_DINNER_MISSING'],
        day_indices: [1],
      },
    ]

    store.resetPlanningEvents()

    expect(store.agentDialogue).toEqual([])
    expect(store.planningEvents).toEqual([])
    expect(store.planningPresentationIdle).toBe(true)
  })
})
