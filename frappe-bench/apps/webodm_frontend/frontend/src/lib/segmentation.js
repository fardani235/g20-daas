// Class-aware styling and legend for semantic-segmentation overlays.
//
// Segmentation outputs are areal, so they render as filled regions (unlike the
// outline-forward detection style) while reusing the same stable class colours.

import { classColor } from './detections.js'

// Leaflet style callback for a segmentation feature.
export function segmentationStyle(feature) {
  const color = classColor(feature?.properties?.class)
  return {
    color,
    weight: 1,
    fillColor: color,
    fillOpacity: 0.35,
  }
}

// [{ class, count, area, color }] for the classes present, most frequent first.
export function segmentationLegend(features) {
  const byClass = new Map()
  for (const feature of features || []) {
    const label = feature?.properties?.class ?? 'unknown'
    const current = byClass.get(label) || { count: 0, area: 0 }
    current.count += 1
    current.area += Number(feature?.properties?.area) || 0
    byClass.set(label, current)
  }
  return [...byClass.entries()]
    .sort((a, b) => b[1].count - a[1].count || a[0].localeCompare(b[0]))
    .map(([label, { count, area }]) => ({
      class: label,
      count,
      area,
      color: classColor(label),
    }))
}
