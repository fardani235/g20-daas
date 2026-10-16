// The elevation profile of a simulated flight: ground and aircraft against
// the ground distance flown, for the chart that follows the playback.
//
// The planner's own profile (`mission.terrain.profile`) covers the survey
// route. This one covers everything the simulation flies - the climb, the
// transit out, every turn as flown, the return and the landing, flight
// after flight - sampled from the same terrain grid.

import { lastAtOrBefore } from './kinematics'
import { PHASE, isMove } from './trajectory'

const GROUNDED = new Set([PHASE.PREFLIGHT, PHASE.TAKEOFF, PHASE.LANDING, PHASE.LANDED, PHASE.BATTERY])

/**
 * Sample a trajectory into profile rows.
 *
 * Returns `{ distance, flight, ground, absolute, length, min, max, lowest,
 * flights }`: parallel typed arrays (ground is NaN where unknown),
 * whether heights are elevations (`absolute`: the terrain's datum) or
 * heights above the takeoff point, the lowest clearance met in flight
 * (`{ clearance, distance, time, x, y }` or null) and the distance at which
 * each battery flight starts.
 *
 * `maxPoints` bounds the result; the point of lowest clearance is always kept.
 */
export function buildProfile(trajectory, { maxPoints = 1600 } = {}) {
  const seg = trajectory.segments
  const base = trajectory.takeoff.ground
  const absolute = base !== null
  const step = Math.max(2, trajectory.terrain?.spacing ? trajectory.terrain.spacing / 2 : 15, trajectory.distance / maxPoints)

  const D = []
  const Z = []
  const G = []
  let lowest = null
  const add = (d, z, x, y, phase, g0, g1, f) => {
    let ground = base === null ? null : trajectory.groundAt(x, y)
    if (ground === null && g0 === g0 && g1 === g1) ground = g0 + (g1 - g0) * f
    D.push(d)
    Z.push(absolute ? base + z : z)
    G.push(ground === null || !absolute ? NaN : ground)
    if (ground !== null && absolute && !GROUNDED.has(phase)) {
      const clearance = base + z - ground
      if (!lowest || clearance < lowest.clearance) lowest = { clearance, distance: d, x, y, index: D.length - 1 }
    }
  }

  const g = trajectory.groundFallback
  for (let i = 0; i < seg.count; i++) {
    if (!isMove(seg.kind[i])) continue
    const len = seg.length[i]
    const pieces = len > 0 ? Math.max(1, Math.ceil(len / step)) : 1
    const g0 = g ? g.g0[i] : NaN
    const g1 = g ? g.g1[i] : NaN
    for (let k = 0; k <= pieces; k++) {
      const f = k / pieces
      // A piece shares its first point with the end of the one before it.
      if (k === 0 && D.length && Math.abs(D[D.length - 1] - seg.distance0[i]) < 1e-9
        && Math.abs((absolute ? base : 0) + seg.z0[i] - Z[Z.length - 1]) < 1e-9) continue
      add(seg.distance0[i] + len * f, seg.z0[i] + (seg.z1[i] - seg.z0[i]) * f,
        seg.x0[i] + (seg.x1[i] - seg.x0[i]) * f, seg.y0[i] + (seg.y1[i] - seg.y0[i]) * f, seg.phase[i], g0, g1, f)
    }
  }

  // Thin to the budget, never dropping the lowest-clearance row or the last one.
  let keep = null
  if (D.length > maxPoints) {
    const stride = Math.ceil(D.length / maxPoints)
    keep = []
    for (let i = 0; i < D.length; i += stride) keep.push(i)
    if (lowest) keep.push(lowest.index)
    keep.push(D.length - 1)
    keep = [...new Set(keep)].sort((a, b) => a - b)
  }
  const pick = arr => Float64Array.from(keep ? keep.map(i => arr[i]) : arr)
  const distance = pick(D)
  const flight = pick(Z)
  const ground = pick(G)

  let min = Infinity
  let max = -Infinity
  for (let i = 0; i < distance.length; i++) {
    if (flight[i] < min) min = flight[i]
    if (flight[i] > max) max = flight[i]
    if (ground[i] === ground[i]) {
      if (ground[i] < min) min = ground[i]
      if (ground[i] > max) max = ground[i]
    }
  }
  if (!(max - min >= 1)) { max += 0.5; min -= 0.5 }

  return {
    distance, flight, ground,
    absolute,
    hasGround: ground.some(v => v === v),
    length: trajectory.distance,
    min,
    max,
    lowest: lowest
      ? { clearance: lowest.clearance, distance: lowest.distance, time: trajectory.timeAtDistance(lowest.distance), x: lowest.x, y: lowest.y }
      : null,
    flights: trajectory.flights.map(f => ({ index: f.index, distance: trajectory.sample(f.t0).distance })),
  }
}

/**
 * SVG geometry of a profile in a `width` x `height` box: path data for the
 * flight line and the ground, the filled ground area, scale functions and
 * the bands of the battery flights. Null without a usable profile.
 */
export function profileGeometry(profile, width, height, pad = 4) {
  if (!profile || profile.distance.length < 2 || !(profile.length > 0)) return null
  const { distance, flight, ground, min, max, length } = profile
  const x = d => pad + (d / length) * (width - 2 * pad)
  const y = v => height - pad - ((v - min) / (max - min)) * (height - 2 * pad)
  let flightPath = ''
  for (let i = 0; i < distance.length; i++) flightPath += `${i ? 'L' : 'M'}${x(distance[i]).toFixed(1)},${y(flight[i]).toFixed(1)} `
  // The ground is drawn in runs: a gap in the data is a gap in the line.
  let groundPath = ''
  let area = ''
  let run = []
  const flush = () => {
    if (run.length > 1) {
      const line = run.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ')
      groundPath += `${line} `
      area += `${line} L${run[run.length - 1][0].toFixed(1)},${height} L${run[0][0].toFixed(1)},${height} Z `
    }
    run = []
  }
  for (let i = 0; i < distance.length; i++) {
    if (ground[i] === ground[i]) run.push([x(distance[i]), y(ground[i])])
    else flush()
  }
  flush()
  const bands = profile.flights.map((f, i) => ({
    index: f.index,
    x0: x(f.distance),
    x1: x(i + 1 < profile.flights.length ? profile.flights[i + 1].distance : length),
  }))
  return {
    flight: flightPath.trim(), ground: groundPath.trim(), area: area.trim(), x, y, min, max, length, bands,
    /** Ground distance under a horizontal pixel position. */
    distanceAt: px => Math.min(Math.max((px - pad) / (width - 2 * pad), 0), 1) * length,
  }
}

/** Flight height and ground at a ground distance, interpolated from the profile rows. */
export function profileAtDistance(profile, d) {
  const { distance, flight, ground } = profile
  const i = Math.max(0, Math.min(distance.length - 2, lastAtOrBefore(distance, d)))
  const span = distance[i + 1] - distance[i]
  const f = span > 0 ? Math.min(Math.max((d - distance[i]) / span, 0), 1) : 0
  const lerp = (a, b) => (a === a && b === b ? a + (b - a) * f : NaN)
  return { flight: lerp(flight[i], flight[i + 1]), ground: lerp(ground[i], ground[i + 1]) }
}
