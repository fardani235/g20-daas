// Leaflet layers for a computed mission: what the planner draws on the map.
//
// Pure construction (mission in, layers out) so the page only decides where
// to add them. Colours are fixed here: the survey shape is the app's blue
// (drawn by the shape editor), the flight path amber, turns a dashed grey.

import L from 'leaflet'

export const PATH_COLOR = '#f59e0b'
export const TURN_COLOR = '#64748b'
export const COVERAGE_COLOR = '#2563eb'
export const FOOTPRINT_COLOR = '#10b981'
export const TAKEOFF_COLOR = '#7c3aed'
export const LOW_COLOR = '#dc2626'

/** Runs of consecutive waypoints, split where the camera state changes: `[{ photo, latlngs }]`. */
export function pathSegments(waypoints) {
  const out = []
  for (let i = 0; i < (waypoints || []).length - 1; i++) {
    const a = waypoints[i]
    const b = waypoints[i + 1]
    const photo = !!a.photo && a.leg === b.leg
    const last = out[out.length - 1]
    if (last && last.photo === photo) last.latlngs.push([b.lat, b.lng])
    else out.push({ photo, latlngs: [[a.lat, a.lng], [b.lat, b.lng]] })
  }
  return out
}

/** Waypoints worth a marker at a given zoom: always line ends; terrain points only when zoomed in. */
export function visibleWaypoints(waypoints, { max = 600 } = {}) {
  const all = waypoints || []
  if (all.length <= max) return all
  const ends = all.filter(w => w.role !== 'via')
  if (ends.length <= max) return ends
  const stride = Math.ceil(ends.length / max)
  return ends.filter((_, i) => i % stride === 0 || i === ends.length - 1)
}

function badgeIcon(text, color, size = 22) {
  return L.divIcon({
    className: 'plan-badge',
    html: `<div style="background:${color};color:#fff;border-radius:9999px;width:${size}px;height:${size}px;`
      + `display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;`
      + `box-shadow:0 1px 3px rgba(0,0,0,.45);border:2px solid #fff">${text}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

export const takeoffIcon = assumed => badgeIcon('H', assumed ? '#94a3b8' : TAKEOFF_COLOR, 24)

// Start / end of the route: a small pin standing above the waypoint, so it is
// not hidden under the corner handle or the takeoff marker that often share
// the spot.
function pinIcon(text, color) {
  const size = 20
  return L.divIcon({
    className: 'plan-pin',
    html: `<div style="display:flex;flex-direction:column;align-items:center">`
      + `<div style="background:${color};color:#fff;border-radius:9999px;width:${size}px;height:${size}px;`
      + `display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;`
      + `box-shadow:0 1px 3px rgba(0,0,0,.45);border:2px solid #fff">${text}</div>`
      + `<div style="width:2px;height:10px;background:${color}"></div></div>`,
    iconSize: [size + 4, size + 14],
    iconAnchor: [(size + 4) / 2, size + 14],
  })
}

function ringLatLngs(geometry) {
  return (geometry?.coordinates?.[0] || []).map(([lng, lat]) => [lat, lng])
}

/**
 * Build the mission's layers. Returns `{ group, bounds }`: one feature group
 * to add to the map, and the bounds of the route (null for an empty mission).
 * `show` toggles the optional parts: `{ waypoints, coverage, footprint }`.
 */
export function buildPlanLayers(mission, show = {}) {
  const group = L.featureGroup()
  if (!mission || !mission.waypoints?.length) return { group, bounds: null }
  const wps = mission.waypoints
  const opts = { waypoints: true, coverage: true, footprint: true, ...show }

  if (opts.coverage && mission.type === 'corridor' && mission.coverage) {
    L.polygon(ringLatLngs(mission.coverage), {
      color: COVERAGE_COLOR, weight: 1, dashArray: '4 4', fillOpacity: 0.07, interactive: false,
    }).addTo(group)
  }
  if (opts.footprint && mission.footprint) {
    L.polygon(ringLatLngs(mission.footprint), {
      color: FOOTPRINT_COLOR, weight: 1.5, fillOpacity: 0.15, interactive: false,
    }).addTo(group)
  }

  for (const seg of pathSegments(wps)) {
    L.polyline(seg.latlngs, seg.photo
      ? { color: PATH_COLOR, weight: 3, opacity: 0.95, interactive: false }
      : { color: TURN_COLOR, weight: 2, opacity: 0.8, dashArray: '5 6', interactive: false },
    ).addTo(group)
  }

  // Transit to and from the takeoff point.
  const take = mission.takeoff
  if (take && !take.assumed) {
    const first = wps[0]
    const last = wps[wps.length - 1]
    L.polyline([[take.lat, take.lng], [first.lat, first.lng]],
      { color: TAKEOFF_COLOR, weight: 2, opacity: 0.7, dashArray: '2 6', interactive: false }).addTo(group)
    if (mission.params?.finish_action !== 'land') {
      L.polyline([[last.lat, last.lng], [take.lat, take.lng]],
        { color: TAKEOFF_COLOR, weight: 2, opacity: 0.7, dashArray: '2 6', interactive: false }).addTo(group)
    }
  }

  if (opts.waypoints) {
    for (const wp of visibleWaypoints(wps)) {
      const via = wp.role === 'via'
      const marker = L.circleMarker([wp.lat, wp.lng], {
        radius: via ? 2.5 : 4, color: '#fff', weight: 1, fillColor: via ? TURN_COLOR : PATH_COLOR, fillOpacity: 1,
      })
      const agl = wp.agl === null || wp.agl === undefined ? '' : `<br>about ${Math.round(wp.agl)} m above ground (estimate)`
      marker.bindTooltip(
        `Waypoint ${wp.index + 1} · line ${wp.line + 1}<br>${wp.alt} m above takeoff${agl}`,
        { direction: 'top', offset: [0, -4] },
      )
      marker.addTo(group)
    }
  }

  const first = wps[0]
  const last = wps[wps.length - 1]
  L.marker([first.lat, first.lng], { icon: pinIcon('S', '#16a34a'), interactive: false, keyboard: false, zIndexOffset: 1300 }).addTo(group)
  L.marker([last.lat, last.lng], { icon: pinIcon('E', '#dc2626'), interactive: false, keyboard: false, zIndexOffset: 1300 }).addTo(group)

  // Where the route comes closest to the terrain model.
  const low = mission.terrain?.clearance_min_at
  if (low && mission.terrain.clearance_min_m !== null) {
    L.circleMarker([low[1], low[0]], { radius: 7, color: LOW_COLOR, weight: 2, fillOpacity: 0.15 })
      .bindTooltip(`Lowest estimated clearance: ${Math.round(mission.terrain.clearance_min_m)} m`, { direction: 'top' })
      .addTo(group)
  }

  const bounds = L.latLngBounds(wps.map(w => [w.lat, w.lng]))
  if (take) bounds.extend([take.lat, take.lng])
  return { group, bounds }
}
