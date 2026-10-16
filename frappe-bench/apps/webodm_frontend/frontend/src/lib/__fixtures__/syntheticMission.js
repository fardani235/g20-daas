// A mission of any size, in the shape the planner returns, for tests that
// need thousands of waypoints (performance) or a geometry that is easy to
// reason about (a flat grid of straight lines). The real planner's output is
// in flightsim.json; this builder only reproduces its structure.

import { LocalFrame } from '../flightsim/frame'

const round7 = v => Math.round(v * 1e7) / 1e7

/**
 * `lines` parallel east-west lines `lineLength` m long and `spacing` m
 * apart, flown boustrophedon, each with `vias` intermediate waypoints,
 * split evenly into `flights`. Heights follow `altitude(x, y)` when given.
 */
export function syntheticMission({
  lines = 10, lineLength = 400, spacing = 40, vias = 0, flights = 1, photoSpacing = 20, altitude = 100,
  speed = 10, takeoff = [-50, -50], airframe = 'multirotor', finish = 'rth', origin = [107.61, -6.82],
  ground = null,
} = {}) {
  const frame = new LocalFrame(origin[1], origin[0])
  const waypoints = []
  const legs = []
  const altAt = typeof altitude === 'function' ? altitude : () => altitude
  for (let li = 0; li < lines; li++) {
    const y = li * spacing
    const forward = li % 2 === 0
    const from = waypoints.length
    for (let k = 0; k <= vias + 1; k++) {
      const f = k / (vias + 1)
      const x = forward ? f * lineLength : (1 - f) * lineLength
      const [lng, lat] = frame.inverse(x, y)
      const alt = Math.round(altAt(x, y) * 10) / 10
      const g = ground ? Math.round(ground(x, y) * 10) / 10 : null
      waypoints.push({
        index: waypoints.length, lng: round7(lng), lat: round7(lat), alt,
        ground: g, agl: g === null ? null : Math.round((ground(takeoff[0], takeoff[1]) + alt - g) * 10) / 10,
        leg: li, line: li, role: k === 0 ? 'start' : k === vias + 1 ? 'end' : 'via', photo: k !== vias + 1,
        heading: forward ? 90 : 270,
      })
    }
    legs.push({
      index: li, line: li, from, to: waypoints.length - 1, length_m: lineLength,
      photos: Math.floor(lineLength / photoSpacing + 1e-9) + 1, heading: forward ? 90 : 270,
    })
  }
  const per = Math.ceil(lines / flights)
  const flightList = []
  for (let a = 0; a < lines; a += per) {
    const b = Math.min(lines - 1, a + per - 1)
    flightList.push({
      index: flightList.length, leg_from: a, leg_to: b, wp_from: legs[a].from, wp_to: legs[b].to,
      duration_s: 0, distance_m: 0, photos: legs.slice(a, b + 1).reduce((s, l) => s + l.photos, 0), battery_percent: 0,
    })
  }
  const [tlng, tlat] = frame.inverse(takeoff[0], takeoff[1])
  const ring = [[0, 0], [lineLength, 0], [lineLength, (lines - 1) * spacing], [0, (lines - 1) * spacing]]
    .map(([x, y]) => frame.inverse(x, y).map(round7))
  ring.push(ring[0])
  const alts = waypoints.map(w => w.alt)
  return {
    version: 1,
    type: 'area',
    frame: { lat0: frame.lat0, lng0: frame.lng0 },
    takeoff: { lng: round7(tlng), lat: round7(tlat), assumed: false, ground_m: ground ? ground(takeoff[0], takeoff[1]) : null },
    camera: {
      altitude_m: typeof altitude === 'number' ? altitude : 100, gsd_cm: 2.74, footprint_across_m: 144.5,
      footprint_along_m: 108.4, line_spacing_m: spacing, photo_spacing_m: photoSpacing,
      photo_interval_s: photoSpacing / speed, orientation: 'landscape', gimbal_pitch_deg: -90,
    },
    layout: { direction_deg: 90, direction_auto: false, line_count: lines, passes: null, overshoot_m: 0, crosshatch: false },
    legs,
    waypoints,
    coverage: { type: 'Polygon', coordinates: [ring] },
    footprint: null,
    flights: flightList,
    stats: {
      area_m2: lineLength * (lines - 1) * spacing, line_count: lines, leg_count: lines,
      waypoint_count: waypoints.length, photo_count: legs.reduce((s, l) => s + l.photos, 0),
      total_distance_m: null, total_duration_s: null, speed_ms: speed, flights: flightList.length,
      batteries: flightList.length, usable_flight_time_s: 45 * 60 * 0.8 * 0.75,
      max_altitude_m: Math.max(...alts), min_altitude_m: Math.min(...alts), rth_altitude_m: Math.max(...alts),
    },
    terrain: null,
    params: {
      speed_ms: speed, transit_speed_ms: null, finish_action: finish, reserve_percent: 25, rth_altitude_m: null,
      front_overlap: 80, side_overlap: 70, gimbal_pitch_deg: -90, min_clearance_m: 20,
    },
    equipment: {
      drone: {
        id: 'test-drone', label: 'Test drone', airframe, max_flight_time_min: 45, endurance_factor: 0.8,
        max_speed_ms: 21, max_mission_speed_ms: 15, cruise_speed_ms: 10, max_ascent_ms: 6, max_descent_ms: 6,
        min_turn_radius_m: airframe === 'multirotor' ? null : 50, min_speed_ms: airframe === 'multirotor' ? null : 8,
      },
      camera: {
        id: 'test-camera', label: 'Test camera', sensor_width_mm: 17.3, sensor_height_mm: 13, image_width_px: 5280,
        image_height_px: 3956, focal_length_mm: 12,
      },
    },
    issues: [],
    assurance: [],
    notice: '',
    summary: { error: 0, warning: 0, info: 0, worst: null, exportable: true },
  }
}

/** A terrain grid payload over the box of `mission`, sampled from `surface(x, y)` every `spacing` metres. */
export function syntheticTerrain(mission, surface, { spacing = 30, pad = 200 } = {}) {
  const frame = LocalFrame.ofMission(mission)
  let xmin = Infinity
  let xmax = -Infinity
  let ymin = Infinity
  let ymax = -Infinity
  for (const w of mission.waypoints) {
    const [x, y] = frame.forward(w.lng, w.lat)
    xmin = Math.min(xmin, x); xmax = Math.max(xmax, x); ymin = Math.min(ymin, y); ymax = Math.max(ymax, y)
  }
  const [tx, ty] = frame.forward(mission.takeoff.lng, mission.takeoff.lat)
  xmin = Math.min(xmin, tx) - pad; xmax = Math.max(xmax, tx) + pad
  ymin = Math.min(ymin, ty) - pad; ymax = Math.max(ymax, ty) + pad
  const [west, south] = frame.inverse(xmin, ymin)
  const [east, north] = frame.inverse(xmax, ymax)
  const cols = Math.max(2, Math.ceil((xmax - xmin) / spacing) + 1)
  const rows = Math.max(2, Math.ceil((ymax - ymin) / spacing) + 1)
  const values = []
  for (let r = 0; r < rows; r++) {
    const row = []
    const lat = south + (north - south) * r / (rows - 1)
    for (let c = 0; c < cols; c++) {
      const lng = west + (east - west) * c / (cols - 1)
      const [x, y] = frame.forward(lng, lat)
      row.push(Math.round(surface(x, y) * 100) / 100)
    }
    values.push(row)
  }
  return {
    south, west, north, east, rows, cols, values, spacing_m: spacing,
    source: { id: 'test-dem', label: 'Test DEM', kind: 'global-dem', resolution_m: spacing, vertical_accuracy_m: 5, surface: 'terrain' },
  }
}
