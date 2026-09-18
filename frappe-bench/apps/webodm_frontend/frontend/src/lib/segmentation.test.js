import { describe, it, expect } from 'vitest'
import { classColor } from './detections.js'
import { segmentationStyle, segmentationLegend } from './segmentation.js'

describe('segmentation lib', () => {
  it('fills a feature with its class colour', () => {
    const style = segmentationStyle({ properties: { class: 'building' } })
    expect(style.color).toBe(classColor('building'))
    expect(style.fillColor).toBe(classColor('building'))
    expect(style.fillOpacity).toBeGreaterThan(0)
  })

  it('builds a legend ordered by count with per-class area', () => {
    const features = [
      { properties: { class: 'building', area: 10 } },
      { properties: { class: 'road', area: 4 } },
      { properties: { class: 'building', area: 6 } },
    ]
    expect(segmentationLegend(features)).toEqual([
      { class: 'building', count: 2, area: 16, color: classColor('building') },
      { class: 'road', count: 1, area: 4, color: classColor('road') },
    ])
    expect(segmentationLegend([])).toEqual([])
  })

  it('tolerates features without an area', () => {
    const legend = segmentationLegend([{ properties: { class: 'water' } }])
    expect(legend[0].count).toBe(1)
    expect(legend[0].area).toBe(0)
  })
})
