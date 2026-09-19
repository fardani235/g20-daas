import { describe, it, expect } from 'vitest'
import { classColor, detectionStyle, detectionLegend } from './detections.js'

describe('detections lib', () => {
  it('assigns stable colours per class', () => {
    expect(classColor('person')).toBe(classColor('person'))
    expect(classColor('person')).not.toBe(classColor('car'))
    expect(classColor(undefined)).toMatch(/^#/)
  })

  it('styles a feature by its class colour', () => {
    const style = detectionStyle({ properties: { class: 'car' } })
    expect(style.color).toBe(classColor('car'))
  })

  it('builds a legend ordered by count', () => {
    const features = [
      { properties: { class: 'car' } },
      { properties: { class: 'person' } },
      { properties: { class: 'car' } },
    ]
    const legend = detectionLegend(features)
    expect(legend).toEqual([
      { class: 'car', count: 2, color: classColor('car') },
      { class: 'person', count: 1, color: classColor('person') },
    ])
    expect(detectionLegend([])).toEqual([])
  })
})
