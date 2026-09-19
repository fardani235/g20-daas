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

export function formatVolume({ volume, fill, cut, area } = {}) {
  const a = Number.isFinite(area) ? area : 0
  if (a === 0) return 'No DSM data under polygon'
  const v = Number.isFinite(volume) ? volume : 0
  const f = Number.isFinite(fill) ? fill : 0
  const c = Number.isFinite(cut) ? cut : 0
  const n = x => Math.round(x).toLocaleString('en-US')
  return `${n(v)} m³ (fill ${n(f)} / cut ${n(c)}) · ${n(a)} m²`
}
