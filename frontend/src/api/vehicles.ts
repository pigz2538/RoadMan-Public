const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export type VehiclePowerType = 'electric' | 'hybrid' | 'fuel'

export interface VehicleSpecification {
  name: string
  value: string
}

export interface Vehicle {
  id: string
  brand: string
  series: string
  model: string
  year?: number | null
  power_type: VehiclePowerType
  rated_range_km?: number | null
  current_energy_percent?: number | null
  battery_kwh?: number | null
  consumption_per_100km?: number | null
  max_charge_kw?: number | null
  height_m?: number | null
  width_m?: number | null
  seats: number
  plate_region?: string | null
  has_etc: boolean
  mountain_ready: boolean
  unpaved_ready: boolean
  safe_energy_reserve_percent: number
  source_id?: string | null
  source_url?: string | null
  detail_source_url?: string | null
  price_min_cny?: number | null
  price_max_cny?: number | null
  specifications?: VehicleSpecification[]
}

export type VehicleInput = Omit<Vehicle, 'id'> & { id?: string }
export type VehicleUpdate = Partial<Omit<Vehicle, 'id'>>

/** A concrete brand/series/model record returned by the carinfo Skill. */
export interface VehicleCatalogItem {
  id: string
  source_id: string
  brand: string
  series: string
  model: string
  year?: number | null
  power_type: VehiclePowerType
  rated_range_km?: number | null
  battery_kwh?: number | null
  consumption_per_100km?: number | null
  max_charge_kw?: number | null
  height_m?: number | null
  width_m?: number | null
  seats?: number | null
  current_energy_percent?: number | null
  price_min_cny?: number | null
  price_max_cny?: number | null
  state?: string
  state_label?: string
  source_url?: string
  detail_source_url?: string
  specifications?: VehicleSpecification[]
  specs_missing?: string[]
  estimated_fields?: string[]
}

export interface VehicleCatalogSearch {
  query: string
  count: number
  items: VehicleCatalogItem[]
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(payload?.error?.message || `车辆请求失败（${response.status}）`)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export async function listVehicles(): Promise<Vehicle[]> {
  return json(await fetch(`${API_BASE}/api/v1/vehicles`, { cache: 'no-store' }))
}

export async function createVehicle(payload: VehicleInput): Promise<Vehicle> {
  return json(await fetch(`${API_BASE}/api/v1/vehicles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }))
}

export async function updateVehicle(vehicleId: string, payload: VehicleUpdate): Promise<Vehicle> {
  return json(await fetch(`${API_BASE}/api/v1/vehicles/${vehicleId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }))
}

export async function deleteVehicle(vehicleId: string): Promise<void> {
  await json(await fetch(`${API_BASE}/api/v1/vehicles/${vehicleId}`, {
    method: 'DELETE',
    cache: 'no-store',
  }))
}

interface CarInfoSkillResponse {
  success: boolean
  data?: VehicleCatalogSearch | null
  warnings?: string[]
}

export async function searchVehicleCatalog(
  query: string,
  limit = 12,
): Promise<VehicleCatalogSearch> {
  const response = await fetch(`${API_BASE}/api/v1/skills/carinfo/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, limit }),
  })
  const payload = await json<CarInfoSkillResponse>(response)
  if (!payload.success || !payload.data) {
    throw new Error(payload.warnings?.[0] || '车型数据库暂不可用，请稍后重试')
  }
  return payload.data
}
