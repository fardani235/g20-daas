// Class-aware styling and legend for object-detection overlays.

// Distinct, readable colours; classes are assigned by a stable hash so the same
// class keeps its colour across tiles, runs, and reloads.
const PALETTE = [
  '#2563eb', '#dc2626', '#16a34a', '#d97706',
  '#7c3aed', '#0891b2', '#db2777', '#65a30d',
]

export function classColor(label) {
  const text = String(label ?? '')
  let hash = 0
  for (let i = 0; i < text.length; i++) hash = (hash * 31 + text.charCodeAt(i)) >>> 0
  return PALETTE[hash % PALETTE.length]
}

// Leaflet style callback for a detection feature.
export function detectionStyle(feature) {
  return {
    color: classColor(feature?.properties?.class),
    weight: 1.5,
    fillOpacity: 0.08,
  }
}

// [{ class, count, color }] for the classes present, most frequent first.
export function detectionLegend(features) {
  const counts = new Map()
  for (const feature of features || []) {
    const label = feature?.properties?.class ?? 'unknown'
    counts.set(label, (counts.get(label) || 0) + 1)
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([label, count]) => ({ class: label, count, color: classColor(label) }))
}
