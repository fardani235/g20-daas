// A mission, flown: where the aircraft is at every second.
//
// `buildTrajectory(mission, { terrain })` turns the mission the planner
// computed into a timeline of motion - pre-flight, climb, transit, every
// flight line and turn, return, landing, and a battery change between
// flights - and `trajectory.sample(t)` answers "where is it, how fast, how
// high above the ground, how much battery, how many photos" for any time.
//
// What it is and is not:
//
// * It **reads** the mission and never changes it. Waypoints, legs, flights,
//   speeds, heights and photo counts are the planner's; nothing here lays
//   out a line or decides an altitude.
// * It is **deterministic**: the timeline is built once from closed-form
//   speed profiles, and a sample is a pure function of the time. Playing,
//   scrubbing and jumping all land on the same state.
// * It is an **estimate**. The motion model is the one behind the planner's
//   own time estimate (accelerate and brake at 2.5 m/s², two seconds to yaw
//   at a line end, the aircraft's published climb and descent rates), flown
//   in still air. It is not telemetry and it verifies nothing about safety.
//
// Units: metres, seconds, degrees. Positions are in the mission's local
// frame (x east, y north); `alt` is height above the takeoff point, exactly
// as in the mission and its exports.

import { LocalFrame, angleDiff, bearingDeg, lerpAngle } from './frame'
import {
  cornerSpeed, lastAtOrBefore, planSpeeds, profileAt, profileTimeAt, segmentProfile, smoothstep,
} from './kinematics'
import { bearingToAngle, dubinsPath, loiterCircles, sampleDubins } from './dubins'

/** The motion model. The first block mirrors `webodm_core/flightplan/mission.py`. */
export const DYNAMICS = Object.freeze({
  accel: 2.5,               // m/s², along track (planner: ACCEL_MS2)
  turnSeconds: 2.0,         // yaw to the next heading at a line end (planner: TURN_SECONDS)
  groundPrep: 10.0,         // spin-up and checks before lift-off   } together the planner's
  landingSettle: 10.0,      // touchdown and motors off             } GROUND_OVERHEAD_S (20 s)
  defaultAscent: 3.0,       // m/s when the profile publishes none (planner: DEFAULT_ASCENT_MS)
  defaultDescent: 2.0,      // m/s (planner: DEFAULT_DESCENT_MS)

  verticalAccel: 2.0,       // m/s², climb and descent
  yawRate: 45.0,            // °/s; a turn takes |Δheading| / yawRate, at most turnSeconds
  cornerBlend: 0.8,         // s to change direction at a bend flown without stopping
  minClimb: 2.0,            // m: lift-off height when the first waypoint is at or below the takeoff point
  hoverSeconds: 5.0,        // "hover in place" before the budgeted return
  batterySwap: 60.0,        // s on the ground between flights; not counted as flight time
  crawl: 0.3,               // m/s floor for a multirotor slowed by a steep climb
  bankLimitDeg: 30,         // assumed bank for an aircraft with no published turn radius
  arcStepDeg: 6,            // resolution of a curved turn
  gravity: 9.80665,
})

export const PHASE = Object.freeze({
  PREFLIGHT: 0, TAKEOFF: 1, TRANSIT_OUT: 2, SURVEY: 3, TURN: 4, HOVER: 5, RETURN: 6, LANDING: 7, LANDED: 8, BATTERY: 9,
})

/** Per phase id: a stable key, what to call it, and whether the aircraft is in the air. */
export const PHASE_INFO = Object.freeze([
  { key: 'preflight', label: 'Pre-flight', airborne: false },
  { key: 'takeoff', label: 'Takeoff', airborne: true },
  { key: 'transit_out', label: 'Transit to the survey', airborne: true },
  { key: 'survey', label: 'Survey line', airborne: true },
  { key: 'turn', label: 'Turn', airborne: true },
  { key: 'hover', label: 'Hover', airborne: true },
  { key: 'return', label: 'Return to takeoff', airborne: true },
  { key: 'landing', label: 'Landing', airborne: true },
  { key: 'landed', label: 'Landed', airborne: false },
  { key: 'battery', label: 'Battery change', airborne: false },
])

const HOLD = 0
const MOVE = 1
const EPS = 1e-6
const ATTITUDE_DT = 0.35   // s either side for the finite differences behind pitch and roll

export class SimulationError extends Error {}

const num = (v, fallback = null) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? fallback : Number(v))

/**
 * Build the timeline of `mission`. `terrain` is a sampler from
 * `createTerrain` (or null: ground unknown away from the waypoints).
 * `options.dynamics` overrides entries of `DYNAMICS`.
 *
 * Throws `SimulationError` for a mission without a route.
 */
export function buildTrajectory(mission, { terrain = null, options = {} } = {}) {
  const wps = mission?.waypoints
  const legs = mission?.legs
  if (!Array.isArray(wps) || wps.length < 2 || !Array.isArray(legs) || !legs.length) {
    throw new SimulationError('This plan has no computed route to simulate')
  }
  const dyn = { ...DYNAMICS, ...(options.dynamics || {}) }
  const frame = LocalFrame.ofMission(mission)
  const drone = mission.equipment?.drone || {}
  const params = mission.params || {}
  const stats = mission.stats || {}
  const airframe = drone.airframe || 'multirotor'
  const hovers = airframe === 'multirotor'
  const notes = []

  // -- the route in the local frame ------------------------------------------------
  const n = wps.length
  const wx = new Float64Array(n)
  const wy = new Float64Array(n)
  const wz = new Float64Array(n)
  const wg = new Float64Array(n)
  let maxAlt = -Infinity
  for (let i = 0; i < n; i++) {
    const w = wps[i]
    const [x, y] = frame.forward(w.lng, w.lat)
    wx[i] = x
    wy[i] = y
    wz[i] = num(w.alt, 0)
    wg[i] = num(w.ground, NaN)
    if (wz[i] > maxAlt) maxAlt = wz[i]
  }
  const take = mission.takeoff || { lng: wps[0].lng, lat: wps[0].lat }
  const [tx, ty] = frame.forward(take.lng, take.lat)

  const groundAt = (x, y) => {
    if (!terrain) return null
    const [lng, lat] = frame.inverse(x, y)
    return terrain.elevation(lng, lat)
  }
  const takeoffGround = groundAt(tx, ty) ?? num(take.ground_m)

  // -- speeds: the plan's, within what the aircraft is known to do --------------------
  const limit = num(drone.max_mission_speed_ms) || num(drone.max_speed_ms) || Infinity
  let speed = num(stats.speed_ms) || num(params.speed_ms) || num(drone.cruise_speed_ms) || 8
  let transit = num(params.transit_speed_ms) || speed
  if (speed > limit + 1e-9) {
    notes.push(`The planned speed of ${fmt(speed)} m/s is above this aircraft's ${fmt(limit)} m/s mission limit; the simulation flies ${fmt(limit)} m/s.`)
    speed = limit
  }
  if (transit > limit + 1e-9) transit = limit
  const ascent = num(drone.max_ascent_ms) || dyn.defaultAscent
  const descent = num(drone.max_descent_ms) || dyn.defaultDescent
  if (!num(drone.max_ascent_ms) || !num(drone.max_descent_ms)) {
    notes.push(`This aircraft publishes no climb or descent rate; ${fmt(dyn.defaultAscent)} m/s up and ${fmt(dyn.defaultDescent)} m/s down are assumed.`)
  }
  const rthAlt = num(stats.rth_altitude_m) ?? num(params.rth_altitude_m) ?? maxAlt
  const finish = params.finish_action || 'rth'
  const returnsHome = finish !== 'land'
  const rated = (num(drone.max_flight_time_min, 0) * 60) * (num(drone.endurance_factor) || 0.8)
  const usable = num(stats.usable_flight_time_s) ?? rated * (1 - (num(params.reserve_percent, 25)) / 100)
  const photoSpacing = num(mission.camera?.photo_spacing_m, 0)

  const flights = Array.isArray(mission.flights) && mission.flights.length
    ? mission.flights
    : [{ index: 0, leg_from: 0, leg_to: legs.length - 1 }]

  // -- segment store ---------------------------------------------------------------
  const S = {
    kind: [], t0: [], dur: [], x0: [], y0: [], z0: [], x1: [], y1: [], z1: [], h: [], hPrev: [], hNext: [],
    v0: [], vp: [], acc: [], ta: [], tc: [], da: [], dc: [], plen: [], len: [], dist0: [],
    phase: [], flight: [], leg: [], camera: [], g0: [], g1: [],
  }
  let now = 0
  let flown = 0
  let slowed = 0
  let steep = 0

  function push(kind, phase, flight, a, b, h, hPrev, hNext, profile, extra = {}) {
    const dur = profile ? profile.T : extra.dur
    if (!(dur > 0)) return -1
    const index = S.kind.length
    S.kind.push(kind); S.t0.push(now); S.dur.push(dur)
    S.x0.push(a[0]); S.y0.push(a[1]); S.z0.push(a[2])
    S.x1.push(b[0]); S.y1.push(b[1]); S.z1.push(b[2])
    S.h.push(h); S.hPrev.push(hPrev); S.hNext.push(hNext)
    S.v0.push(profile ? profile.v0 : 0); S.vp.push(profile ? profile.vp : 0); S.acc.push(profile ? profile.a : 0)
    S.ta.push(profile ? profile.ta : 0); S.tc.push(profile ? profile.tc : 0)
    S.da.push(profile ? profile.da : 0); S.dc.push(profile ? profile.dc : 0)
    S.plen.push(profile ? profile.d : 0)
    const len = extra.len || 0
    S.len.push(len); S.dist0.push(flown)
    S.phase.push(phase); S.flight.push(flight); S.leg.push(extra.leg ?? -1); S.camera.push(extra.camera ? 1 : 0)
    S.g0.push(extra.g0 ?? NaN); S.g1.push(extra.g1 ?? NaN)
    now += dur
    flown += len
    return index
  }

  // The aircraft stays where it is, optionally yawing from `hFrom` to `hTo`.
  function hold(phase, flight, p, seconds, hFrom, hTo = hFrom) {
    push(HOLD, phase, flight, p, p, hFrom, hFrom, hTo, null, { dur: seconds })
  }

  // Yaw on the spot to a new heading; returns the heading it ends on.
  function yaw(phase, flight, p, hFrom, hTo) {
    const turn = Math.abs(angleDiff(hFrom, hTo))
    if (turn < 1) return hTo
    hold(phase, flight, p, Math.min(dyn.turnSeconds, Math.max(0.4, turn / dyn.yawRate)), hFrom, hTo)
    return hTo
  }

  // Straight up or down at the published rate.
  function vertical(phase, flight, x, y, zFrom, zTo, heading) {
    const dz = zTo - zFrom
    if (Math.abs(dz) < 0.01) return
    const rate = dz > 0 ? ascent : descent
    const profile = segmentProfile(Math.abs(dz), 0, 0, rate, dyn.verticalAccel)
    push(MOVE, phase, flight, [x, y, zFrom], [x, y, zTo], heading, heading, heading, profile)
  }

  /**
   * Fly a polyline in one go. `pts[i] = { x, y, z, g }`; `meta[i]` describes
   * the piece from `pts[i]` to `pts[i + 1]`: `{ vmax, phase, leg, camera }`.
   * Starts and ends at rest unless `vStart` / `vEnd` say otherwise. With
   * `corners`, bends slow the aircraft (a multirotor's waypoint turn);
   * without, the path is taken as flyable at speed (a pre-computed curve).
   * Returns `{ first, profiles, lengths }` for the pieces pushed, one per pair.
   */
  function run(flight, pts, meta, { corners = true, floor = dyn.crawl, vStart = 0, vEnd = 0 } = {}) {
    const m = pts.length - 1
    const lengths = new Float64Array(m)
    const headings = new Float64Array(m)
    const segMax = new Float64Array(m)
    const vertexMax = new Float64Array(m + 1).fill(Infinity)
    let lastHeading = null
    for (let i = 0; i < m; i++) {
      const a = pts[i]
      const b = pts[i + 1]
      const len = Math.hypot(b.x - a.x, b.y - a.y)
      const dz = b.z - a.z
      lengths[i] = len
      if (len < EPS) {
        // A step in height with no ground covered: stop, move vertically, go on.
        lengths[i] = 0
        headings[i] = lastHeading ?? 0
        segMax[i] = dz > 0 ? ascent : descent
        vertexMax[i] = 0
        vertexMax[i + 1] = 0
        continue
      }
      headings[i] = bearingDeg(b.x - a.x, b.y - a.y)
      lastHeading = headings[i]
      let vmax = meta[i].vmax
      if (Math.abs(dz) > 0.01) {
        // The published climb / descent rate caps how fast a sloping piece can be flown.
        const allowed = (dz > 0 ? ascent : descent) * len / Math.abs(dz)
        if (allowed < vmax) {
          if (allowed < floor) { steep++; vmax = Math.min(vmax, floor) } else { slowed++; vmax = allowed }
        }
      }
      segMax[i] = vmax
    }
    if (corners) {
      for (let i = 1; i < m; i++) {
        if (lengths[i - 1] < EPS || lengths[i] < EPS) continue
        vertexMax[i] = Math.min(vertexMax[i], cornerSpeed(angleDiff(headings[i - 1], headings[i]), dyn.accel, dyn.cornerBlend))
      }
    }
    // Speed is planned over ground distance; a vertical step is planned at rest.
    const v = planSpeeds(lengths, segMax, vertexMax, dyn.accel, vStart, vEnd)
    const first = S.kind.length
    const profiles = new Array(m)
    let vIn = v[0]
    for (let i = 0; i < m; i++) {
      const a = pts[i]
      const b = pts[i + 1]
      const info = meta[i]
      const h = headings[i]
      const hPrev = i > 0 && lengths[i - 1] >= EPS ? headings[i - 1] : h
      const hNext = i < m - 1 && lengths[i + 1] >= EPS ? headings[i + 1] : h
      let profile
      if (lengths[i] < EPS) {
        profile = segmentProfile(Math.abs(b.z - a.z), 0, 0, segMax[i], dyn.verticalAccel)
        vIn = 0
      } else {
        profile = segmentProfile(lengths[i], vIn, v[i + 1], segMax[i], dyn.accel)
        vIn = profile.v1
      }
      profiles[i] = profile
      const index = push(MOVE, info.phase, flight, [a.x, a.y, a.z], [b.x, b.y, b.z], h, hPrev, hNext, profile, {
        len: lengths[i], leg: info.leg, camera: info.camera, g0: a.g, g1: b.g,
      })
      if (index < 0) profiles[i] = null
    }
    return { first, profiles, lengths }
  }

  // -- photo events ----------------------------------------------------------------
  const photoTotal = legs.reduce((s, l) => s + (l.photos | 0), 0)
  const photoT = new Float64Array(photoTotal)
  const photoX = new Float64Array(photoTotal)
  const photoY = new Float64Array(photoTotal)
  const photoZ = new Float64Array(photoTotal)
  const photoH = new Float32Array(photoTotal)
  const photoLeg = new Int32Array(photoTotal)
  let photoCount = 0

  // Exposures of one leg: every `photoSpacing` metres from its start, as the
  // planner counts them and as the "photo at every waypoint" export places them.
  function placePhotos(legIndex, pts, pieces, startTimes) {
    const count = legs[legIndex].photos | 0
    if (!count) return
    if (!pieces.lengths.length) {
      // A leg that is a single point: every exposure is taken there.
      for (let k = 0; k < count; k++) {
        photoT[photoCount] = now
        photoX[photoCount] = pts[0].x
        photoY[photoCount] = pts[0].y
        photoZ[photoCount] = pts[0].z
        photoH[photoCount] = num(wps[legs[legIndex].from].heading, 0)
        photoLeg[photoCount] = legIndex
        photoCount++
      }
      return
    }
    let total = 0
    for (let i = 0; i < pieces.lengths.length; i++) total += pieces.lengths[i]
    let seg = 0
    let before = 0
    for (let k = 0; k < count; k++) {
      const s = Math.min(k * photoSpacing, total)
      while (seg < pieces.lengths.length - 1 && s > before + pieces.lengths[seg] + 1e-9) {
        before += pieces.lengths[seg]
        seg++
      }
      // Skip vertical steps: no ground is covered on them.
      while (seg < pieces.lengths.length - 1 && pieces.lengths[seg] < EPS) seg++
      const len = pieces.lengths[seg]
      const local = Math.min(Math.max(s - before, 0), len)
      const f = len > 0 ? local / len : 0
      const a = pts[seg]
      const b = pts[seg + 1]
      const profile = pieces.profiles[seg]
      photoT[photoCount] = startTimes[seg] + (profile ? profileTimeAt(profile, local) : 0)
      photoX[photoCount] = a.x + (b.x - a.x) * f
      photoY[photoCount] = a.y + (b.y - a.y) * f
      photoZ[photoCount] = a.z + (b.z - a.z) * f
      photoH[photoCount] = len > 0 ? bearingDeg(b.x - a.x, b.y - a.y) : num(wps[legs[legIndex].from].heading, 0)
      photoLeg[photoCount] = legIndex
      photoCount++
    }
  }

  // Start time of each piece pushed by `run` (a piece of zero duration was not pushed).
  function startTimesOf(pieces) {
    const out = new Float64Array(pieces.profiles.length)
    let index = pieces.first
    let t = index < S.t0.length ? S.t0[index] : now
    for (let i = 0; i < out.length; i++) {
      out[i] = t
      if (pieces.profiles[i]) { t = S.t0[index] + S.dur[index]; index++ }
    }
    return out
  }

  const legPoints = leg => {
    const out = []
    for (let i = leg.from; i <= leg.to; i++) {
      const p = { x: wx[i], y: wy[i], z: wz[i], g: wg[i] }
      const last = out[out.length - 1]
      // The planner never repeats a point; a hand-edited mission might.
      if (last && Math.hypot(p.x - last.x, p.y - last.y) < EPS && Math.abs(p.z - last.z) < 0.01) continue
      out.push(p)
    }
    return out
  }
  const headingOf = (a, b, fallback) => (Math.hypot(b.x - a.x, b.y - a.y) < EPS ? fallback : bearingDeg(b.x - a.x, b.y - a.y))
  const legT0 = new Float64Array(legs.length)
  const legT1 = new Float64Array(legs.length)

  // Height of the ground where the aircraft lands in place, relative to the takeoff point.
  function groundRelAt(p, wpIndex) {
    const g = groundAt(p.x, p.y)
    if (g !== null && takeoffGround !== null) return g - takeoffGround
    const agl = num(wps[wpIndex]?.agl)
    return agl !== null ? p.z - agl : 0
  }

  // -- a flight of an aircraft that hovers: stop, turn, go --------------------------------
  function flyMultirotor(fi, flight) {
    const legA = legs[flight.leg_from]
    const first = { x: wx[legA.from], y: wy[legA.from], z: wz[legA.from] }
    const home = [tx, ty, 0]
    const dOut = Math.hypot(first.x - tx, first.y - ty)
    const firstPts = legPoints(legA)
    const firstLegHeading = headingOf(firstPts[0], firstPts[1] || firstPts[0], num(wps[legA.from].heading, 0))
    let heading = dOut > 1 ? bearingDeg(first.x - tx, first.y - ty) : firstLegHeading

    hold(PHASE.PREFLIGHT, fi, home, dyn.groundPrep, heading)
    const climbTo = Math.max(first.z, dyn.minClimb)
    vertical(PHASE.TAKEOFF, fi, tx, ty, 0, climbTo, heading)
    if (dOut > EPS) {
      run(fi, [{ x: tx, y: ty, z: climbTo, g: NaN }, { x: first.x, y: first.y, z: climbTo, g: NaN }],
        [{ vmax: transit, phase: PHASE.TRANSIT_OUT, leg: -1, camera: false }])
    }
    vertical(PHASE.TRANSIT_OUT, fi, first.x, first.y, climbTo, first.z, heading)

    let end = first
    let endWp = legA.from
    for (let li = flight.leg_from; li <= flight.leg_to; li++) {
      const leg = legs[li]
      const pts = legPoints(leg)
      const start = pts[0]
      heading = yaw(PHASE.TURN, fi, [start.x, start.y, start.z], heading, headingOf(start, pts[1] || start, heading))
      legT0[li] = now
      const pieces = run(fi, pts, pts.slice(1).map(() => ({ vmax: speed, phase: PHASE.SURVEY, leg: li, camera: true })))
      placePhotos(li, pts, pieces, startTimesOf(pieces))
      legT1[li] = now
      end = pts[pts.length - 1]
      endWp = leg.to
      if (pts.length > 1) heading = headingOf(pts[pts.length - 2], end, heading)

      if (li < flight.leg_to) {
        const next = legs[li + 1]
        const to = { x: wx[next.from], y: wy[next.from], z: wz[next.from], g: wg[next.from] }
        const hop = Math.hypot(to.x - end.x, to.y - end.y)
        if (hop > EPS) {
          heading = yaw(PHASE.TURN, fi, [end.x, end.y, end.z], heading, bearingDeg(to.x - end.x, to.y - end.y))
          run(fi, [end, to], [{ vmax: speed, phase: PHASE.TURN, leg: -1, camera: false }])
        } else {
          vertical(PHASE.TURN, fi, end.x, end.y, end.z, to.z, heading)
        }
      }
    }

    if (!returnsHome) {
      vertical(PHASE.LANDING, fi, end.x, end.y, end.z, groundRelAt(end, endWp), heading)
      hold(PHASE.LANDED, fi, [end.x, end.y, groundRelAt(end, endWp)], dyn.landingSettle, heading)
      return
    }
    const back = Math.max(rthAlt, end.z)
    if (finish === 'hover' && fi === flights.length - 1) {
      hold(PHASE.HOVER, fi, [end.x, end.y, end.z], dyn.hoverSeconds, heading)
    }
    vertical(PHASE.RETURN, fi, end.x, end.y, end.z, back, heading)
    const dBack = Math.hypot(tx - end.x, ty - end.y)
    if (dBack > EPS) {
      if (dBack > 1) heading = yaw(PHASE.RETURN, fi, [end.x, end.y, back], heading, bearingDeg(tx - end.x, ty - end.y))
      run(fi, [{ x: end.x, y: end.y, z: back, g: NaN }, { x: tx, y: ty, z: back, g: NaN }],
        [{ vmax: transit, phase: PHASE.RETURN, leg: -1, camera: false }])
    }
    vertical(PHASE.LANDING, fi, tx, ty, back, 0, heading)
    hold(PHASE.LANDED, fi, home, dyn.landingSettle, heading)
  }

  // -- a flight of an aircraft that keeps moving: lines joined by flyable curves ----------
  const turnRadius = num(drone.min_turn_radius_m)
    || (speed * speed) / (dyn.gravity * Math.tan(dyn.bankLimitDeg * Math.PI / 180))
  const cruiseFloor = Math.min(speed, num(drone.min_speed_ms) || 0.7 * speed)
  // Coarser arcs for very long missions keep the timeline small.
  const arcStep = (legs.length > 600 ? 2 * dyn.arcStepDeg : dyn.arcStepDeg) * Math.PI / 180

  function flyCruise(fi, flight) {
    const vtol = airframe === 'vtol'
    const legA = legs[flight.leg_from]
    const legB = legs[flight.leg_to]
    const firstPts = legPoints(legA)
    const lastPts = legPoints(legB)
    const first = firstPts[0]
    const last = lastPts[lastPts.length - 1]
    const hFirst = headingOf(first, firstPts[1] || first, num(wps[legA.from].heading, 0))
    const hLast = lastPts.length > 1 ? headingOf(lastPts[lastPts.length - 2], last, 0) : hFirst
    const dOut = Math.hypot(first.x - tx, first.y - ty)
    const hOut = dOut > 1 ? bearingDeg(first.x - tx, first.y - ty) : hFirst
    const home = [tx, ty, 0]

    hold(PHASE.PREFLIGHT, fi, home, dyn.groundPrep, hOut)
    let z0 = 0
    if (vtol) {
      z0 = Math.max(first.z, dyn.minClimb)
      vertical(PHASE.TAKEOFF, fi, tx, ty, 0, z0, hOut)
    }

    const pts = []
    const meta = []
    const add = (x, y, z, g, info) => {
      const prev = pts[pts.length - 1]
      if (prev && Math.hypot(x - prev.x, y - prev.y) < EPS && Math.abs(z - prev.z) < 0.01) return false
      if (prev) meta.push(info)
      pts.push({ x, y, z, g })
      return true
    }
    // A curve as `[x, y]` points with its running length, for assigning heights along it.
    const measured = points => {
      const s = [0]
      for (let i = 1; i < points.length; i++) s.push(s[i - 1] + Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]))
      return s
    }

    // Out: a curve from the takeoff point onto the first line. A fixed-wing
    // climbs along it, circling over the takeoff point first when the way
    // out is too short to gain the height.
    {
      const path = dubinsPath(tx, ty, bearingToAngle(hOut), first.x, first.y, bearingToAngle(hFirst), turnRadius)
      let curve = sampleDubins(path, arcStep)
      const climb = vtol ? 0 : Math.max(0, first.z) * transit / ascent * 1.15
      if (!vtol && path.length < climb) {
        const turns = Math.ceil((climb - path.length) / (2 * Math.PI * turnRadius))
        curve = [[tx, ty], ...loiterCircles(tx, ty, bearingToAngle(hOut), turnRadius, turns, arcStep), ...curve.slice(1)]
      }
      const s = measured(curve)
      const total = s[s.length - 1]
      add(tx, ty, z0, NaN, null)
      for (let i = 1; i < curve.length; i++) {
        const lastPoint = i === curve.length - 1
        const z = lastPoint ? first.z
          : vtol ? z0 + (first.z - z0) * (total > 0 ? s[i] / total : 1)
            : first.z * Math.min(1, climb > 0 ? s[i] / climb : 1)
        const climbing = !vtol && s[i] <= climb + 1e-6
        add(lastPoint ? first.x : curve[i][0], lastPoint ? first.y : curve[i][1], z, NaN,
          { vmax: transit, phase: climbing ? PHASE.TAKEOFF : PHASE.TRANSIT_OUT, leg: -1, camera: false })
      }
    }

    // The lines, joined by turns no tighter than the aircraft's radius.
    const legRanges = []
    for (let li = flight.leg_from; li <= flight.leg_to; li++) {
      const lp = legPoints(legs[li])
      const from = pts.length - 1
      for (let i = 1; i < lp.length; i++) {
        add(lp[i].x, lp[i].y, lp[i].z, lp[i].g, { vmax: speed, phase: PHASE.SURVEY, leg: li, camera: true })
      }
      pts[from].g = lp[0].g
      legRanges.push({ li, from, to: pts.length - 1, lp })
      if (li < flight.leg_to) {
        const np = legPoints(legs[li + 1])
        const end = lp[lp.length - 1]
        const hEnd = lp.length > 1 ? headingOf(lp[lp.length - 2], end, 0) : 0
        const hNext = headingOf(np[0], np[1] || np[0], hEnd)
        const path = dubinsPath(end.x, end.y, bearingToAngle(hEnd), np[0].x, np[0].y, bearingToAngle(hNext), turnRadius)
        const curve = sampleDubins(path, arcStep)
        const s = measured(curve)
        const total = s[s.length - 1]
        for (let i = 1; i < curve.length; i++) {
          const lastPoint = i === curve.length - 1
          const f = total > 0 ? s[i] / total : 1
          add(lastPoint ? np[0].x : curve[i][0], lastPoint ? np[0].y : curve[i][1],
            lastPoint ? np[0].z : end.z + (np[0].z - end.z) * f, NaN,
            { vmax: speed, phase: PHASE.TURN, leg: -1, camera: false })
        }
      }
    }

    // Back. A fixed-wing descends along the way home, circling over the
    // takeoff point when the way is too short to lose the height; a VTOL
    // arrives level and lands vertically.
    let landZ = 0
    let landAt = home
    if (returnsHome || !vtol) {
      const back = returnsHome ? Math.max(rthAlt, last.z) : last.z
      const target = returnsHome ? { x: tx, y: ty } : last
      landZ = returnsHome ? 0 : groundRelAt(last, legB.to)
      const dBack = Math.hypot(tx - last.x, ty - last.y)
      const hHome = returnsHome && dBack > 1 ? bearingDeg(tx - last.x, ty - last.y) : hLast
      let curve = returnsHome
        ? sampleDubins(dubinsPath(last.x, last.y, bearingToAngle(hLast), tx, ty, bearingToAngle(hHome), turnRadius), arcStep)
        : [[last.x, last.y]]
      const up = Math.max(0, back - last.z) * transit / ascent
      const down = vtol ? 0 : Math.max(0, back - landZ) * transit / descent * 1.15
      let s = measured(curve)
      if (s[s.length - 1] < up + down) {
        const turns = Math.max(1, Math.ceil((up + down - s[s.length - 1]) / (2 * Math.PI * turnRadius)))
        curve = [...curve, ...loiterCircles(target.x, target.y, bearingToAngle(hHome), turnRadius, turns, arcStep)]
        s = measured(curve)
      }
      const total = s[s.length - 1]
      for (let i = 1; i < curve.length; i++) {
        const lastPoint = i === curve.length - 1
        const rise = up > 0 ? last.z + (back - last.z) * Math.min(1, s[i] / up) : back
        const fall = down > 0 ? landZ + (back - landZ) * Math.min(1, (total - s[i]) / down) : back
        const z = lastPoint ? (vtol ? back : landZ) : Math.min(rise, fall)
        const descending = !vtol && total - s[i] < down - 1e-6
        add(lastPoint ? target.x : curve[i][0], lastPoint ? target.y : curve[i][1], z, NaN,
          { vmax: transit, phase: descending ? PHASE.LANDING : PHASE.RETURN, leg: -1, camera: false })
      }
      landAt = [target.x, target.y, landZ]
    } else {
      landZ = groundRelAt(last, legB.to)
      landAt = [last.x, last.y, landZ]
    }

    const pieces = run(fi, pts, meta, { corners: false, floor: cruiseFloor })
    const starts = startTimesOf(pieces)
    for (const range of legRanges) {
      const sub = {
        lengths: pieces.lengths.subarray(range.from, range.to),
        profiles: pieces.profiles.slice(range.from, range.to),
      }
      legT0[range.li] = starts[range.from]
      legT1[range.li] = range.to < starts.length ? starts[range.to] : now
      placePhotos(range.li, pts.slice(range.from, range.to + 1), sub, starts.subarray(range.from, range.to))
    }

    const endPoint = pts[pts.length - 1]
    const hEnd = pts.length > 1 ? headingOf(pts[pts.length - 2], endPoint, hLast) : hLast
    if (vtol) vertical(PHASE.LANDING, fi, endPoint.x, endPoint.y, endPoint.z, landZ, hEnd)
    hold(PHASE.LANDED, fi, landAt, dyn.landingSettle, hEnd)
  }

  // -- every flight, with a battery change between them ---------------------------------
  const flightT0 = new Float64Array(flights.length)
  const flightT1 = new Float64Array(flights.length)
  const flightD0 = new Float64Array(flights.length)
  const flightD1 = new Float64Array(flights.length)
  for (let fi = 0; fi < flights.length; fi++) {
    const flight = flights[fi]
    if (fi > 0) hold(PHASE.BATTERY, fi - 1, [tx, ty, 0], dyn.batterySwap, S.h[S.h.length - 1] ?? 0)
    flightT0[fi] = now
    flightD0[fi] = flown
    if (hovers) flyMultirotor(fi, flight)
    else flyCruise(fi, flight)
    flightT1[fi] = now
    flightD1[fi] = flown
  }
  const duration = now
  const count = S.kind.length
  if (!count || !(duration > 0)) throw new SimulationError('This plan has no route to simulate')

  if (take.assumed) notes.push('No takeoff point is set in the plan: the simulation launches from the first waypoint.')
  if (slowed) notes.push(`${slowed} stretch${slowed === 1 ? ' is' : 'es are'} flown slower than planned so the climb or descent stays within the aircraft's published rate.`)
  if (steep) notes.push(`${steep} stretch${steep === 1 ? ' is' : 'es are'} steeper than the aircraft's published climb or descent rate allows even at its slowest; the simulation follows the planned heights regardless.`)
  if (!hovers) {
    notes.push(`Turns are flown as curves of ${fmt(turnRadius)} m radius${num(drone.min_turn_radius_m) ? '' : ' (assumed: no turn radius is published)'}; the plan's time estimate approximates them, so the two differ.`)
  }
  if (flights.length > 1 && !returnsHome) {
    notes.push('The plan ends each flight by landing where its last line ends; the next flight is shown starting from the takeoff point after the battery change.')
  }

  // -- freeze into typed arrays ---------------------------------------------------------
  const f64 = a => Float64Array.from(a)
  const T0 = f64(S.t0)
  const DUR = f64(S.dur)
  const X0 = f64(S.x0); const Y0 = f64(S.y0); const Z0 = f64(S.z0)
  const X1 = f64(S.x1); const Y1 = f64(S.y1); const Z1 = f64(S.z1)
  const H = f64(S.h); const HPREV = f64(S.hPrev); const HNEXT = f64(S.hNext)
  const V0 = f64(S.v0); const VP = f64(S.vp); const ACC = f64(S.acc)
  const TA = f64(S.ta); const TC = f64(S.tc); const DA = f64(S.da); const DC = f64(S.dc)
  const PLEN = f64(S.plen); const LEN = f64(S.len); const DIST0 = f64(S.dist0)
  const KIND = Uint8Array.from(S.kind)
  const PHASE_OF = Uint8Array.from(S.phase)
  const FLIGHT = Uint16Array.from(S.flight)
  const LEG = Int32Array.from(S.leg)
  const CAMERA = Uint8Array.from(S.camera)
  const G0 = f64(S.g0); const G1 = f64(S.g1)
  const pT = photoT.subarray(0, photoCount)

  // Distance along a moving piece and the speed there, `u` seconds in.
  function along(i, u, out) {
    const dur = DUR[i]
    const ta = TA[i]
    const tc = TC[i]
    const a = ACC[i]
    if (u <= 0) { out.s = 0; out.v = V0[i]; return }
    if (u >= dur) { out.s = PLEN[i]; out.v = Math.max(0, VP[i] - a * (dur - ta - tc)); return }
    if (u < ta) { out.s = V0[i] * u + 0.5 * a * u * u; out.v = V0[i] + a * u; return }
    if (u < ta + tc) { out.s = DA[i] + VP[i] * (u - ta); out.v = VP[i]; return }
    const w = u - ta - tc
    out.s = Math.min(PLEN[i], DA[i] + DC[i] + VP[i] * w - 0.5 * a * w * w)
    out.v = Math.max(0, VP[i] - a * w)
  }

  const segmentAt = t => Math.max(0, lastAtOrBefore(T0, t, count))
  const scratch = { s: 0, v: 0 }

  // Position, heading and velocity at `t`: the part of a sample that needs no terrain.
  function kinematic(t, o) {
    const i = segmentAt(t)
    const u = Math.min(Math.max(t - T0[i], 0), DUR[i])
    o.index = i
    if (KIND[i] === HOLD) {
      o.x = X0[i]; o.y = Y0[i]; o.z = Z0[i]
      o.h = lerpAngle(H[i], HNEXT[i], smoothstep(u / DUR[i]))
      o.vg = 0; o.vz = 0; o.f = 0; o.s = 0
      return o
    }
    along(i, u, scratch)
    const plen = PLEN[i]
    const f = plen > 0 ? scratch.s / plen : 1
    o.f = f
    o.s = LEN[i] * f
    o.x = X0[i] + (X1[i] - X0[i]) * f
    o.y = Y0[i] + (Y1[i] - Y0[i]) * f
    o.z = Z0[i] + (Z1[i] - Z0[i]) * f
    o.vg = plen > 0 ? scratch.v * LEN[i] / plen : 0
    o.vz = plen > 0 ? scratch.v * (Z1[i] - Z0[i]) / plen : 0
    // The heading swings through a bend over a short window centred on it.
    const h = H[i]
    const window = Math.min(dyn.cornerBlend, DUR[i] / 2)
    if (window > 0 && u < window && HPREV[i] !== h) o.h = lerpAngle(HPREV[i], h, 0.5 + 0.5 * u / window)
    else if (window > 0 && DUR[i] - u < window && HNEXT[i] !== h) o.h = lerpAngle(h, HNEXT[i], 0.5 * (1 - (DUR[i] - u) / window))
    else o.h = h
    return o
  }

  const before = {}
  const after = {}
  const here = {}

  /**
   * The state of the flight at `t` seconds (clamped to the timeline). Pass
   * `out` to reuse an object in a render loop; the result depends only on `t`.
   */
  function sample(t, out = {}) {
    t = Math.min(Math.max(Number(t) || 0, 0), duration)
    const k = kinematic(t, here)
    const i = k.index
    const phase = PHASE_OF[i]
    const flight = FLIGHT[i]
    const leg = LEG[i]

    // Attitude from how the velocity is changing around this instant.
    const b = kinematic(Math.max(0, t - ATTITUDE_DT), before)
    const a = kinematic(Math.min(duration, t + ATTITUDE_DT), after)
    const span = Math.max(1e-3, Math.min(duration, t + ATTITUDE_DT) - Math.max(0, t - ATTITUDE_DT))
    const accelAlong = (a.vg - b.vg) / span
    const yawRate = angleDiff(b.h, a.h) / span * Math.PI / 180
    let pitch
    let roll
    if (!PHASE_INFO[phase].airborne) {
      pitch = 0
      roll = 0
    } else if (hovers) {
      // A multirotor leans into its acceleration and into the air it pushes through.
      pitch = -(Math.atan2(accelAlong, dyn.gravity) + Math.atan(0.0016 * k.vg * k.vg)) * 180 / Math.PI
      roll = Math.atan2(k.vg * yawRate, dyn.gravity) * 180 / Math.PI
      pitch = clamp(pitch, -35, 35)
      roll = clamp(roll, -35, 35)
    } else {
      const forward = Math.min(1, k.vg / Math.max(1, 0.5 * speed))
      pitch = clamp((Math.atan2(k.vz, Math.max(k.vg, 1)) * 180 / Math.PI + 3) * forward, -30, 30)
      roll = clamp(Math.atan2(k.vg * yawRate, dyn.gravity) * 180 / Math.PI, -45, 45)
    }

    const [lng, lat] = frame.inverse(k.x, k.y)
    let ground = terrain ? terrain.elevation(lng, lat) : null
    if (ground === null && G0[i] === G0[i] && G1[i] === G1[i]) ground = G0[i] + (G1[i] - G0[i]) * k.f
    const groundKnown = ground !== null && takeoffGround !== null
    const agl = groundKnown ? takeoffGround + k.z - ground : null
    const elapsed = t - flightT0[flight]
    const photos = lastAtOrBefore(pT, t, photoCount) + 1

    out.t = t
    out.index = i
    out.phase = phase
    out.phaseKey = PHASE_INFO[phase].key
    out.airborne = PHASE_INFO[phase].airborne
    out.flight = flight
    out.leg = leg
    out.line = leg >= 0 ? legs[leg].line : -1
    out.x = k.x
    out.y = k.y
    out.alt = k.z
    out.lng = lng
    out.lat = lat
    out.heading = k.h
    out.pitch = pitch
    out.roll = roll
    out.speed = k.vg
    out.verticalSpeed = k.vz
    out.distance = DIST0[i] + k.s
    out.flightDistance = out.distance - flightD0[flight]
    out.flightTime = Math.min(Math.max(elapsed, 0), flightT1[flight] - flightT0[flight])
    out.battery = rated > 0 ? clamp(100 * (1 - out.flightTime / rated), 0, 100) : null
    out.belowReserve = rated > 0 && out.flightTime > usable + 1e-6
    out.ground = ground
    out.groundKnown = groundKnown
    out.agl = agl
    // What the camera geometry uses when the ground is unknown: flat ground at the takeoff height.
    out.aglAssumed = agl !== null ? agl : k.z
    out.amsl = takeoffGround !== null ? takeoffGround + k.z : null
    out.photos = photos
    out.sincePhoto = photos > 0 ? t - pT[photos - 1] : Infinity
    out.capturing = CAMERA[i] === 1
    return out
  }

  /** First time at which `metres` of ground distance have been flown. */
  function timeAtDistance(metres) {
    const d = Math.min(Math.max(metres, 0), flown)
    let i = Math.max(0, lastAtOrBefore(DIST0, d, count))
    // Several pieces can start at the same distance (holds, climbs): the first of them.
    while (i > 0 && DIST0[i - 1] >= d - 1e-9 && LEN[i - 1] === 0) i--
    if (KIND[i] !== MOVE || LEN[i] <= 0) return T0[i]
    const profile = {
      d: PLEN[i], v0: V0[i], vp: VP[i], a: ACC[i], ta: TA[i], tc: TC[i], da: DA[i], dc: DC[i], T: DUR[i],
    }
    return T0[i] + profileTimeAt(profile, (d - DIST0[i]) / LEN[i] * PLEN[i])
  }

  // -- summaries ------------------------------------------------------------------------
  const flightInfo = flights.map((f, fi) => {
    const time = flightT1[fi] - flightT0[fi]
    let photos = 0
    for (let li = f.leg_from; li <= f.leg_to; li++) photos += legs[li].photos | 0
    return {
      index: fi,
      t0: flightT0[fi],
      t1: flightT1[fi],
      duration: time,
      distance: flightD1[fi] - flightD0[fi],
      photos,
      legFrom: f.leg_from,
      legTo: f.leg_to,
      batteryUsed: rated > 0 ? Math.min(100, (time / rated) * 100) : null,
      exceedsUsable: rated > 0 && time > usable + 1e-6,
      planned: {
        duration: num(f.duration_s), distance: num(f.distance_m), batteryUsed: num(f.battery_percent),
        photos: num(f.photos),
      },
    }
  })

  // Runs of consecutive pieces in the same phase of the same flight, for the timeline.
  const phases = []
  for (let i = 0; i < count; i++) {
    const last = phases[phases.length - 1]
    if (last && last.phase === PHASE_OF[i] && last.flight === FLIGHT[i] && (PHASE_OF[i] !== PHASE.SURVEY || last.leg === LEG[i])) {
      last.t1 = T0[i] + DUR[i]
    } else {
      phases.push({ phase: PHASE_OF[i], key: PHASE_INFO[PHASE_OF[i]].key, flight: FLIGHT[i], leg: LEG[i], t0: T0[i], t1: T0[i] + DUR[i] })
    }
  }

  const flightTime = flightInfo.reduce((s, f) => s + f.duration, 0)

  return {
    mission,
    frame,
    terrain,
    airframe,
    hovers,
    duration,
    flightTime,
    distance: flown,
    takeoff: { x: tx, y: ty, lng: take.lng, lat: take.lat, ground: takeoffGround, assumed: !!take.assumed },
    speeds: { survey: speed, transit, ascent, descent, turnRadius: hovers ? null : turnRadius },
    battery: { rated, usable, reservePercent: num(params.reserve_percent, 25) },
    flights: flightInfo,
    phases,
    notes,
    legTimes: { t0: legT0, t1: legT1 },
    photos: {
      count: photoCount, t: pT, x: photoX.subarray(0, photoCount), y: photoY.subarray(0, photoCount),
      alt: photoZ.subarray(0, photoCount), heading: photoH.subarray(0, photoCount), leg: photoLeg.subarray(0, photoCount),
    },
    segments: {
      count, kind: KIND, t0: T0, duration: DUR, x0: X0, y0: Y0, z0: Z0, x1: X1, y1: Y1, z1: Z1,
      heading: H, length: LEN, distance0: DIST0, phase: PHASE_OF, flight: FLIGHT, leg: LEG, camera: CAMERA,
    },
    // Ground under the route as the mission records it at its waypoints (NaN elsewhere):
    // what a sample falls back to where the terrain grid has no answer.
    groundFallback: { g0: G0, g1: G1 },
    sample,
    segmentAt,
    timeAtDistance,
    groundAt,
    /** Number of photos taken up to and including `t`. */
    photosAt: t => lastAtOrBefore(pT, t, photoCount) + 1,
  }
}

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v)
const fmt = v => String(Math.round(Number(v) * 10) / 10)

export const isMove = kind => kind === MOVE
