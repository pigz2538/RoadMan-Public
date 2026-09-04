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
  /** Meal may intentionally be taken during a train/flight/drive or service stop. */
  in_transit?: boolean
  description?: string
  image_url?: string
  detail_url?: string
  ticket_or_price?: {
    currency: string
    minimum: number
    maximum: number
    estimated: boolean
    source_count?: number
    as_of?: string
    note?: string
  }
  ticket_name?: string
  ticket_status?: 'known' | 'free' | 'unknown'
  ticket_note?: string
  parking_note?: string
  parking_or_price?: {
    currency: string
    minimum: number
    maximum: number
    estimated: boolean
    source_count?: number
    as_of?: string
    note?: string
  }
  opening_hours?: { text: string; confirmed: boolean; source_count?: number; as_of?: string; note?: string }
  official_url?: string
  booking_url?: string
  information_status?: 'complete' | 'partial' | 'unavailable'
  information_checked_at?: string
  information_sources_count?: number
  reservation_status?: 'required' | 'recommended' | 'not_required' | 'unknown'
  reservation_note?: string
  risk_level?: 'low' | 'moderate' | 'high'
  risk_tags?: string[]
  risk_note?: string
  source_records?: Array<{
    provider: string
    title: string
    url?: string
    source_type?: string
    confidence?: string
    facts?: Record<string, unknown>
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
  transit_legs?: Array<{
    mode: 'bus' | 'subway' | 'rail' | 'walk' | 'shuttle' | 'ferry' | 'other'
    line_name?: string
    line_id?: string
    line_type?: string
    departure_stop?: string
    arrival_stop?: string
    departure_time?: string
    arrival_time?: string
    stop_count?: number
    duration_minutes?: number
    distance_km?: number
    fare_cny?: number
  }>
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
  service_number?: string
  service_operator?: string
  departure_terminal?: string
  arrival_terminal?: string
  service_detail_url?: string
  service_departure_at?: string
  service_arrival_at?: string
  service_seat_class?: string
  service_price?: { currency: string; minimum: number; maximum: number; estimated: boolean; note?: string }
  service_status?: 'confirmed' | 'estimated' | 'unavailable'
  transit_fare_cny?: number
  weather_summary?: string
  toll_fee?: { currency: string; minimum: number; maximum: number; estimated: boolean }
  energy_estimate?: {
    amount: number
    unit: 'kWh' | 'L'
    starting_percent?: number
    consumed_percent?: number
    before_replenishment_percent?: number
    replenished_amount?: number
    replenished_unit?: 'kWh' | 'L'
    replenishment_minutes?: number
    charging_power_kw?: number
    after_replenishment_percent?: number
    remaining_percent?: number
    calculation_basis?: 'consumption_model' | 'measured_energy' | 'charger_power' | 'conservative_fallback' | 'fuel_service_estimate'
    estimated: boolean
  }
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
  total_walk_minutes?: number
  total_walk_distance_km?: number
}

export interface Trip {
  id: string
  title: string
  status: string
  days: DayPlan[]
  warnings: Array<{ code: string; message: string }>
  created_at?: string
  updated_at?: string
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
  batch_id?: string
}
