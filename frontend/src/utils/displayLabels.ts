/**
 * User-facing labels deliberately hide implementation/provider names.
 *
 * Registry IDs, API paths and audit records remain technical so integrations
 * and diagnostics keep working.  Only text rendered in the product passes
 * through these helpers.
 */
const DISPLAY_REPLACEMENTS: Array<[RegExp, string]> = [
  [/在线地图官方网站，提供全国地图浏览，地点搜索，公交驾车查询服务。可同时查看商家团购、优惠信息。在线地图，您的出行、生活好帮手。/g, ''],
  [/飞猪AI开放平台（旅行信息服务）是飞猪旅行的AI能力开放平台，为开发者提供酒店预订、机票搜索、门票API、度假套餐等全品类旅行AI服务，支持OpenClaw协议实时接入飞猪官方商品库。/g, ''],
  [/^public_web$/i, '公开资料'],
  [/FlyAI\s*\/\s*飞猪/gi, '旅行信息服务'],
  [/FlyAI/gi, '旅行信息服务'],
  [/Open[_-]?TripMap\s*\/\s*OpenStreetMap/gi, '全球景点资料'],
  [/Open[_-]?TripMap/gi, '全球景点资料'],
  [/OpenStreetMap/gi, '开放地图资料'],
  [/Open[_-]?Meteo/gi, '天气服务'],
  [/Bitefu\s*CarApi/gi, '车型资料库'],
  [/\bcarinfo(?:\.(?:catalog|demo))?\b/gi, '车型资料库'],
  [/AMap(?:\s+JSAPI)?/gi, '在线地图'],
  [/高德地图/gi, '在线地图'],
  [/高德/gi, '地图服务'],
  [/\bPOI\b/gi, '地点候选'],
  [/\bMock\b/gi, '示例'],
  [/Agents?/gi, '智能体'],
  [/ollama/gi, '语义分析'],
]

export function humanizeDisplayText(value: string | null | undefined): string {
  if (!value) return ''
  return DISPLAY_REPLACEMENTS.reduce((text, [pattern, replacement]) => text.replace(pattern, replacement), value)
}

export function humanizeProvider(value: string | null | undefined): string {
  return humanizeDisplayText(value) || '综合资料'
}
