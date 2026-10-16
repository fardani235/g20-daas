import { describe, it, expect, vi, beforeAll } from 'vitest'
import fixture from '@/lib/__fixtures__/flightsim.json'
import { useMissionSim } from './useMissionSim'

// The controller every view hangs off: it builds the simulation, owns the
// clock and hands each frame's state to the subscribed views.

const { missions } = fixture

// A 2D context good enough for the coverage canvas (jsdom has none).
beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = function () {
    const canvas = this
    return {
      createImageData: (w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
      putImageData: vi.fn(image => { canvas.lastImage = image }),
    }
  }
})

function fakeTime() {
  let now = 0
  let next = 1
  const frames = new Map()
  return {
    clock: { now: () => now, raf: cb => { const id = next++; frames.set(id, cb); return id }, caf: id => frames.delete(id) },
    now: () => now,
    frame(ms = 16) {
      now += ms
      const pending = [...frames.values()]
      frames.clear()
      for (const cb of pending) cb(now)
    },
  }
}

function setup({ terrain = fixture.terrain.area_terrain, loadTerrain } = {}) {
  const time = fakeTime()
  const load = loadTerrain || vi.fn(async () => ({ terrain, pending: false, message: null }))
  const sim = useMissionSim({ loadTerrain: load, clock: time.clock, now: time.now })
  return { sim, time, load }
}
const DEFINITION = { type: 'area', elevation_source: 'auto', drone: 'dji-mavic-3e' }

describe('opening a simulation', () => {
  it('asks for the elevation grid of the plan\'s own definition and builds the simulation', async () => {
    const { sim, load } = setup()
    expect(sim.status.value).toBe('idle')
    const opening = sim.open(missions.area_terrain, { definition: DEFINITION, project: 'P1' })
    expect(sim.status.value).toBe('loading')
    expect(await opening).toBe(true)
    expect(load).toHaveBeenCalledWith({ definition: DEFINITION, project: 'P1' })
    expect(sim.status.value).toBe('ready')
    expect(sim.simulation.value.terrain.rows).toBe(fixture.terrain.area_terrain.rows)
    expect(sim.simulation.value.mission).toBe(missions.area_terrain)
    expect(sim.playback).toMatchObject({ time: 0, playing: false, speed: 1, duration: sim.simulation.value.duration })
    expect(sim.state.value.phaseKey).toBe('preflight')
    expect(sim.canvas.value.width).toBe(sim.simulation.value.coverage.cols)
  })

  it('does not ask for terrain the plan does not use', async () => {
    const { sim, load } = setup()
    await sim.open({ ...missions.area_flat, elevation_source: 'none' }, { definition: { ...DEFINITION, elevation_source: 'none' } })
    expect(load).not.toHaveBeenCalled()
    expect(sim.status.value).toBe('ready')
    expect(sim.simulation.value.terrain).toBeNull()
    // Nor without a definition to ask with.
    await sim.open(missions.area_flat)
    expect(load).not.toHaveBeenCalled()
  })

  it('simulates on flat ground and says why when the grid cannot be had', async () => {
    const planned = { ...missions.area_flat, elevation_source: 'auto' }   // planned with elevation data, now out of reach
    const failing = setup({ loadTerrain: vi.fn(async () => { throw new Error('Not permitted') }) })
    expect(await failing.sim.open(planned, { definition: DEFINITION })).toBe(true)
    expect(failing.sim.terrainMessage.value).toBe('Not permitted')
    expect(failing.sim.simulation.value.terrain).toBeNull()

    const refused = setup({ loadTerrain: vi.fn(async () => ({ terrain: null, pending: false, message: 'Elevation data could not be loaded (timeout).' })) })
    await refused.sim.open(planned, { definition: DEFINITION })
    expect(refused.sim.terrainMessage.value).toMatch(/could not be loaded/)
    expect(refused.sim.status.value).toBe('ready')
  })

  it('reports a plan it cannot simulate instead of throwing', async () => {
    const { sim } = setup()
    expect(await sim.open({ waypoints: [], legs: [] }, { definition: DEFINITION })).toBe(false)
    expect(sim.status.value).toBe('error')
    expect(sim.error.value).toMatch(/no computed route/)
    expect(sim.simulation.value).toBeNull()
  })

  it('a newer open wins over one still loading', async () => {
    let release
    const slow = vi.fn(() => new Promise(resolve => { release = () => resolve({ terrain: fixture.terrain.area_terrain }) }))
    const { sim } = setup({ loadTerrain: slow })
    const first = sim.open(missions.area_terrain, { definition: DEFINITION })
    const second = sim.open({ ...missions.area_flat, elevation_source: 'none' }, { definition: DEFINITION })
    expect(await second).toBe(true)
    release()
    expect(await first).toBe(false)
    expect(sim.simulation.value.mission.stats.waypoint_count).toBe(missions.area_flat.stats.waypoint_count)
  })

  it('reopening replaces the simulation and starts from the beginning, keeping the chosen speed', async () => {
    const { sim, time } = setup()
    await sim.open(missions.area_terrain, { definition: DEFINITION })
    sim.setSpeed(8)
    sim.play()
    time.frame(100)
    expect(sim.playback.time).toBeGreaterThan(0)
    await sim.open(missions.multi_battery, { definition: { ...DEFINITION, elevation_source: 'none' } })
    expect(sim.playback).toMatchObject({ time: 0, playing: false, speed: 8 })
    expect(sim.simulation.value.trajectory.flights.length).toBe(3)
  })

  it('close forgets everything', async () => {
    const { sim, time } = setup()
    await sim.open(missions.area_terrain, { definition: DEFINITION })
    sim.play()
    sim.close()
    expect(sim.status.value).toBe('idle')
    expect(sim.simulation.value).toBeNull()
    expect(sim.state.value).toBeNull()
    expect(sim.canvas.value).toBeNull()
    time.frame(100)
    expect(sim.playback).toMatchObject({ time: 0, playing: false })
    sim.play()                                                           // controls are inert without a simulation
    sim.seek(10)
    expect(sim.playback.time).toBe(0)
  })
})

describe('playback', () => {
  it('play, pause, stop, restart and seek move the one clock', async () => {
    const { sim, time } = setup()
    await sim.open(missions.area_terrain, { definition: DEFINITION })
    sim.play()
    expect(sim.playback.playing).toBe(true)
    time.frame(200)
    expect(sim.playback.time).toBeCloseTo(0.2, 9)
    sim.pause()
    time.frame(1000)
    expect(sim.playback.time).toBeCloseTo(0.2, 9)
    sim.seek(300)
    expect(sim.playback.time).toBe(300)
    expect(sim.state.value.t).toBe(300)
    sim.toggle()
    expect(sim.playback.playing).toBe(true)
    sim.stop()
    expect(sim.playback).toMatchObject({ time: 0, playing: false })
    sim.seek(50)
    sim.restart()
    expect(sim.playback).toMatchObject({ time: 0, playing: true })
  })

  it('speed scales simulated time, not the state at a time', async () => {
    const { sim, time } = setup()
    await sim.open(missions.area_terrain, { definition: DEFINITION })
    sim.setSpeed(16)
    sim.play()
    for (let i = 0; i < 10; i++) time.frame(100)
    expect(sim.playback.time).toBeCloseTo(16, 6)
    sim.pause()
    const reached = sim.playback.time
    const played = { ...sim.state.value }
    // The same time reached by seeking is the same state.
    sim.seek(0)
    sim.seek(reached)
    expect({ ...sim.state.value }).toEqual(played)
  })

  it('stops at the end and can be played again', async () => {
    const { sim, time } = setup()
    await sim.open(missions.area_flat)
    sim.seek(sim.playback.duration - 0.05)
    sim.play()
    time.frame(100)
    expect(sim.playback).toMatchObject({ playing: false, ended: true, time: sim.playback.duration })
    expect(sim.state.value.phaseKey).toBe('landed')
    sim.play()
    expect(sim.playback).toMatchObject({ time: 0, playing: true })
  })

  it('jumps by distance and by battery flight', async () => {
    const { sim } = setup()
    await sim.open(missions.multi_battery)
    sim.seekDistance(5000)
    expect(sim.state.value.distance).toBeCloseTo(5000, 3)
    sim.seekFlight(2)
    expect(sim.state.value.flight).toBe(2)
    expect(sim.state.value.phaseKey).toBe('preflight')
    expect(sim.playback.time).toBe(sim.simulation.value.trajectory.flights[2].t0)
    sim.seekFlight(9)                                                    // no such flight: nothing happens
    expect(sim.state.value.flight).toBe(2)
  })
})

describe('feeding the views', () => {
  it('hands every subscribed view the same state object in the same frame', async () => {
    const { sim, time } = setup()
    await sim.open(missions.area_terrain, { definition: DEFINITION })
    const map = []
    const scene = []
    const offMap = sim.onFrame(state => map.push(state))
    sim.onFrame(state => scene.push(state))
    expect(map.length).toBe(1)                                           // a view is drawn as soon as it subscribes
    sim.play()
    time.frame(16); time.frame(16)
    sim.seek(120)
    expect(map.length).toBe(scene.length)
    // The very same object: there is no second copy that could drift.
    for (let i = 0; i < map.length; i++) expect(map[i]).toBe(scene[i])
    expect(map.at(-1).t).toBe(120)
    offMap()
    sim.seek(130)
    expect(scene.length).toBe(map.length + 1)
  })

  it('what the views drew is what a fresh sample of that time gives', async () => {
    const { sim, time } = setup()
    await sim.open(missions.area_terrain, { definition: DEFINITION })
    const seen = []
    sim.onFrame(s => seen.push({ t: s.t, lng: s.lng, lat: s.lat, alt: s.alt, heading: s.heading, photos: s.photos, agl: s.agl }))
    sim.setSpeed(32)
    sim.play()
    for (let i = 0; i < 30; i++) time.frame(17)
    sim.pause()
    const fresh = useMissionSim({ loadTerrain: async () => ({ terrain: fixture.terrain.area_terrain }) })
    await fresh.open(missions.area_terrain, { definition: DEFINITION })
    for (const frame of seen) {
      const s = fresh.simulation.value.sample(frame.t)
      expect({ t: s.t, lng: s.lng, lat: s.lat, alt: s.alt, heading: s.heading, photos: s.photos, agl: s.agl }).toEqual(frame)
    }
  })

  it('refreshes the dashboard a few times a second while playing, and at once on a jump', async () => {
    const { sim, time } = setup()
    await sim.open(missions.area_terrain, { definition: DEFINITION })
    const frames = []
    sim.onFrame(s => frames.push(s.t))
    sim.play()
    const before = sim.state.value
    time.frame(16)
    expect(sim.state.value).toBe(before)                                 // 16 ms later: the views moved, the panel did not
    expect(frames.length).toBeGreaterThan(1)
    for (let i = 0; i < 7; i++) time.frame(16)
    expect(sim.state.value).not.toBe(before)                             // after a tenth of a second it did
    sim.pause()
    sim.seek(400)
    expect(sim.state.value.t).toBe(400)
  })

  it('paints the shared coverage canvas as photos are taken and tells the views', async () => {
    const { sim } = setup()
    await sim.open(missions.area_flat)
    const repainted = vi.fn()
    sim.onCoverage(repainted)
    const canvas = sim.canvas.value
    const covered = () => { let n = 0; const d = canvas.lastImage.data; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n }
    sim.seek(sim.simulation.value.trajectory.legTimes.t1[0])             // one line flown
    const one = covered()
    expect(one).toBeGreaterThan(0)
    expect(repainted).toHaveBeenCalled()
    sim.seek(sim.playback.duration)
    expect(covered()).toBeGreaterThan(one * 2)
    sim.seek(0)
    expect(covered()).toBe(0)                                            // back at the start: nothing photographed
    // Seeking within the same photo count does not repaint.
    repainted.mockClear()
    sim.seek(1)
    expect(repainted).not.toHaveBeenCalled()
  })

  it('while playing fast, repaints coverage a few times a second; a pause paints the exact moment', async () => {
    const { sim, time } = setup()
    await sim.open(missions.multi_battery)
    const repainted = vi.fn()
    sim.onCoverage(repainted)
    const canvas = sim.canvas.value
    const covered = () => { let n = 0; const d = canvas.lastImage.data; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n }
    sim.seek(sim.simulation.value.trajectory.legTimes.t0[0])          // the start of the first line
    repainted.mockClear()
    sim.setSpeed(64)
    sim.play()
    for (let i = 0; i < 30; i++) time.frame(16)                        // 30 frames, photos in every one of them
    expect(repainted.mock.calls.length).toBeGreaterThan(5)
    expect(repainted.mock.calls.length).toBeLessThanOrEqual(11)        // 480 ms at one repaint per 50 ms
    sim.pause()
    // Paused: the canvas shows exactly the photos taken so far.
    const shown = covered()
    const fresh = new Uint8ClampedArray(canvas.width * canvas.height * 4)
    sim.simulation.value.coverage.paint(fresh, sim.state.value.photos, 'covered')
    let exact = 0
    for (let i = 3; i < fresh.length; i += 4) if (fresh[i]) exact++
    expect(shown).toBe(exact)
    expect(exact).toBeGreaterThan(0)
  })

  it('switches the coverage view for both views at once', async () => {
    const { sim } = setup()
    await sim.open(missions.area_flat)
    sim.seek(sim.playback.duration)
    const repainted = vi.fn()
    const frames = vi.fn()
    sim.onCoverage(repainted)
    sim.onFrame(frames)
    frames.mockClear()
    sim.setCoverageMode('overlap')
    expect(sim.coverageMode.value).toBe('overlap')
    expect(repainted).toHaveBeenCalledTimes(1)
    expect(frames).toHaveBeenCalledWith(expect.any(Object), 'coverage')
    const colours = new Set()
    const d = sim.canvas.value.lastImage.data
    for (let i = 0; i < d.length; i += 4) if (d[i + 3]) colours.add(`${d[i]},${d[i + 1]},${d[i + 2]}`)
    expect(colours.size).toBeGreaterThan(1)                              // classes of overlap, not one tint
    sim.setCoverageMode('off')
    sim.seek(10)
    expect(repainted).toHaveBeenCalledTimes(1)                           // off: no more painting
    sim.setCoverageMode('covered')
    expect(repainted).toHaveBeenCalledTimes(2)                           // back on: painted for the current moment
  })

  it('refresh redraws the views without moving time', async () => {
    const { sim } = setup()
    await sim.open(missions.area_flat)
    sim.seek(77)
    const frames = vi.fn()
    sim.onFrame(frames)
    frames.mockClear()
    sim.refresh()
    expect(frames).toHaveBeenCalledTimes(1)
    expect(frames.mock.calls[0][0].t).toBe(77)
    expect(sim.playback.time).toBe(77)
  })
})
