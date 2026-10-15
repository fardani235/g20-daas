import { describe, it, expect, beforeEach, vi } from 'vitest'
import L from 'leaflet'
import { useShapeEditor } from '@/composables/useShapeEditor'

// Leaflet's vector renderer cannot mount in jsdom, so the map is a stand-in
// with only the surface the editor touches (same approach as the measure
// tool's tests). The editor's own logic is real: which clicks add a corner,
// what finishes or abandons a shape, and what it reports to its owner.
function makeMap() {
  const handlers = {}
  return {
    doubleClickZoom: { enable: vi.fn(), disable: vi.fn() },
    on(event, fn) { (handlers[event] ||= []).push(fn) },
    off(event, fn) { handlers[event] = (handlers[event] || []).filter(h => h !== fn) },
    fire(event, payload) { for (const h of [...(handlers[event] || [])]) h(payload) },
    listeners: event => (handlers[event] || []).length,
    removeLayer: vi.fn(),
    getContainer: () => ({ style: {} }),
    // 1 degree = 1000 px: far-apart test points never "close" by accident.
    latLngToContainerPoint: p => {
      const ll = L.latLng(p)
      return L.point(ll.lng * 1000, -ll.lat * 1000)
    },
  }
}

function stubMounting() {
  const self = function () { return this }
  vi.spyOn(L.Polygon.prototype, 'addTo').mockImplementation(self)
  vi.spyOn(L.Polyline.prototype, 'addTo').mockImplementation(self)
  vi.spyOn(L.Marker.prototype, 'addTo').mockImplementation(self)
}

const click = (map, lat, lng) => map.fire('click', { latlng: L.latLng(lat, lng) })
const key = k => document.dispatchEvent(new KeyboardEvent('keydown', { key: k }))

describe('useShapeEditor', () => {
  let map, changes, finished, editor

  beforeEach(() => {
    vi.restoreAllMocks()
    stubMounting()
    map = makeMap()
    changes = []
    finished = []
    editor = useShapeEditor(() => map, {
      onChange: (vertices, meta) => changes.push({ vertices, final: meta.final }),
      onFinish: vertices => finished.push(vertices),
    })
  })

  it('collects corners while drawing and reports each one', () => {
    editor.start('area')
    expect(editor.state.drawing).toBe(true)
    expect(map.doubleClickZoom.disable).toHaveBeenCalled()
    click(map, 0, 0)
    click(map, 0, 1)
    expect(editor.getVertices()).toEqual([{ lat: 0, lng: 0 }, { lat: 0, lng: 1 }])
    expect(changes.at(-1)).toEqual({ vertices: editor.getVertices(), final: true })
    expect(editor.state.count).toBe(2)
  })

  it('will not finish an area with fewer than three corners', () => {
    editor.start('area')
    click(map, 0, 0)
    click(map, 0, 1)
    expect(editor.finish()).toBe(false)
    expect(editor.state.drawing).toBe(true)
    click(map, 1, 1)
    expect(editor.finish()).toBe(true)
    expect(editor.state.drawing).toBe(false)
    expect(finished).toHaveLength(1)
    expect(finished[0]).toHaveLength(3)
    // The map stops collecting clicks once the shape is finished.
    expect(map.listeners('click')).toBe(0)
    expect(map.doubleClickZoom.enable).toHaveBeenCalled()
  })

  it('a corridor needs only two points', () => {
    editor.start('corridor')
    click(map, 0, 0)
    expect(editor.finish()).toBe(false)
    click(map, 0, 1)
    expect(editor.finish()).toBe(true)
  })

  it('clicking the first corner again closes an area instead of adding a corner', () => {
    editor.start('area')
    click(map, 0, 0)
    click(map, 0, 1)
    click(map, 1, 1)
    click(map, 0.001, 0.001)            // within a few pixels of the first corner
    expect(editor.state.drawing).toBe(false)
    expect(editor.getVertices()).toHaveLength(3)
  })

  it('a double click finishes and drops the echoed corner', () => {
    editor.start('corridor')
    click(map, 0, 0)
    click(map, 0, 1)
    click(map, 0, 1)                    // the second click of the double click
    map.fire('dblclick', { latlng: L.latLng(0, 1), originalEvent: new MouseEvent('dblclick') })
    expect(editor.state.drawing).toBe(false)
    expect(editor.getVertices()).toEqual([{ lat: 0, lng: 0 }, { lat: 0, lng: 1 }])
  })

  it('keyboard: Backspace removes the last corner, Enter finishes, Escape abandons', () => {
    editor.start('area')
    click(map, 0, 0)
    click(map, 0, 1)
    click(map, 1, 1)
    click(map, 1, 0)
    key('Backspace')
    expect(editor.getVertices()).toHaveLength(3)
    key('Enter')
    expect(editor.state.drawing).toBe(false)
    expect(editor.getVertices()).toHaveLength(3)

    editor.start('area')
    click(map, 5, 5)
    key('Escape')
    expect(editor.state.drawing).toBe(false)
    expect(editor.getVertices()).toEqual([])
    expect(changes.at(-1).vertices).toEqual([])
  })

  it('ignores keys typed into a form field', () => {
    editor.start('area')
    click(map, 0, 0)
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }))
    expect(editor.getVertices()).toHaveLength(1)
    input.remove()
  })

  it('setShape shows a stored shape without reporting a change', () => {
    const stored = [{ lat: 0, lng: 0 }, { lat: 0, lng: 1 }, { lat: 1, lng: 1 }]
    editor.setShape('area', stored)
    expect(editor.getVertices()).toEqual(stored)
    expect(editor.state).toMatchObject({ drawing: false, type: 'area', count: 3 })
    expect(changes).toEqual([])
    expect(editor.bounds().getNorthEast()).toEqual(L.latLng(1, 1))
  })

  it('starting again discards the previous shape', () => {
    editor.setShape('area', [{ lat: 0, lng: 0 }, { lat: 0, lng: 1 }, { lat: 1, lng: 1 }])
    editor.start('corridor')
    expect(editor.getVertices()).toEqual([])
    expect(editor.state.type).toBe('corridor')
    expect(map.listeners('click')).toBe(1)
  })

  it('clear and destroy stop listening', () => {
    editor.start('area')
    click(map, 0, 0)
    editor.clear()
    expect(map.listeners('click')).toBe(0)
    expect(editor.bounds()).toBeNull()
    editor.start('area')
    editor.destroy()
    expect(map.listeners('click')).toBe(0)
  })
})
