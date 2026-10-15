// Flight planning client + the pure helpers the planner pages share.
//
// The planner itself runs on the server (`webodm_core.flightplan`): this
// module sends a plan definition and receives the mission — flight lines,
// waypoints, estimates and findings. Nothing here lays out a flight line.
// The one piece of arithmetic kept client-side is the GSD <-> height
// relation, so the two linked inputs can update each other as the user
// types; it mirrors `photogrammetry.gsd_m` and is tested against the same
// reference values.

import { postMethod } from './api'
import { ensureCsrfToken } from './session'
import { frappeErrorMessage } from './utils'

const API = 'webodm_core.api.flightplan'

// Every call is a POST. These pages request data as soon as they mount, and
// on a full page load that can be before the session's CSRF token has
// arrived, so the first call waits for it instead of being refused.
async function call(method, body) {
  if (!window.csrf_token) await ensureCsrfToken()
  return postMethod(`${API}.${method}`, body)
}

export const getCatalog = () => call('catalog')
export const searchPlace = query => call('geocode', { query })
export const getElevationSources = project => call('elevation_sources', { project })
export const listPlans = project => call('list_plans', project ? { project } : {})
export const getPlan = name => call('get_plan', { name })
export const deletePlan = name => call('delete_plan', { name })
export const duplicatePlan = (name, { project, title } = {}) => call('duplicate_plan', { name, project, title })
export const getExportOptions = (name, triggers) => call('export_options', { name, triggers })
export const saveCamera = profile => call('save_camera', { profile })
export const deleteCamera = docname => call('delete_camera', { docname })
export const saveDrone = profile => call('save_drone', { profile })
export const deleteDrone = docname => call('delete_drone', { docname })

/**
 * Compute a mission without saving. `terrain: 'cached'` answers at once and
 * sets `mission.terrain_pending` when elevation data still has to be loaded;
 * `'fetch'` waits for it. Resolves `{ ok, mission }` or `{ ok: false, error }`.
 */
export const previewPlan = ({ definition, project, terrain = 'fetch', plan }) =>
  call('preview', { definition, project, terrain, plan })

export const savePlan = ({ name, project, title, description, definition }) =>
  call('save_plan', { name, project, title, description, definition })

/**
 * Download a plan in one format. Resolves `{ blob, filename, files }`; a
 * refusal (incompatible aircraft, blocking findings, unacknowledged
 * warnings) rejects with the server's explanation.
 */
export async function exportPlan(name, { format, trigger, acknowledged, firmware } = {}) {
  if (!window.csrf_token) await ensureCsrfToken()
  const headers = { 'Content-Type': 'application/json' }
  if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
  const res = await fetch(`/api/method/${API}.export_plan`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ name, format, trigger, acknowledged: acknowledged ? 1 : 0, firmware }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(frappeErrorMessage(err, 'Export failed'))
  }
  const blob = await res.blob()
  return {
    blob,
    filename: filenameFromDisposition(res.headers.get('Content-Disposition')) || `flight-plan.${format}`,
    files: Number(res.headers.get('X-Flight-Plan-Files')) || 1,
  }
}

export function filenameFromDisposition(header) {
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header || '')
  if (!m) return ''
  try { return decodeURIComponent(m[1]) } catch { return m[1] }
}

/** Hand a blob to the browser as a file download. */
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// -- photogrammetry (mirrors webodm_core/flightplan/photogrammetry.py) ---------

/** Ground sample distance in cm/px of a nadir photo from `heightM` above ground. */
export function gsdCm(camera, heightM) {
  if (!camera || !(heightM > 0)) return null
  return (camera.sensor_width_mm * heightM) / (camera.focal_length_mm * camera.image_width_px) * 100
}

/** Height above ground (m) that gives `gsd` cm/px. */
export function heightForGsd(camera, gsd) {
  if (!camera || !(gsd > 0)) return null
  return (gsd / 100) * camera.focal_length_mm * camera.image_width_px / camera.sensor_width_mm
}

// -- equipment -----------------------------------------------------------------

/** Drones grouped by manufacturer for an <optgroup> select; custom profiles first. */
export function groupDrones(drones) {
  const groups = new Map()
  for (const d of drones || []) {
    const key = d.source === 'custom' ? 'Your organization' : (d.manufacturer || 'Other')
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(d)
  }
  const out = [...groups.entries()].map(([label, items]) => ({
    label,
    items: items.slice().sort((a, b) => a.label.localeCompare(b.label)),
  }))
  const rank = label => (label === 'Your organization' ? 0 : label === 'Generic' ? 2 : 1)
  out.sort((a, b) => rank(a.label) - rank(b.label) || a.label.localeCompare(b.label))
  return out
}

/** The cameras a drone can carry, in the order the server ranked them (default first). */
export function camerasFor(drone, cameras) {
  if (!drone) return []
  const byId = new Map((cameras || []).map(c => [c.id, c]))
  return (drone.camera_choices || []).map(id => byId.get(id)).filter(Boolean)
}

/** The camera to select after the drone changes: keep the current one if the drone carries it. */
export function cameraAfterDroneChange(drone, cameras, currentId) {
  const choices = camerasFor(drone, cameras)
  if (choices.some(c => c.id === currentId)) return currentId
  return drone?.default_camera || choices[0]?.id || null
}

const LEVELS = {
  official: { label: 'Supported', variant: 'success', hint: 'The flight app documents importing this file.' },
  community: { label: 'Unofficial', variant: 'warning', hint: 'Works through a procedure the manufacturer does not document.' },
  boundary: { label: 'Boundary import', variant: 'default', hint: 'The app imports the survey boundary and plans its own lines.' },
  reference: { label: 'Reference', variant: 'secondary', hint: 'For review and GIS. Not a mission file.' },
}
export const supportLevel = level => LEVELS[level] || { label: '', variant: 'secondary', hint: '' }

/** One line on how missions reach an aircraft, for the drone picker. */
export function missionSupportSummary(drone) {
  if (!drone) return ''
  const mission = drone.mission || {}
  if (!mission.waypoints) return 'No autonomous waypoint flight: the plan is a reference only.'
  const formats = mission.formats || []
  const flyable = formats.filter(f => f.level !== 'boundary')
  if (flyable.length) {
    const f = flyable[0]
    return `${f.level === 'official' ? 'Mission files' : 'Mission files (unofficial)'}: ${f.app || f.id}`
  }
  if (formats.length) return `${formats[0].app || 'The flight app'} plans its own lines from the boundary.`
  return 'No mission file format known for this aircraft.'
}

export const AIRFRAME_LABELS = { multirotor: 'Multirotor', 'fixed-wing': 'Fixed-wing', vtol: 'VTOL' }

/** Rows of a drone's key figures; unknown values are shown as such, never as 0. */
export function droneSpecRows(drone) {
  if (!drone) return []
  const n = (v, unit, digits = 0) => (v === null || v === undefined || v === '' ? 'not published' : `${Number(v).toFixed(digits).replace(/\.0+$/, '')} ${unit}`)
  return [
    { label: 'Airframe', value: AIRFRAME_LABELS[drone.airframe] || drone.airframe },
    { label: 'Rated flight time', value: n(drone.max_flight_time_min, 'min') },
    { label: 'Speed limit in missions', value: n(drone.max_mission_speed_ms ?? drone.max_speed_ms, 'm/s', 1) },
    { label: 'Climb / descent', value: `${n(drone.max_ascent_ms, 'm/s', 1)} / ${n(drone.max_descent_ms, 'm/s', 1)}` },
    { label: 'Wind limit', value: n(drone.max_wind_ms, 'm/s', 1) },
    { label: 'Service ceiling', value: n(drone.service_ceiling_m, 'm') },
    { label: 'RTK', value: { none: 'No', optional: 'Optional', standard: 'Yes' }[drone.rtk] || '—' },
  ]
}

export function cameraSpecRows(camera) {
  if (!camera) return []
  const mp = (camera.image_width_px * camera.image_height_px / 1e6)
  return [
    { label: 'Sensor', value: `${camera.sensor_width_mm} × ${camera.sensor_height_mm} mm` },
    { label: 'Image', value: `${camera.image_width_px} × ${camera.image_height_px} px (${mp.toFixed(mp < 10 ? 1 : 0)} MP)` },
    { label: 'Focal length', value: `${camera.focal_length_mm} mm` },
    { label: 'Shortest photo interval', value: camera.min_interval_s ? `${camera.min_interval_s} s` : 'not published (2 s assumed)' },
    { label: 'Shutter', value: { mechanical: 'Mechanical', global: 'Global', electronic: 'Electronic (rolling)' }[camera.shutter] || camera.shutter },
  ]
}

// -- plan definition -------------------------------------------------------------

export const PLAN_TYPES = Object.freeze({ area: 'area', corridor: 'corridor' })
export const MIN_VERTICES = Object.freeze({ area: 3, corridor: 2 })

/** `{ name: default }` from the server's parameter table. */
export function defaultParams(specs) {
  const out = {}
  for (const s of specs || []) out[s.name] = s.default ?? null
  return out
}

/** A blank editor state. */
export function emptyPlanState(specs) {
  return {
    title: '',
    description: '',
    type: PLAN_TYPES.area,
    vertices: [],            // [{ lat, lng }]
    corridorWidth: 100,
    takeoff: null,           // { lat, lng } | null
    drone: null,
    camera: null,
    elevationSource: 'auto',
    params: defaultParams(specs),
  }
}

/** GeoJSON geometry for a vertex list, or null while there are too few vertices. */
export function geometryFor(type, vertices) {
  const pts = (vertices || []).map(v => [round7(v.lng), round7(v.lat)])
  if (pts.length < MIN_VERTICES[type]) return null
  if (type === PLAN_TYPES.corridor) return { type: 'LineString', coordinates: pts }
  return { type: 'Polygon', coordinates: [[...pts, pts[0]]] }
}

export function verticesFrom(geometry) {
  if (!geometry) return []
  let pts = geometry.type === 'Polygon' ? (geometry.coordinates?.[0] || []) : (geometry.coordinates || [])
  if (geometry.type === 'Polygon' && pts.length > 1) {
    const [a, b] = [pts[0], pts[pts.length - 1]]
    if (a[0] === b[0] && a[1] === b[1]) pts = pts.slice(0, -1)
  }
  return pts.map(p => ({ lat: p[1], lng: p[0] }))
}

const round7 = v => Math.round(v * 1e7) / 1e7

/**
 * The definition sent to the server. Parameters still at their default are
 * left out, so a saved plan records what the user chose and nothing else.
 */
export function buildDefinition(state, specs) {
  const defaults = defaultParams(specs)
  const params = {}
  for (const [key, value] of Object.entries(state.params || {})) {
    if (value === null || value === undefined || value === '') continue
    if (key in defaults && sameValue(value, defaults[key])) continue
    params[key] = value
  }
  // Only one of the linked pair is the user's input; the other is derived.
  if ((state.params?.altitude_from || 'altitude') === 'gsd') delete params.altitude_m
  else delete params.gsd_cm
  return {
    type: state.type,
    geometry: geometryFor(state.type, state.vertices),
    corridor_width_m: state.type === PLAN_TYPES.corridor ? Number(state.corridorWidth) || null : null,
    takeoff: state.takeoff ? [round7(state.takeoff.lng), round7(state.takeoff.lat)] : null,
    drone: state.drone,
    camera: state.camera,
    elevation_source: state.elevationSource || 'auto',
    params,
  }
}

function sameValue(a, b) {
  if (typeof a === 'number' || typeof b === 'number') return Number(a) === Number(b)
  return a === b
}

/** Editor state from a saved plan payload (`get_plan`). */
export function stateFromPlan(plan, specs) {
  const d = plan.definition || {}
  const state = emptyPlanState(specs)
  state.title = plan.title || ''
  state.description = plan.description || ''
  state.type = d.type || PLAN_TYPES.area
  state.vertices = verticesFrom(d.geometry)
  state.corridorWidth = d.corridor_width_m || 100
  state.takeoff = Array.isArray(d.takeoff) ? { lat: d.takeoff[1], lng: d.takeoff[0] } : null
  state.drone = d.drone || null
  state.camera = d.camera || null
  state.elevationSource = d.elevation_source || 'auto'
  state.params = { ...state.params, ...(d.params || {}) }
  // Fill the derived half of the height / resolution pair for display.
  const cam = plan.mission?.camera
  if (cam) {
    if (state.params.altitude_from === 'gsd') state.params.altitude_m = cam.altitude_m
    else state.params.gsd_cm = cam.gsd_cm
  }
  return state
}

/** Stable text of everything a save would store, for the unsaved-changes check. */
export function planFingerprint(state, specs) {
  return JSON.stringify([state.title, state.description, buildDefinition(state, specs)])
}

/** Whether the shape is complete enough to ask the server for a mission. */
export function canPreview(state) {
  return !!state.drone && (state.vertices?.length || 0) >= MIN_VERTICES[state.type]
}

// -- vertex editing (pure; the Leaflet glue is in useShapeEditor) ---------------

export function moveVertex(vertices, index, latlng) {
  return vertices.map((v, i) => (i === index ? { lat: latlng.lat, lng: latlng.lng } : v))
}

export function removeVertex(vertices, index, type) {
  if (vertices.length <= MIN_VERTICES[type]) return vertices
  return vertices.filter((_, i) => i !== index)
}

/** Insert a vertex after `afterIndex` (the midpoint handle that was dragged). */
export function insertVertex(vertices, afterIndex, latlng) {
  const out = vertices.slice()
  out.splice(afterIndex + 1, 0, { lat: latlng.lat, lng: latlng.lng })
  return out
}

/** Midpoints between consecutive vertices; a polygon also closes back to the first. */
export function midpoints(vertices, type) {
  const n = vertices.length
  if (n < 2) return []
  const count = type === PLAN_TYPES.area && n >= 3 ? n : n - 1
  const out = []
  for (let i = 0; i < count; i++) {
    const a = vertices[i]
    const b = vertices[(i + 1) % n]
    out.push({ after: i, lat: (a.lat + b.lat) / 2, lng: (a.lng + b.lng) / 2 })
  }
  return out
}

// -- formatting ------------------------------------------------------------------

export function formatDuration(seconds) {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s < 0) return '—'
  if (s < 60) return `${Math.round(s)} s`
  const minutes = Math.round(s / 60)
  if (minutes < 60) return `${minutes} min`
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`
}

export function formatLength(metres) {
  const m = Number(metres)
  if (!Number.isFinite(m)) return '—'
  if (Math.abs(m) < 1000) return `${Math.round(m)} m`
  return `${(m / 1000).toFixed(m < 10000 ? 2 : 1)} km`
}

export function formatAreaHa(m2) {
  const a = Number(m2)
  if (!Number.isFinite(a) || a < 0) return '—'
  if (a < 10000) return `${Math.round(a).toLocaleString('en-US')} m²`
  const ha = a / 10000
  if (ha < 100) return `${ha.toFixed(ha < 10 ? 2 : 1)} ha`
  return `${(a / 1e6).toFixed(2)} km²`
}

export const formatCount = n => (Number.isFinite(Number(n)) ? Number(n).toLocaleString('en-US') : '—')

export function formatGsd(cm) {
  const v = Number(cm)
  if (!Number.isFinite(v) || v <= 0) return '—'
  return `${v.toFixed(v < 10 ? 2 : 1)} cm/px`
}

export const BEARINGS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
/** "90° (E–W)" for a flight-line bearing in [0, 180). */
export function formatDirection(deg) {
  const d = Number(deg)
  if (!Number.isFinite(d)) return '—'
  const a = BEARINGS[Math.round(d / 45) % 8]
  const b = BEARINGS[(Math.round(d / 45) + 4) % 8]
  return `${Math.round(d)}° (${a}–${b})`
}

/** The headline numbers of a mission, each flagged as an estimate. */
export function estimateRows(mission) {
  if (!mission) return []
  const s = mission.stats
  const c = mission.camera
  const t = mission.terrain
  const gsd = t && t.gsd_min_cm && t.gsd_max_cm && Math.abs(t.gsd_max_cm - t.gsd_min_cm) > 0.05
    ? `${t.gsd_min_cm.toFixed(2)}–${t.gsd_max_cm.toFixed(2)} cm/px`
    : formatGsd(c.gsd_cm)
  return [
    { key: 'gsd', label: 'Ground resolution', value: gsd },
    { key: 'area', label: mission.type === 'corridor' ? 'Corridor area' : 'Survey area', value: formatAreaHa(s.area_m2) },
    { key: 'photos', label: 'Photos', value: formatCount(s.photo_count) },
    { key: 'time', label: 'Flight time', value: formatDuration(s.total_duration_s) },
    { key: 'distance', label: 'Flight distance', value: formatLength(s.total_distance_m) },
    { key: 'batteries', label: 'Batteries', value: formatCount(s.batteries) },
  ]
}

/** The secondary numbers, for the details table. */
export function detailRows(mission) {
  if (!mission) return []
  const s = mission.stats
  const c = mission.camera
  const l = mission.layout
  const rows = [
    { label: 'Flight height', value: `${c.altitude_m} m` },
    { label: 'Photo footprint', value: `${c.footprint_across_m} × ${c.footprint_along_m} m` },
    { label: 'Line spacing', value: `${c.line_spacing_m} m` },
    { label: 'Photo spacing', value: `${c.photo_spacing_m} m (every ${c.photo_interval_s} s)` },
    mission.type === 'corridor'
      ? { label: 'Parallel passes', value: String(l.passes) }
      : { label: 'Flight lines', value: `${s.line_count}${l.crosshatch ? ' (double grid)' : ''}, ${formatDirection(l.direction_deg)}${l.direction_auto ? ', automatic' : ''}` },
    { label: 'Waypoints', value: formatCount(s.waypoint_count) },
    { label: 'Survey only', value: `${formatLength(s.survey_distance_m)}, ${formatDuration(s.survey_duration_s)}` },
    { label: 'Usable time per battery', value: formatDuration(s.usable_flight_time_s) },
    { label: 'Furthest from takeoff', value: formatLength(s.furthest_from_takeoff_m) },
  ]
  if (s.max_altitude_m !== s.min_altitude_m) {
    rows.push({ label: 'Waypoint heights', value: `${s.min_altitude_m}–${s.max_altitude_m} m above takeoff` })
  }
  return rows
}

// -- findings --------------------------------------------------------------------

const SEVERITY = {
  error: { label: 'Blocks export', variant: 'destructive' },
  warning: { label: 'Needs your decision', variant: 'warning' },
  info: { label: 'Note', variant: 'secondary' },
}
export const severityInfo = s => SEVERITY[s] || SEVERITY.info

// How a finding or an assurance line was arrived at. The wording is the
// point: an estimate must never read like a verified fact.
const BASIS = {
  checked: { label: 'Checked against profile', variant: 'default', hint: 'Arithmetic against the aircraft and camera profile.' },
  estimated: { label: 'Estimate', variant: 'warning', hint: 'From a model with real uncertainty. Indicative only.' },
  advisory: { label: 'Advisory', variant: 'warning', hint: 'Compared with data that may be incomplete or out of date.' },
  not_checked: { label: 'Not checked', variant: 'destructive', hint: 'The platform has no data for this.' },
  // Guidance on using the plan or the file; not a check, so it gets no badge.
  note: { label: '', variant: 'secondary', hint: '' },
}
export const basisInfo = b => BASIS[b] || BASIS.not_checked

/** Findings split by severity, in display order. */
export function groupIssues(issues) {
  const out = { error: [], warning: [], info: [] }
  for (const i of issues || []) (out[i.severity] || out.info).push(i)
  return out
}

/** Short status for a plan: what stands between it and a mission file. */
export function planStatus(summary) {
  if (!summary) return { label: 'Not computed', variant: 'secondary' }
  if (summary.error) return { label: `${summary.error} problem${summary.error === 1 ? '' : 's'} to fix`, variant: 'destructive' }
  if (summary.warning) return { label: `${summary.warning} warning${summary.warning === 1 ? '' : 's'} to review`, variant: 'warning' }
  return { label: 'No problems found', variant: 'success' }
}

/** What the export dialog says about one format entry. */
export function exportVerdict(entry) {
  if (!entry.available) return { state: 'unavailable', text: entry.reason || 'Not available for this aircraft.' }
  if (entry.blocked) {
    const n = entry.blocking.length
    return { state: 'blocked', text: `Blocked by ${n} problem${n === 1 ? '' : 's'}: ${entry.blocking[0].message}` }
  }
  if (entry.needs_acknowledgement) {
    const n = entry.acknowledge.length
    return { state: 'acknowledge', text: `${n} warning${n === 1 ? '' : 's'} to acknowledge before download.` }
  }
  return { state: 'ready', text: entry.files > 1 ? `${entry.files} files in a zip, one per flight.` : 'Ready to download.' }
}

// -- terrain profile chart -------------------------------------------------------

/**
 * SVG path data for the terrain profile (`mission.terrain.profile`:
 * `[distance, ground, flight]` rows). Returns `{ ground, flight, area, x, y,
 * min, max, length }`, or null without a usable profile.
 */
export function profilePaths(profile, width, height, pad = 4) {
  if (!Array.isArray(profile) || profile.length < 2) return null
  const length = profile[profile.length - 1][0] - profile[0][0]
  if (!(length > 0)) return null
  let min = Infinity
  let max = -Infinity
  for (const [, ground, flight] of profile) {
    min = Math.min(min, ground, flight)
    max = Math.max(max, ground, flight)
  }
  if (max - min < 1) { max += 0.5; min -= 0.5 }
  const d0 = profile[0][0]
  const x = d => pad + ((d - d0) / length) * (width - 2 * pad)
  const y = v => height - pad - ((v - min) / (max - min)) * (height - 2 * pad)
  const line = idx => profile.map((row, i) => `${i ? 'L' : 'M'}${x(row[0]).toFixed(1)},${y(row[idx]).toFixed(1)}`).join(' ')
  const ground = line(1)
  return {
    ground,
    flight: line(2),
    area: `${ground} L${x(profile[profile.length - 1][0]).toFixed(1)},${height} L${x(d0).toFixed(1)},${height} Z`,
    x, y, min, max, length,
  }
}
