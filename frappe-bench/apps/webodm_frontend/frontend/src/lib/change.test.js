import { describe, it, expect } from 'vitest'
import { CHANGE_CLASSES, changeStyle, changeLegend, changeRows, changePopupHtml } from './change.js'

const feature = (cls, extra = {}) => ({ properties: { class: cls, ...extra } })

describe('change lib', () => {
  it('colours cut red and fill blue, and plays the informational classes down', () => {
    expect(changeStyle(feature('cut')).fillColor).toBe('#dc2626')
    expect(changeStyle(feature('fill')).fillColor).toBe('#2563eb')
    expect(changeStyle(feature('fill')).fillOpacity).toBeGreaterThan(changeStyle(feature('vegetation')).fillOpacity)
    expect(changeStyle(feature('appearance')).color).toBe(CHANGE_CLASSES.appearance.color)
    // An unknown class (a newer plugin version) still draws.
    expect(changeStyle(feature('mystery')).color).toBe('#6b7280')
    expect(changeStyle(null).color).toBe('#6b7280')
  })

  it('builds a legend in a fixed order with volumes for earthwork and areas for the rest', () => {
    const legend = changeLegend([
      feature('appearance', { area: 144 }),
      feature('cut', { area: 600, volume_m3: 1200 }),
      feature('fill', { area: 400, volume_m3: 600.4 }),
      feature('cut', { area: 40, volume_m3: 39.5 }),
      feature('vegetation', { area: 225, volume_m3: 675 }),
    ])
    expect(legend.map(l => l.class)).toEqual([
      'Fill · 600 m³',
      'Cut · 1,240 m³',
      'Vegetation (not counted) · 225 m²',
      'Looks different, same height · 144 m²',
    ])
    expect(legend[1]).toMatchObject({ count: 2, area: 640, volume: 1239.5, color: '#dc2626' })
    expect(changeLegend([])).toEqual([])
    expect(changeLegend(null)).toEqual([])
  })

  it('describes a region with its numbers', () => {
    const rows = changeRows(feature('cut', {
      area: 625.06, volume_m3: 1200.08, volume_uncertainty_m3: 14.2, mean_change_m: -1.92, max_change_m: -2.086,
    }))
    expect(rows).toEqual([
      { label: 'Area', value: '625 m²' },
      { label: 'Volume', value: '1,200 m³ ± 14.2 m³' },
      { label: 'Average change', value: '-1.92 m' },
      { label: 'Largest change', value: '-2.09 m' },
    ])
    // Appearance regions have an area and nothing else.
    expect(changeRows(feature('appearance', { area: 143.56 }))).toEqual([{ label: 'Area', value: '144 m²' }])
    expect(changeRows({})).toEqual([])
  })

  it('escapes everything a plugin put into the popup', () => {
    const html = changePopupHtml(feature('fill', { label: '<img src=x onerror=alert(1)>', area: 10 }))
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;')
    expect(html).toContain('10 m²')
    expect(changePopupHtml(feature('cut', { area: 5 }))).toContain('<strong>Cut</strong>')
  })
})
