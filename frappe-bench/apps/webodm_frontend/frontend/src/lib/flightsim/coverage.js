// Estimated photo coverage: which ground ends up in how many photos.
//
// Every exposure of the simulated flight is projected to its ground
// footprint and burned into a raster over the site. Each cell remembers the
// first photo that saw it and how many photos see it in total. From that
// come the figures on the dashboard (share of the survey area covered, how
// many photos overlap a typical point) and the overlay both views draw -
// the same raster, so the 2D map and the 3D scene cannot disagree.
//
// The raster is regular in Web-Mercator, the projection of the map: its
// canvas can be laid on the Leaflet map as an image and on the 3D terrain as
// a texture without resampling. Cells are filled once, when the simulation
// is built; showing coverage "up to now" during playback only compares each
// cell's first photo with the number of photos taken so far.
//
// This is geometry, not image quality: it says where the camera pointed,
// under the footprint model in `camera.js`. It is an estimate.

import { mercatorX, mercatorY, normalizeLng } from './frame'
import { footprintCorners, overlapAt, photoHeights } from './camera'

const LATTICE = 16

/** Photos-per-point classes of the overlap view, lowest first: `{ min, label, rgba }`. */
export const OVERLAP_CLASSES = Object.freeze([
  { min: 1, label: '1 photo', rgba: [220, 38, 38, 150] },
  { min: 2, label: '2', rgba: [249, 115, 22, 150] },
  { min: 3, label: '3–4', rgba: [234, 179, 8, 140] },
  { min: 5, label: '5–8', rgba: [34, 197, 94, 130] },
  { min: 9, label: '9 or more', rgba: [13, 148, 136, 140] },
])
export const COVERED_RGBA = Object.freeze([16, 185, 129, 105])

const classOfCount = count => {
  let k = -1
  for (let i = 0; i < OVERLAP_CLASSES.length; i++) if (count >= OVERLAP_CLASSES[i].min) k = i
  return k
}

// 0xAABBGGRR, the byte order of a Uint32 view over canvas pixels on every platform browsers run on.
const pack = ([r, g, b, a]) => ((a << 24) | (b << 16) | (g << 8) | r) >>> 0

/**
 * Build the coverage raster of a trajectory. `model` is `cameraModel(mission)`.
 * Returns null when there is nothing to cover (no camera, no exposures).
 *
 * `maxSide` caps the raster's longer side in cells (default 512).
 */
export function buildCoverage(trajectory, model, { maxSide = 512 } = {}) {
  const photos = trajectory?.photos
  if (!model || !photos || !photos.count) return null
  const { frame, mission } = trajectory
  const heights = photoHeights(trajectory)
  const corners = new Float64Array(8)

  // -- extent in the local frame: every footprint, and the area to be covered -------------
  let xmin = Infinity
  let ymin = Infinity
  let xmax = -Infinity
  let ymax = -Infinity
  const grow = (x, y) => {
    if (x < xmin) xmin = x
    if (x > xmax) xmax = x
    if (y < ymin) ymin = y
    if (y > ymax) ymax = y
  }
  for (let i = 0; i < photos.count; i++) {
    footprintCorners(model, photos.x[i], photos.y[i], Math.max(heights.agl[i], 1), photos.heading[i], corners)
    for (let k = 0; k < 8; k += 2) grow(corners[k], corners[k + 1])
  }
  const rings = (mission.coverage?.coordinates || []).filter(r => Array.isArray(r) && r.length >= 3)
  for (const ring of rings) for (const [lng, lat] of ring) grow(...frame.forward(lng, lat))
  const padX = Math.max(2, (xmax - xmin) * 0.02)
  const padY = Math.max(2, (ymax - ymin) * 0.02)
  xmin -= padX; xmax += padX; ymin -= padY; ymax += padY

  const cell = Math.max(0.25, Math.max(xmax - xmin, ymax - ymin) / maxSide)
  const cols = Math.max(1, Math.ceil((xmax - xmin) / cell))
  const rows = Math.max(1, Math.ceil((ymax - ymin) / cell))

  // -- the raster's geographic box, and a fast local -> cell mapping -----------------------
  // Exact at the nodes of a lattice over the extent, bilinear between them:
  // for the largest plan the planner accepts the error is a fraction of a metre.
  const n = LATTICE
  const nodeLng = new Float64Array((n + 1) * (n + 1))
  const nodeLat = new Float64Array((n + 1) * (n + 1))
  let west = Infinity
  let east = -Infinity
  let south = Infinity
  let north = -Infinity
  for (let j = 0; j <= n; j++) {
    for (let i = 0; i <= n; i++) {
      const [lng, lat] = frame.inverse(xmin + (xmax - xmin) * i / n, ymin + (ymax - ymin) * j / n)
      // Unwrapped around the frame's origin, so a site on the antimeridian stays in one piece.
      const l = frame.lng0 + normalizeLng(lng - frame.lng0)
      nodeLng[j * (n + 1) + i] = l
      nodeLat[j * (n + 1) + i] = lat
      if (l < west) west = l
      if (l > east) east = l
      if (lat < south) south = lat
      if (lat > north) north = lat
    }
  }
  const mx0 = mercatorX(west)
  const mxSpan = mercatorX(east) - mx0
  const myN = mercatorY(north)
  const mySpan = mercatorY(south) - myN
  const colOfLng = lng => (mercatorX(lng) - mx0) / mxSpan * cols
  const rowOfLat = lat => (mercatorY(lat) - myN) / mySpan * rows
  const nodeCol = nodeLng.map(colOfLng)
  const nodeRow = nodeLat.map(rowOfLat)
  const cellOf = [0, 0]
  function toCell(x, y, out = cellOf) {
    const u = (x - xmin) / (xmax - xmin) * n
    const v = (y - ymin) / (ymax - ymin) * n
    const i = Math.min(Math.max(Math.floor(u), 0), n - 1)
    const j = Math.min(Math.max(Math.floor(v), 0), n - 1)
    const fu = u - i
    const fv = v - j
    const a = j * (n + 1) + i
    const b = a + n + 1
    out[0] = (nodeCol[a] * (1 - fu) + nodeCol[a + 1] * fu) * (1 - fv) + (nodeCol[b] * (1 - fu) + nodeCol[b + 1] * fu) * fv
    out[1] = (nodeRow[a] * (1 - fu) + nodeRow[a + 1] * fu) * (1 - fv) + (nodeRow[b] * (1 - fu) + nodeRow[b + 1] * fu) * fv
    return out
  }

  // -- burn every footprint -----------------------------------------------------------------
  const cells = cols * rows
  const first = new Int32Array(cells).fill(-1)
  const count = new Uint16Array(cells)
  const px = new Float64Array(4)
  const py = new Float64Array(4)
  for (let i = 0; i < photos.count; i++) {
    footprintCorners(model, photos.x[i], photos.y[i], Math.max(heights.agl[i], 1), photos.heading[i], corners)
    let top = Infinity
    let bottom = -Infinity
    for (let k = 0; k < 4; k++) {
      toCell(corners[2 * k], corners[2 * k + 1])
      px[k] = cellOf[0]
      py[k] = cellOf[1]
      if (cellOf[1] < top) top = cellOf[1]
      if (cellOf[1] > bottom) bottom = cellOf[1]
    }
    const r0 = Math.max(0, Math.ceil(top - 0.5))
    const r1 = Math.min(rows - 1, Math.floor(bottom - 0.5))
    for (let r = r0; r <= r1; r++) {
      // The footprint is convex: one span per row, between where its edges cross the row's centre line.
      const y = r + 0.5
      let left = Infinity
      let right = -Infinity
      for (let k = 0; k < 4; k++) {
        const m = (k + 1) & 3
        const ya = py[k]
        const yb = py[m]
        if ((ya <= y && yb > y) || (yb <= y && ya > y)) {
          const x = px[k] + (y - ya) / (yb - ya) * (px[m] - px[k])
          if (x < left) left = x
          if (x > right) right = x
        }
      }
      if (!(right >= left)) continue
      const c0 = Math.max(0, Math.ceil(left - 0.5))
      const c1 = Math.min(cols - 1, Math.floor(right - 0.5))
      for (let c = c0, idx = r * cols + c0; c <= c1; c++, idx++) {
        if (first[idx] < 0) first[idx] = i
        if (count[idx] < 65535) count[idx]++
      }
    }
  }

  // -- the area the mission is meant to cover (even-odd over the plan's rings) -----------------
  const target = new Uint8Array(cells)
  let targetCells = 0
  if (rings.length) {
    const edges = []
    for (const ring of rings) {
      for (let k = 0; k < ring.length; k++) {
        const a = ring[k]
        const b = ring[(k + 1) % ring.length]
        const la = frame.lng0 + normalizeLng(a[0] - frame.lng0)
        const lb = frame.lng0 + normalizeLng(b[0] - frame.lng0)
        edges.push([colOfLng(la), rowOfLat(a[1]), colOfLng(lb), rowOfLat(b[1])])
      }
    }
    const xs = []
    for (let r = 0; r < rows; r++) {
      const y = r + 0.5
      xs.length = 0
      for (const [xa, ya, xb, yb] of edges) {
        if ((ya <= y && yb > y) || (yb <= y && ya > y)) xs.push(xa + (y - ya) / (yb - ya) * (xb - xa))
      }
      xs.sort((p, q) => p - q)
      for (let k = 0; k + 1 < xs.length; k += 2) {
        const c0 = Math.max(0, Math.ceil(xs[k] - 0.5))
        const c1 = Math.min(cols - 1, Math.floor(xs[k + 1] - 0.5))
        for (let c = c0; c <= c1; c++) { target[r * cols + c] = 1; targetCells++ }
      }
    }
  }
  // Without a target polygon the figures describe whatever the photos cover.
  const scoped = targetCells > 0
  if (!scoped) {
    for (let i = 0; i < cells; i++) if (count[i]) { target[i] = 1; targetCells++ }
  }

  // -- figures ----------------------------------------------------------------------------------
  const firstSeen = new Uint32Array(photos.count + 1)
  const histogram = new Array(OVERLAP_CLASSES.length + 1).fill(0)   // [uncovered, class 0, class 1, ...]
  let covered = 0
  let sum = 0
  let lowest = Infinity
  const cls = new Int8Array(cells)
  for (let i = 0; i < cells; i++) {
    cls[i] = classOfCount(count[i])
    if (!target[i]) continue
    if (count[i]) {
      covered++
      sum += count[i]
      firstSeen[first[i] + 1]++
      if (count[i] < lowest) lowest = count[i]
    }
    histogram[cls[i] + 1]++
  }
  for (let k = 1; k <= photos.count; k++) firstSeen[k] += firstSeen[k - 1]
  const share = k => (targetCells ? histogram.slice(k + 1).reduce((s, v) => s + v, 0) / targetCells : 0)

  const planned = { front: Number(mission.params?.front_overlap), side: Number(mission.params?.side_overlap) }
  const lowestOverlap = heights.min !== null ? overlapAt(model, heights.min) : null
  const colours = OVERLAP_CLASSES.map(c => pack(c.rgba))
  const coveredColour = pack(COVERED_RGBA)
  let painted = null

  return {
    cols,
    rows,
    cellM: cell,
    bounds: { west, south, east, north },
    first,
    count,
    target,
    targetCells,
    scoped,
    heights,
    toCell,
    /** Share (0..1) of the survey area inside at least one of the first `photoCount` photos. */
    fractionAt(photoCount) {
      if (!targetCells) return 0
      const k = Math.min(Math.max(photoCount | 0, 0), photos.count)
      return firstSeen[k] / targetCells
    },
    stats: {
      coveredPercent: targetCells ? (covered / targetCells) * 100 : 0,
      meanPhotos: covered ? sum / covered : 0,
      minPhotos: covered ? lowest : 0,
      // Share of the survey area in at least 2, 3 and 5 photos: what stereo, a robust match and dense 3D want.
      atLeast2: share(1) * 100,
      atLeast3: share(2) * 100,
      atLeast5: share(3) * 100,
      histogram: histogram.map(v => (targetCells ? (v / targetCells) * 100 : 0)),
      plannedFront: Number.isFinite(planned.front) ? planned.front : null,
      plannedSide: Number.isFinite(planned.side) ? planned.side : null,
      lowestFront: lowestOverlap ? lowestOverlap.front : null,
      lowestSide: lowestOverlap ? lowestOverlap.side : null,
      heightMin: heights.min,
      heightMax: heights.max,
      groundKnown: heights.known === photos.count,
      nadir: model.nadir,
    },
    /**
     * Paint the raster into `pixels` (a `Uint8ClampedArray` of `cols * rows * 4`,
     * e.g. `ImageData.data`), showing the first `photoCount` photos.
     * `mode`: `'covered'` tints what has been photographed, `'overlap'`
     * colours it by how many photos will see it. Returns false, without
     * touching `pixels`, when that is what was painted last.
     */
    paint(pixels, photoCount, mode = 'covered') {
      const k = Math.min(Math.max(photoCount | 0, 0), photos.count)
      if (painted && painted.k === k && painted.mode === mode && painted.pixels === pixels) return false
      const out = new Uint32Array(pixels.buffer, pixels.byteOffset, cells)
      if (mode === 'overlap') {
        for (let i = 0; i < cells; i++) out[i] = first[i] >= 0 && first[i] < k ? colours[cls[i]] : 0
      } else {
        for (let i = 0; i < cells; i++) out[i] = first[i] >= 0 && first[i] < k ? coveredColour : 0
      }
      painted = { k, mode, pixels }
      return true
    },
  }
}
