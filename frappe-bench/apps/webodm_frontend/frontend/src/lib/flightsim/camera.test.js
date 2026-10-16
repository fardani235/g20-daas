import { describe, it, expect } from 'vitest'
import fixture from '../__fixtures__/flightsim.json'
import { syntheticMission, syntheticTerrain } from '../__fixtures__/syntheticMission'
import { boresightGround, cameraModel, footprintCorners, footprintSize, overlapAt, photoHeights } from './camera'
import { COVERED_RGBA, OVERLAP_CLASSES, buildCoverage } from './coverage'
import { LocalFrame, mercatorX, mercatorY } from './frame'
import { createTerrain } from './terrain'
import { buildTrajectory } from './trajectory'

const { missions } = fixture
const sim = name => {
  const trajectory = buildTrajectory(missions[name], { terrain: createTerrain(fixture.terrain[name]) })
  const model = cameraModel(missions[name])
  return { trajectory, model, coverage: buildCoverage(trajectory, model) }
}
const ring = c => [[c[0], c[1]], [c[2], c[3]], [c[4], c[5]], [c[6], c[7]]]
const shoelace = pts => Math.abs(pts.reduce((s, p, i) => {
  const q = pts[(i + 1) % pts.length]
  return s + p[0] * q[1] - q[0] * p[1]
}, 0)) / 2

describe('camera model', () => {
  it('reproduces the footprint and resolution the planner computed', () => {
    for (const name of ['area_flat', 'area_terrain', 'fixed_wing', 'vtol']) {
      const m = missions[name]
      const model = cameraModel(m)
      const size = footprintSize(model, m.camera.altitude_m)
      expect(size.across).toBeCloseTo(m.camera.footprint_across_m, 1)
      expect(size.along).toBeCloseTo(m.camera.footprint_along_m, 1)
      expect(size.gsdCm).toBeCloseTo(m.camera.gsd_cm, 2)
    }
  })

  it('at the planned height the planned spacings give the planned overlaps', () => {
    for (const name of ['area_flat', 'multi_battery', 'corridor']) {
      const m = missions[name]
      const o = overlapAt(cameraModel(m), m.camera.altitude_m)
      expect(o.front).toBeCloseTo(m.params.front_overlap, 0)
      expect(o.side).toBeCloseTo(m.params.side_overlap, 0)
    }
  })

  it('rising ground eats overlap; below half the footprint it becomes a gap', () => {
    const m = missions.area_flat                                   // 80 % front, 70 % side at 100 m
    const model = cameraModel(m)
    expect(overlapAt(model, 50).front).toBeCloseTo(60, 0)          // half the height: the spacing is twice the share
    expect(overlapAt(model, 50).side).toBeCloseTo(40, 0)
    expect(overlapAt(model, 25).side).toBeLessThan(0)              // a gap between lines
    expect(overlapAt(model, 0).front).toBe(-100)
  })

  it('landscape puts the long side across the line, portrait the short side', () => {
    const landscape = cameraModel(missions.area_flat)
    expect(landscape.orientation).toBe('landscape')
    expect(landscape.tanAcross).toBeGreaterThan(landscape.tanAlong)
    const portrait = cameraModel({ ...missions.area_flat, camera: { ...missions.area_flat.camera, orientation: 'portrait' } })
    expect(portrait.tanAcross).toBeCloseTo(landscape.tanAlong, 12)
    expect(portrait.tanAlong).toBeCloseTo(landscape.tanAcross, 12)
    expect(portrait.aspect).toBeCloseTo(1 / landscape.aspect, 12)
  })

  it('field of view comes from the sensor and the lens', () => {
    const model = cameraModel(missions.area_flat)
    const cam = missions.area_flat.equipment.camera
    const long = Math.max(cam.sensor_width_mm, cam.sensor_height_mm)
    expect(model.fovAcrossDeg).toBeCloseTo(2 * Math.atan(long / (2 * cam.focal_length_mm)) * 180 / Math.PI, 9)
    expect(model.fovAcrossDeg).toBeGreaterThan(60)                 // a wide mapping lens
    expect(model.fovAcrossDeg).toBeLessThan(100)
    expect(model.nadir).toBe(true)
  })

  it('is null without a usable camera', () => {
    expect(cameraModel(null)).toBeNull()
    expect(cameraModel({ equipment: { camera: { sensor_width_mm: 0, sensor_height_mm: 1, focal_length_mm: 1 } } })).toBeNull()
  })
})

describe('ground footprint', () => {
  const model = cameraModel(missions.area_flat)

  it('straight down: a rectangle under the aircraft, long side across the track', () => {
    const c = ring(footprintCorners(model, 100, 200, 100, 0))      // flying north
    const size = footprintSize(model, 100)
    const xs = c.map(p => p[0])
    const ys = c.map(p => p[1])
    expect(Math.max(...xs) - Math.min(...xs)).toBeCloseTo(size.across, 6)      // east-west = across
    expect(Math.max(...ys) - Math.min(...ys)).toBeCloseTo(size.along, 6)
    expect((Math.max(...xs) + Math.min(...xs)) / 2).toBeCloseTo(100, 6)        // centred on the nadir point
    expect((Math.max(...ys) + Math.min(...ys)) / 2).toBeCloseTo(200, 6)
    expect(shoelace(c)).toBeCloseTo(size.across * size.along, 4)
    // Rear-left first: behind (south) and to the left (west) when flying north.
    expect(c[0][0]).toBeLessThan(100)
    expect(c[0][1]).toBeLessThan(200)
    expect(c[2][0]).toBeGreaterThan(100)
    expect(c[2][1]).toBeGreaterThan(200)
  })

  it('turns with the heading', () => {
    const north = ring(footprintCorners(model, 0, 0, 100, 0))
    const east = ring(footprintCorners(model, 0, 0, 100, 90))
    // Rotating the north-up footprint by 90° clockwise gives the east-bound one.
    north.forEach(([x, y], k) => {
      expect(east[k][0]).toBeCloseTo(y, 6)
      expect(east[k][1]).toBeCloseTo(-x, 6)
    })
  })

  it('scales with the height above ground', () => {
    const low = ring(footprintCorners(model, 0, 0, 50, 37))
    const high = ring(footprintCorners(model, 0, 0, 100, 37))
    expect(shoelace(high)).toBeCloseTo(4 * shoelace(low), 3)
  })

  it('an oblique camera looks ahead: the footprint moves forward and widens with distance', () => {
    const oblique = { ...model, pitch: -60, nadir: false }
    const c = ring(footprintCorners(oblique, 0, 0, 100, 0))
    const [rearLeft, rearRight, frontRight, frontLeft] = c
    expect(frontLeft[1]).toBeGreaterThan(rearLeft[1])
    expect((frontLeft[1] + rearLeft[1]) / 2).toBeGreaterThan(20)               // ahead of the aircraft
    expect(frontRight[0] - frontLeft[0]).toBeGreaterThan(rearRight[0] - rearLeft[0])   // a trapezoid
    const centre = boresightGround(oblique, 0, 0, 100, 0)
    expect(centre[0]).toBeCloseTo(0, 9)
    expect(centre[1]).toBeCloseTo(100 / Math.tan(60 * Math.PI / 180), 6)
    expect(boresightGround(model, 5, 6, 100, 123)).toEqual([5, 6])              // nadir: straight below
  })

  it('a camera at the horizon gets a finite footprint', () => {
    const level = { ...model, pitch: 0, nadir: false }
    const c = footprintCorners(level, 0, 0, 100, 0)
    expect(Array.from(c).every(Number.isFinite)).toBe(true)
    expect(Math.max(...Array.from(c).map(Math.abs))).toBeLessThanOrEqual(600 + 1e-6)   // six flying heights
    expect(boresightGround(level, 0, 0, 100, 0)).toEqual([0, 0])
  })

  it('fills a caller-supplied array', () => {
    const out = new Float64Array(8)
    expect(footprintCorners(model, 1, 2, 100, 0, out)).toBe(out)
    expect(out[0]).not.toBe(0)
  })
})

describe('height above ground at the exposures', () => {
  it('follows the terrain when it is known', () => {
    const { trajectory } = sim('area_slope')
    const h = photoHeights(trajectory)
    expect(h.agl.length).toBe(133)
    expect(h.max - h.min).toBeGreaterThan(20)                      // a 10 % slope under a level flight
    expect(h.known).toBeGreaterThan(100)
  })

  it('uses the plan\'s own assumption - flat ground - when it is not', () => {
    const { trajectory } = sim('area_flat')
    const h = photoHeights(trajectory)
    expect(h.known).toBe(0)
    expect([h.min, h.max]).toEqual([100, 100])
  })
})

describe('coverage raster', () => {
  it('a planned grid covers its survey area, several photos deep', () => {
    const { coverage } = sim('area_flat')
    const s = coverage.stats
    expect(s.coveredPercent).toBeGreaterThan(99.5)
    expect(s.atLeast3).toBeGreaterThan(99)
    // 80 % front and 70 % side overlap put a point in about 1 / (0.2 x 0.3) photos.
    expect(s.meanPhotos).toBeGreaterThan(10)
    expect(s.meanPhotos).toBeLessThan(18)
    expect(s.minPhotos).toBeGreaterThanOrEqual(3)
    expect([s.plannedFront, s.plannedSide]).toEqual([80, 70])
    expect(s.nadir).toBe(true)
    expect(coverage.scoped).toBe(true)
  })

  it('mean photos per point is total footprint area over covered area', () => {
    // An independent count: every footprint is across x along, the raster says how much ground they cover.
    const { trajectory, model, coverage } = sim('area_flat')
    const size = footprintSize(model, 100)
    const total = trajectory.photos.count * size.across * size.along
    let cellsCovered = 0
    let sum = 0
    for (let i = 0; i < coverage.count.length; i++) if (coverage.count[i]) { cellsCovered++; sum += coverage.count[i] }
    const cellArea = total / sum                                   // what one cell must be worth for the sums to agree
    expect(Math.sqrt(cellArea)).toBeCloseTo(coverage.cellM, 1)
    expect(cellsCovered * cellArea).toBeGreaterThan(missions.area_flat.stats.area_m2)
  })

  it('grows with the photos taken, from nothing to everything', () => {
    const { trajectory, coverage } = sim('area_flat')
    expect(coverage.fractionAt(0)).toBe(0)
    let prev = 0
    for (let k = 0; k <= trajectory.photos.count; k += 7) {
      const f = coverage.fractionAt(k)
      expect(f).toBeGreaterThanOrEqual(prev)
      prev = f
    }
    expect(coverage.fractionAt(trajectory.photos.count)).toBeCloseTo(coverage.stats.coveredPercent / 100, 9)
    expect(coverage.fractionAt(1e9)).toBe(coverage.fractionAt(trajectory.photos.count))
    // One line of seven covers well under half; after four lines more than half.
    const perLine = missions.area_flat.legs[0].photos
    expect(coverage.fractionAt(perLine)).toBeLessThan(0.45)
    expect(coverage.fractionAt(perLine * 4)).toBeGreaterThan(0.5)
  })

  it('the first photo covers exactly its footprint', () => {
    const { trajectory, model, coverage } = sim('area_flat')
    let cells = 0
    for (let i = 0; i < coverage.first.length; i++) if (coverage.first[i] === 0) cells++
    const size = footprintSize(model, 100)
    expect(cells * coverage.cellM ** 2).toBeCloseTo(size.across * size.along, -3)      // within the cell quantisation
    expect(Math.abs(cells * coverage.cellM ** 2 - size.across * size.along) / (size.across * size.along)).toBeLessThan(0.05)
    // And it is where the first exposure is.
    const cell = coverage.toCell(trajectory.photos.x[0], trajectory.photos.y[0])
    const idx = Math.floor(cell[1]) * coverage.cols + Math.floor(cell[0])
    expect(coverage.first[idx]).toBe(0)
  })

  it('is georeferenced: a raster cell is where the map says it is', () => {
    const { trajectory, coverage } = sim('area_terrain')
    const { west, south, east, north } = coverage.bounds
    const frame = LocalFrame.ofMission(missions.area_terrain)
    // The raster is linear in Web-Mercator; check the fast local->cell mapping against the exact one.
    for (const [x, y] of [[0, 0], [250, -120], [-290, 140], [trajectory.takeoff.x, trajectory.takeoff.y]]) {
      const [lng, lat] = frame.inverse(x, y)
      const col = (mercatorX(lng) - mercatorX(west)) / (mercatorX(east) - mercatorX(west)) * coverage.cols
      const row = (mercatorY(lat) - mercatorY(north)) / (mercatorY(south) - mercatorY(north)) * coverage.rows
      const cell = coverage.toCell(x, y)
      expect(Math.abs(cell[0] - col)).toBeLessThan(0.01)
      expect(Math.abs(cell[1] - row)).toBeLessThan(0.01)
    }
    // North is up: a point further north is in a lower row.
    expect(coverage.toCell(0, 100)[1]).toBeLessThan(coverage.toCell(0, -100)[1])
    expect(coverage.cols).toBeLessThanOrEqual(512)
    expect(coverage.rows).toBeLessThanOrEqual(512)
  })

  it('stays accurate over the largest plan the planner accepts', () => {
    // 20 km lines at 60° north: the local frame and the map projection diverge most here.
    const m = syntheticMission({ lines: 6, lineLength: 20000, spacing: 3000, photoSpacing: 2000, altitude: 1500, origin: [15, 60] })
    const trajectory = buildTrajectory(m)
    const coverage = buildCoverage(trajectory, cameraModel(m))
    const frame = LocalFrame.ofMission(m)
    const { west, south, east, north } = coverage.bounds
    let worst = 0
    for (let i = 0; i < trajectory.photos.count; i++) {
      const [lng, lat] = frame.inverse(trajectory.photos.x[i], trajectory.photos.y[i])
      const col = (mercatorX(lng) - mercatorX(west)) / (mercatorX(east) - mercatorX(west)) * coverage.cols
      const row = (mercatorY(lat) - mercatorY(north)) / (mercatorY(south) - mercatorY(north)) * coverage.rows
      const cell = coverage.toCell(trajectory.photos.x[i], trajectory.photos.y[i])
      worst = Math.max(worst, Math.hypot(cell[0] - col, cell[1] - row))
    }
    expect(worst).toBeLessThan(0.05)                               // a twentieth of a cell
  })

  it('shows less overlap over high ground when flying at a constant altitude', () => {
    const { coverage } = sim('area_slope')
    const s = coverage.stats
    // The takeoff point is at the low end of the slope: the ground rises 40 m under a level flight.
    expect(s.heightMin).toBeLessThan(65)
    expect(s.heightMax).toBeGreaterThan(95)
    expect(s.lowestFront).toBeLessThan(s.plannedFront - 2)
    expect(s.lowestSide).toBeLessThan(s.plannedSide - 2)
    // The estimate matches the planner's own worst-case overlap.
    const t = missions.area_slope.terrain
    expect(s.lowestSide).toBeCloseTo(t.side_overlap_min, 0)
    expect(s.lowestFront).toBeCloseTo(t.front_overlap_min, 0)
  })

  it('covers a corridor and scopes the figures to it', () => {
    const { coverage } = sim('corridor')
    expect(coverage.scoped).toBe(true)
    expect(coverage.stats.coveredPercent).toBeGreaterThan(99)
    // Photos reach beyond the corridor; cells outside it are not counted.
    let outside = 0
    for (let i = 0; i < coverage.count.length; i++) if (coverage.count[i] && !coverage.target[i]) outside++
    expect(outside).toBeGreaterThan(0)
  })

  it('finds a gap when the lines are too far apart for the footprint', () => {
    // Lines 300 m apart under a 144 m wide footprint: strips, with bare ground between.
    const m = syntheticMission({ lines: 4, spacing: 300, lineLength: 600 })
    const coverage = buildCoverage(buildTrajectory(m), cameraModel(m))
    expect(coverage.stats.coveredPercent).toBeLessThan(60)
    expect(coverage.stats.coveredPercent).toBeGreaterThan(30)
    expect(coverage.stats.histogram[0]).toBeGreaterThan(40)        // the uncovered share
    expect(coverage.stats.histogram.reduce((a, b) => a + b, 0)).toBeCloseTo(100, 6)
  })

  it('describes the photos themselves when the mission has no target polygon', () => {
    const m = syntheticMission({ lines: 3 })
    m.coverage = null
    const coverage = buildCoverage(buildTrajectory(m), cameraModel(m))
    expect(coverage.scoped).toBe(false)
    expect(coverage.stats.coveredPercent).toBe(100)
  })

  it('uses the terrain: lower ground gets a wider footprint', () => {
    const m = syntheticMission({ lines: 1, lineLength: 600, photoSpacing: 600 })      // two photos, 600 m apart
    const grid = syntheticTerrain(m, x => 500 + (x > 300 ? 50 : 0))                    // the far end is 50 m higher
    const trajectory = buildTrajectory(m, { terrain: createTerrain(grid) })
    const coverage = buildCoverage(trajectory, cameraModel(m))
    const cells = k => coverage.first.reduce((n, v) => n + (v === k ? 1 : 0), 0)
    expect(coverage.heights.agl[0]).toBeCloseTo(100, 0)
    expect(coverage.heights.agl[1]).toBeCloseTo(50, 0)
    expect(cells(0) / cells(1)).toBeCloseTo(4, 0)                  // twice the height, four times the area
  })

  it('is null when there is nothing to cover', () => {
    const { trajectory } = sim('area_flat')
    expect(buildCoverage(trajectory, null)).toBeNull()
    expect(buildCoverage(null, cameraModel(missions.area_flat))).toBeNull()
  })

  it('paints progress and overlap into canvas pixels', () => {
    const { trajectory, coverage } = sim('area_flat')
    const pixels = new Uint8ClampedArray(coverage.cols * coverage.rows * 4)
    const alpha = () => { let n = 0; for (let i = 3; i < pixels.length; i += 4) if (pixels[i]) n++; return n }

    expect(coverage.paint(pixels, 0)).toBe(true)
    expect(alpha()).toBe(0)                                        // nothing photographed yet
    expect(coverage.paint(pixels, 0)).toBe(false)                  // same request: untouched

    coverage.paint(pixels, 10)
    const some = alpha()
    expect(some).toBeGreaterThan(0)
    const first = pixels.findIndex((v, i) => i % 4 === 3 && v > 0) - 3
    expect(Array.from(pixels.slice(first, first + 4))).toEqual([...COVERED_RGBA])

    coverage.paint(pixels, trajectory.photos.count)
    const all = alpha()
    expect(all).toBeGreaterThan(some)
    let covered = 0
    for (let i = 0; i < coverage.count.length; i++) if (coverage.count[i]) covered++
    expect(all).toBe(covered)

    // Overlap view: the same cells, coloured by how many photos see them.
    expect(coverage.paint(pixels, trajectory.photos.count, 'overlap')).toBe(true)
    expect(alpha()).toBe(covered)
    const colours = new Set()
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3]) colours.add(pixels.slice(i, i + 4).join(','))
    expect(colours.size).toBeGreaterThan(2)
    const palette = new Set(OVERLAP_CLASSES.map(c => c.rgba.join(',')))
    for (const c of colours) expect(palette.has(c)).toBe(true)

    // Scrubbing back removes what had not been photographed yet.
    coverage.paint(pixels, 10, 'covered')
    expect(alpha()).toBe(some)
  })
})
