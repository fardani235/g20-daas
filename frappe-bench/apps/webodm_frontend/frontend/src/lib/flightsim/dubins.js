// Turns an aircraft that cannot hover can actually fly.
//
// A multirotor stops at the end of a flight line, yaws and flies straight to
// the next one. A fixed-wing or a VTOL in forward flight cannot: it joins
// two lines with arcs no tighter than its turn radius. The shortest such
// join between two positions with given headings is a Dubins path - three
// pieces, each a left arc, a right arc or a straight (Dubins 1957; the
// closed forms below follow Shkel & Lumelsky 2001).
//
// The planner only *approximates* these turns in its time estimate (a
// straight hop plus half a circle); the simulator flies the real curve, and
// says so where its figures differ.
//
// Angles here are mathematical: radians, counter-clockwise from +x (east).
// `bearingToAngle` converts from the compass bearings used everywhere else.

const TAU = Math.PI * 2
const mod = a => ((a % TAU) + TAU) % TAU

export const bearingToAngle = deg => (90 - deg) * Math.PI / 180
export const angleToBearing = rad => (((90 - rad * 180 / Math.PI) % 360) + 360) % 360

const WORDS = ['LSL', 'RSR', 'LSR', 'RSL', 'RLR', 'LRL']

// Segment lengths (in units of the radius) of one path type, or null where it does not exist.
function solve(word, d, alpha, beta) {
  const sa = Math.sin(alpha)
  const sb = Math.sin(beta)
  const ca = Math.cos(alpha)
  const cb = Math.cos(beta)
  const cab = Math.cos(alpha - beta)
  switch (word) {
    case 'LSL': {
      const p2 = 2 + d * d - 2 * cab + 2 * d * (sa - sb)
      if (p2 < 0) return null
      const tmp = Math.atan2(cb - ca, d + sa - sb)
      return [mod(tmp - alpha), Math.sqrt(p2), mod(beta - tmp)]
    }
    case 'RSR': {
      const p2 = 2 + d * d - 2 * cab + 2 * d * (sb - sa)
      if (p2 < 0) return null
      const tmp = Math.atan2(ca - cb, d - sa + sb)
      return [mod(alpha - tmp), Math.sqrt(p2), mod(tmp - beta)]
    }
    case 'LSR': {
      const p2 = -2 + d * d + 2 * cab + 2 * d * (sa + sb)
      if (p2 < 0) return null
      const p = Math.sqrt(p2)
      const tmp = Math.atan2(-ca - cb, d + sa + sb) - Math.atan2(-2, p)
      return [mod(tmp - alpha), p, mod(tmp - mod(beta))]
    }
    case 'RSL': {
      const p2 = -2 + d * d + 2 * cab - 2 * d * (sa + sb)
      if (p2 < 0) return null
      const p = Math.sqrt(p2)
      const tmp = Math.atan2(ca + cb, d - sa - sb) - Math.atan2(2, p)
      return [mod(alpha - tmp), p, mod(beta - tmp)]
    }
    case 'RLR': {
      const tmp = (6 - d * d + 2 * cab + 2 * d * (sa - sb)) / 8
      if (Math.abs(tmp) > 1) return null
      const p = mod(TAU - Math.acos(tmp))
      const t = mod(alpha - Math.atan2(ca - cb, d - sa + sb) + p / 2)
      return [t, p, mod(alpha - beta - t + p)]
    }
    case 'LRL': {
      const tmp = (6 - d * d + 2 * cab + 2 * d * (sb - sa)) / 8
      if (Math.abs(tmp) > 1) return null
      const p = mod(TAU - Math.acos(tmp))
      const t = mod(-alpha - Math.atan2(ca - cb, d + sa - sb) + p / 2)
      return [t, p, mod(mod(beta) - alpha - t + p)]
    }
    default:
      return null
  }
}

// Pose after flying one piece of `len` metres from `(x, y, th)`.
function advance(x, y, th, type, len, r) {
  if (type === 'S') return [x + len * Math.cos(th), y + len * Math.sin(th), th]
  const phi = len / r
  if (type === 'L') return [x + r * (Math.sin(th + phi) - Math.sin(th)), y + r * (Math.cos(th) - Math.cos(th + phi)), th + phi]
  return [x + r * (Math.sin(th) - Math.sin(th - phi)), y + r * (Math.cos(th - phi) - Math.cos(th)), th - phi]
}

function endPose(start, pieces, r) {
  let [x, y, th] = start
  for (const piece of pieces) [x, y, th] = advance(x, y, th, piece.type, piece.len, r)
  return [x, y, th]
}

/**
 * Shortest path of curvature at most `1 / r` from `(x0, y0)` heading `th0`
 * to `(x1, y1)` heading `th1`.
 *
 * Returns `{ length, pieces: [{ type: 'L' | 'R' | 'S', len }], word, start, r }`.
 * Every candidate is flown forward and kept only if it really arrives, so a
 * numerical corner case can cost optimality but never correctness.
 */
export function dubinsPath(x0, y0, th0, x1, y1, th1, r) {
  const start = [x0, y0, th0]
  const dx = x1 - x0
  const dy = y1 - y0
  const dist = Math.hypot(dx, dy)
  if (!(r > 0)) return { length: dist, pieces: dist > 0 ? [{ type: 'S', len: dist }] : [], word: 'S', start, r: 1 }
  const d = dist / r
  const theta = Math.atan2(dy, dx)
  const alpha = mod(th0 - theta)
  const beta = mod(th1 - theta)
  const tol = Math.max(1e-6 * r, 1e-6)
  let best = null
  for (const word of WORDS) {
    const tpq = solve(word, d, alpha, beta)
    if (!tpq || tpq.some(v => !Number.isFinite(v))) continue
    const pieces = tpq.map((v, i) => ({ type: word[i], len: v * r })).filter(p => p.len > 1e-9)
    const length = pieces.reduce((s, p) => s + p.len, 0)
    if (best && length >= best.length) continue
    const [ex, ey, eth] = endPose(start, pieces, r)
    const turnError = Math.abs(Math.atan2(Math.sin(eth - th1), Math.cos(eth - th1)))
    if (Math.hypot(ex - x1, ey - y1) > tol || turnError > 1e-6) continue
    best = { length, pieces, word, start, r }
  }
  // Unreachable in practice (LSL and RSR always exist); a straight line keeps the simulation running.
  return best || { length: dist, pieces: dist > 0 ? [{ type: 'S', len: dist }] : [], word: 'S', start, r }
}

/**
 * Points along a Dubins path: `[[x, y], ...]`, first and last included.
 * Arcs are cut every `stepRad` of turn, straights are two points.
 */
export function sampleDubins(path, stepRad = Math.PI / 30) {
  let [x, y, th] = path.start
  const out = [[x, y]]
  for (const piece of path.pieces) {
    if (piece.type === 'S') {
      [x, y, th] = advance(x, y, th, 'S', piece.len, path.r)
      out.push([x, y])
      continue
    }
    const n = Math.max(1, Math.ceil((piece.len / path.r) / stepRad))
    const step = piece.len / n
    for (let i = 0; i < n; i++) {
      [x, y, th] = advance(x, y, th, piece.type, step, path.r)
      out.push([x, y])
    }
  }
  return out
}

/** Points of `turns` full circles of radius `r` starting and ending at `(x, y)` heading `th`, turning left. */
export function loiterCircles(x, y, th, r, turns, stepRad = Math.PI / 30) {
  const out = []
  const n = Math.max(8, Math.ceil(TAU / stepRad))
  let pose = [x, y, th]
  for (let k = 0; k < turns * n; k++) {
    pose = advance(pose[0], pose[1], pose[2], 'L', (TAU * r) / n, r)
    out.push([pose[0], pose[1]])
  }
  return out
}
