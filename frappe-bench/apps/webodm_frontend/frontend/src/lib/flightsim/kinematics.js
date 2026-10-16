// Speed along a path: the arithmetic of "accelerate, cruise, brake".
//
// Every moving part of a simulated flight is a straight piece of path flown
// with a trapezoidal speed profile: speed up at a constant acceleration,
// hold the cruise speed, slow down at the same rate. A profile is closed
// form in both directions (where am I after `t` seconds, when do I pass
// `s` metres), which is what makes playback deterministic and scrubbing
// exact - nothing is integrated step by step.
//
// Pure functions; no state, no units other than metres and seconds.

const EPS = 1e-9

/**
 * Profile over `d` metres entered at `v0` and left at `v1`, never faster
 * than `vmax`, accelerating and braking at `a`.
 *
 * The caller guarantees the ends are reachable (`|v1² - v0²| <= 2·a·d`, see
 * `planSpeeds`); if they are not, the profile brakes or accelerates over the
 * whole length and leaves at whatever speed that gives.
 *
 * Returns `{ d, v0, v1, vp, a, ta, tc, td, da, dc, T }`: peak speed, the
 * three phase durations, the distances of the first two and the total time.
 */
export function segmentProfile(d, v0, v1, vmax, a) {
  d = Math.max(0, d)
  if (d < EPS) return { d: 0, v0, v1: v0, vp: v0, a, ta: 0, tc: 0, td: 0, da: 0, dc: 0, T: 0 }
  const top = Math.max(vmax, EPS)
  v0 = Math.min(Math.max(v0, 0), top)
  v1 = Math.min(Math.max(v1, 0), top)
  const reach = 2 * a * d
  // An end that cannot be reached in the distance is pulled to what can.
  if (v1 * v1 - v0 * v0 > reach) v1 = Math.sqrt(v0 * v0 + reach)
  else if (v0 * v0 - v1 * v1 > reach) v1 = Math.sqrt(Math.max(0, v0 * v0 - reach))
  // The highest speed from which v1 is still reachable before the end.
  let vp = Math.min(top, Math.sqrt((reach + v0 * v0 + v1 * v1) / 2))
  vp = Math.max(vp, v0, v1, EPS)
  const da = (vp * vp - v0 * v0) / (2 * a)
  const dd = (vp * vp - v1 * v1) / (2 * a)
  const dc = Math.max(0, d - da - dd)
  const ta = (vp - v0) / a
  const td = (vp - v1) / a
  const tc = dc / vp
  return { d, v0, v1, vp, a, ta, tc, td, da, dc, T: ta + tc + td }
}

/** Distance covered and speed `t` seconds into a profile: `{ s, v, acc }`. */
export function profileAt(p, t) {
  if (t <= 0) return { s: 0, v: p.v0, acc: p.ta > 0 ? p.a : 0 }
  if (t >= p.T) return { s: p.d, v: p.v1, acc: 0 }
  if (t < p.ta) return { s: p.v0 * t + 0.5 * p.a * t * t, v: p.v0 + p.a * t, acc: p.a }
  if (t < p.ta + p.tc) return { s: p.da + p.vp * (t - p.ta), v: p.vp, acc: 0 }
  const w = t - p.ta - p.tc
  return { s: Math.min(p.d, p.da + p.dc + p.vp * w - 0.5 * p.a * w * w), v: Math.max(0, p.vp - p.a * w), acc: -p.a }
}

/** Seconds into a profile at which `s` metres have been covered (the inverse of `profileAt`). */
export function profileTimeAt(p, s) {
  if (s <= 0) return 0
  if (s >= p.d) return p.T
  if (s <= p.da) return (-p.v0 + Math.sqrt(p.v0 * p.v0 + 2 * p.a * s)) / p.a
  if (s <= p.da + p.dc) return p.ta + (s - p.da) / p.vp
  const r = s - p.da - p.dc
  return p.ta + p.tc + (p.vp - Math.sqrt(Math.max(0, p.vp * p.vp - 2 * p.a * r))) / p.a
}

/**
 * Speeds at the vertices of a polyline so that the whole path can be flown
 * within the limits: `lengths[i]` and `segMax[i]` describe the piece from
 * vertex `i` to `i + 1`, `vertexMax[i]` caps the speed through vertex `i`
 * (a corner), `a` is the acceleration available. Starts at `vStart`, ends
 * at `vEnd`.
 *
 * The classic two sweeps: forward, no vertex is entered faster than the
 * aircraft can have accelerated to; backward, none faster than it can still
 * brake from. Returns a `Float64Array` of `lengths.length + 1` speeds.
 */
export function planSpeeds(lengths, segMax, vertexMax, a, vStart = 0, vEnd = 0) {
  const n = lengths.length
  const v = new Float64Array(n + 1)
  const cap = i => {
    let limit = vertexMax ? vertexMax[i] : Infinity
    if (!(limit >= 0)) limit = Infinity
    if (i > 0) limit = Math.min(limit, segMax[i - 1])
    if (i < n) limit = Math.min(limit, segMax[i])
    return limit
  }
  v[0] = Math.min(vStart, cap(0))
  for (let i = 0; i < n; i++) {
    v[i + 1] = Math.min(cap(i + 1), Math.sqrt(v[i] * v[i] + 2 * a * lengths[i]))
  }
  v[n] = Math.min(v[n], vEnd)
  for (let i = n - 1; i >= 0; i--) {
    v[i] = Math.min(v[i], Math.sqrt(v[i + 1] * v[i + 1] + 2 * a * lengths[i]))
  }
  return v
}

/**
 * Fastest speed through a corner that turns the track by `turnDeg`.
 *
 * Changing direction by θ at speed v changes the velocity by 2·v·sin(θ/2);
 * the aircraft is given `blend` seconds at acceleration `a` to do it. A
 * straight join is unlimited, a right angle slows a multirotor to walking
 * pace - which is what a waypoint mission does at a sharp bend.
 */
export function cornerSpeed(turnDeg, a, blend) {
  const theta = Math.abs(turnDeg) * Math.PI / 180
  if (theta < 1e-3) return Infinity
  return (a * blend) / (2 * Math.sin(Math.min(theta, Math.PI) / 2))
}

/** Hermite smoothstep on [0, 1]: eases a turn in and out. */
export function smoothstep(f) {
  if (f <= 0) return 0
  if (f >= 1) return 1
  return f * f * (3 - 2 * f)
}

/** Index of the last element of ascending `arr` (first `n`) that is `<= x`; -1 if none. */
export function lastAtOrBefore(arr, x, n = arr.length) {
  let lo = 0
  let hi = n - 1
  let ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (arr[mid] <= x) { ans = mid; lo = mid + 1 } else hi = mid - 1
  }
  return ans
}
