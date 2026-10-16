import { describe, it, expect } from 'vitest'
import { syntheticMission, syntheticTerrain } from '../__fixtures__/syntheticMission'
import { createSimulation } from './index'

// The planner accepts up to 20 000 waypoints and 100 000 photos. The
// simulator has to build such a mission once without a noticeable wait and
// then answer "where is the aircraft" far faster than a frame, however long
// the timeline is. Budgets are several times what a laptop needs (the
// figures in the comments), so they fail on a regression in complexity, not
// on a busy CI machine.

const elapsed = fn => { const t = performance.now(); const out = fn(); return [performance.now() - t, out] }

const big = airframe => syntheticMission({
  lines: 2000, vias: 8, lineLength: 1000, spacing: 30, photoSpacing: 20.5, flights: 40, airframe,
  altitude: x => 100 + 10 * Math.sin(x / 150),
})

describe('at the planner\'s limits: 20 000 waypoints, 98 000 photos, 40 batteries', () => {
  const mission = big('multirotor')
  const grid = syntheticTerrain(mission, x => 500 + 10 * Math.sin(x / 150), { spacing: 620 })
  const [buildMs, sim] = elapsed(() => createSimulation(mission, grid))

  it('is the size it claims to be', () => {
    expect(mission.waypoints.length).toBe(20000)
    expect(sim.trajectory.photos.count).toBe(98000)
    expect(sim.trajectory.flights.length).toBe(40)
    expect(sim.trajectory.segments.count).toBeGreaterThan(20000)
  })

  it('builds in well under the time a user would notice (about 0.5 s; budget 4 s)', () => {
    expect(buildMs).toBeLessThan(4000)
  })

  it('samples a state in microseconds (about 7 µs; budget 100 µs)', () => {
    const state = {}
    const [ms] = elapsed(() => { for (let i = 0; i < 20000; i++) sim.sample((i * 7919.3) % sim.duration, state) })
    expect(ms / 20000).toBeLessThan(0.1)
  })

  it('sampling cost does not grow with the mission: a thousand times the waypoints, about the same time', () => {
    const small = createSimulation(syntheticMission({ lines: 2, vias: 8 }), null)
    const state = {}
    const run = s => elapsed(() => { for (let i = 0; i < 20000; i++) s.sample((i * 131.7) % s.duration, state) })[0]
    run(small); run(sim)                                           // warm up
    const a = Math.min(run(small), run(small), run(small))
    const b = Math.min(run(sim), run(sim), run(sim))
    expect(b).toBeLessThan(a * 6 + 20)
  })

  it('repaints coverage for any moment in a fraction of a frame (about 0.1 ms; budget 8 ms)', () => {
    const c = sim.coverage
    expect(c.cols * c.rows).toBeLessThanOrEqual(512 * 512)
    const pixels = new Uint8ClampedArray(c.cols * c.rows * 4)
    const [ms] = elapsed(() => { for (let i = 0; i < 50; i++) c.paint(pixels, i * 1900, i % 2 ? 'overlap' : 'covered') })
    expect(ms / 50).toBeLessThan(8)
  })

  it('keeps the profile and the timeline small enough to draw', () => {
    expect(sim.profile.distance.length).toBeLessThanOrEqual(1700)
    expect(sim.trajectory.phases.length).toBeLessThan(sim.trajectory.segments.count)
  })

  it('still finds every photo and flies every flight', () => {
    const end = sim.sample(sim.duration)
    expect(end.photos).toBe(98000)
    expect(end.flight).toBe(39)
    expect(end.coverage).toBeGreaterThan(0.99)
    expect(sim.trajectory.distance).toBeGreaterThan(2000 * 1000)
  })

  it('scrubbing is as cheap backwards as forwards', () => {
    const state = {}
    const forward = elapsed(() => { for (let t = 0; t < sim.duration; t += sim.duration / 5000) sim.sample(t, state) })[0]
    const backward = elapsed(() => { for (let t = sim.duration; t > 0; t -= sim.duration / 5000) sim.sample(t, state) })[0]
    expect(backward).toBeLessThan(forward * 4 + 20)
  })
})

describe('curved turns at scale', () => {
  it('a fixed-wing mission of the same size builds within budget (about 0.7 s; budget 5 s)', () => {
    const [ms, sim] = elapsed(() => createSimulation(big('fixed-wing'), null))
    expect(ms).toBeLessThan(5000)
    // Coarser arcs on a long mission keep the timeline bounded.
    expect(sim.trajectory.segments.count).toBeLessThan(150000)
    expect(sim.trajectory.photos.count).toBe(98000)
  })
})

describe('a large terrain grid', () => {
  it('samples from a 10 000 node grid as fast as from a small one', () => {
    const mission = syntheticMission({ lines: 30, lineLength: 2900, spacing: 100 })
    const grid = syntheticTerrain(mission, (x, y) => 300 + 40 * Math.sin(x / 200) * Math.cos(y / 300), { spacing: 33, pad: 100 })
    expect(grid.rows * grid.cols).toBeGreaterThan(9000)
    const [ms, sim] = elapsed(() => createSimulation(mission, grid))
    expect(ms).toBeLessThan(3000)
    const state = {}
    const [sampleMs] = elapsed(() => { for (let i = 0; i < 20000; i++) sim.sample((i * 37.3) % sim.duration, state) })
    expect(sampleMs / 20000).toBeLessThan(0.1)
    expect(state.groundKnown).toBe(true)
  })
})
