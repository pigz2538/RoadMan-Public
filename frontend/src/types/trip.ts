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
  user_note?: string
}

export interface Stage {
  id: string
  day_id: string
  sequence: number
  title: string
  mode: string
  origin: Place
  destination: Place
  waypoints: Place[]
  route_segments: Array<{
    coordinates: Array<{ longitude: number; latitude: number }>
  }>
  distance_km: number
  duration_minutes: number
  status: string
  warnings: Array<{ code: string; message: string }>
}

export interface DayPlan {
  id: string
  day_index: number
  date: string
  title: string
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
}

export interface PlanningEvent {
  event: string
  label: string
  progress: number
  node?: string
  tool?: string
}
