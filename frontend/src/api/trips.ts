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
  verification_result?: {
    passed: boolean
    issues: Array<{ code: string; severity?: 'blocker' | 'warning' | string; description: string }>
    auto_repair_attempts?: number
    auto_repair_exhausted?: boolean
  }
  special_event_research?: SpecialEventResearch[]
  plan_markdown?: string
  job_id?: string
  planning_batch_id?: string
  edit_confirmation_pending?: boolean
  route_replan_required?: boolean
  excluded_places?: Array<{ name?: string; category?: string; reason?: string }>
}

export interface SpecialEventResearch {
  event: string
  status: 'researched' | 'needs_review' | string
  query?: string
  search_url?: string
  sources?: Array<{ title?: string; url?: string; snippet?: string; provider?: string }>
  facts?: {
    peak_start_date?: string
    peak_end_date?: string
    peak_time_utc?: string
    peak_time_local?: string
    peak_time_label?: string
    observation_window_local?: string
    active_period?: string
    zhr?: number
    confidence?: 'high' | 'medium' | 'low' | string
    summary?: string
    evidence_source_indexes?: number[]
  }
}

export interface PreflightResult {
  ready: boolean
  confirmation_required: boolean
  semantic_checked: boolean
  issues: Array<{
    code: string
    message: string
    field?: string
    severity: 'question' | 'error'
    answer_type: 'text' | 'date' | 'choice' | 'time'
    options: string[]
  }>
  extracted: Record<string, unknown>
  summary: {
    origin_name?: string
    destination_name?: string
    start_date?: string
    end_date?: string
    departure_time?: string
    return_time?: string
    travelers?: number
    max_days?: number
    preferences?: string[]
    transport_modes?: string[]
    clarifications?: string[]
  }
  special_event_research?: SpecialEventResearch[]
}

export interface RecommendationCandidate {
  candidate_id: string
  rank: number
  score: number
  backup?: boolean
  seasonal_excluded?: boolean
  seasonal_warning?: string
  seasonal_reason?: string
  agent_suitability?: boolean
  suitability_confidence?: 'high' | 'medium' | 'low'
  suitability_reason?: string
  weather_fit_reason?: string
  terrain_fit_reason?: string
  personal_fit_reason?: string
  elevation_m?: number
  recommendation_reasons?: string[]
  agent_reason?: string
  description?: string
  image_url?: string
  detail_url?: string
  place: {
    name: string
    address?: string
    coordinates?: { longitude: number; latitude: number }
  }
  rating?: number
  ticket_or_price?: {
    currency: string
    minimum: number
    maximum: number
    estimated: boolean
  }
  price_min_cny?: number
  price_max_cny?: number
  source_records?: Array<{ provider: string; title: string; url?: string }>
}

export interface PlanPatch {
  id: string
  trip_id: string
  target_type: string
  target_id: string
  operation: 'add' | 'replace' | 'delete'
  original_value: Record<string, unknown>
  proposed_value: {
    candidate_id?: string
    category?: 'attractions' | 'hotels' | 'meals'
    day_id: string
    candidate?: RecommendationCandidate
  }
  impact_scope: string[]
  time_delta_minutes: number
  status: 'preview' | 'rejected' | 'applied'
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(payload?.error?.message || `请求失败（${response.status}）`)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export async function createTrip(
  rawText: string,
  extracted: Record<string, unknown> = {},
  selectedVehicleId?: string,
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
        departure_time: extracted.departure_time,
        return_time: extracted.return_time,
        travelers: extracted.travelers,
        preferences: extracted.preferences,
        transport_modes: extracted.transport_modes,
        special_events: extracted.special_events,
        max_days: extracted.max_days,
      },
      selected_vehicle_id: selectedVehicleId,
    }),
  }))
}

export async function listTrips(): Promise<Trip[]> {
  return json(await fetch(`${API_BASE}/api/v1/trips`, { cache: 'no-store' }))
}

export async function deleteTrip(tripId: string): Promise<void> {
  await json(await fetch(`${API_BASE}/api/v1/trips/${tripId}`, {
    method: 'DELETE',
    cache: 'no-store',
  }))
}

export async function fetchWeatherForecast(
  latitude: number,
  longitude: number,
): Promise<{ success: boolean; data?: { current?: Record<string, number | string | null> } }> {
  return json(await fetch(`${API_BASE}/api/v1/skills/weather/forecast`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ latitude, longitude, forecast_days: 1, timezone: 'Asia/Shanghai' }),
  }))
}

export async function preflightTrip(
  rawText: string,
  answers: Record<string, string> = {},
  confirmed = false,
  previousExtracted: Record<string, unknown> = {},
  semanticChecked = false,
): Promise<PreflightResult> {
  return json(await fetch(`${API_BASE}/api/v1/trips/preflight`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      raw_text: rawText,
      answers,
      confirmed,
      previous_extracted: previousExtracted,
      semantic_checked: semanticChecked,
    }),
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

export async function fetchRecommendations(
  tripId: string,
  category: 'attractions' | 'hotels' | 'meals',
): Promise<{ items: RecommendationCandidate[] }> {
  return json(await fetch(
    `${API_BASE}/api/v1/trips/${tripId}/recommendations?category=${category}`,
  ))
}

export async function previewCandidatePatch(
  tripId: string,
  payload: {
    candidate_id: string
    category: 'attractions' | 'hotels' | 'meals'
    day_id: string
    operation: 'add' | 'replace'
    target_activity_id?: string
  },
): Promise<PlanPatch> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/patches/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }))
}

export async function previewMapPointPatch(
  tripId: string,
  payload: {
    day_id: string
    category: 'attractions' | 'hotels' | 'meals'
    name: string
    address?: string
    longitude: number
    latitude: number
  },
): Promise<PlanPatch> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/patches/preview-map-point`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }))
}

export async function applyPlanPatch(
  tripId: string,
  patchId: string,
): Promise<{ patch: PlanPatch; trip: Trip; route_replan_required?: boolean }> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/patches/${patchId}/apply`, {
    method: 'POST',
  }))
}

export async function rejectPlanPatch(tripId: string, patchId: string): Promise<PlanPatch> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/patches/${patchId}/reject`, {
    method: 'POST',
  }))
}

export async function previewDeletePatch(
  tripId: string,
  payload: { day_id: string; activity_id: string },
): Promise<PlanPatch> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/patches/preview-delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }))
}

export async function rollbackPlanPatch(
  tripId: string,
  patchId: string,
): Promise<{ patch: PlanPatch; trip: Trip }> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/patches/${patchId}/rollback`, {
    method: 'POST',
  }))
}

export async function interpretTripEdit(
  tripId: string,
  payload: {
    message: string
    current_day_id?: string
    current_target_id?: string
  },
): Promise<{
  message: string
  patch?: PlanPatch
  global_replan_required: boolean
  requires_confirmation?: boolean
  confirmation_message?: string | null
}> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/editing/interpret`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }))
}

export async function confirmTripReplan(
  tripId: string,
): Promise<{ message: string; trip: Trip; global_replan_required: boolean }> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/editing/confirm-replan`, {
    method: 'POST',
  }))
}

export interface TripVersion {
  id: string
  trip_id: string
  name: string
  note?: string
  created_at: string
}

export interface AttachmentExtraction {
  file_id: string
  status: 'preview' | 'confirmed'
  places: string[]
  hotels: string[]
  dates: string[]
  order_numbers: string[]
  text_preview: string
  warnings: string[]
}

export async function uploadTripAttachment(tripId: string, file: File): Promise<{ id: string; original_name: string }> {
  const body = new FormData()
  body.append('trip_id', tripId)
  body.append('upload', file)
  return json(await fetch(`${API_BASE}/api/v1/files`, { method: 'POST', body }))
}

export async function extractTripAttachment(fileId: string): Promise<AttachmentExtraction> {
  return json(await fetch(`${API_BASE}/api/v1/files/${fileId}/extract`, { method: 'POST' }))
}

export async function confirmTripAttachment(fileId: string, acceptedPlaces: string[]): Promise<AttachmentExtraction> {
  return json(await fetch(`${API_BASE}/api/v1/files/${fileId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ accepted_places: acceptedPlaces }),
  }))
}

export async function createTripVersion(
  tripId: string,
  name: string,
  note?: string,
): Promise<TripVersion> {
  return json(await fetch(`${API_BASE}/api/v1/trips/${tripId}/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, note }),
  }))
}

export function downloadTripMarkdown(tripId: string) {
  window.location.href = `${API_BASE}/api/v1/trips/${tripId}/roadbook`
}

export function downloadTripExport(tripId: string, format: 'pdf' | 'pptx' | 'png' | 'html') {
  window.location.href = `${API_BASE}/api/v1/trips/${tripId}/roadbook.${format}`
}
