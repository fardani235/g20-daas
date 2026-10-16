import { describe, it, expect, vi } from 'vitest'
import fixture from '../__fixtures__/flightsim.json'
import { syntheticMission } from '../__fixtures__/syntheticMission'
import { SPEEDS, createClock } from './clock'
import {
  comparisonRows, compassPoint, coverageRows, dashboardTiles, flightMarks, formatClock, formatDistance, phaseText,
  timelineColumns,
} from './describe'
import { PHASE, SIMULATION_NOTICE, SimulationError, createSimulation, phaseLabel } from './index'
import { buildProfile, profileAtDistance, profileGeometry } from './profile'

const { missions } = fixture
const simulate = (name, options) => createSimulation(missions[name], fixture.terrain[name] || null, options)

// A clock whose time and animation frames the test controls.
function fakeTime() {
  let now = 0
  let next = 1
  const frames = new Map()
  return {
    now: () => now,
    raf: cb => { const id = next++; frames.set(id, cb); return id },
    caf: id => frames.delete(id),
    /** Advance real time by `ms` and run the pending frame. */
    frame(ms = 16) {
      now += ms
      const pending = [...frames.entries()]
      frames.clear()
      for (const [, cb] of pending) cb(now)
    },
    pending: () => frames.size,
  }
}

describe('simulation clock', () => {
  it('plays in real time, scaled by the speed', () => {
    const time = fakeTime()
    const clock = createClock({ duration: 100, ...time })
    clock.play()
    time.frame(100)
    expect(clock.state.time).toBeCloseTo(0.1, 9)
    clock.setSpeed(8)
    time.frame(100)
    expect(clock.state.time).toBeCloseTo(0.1 + 0.8, 9)
    expect(clock.state.playing).toBe(true)
  })

  it('pause holds the time; play resumes from it without a jump', () => {
    const time = fakeTime()
    const clock = createClock({ duration: 100, ...time })
    clock.play()
    time.frame(200)
    clock.pause()
    expect(time.pending()).toBe(0)                                 // no frame left scheduled
    const held = clock.state.time
    time.frame(5000)                                               // paused: time passes, the clock does not
    expect(clock.state.time).toBe(held)
    clock.play()
    time.frame(100)
    expect(clock.state.time).toBeCloseTo(held + 0.1, 9)
  })

  it('stop returns to the start, paused; restart returns to the start, playing', () => {
    const time = fakeTime()
    const clock = createClock({ duration: 100, ...time })
    clock.play()
    time.frame(200)
    clock.stop()
    expect([clock.state.time, clock.state.playing]).toEqual([0, false])
    clock.seek(40)
    clock.restart()
    expect([clock.state.time, clock.state.playing]).toEqual([0, true])
    time.frame(50)
    expect(clock.state.time).toBeCloseTo(0.05, 9)
  })

  it('seeks anywhere, clamped to the timeline, playing or not', () => {
    const time = fakeTime()
    const clock = createClock({ duration: 100, ...time })
    clock.seek(42.5)
    expect(clock.state.time).toBe(42.5)
    clock.seek(-3)
    expect(clock.state.time).toBe(0)
    clock.seek(1e6)
    expect([clock.state.time, clock.state.ended]).toEqual([100, true])
    clock.seek(10)
    clock.play()
    clock.seek(60)
    time.frame(100)
    expect(clock.state.time).toBeCloseTo(60.1, 9)
  })

  it('stops at the end, and play from the end starts over', () => {
    const time = fakeTime()
    const clock = createClock({ duration: 1, ...time })
    const reasons = []
    clock.subscribe((_s, reason) => reasons.push(reason))
    clock.setSpeed(64)
    clock.play()
    time.frame(200)
    expect(clock.state).toMatchObject({ time: 1, playing: false, ended: true })
    expect(reasons.at(-1)).toBe('end')
    expect(time.pending()).toBe(0)
    clock.play()
    expect(clock.state).toMatchObject({ time: 0, playing: true, ended: false })
  })

  it('does not leap ahead after the tab was in the background', () => {
    const time = fakeTime()
    const clock = createClock({ duration: 1000, ...time })
    clock.play()
    time.frame(60000)                                              // a minute without animation frames
    expect(clock.state.time).toBeLessThanOrEqual(0.25)
  })

  it('tells every subscriber the same time, in one call each', () => {
    const time = fakeTime()
    const clock = createClock({ duration: 100, ...time })
    const a = []
    const b = []
    clock.subscribe(s => a.push(s.time))
    const off = clock.subscribe(s => b.push(s.time))
    clock.play()
    time.frame(16); time.frame(16); time.frame(16)
    clock.seek(50)
    expect(a).toEqual(b)
    expect(a.length).toBe(5)                                       // play, three ticks, seek
    off()
    clock.seek(60)
    expect(b.length).toBe(5)
    expect(a.length).toBe(6)
  })

  it('ignores nonsense and survives destroy', () => {
    const time = fakeTime()
    const clock = createClock({ duration: 0, ...time })
    clock.play()                                                   // nothing to play
    expect(clock.state.playing).toBe(false)
    clock.setDuration(10)
    clock.setSpeed(-1)
    clock.setSpeed('fast')
    expect(clock.state.speed).toBe(1)
    clock.play()
    clock.destroy()
    time.frame(100)
    expect(clock.state.playing).toBe(false)
    clock.play()
    expect(time.pending()).toBe(0)
  })

  it('offers speeds from half to 64 times', () => {
    expect(SPEEDS[0]).toBe(0.5)
    expect(SPEEDS.at(-1)).toBe(64)
    expect(SPEEDS).toContain(1)
  })

  it('toggle switches between playing and paused', () => {
    const clock = createClock({ duration: 10, ...fakeTime() })
    clock.toggle()
    expect(clock.state.playing).toBe(true)
    clock.toggle()
    expect(clock.state.playing).toBe(false)
  })
})

describe('playback is deterministic', () => {
  it('the same clock inputs give the same flight, frame for frame', () => {
    const run = () => {
      const time = fakeTime()
      const sim = simulate('area_terrain')
      const clock = createClock({ duration: sim.duration, ...time })
      const seen = []
      clock.subscribe(s => { const st = sim.sample(s.time); seen.push([st.t, st.x, st.y, st.alt, st.heading, st.photos, st.battery]) })
      clock.setSpeed(16)
      clock.play()
      for (const ms of [16, 17, 16, 33, 16, 250, 16, 8]) time.frame(ms)
      clock.seek(400)
      for (const ms of [16, 16, 40]) time.frame(ms)
      return seen
    }
    expect(run()).toEqual(run())
  })

  it('playing to a time and scrubbing to it show the same state', () => {
    const sim = simulate('multi_battery')
    const time = fakeTime()
    const clock = createClock({ duration: sim.duration, speed: 64, ...time })
    clock.play()
    for (let i = 0; i < 300; i++) time.frame(20)
    clock.pause()
    const played = { ...sim.sample(clock.state.time) }
    // A fresh simulation, no playback: straight to that time.
    const direct = { ...simulate('multi_battery').sample(clock.state.time) }
    expect(direct).toEqual(played)
  })

  it('two views driven by one clock see identical states', () => {
    // The 2D map and the 3D scene are both subscribers that sample the shared simulation.
    const sim = simulate('area_flat')
    const time = fakeTime()
    const clock = createClock({ duration: sim.duration, ...time })
    const map2d = []
    const scene3d = []
    const state = {}
    clock.subscribe(s => {
      sim.sample(s.time, state)                                    // sampled once per frame...
      for (const view of [map2d, scene3d]) view.push({ t: state.t, lng: state.lng, lat: state.lat, alt: state.alt, heading: state.heading, photos: state.photos })
    })
    clock.setSpeed(32)
    clock.play()
    for (let i = 0; i < 40; i++) time.frame(16)
    clock.seek(sim.duration * 0.6)
    clock.stop()
    expect(map2d.length).toBeGreaterThan(40)
    expect(map2d).toEqual(scene3d)
    expect(map2d.at(-1).t).toBe(0)
  })
})

describe('createSimulation', () => {
  it('builds everything the views share from the mission and the grid', () => {
    const sim = simulate('area_terrain')
    expect(sim.mission).toBe(missions.area_terrain)                // the planner's object, not a copy
    expect(sim.terrain.rows).toBe(fixture.terrain.area_terrain.rows)
    expect(sim.duration).toBe(sim.trajectory.duration)
    expect(sim.camera.label).toBeTruthy()
    expect(sim.coverage.cols).toBeGreaterThan(100)
    expect(sim.profile.distance.length).toBeGreaterThan(100)
  })

  it('a sample carries coverage and camera figures for that moment', () => {
    const sim = simulate('area_slope')
    const tr = sim.trajectory
    const start = sim.sample(0)
    expect(start.coverage).toBe(0)
    const mid = sim.sample((tr.legTimes.t0[3] + tr.legTimes.t1[3]) / 2)
    expect(mid.coverage).toBeGreaterThan(0.3)
    expect(mid.coverage).toBeLessThan(0.8)
    // Resolution follows the height above ground: constant altitude over a slope.
    expect(mid.gsdCm).toBeCloseTo(sim.camera.gsdPerMetre * mid.agl * 100, 9)
    expect(mid.footprintAcross / mid.footprintAlong).toBeCloseTo(sim.camera.aspect, 9)
    expect(sim.sample(sim.duration).coverage).toBeCloseTo(sim.coverage.stats.coveredPercent / 100, 9)
  })

  it('the live footprint is under the aircraft in flight and absent on the ground', () => {
    const sim = simulate('area_flat')
    const tr = sim.trajectory
    expect(sim.footprintAt(sim.sample(0))).toBeNull()
    const s = sim.sample((tr.legTimes.t0[0] + tr.legTimes.t1[0]) / 2)
    const c = sim.footprintAt(s)
    const cx = (c[0] + c[2] + c[4] + c[6]) / 4
    const cy = (c[1] + c[3] + c[5] + c[7]) / 4
    expect(Math.hypot(cx - s.x, cy - s.y)).toBeLessThan(1e-6)
    const o = sim.overlapAt(s)
    expect(o.front).toBeCloseTo(80, 0)
    expect(o.side).toBeCloseTo(70, 0)
  })

  it('works without elevation data and without a camera', () => {
    const sim = createSimulation(missions.area_flat, null)
    expect(sim.terrain).toBeNull()
    expect(sim.sample(100).agl).toBeNull()
    const bare = JSON.parse(JSON.stringify(missions.area_flat))
    bare.equipment.camera = {}
    const noCamera = createSimulation(bare, null)
    expect(noCamera.camera).toBeNull()
    expect(noCamera.coverage).toBeNull()
    expect(noCamera.sample(100).coverage).toBeNull()
    expect(noCamera.sample(100).gsdCm).toBeNull()
    expect(noCamera.footprintAt(noCamera.sample(100))).toBeNull()
    expect(noCamera.overlapAt(noCamera.sample(100))).toBeNull()
  })

  it('puts its totals next to the plan\'s, so a difference shows', () => {
    const sim = simulate('multi_battery')
    const m = missions.multi_battery
    expect(sim.summary.planned).toEqual({
      flightTime: m.stats.total_duration_s, distance: m.stats.total_distance_m, photos: m.stats.photo_count, flights: 3,
    })
    expect(sim.summary.photos).toBe(m.stats.photo_count)
    expect(sim.summary.flights).toBe(3)
    expect(sim.summary.duration).toBeGreaterThan(sim.summary.flightTime)       // battery changes
    expect(sim.summary.exceedsUsable).toBe(false)
    expect(sim.summary.lowestClearance).toBeNull()                 // no elevation data in this plan
  })

  it('throws for a mission it cannot fly, and never touches the one it can', () => {
    expect(() => createSimulation({ waypoints: [] })).toThrow(SimulationError)
    const before = JSON.stringify(missions.area_terrain)
    const sim = simulate('area_terrain')
    for (let t = 0; t < sim.duration; t += 9) { const s = sim.sample(t); sim.footprintAt(s) }
    expect(JSON.stringify(missions.area_terrain)).toBe(before)
  })

  it('calls itself what it is', () => {
    expect(SIMULATION_NOTICE).toMatch(/Simulation only/)
    expect(SIMULATION_NOTICE).toMatch(/not flight telemetry/)
    expect(SIMULATION_NOTICE).toMatch(/does not verify/)
    expect(SIMULATION_NOTICE).not.toMatch(/\b(safe to fly|verified|cleared|guaranteed)\b/i)
    expect(phaseLabel(PHASE.SURVEY)).toBe('Survey line')
    expect(phaseLabel(99)).toBe('')
  })
})

describe('elevation profile', () => {
  it('covers the whole flight: ground distance against elevation, takeoff to landing', () => {
    const sim = simulate('area_terrain')
    const p = sim.profile
    expect(p.absolute).toBe(true)
    expect(p.hasGround).toBe(true)
    expect(p.length).toBeCloseTo(sim.trajectory.distance, 6)
    expect(p.distance[0]).toBe(0)
    expect(p.distance.at(-1)).toBeCloseTo(p.length, 6)
    for (let i = 1; i < p.distance.length; i++) expect(p.distance[i]).toBeGreaterThanOrEqual(p.distance[i - 1])
    // On the ground at both ends, at the takeoff elevation.
    expect(p.flight[0]).toBeCloseTo(700, 1)
    expect(p.flight.at(-1)).toBeCloseTo(700, 1)
    expect(Math.max(...p.flight)).toBeCloseTo(700 + missions.area_terrain.stats.max_altitude_m, 0)
    // The ridge is in it.
    const ground = Array.from(p.ground).filter(v => v === v)
    expect(Math.max(...ground) - Math.min(...ground)).toBeGreaterThan(50)
  })

  it('agrees with the state sampled at the same distance', () => {
    const sim = simulate('area_terrain')
    for (const d of [50, 700, 1500, 3200, 6000]) {
      const s = sim.sample(sim.trajectory.timeAtDistance(d))
      const at = profileAtDistance(sim.profile, d)
      expect(at.flight).toBeCloseTo(s.amsl, 0)
      expect(Math.abs(at.ground - s.ground)).toBeLessThan(4)       // linear between rows 15 m apart, on a 1:2 slope
    }
  })

  it('finds the lowest clearance in flight, ignoring the takeoff and the landing', () => {
    const sim = simulate('area_slope')
    const low = sim.profile.lowest
    expect(low.clearance).toBeGreaterThan(40)                      // not the zero of standing on the ground
    expect(low.clearance).toBeCloseTo(missions.area_slope.terrain.clearance_min_m, 0)
    const s = sim.sample(low.time)
    expect(s.agl).toBeCloseTo(low.clearance, 0)
    expect(sim.summary.lowestClearance).toBe(low.clearance)
    expect(sim.summary.plannedLowestClearance).toBe(missions.area_slope.terrain.clearance_min_m)
  })

  it('without elevation data it shows height above takeoff and no ground', () => {
    const p = simulate('area_flat').profile
    expect(p.absolute).toBe(false)
    expect(p.hasGround).toBe(false)
    expect(p.lowest).toBeNull()
    expect(Math.max(...p.flight)).toBe(100)
    expect(p.flight[0]).toBe(0)
  })

  it('marks where each battery flight starts', () => {
    const sim = simulate('multi_battery')
    const starts = sim.profile.flights.map(f => f.distance)
    expect(starts[0]).toBe(0)
    expect(starts[1]).toBeCloseTo(sim.trajectory.flights[0].distance, 6)
    expect(starts[2]).toBeCloseTo(sim.trajectory.flights[0].distance + sim.trajectory.flights[1].distance, 6)
  })

  it('stays within its point budget on a long mission and keeps the lowest point', () => {
    const sim = simulate('area_slope')
    const full = buildProfile(sim.trajectory)
    const thin = buildProfile(sim.trajectory, { maxPoints: 40 })
    expect(thin.distance.length).toBeLessThanOrEqual(42)
    expect(thin.distance.at(-1)).toBeCloseTo(full.length, 6)
    expect(thin.lowest.clearance).toBe(full.lowest.clearance)
    // The row of the lowest point survived the thinning.
    const i = Array.from(thin.distance).findIndex(d => Math.abs(d - thin.lowest.distance) < 1e-9)
    expect(i).toBeGreaterThanOrEqual(0)
    expect(thin.flight[i] - thin.ground[i]).toBeCloseTo(thin.lowest.clearance, 6)
  })

  it('turns into SVG geometry with a scale that maps back to distance', () => {
    const sim = simulate('area_terrain')
    const g = profileGeometry(sim.profile, 400, 120)
    expect(g.flight.startsWith('M')).toBe(true)
    expect(g.ground.startsWith('M')).toBe(true)
    expect(g.area.endsWith('Z')).toBe(true)
    expect(g.x(0)).toBe(4)
    expect(g.x(g.length)).toBe(396)
    expect(g.y(g.min)).toBe(116)
    expect(g.y(g.max)).toBe(4)
    expect(g.distanceAt(g.x(1234))).toBeCloseTo(1234, 6)
    expect(g.distanceAt(-50)).toBe(0)
    expect(g.distanceAt(5000)).toBe(g.length)
    expect(g.bands.length).toBe(1)
    expect(profileGeometry(null, 400, 120)).toBeNull()
    // A gap in the ground data is a gap in the line, not a line to zero.
    const holes = profileGeometry(simulate('area_slope').profile, 400, 120)
    expect(holes.ground).not.toMatch(/NaN/)
    // Without ground there is only the flight line.
    const flat = profileGeometry(simulate('area_flat').profile, 400, 120)
    expect(flat.ground).toBe('')
    expect(flat.flight.length).toBeGreaterThan(10)
  })
})

describe('dashboard wording', () => {
  it('formats clock time and distance', () => {
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(59.6)).toBe('1:00')
    expect(formatClock(605)).toBe('10:05')
    expect(formatClock(3725)).toBe('1:02:05')
    expect(formatClock(-4)).toBe('0:00')
    expect(formatDistance(0)).toBe('0 m')
    expect(formatDistance(999.4)).toBe('999 m')
    expect(formatDistance(3624)).toBe('3.62 km')
    expect(formatDistance(38598)).toBe('38.6 km')
    expect(formatDistance(NaN)).toBe('—')
    expect(['N', 'E', 'S', 'W', 'N'].map((_, i) => compassPoint(i * 90))).toEqual(['N', 'E', 'S', 'W', 'N'])
    expect(compassPoint(-45)).toBe('NW')
  })

  it('says what the aircraft is doing', () => {
    const sim = simulate('multi_battery')
    const tr = sim.trajectory
    expect(phaseText(sim.sample(0), tr)).toEqual({ key: 'preflight', title: 'Pre-flight', flight: 'Flight 1 of 3' })
    const online = sim.sample((tr.legTimes.t0[4] + tr.legTimes.t1[4]) / 2)
    expect(phaseText(online, tr).title).toBe(`Survey line 5 of ${missions.multi_battery.stats.line_count}`)
    const swap = tr.phases.find(p => p.key === 'battery')
    expect(phaseText(sim.sample(swap.t0 + 1), tr).title).toBe('Battery change before flight 2')
    // A single-flight mission does not number its flights.
    const single = simulate('area_flat')
    expect(phaseText(single.sample(0), single.trajectory).flight).toBe('')
  })

  it('builds the tiles, with estimates labelled and unknowns shown as unknown', () => {
    const sim = simulate('area_terrain')
    const tr = sim.trajectory
    const s = sim.sample((tr.legTimes.t0[2] + tr.legTimes.t1[2]) / 2)
    const tiles = Object.fromEntries(dashboardTiles(s, sim).map(t => [t.key, t]))
    expect(Object.keys(tiles)).toEqual(['phase', 'progress', 'distance', 'speed', 'altitude', 'clearance', 'battery', 'photos', 'coverage', 'attitude'])
    expect(tiles.phase.value).toBe('Survey line 3 of 9')
    expect(tiles.progress.value).toMatch(/^\d+ %$/)
    expect(tiles.progress.detail).toBe(`${formatClock(s.t)} of ${formatClock(tr.duration)}`)
    expect(tiles.speed.value).toBe('10.0 m/s')
    expect(tiles.altitude.value).toBe(`${Math.round(s.alt)} m`)
    expect(tiles.altitude.detail).toMatch(/^about \d+ m elevation$/)
    expect(tiles.clearance.value).toBe(`${Math.round(s.agl)} m`)
    expect(tiles.clearance.detail).toMatch(/estimate/)
    expect(tiles.battery.value).toMatch(/^\d+ %$/)
    expect(tiles.battery.detail).toMatch(/estimate; 25 % kept in reserve/)
    expect(tiles.battery.meter).toBeCloseTo(s.battery / 100, 9)
    expect(tiles.battery.mark).toBe(0.25)
    expect(tiles.photos.detail).toMatch(/of 315 · camera on/)
    expect(tiles.coverage.detail).toMatch(/estimate/)
    expect(tiles.attitude.value).toMatch(/^\d{3}° [NESW]{1,2}$/)

    const flat = simulate('area_flat')
    const unknown = Object.fromEntries(dashboardTiles(flat.sample(200), flat).map(t => [t.key, t]))
    expect(unknown.clearance.value).toBe('not known')
    expect(unknown.altitude.detail).toBe('elevation not known')
  })

  it('adds a per-flight clock on a multi-battery mission and warns inside the reserve', () => {
    const sim = simulate('multi_battery')
    const keys = dashboardTiles(sim.sample(2000), sim).map(t => t.key)
    expect(keys).toContain('flight')
    const short = JSON.parse(JSON.stringify(missions.area_flat))
    short.equipment.drone.max_flight_time_min = 8
    short.stats.usable_flight_time_s = 8 * 60 * 0.8 * 0.75
    const tight = createSimulation(short, null)
    const tile = dashboardTiles(tight.sample(tight.duration - 20), tight).find(t => t.key === 'battery')
    expect(tile.tone).toBe('warn')
    expect(tile.detail).toMatch(/into the 25 % reserve/)
  })

  it('flags a clearance below the plan\'s minimum while flying, not while landing', () => {
    const m = JSON.parse(JSON.stringify(missions.area_slope))
    m.params.min_clearance_m = 90
    const sim = createSimulation(m, fixture.terrain.area_slope)
    const tr = sim.trajectory
    const online = sim.sample((tr.legTimes.t0[0] + tr.legTimes.t1[0]) / 2)
    expect(dashboardTiles(online, sim).find(t => t.key === 'clearance').tone).toBe('warn')
    const landing = tr.phases.find(p => p.key === 'landing')
    const low = sim.sample(landing.t1 - 0.5)
    expect(dashboardTiles(low, sim).find(t => t.key === 'clearance').tone).toBe('')
  })

  it('compares the simulation with the plan and reports coverage', () => {
    const sim = simulate('area_slope')
    const rows = comparisonRows(sim)
    expect(rows.map(r => r.label)).toEqual(['Flight time', 'Distance', 'Photos', 'Batteries', 'Lowest clearance'])
    expect(rows[2]).toMatchObject({ simulated: '133', planned: '133' })
    expect(rows[4].planned).toBe(`${Math.round(missions.area_slope.terrain.clearance_min_m)} m`)
    expect(comparisonRows(simulate('area_flat')).length).toBe(4)   // no clearance without elevation data

    const cover = coverageRows(sim)
    expect(cover[0].label).toBe('Survey area covered')
    expect(cover[0].value).toMatch(/^\d+ %$/)
    expect(cover.at(-1).label).toBe('Over the highest ground')     // overlap is worse there
    expect(coverageRows(simulate('area_flat')).length).toBe(4)
    const bare = JSON.parse(JSON.stringify(missions.area_flat))
    bare.equipment.camera = {}
    expect(coverageRows(createSimulation(bare, null))).toEqual([])
  })

  it('reduces the timeline to one phase per pixel column', () => {
    const tr = simulate('multi_battery').trajectory
    const cols = timelineColumns(tr, 600)
    expect(cols.length).toBe(600)
    expect(cols[0]).toBe(PHASE.PREFLIGHT)
    expect(cols.at(-1)).toBe(PHASE.LANDED)
    const share = Array.from(cols).filter(p => p === PHASE.SURVEY).length / 600
    const actual = tr.phases.filter(p => p.key === 'survey').reduce((s, p) => s + p.t1 - p.t0, 0) / tr.duration
    expect(share).toBeCloseTo(actual, 1)
    expect(Array.from(cols)).toContain(PHASE.BATTERY)
    expect(timelineColumns(tr, 0).length).toBe(1)

    const marks = flightMarks(tr)
    expect(marks.length).toBe(3)
    expect(marks[0].start).toBe(0)
    expect(marks[1].start).toBeGreaterThan(marks[0].end)
    expect(marks[2].end).toBeCloseTo(1, 9)
  })
})

describe('a mission generated for the tests behaves like a planned one', () => {
  it('simulates end to end', () => {
    const sim = createSimulation(syntheticMission({ lines: 6, flights: 2 }), null)
    expect(sim.trajectory.flights.length).toBe(2)
    expect(sim.summary.photos).toBe(6 * 21)
    expect(sim.coverage.stats.coveredPercent).toBeGreaterThan(99)
    const spy = vi.fn()
    for (let t = 0; t <= sim.duration; t += sim.duration / 20) spy(sim.sample(t).phaseKey)
    expect(new Set(spy.mock.calls.flat()).size).toBeGreaterThan(4)
  })
})
