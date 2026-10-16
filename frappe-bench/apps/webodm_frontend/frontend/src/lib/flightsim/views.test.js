import { describe, it, expect, vi } from 'vitest'
import fixture from '../__fixtures__/flightsim.json'
import { syntheticMission } from '../__fixtures__/syntheticMission'
import {
  CAMERA_MODES, aircraftPosition, aircraftScale, defaultTopHeight, followOffset, gimbalFraming, headingAxes, isAttached,
  isUserControlled, rigPose,
} from './cameraRig'
import { LocalFrame, latOfMercatorY, mercatorX, mercatorY } from './frame'
import { TILE_SIZE, drawTiles, orthoTemplate, planTiles, tilePlan, tileUrl } from './imagery'
import { createSimulation } from './index'
import { CHUNK, createSimMapLayer, createTrack } from './mapLayer'
import {
  PATH_COLORS, drapedRings, flownPieces, gridUV, groundGrid, groundHeightFn, groundMesh, pathPieces, photoPoints,
  sceneBounds, skirtMesh, waypointPoints,
} from './sceneGeometry'
import { PHASE, isMove } from './trajectory'

// What the two views draw, checked without a browser: the geometry handed to
// three.js, the camera poses, the imagery mosaic, and the Leaflet layer on a
// stand-in map. Both views are fed the same sampled state; these tests pin
// that each one puts it in the right place.

const { missions } = fixture
const simulate = name => createSimulation(missions[name], fixture.terrain[name] || null)

describe('3D ground', () => {
  it('is the planner\'s elevation grid, in metres, with the takeoff ground at height zero', () => {
    const sim = simulate('area_terrain')
    const grid = groundGrid(sim)
    const t = sim.terrain
    expect([grid.rows, grid.cols, grid.flat]).toEqual([t.rows, t.cols, false])
    expect(grid.base).toBe(sim.trajectory.takeoff.ground)
    // Every node is where the planner's frame puts it and as high as the grid says.
    const frame = LocalFrame.ofMission(missions.area_terrain)
    for (const [r, c] of [[0, 0], [5, 17], [t.rows - 1, t.cols - 1], [20, 20]]) {
      const i = r * t.cols + c
      const lng = t.west + (t.east - t.west) * c / (t.cols - 1)
      const lat = t.south + (t.north - t.south) * r / (t.rows - 1)
      const [x, y] = frame.forward(lng, lat)
      expect(grid.x[i]).toBeCloseTo(x, 6)
      expect(grid.y[i]).toBeCloseTo(y, 6)
      expect(grid.up[i]).toBeCloseTo(t.node(r, c) - grid.base, 4)
    }
    // The ridge stands about 60 m above the takeoff point; nothing is exaggerated.
    expect(grid.upMax).toBeGreaterThan(50)
    expect(grid.upMax).toBeLessThan(61)
    expect(grid.upMin).toBeCloseTo(0, 1)
    const heightAt = groundHeightFn(sim, grid)
    expect(heightAt(sim.trajectory.takeoff.x, sim.trajectory.takeoff.y)).toBeCloseTo(0, 6)
    expect(heightAt(0, 0)).toBeCloseTo(sim.trajectory.groundAt(0, 0) - 700, 6)
  })

  it('the aircraft sits on the drawn ground when it stands on the takeoff point, and at its height above ground in flight', () => {
    const sim = simulate('area_terrain')
    const heightAt = groundHeightFn(sim, groundGrid(sim))
    const start = sim.sample(0)
    expect(aircraftPosition(start)[1] - heightAt(start.x, start.y)).toBeCloseTo(0, 6)
    const tr = sim.trajectory
    for (const leg of [0, 3, 7]) {
      const s = sim.sample((tr.legTimes.t0[leg] + tr.legTimes.t1[leg]) / 2)
      // Scene height of the aircraft minus scene height of the ground below it is the state's AGL.
      expect(aircraftPosition(s)[1] - heightAt(s.x, s.y)).toBeCloseTo(s.agl, 6)
    }
  })

  it('fills holes in the drawing only: the simulation still does not know that ground', () => {
    const sim = simulate('area_slope')
    const grid = groundGrid(sim)
    expect(sim.terrain.coverage).toBeLessThan(1)
    expect(Array.from(grid.up).every(Number.isFinite)).toBe(true)
    expect(sim.trajectory.groundAt(300, 250)).toBeNull()
    expect(groundHeightFn(sim, grid)(300, 250)).toBe(0)                // drawn at the takeoff level, not invented
  })

  it('is a flat sheet under the whole flight when there is no elevation data', () => {
    const sim = simulate('area_flat')
    const grid = groundGrid(sim)
    expect(grid.flat).toBe(true)
    expect(grid.base).toBeNull()
    expect(Array.from(grid.up).every(v => v === 0)).toBe(true)
    const seg = sim.trajectory.segments
    for (let i = 0; i < seg.count; i++) {
      expect(seg.x0[i]).toBeGreaterThan(grid.extent.xmin + 150)
      expect(seg.x0[i]).toBeLessThan(grid.extent.xmax - 150)
      expect(seg.y0[i]).toBeGreaterThan(grid.extent.ymin + 150)
      expect(seg.y0[i]).toBeLessThan(grid.extent.ymax - 150)
    }
    // Node coordinates and geography agree.
    const frame = LocalFrame.ofMission(missions.area_flat)
    const [lng, lat] = frame.inverse(grid.x[40], grid.y[40])
    expect(grid.lng[40]).toBeCloseTo(lng, 9)
    expect(grid.lat[40]).toBeCloseTo(lat, 9)
    expect(groundHeightFn(sim, grid)(12, 34)).toBe(0)
  })

  it('meshes with two upward-facing triangles per cell, in scene axes (x east, y up, z south)', () => {
    for (const name of ['area_terrain', 'area_flat']) {
      const grid = groundGrid(simulate(name))
      const { positions, indices } = groundMesh(grid)
      expect(positions.length).toBe(grid.rows * grid.cols * 3)
      expect(indices.length).toBe((grid.rows - 1) * (grid.cols - 1) * 6)
      expect(Math.max(...indices)).toBe(grid.rows * grid.cols - 1)
      // Node 1 is east of node 0; a node one row up is north, i.e. smaller z.
      expect(positions[3]).toBeGreaterThan(positions[0])
      expect(positions[3 * grid.cols + 2]).toBeLessThan(positions[2])
      expect(positions[1]).toBeCloseTo(grid.up[0], 4)
      let down = 0
      for (let k = 0; k < indices.length; k += 3) {
        const [a, b, c] = [indices[k], indices[k + 1], indices[k + 2]].map(i => positions.subarray(3 * i, 3 * i + 3))
        const normalY = (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2])
        if (!(normalY > 0)) down++
      }
      expect(down).toBe(0)
    }
  })

  it('maps imagery by geography: each node samples the mosaic at its own longitude and latitude', () => {
    const sim = simulate('area_terrain')
    const grid = groundGrid(sim)
    const plan = tilePlan(grid.bounds, { maxTiles: 64 })
    const uv = gridUV(grid, plan.box)
    for (let i = 0; i < grid.x.length; i += 97) {
      expect(uv[2 * i]).toBeGreaterThanOrEqual(0)
      expect(uv[2 * i]).toBeLessThanOrEqual(1)
      expect(uv[2 * i + 1]).toBeGreaterThanOrEqual(0)
      expect(uv[2 * i + 1]).toBeLessThanOrEqual(1)
      // The mosaic pixel under the node, computed from tile arithmetic.
      const n = 2 ** plan.z
      const px = (mercatorX(grid.lng[i]) * n - plan.x0) * TILE_SIZE
      const py = (mercatorY(grid.lat[i]) * n - plan.y0) * TILE_SIZE
      expect(uv[2 * i] * plan.width).toBeCloseTo(px, 3)
      expect((1 - uv[2 * i + 1]) * plan.height).toBeCloseTo(py, 3)
    }
    // East is to the right, north is up.
    expect(uv[2]).toBeGreaterThan(uv[0])
    expect(uv[2 * grid.cols + 1]).toBeGreaterThan(uv[1])
  })

  it('maps the coverage raster onto the same ground the 2D map lays it on', () => {
    const sim = simulate('area_terrain')
    const grid = groundGrid(sim)
    const uv = gridUV(grid, sim.coverage.bounds)
    for (let i = 0; i < grid.x.length; i += 131) {
      const cell = sim.coverage.toCell(grid.x[i], grid.y[i])
      expect(Math.abs(uv[2 * i] * sim.coverage.cols - cell[0])).toBeLessThan(0.02)
      expect(Math.abs((1 - uv[2 * i + 1]) * sim.coverage.rows - cell[1])).toBeLessThan(0.02)
    }
  })

  it('closes the block with a skirt around the edge', () => {
    const grid = groundGrid(simulate('area_terrain'))
    const { positions, indices } = skirtMesh(grid, -40)
    const ring = 2 * (grid.rows + grid.cols) - 4
    expect(positions.length).toBe(ring * 2 * 3)
    expect(indices.length).toBe(ring * 6)
    for (let k = 0; k < ring; k++) {
      expect(positions[6 * k + 4]).toBe(-40)                           // the bottom of the wall
      expect(positions[6 * k]).toBe(positions[6 * k + 3])              // straight below its top
      expect(positions[6 * k + 2]).toBe(positions[6 * k + 5])
    }
    expect(positions[1]).toBeCloseTo(grid.up[0], 4)
  })

  it('frames the terrain and the flight', () => {
    const sim = simulate('area_terrain')
    const grid = groundGrid(sim)
    const b = sceneBounds(grid, sim.trajectory)
    expect(b.top).toBeCloseTo(missions.area_terrain.stats.max_altitude_m, 1)
    expect(b.bottom).toBe(grid.upMin)
    expect(b.radius).toBeGreaterThan(600)
    const s = sim.sample(sim.duration / 2)
    const [x, y, z] = aircraftPosition(s)
    expect(Math.hypot(x - b.center[0], y - b.center[1], z - b.center[2])).toBeLessThan(b.radius)
  })
})

describe('3D path, photos and boundary', () => {
  it('draws one piece per moving piece of the timeline, coloured by what the aircraft is doing', () => {
    const sim = simulate('area_flat')
    const tr = sim.trajectory
    const pieces = pathPieces(tr)
    const seg = tr.segments
    let moves = 0
    for (let i = 0; i < seg.count; i++) if (isMove(seg.kind[i])) moves++
    expect(pieces.count).toBe(moves)
    expect(pieces.positions.length).toBe(moves * 6)
    expect(pieces.before[seg.count]).toBe(moves)
    for (let i = 0; i < seg.count; i++) {
      if (!isMove(seg.kind[i])) { expect(pieces.before[i + 1]).toBe(pieces.before[i]); continue }
      const k = pieces.before[i] * 6
      expect(Array.from(pieces.positions.subarray(k, k + 6))).toEqual(
        [seg.x0[i], seg.z0[i], -seg.y0[i], seg.x1[i], seg.z1[i], -seg.y1[i]].map(Math.fround))
      const expected = seg.phase[i] === PHASE.SURVEY ? PATH_COLORS.survey
        : seg.phase[i] === PHASE.TURN ? PATH_COLORS.turn
          : (seg.phase[i] === PHASE.TAKEOFF || seg.phase[i] === PHASE.LANDING) ? PATH_COLORS.vertical : PATH_COLORS.transit
      expect(Array.from(pieces.colors.subarray(k, k + 3))).toEqual(expected.map(Math.fround))
    }
  })

  it('"flown so far" is a prefix of the path: whole pieces behind, the one in progress up to the aircraft', () => {
    const sim = simulate('area_flat')
    const tr = sim.trajectory
    const pieces = pathPieces(tr)
    const start = flownPieces(pieces, tr, sim.sample(0))
    expect(start).toEqual({ whole: 0, partial: -1 })                    // standing on the ground
    const mid = sim.sample((tr.legTimes.t0[2] + tr.legTimes.t1[2]) / 2)
    const flown = flownPieces(pieces, tr, mid)
    expect(flown.partial).toBe(mid.index)
    expect(flown.whole).toBe(pieces.before[mid.index])
    expect(flown.whole).toBeGreaterThan(0)
    const end = flownPieces(pieces, tr, sim.sample(sim.duration))
    expect(end.whole).toBe(pieces.count)
    // It only grows as time goes on.
    let prev = 0
    for (let t = 0; t <= sim.duration; t += 5) {
      const f = flownPieces(pieces, tr, sim.sample(t))
      expect(f.whole).toBeGreaterThanOrEqual(prev)
      prev = f.whole
    }
  })

  it('places a point for every photo at the height it is taken, and for the ends of the lines', () => {
    const sim = simulate('area_terrain')
    const tr = sim.trajectory
    const pts = photoPoints(tr)
    expect(pts.length).toBe(tr.photos.count * 3)
    expect(Array.from(pts.subarray(30, 33))).toEqual([tr.photos.x[10], tr.photos.alt[10], -tr.photos.y[10]].map(Math.fround))
    const ends = waypointPoints(tr)
    expect(ends.length).toBe(missions.area_terrain.legs.length * 2 * 3)
    const capped = waypointPoints(createSimulation(syntheticMission({ lines: 300 }), null).trajectory, { max: 100 })
    expect(capped.length / 3).toBeLessThanOrEqual(101)
  })

  it('lays the survey boundary on the terrain', () => {
    const sim = simulate('area_terrain')
    const heightAt = groundHeightFn(sim, groundGrid(sim))
    const rings = drapedRings(missions.area_terrain, sim.trajectory.frame, heightAt, { lift: 1, step: 20 })
    expect(rings.length).toBe(1)
    const ring = rings[0]
    expect(Array.from(ring.subarray(0, 3))).toEqual(Array.from(ring.subarray(ring.length - 3)))     // closed
    let highest = -Infinity
    for (let k = 0; k < ring.length; k += 3) {
      expect(ring[k + 1]).toBeCloseTo(heightAt(ring[k], -ring[k + 2]) + 1, 3)
      highest = Math.max(highest, ring[k + 1])
    }
    expect(highest).toBeGreaterThan(50)                                 // it climbs over the ridge
    expect(ring.length / 3).toBeGreaterThan(80)                         // densified, not four corners
    expect(drapedRings({ coverage: null }, sim.trajectory.frame, heightAt)).toEqual([])
  })
})

describe('camera rig', () => {
  const sim = simulate('area_flat')
  const tr = sim.trajectory
  const east = sim.sample((tr.legTimes.t0[0] + tr.legTimes.t1[0]) / 2)        // flying east at 100 m

  it('knows which modes follow the aircraft and which the user steers', () => {
    expect(CAMERA_MODES.map(m => m.id)).toEqual(['follow', 'free', 'top', 'fpv', 'gimbal'])
    expect(['top', 'fpv', 'gimbal'].every(isAttached)).toBe(true)
    expect(['follow', 'free'].some(isAttached)).toBe(false)
    expect(['follow', 'free'].every(isUserControlled)).toBe(true)
  })

  it('heading axes: north is -z, east is +x', () => {
    const n = headingAxes(0)
    expect(n.forward.map(v => Math.round(v * 1e9) / 1e9 + 0)).toEqual([0, 0, -1])
    expect(n.right.map(v => Math.round(v * 1e9) / 1e9 + 0)).toEqual([1, 0, 0])
    const e = headingAxes(90)
    expect(e.forward[0]).toBeCloseTo(1, 9)
    expect(e.right[2]).toBeCloseTo(1, 9)                                // south is to the right of east
  })

  it('top-down: straight above the aircraft, looking down, north up', () => {
    expect(east.heading).toBeCloseTo(90, 3)
    const pose = rigPose('top', east, sim.camera, { topHeight: 400 })
    const p = aircraftPosition(east)
    expect(pose.position).toEqual([p[0], p[1] + 400, p[2]])
    expect(pose.target).toEqual(p)
    expect(pose.up).toEqual([0, 0, -1])
  })

  it('through the survey camera: straight down for a nadir plan, the top of the image ahead', () => {
    const pose = rigPose('gimbal', east, sim.camera, { viewAspect: 1.5 })
    const dir = pose.target.map((v, i) => v - pose.position[i])
    expect(dir[0]).toBeCloseTo(0, 9)
    expect(dir[1]).toBeCloseTo(-1, 9)
    expect(dir[2]).toBeCloseTo(0, 9)
    expect(pose.up[0]).toBeCloseTo(1, 9)                                // image-up = the direction of flight (east)
    expect(pose.up[1]).toBeCloseTo(0, 9)
    expect(pose.position[1]).toBeCloseTo(east.alt - 0.2, 9)
  })

  it('the camera view shows exactly one photo: its frame fits the viewport, at the camera\'s real field of view', () => {
    const wide = gimbalFraming(sim.camera, 3, 1)                          // a very wide pane: height limits
    expect(wide.height).toBeCloseTo(1, 9)
    expect(wide.width).toBeLessThan(1)
    expect(wide.fov).toBeCloseTo(sim.camera.fovAlongDeg, 9)
    const tall = gimbalFraming(sim.camera, 0.5, 1)                        // a tall pane: width limits
    expect(tall.width).toBeCloseTo(1, 9)
    expect(tall.height).toBeLessThan(1)
    // The frame keeps the photo's proportions on screen.
    for (const aspect of [0.5, 1, 1.6, 3]) {
      const f = gimbalFraming(sim.camera, aspect)
      expect((f.width * aspect) / f.height).toBeCloseTo(sim.camera.aspect, 9)
      expect(Math.max(f.width, f.height)).toBeCloseTo(1 / 1.08, 9)       // with a margin around it
    }
  })

  it('an oblique camera looks ahead and down', () => {
    const oblique = { ...sim.camera, pitch: -45 }
    const pose = rigPose('gimbal', east, oblique)
    const dir = pose.target.map((v, i) => v - pose.position[i])
    expect(dir[0]).toBeCloseTo(Math.SQRT1_2, 9)
    expect(dir[1]).toBeCloseTo(-Math.SQRT1_2, 9)
    expect(pose.up[1]).toBeGreaterThan(0)
  })

  it('FPV: from the nose, along the heading, the horizon rolling with the aircraft', () => {
    const level = rigPose('fpv', { ...east, roll: 0 }, sim.camera)
    const dir = level.target.map((v, i) => v - level.position[i])
    expect(dir[0]).toBeGreaterThan(0.95)                                 // east
    expect(dir[1]).toBeLessThan(0)                                       // a little below level
    expect(dir[1]).toBeGreaterThan(-0.3)
    expect(level.up[1]).toBeGreaterThan(0.95)
    expect(level.up[2]).toBeCloseTo(0, 9)
    const banked = rigPose('fpv', { ...east, roll: 20 }, sim.camera)
    expect(banked.up[2]).toBeCloseTo(Math.sin(20 * Math.PI / 180), 6)    // up tilts to the right (south when flying east)
    expect(level.position[0]).toBeGreaterThan(east.x)                    // ahead of the centre
  })

  it('poses depend only on the state: the same moment gives the same camera', () => {
    for (const mode of ['top', 'fpv', 'gimbal']) {
      const a = rigPose(mode, sim.sample(123.4), sim.camera)
      sim.sample(999)
      const b = rigPose(mode, sim.sample(123.4), sim.camera)
      expect(a).toEqual(b)
    }
  })

  it('starts the follow camera behind and above the aircraft', () => {
    const o = followOffset(90, 1000)
    expect(o[0]).toBeLessThan(0)                                         // west of an east-bound aircraft
    expect(o[1]).toBeGreaterThan(0)
    expect(Math.hypot(o[0], o[2])).toBeCloseTo(350, 6)
    expect(Math.hypot(...followOffset(0, 10).slice(0, 3))).toBeGreaterThan(80)   // never on top of it
  })

  it('keeps the aircraft model visible without pretending it is that big', () => {
    expect(aircraftScale(5)).toBe(1)                                     // life size up close
    expect(aircraftScale(1000)).toBeCloseTo(45, 6)
    expect(aircraftScale(1e7)).toBe(900)
    expect(defaultTopHeight(east, 144)).toBeGreaterThanOrEqual(144 * 3)
    expect(defaultTopHeight(null)).toBe(250)
  })
})

describe('imagery mosaic', () => {
  const bounds = { west: 107.604, south: -6.826, east: 107.616, north: -6.814 }

  it('covers the ground with whole tiles at the deepest zoom the budget allows', () => {
    const plan = tilePlan(bounds, { maxTiles: 36 })
    expect(plan.nx * plan.ny).toBeLessThanOrEqual(36)
    expect([plan.width, plan.height]).toEqual([plan.nx * 256, plan.ny * 256])
    expect(plan.box.west).toBeLessThanOrEqual(bounds.west)
    expect(plan.box.east).toBeGreaterThanOrEqual(bounds.east)
    expect(plan.box.north).toBeGreaterThanOrEqual(bounds.north)
    expect(plan.box.south).toBeLessThanOrEqual(bounds.south)
    // One zoom deeper would not fit.
    const n = 2 ** (plan.z + 1)
    const nx = Math.floor(mercatorX(bounds.east) * n) - Math.floor(mercatorX(bounds.west) * n) + 1
    const ny = Math.floor(mercatorY(bounds.south) * n) - Math.floor(mercatorY(bounds.north) * n) + 1
    expect(nx * ny).toBeGreaterThan(36)
    // The box is the tiles' own extent.
    expect(plan.box.west).toBeCloseTo(plan.x0 / 2 ** plan.z * 360 - 180, 12)
    expect(plan.box.north).toBeCloseTo(latOfMercatorY(plan.y0 / 2 ** plan.z), 12)
  })

  it('respects the basemap\'s deepest zoom and a larger budget', () => {
    const tiny = { west: 107.61, south: -6.8201, east: 107.6101, north: -6.82 }
    expect(tilePlan(tiny, { maxZoom: 19 }).z).toBe(19)
    expect(tilePlan(tiny, { maxZoom: 15 }).z).toBe(15)
    expect(tilePlan(bounds, { maxTiles: 64 }).z).toBeGreaterThanOrEqual(tilePlan(bounds, { maxTiles: 9 }).z)
    const world = tilePlan({ west: -170, south: -60, east: 170, north: 60 }, { maxTiles: 4 })
    expect(world.nx * world.ny).toBeLessThanOrEqual(4)
  })

  it('builds tile URLs for the app\'s basemaps', () => {
    expect(tileUrl('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', 15, 26174, 16963))
      .toMatch(/^https:\/\/[abc]\.tile\.openstreetmap\.org\/15\/26174\/16963\.png$/)
    expect(tileUrl('https://server.arcgisonline.com/x/tile/{z}/{y}/{x}', 15, 26174, 16963))
      .toBe('https://server.arcgisonline.com/x/tile/15/16963/26174')
    expect(tileUrl('/t/{z}/{x}/{y}', 2, 5, 1)).toBe('/t/2/1/1')         // wraps around the world
    expect(orthoTemplate('a b')).toBe('/api/method/webodm_core.api.tiles.serve?task_name=a%20b&dataset=orthophoto&z={z}&x={x}&y={y}')
  })

  it('lists the tiles row by row with their place in the mosaic', () => {
    const plan = tilePlan(bounds, { maxTiles: 36 })
    const tiles = planTiles(plan, '/t/{z}/{x}/{y}')
    expect(tiles.length).toBe(plan.nx * plan.ny)
    expect(tiles[0]).toEqual({ x: plan.x0, y: plan.y0, px: 0, py: 0, url: `/t/${plan.z}/${plan.x0}/${plan.y0}` })
    expect(tiles.at(-1).px).toBe(plan.width - 256)
    expect(tiles.at(-1).py).toBe(plan.height - 256)
  })

  // Images that load (or fail) when the test says so.
  function fakeImages(fail = () => false) {
    const made = []
    return {
      made,
      createImage: () => {
        const img = { crossOrigin: undefined, set src(url) { this.url = url; made.push(this) } }
        return img
      },
      settle() { for (const img of made) (fail(img.url) ? img.onerror : img.onload)() },
    }
  }
  const fakeCanvas = () => {
    const drawImage = vi.fn()
    return { drawImage, getContext: () => ({ drawImage }) }
  }

  it('draws tiles where they belong as they arrive, asking for them as CORS images', async () => {
    const plan = tilePlan(bounds, { maxTiles: 9 })
    const images = fakeImages()
    const canvas = fakeCanvas()
    const onTile = vi.fn()
    const result = drawTiles(canvas, plan, '/t/{z}/{x}/{y}', { createImage: images.createImage, onTile })
    expect(images.made.length).toBe(plan.nx * plan.ny)
    expect(images.made.every(i => i.crossOrigin === 'anonymous')).toBe(true)
    images.settle()
    expect(await result).toEqual({ loaded: plan.nx * plan.ny, failed: 0 })
    expect(onTile).toHaveBeenCalledTimes(plan.nx * plan.ny)
    expect(canvas.drawImage.mock.calls[0].slice(1)).toEqual([0, 0, 256, 256])
    const last = canvas.drawImage.mock.calls.at(-1)
    expect(last.slice(1)).toEqual([plan.width - 256, plan.height - 256, 256, 256])
  })

  it('a tile that fails leaves a gap, not an error', async () => {
    const plan = tilePlan(bounds, { maxTiles: 9 })
    const images = fakeImages(url => url.endsWith(String(plan.y0)))       // the first row fails
    const canvas = fakeCanvas()
    const result = drawTiles(canvas, plan, '/t/{z}/{x}/{y}', { createImage: images.createImage })
    images.settle()
    const out = await result
    expect(out.failed).toBe(plan.nx)
    expect(out.loaded).toBe(plan.nx * (plan.ny - 1))
    expect(canvas.drawImage).toHaveBeenCalledTimes(out.loaded)
  })

  it('stops drawing once the view is gone, and copes with a canvas that cannot draw', async () => {
    const plan = tilePlan(bounds, { maxTiles: 4 })
    const images = fakeImages()
    const canvas = fakeCanvas()
    const result = drawTiles(canvas, plan, '/t/{z}/{x}/{y}', { createImage: images.createImage, isCancelled: () => true })
    images.settle()
    expect((await result).loaded).toBe(0)
    expect(canvas.drawImage).not.toHaveBeenCalled()
    expect(await drawTiles({ getContext: () => null }, plan, '/t/{z}/{x}/{y}')).toEqual({ loaded: 0, failed: plan.nx * plan.ny })
    // Same-origin tiles (the project's orthophoto) are requested without CORS mode.
    const plain = fakeImages()
    drawTiles(fakeCanvas(), plan, '/t/{z}/{x}/{y}', { createImage: plain.createImage, crossOrigin: null })
    expect(plain.made.every(i => i.crossOrigin === undefined)).toBe(true)
  })
})

describe('2D map layer', () => {
  // A stand-in for the planner's Leaflet map: it records what is put on it.
  function fakeMap() {
    const layers = new Set()
    const handlers = {}
    const panes = {}
    return {
      layers,
      panes,
      addLayer(l) { layers.add(l); return this },
      removeLayer(l) { layers.delete(l); return this },
      hasLayer: l => layers.has(l),
      getPane: name => panes[name],
      createPane: name => (panes[name] = document.createElement('div')),
      on(event, fn) { handlers[event] = fn },
      off(event) { delete handlers[event] },
      fire: event => handlers[event]?.(),
      panTo: vi.fn(),
      handlers,
    }
  }
  const points = lines => lines.reduce((n, l) => n + l.length, 0)

  it('cuts the flown track into chunks that never change once flown', () => {
    const tr = simulate('fixed_wing').trajectory
    const track = createTrack(tr)
    expect(track.chunks).toBe(Math.ceil(tr.segments.count / CHUNK))
    expect(track.completeBefore(0)).toBe(0)
    expect(track.completeBefore(CHUNK)).toBe(1)
    expect(track.completeBefore(2 * CHUNK + 5)).toBe(2)
    const first = track.chunkLines(0)
    expect(track.chunkLines(0)).toBe(first)                               // cached: built once
    expect(points(first)).toBeGreaterThan(100)
    // The chunk starts where the flight starts and each next chunk continues where the last ended.
    const frame = tr.frame
    const [lng, lat] = frame.inverse(tr.segments.x0[0], tr.segments.y0[0])
    expect(first[0][0][0]).toBeCloseTo(lat, 9)
    expect(first[0][0][1]).toBeCloseTo(lng, 9)
    const second = track.chunkLines(1)
    expect(second[0][0][0]).toBeCloseTo(first.at(-1).at(-1)[0], 9)
    expect(second[0][0][1]).toBeCloseTo(first.at(-1).at(-1)[1], 9)
  })

  it('the chunk in progress ends at the aircraft', () => {
    const sim = simulate('area_flat')
    const track = createTrack(sim.trajectory)
    const tr = sim.trajectory
    const s = sim.sample((tr.legTimes.t0[1] + tr.legTimes.t1[1]) / 2)
    const lines = track.activeLines(s)
    const end = lines.at(-1).at(-1)
    expect(end[0]).toBeCloseTo(s.lat, 9)
    expect(end[1]).toBeCloseTo(s.lng, 9)
    expect(track.activeLines(sim.sample(0))).toEqual([])                  // nothing flown yet
  })

  it('does not draw a line across a jump (the aircraft carried back after landing elsewhere)', () => {
    const sim = createSimulation(syntheticMission({ lines: 4, flights: 2, finish: 'land' }), null)
    const track = createTrack(sim.trajectory)
    const lines = track.activeLines(sim.sample(sim.duration))
    expect(lines.length).toBe(2)                                          // one line per flight
    // No drawn step is longer than the longest thing actually flown.
    for (const line of lines) {
      for (let i = 1; i < line.length; i++) {
        const metres = Math.hypot((line[i][0] - line[i - 1][0]) * 111320, (line[i][1] - line[i - 1][1]) * 110500)
        expect(metres).toBeLessThan(420)
      }
    }
  })

  it('puts the aircraft where the state says, turned to its heading', () => {
    const sim = simulate('area_flat')
    const map = fakeMap()
    const layer = createSimMapLayer(map, sim)
    const tr = sim.trajectory
    const s = sim.sample((tr.legTimes.t0[1] + tr.legTimes.t1[1]) / 2)
    layer.update(s)
    const at = layer.layers.marker.getLatLng()
    expect(at.lat).toBeCloseTo(s.lat, 12)
    expect(at.lng).toBeCloseTo(s.lng, 12)
    expect(layer.layers.arrow.style.transform).toBe(`rotate(${s.heading.toFixed(1)}deg)`)
    expect(layer.layers.arrow.innerHTML).toContain('<svg')
    expect(map.layers.has(layer.layers.group)).toBe(true)
    // The same state at another time moves it.
    layer.update(sim.sample(0))
    expect(layer.layers.marker.getLatLng().lat).toBeCloseTo(missions.area_flat.takeoff.lat, 9)
    expect(layer.layers.arrow.style.opacity).toBe('0.75')                 // dimmed on the ground
  })

  it('draws the camera footprint under the aircraft in flight, and removes it on the ground', () => {
    const sim = simulate('area_flat')
    const layer = createSimMapLayer(fakeMap(), sim)
    const tr = sim.trajectory
    const s = sim.sample((tr.legTimes.t0[0] + tr.legTimes.t1[0]) / 2)
    layer.update(s)
    const { group, footprint } = layer.layers
    expect(group.hasLayer(footprint)).toBe(true)
    const ring = footprint.getLatLngs()[0]
    expect(ring.length).toBe(4)
    const lat = ring.reduce((a, p) => a + p.lat, 0) / 4
    const lng = ring.reduce((a, p) => a + p.lng, 0) / 4
    expect(lat).toBeCloseTo(s.lat, 7)
    expect(lng).toBeCloseTo(s.lng, 7)
    // Its size on the map is the footprint the dashboard reports.
    const across = Math.abs(ring[0].lat - ring[1].lat) * 110574                // flying east: across-track is north-south
    expect(across).toBeCloseTo(s.footprintAcross, -1)
    // It lights up for a moment at each exposure, then settles while the camera stays on.
    const shot = tr.photos.t[5]
    layer.update(sim.sample(shot + 0.1))
    expect(footprint.options.fillOpacity).toBe(0.5)
    layer.update(sim.sample(shot + 0.6))
    expect(footprint.options.fillOpacity).toBe(0.25)
    // Between lines the camera is off: faint and dashed.
    const turn = tr.phases.find(p => p.key === 'turn' && p.t1 - p.t0 > 3 && p.t0 > tr.legTimes.t1[0])
    layer.update(sim.sample((turn.t0 + turn.t1) / 2))
    expect(footprint.options.fillOpacity).toBe(0.06)
    expect(footprint.options.dashArray).toBe('4 4')
    layer.update(sim.sample(0))
    expect(group.hasLayer(footprint)).toBe(false)
    layer.setFootprintVisible(false)
    layer.update(s)
    expect(group.hasLayer(footprint)).toBe(false)
  })

  it('grows the flown track with the flight and takes it back when scrubbing backwards', () => {
    const sim = simulate('fixed_wing')
    const layer = createSimMapLayer(fakeMap(), sim)
    const count = () => layer.layers.group.getLayers().length
    layer.update(sim.sample(0))
    expect(layer.finishedChunks()).toBe(0)
    const base = count()
    layer.update(sim.sample(sim.duration))
    const chunks = Math.floor((sim.trajectory.segments.count - 1) / CHUNK)
    expect(layer.finishedChunks()).toBe(chunks)
    expect(count()).toBe(base + chunks)
    layer.update(sim.sample(sim.duration * 0.4))
    expect(layer.finishedChunks()).toBeLessThan(chunks)
    expect(layer.finishedChunks()).toBeGreaterThan(0)
    layer.update(sim.sample(0))
    expect(layer.finishedChunks()).toBe(0)
    expect(count()).toBe(base)
    expect(layer.layers.active.getLatLngs()).toEqual([])
  })

  it('lays the coverage canvas on the map, georeferenced, and shows or hides it', () => {
    const sim = simulate('area_flat')
    const map = fakeMap()
    const canvas = document.createElement('canvas')
    const layer = createSimMapLayer(map, sim, { canvas })
    expect(map.panes['flightsim-coverage'].style.zIndex).toBe('350')
    const overlay = layer.layers.overlay
    expect(map.hasLayer(overlay)).toBe(false)
    layer.setCoverageVisible(true)
    expect(map.hasLayer(overlay)).toBe(true)
    const b = overlay.getBounds()
    const c = sim.coverage.bounds
    expect([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]).toEqual([c.west, c.south, c.east, c.north])
    layer.setCoverageVisible(false)
    expect(map.hasLayer(overlay)).toBe(false)
    // Without a canvas there is simply no overlay.
    const bare = createSimMapLayer(fakeMap(), sim)
    bare.setCoverageVisible(true)
    expect(bare.layers.overlay).toBeNull()
  })

  it('follows the aircraft on request until the user drags the map', () => {
    const sim = simulate('area_flat')
    const map = fakeMap()
    const ended = vi.fn()
    const layer = createSimMapLayer(map, sim, { onFollowEnd: ended })
    const s = sim.sample(100)
    layer.update(s)
    expect(map.panTo).not.toHaveBeenCalled()
    layer.setFollow(true)
    layer.update(s)
    expect(map.panTo).toHaveBeenCalledWith([s.lat, s.lng], { animate: false })
    map.fire('dragstart')
    expect(layer.isFollowing()).toBe(false)
    expect(ended).toHaveBeenCalledTimes(1)
    map.panTo.mockClear()
    layer.update(s)
    expect(map.panTo).not.toHaveBeenCalled()
  })

  it('leaves nothing on the map when destroyed', () => {
    const sim = simulate('area_flat')
    const map = fakeMap()
    const layer = createSimMapLayer(map, sim, { canvas: document.createElement('canvas') })
    layer.setCoverageVisible(true)
    layer.update(sim.sample(sim.duration))
    expect(map.layers.size).toBe(2)
    layer.destroy()
    expect(map.layers.size).toBe(0)
    expect(map.handlers.dragstart).toBeUndefined()
    layer.update(sim.sample(10))                                          // a late frame is ignored
    expect(map.layers.size).toBe(0)
  })

  it('the map and the 3D scene are handed the same state and place the aircraft at the same point', () => {
    const sim = simulate('area_terrain')
    const layer = createSimMapLayer(fakeMap(), sim)
    const frame = sim.trajectory.frame
    const state = {}
    for (const t of [0, 77.7, 300, 512.25, sim.duration]) {
      sim.sample(t, state)                                                // one state per frame...
      layer.update(state)                                                 // ...drawn on the map
      const [x, up, z] = aircraftPosition(state)                          // ...and in the scene
      const at = layer.layers.marker.getLatLng()
      const [lng, lat] = frame.inverse(x, -z)
      expect(at.lat).toBeCloseTo(lat, 12)
      expect(at.lng).toBeCloseTo(lng, 12)
      expect(up).toBe(state.alt)
    }
  })
})
