import { describe, it, expect, beforeEach, vi } from 'vitest'
import L from 'leaflet'
import { useMeasure } from '@/composables/useMeasure'

// Leaflet's vector renderer cannot mount in jsdom (Path.beforeAdd dereferences a
// null renderer), so the map is a stand-in exposing only the surface useMeasure
// touches. The composable's own logic -- point collection, mode/token guards,
// readout text -- is real; only Leaflet's DOM output is stubbed.
function makeMap() {
  const handlers = {}
  return {
    doubleClickZoom: { enable() {}, disable() {} },
    on(event, fn) { (handlers[event] ||= []).push(fn) },
    off(event, fn) {
      if (!handlers[event]) return
      handlers[event] = handlers[event].filter(h => h !== fn)
    },
    fire(event, payload) { for (const h of handlers[event] || []) h(payload) },
    removeLayer() {},
    addLayer() {},
  }
}

// L.polygon/L.circleMarker still construct fine; only .addTo(map) needs taming.
function stubLayerMounting() {
  const noopAddTo = function () { return this }
  vi.spyOn(L.Polygon.prototype, 'addTo').mockImplementation(noopAddTo)
  vi.spyOn(L.Polyline.prototype, 'addTo').mockImplementation(noopAddTo)
  vi.spyOn(L.CircleMarker.prototype, 'addTo').mockImplementation(noopAddTo)
  vi.spyOn(L.Polygon.prototype, 'bindTooltip').mockImplementation(function () { return this })
  vi.spyOn(L.Polygon.prototype, 'openTooltip').mockImplementation(function () { return this })
  vi.spyOn(L.Polygon.prototype, 'setTooltipContent').mockImplementation(function () { return this })
}

function drawTriangle(measure, map) {
  measure.start('volume')
  for (const [lat, lng] of [[0, 0], [0, 0.01], [0.01, 0.01]]) {
    map.fire('click', { latlng: L.latLng(lat, lng) })
  }
}

describe('useMeasure volume recompute', () => {
  let map
  beforeEach(() => {
    vi.restoreAllMocks()
    stubLayerMounting()
    map = makeMap()
  })

  it('re-runs onVolume for the same polygon when recomputeVolume is called', async () => {
    const onVolume = vi.fn(async () => 'first')
    const measure = useMeasure(() => map, { onVolume })
    drawTriangle(measure, map)
    await measure.finish()
    expect(measure.state.formatted).toBe('first')

    onVolume.mockImplementation(async () => 'second')
    await measure.recomputeVolume()

    expect(onVolume).toHaveBeenCalledTimes(2)
    // Same three vertices sent again -- the drawn polygon is reused, not redrawn.
    expect(onVolume.mock.calls[1][0]).toHaveLength(3)
    expect(measure.state.formatted).toBe('second')
  })

  it('does nothing when there is no finished volume polygon', async () => {
    const onVolume = vi.fn(async () => 'x')
    const measure = useMeasure(() => map, { onVolume })
    await measure.recomputeVolume()
    expect(onVolume).not.toHaveBeenCalled()
  })

  it('does not recompute while the polygon is still being drawn', async () => {
    const onVolume = vi.fn(async () => 'x')
    const measure = useMeasure(() => map, { onVolume })
    drawTriangle(measure, map) // started but never finished
    await measure.recomputeVolume()
    expect(onVolume).not.toHaveBeenCalled()
  })

  it('shows a failure readout when the recompute rejects', async () => {
    const onVolume = vi.fn(async () => 'ok')
    const measure = useMeasure(() => map, { onVolume })
    drawTriangle(measure, map)
    await measure.finish()

    onVolume.mockImplementation(async () => {
      throw new Error('boom')
    })
    await measure.recomputeVolume()
    expect(measure.state.formatted).toBe('Volume failed')
  })

  it('discards a recompute result after the measurement is cleared', async () => {
    let release
    const onVolume = vi.fn(async () => 'done')
    const measure = useMeasure(() => map, { onVolume })
    drawTriangle(measure, map)
    await measure.finish()

    onVolume.mockImplementation(() => new Promise(resolve => { release = resolve }))
    const pending = measure.recomputeVolume()
    measure.clear()
    release('stale')
    await pending

    expect(measure.state.formatted).toBe('')
  })
})
