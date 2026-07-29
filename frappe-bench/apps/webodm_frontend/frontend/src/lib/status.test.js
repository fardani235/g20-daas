import { describe, it, expect } from 'vitest'
import { statusVariant } from './status'

describe('statusVariant', () => {
  it('maps Completed to success', () => {
    expect(statusVariant('Completed')).toBe('success')
  })

  it('maps Failed to destructive', () => {
    expect(statusVariant('Failed')).toBe('destructive')
  })

  it('maps in-flight statuses to default (the accent)', () => {
    expect(statusVariant('Running')).toBe('default')
    expect(statusVariant('In Progress')).toBe('default')
  })

  it('maps waiting statuses to warning', () => {
    expect(statusVariant('Pending')).toBe('warning')
    expect(statusVariant('Queued')).toBe('warning')
  })

  it('maps both spellings of cancelled to secondary', () => {
    // Project status uses "Cancelled", task status uses "Canceled".
    expect(statusVariant('Cancelled')).toBe('secondary')
    expect(statusVariant('Canceled')).toBe('secondary')
  })

  it('maps Planned to outline', () => {
    expect(statusVariant('Planned')).toBe('outline')
  })

  it('is case-insensitive', () => {
    expect(statusVariant('completed')).toBe('success')
    expect(statusVariant('RUNNING')).toBe('default')
  })

  it('falls back to secondary for unknown, empty, and nullish input', () => {
    expect(statusVariant('Wat')).toBe('secondary')
    expect(statusVariant('')).toBe('secondary')
    expect(statusVariant(null)).toBe('secondary')
    expect(statusVariant(undefined)).toBe('secondary')
  })
})
