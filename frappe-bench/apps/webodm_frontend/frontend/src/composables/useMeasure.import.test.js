import { describe, it, expect } from 'vitest'
import { useMeasure } from '@/composables/useMeasure'

// Import-resolution + interface smoke test. useMeasure() does not touch the map
// until start()/finish(), so a null-returning accessor and no-op onVolume are safe.
describe('useMeasure module', () => {
  it('imports cleanly and returns the expected interface', () => {
    const m = useMeasure(() => null)
    expect(typeof m.start).toBe('function')
    expect(typeof m.finish).toBe('function')
    expect(typeof m.clear).toBe('function')
    expect(m.state).toMatchObject({ mode: null, value: 0, formatted: '', drawing: false })
  })

  it('accepts an onVolume option without changing the interface', () => {
    const m = useMeasure(() => null, { onVolume: async () => 'x' })
    expect(typeof m.start).toBe('function')
    expect(m.state.mode).toBe(null)
  })
})
