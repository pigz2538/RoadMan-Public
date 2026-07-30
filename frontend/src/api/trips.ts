import type { Trip } from '../types/trip'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export interface PlanningSnapshot {
  trip_id: string
  status: string
  missing_fields: string[]
  clarification_round: number
  clarification_question?: string
  defaults_applied: string[]
  progress: { node?: string; value?: number; label?: string }
  verification_result?: { passed: boolean; issues: Array<{ code: string; description: string }> }
  plan_markdown?: string
  job_id?: string
}

export interface PreflightResult {
  ready: boolean
  issues: Array<{
    code: string
    message: string
    field?: string
    severity: 'question' | 'error'
  }>
  extracted: Record<string, unknown>
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(payload?.error?.message || `请求失败（${response.status}）`)
  }
  return response.json()
}

export async function createTrip(
  rawText: string,
  extracted: Record<string, unknown> = {},
): Promise<Trip> {
  return json(await fetch(`${API_BASE}/api/v1/trips`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: '正在理解您的旅行需求',
      request: {
        raw_text: rawText,
        origin: extracted.origin_name ? { name: extracted.origin_name } : undefined,
        destination: extracted.destination_name ? { name: extracted.destination_name } : undefined,
        start_date: extracted.start_date,
        end_date: extracted.end_date,
        travelers: extracted.travelers,
        preferences: extracted.preferences,
      },
    }),
  }))
}

export async function preflightTrip(rawText: string): Promise<PreflightResult> {
  return json(await fetch(`${API_BASE}/api/v1/trips/preflight`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_text: rawText }),
  }))
}

export async function fetchTrip(tripId: string): Promise<Trip> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}`))
}

export async function fetchMockTrip(): Promise<Trip> {
  return json(await fetch(`${API_BASE}/api/v1/trips/mock/wuhan-lushan`))
}

export async function startPlanning(tripId: string): Promise<PlanningSnapshot> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/planning/start`, {
    method: 'POST',
  }))
}

export async function fetchPlanning(tripId: string): Promise<PlanningSnapshot> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/planning`))
}

export async function answerClarification(
  tripId: string,
  answer: string,
): Promise<PlanningSnapshot> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/planning/clarifications`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer }),
  }))
}
