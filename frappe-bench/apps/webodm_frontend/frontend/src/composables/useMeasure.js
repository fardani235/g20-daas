import { reactive } from 'vue'
import L from 'leaflet'
import { length as turfLength, area as turfArea } from '@turf/turf'
import { formatDistance, formatArea } from '@/lib/format'

const DRAW_COLOR = '#2563eb'

// Click-to-draw distance/area measurement over a Leaflet map.
// getMap() returns the live L.Map (MapView holds it in a module-scoped let).
export function useMeasure(getMap) {
  const state = reactive({ mode: null, value: 0, formatted: '' })

  let points = [] // L.LatLng[]
  let shape = null // L.Polyline (distance) | L.Polygon (area)
  let dots = [] // L.CircleMarker[]

  function toCoords(latlngs) {
    return latlngs.map(p => [p.lng, p.lat]) // GeoJSON is [lng, lat]
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
  }

  function redraw() {
    const map = getMap()
    if (!map) return
    if (!shape) {
      shape = state.mode === 'area'
        ? L.polygon(points, { color: DRAW_COLOR, weight: 2, fillOpacity: 0.1 })
        : L.polyline(points, { color: DRAW_COLOR, weight: 2 })
      shape.addTo(map)
    } else {
      shape.setLatLngs(points)
    }
    for (const d of dots) map.removeLayer(d)
    dots = points.map(p =>
      L.circleMarker(p, { radius: 4, color: DRAW_COLOR, fillColor: '#fff', fillOpacity: 1, weight: 2 }).addTo(map)
    )
    if (shape) {
      shape.bindTooltip(state.formatted || '', { permanent: true, direction: 'top', className: 'measure-tooltip' })
      shape.openTooltip(points[points.length - 1])
    }
  }

  function onClick(e) {
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

  function start(mode) {
    clear()
    state.mode = mode
    const map = getMap()
    if (!map) return
    map.doubleClickZoom.disable()
    map.on('click', onClick)
    map.on('dblclick', onDblClick)
    document.addEventListener('keydown', onKey)
  }

  function finish() {
    recompute()
    redraw()
    stopListening()
  }

  function clear() {
    const map = getMap()
    stopListening()
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
  }

  return { state, start, finish, clear }
}
