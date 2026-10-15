import { reactive } from 'vue'
import L from 'leaflet'
import { MIN_VERTICES, PLAN_TYPES, insertVertex, midpoints, moveVertex, removeVertex } from '@/lib/flightplan'

const COLOR = '#2563eb'
// Clicking within this many screen pixels of the first vertex closes a polygon.
const CLOSE_PX = 14

function handleIcon(kind, title = '') {
  // Plain divs rather than Leaflet's default image markers: no asset paths to
  // resolve, and they can be styled per role.
  const size = kind === 'mid' ? 12 : 16
  const style = kind === 'mid'
    ? `width:${size}px;height:${size}px;border-radius:9999px;background:#fff;border:2px solid ${COLOR};opacity:.55;cursor:copy`
    : `width:${size}px;height:${size}px;border-radius:9999px;background:${kind === 'first' ? COLOR : '#fff'};`
      + `border:2px solid ${COLOR};box-shadow:0 1px 3px rgba(0,0,0,.4);cursor:${kind === 'first' ? 'pointer' : 'grab'}`
  return L.divIcon({
    className: `shape-handle shape-handle-${kind}`,
    html: `<div style="${style}" title="${title}"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

/**
 * Draw and edit the survey shape on a Leaflet map: a polygon (area) or a
 * polyline (corridor centre line).
 *
 * Drawing: each click adds a corner; clicking the first corner again (area),
 * double-clicking or pressing Enter finishes; Backspace removes the last
 * corner; Escape abandons the drawing.
 * Editing: drag a corner to move it, drag a faint midpoint handle to add a
 * corner there, right-click (or long-press) a corner to remove it.
 *
 * The vertex list is owned by the caller: every change is reported through
 * `onChange(vertices, { final })` and the caller passes the list back with
 * `setShape`. `final` is false while a handle is still being dragged, so the
 * caller can defer expensive work until the drag ends.
 */
export function useShapeEditor(getMap, { onChange, onFinish } = {}) {
  const state = reactive({ drawing: false, type: PLAN_TYPES.area, count: 0 })

  let vertices = []
  let shape = null
  let handles = []
  let mids = []
  let editable = true

  const map = () => getMap()

  function emit(final = true) {
    state.count = vertices.length
    if (onChange) onChange(vertices.slice(), { final })
  }

  function drawShape() {
    const m = map()
    if (!m) return
    const latlngs = vertices.map(v => [v.lat, v.lng])
    const wantsPolygon = state.type === PLAN_TYPES.area && !state.drawing
    if (shape && (shape instanceof L.Polygon) !== wantsPolygon) {
      m.removeLayer(shape)
      shape = null
    }
    if (!latlngs.length) {
      if (shape) { m.removeLayer(shape); shape = null }
      return
    }
    if (!shape) {
      const opts = { color: COLOR, weight: 2.5, interactive: false }
      shape = wantsPolygon
        ? L.polygon(latlngs, { ...opts, fillOpacity: 0.08 })
        : L.polyline(latlngs, { ...opts, dashArray: state.drawing ? '6 6' : null })
      shape.addTo(m)
    } else {
      shape.setLatLngs(latlngs)
    }
  }

  function clearHandles() {
    const m = map()
    for (const h of [...handles, ...mids]) if (m) m.removeLayer(h)
    handles = []
    mids = []
  }

  function drawHandles() {
    const m = map()
    clearHandles()
    if (!m || !editable) return
    vertices.forEach((v, i) => {
      const closing = state.drawing && state.type === PLAN_TYPES.area && i === 0
        && vertices.length >= MIN_VERTICES.area
      const marker = L.marker([v.lat, v.lng], {
        icon: handleIcon(closing ? 'first' : 'vertex', closing ? 'Click to close the area' : 'Drag to move, right-click to remove'),
        draggable: !state.drawing,
        keyboard: false,
        zIndexOffset: 1000,
      })
      if (state.drawing) {
        if (closing) marker.on('click', e => { L.DomEvent.stop(e); finish() })
      } else {
        marker.on('drag', e => {
          vertices = moveVertex(vertices, i, e.target.getLatLng())
          drawShape()
          emit(false)
        })
        marker.on('dragend', () => { drawHandles(); emit(true) })
        marker.on('contextmenu', e => {
          L.DomEvent.stop(e)
          const next = removeVertex(vertices, i, state.type)
          if (next !== vertices) { vertices = next; redraw(); emit(true) }
        })
      }
      marker.addTo(m)
      handles.push(marker)
    })
    if (state.drawing) return
    for (const mid of midpoints(vertices, state.type)) {
      const marker = L.marker([mid.lat, mid.lng], {
        icon: handleIcon('mid', 'Drag to add a corner'), draggable: true, keyboard: false, zIndexOffset: 900,
      })
      let inserted = -1
      marker.on('dragstart', e => {
        vertices = insertVertex(vertices, mid.after, e.target.getLatLng())
        inserted = mid.after + 1
      })
      marker.on('drag', e => {
        vertices = moveVertex(vertices, inserted, e.target.getLatLng())
        drawShape()
        emit(false)
      })
      marker.on('dragend', () => { drawHandles(); emit(true) })
      marker.addTo(m)
      mids.push(marker)
    }
  }

  function redraw() {
    drawShape()
    drawHandles()
  }

  // -- drawing -------------------------------------------------------------------

  function onMapClick(e) {
    const m = map()
    if (m && state.type === PLAN_TYPES.area && vertices.length >= MIN_VERTICES.area) {
      const first = m.latLngToContainerPoint([vertices[0].lat, vertices[0].lng])
      if (first.distanceTo(m.latLngToContainerPoint(e.latlng)) <= CLOSE_PX) { finish(); return }
    }
    vertices = [...vertices, { lat: e.latlng.lat, lng: e.latlng.lng }]
    redraw()
    emit(true)
  }

  function onMapDblClick(e) {
    L.DomEvent.stop(e)
    // The double click's two single clicks each added the same corner: drop the echo.
    const n = vertices.length
    if (n >= 2 && vertices[n - 1].lat === vertices[n - 2].lat && vertices[n - 1].lng === vertices[n - 2].lng) {
      vertices = vertices.slice(0, -1)
      redraw()
      emit(true)
    }
    finish()
  }

  function onKey(e) {
    if (!state.drawing) return
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return
    if (e.key === 'Enter') { e.preventDefault(); finish() }
    else if (e.key === 'Escape') { e.preventDefault(); cancel() }
    else if (e.key === 'Backspace') {
      e.preventDefault()
      vertices = vertices.slice(0, -1)
      redraw()
      emit(true)
    }
  }

  function listen(on) {
    const m = map()
    if (m) {
      m[on ? 'on' : 'off']('click', onMapClick)
      m[on ? 'on' : 'off']('dblclick', onMapDblClick)
      if (m.doubleClickZoom) m.doubleClickZoom[on ? 'disable' : 'enable']()
      const el = m.getContainer && m.getContainer()
      if (el) el.style.cursor = on ? 'crosshair' : ''
    }
    document[on ? 'addEventListener' : 'removeEventListener']('keydown', onKey)
  }

  /** Begin drawing a new shape of `type`, discarding the current one. */
  function start(type) {
    if (state.drawing) listen(false)
    state.type = type
    vertices = []
    state.drawing = true
    redraw()
    listen(true)
    emit(true)
  }

  /** Finish drawing. Returns false (and keeps drawing) while there are too few corners. */
  function finish() {
    if (!state.drawing) return true
    if (vertices.length < MIN_VERTICES[state.type]) return false
    listen(false)
    state.drawing = false
    redraw()
    emit(true)
    if (onFinish) onFinish(vertices.slice())
    return true
  }

  /** Abandon the drawing in progress. */
  function cancel() {
    if (!state.drawing) return
    listen(false)
    state.drawing = false
    vertices = []
    redraw()
    emit(true)
  }

  /** Remove the shape. */
  function clear() {
    if (state.drawing) listen(false)
    state.drawing = false
    vertices = []
    redraw()
    emit(true)
  }

  /** Show `list` as a finished, editable shape (loading a plan, or the caller's own update). */
  function setShape(type, list) {
    if (state.drawing) listen(false)
    state.drawing = false
    state.type = type
    vertices = (list || []).map(v => ({ lat: v.lat, lng: v.lng }))
    state.count = vertices.length
    redraw()
  }

  function setEditable(value) {
    editable = !!value
    drawHandles()
  }

  function bounds() {
    return vertices.length ? L.latLngBounds(vertices.map(v => [v.lat, v.lng])) : null
  }

  function destroy() {
    if (state.drawing) listen(false)
    const m = map()
    clearHandles()
    if (m && shape) m.removeLayer(shape)
    shape = null
    vertices = []
  }

  return { state, start, finish, cancel, clear, setShape, setEditable, bounds, destroy, getVertices: () => vertices.slice() }
}
