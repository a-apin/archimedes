const API_BASE = import.meta.env?.VITE_API_BASE ?? ''

export const defaultFeatures = Object.freeze({ quant: import.meta.env?.PROD !== true })

export function parseFeatures(value, fallback = defaultFeatures) {
  return { quant: typeof value?.quant === 'boolean' ? value.quant : fallback.quant }
}

export async function fetchFeatures() {
  const response = await fetch(`${API_BASE}/api/features`, { credentials: 'include' })
  if (!response.ok) throw new Error('Feature configuration unavailable')
  return parseFeatures(await response.json())
}
