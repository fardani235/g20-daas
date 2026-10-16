// Geometry for the 3D view, as plain typed arrays.
//
// Everything the scene draws is derived here from the simulation, without
// three.js: the terrain mesh from the planner's elevation grid, the flight
// path from the timeline's pieces, the photo positions, the draped survey
// boundary. `scene.js` only wraps the arrays in buffers and materials, so
// what is on screen can be checked by tests that have no GPU.
//
// Scene axes (three.js is Y-up): **x east, y up, z south**. A point of the
// local frame `(x east, y north)` at height `h` is `(x, h, -y)`. Heights are
// relative to the ground at the takeoff point - the mission's own altitude
// reference - so the aircraft's `alt` is its scene height unchanged, and the
// terrain is drawn at `elevation - elevation(takeoff)`. No vertical
// exaggeration: a metre is a metre in every direction.

import { mercatorX, mercatorY, normalizeLng } from './frame'
import { PHASE, isMove } from './trajectory'

/** Colours of the flight path by what the aircraft is doing, as `[r, g, b]` in 0..1 (sRGB). */
export const PATH_COLORS = Object.freeze({
  survey: [0.96, 0.62, 0.04],        // #f59e0b, the planner's flight line colour
  turn: [0.58, 0.64, 0.72],          // #94a3b8
  transit: [0.65, 0.55, 0.98],       // #a78bfa
  vertical: [0.89, 0.91, 0.94],      // #e2e8f0
})

const colourOfPhase = phase => {
  if (phase === PHASE.SURVEY) return PATH_COLORS.survey
  if (phase === PHASE.TURN || phase === PHASE.HOVER) return PATH_COLORS.turn
  if (phase === PHASE.TAKEOFF || phase === PHASE.LANDING) return PATH_COLORS.vertical
  return PATH_COLORS.transit
}

/**
 * The ground as a grid of nodes: the planner's elevation grid, or - without
 * elevation data - a flat sheet at the takeoff height under the whole
 * flight.
 *
 * Returns `{ rows, cols, x, y, up, lng, lat, flat, base, bounds, upMin,
 * upMax, extent }`: per-node local coordinates, height above the takeoff
 * ground and longitude / latitude (rows south to north), the elevation that
 * is scene height zero, and the geographic and local boxes.
 */
export function groundGrid(simulation, { flatNodes = 9, margin = 0.3, minMargin = 200 } = {}) {
  const { trajectory, terrain } = simulation
  const { frame } = trajectory
  const seg = trajectory.segments

  if (terrain) {
    const { rows, cols } = terrain
    const count = rows * cols
    const x = new Float64Array(count)
    const y = new Float64Array(count)
    const up = new Float32Array(count)
    const lng = new Float64Array(count)
    const lat = new Float64Array(count)
    // Scene height zero is the ground at the takeoff point; if the grid does
    // not reach it, its lowest node (the offset is then unknown, and the
    // dashboard already says the height above ground is).
    const base = trajectory.takeoff.ground ?? terrain.min ?? 0
    const z = Float64Array.from(terrain.values)
    fillVoids(z, rows, cols, base)
    let upMin = Infinity
    let upMax = -Infinity
    for (let r = 0; r < rows; r++) {
      const la = terrain.south + (terrain.north - terrain.south) * r / (rows - 1)
      for (let c = 0; c < cols; c++) {
        const i = r * cols + c
        const lo = terrain.west + (terrain.east - terrain.west) * c / (cols - 1)
        const [px, py] = frame.forward(lo, la)
        x[i] = px
        y[i] = py
        lng[i] = frame.lng0 + normalizeLng(lo - frame.lng0)
        lat[i] = la
        up[i] = z[i] - base
        if (up[i] < upMin) upMin = up[i]
        if (up[i] > upMax) upMax = up[i]
      }
    }
    return finish({ rows, cols, x, y, up, lng, lat, flat: false, base, upMin, upMax })
  }

  // No elevation data: a level sheet under everything the simulation flies.
  let xmin = trajectory.takeoff.x
  let xmax = xmin
  let ymin = trajectory.takeoff.y
  let ymax = ymin
  for (let i = 0; i < seg.count; i++) {
    xmin = Math.min(xmin, seg.x0[i], seg.x1[i]); xmax = Math.max(xmax, seg.x0[i], seg.x1[i])
    ymin = Math.min(ymin, seg.y0[i], seg.y1[i]); ymax = Math.max(ymax, seg.y0[i], seg.y1[i])
  }
  const pad = Math.max(minMargin, margin * Math.max(xmax - xmin, ymax - ymin))
  xmin -= pad; xmax += pad; ymin -= pad; ymax += pad
  const rows = flatNodes
  const cols = flatNodes
  const count = rows * cols
  const x = new Float64Array(count)
  const y = new Float64Array(count)
  const lng = new Float64Array(count)
  const lat = new Float64Array(count)
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c
      x[i] = xmin + (xmax - xmin) * c / (cols - 1)
      y[i] = ymin + (ymax - ymin) * r / (rows - 1)
      const [lo, la] = frame.inverse(x[i], y[i])
      lng[i] = frame.lng0 + normalizeLng(lo - frame.lng0)
      lat[i] = la
    }
  }
  return finish({ rows, cols, x, y, up: new Float32Array(count), lng, lat, flat: true, base: null, upMin: 0, upMax: 0 })
}

function finish(grid) {
  let west = Infinity
  let east = -Infinity
  let south = Infinity
  let north = -Infinity
  let xmin = Infinity
  let xmax = -Infinity
  let ymin = Infinity
  let ymax = -Infinity
  for (let i = 0; i < grid.x.length; i++) {
    west = Math.min(west, grid.lng[i]); east = Math.max(east, grid.lng[i])
    south = Math.min(south, grid.lat[i]); north = Math.max(north, grid.lat[i])
    xmin = Math.min(xmin, grid.x[i]); xmax = Math.max(xmax, grid.x[i])
    ymin = Math.min(ymin, grid.y[i]); ymax = Math.max(ymax, grid.y[i])
  }
  grid.bounds = { west, south, east, north }
  grid.extent = { xmin, xmax, ymin, ymax }
  return grid
}

// Nodes without data take the mean of their known neighbours, pass after
// pass, so the mesh has no holes. Only the drawing is filled: the sampler
// the simulation reads from still says "unknown" there.
function fillVoids(z, rows, cols, fallback) {
  let missing = 0
  for (let i = 0; i < z.length; i++) if (z[i] !== z[i]) missing++
  if (!missing) return
  if (missing === z.length) { z.fill(fallback); return }
  while (missing > 0) {
    const next = Float64Array.from(z)
    let filled = 0
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const i = r * cols + c
        if (z[i] === z[i]) continue
        let sum = 0
        let n = 0
        for (let dr = -1; dr <= 1; dr++) {
          for (let dc = -1; dc <= 1; dc++) {
            const rr = r + dr
            const cc = c + dc
            if (rr < 0 || cc < 0 || rr >= rows || cc >= cols) continue
            const v = z[rr * cols + cc]
            if (v === v) { sum += v; n++ }
          }
        }
        if (n) { next[i] = sum / n; filled++ }
      }
    }
    z.set(next)
    missing -= filled
    if (!filled) break
  }
}

/**
 * Triangle mesh of a ground grid: `{ positions, indices }` in scene axes,
 * two triangles per cell, wound so the surface faces up.
 */
export function groundMesh(grid) {
  const { rows, cols } = grid
  const positions = new Float32Array(rows * cols * 3)
  for (let i = 0; i < rows * cols; i++) {
    positions[3 * i] = grid.x[i]
    positions[3 * i + 1] = grid.up[i]
    positions[3 * i + 2] = -grid.y[i]
  }
  const indices = new Uint32Array((rows - 1) * (cols - 1) * 6)
  let k = 0
  for (let r = 0; r < rows - 1; r++) {
    for (let c = 0; c < cols - 1; c++) {
      const a = r * cols + c
      const b = a + 1
      const d = a + cols
      const e = d + 1
      indices[k++] = a; indices[k++] = b; indices[k++] = d
      indices[k++] = b; indices[k++] = e; indices[k++] = d
    }
  }
  return { positions, indices }
}

/**
 * Texture coordinates of the grid's nodes for an image that is linear in
 * Web-Mercator over `box` (`{ west, south, east, north }`): the basemap
 * mosaic and the coverage raster. `v` is 1 at the north edge, matching a
 * canvas whose first row is north.
 */
export function gridUV(grid, box) {
  const uv = new Float32Array(grid.x.length * 2)
  const mx0 = mercatorX(box.west)
  const mxSpan = mercatorX(box.east) - mx0
  const myN = mercatorY(box.north)
  const mySpan = mercatorY(box.south) - myN
  for (let i = 0; i < grid.x.length; i++) {
    uv[2 * i] = (mercatorX(grid.lng[i]) - mx0) / mxSpan
    uv[2 * i + 1] = 1 - (mercatorY(grid.lat[i]) - myN) / mySpan
  }
  return uv
}

/** Walls from the grid's edge down to `floor`, so the terrain reads as a solid block: `{ positions, indices }`. */
export function skirtMesh(grid, floor) {
  const { rows, cols } = grid
  const ring = []
  for (let c = 0; c < cols; c++) ring.push(c)                                   // south edge, west to east
  for (let r = 1; r < rows; r++) ring.push(r * cols + cols - 1)                 // east edge, northwards
  for (let c = cols - 2; c >= 0; c--) ring.push((rows - 1) * cols + c)          // north edge, east to west
  for (let r = rows - 2; r >= 1; r--) ring.push(r * cols)                       // west edge, southwards
  const n = ring.length
  const positions = new Float32Array(n * 2 * 3)
  for (let k = 0; k < n; k++) {
    const i = ring[k]
    positions.set([grid.x[i], grid.up[i], -grid.y[i]], 6 * k)
    positions.set([grid.x[i], floor, -grid.y[i]], 6 * k + 3)
  }
  const indices = new Uint32Array(n * 6)
  for (let k = 0; k < n; k++) {
    const a = 2 * k
    const b = 2 * ((k + 1) % n)
    // Counter-clockwise ring seen from above: the outside is on the right.
    indices.set([a, a + 1, b, b, a + 1, b + 1], 6 * k)
  }
  return { positions, indices }
}

/** Height of the drawn ground at a local point: the sampler's value, or the takeoff level where it has none. */
export function groundHeightFn(simulation, grid) {
  const { trajectory } = simulation
  if (grid.flat || grid.base === null) return () => 0
  return (x, y) => {
    const g = trajectory.groundAt(x, y)
    return g === null ? 0 : g - grid.base
  }
}

/**
 * The flight path as independent line pieces, one per moving piece of the
 * timeline: `{ positions, colors, before, count }`. `positions` holds two
 * scene points per piece, `colors` an RGB per point, and `before[i]` is the
 * number of pieces drawn before timeline piece `i` - so "flown so far" is a
 * prefix of the arrays.
 */
export function pathPieces(trajectory) {
  const seg = trajectory.segments
  const before = new Uint32Array(seg.count + 1)
  let count = 0
  for (let i = 0; i < seg.count; i++) {
    before[i] = count
    if (isMove(seg.kind[i])) count++
  }
  before[seg.count] = count
  const positions = new Float32Array(count * 6)
  const colors = new Float32Array(count * 6)
  let k = 0
  for (let i = 0; i < seg.count; i++) {
    if (!isMove(seg.kind[i])) continue
    positions[k] = seg.x0[i]; positions[k + 1] = seg.z0[i]; positions[k + 2] = -seg.y0[i]
    positions[k + 3] = seg.x1[i]; positions[k + 4] = seg.z1[i]; positions[k + 5] = -seg.y1[i]
    const colour = colourOfPhase(seg.phase[i])
    colors.set(colour, k)
    colors.set(colour, k + 3)
    k += 6
  }
  return { positions, colors, before, count }
}

/** How much of `pathPieces` has been flown at a sampled state: whole pieces, and the start of the one in progress (or -1). */
export function flownPieces(pieces, trajectory, state) {
  const seg = trajectory.segments
  const i = state.index
  return { whole: pieces.before[i], partial: isMove(seg.kind[i]) ? i : -1 }
}

/** Where every photo is taken, in scene axes: `Float32Array` of `x, up, z`. */
export function photoPoints(trajectory) {
  const { photos } = trajectory
  const out = new Float32Array(photos.count * 3)
  for (let i = 0; i < photos.count; i++) {
    out[3 * i] = photos.x[i]
    out[3 * i + 1] = photos.alt[i]
    out[3 * i + 2] = -photos.y[i]
  }
  return out
}

/** The ends of the flight lines, in scene axes; at most `max` of them on a long mission. */
export function waypointPoints(trajectory, { max = 4000 } = {}) {
  const { mission, frame } = trajectory
  const ends = mission.waypoints.filter(w => w.role !== 'via')
  const stride = Math.max(1, Math.ceil(ends.length / max))
  const kept = ends.filter((_, i) => i % stride === 0 || i === ends.length - 1)
  const out = new Float32Array(kept.length * 3)
  kept.forEach((w, i) => {
    const [x, y] = frame.forward(w.lng, w.lat)
    out.set([x, w.alt, -y], 3 * i)
  })
  return out
}

/**
 * The rings of the area the mission covers, laid on the ground: an array of
 * closed line strips (`Float32Array` of scene points), densified every
 * `step` metres and lifted `lift` metres so they do not sink into the mesh.
 */
export function drapedRings(mission, frame, heightAt, { step = 20, lift = 1 } = {}) {
  const rings = (mission.coverage?.coordinates || []).filter(r => Array.isArray(r) && r.length >= 3)
  return rings.map(ring => {
    const local = ring.map(([lng, lat]) => frame.forward(lng, lat))
    if (Math.hypot(local[0][0] - local[local.length - 1][0], local[0][1] - local[local.length - 1][1]) > 1e-6) local.push(local[0])
    const pts = []
    for (let i = 0; i < local.length - 1; i++) {
      const [ax, ay] = local[i]
      const [bx, by] = local[i + 1]
      const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / step))
      for (let k = 0; k < n; k++) {
        const x = ax + (bx - ax) * k / n
        const y = ay + (by - ay) * k / n
        pts.push(x, heightAt(x, y) + lift, -y)
      }
    }
    const [lx, ly] = local[local.length - 1]
    pts.push(lx, heightAt(lx, ly) + lift, -ly)
    return Float32Array.from(pts)
  })
}

/** Centre and radius of a ball around the ground grid and the flight, for framing the camera. */
export function sceneBounds(grid, trajectory) {
  const { xmin, xmax, ymin, ymax } = grid.extent
  let top = grid.upMax
  const seg = trajectory.segments
  for (let i = 0; i < seg.count; i++) top = Math.max(top, seg.z0[i], seg.z1[i])
  const cx = (xmin + xmax) / 2
  const cy = (ymin + ymax) / 2
  const cz = (grid.upMin + top) / 2
  const radius = Math.hypot(xmax - xmin, ymax - ymin, top - grid.upMin) / 2
  return { center: [cx, cz, -cy], radius: Math.max(radius, 10), top, bottom: grid.upMin }
}
