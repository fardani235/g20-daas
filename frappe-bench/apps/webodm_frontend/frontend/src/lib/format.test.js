import { describe, it, expect } from 'vitest'
import { formatDistance, formatArea, formatVolume } from '@/lib/format'

describe('formatDistance', () => {
  it('shows metres under 1 km', () => {
    expect(formatDistance(500)).toBe('500.0 m')
  })
  it('shows kilometres at or above 1 km', () => {
    expect(formatDistance(1500)).toBe('1.50 km')
  })
  it('clamps invalid input to zero', () => {
    expect(formatDistance(-5)).toBe('0.0 m')
    expect(formatDistance(NaN)).toBe('0.0 m')
  })
})

describe('formatArea', () => {
  it('shows square metres with acres under 1 ha', () => {
    const s = formatArea(5000)
    expect(s).toContain('5000.0 m²')
    expect(s).toContain('ac')
  })
  it('shows hectares with acres at or above 1 ha', () => {
    const s = formatArea(20000)
    expect(s).toContain('2.00 ha')
    expect(s).toContain('ac')
  })
  it('clamps invalid input to zero', () => {
    expect(formatArea(-1)).toBe('0.0 m²')
  })
})

describe('formatVolume', () => {
  it('formats volume with fill/cut/area and thousands separators', () => {
    const s = formatVolume({ volume: 1240, fill: 1310, cut: 70, area: 890 })
    expect(s).toBe('1,240 m³ (fill 1,310 / cut 70) · 890 m²')
  })
  it('handles a net-cut (negative volume)', () => {
    const s = formatVolume({ volume: -500, fill: 100, cut: 600, area: 300 })
    expect(s).toContain('-500 m³')
    expect(s).toContain('cut 600')
  })
  it('reports empty when area is zero', () => {
    expect(formatVolume({ volume: 0, fill: 0, cut: 0, area: 0 })).toBe('No DSM data under polygon')
  })
})
