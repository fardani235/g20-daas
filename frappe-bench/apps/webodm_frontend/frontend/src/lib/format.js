// Human-readable formatting for map measurements. Pure, no dependencies.

const ACRES_PER_M2 = 1 / 4046.8564224

export function formatDistance(meters) {
  const m = Number.isFinite(meters) && meters > 0 ? meters : 0
  if (m < 1000) return `${m.toFixed(1)} m`
  return `${(m / 1000).toFixed(2)} km`
}

export function formatArea(m2) {
  const a = Number.isFinite(m2) && m2 > 0 ? m2 : 0
  if (a === 0) return `${a.toFixed(1)} m²`
  const acres = a * ACRES_PER_M2
  if (a < 10000) return `${a.toFixed(1)} m² (${acres.toFixed(2)} ac)`
  return `${(a / 10000).toFixed(2)} ha (${acres.toFixed(2)} ac)`
}
