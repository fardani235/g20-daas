import { reactive } from 'vue'
import L from 'leaflet'
import { length as turfLength, area as turfArea } from '@turf/turf'
import { formatDistance, formatArea } from '@/lib/format'

const DRAW_COLOR = '#2563eb'

// Click-to-draw distance/area/volume measurement over a Leaflet map.
// getMap() returns the live L.Map. onVolume(latlngs) -> Promise<string> is
// called when a volume polygon is finished; distance/area need no callback.
// Clicking within this many screen pixels of the first vertex closes/finishes
// the shape — the version-proof alternative to relying on dblclick.
const CLOSE_PX = 14

export function useMeasure(getMap, { onVolume } = {}) {
  // `drawing` is true while the map is accepting vertex clicks; the toolbar
  // uses it to show the Finish button.
  const state = reactive({ mode: null, value: 0, formatted: '', drawing: false })

  let points = [] // L.LatLng[]
  let shape = null // L.Polyline (distance) | L.Polygon (area/volume)
  let dots = [] // L.CircleMarker[]
  let reqToken = 0 // guards stale async volume results

  function toCoords(latlngs) {
    return latlngs.map(p => [p.lng, p.lat]) // GeoJSON is [lng, lat]
  }

  function isPolygonMode() {
    return state.mode === 'area' || state.mode === 'volume'
  }

  function recompute() {
    if (state.mode === 'distance') {
      if (points.length < 2) {
        state.value = 0
        state.formatted = formatDistance(0)
        return
      }
      const gj = { type: 'Feature', geometry: { type: 'LineString', coordinates: toCoords(points) } }
      state.value = turfLength(gj, { units: 'kilometers' }) * 1000
      state.formatted = formatDistance(state.value)
    } else if (state.mode === 'area') {
      if (points.length < 3) {
        state.value = 0
        state.formatted = formatArea(0)
        return
      }
      const ring = toCoords(points)
      ring.push(ring[0]) // close the polygon
      const gj = { type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] } }
      state.value = turfArea(gj)
      state.formatted = formatArea(state.value)
    }
    // 'volume' does no live computation while drawing; the readout is set on finish().
  }

  function redraw() {
    const map = getMap()
    if (!map) return
    if (!shape) {
      shape = isPolygonMode()
        ? L.polygon(points, { color: DRAW_COLOR, weight: 2, fillOpacity: 0.1 })
        : L.polyline(points, { color: DRAW_COLOR, weight: 2 })
      shape.addTo(map)
    } else {
      shape.setLatLngs(points)
    }
    for (const d of dots) map.removeLayer(d)
    dots = points.map((p, i) => {
      // The first vertex is the click-to-close target once the shape is
      // finishable, so make it larger with a pointer cursor.
      const isCloseTarget = i === 0 && points.length >= minPoints()
      return L.circleMarker(p, {
        radius: isCloseTarget ? 7 : 4,
        color: DRAW_COLOR,
        fillColor: isCloseTarget ? DRAW_COLOR : '#fff',
        fillOpacity: 1,
        weight: 2,
        className: isCloseTarget ? 'measure-close-target' : '',
      }).addTo(map)
    })
    if (shape) {
      shape.bindTooltip(state.formatted || '', { permanent: true, direction: 'top', className: 'measure-tooltip' })
      shape.openTooltip(points[points.length - 1])
    }
  }

  // Minimum vertices before a shape can be finished/closed.
  function minPoints() {
    return isPolygonMode() ? 3 : 2
  }

  function onClick(e) {
    const map = getMap()
    // If the click lands on/near the first vertex and we already have enough
    // points, treat it as "close the shape" rather than adding a vertex.
    if (map && points.length >= minPoints()) {
      const first = map.latLngToContainerPoint(points[0])
      const here = map.latLngToContainerPoint(e.latlng)
      if (first.distanceTo(here) <= CLOSE_PX) {
        finish()
        return
      }
    }
    points.push(e.latlng)
    recompute()
    redraw()
  }

  function onDblClick(e) {
    L.DomEvent.stop(e)
    finish()
  }

  function onKey(e) {
    if (e.key === 'Escape') clear()
  }

  function stopListening() {
    const map = getMap()
    if (map) {
      map.off('click', onClick)
      map.off('dblclick', onDblClick)
      map.doubleClickZoom.enable()
    }
    document.removeEventListener('keydown', onKey)
  }

  function setReadout(text) {
    state.formatted = text
    if (shape) shape.setTooltipContent(text)
  }

  async function finishVolume() {
    stopListening()
    state.drawing = false
    if (points.length < 3) {
      clear()
      return
    }
    redraw()
    setReadout('Computing…')
    const token = ++reqToken
    const latlngs = points.slice()
    try {
      const text = onVolume ? await onVolume(latlngs) : ''
      if (state.mode === 'volume' && token === reqToken) setReadout(text)
    } catch (e) {
      if (state.mode === 'volume' && token === reqToken) setReadout('Volume failed')
    }
  }

  function finish() {
    if (state.mode === 'volume') {
      finishVolume()
      return
    }
    recompute()
    redraw()
    stopListening()
    state.drawing = false
  }

  function start(mode) {
    clear()
    state.mode = mode
    state.drawing = true
    const map = getMap()
    if (!map) return
    map.doubleClickZoom.disable()
    map.on('click', onClick)
    map.on('dblclick', onDblClick)
    document.addEventListener('keydown', onKey)
  }

  function clear() {
    const map = getMap()
    stopListening()
    reqToken++ // invalidate any in-flight volume request
    if (map) {
      if (shape) map.removeLayer(shape)
      for (const d of dots) map.removeLayer(d)
    }
    shape = null
    dots = []
    points = []
    state.mode = null
    state.value = 0
    state.formatted = ''
    state.drawing = false
  }

  return { state, start, finish, clear }
}
