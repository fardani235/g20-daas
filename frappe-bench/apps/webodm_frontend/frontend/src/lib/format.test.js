import { describe, it, expect } from 'vitest'
import { formatDistance, formatArea } from '@/lib/format'

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
