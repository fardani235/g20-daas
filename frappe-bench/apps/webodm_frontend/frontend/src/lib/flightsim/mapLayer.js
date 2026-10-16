// The simulation on the 2D map.
//
// Adds to the planner's Leaflet map, on top of the route it already draws:
// the aircraft, turning with its heading; the track flown so far; the
// camera's footprint on the ground; and the coverage raster. It is a view
// and nothing else - `update(state)` draws the state it is handed, the same
// one the 3D scene gets in the same frame.
//
// The flown track is kept in chunks: finished chunks are polylines that are
// never touched again, and only the chunk in progress is rebuilt per frame,
// so a mission of tens of thousands of pieces costs the same per frame as a
// small one.

import L from 'leaflet'
import { SHUTTER_FLASH_S } from './camera'

export const TRACK_COLOR = '#22d3ee'
export const FOOTPRINT_COLOR = '#10b981'
export const CHUNK = 256

const DISCONTINUITY_M = 0.5

/**
 * The flown track of a trajectory, cut into chunks of `chunk` timeline
 * pieces. Pure: `[lat, lng]` lists in, no map involved.
 */
export function createTrack(trajectory, chunk = CHUNK) {
  const seg = trajectory.segments
  const { frame } = trajectory
  const chunks = Math.ceil(seg.count / chunk)
  const cache = new Map()
  const latlng = (x, y) => { const [lng, lat] = frame.inverse(x, y); return [lat, lng] }

  // Lines of the pieces `from .. to - 1`: consecutive pieces join; a jump (the
  // aircraft carried back to the takeoff point) starts a new line.
  function lines(from, to, end = null) {
    const out = []
    let line = null
    let lx = NaN
    let ly = NaN
    const push = (x, y) => { line.push(latlng(x, y)); lx = x; ly = y }
    for (let i = from; i < to; i++) {
      const last = i === to - 1 && end
      const x1 = last ? end.x : seg.x1[i]
      const y1 = last ? end.y : seg.y1[i]
      if (!line || Math.hypot(seg.x0[i] - lx, seg.y0[i] - ly) > DISCONTINUITY_M) {
        line = []
        out.push(line)
        push(seg.x0[i], seg.y0[i])
      }
      if (Math.hypot(x1 - lx, y1 - ly) > 1e-6) push(x1, y1)
    }
    return out.filter(l => l.length > 1)
  }

  return {
    chunks,
    chunkSize: chunk,
    /** Number of chunks entirely behind timeline piece `index`. */
    completeBefore: index => Math.floor(index / chunk),
    /** Lines of finished chunk `k` (cached: it never changes). */
    chunkLines(k) {
      if (!cache.has(k)) cache.set(k, lines(k * chunk, Math.min((k + 1) * chunk, seg.count)))
      return cache.get(k)
    },
    /** Lines of the chunk in progress, up to the aircraft's position in `state`. */
    activeLines(state) {
      const k = Math.floor(state.index / chunk)
      return lines(k * chunk, state.index + 1, state)
    },
  }
}

// Leaflet's image overlay, holding a canvas instead of an <img>: the
// coverage raster is repainted in place and needs no re-encoding.
const CanvasOverlay = L.ImageOverlay.extend({
  _initImage() {
    const el = this._image = this._url
    L.DomUtil.addClass(el, 'leaflet-image-layer')
    if (this._zoomAnimated) L.DomUtil.addClass(el, 'leaflet-zoom-animated')
    if (this.options.className) L.DomUtil.addClass(el, this.options.className)
    el.onselectstart = L.Util.falseFn
    el.onmousemove = L.Util.falseFn
  },
})

const AIRCRAFT_SVG = {
  // A quadcopter from above: four rotors, a body, and a nose mark.
  multirotor: '<svg viewBox="-16 -16 32 32" width="34" height="34" aria-hidden="true">'
    + '<g stroke="#0f172a" stroke-width="1.2"><line x1="-9" y1="-9" x2="9" y2="9"/><line x1="9" y1="-9" x2="-9" y2="9"/></g>'
    + '<g fill="#f8fafc" stroke="#0f172a" stroke-width="1.2"><circle cx="-9" cy="-9" r="4.6"/><circle cx="9" cy="-9" r="4.6"/>'
    + '<circle cx="-9" cy="9" r="4.6"/><circle cx="9" cy="9" r="4.6"/></g>'
    + '<rect x="-4" y="-5.5" width="8" height="11" rx="2.5" fill="#0ea5e9" stroke="#0f172a" stroke-width="1.2"/>'
    + '<path d="M0,-13 L3.2,-7 L-3.2,-7 Z" fill="#ef4444" stroke="#0f172a" stroke-width="0.8"/></svg>',
  // A wing from above.
  wing: '<svg viewBox="-16 -16 32 32" width="36" height="36" aria-hidden="true">'
    + '<path d="M0,-13 L2.4,-3 L15,3 L15,6 L2.2,4 L1.6,10 L5,12.5 L5,14 L0,13 L-5,14 L-5,12.5 L-1.6,10 L-2.2,4 L-15,6 L-15,3 L-2.4,-3 Z" '
    + 'fill="#0ea5e9" stroke="#0f172a" stroke-width="1.2" stroke-linejoin="round"/></svg>',
}

/**
 * Put a simulation on `map`. `canvas` is the shared coverage canvas (or
 * null). Returns `{ update(state), setCoverageVisible, setFootprintVisible,
 * setFollow, destroy }`.
 */
export function createSimMapLayer(map, simulation, { canvas = null, onFollowEnd = null } = {}) {
  const { trajectory, coverage } = simulation
  const { frame } = trajectory
  const track = createTrack(trajectory)
  const group = L.layerGroup().addTo(map)
  const done = []                    // polylines of finished chunks, by chunk index
  let follow = false
  let footprintVisible = true
  let lastIndex = -1
  let destroyed = false

  // Under the planner's route lines, above the basemap.
  if (map.getPane && !map.getPane('flightsim-coverage')) {
    const pane = map.createPane('flightsim-coverage')
    pane.style.zIndex = 350
    pane.style.pointerEvents = 'none'
  }
  let overlay = null
  if (canvas && coverage) {
    const b = coverage.bounds
    overlay = new CanvasOverlay(canvas, [[b.south, b.west], [b.north, b.east]], {
      pane: 'flightsim-coverage', interactive: false, className: 'flightsim-coverage',
    })
  }

  const trackStyle = { color: TRACK_COLOR, weight: 4, opacity: 0.95, interactive: false, lineCap: 'round' }
  const active = L.polyline([], trackStyle).addTo(group)
  const footprint = L.polygon([], {
    color: FOOTPRINT_COLOR, weight: 2, fillColor: FOOTPRINT_COLOR, fillOpacity: 0.22, interactive: false,
  })

  const arrow = document.createElement('div')
  arrow.className = 'flightsim-aircraft'
  arrow.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;'
    + 'filter:drop-shadow(0 1px 2px rgba(0,0,0,.6));will-change:transform'
  arrow.innerHTML = trajectory.hovers ? AIRCRAFT_SVG.multirotor : AIRCRAFT_SVG.wing
  const marker = L.marker([trajectory.takeoff.lat, trajectory.takeoff.lng], {
    icon: L.divIcon({ className: 'flightsim-marker', html: arrow, iconSize: [36, 36], iconAnchor: [18, 18] }),
    interactive: false, keyboard: false, zIndexOffset: 2000,
  }).addTo(group)

  const stopFollowing = () => {
    if (!follow) return
    follow = false
    if (onFollowEnd) onFollowEnd()
  }
  if (map.on) map.on('dragstart', stopFollowing)

  function syncChunks(index) {
    const complete = track.completeBefore(index)
    while (done.length > complete) group.removeLayer(done.pop())
    while (done.length < complete) {
      const lines = track.chunkLines(done.length)
      done.push(L.polyline(lines, trackStyle).addTo(group))
    }
  }

  function update(state) {
    if (destroyed) return
    marker.setLatLng([state.lat, state.lng])
    arrow.style.transform = `rotate(${state.heading.toFixed(1)}deg)`
    arrow.style.opacity = state.airborne ? '1' : '0.75'

    if (state.index !== lastIndex) syncChunks(state.index)
    lastIndex = state.index
    active.setLatLngs(track.activeLines(state))

    const corners = footprintVisible ? simulation.footprintAt(state) : null
    if (corners) {
      const ring = []
      for (let k = 0; k < 8; k += 2) {
        const [lng, lat] = frame.inverse(corners[k], corners[k + 1])
        ring.push([lat, lng])
      }
      footprint.setLatLngs(ring)
      // Brighter for a moment when a photo is taken; faint and dashed while the camera is off.
      const shutter = state.capturing && state.sincePhoto < SHUTTER_FLASH_S
      footprint.setStyle({
        fillOpacity: shutter ? 0.5 : state.capturing ? 0.25 : 0.06,
        opacity: state.capturing ? 1 : 0.45,
        dashArray: state.capturing ? null : '4 4',
      })
      if (!group.hasLayer(footprint)) group.addLayer(footprint)
    } else if (group.hasLayer(footprint)) {
      group.removeLayer(footprint)
    }

    if (follow && map.panTo) map.panTo([state.lat, state.lng], { animate: false })
  }

  return {
    update,
    /** Number of polylines holding the finished part of the track (for tests and diagnostics). */
    finishedChunks: () => done.length,
    setCoverageVisible(visible) {
      if (!overlay) return
      if (visible && !map.hasLayer(overlay)) overlay.addTo(map)
      if (!visible && map.hasLayer(overlay)) map.removeLayer(overlay)
    },
    setFootprintVisible(visible) {
      footprintVisible = !!visible
      if (!visible && group.hasLayer(footprint)) group.removeLayer(footprint)
    },
    setFollow(on) { follow = !!on },
    /** What is on the map right now, for the browser checks. */
    snapshot() {
      const at = marker.getLatLng()
      return {
        aircraft: { lat: at.lat, lng: at.lng, rotation: arrow.style.transform },
        finishedChunks: done.length,
        footprint: group.hasLayer(footprint),
        coverage: !!overlay && map.hasLayer(overlay),
        following: follow,
      }
    },
    isFollowing: () => follow,
    destroy() {
      destroyed = true
      if (map.off) map.off('dragstart', stopFollowing)
      if (overlay && map.hasLayer(overlay)) map.removeLayer(overlay)
      map.removeLayer(group)
      done.length = 0
    },
    // Exposed for tests: the Leaflet objects this view owns.
    layers: { group, marker, active, footprint, arrow, get overlay() { return overlay } },
  }
}
