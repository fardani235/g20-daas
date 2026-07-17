import { describe, it, expect } from 'vitest'
import { useMeasure } from '@/composables/useMeasure'

// Import-resolution smoke test: exercises the module-level imports
// (@turf/turf, @/lib/format, leaflet) and the returned interface shape.
// useMeasure() does not touch the map until start()/redraw(), so a
// null-returning accessor is safe here.
describe('useMeasure module', () => {
  it('imports cleanly and returns the expected interface', () => {
    const m = useMeasure(() => null)
    expect(typeof m.start).toBe('function')
    expect(typeof m.finish).toBe('function')
    expect(typeof m.clear).toBe('function')
    expect(m.state).toMatchObject({ mode: null, value: 0, formatted: '' })
  })
})
