import type { Trip } from '../types/trip'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export async function fetchMockTrip(): Promise<Trip> {
  const response = await fetch(`${API_BASE}/api/v1/trips/mock/wuhan-lushan`)
  if (!response.ok) throw new Error('后端不可用')
  return response.json()
}
