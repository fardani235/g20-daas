import { describe, it, expect } from 'vitest'
import fixture from '../__fixtures__/flightsim.json'
import { LocalFrame, angleDiff, bearingDeg, latOfMercatorY, lerpAngle, mercatorX, mercatorY, normalizeLng } from './frame'
import { createTerrain, isSurfaceModel, terrainLabel } from './terrain'

// The simulator moves the aircraft in the planner's local frame and reads the
// ground from the planner's grid. Both are ports of Python code; the
// reference values in the fixture were produced by the originals.

describe('LocalFrame (port of flightplan/geo.py)', () => {
  it('projects like the planner at every latitude, to a tenth of a millimetre', () => {
    expect(fixture.refs.frame.length).toBeGreaterThan(30)
    for (const [lat0, lng0, lng, lat, x, y] of fixture.refs.frame) {
      const frame = new LocalFrame(lat0, lng0)
      const [fx, fy] = frame.forward(lng, lat)
      expect(Math.abs(fx - x)).toBeLessThan(1e-4)
      expect(Math.abs(fy - y)).toBeLessThan(1e-4)
    }
  })

  it('inverts like the planner', () => {
    for (const [lat0, lng0, lng, lat, x, y] of fixture.refs.frame) {
      const frame = new LocalFrame(lat0, lng0)
      const [ilng, ilat] = frame.inverse(x, y)
      // 1e-9 degree is a tenth of a millimetre. The planner's series round-trips to about a millimetre.
      expect(Math.abs(normalizeLng(ilng - lng))).toBeLessThan(2e-8)
      expect(Math.abs(ilat - lat)).toBeLessThan(2e-8)
    }
  })

  it('round-trips 30 km from the origin to a millimetre', () => {
    const frame = new LocalFrame(-6.82, 107.61)
    for (const [x, y] of [[0, 0], [30000, -30000], [-12345.6, 2222.2], [1, 1]]) {
      const [lng, lat] = frame.inverse(x, y)
      const [bx, by] = frame.forward(lng, lat)
      expect(Math.hypot(bx - x, by - y)).toBeLessThan(1e-3)
    }
  })

  it('keeps true distances: a metre east is a metre', () => {
    const frame = new LocalFrame(52, 5)
    const [lng, lat] = frame.inverse(1000, 0)
    // Great-circle check, independent of the projection.
    const R = 6378137 / Math.sqrt(1 - 0.00669437999014 * Math.sin(52 * Math.PI / 180) ** 2)
    const metres = (lng - 5) * Math.PI / 180 * R * Math.cos(lat * Math.PI / 180)
    expect(metres).toBeCloseTo(1000, 1)
  })

  it('takes its origin from the mission and refuses a mission without one', () => {
    const m = fixture.missions.area_flat
    const frame = LocalFrame.ofMission(m)
    expect([frame.lat0, frame.lng0]).toEqual([m.frame.lat0, m.frame.lng0])
    expect(() => LocalFrame.ofMission({})).toThrow(TypeError)
    expect(() => new LocalFrame(89.5, 0)).toThrow(RangeError)
  })

  it('works across the antimeridian', () => {
    const frame = new LocalFrame(35, 179.99)
    const [lng, lat] = frame.inverse(5000, 0)
    expect(lng).toBeLessThan(-179.9)
    const [x, y] = frame.forward(lng, lat)
    expect(x).toBeCloseTo(5000, 3)
    expect(y).toBeCloseTo(0, 3)
  })
})

describe('angles', () => {
  it('bearing is compass degrees of a local vector', () => {
    expect(bearingDeg(0, 1)).toBe(0)
    expect(bearingDeg(1, 0)).toBe(90)
    expect(bearingDeg(0, -1)).toBe(180)
    expect(bearingDeg(-1, 0)).toBe(270)
  })

  it('differences and interpolation take the short way round', () => {
    expect(angleDiff(350, 10)).toBe(20)
    expect(angleDiff(10, 350)).toBe(-20)
    expect(angleDiff(0, 180)).toBe(180)
    expect(angleDiff(90, 90)).toBe(0)
    expect(lerpAngle(350, 10, 0.5)).toBe(0)
    expect(lerpAngle(10, 350, 0.25)).toBe(5)
    expect(lerpAngle(90, 270, 1)).toBe(270)
  })
})

describe('web mercator', () => {
  it('is the map projection: 0..1 across the world, equator in the middle', () => {
    expect(mercatorX(-180)).toBe(0)
    expect(mercatorX(180)).toBe(1)
    expect(mercatorY(0)).toBeCloseTo(0.5, 12)
    expect(mercatorY(85.0511287798066)).toBeCloseTo(0, 9)
    expect(mercatorY(60)).toBeLessThan(mercatorY(-60))
    for (const lat of [-70, -6.82, 0, 45, 80]) expect(latOfMercatorY(mercatorY(lat))).toBeCloseTo(lat, 9)
  })
})

describe('terrain sampler (port of TerrainModel.elevation)', () => {
  const grid = values => ({ south: 0, west: 0, north: 1, east: 1, rows: values.length, cols: values[0].length, values })

  it('interpolates bilinearly', () => {
    const t = createTerrain(grid([[0, 10], [20, 30]]))          // south row first
    expect(t.elevation(0, 0)).toBe(0)
    expect(t.elevation(1, 0)).toBe(10)
    expect(t.elevation(0, 1)).toBe(20)
    expect(t.elevation(1, 1)).toBe(30)
    expect(t.elevation(0.5, 0.5)).toBeCloseTo(15)
    expect(t.elevation(0.25, 0)).toBeCloseTo(2.5)
  })

  it('does not know the ground outside the grid', () => {
    const t = createTerrain(grid([[0, 10], [20, 30]]))
    expect(t.elevation(1.5, 0.5)).toBeNull()
    expect(t.elevation(0.5, -0.1)).toBeNull()
  })

  it('skips missing nodes and reports how much is known', () => {
    const t = createTerrain(grid([[null, 10], [20, 30]]))
    expect(t.coverage).toBeCloseTo(0.75)
    expect(t.elevation(0.5, 0.5)).toBeCloseTo(20)
    expect(t.elevation(0, 0)).not.toBeNull()                    // on the missing corner: its neighbours
    expect([t.min, t.max]).toEqual([10, 30])
    const empty = createTerrain(grid([[null, null], [null, null]]))
    expect(empty.elevation(0.5, 0.5)).toBeNull()
    expect(empty.min).toBeNull()
  })

  it('agrees with the planner on real grids, holes and edges included', () => {
    for (const [name, samples] of Object.entries(fixture.refs.terrain_samples)) {
      const t = createTerrain(fixture.terrain[name])
      let unknown = 0
      for (const [lng, lat, expected] of samples) {
        const got = t.elevation(lng, lat)
        if (expected === null) { expect(got).toBeNull(); unknown++ } else {
          // The payload rounds nodes to a centimetre.
          expect(Math.abs(got - expected)).toBeLessThan(0.011)
        }
      }
      expect(unknown).toBeGreaterThan(0)                         // points outside the grid are in the sample
    }
  })

  it('rejects a grid that is not one', () => {
    expect(createTerrain(null)).toBeNull()
    expect(createTerrain({ rows: 1, cols: 2, values: [[1, 2]] })).toBeNull()
    expect(createTerrain({ south: 0, west: 0, north: 1, east: 1, rows: 2, cols: 2, values: [[1, 2]] })).toBeNull()
    expect(createTerrain({ south: 0, west: 0, north: 1, east: 1, rows: 2, cols: 2, values: [[1, 2], [3]] })).toBeNull()
    expect(createTerrain({ south: 1, west: 0, north: 1, east: 1, rows: 2, cols: 2, values: [[1, 2], [3, 4]] })).toBeNull()
  })

  it('says what the ground is, and when there is none', () => {
    const t = createTerrain(fixture.terrain.area_terrain)
    expect(terrainLabel(t)).toBe('Test DEM, about 30 m resolution, ±5 m nominal')
    expect(terrainLabel(null)).toMatch(/flat ground .* assumed/)
    expect(isSurfaceModel(t)).toBe(false)
    expect(isSurfaceModel({ source: { surface: 'surface' } })).toBe(true)
  })
})
