export type Category = '景点' | '住宿' | '餐饮' | '服务'

export interface Place {
  name: string
  city?: string
  coordinates?: { longitude: number; latitude: number }
}

export interface Activity {
  id: string
  day_id: string
  sequence: number
  type: string
  place: Place
  duration_minutes: number
  planned_start?: string
  planned_end?: string
  required?: boolean
  backup?: boolean
  user_note?: string
  description?: string
  image_url?: string
  detail_url?: string
  ticket_or_price?: {
    currency: string
    minimum: number
    maximum: number
    estimated: boolean
  }
  opening_hours?: { text: string; confirmed: boolean }
  reservation_status?: 'required' | 'recommended' | 'not_required' | 'unknown'
  reservation_note?: string
  risk_level?: 'low' | 'moderate' | 'high'
  risk_tags?: string[]
  risk_note?: string
  source_records?: Array<{
    provider: string
    title: string
    url?: string
  }>
  warnings?: Array<{ code: string; message: string; severity?: string }>
}

export interface Stage {
  id: string
  day_id: string
  sequence: number
  title: string
  mode: string
  transit_type?: 'bus' | 'subway' | 'shuttle' | 'ferry'
  origin: Place
  destination: Place
  waypoints: Place[]
  route_segments: Array<{
    coordinates: Array<{ longitude: number; latitude: number }>
    road_name?: string
    estimated?: boolean
    elevation_gain_m?: number
  }>
  planned_start: string
  planned_end: string
  distance_km: number
  duration_minutes: number
  elevation_gain_m?: number
  traffic_summary?: string
  weather_summary?: string
  toll_fee?: { currency: string; minimum: number; maximum: number; estimated: boolean }
  energy_estimate?: { amount: number; unit: string; remaining_percent?: number; estimated: boolean }
  weather_samples?: Array<{
    sampled_at: string
    temperature_c?: number
    precipitation_probability?: number
    visibility_m?: number
    wind_speed_kmh?: number
  }>
  risk_level?: 'low' | 'moderate' | 'high'
  risk_tags?: string[]
  status: string
  warnings: Array<{ code: string; message: string; severity?: string; estimated?: boolean }>
}

export interface DayPlan {
  id: string
  day_index: number
  date: string
  title: string
  items: Array<{ type: 'stage' | 'activity'; id: string }>
  activities: Activity[]
  stages: Stage[]
  weather_summary?: string
  total_distance_km: number
  total_drive_minutes: number
}

export interface Trip {
  id: string
  title: string
  status: string
  days: DayPlan[]
  warnings: Array<{ code: string; message: string }>
  request?: {
    raw_text: string
    defaults_applied: string[]
    preferences?: string[]
    transport_modes?: string[]
  }
}

export interface PlanningEvent {
  event: string
  label: string
  progress: number
  node?: string
  tool?: string
}
