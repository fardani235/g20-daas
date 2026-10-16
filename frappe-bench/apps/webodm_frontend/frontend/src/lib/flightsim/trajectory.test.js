import { describe, it, expect } from 'vitest'
import fixture from '../__fixtures__/flightsim.json'
import { syntheticMission, syntheticTerrain } from '../__fixtures__/syntheticMission'
import { LocalFrame, angleDiff } from './frame'
import { createTerrain } from './terrain'
import { DYNAMICS, PHASE, PHASE_INFO, SimulationError, buildTrajectory, isMove } from './trajectory'

// The simulated flight against the mission it replays. The missions and the
// reference positions come from the real planner (flightsim.json); nothing
// here re-plans anything, so every assertion is "the simulation flies what
// the plan says", checked independently of the simulator's own bookkeeping.

const { missions, refs } = fixture
const build = (name, options) => buildTrajectory(missions[name], { terrain: createTerrain(fixture.terrain[name]), options })
const MULTIROTOR = ['area_flat', 'area_terrain', 'area_slope', 'multi_battery', 'corridor', 'land_in_place']
const ALL = Object.keys(missions)

function deepFreeze(o) {
  if (o && typeof o === 'object' && !Object.isFrozen(o)) {
    Object.freeze(o)
    for (const v of Object.values(o)) deepFreeze(v)
  }
  return o
}

// Positions sampled densely over the whole timeline.
function sweep(tr, dt = 0.05) {
  const out = []
  for (let t = 0; t <= tr.duration; t += dt) out.push(tr.sample(t))
  out.push(tr.sample(tr.duration))
  return out
}

describe('the route flown is the route planned', () => {
  it.each(ALL)('%s: every waypoint is flown through, at its planned height, in order', name => {
    const m = missions[name]
    const tr = build(name)
    const frame = LocalFrame.ofMission(m)
    const seg = tr.segments
    for (const leg of m.legs) {
      // The vertices of the pieces flown on this leg, in the order flown.
      const flown = []
      for (let i = 0; i < seg.count; i++) {
        if (seg.leg[i] !== leg.index || !isMove(seg.kind[i])) continue
        if (!flown.length) flown.push([seg.x0[i], seg.y0[i], seg.z0[i]])
        flown.push([seg.x1[i], seg.y1[i], seg.z1[i]])
      }
      const planned = m.waypoints.slice(leg.from, leg.to + 1)
      expect(flown.length).toBe(planned.length)
      planned.forEach((wp, k) => {
        const [x, y] = frame.forward(wp.lng, wp.lat)
        expect(Math.hypot(flown[k][0] - x, flown[k][1] - y)).toBeLessThan(1e-6)
        expect(flown[k][2]).toBe(wp.alt)
      })
    }
  })

  it.each(ALL)('%s: legs are flown in the planned order, once each', name => {
    const tr = build(name)
    const order = []
    for (let i = 0; i < tr.segments.count; i++) {
      const leg = tr.segments.leg[i]
      if (leg >= 0 && order[order.length - 1] !== leg) order.push(leg)
    }
    expect(order).toEqual(missions[name].legs.map(l => l.index))
  })

  it.each(ALL)('%s: sampling at the start and end of a leg is on its first and last waypoint', name => {
    const m = missions[name]
    const tr = build(name)
    for (const leg of m.legs) {
      const a = tr.sample(tr.legTimes.t0[leg.index])
      const b = tr.sample(tr.legTimes.t1[leg.index])
      const first = m.waypoints[leg.from]
      const last = m.waypoints[leg.to]
      // The mission rounds coordinates to 1e-7 degree (about a centimetre).
      expect(Math.abs(a.lng - first.lng)).toBeLessThan(2e-7)
      expect(Math.abs(a.lat - first.lat)).toBeLessThan(2e-7)
      expect(a.alt).toBeCloseTo(first.alt, 6)
      expect(Math.abs(b.lng - last.lng)).toBeLessThan(2e-7)
      expect(Math.abs(b.lat - last.lat)).toBeLessThan(2e-7)
      expect(b.alt).toBeCloseTo(last.alt, 6)
    }
  })

  it.each(ALL)('%s: starts and ends on the ground at the takeoff point', name => {
    const m = missions[name]
    const tr = build(name)
    const start = tr.sample(0)
    expect(start.phaseKey).toBe('preflight')
    expect(start.alt).toBe(0)
    expect(start.speed).toBe(0)
    expect(Math.abs(start.lng - m.takeoff.lng)).toBeLessThan(2e-7)
    expect(Math.abs(start.lat - m.takeoff.lat)).toBeLessThan(2e-7)
    const end = tr.sample(tr.duration)
    expect(end.phaseKey).toBe('landed')
    expect(end.speed).toBe(0)
    if (m.params.finish_action !== 'land') {
      expect(end.alt).toBeCloseTo(0, 6)
      expect(Math.abs(end.lng - m.takeoff.lng)).toBeLessThan(2e-7)
      expect(Math.abs(end.lat - m.takeoff.lat)).toBeLessThan(2e-7)
    }
  })

  it('flies out at the first waypoint\'s height and back at the plan\'s return height', () => {
    // Terrain following: the survey ends lower than the highest point of the mission.
    const m = missions.area_terrain
    const tr = build('area_terrain')
    expect(m.stats.rth_altitude_m).toBeGreaterThan(m.waypoints.at(-1).alt + 10)
    const level = key => {
      const heights = new Set()
      for (const p of tr.phases.filter(q => q.key === key)) {
        for (let t = p.t0; t <= p.t1; t += 0.5) { const s = tr.sample(t); if (s.speed > 0.5) heights.add(Math.round(s.alt * 10) / 10) }
      }
      return [...heights]
    }
    expect(level('transit_out')).toEqual([m.waypoints[0].alt])
    expect(level('return')).toEqual([m.stats.rth_altitude_m])
    // A higher return height set in the plan is flown; a lower one never takes the aircraft below where it is.
    const high = JSON.parse(JSON.stringify(missions.area_flat))
    high.stats.rth_altitude_m = 150
    const up = buildTrajectory(high)
    expect(Math.max(...sweep(up, 0.5).filter(s => s.phaseKey === 'return').map(s => s.alt))).toBe(150)
    const low = JSON.parse(JSON.stringify(missions.area_flat))
    low.stats.rth_altitude_m = 40
    expect(Math.min(...sweep(buildTrajectory(low), 0.5).filter(s => s.phaseKey === 'return').map(s => s.alt))).toBe(100)
  })

  it('lands where the survey ends when the plan says so', () => {
    const m = missions.land_in_place
    const tr = build('land_in_place')
    const end = tr.sample(tr.duration)
    const last = m.waypoints.at(-1)
    expect(Math.abs(end.lng - last.lng)).toBeLessThan(2e-7)
    expect(Math.abs(end.lat - last.lat)).toBeLessThan(2e-7)
    expect(end.alt).toBeCloseTo(0, 6)
    expect(tr.phases.some(p => p.key === 'return')).toBe(false)
  })
})

describe('photo events', () => {
  it.each(ALL)('%s: one event per planned photo, leg by leg', name => {
    const m = missions[name]
    const tr = build(name)
    expect(tr.photos.count).toBe(m.stats.photo_count)
    const perLeg = new Array(m.legs.length).fill(0)
    for (let i = 0; i < tr.photos.count; i++) perLeg[tr.photos.leg[i]]++
    expect(perLeg).toEqual(m.legs.map(l => l.photos))
  })

  it.each(ALL)('%s: taken where the per-photo export puts its waypoints', name => {
    // refs.exposures comes from the exporter's "photo at every waypoint" route.
    const m = missions[name]
    const tr = build(name)
    const expected = refs.exposures[name]
    expect(tr.photos.count).toBe(expected.length)
    const frame = LocalFrame.ofMission(m)
    let wrongLeg = 0
    let offPosition = 0
    let offHeight = 0
    for (let i = 0; i < expected.length; i++) {
      const [leg, lng, lat, alt] = expected[i]
      const [x, y] = frame.forward(lng, lat)
      if (tr.photos.leg[i] !== leg) wrongLeg++
      offPosition = Math.max(offPosition, Math.hypot(tr.photos.x[i] - x, tr.photos.y[i] - y))
      offHeight = Math.max(offHeight, Math.abs(tr.photos.alt[i] - alt))
    }
    expect(wrongLeg).toBe(0)
    // The export rounds to 1e-7 degree (about a centimetre) and 0.1 m.
    expect(offPosition).toBeLessThan(0.02)
    expect(offHeight).toBeLessThanOrEqual(0.051)
  })

  it.each(ALL)('%s: in time order, on a survey line, at the position the aircraft is then', name => {
    const tr = build(name)
    const spacing = missions[name].camera.photo_spacing_m
    let outOfOrder = 0
    let offTrack = 0
    let offHeight = 0
    let widest = 0
    let notOnALine = 0
    const s = {}
    for (let i = 0; i < tr.photos.count; i++) {
      if (i && tr.photos.t[i] < tr.photos.t[i - 1]) outOfOrder++
      tr.sample(tr.photos.t[i], s)
      offTrack = Math.max(offTrack, Math.hypot(s.x - tr.photos.x[i], s.y - tr.photos.y[i]))
      offHeight = Math.max(offHeight, Math.abs(s.alt - tr.photos.alt[i]))
      // The first and last photo of a line are taken at its very ends, where the phase changes.
      if (s.leg !== tr.photos.leg[i] && tr.sample(tr.photos.t[i] + 1e-3).leg !== tr.photos.leg[i]
        && tr.sample(tr.photos.t[i] - 1e-3).leg !== tr.photos.leg[i]) notOnALine++
      if (i && tr.photos.leg[i] === tr.photos.leg[i - 1]) {
        widest = Math.max(widest, Math.hypot(tr.photos.x[i] - tr.photos.x[i - 1], tr.photos.y[i] - tr.photos.y[i - 1]))
      }
    }
    expect(outOfOrder).toBe(0)
    expect(offTrack).toBeLessThan(1e-4)
    expect(offHeight).toBeLessThan(1e-4)
    expect(notOnALine).toBe(0)
    expect(widest).toBeLessThanOrEqual(spacing + 1e-6)              // a bend can only shorten the chord
    expect(widest).toBeGreaterThan(spacing * 0.99)
  })

  it('the photo counter follows the clock', () => {
    const tr = build('area_flat')
    expect(tr.sample(0).photos).toBe(0)
    expect(tr.sample(tr.duration).photos).toBe(133)
    const t = tr.photos.t[40]
    expect(tr.sample(t - 1e-6).photos).toBe(40)
    expect(tr.sample(t).photos).toBe(41)
    expect(tr.photosAt(t)).toBe(41)
    expect(tr.sample(t + 0.2).sincePhoto).toBeCloseTo(0.2, 6)
    expect(tr.sample(0).sincePhoto).toBe(Infinity)
    // On a line the interval is the planned one.
    expect(tr.photos.t[41] - tr.photos.t[40]).toBeCloseTo(missions.area_flat.camera.photo_spacing_m / 10, 3)
  })

  it('the camera is on along the lines and off everywhere else', () => {
    const tr = build('area_flat')
    for (const s of sweep(tr, 0.5)) expect(s.capturing).toBe(s.phaseKey === 'survey')
  })
})

describe('flight dynamics', () => {
  it.each(ALL)('%s: position is continuous - no teleporting in the air', name => {
    const tr = build(name)
    const dt = 0.05
    const top = Math.max(tr.speeds.survey, tr.speeds.transit, tr.speeds.ascent, tr.speeds.descent)
    let prev = { ...tr.sample(0) }
    let worst = 0
    const s = {}
    for (let t = dt; t <= tr.duration; t += dt) {
      tr.sample(t, s)
      // A battery change may move the aircraft (it is carried); flying never jumps.
      if (s.airborne && prev.airborne && s.flight === prev.flight) {
        worst = Math.max(worst, Math.hypot(s.x - prev.x, s.y - prev.y, s.alt - prev.alt))
      }
      prev = { ...s }
    }
    expect(worst).toBeGreaterThan(0)
    expect(worst).toBeLessThanOrEqual(Math.hypot(top, top) * dt + 1e-6)
  })

  it.each(ALL)('%s: never faster than planned, never beyond the published climb and descent rates', name => {
    const tr = build(name)
    const drone = missions[name].equipment.drone
    let fastest = 0
    let fastestOnLine = 0
    let up = 0
    let down = 0
    const s = {}
    for (let t = 0; t <= tr.duration; t += 0.1) {
      tr.sample(t, s)
      fastest = Math.max(fastest, s.speed)
      if (s.phaseKey === 'survey' || s.phaseKey === 'turn') fastestOnLine = Math.max(fastestOnLine, s.speed)
      up = Math.max(up, s.verticalSpeed)
      down = Math.max(down, -s.verticalSpeed)
    }
    expect(fastest).toBeLessThanOrEqual(Math.max(tr.speeds.survey, tr.speeds.transit) + 1e-6)
    expect(fastestOnLine).toBeCloseTo(tr.speeds.survey, 6)            // and it does reach the planned speed
    expect(up).toBeLessThanOrEqual(drone.max_ascent_ms + 1e-6)
    expect(down).toBeLessThanOrEqual(drone.max_descent_ms + 1e-6)
    expect(up).toBeGreaterThan(0)
  })

  it.each(ALL)('%s: speed changes no faster than the acceleration allows', name => {
    const tr = build(name)
    const dt = 0.04
    let prevSpeed = 0
    let prevFlight = 0
    let worst = 0
    const s = {}
    for (let t = dt; t <= tr.duration; t += dt) {
      tr.sample(t, s)
      if (s.flight === prevFlight) worst = Math.max(worst, Math.abs(s.speed - prevSpeed))
      prevSpeed = s.speed
      prevFlight = s.flight
    }
    expect(worst).toBeLessThanOrEqual(DYNAMICS.accel * dt + 1e-6)
    expect(worst).toBeGreaterThan(DYNAMICS.accel * dt * 0.9)          // and it does use it
  })

  it('a multirotor stops at the end of every line, turns on the spot and goes on', () => {
    const m = missions.area_flat
    const tr = build('area_flat')
    for (const leg of m.legs) {
      expect(tr.sample(tr.legTimes.t0[leg.index]).speed).toBeCloseTo(0, 6)
      expect(tr.sample(tr.legTimes.t1[leg.index]).speed).toBeCloseTo(0, 6)
    }
    // Mid-line it is at the planned speed, pointing along the line, level.
    const mid = tr.sample((tr.legTimes.t0[2] + tr.legTimes.t1[2]) / 2)
    expect(mid.speed).toBeCloseTo(10, 6)
    expect(mid.heading).toBeCloseTo(m.legs[2].heading, 3)
    expect(mid.verticalSpeed).toBe(0)
    expect(mid.roll).toBeCloseTo(0, 6)
    expect(mid.pitch).toBeLessThan(-5)                             // leaning into the airflow
    // Between two lines it yaws while standing still.
    const turn = tr.phases.find(p => p.key === 'turn' && p.t0 > tr.legTimes.t1[0])
    const a = tr.sample(turn.t0 + 0.01)
    const b = tr.sample(turn.t0 + 1)
    expect(a.speed).toBe(0)
    expect(Math.abs(angleDiff(a.heading, b.heading))).toBeGreaterThan(20)
  })

  it('pitches forward to accelerate and back to brake', () => {
    const tr = build('area_flat')
    const t0 = tr.legTimes.t0[1]
    const t1 = tr.legTimes.t1[1]
    const speedingUp = tr.sample(t0 + 1.5)
    const cruising = tr.sample((t0 + t1) / 2)
    const braking = tr.sample(t1 - 1.5)
    expect(speedingUp.pitch).toBeLessThan(cruising.pitch - 5)
    expect(braking.pitch).toBeGreaterThan(cruising.pitch + 5)
    expect(Math.abs(speedingUp.pitch)).toBeLessThanOrEqual(35)
  })

  it('heading turns smoothly: no step larger than the yaw can make', () => {
    for (const name of ['area_flat', 'corridor', 'fixed_wing']) {
      const tr = build(name)
      const dt = 0.05
      let prevHeading = tr.sample(0).heading
      let worst = 0
      const s = {}
      for (let t = dt; t <= tr.duration; t += dt) {
        tr.sample(t, s)
        if (s.phaseKey !== 'preflight') worst = Math.max(worst, Math.abs(angleDiff(prevHeading, s.heading)))
        prevHeading = s.heading
      }
      expect(worst).toBeLessThan(200 * dt)                            // well under 200 °/s
    }
  })

  it('slows for a bend in a corridor instead of cutting it', () => {
    const tr = build('corridor')
    const m = missions.corridor
    const frame = LocalFrame.ofMission(m)
    // The bend is the via point of the first pass.
    const via = m.waypoints.find(w => w.role === 'via')
    const [vx, vy] = frame.forward(via.lng, via.lat)
    let nearest = null
    for (const s of sweep(tr, 0.05)) {
      const d = Math.hypot(s.x - vx, s.y - vy)
      if (s.leg === via.leg && (!nearest || d < nearest.d)) nearest = { d, speed: s.speed }
    }
    expect(nearest.d).toBeLessThan(0.3)                            // flown through, not cut
    expect(nearest.speed).toBeLessThan(6)                          // and slower than the 10 m/s of the line
    expect(nearest.speed).toBeGreaterThan(0.5)                     // but not a stop
  })

  it('climbs with the terrain along a line, as the waypoints say', () => {
    const tr = build('area_terrain')
    const climbs = sweep(tr, 0.2).filter(s => s.phaseKey === 'survey' && Math.abs(s.verticalSpeed) > 1)
    expect(climbs.length).toBeGreaterThan(50)
    expect(Math.max(...climbs.map(s => s.verticalSpeed))).toBeCloseTo(missions.area_terrain.stats.max_climb_ms, 0)
  })

  it('slows down where the planned speed would need a climb the aircraft cannot do', () => {
    // A 1:1 slope at 10 m/s asks for 10 m/s of climb from an aircraft that does 6.
    const m = syntheticMission({ lines: 2, lineLength: 200, vias: 1, altitude: (x) => 60 + Math.max(0, x - 100) })
    const tr = buildTrajectory(m)
    const steepest = sweep(tr, 0.1).filter(s => s.phaseKey === 'survey' && s.verticalSpeed > 0.5)
    expect(Math.max(...steepest.map(s => s.verticalSpeed))).toBeLessThanOrEqual(6 + 1e-6)
    expect(Math.max(...steepest.map(s => s.speed))).toBeLessThanOrEqual(6 + 1e-6)
    expect(tr.notes.join(' ')).toMatch(/flown slower than planned/)
  })

  it('flies the aircraft\'s own speed limit when the plan asks for more, and says so', () => {
    const m = syntheticMission({ speed: 18 })                      // the test drone's mission limit is 15 m/s
    const tr = buildTrajectory(m)
    expect(tr.speeds.survey).toBe(15)
    expect(Math.max(...sweep(tr, 0.5).map(s => s.speed))).toBeLessThanOrEqual(15 + 1e-6)
    expect(tr.notes[0]).toMatch(/18 m\/s .* 15 m\/s mission limit/)
  })

  it('uses the planner\'s default rates when the aircraft publishes none, and says so', () => {
    const m = syntheticMission()
    m.equipment.drone.max_ascent_ms = null
    m.equipment.drone.max_descent_ms = null
    const tr = buildTrajectory(m)
    expect([tr.speeds.ascent, tr.speeds.descent]).toEqual([3, 2])
    expect(tr.notes.join(' ')).toMatch(/no climb or descent rate/)
  })
})

describe('fixed-wing and VTOL', () => {
  it.each(['fixed_wing', 'vtol'])('%s: keeps flying - no stop between takeoff and landing', name => {
    const tr = build(name)
    const onRoute = sweep(tr, 0.25).filter(s => s.phaseKey === 'survey' || s.phaseKey === 'turn')
    expect(Math.min(...onRoute.map(s => s.speed))).toBeGreaterThanOrEqual((missions[name].equipment.drone.min_speed_ms || 1) - 1e-6)
  })

  it.each(['fixed_wing', 'vtol'])('%s: turns no tighter than its turn radius, banking as it does', name => {
    const tr = build(name)
    const r = missions[name].equipment.drone.min_turn_radius_m
    expect(tr.speeds.turnRadius).toBe(r)
    // Radius of the track from three consecutive samples, a second apart: a
    // curve is drawn as short chords, so the samples have to span a few of them.
    const turning = sweep(tr, 1).filter(s => s.phaseKey === 'turn')
    let tightest = Infinity
    for (let k = 1; k < turning.length - 1; k++) {
      const [a, b, c] = [turning[k - 1], turning[k], turning[k + 1]]
      if (c.t - a.t > 2.5) continue
      const cross = Math.abs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x))
      const abc = Math.hypot(b.x - a.x, b.y - a.y) * Math.hypot(c.x - b.x, c.y - b.y) * Math.hypot(c.x - a.x, c.y - a.y)
      if (cross > 1e-6) tightest = Math.min(tightest, abc / (2 * cross))
    }
    expect(tightest).toBeGreaterThan(r * 0.9)
    expect(tightest).toBeLessThan(r * 1.5)
    const bank = Math.max(...sweep(tr, 0.25).filter(s => s.phaseKey === 'turn').map(s => Math.abs(s.roll)))
    const expected = Math.atan(tr.speeds.survey ** 2 / (9.80665 * r)) * 180 / Math.PI
    expect(bank).toBeGreaterThan(expected * 0.7)
    expect(bank).toBeLessThanOrEqual(45)
  })

  it('a fixed-wing climbs along its way out and descends along its way home', () => {
    const tr = build('fixed_wing')
    const states = sweep(tr, 0.5)
    const takeoff = states.filter(s => s.phaseKey === 'takeoff')
    expect(takeoff.length).toBeGreaterThan(10)
    expect(takeoff.every(s => s.speed > 0 || s.alt === 0)).toBe(true)          // no hovering climb
    expect(Math.max(...takeoff.map(s => s.verticalSpeed))).toBeGreaterThan(1)
    const landing = states.filter(s => s.phaseKey === 'landing')
    expect(Math.min(...landing.map(s => s.verticalSpeed))).toBeLessThan(-1)
    expect(landing.some(s => s.speed > 5)).toBe(true)
  })

  it('a VTOL lifts off and lands vertically', () => {
    const tr = build('vtol')
    const states = sweep(tr, 0.5)
    for (const key of ['takeoff', 'landing']) {
      const phase = states.filter(s => s.phaseKey === key)
      expect(phase.length).toBeGreaterThan(5)
      expect(Math.max(...phase.map(s => s.speed))).toBe(0)
    }
  })

  it('says its turns differ from the plan\'s approximation', () => {
    expect(build('fixed_wing').notes.join(' ')).toMatch(/curves of 50 m radius/)
    expect(build('area_flat').notes.join(' ')).not.toMatch(/curves/)
  })

  it('assumes a turn radius from a bank angle when the profile has none', () => {
    const m = syntheticMission({ airframe: 'fixed-wing', lines: 3, spacing: 120 })
    m.equipment.drone.min_turn_radius_m = null
    const tr = buildTrajectory(m)
    expect(tr.speeds.turnRadius).toBeCloseTo(100 / (9.80665 * Math.tan(Math.PI / 6)), 6)
    expect(tr.notes.join(' ')).toMatch(/assumed: no turn radius is published/)
  })
})

describe('agreement with the planner\'s estimates', () => {
  it.each(MULTIROTOR)('%s: flight time within 4 %% of the plan, distance within a metre per flight', name => {
    const m = missions[name]
    const tr = build(name)
    expect(tr.flights.length).toBe(m.flights.length)
    tr.flights.forEach((f, i) => {
      const planned = m.flights[i]
      expect(Math.abs(f.distance - planned.distance_m)).toBeLessThan(1)
      expect(Math.abs(f.duration - planned.duration_s) / planned.duration_s).toBeLessThan(0.04)
      expect(f.photos).toBe(planned.photos)
      expect(Math.abs(f.batteryUsed - planned.battery_percent)).toBeLessThan(2)
    })
    expect(Math.abs(tr.distance - m.stats.total_distance_m)).toBeLessThan(1 * m.flights.length)
  })

  it('differs from the plan for curved turns, in the direction the note says', () => {
    const m = missions.fixed_wing
    const tr = build('fixed_wing')
    expect(tr.distance).toBeGreaterThan(m.stats.total_distance_m)
    expect(tr.distance).toBeLessThan(m.stats.total_distance_m * 1.3)
  })
})

describe('multi-battery missions', () => {
  const tr = build('multi_battery')
  const m = missions.multi_battery

  it('flies one flight per battery, each over its own legs', () => {
    expect(tr.flights.map(f => [f.legFrom, f.legTo])).toEqual(m.flights.map(f => [f.leg_from, f.leg_to]))
    for (const f of tr.flights) {
      expect(tr.sample(f.t0).phaseKey).toBe('preflight')
      expect(tr.sample(f.t1 - 0.01).phaseKey).toBe('landed')
      expect(tr.sample(f.t0).flight).toBe(f.index)
    }
  })

  it('every flight takes off from and returns to the takeoff point', () => {
    for (const f of tr.flights) {
      for (const t of [f.t0, f.t1]) {
        const s = tr.sample(t === f.t1 ? t - 0.01 : t)
        expect(Math.hypot(s.x - tr.takeoff.x, s.y - tr.takeoff.y)).toBeLessThan(1e-6)
        expect(s.alt).toBeCloseTo(0, 6)
      }
    }
  })

  it('puts a battery change on the ground between flights, outside the flight time', () => {
    const swaps = tr.phases.filter(p => p.key === 'battery')
    expect(swaps.length).toBe(2)
    for (const p of swaps) {
      expect(p.t1 - p.t0).toBeCloseTo(DYNAMICS.batterySwap, 6)
      const s = tr.sample((p.t0 + p.t1) / 2)
      expect([s.airborne, s.speed, s.alt]).toEqual([false, 0, 0])
    }
    expect(tr.duration).toBeCloseTo(tr.flightTime + 2 * DYNAMICS.batterySwap, 6)
    expect(tr.flightTime).toBeCloseTo(tr.flights.reduce((s, f) => s + f.duration, 0), 9)
  })

  it('drains the battery through a flight and starts the next one full', () => {
    const [a, b] = tr.flights
    expect(tr.sample(a.t0).battery).toBe(100)
    const half = tr.sample((a.t0 + a.t1) / 2).battery
    const end = tr.sample(a.t1 - 0.01).battery
    expect(half).toBeLessThan(100)
    expect(end).toBeLessThan(half)
    expect(100 - end).toBeCloseTo(a.batteryUsed, 1)
    // The used share is the planner's model: time over rated endurance.
    expect(a.batteryUsed).toBeCloseTo(a.duration / (45 * 60 * 0.8) * 100, 6)
    // During the change the old battery's reading stays; the new flight starts at 100 %.
    expect(tr.sample(a.t1 + 30).battery).toBeCloseTo(end, 1)
    expect(tr.sample(b.t0).battery).toBe(100)
    // Battery only ever falls within a flight.
    let prev = 101
    for (let t = a.t0; t < a.t1; t += 7) { const v = tr.sample(t).battery; expect(v).toBeLessThanOrEqual(prev); prev = v }
  })

  it('stays within the usable time the plan allowed, and flags a flight that would not', () => {
    expect(tr.flights.every(f => !f.exceedsUsable)).toBe(true)
    expect(tr.battery.usable).toBe(m.stats.usable_flight_time_s)
    expect(tr.battery.reservePercent).toBe(25)
    expect(sweep(tr, 20).some(s => s.belowReserve)).toBe(false)
    // The same route with a quarter of the endurance does not fit.
    const short = JSON.parse(JSON.stringify(m))
    short.equipment.drone.max_flight_time_min = 12
    short.stats.usable_flight_time_s = 12 * 60 * 0.8 * 0.75
    const over = buildTrajectory(short)
    expect(over.flights.every(f => f.exceedsUsable)).toBe(true)
    expect(over.sample(over.flights[0].t1 - 1).belowReserve).toBe(true)
    expect(over.sample(over.flights[0].t1 - 1).battery).toBe(0)
  })

  it('counts distance and time per flight', () => {
    const b = tr.flights[1]
    const s = tr.sample(b.t0 + 100)
    expect(s.flightTime).toBeCloseTo(100, 6)
    expect(s.flightDistance).toBeGreaterThan(0)
    expect(s.distance).toBeCloseTo(tr.flights[0].distance + s.flightDistance, 6)
  })

  it('hover-at-the-end is shown, then the return the plan budgets', () => {
    const hover = JSON.parse(JSON.stringify(missions.area_flat))
    hover.params.finish_action = 'hover'
    const h = buildTrajectory(hover)
    const keys = h.phases.map(p => p.key)
    expect(keys.indexOf('hover')).toBeGreaterThan(keys.lastIndexOf('survey'))
    expect(keys.indexOf('return')).toBeGreaterThan(keys.indexOf('hover'))
    expect(h.duration).toBeCloseTo(build('area_flat').duration + DYNAMICS.hoverSeconds, 6)
  })
})

describe('terrain and altitude references', () => {
  it.each(['area_terrain', 'area_slope'])('%s: height above ground at each waypoint is the mission\'s own', name => {
    const m = missions[name]
    const tr = build(name)
    expect(tr.takeoff.ground).toBeCloseTo(m.takeoff.ground_m, 1)
    let compared = 0
    for (const leg of m.legs) {
      for (const [t, wp] of [[tr.legTimes.t0[leg.index], m.waypoints[leg.from]], [tr.legTimes.t1[leg.index], m.waypoints[leg.to]]]) {
        const s = tr.sample(t)
        if (wp.agl === null) continue
        expect(s.groundKnown).toBe(true)
        // Mission figures are rounded to 0.1 m, the grid to 0.01 m.
        expect(Math.abs(s.agl - wp.agl)).toBeLessThan(0.16)
        expect(Math.abs(s.ground - wp.ground)).toBeLessThan(0.07)
        expect(s.amsl).toBeCloseTo(tr.takeoff.ground + wp.alt, 6)
        compared++
      }
    }
    expect(compared).toBeGreaterThan(10)
  })

  it('terrain following holds the planned height above ground along the lines', () => {
    const m = missions.area_terrain
    const tr = build('area_terrain')
    const agl = sweep(tr, 0.2).filter(s => s.phaseKey === 'survey').map(s => s.agl)
    // Within the plan's following tolerance of the planned 80 m, everywhere on the grid -
    // including between the points the planner's own analysis sampled.
    const tolerance = m.terrain.tolerance_m
    expect(tolerance).toBe(4)
    expect(Math.min(...agl)).toBeGreaterThan(80 - tolerance - 0.5)
    expect(Math.max(...agl)).toBeLessThan(80 + tolerance + 0.5)
    // The plan's range is from coarser sampling, so it lies inside the simulated one.
    expect(Math.min(...agl)).toBeLessThanOrEqual(m.terrain.agl_min_m + 0.1)
    expect(Math.max(...agl)).toBeGreaterThanOrEqual(m.terrain.agl_max_m - 0.1)
  })

  it('constant altitude over a slope: height above ground varies as the plan computed', () => {
    const m = missions.area_slope
    const tr = build('area_slope')
    const agl = sweep(tr, 0.2).filter(s => s.phaseKey === 'survey' && s.agl !== null).map(s => s.agl)
    expect(Math.min(...agl)).toBeCloseTo(m.terrain.agl_min_m, 0)
    expect(Math.max(...agl)).toBeCloseTo(m.terrain.agl_max_m, 0)
    // All survey samples share one height above takeoff.
    expect(new Set(sweep(tr, 1).filter(s => s.phaseKey === 'survey').map(s => s.alt)).size).toBe(1)
  })

  it('heights are relative to the takeoff point: on the ground there, both are zero', () => {
    const tr = build('area_terrain')
    const s = tr.sample(0)
    expect([s.alt, s.agl]).toEqual([0, 0])
    expect(s.amsl).toBe(tr.takeoff.ground)
  })

  it('without elevation data the ground is unknown, not assumed', () => {
    const tr = build('area_flat')
    const s = tr.sample(tr.legTimes.t0[3] + 5)
    expect(tr.takeoff.ground).toBeNull()
    expect([s.ground, s.agl, s.amsl, s.groundKnown]).toEqual([null, null, null, false])
    expect(s.aglAssumed).toBe(s.alt)                               // what the camera geometry falls back to
    expect(s.alt).toBe(100)
  })

  it('falls back to the ground recorded at the waypoints where the grid has no answer', () => {
    // The mission knows its waypoints' ground; the grid is not available.
    const m = missions.area_slope
    const tr = buildTrajectory(m, { terrain: null })
    expect(tr.takeoff.ground).toBe(m.takeoff.ground_m)
    const leg = m.legs[1]
    const mid = tr.sample((tr.legTimes.t0[1] + tr.legTimes.t1[1]) / 2)
    const expected = (m.waypoints[leg.from].ground + m.waypoints[leg.to].ground) / 2
    expect(mid.ground).toBeCloseTo(expected, 3)
    expect(mid.agl).toBeCloseTo(m.takeoff.ground_m + mid.alt - expected, 3)
    // Away from the lines (the transit) nothing is known.
    expect(tr.sample(tr.phases.find(p => p.key === 'transit_out').t0 + 2).agl).toBeNull()
  })

  it('a hole in the grid is unknown ground, in flight too', () => {
    const tr = build('area_slope')
    const frame = LocalFrame.ofMission(missions.area_slope)
    const [lng, lat] = frame.inverse(300, 250)                     // inside the fixture's hole
    expect(tr.terrain.elevation(lng, lat)).toBeNull()
    expect(tr.groundAt(300, 250)).toBeNull()
  })

  it('lands on the ground where it lands in place, not at the takeoff height', () => {
    const m = syntheticMission({ lines: 2, finish: 'land' })
    const grid = syntheticTerrain(m, (x) => 500 + 0.05 * x)
    const tr = buildTrajectory(m, { terrain: createTerrain(grid) })
    const end = tr.sample(tr.duration)
    expect(end.agl).toBeCloseTo(0, 1)
    expect(end.alt).toBeCloseTo(tr.groundAt(end.x, end.y) - tr.takeoff.ground, 2)
  })
})

describe('determinism and safety of the data', () => {
  it.each(ALL)('%s: the mission is never modified', name => {
    const frozen = deepFreeze(JSON.parse(JSON.stringify(missions[name])))
    const before = JSON.stringify(frozen)
    const tr = buildTrajectory(frozen, { terrain: createTerrain(fixture.terrain[name]) })
    for (let t = 0; t <= tr.duration; t += tr.duration / 50) tr.sample(t)
    expect(JSON.stringify(frozen)).toBe(before)
  })

  it('two builds of the same mission are identical', () => {
    const a = build('area_terrain')
    const b = build('area_terrain')
    expect(a.duration).toBe(b.duration)
    expect(Array.from(a.segments.t0)).toEqual(Array.from(b.segments.t0))
    expect(Array.from(a.photos.t)).toEqual(Array.from(b.photos.t))
    for (const t of [0, 13.37, 250, a.duration]) expect(a.sample(t)).toEqual(b.sample(t))
  })

  it('a sample depends only on the time, not on what was sampled before', () => {
    const tr = build('multi_battery')
    const times = [1200.5, 3.2, 4000, 1200.5, 0, tr.duration, 77.7]
    const first = times.map(t => ({ ...tr.sample(t) }))
    // Again in another order, after sweeping the whole timeline, and into a reused object.
    sweep(tr, 5)
    const reused = {}
    const second = [...times].reverse().map(t => ({ ...tr.sample(t, reused) })).reverse()
    expect(second).toEqual(first)
    expect(first[0]).toEqual(first[3])
  })

  it('clamps times outside the timeline', () => {
    const tr = build('area_flat')
    expect(tr.sample(-5)).toEqual(tr.sample(0))
    expect(tr.sample(1e9)).toEqual(tr.sample(tr.duration))
    expect(tr.sample(NaN).t).toBe(0)
  })

  it('refuses a mission without a route', () => {
    expect(() => buildTrajectory(null)).toThrow(SimulationError)
    expect(() => buildTrajectory({ waypoints: [], legs: [] })).toThrow(SimulationError)
    expect(() => buildTrajectory({ ...missions.area_flat, waypoints: missions.area_flat.waypoints.slice(0, 1) })).toThrow(SimulationError)
  })

  it('survives a mission with repeated points and no flights list', () => {
    const m = syntheticMission({ lines: 2, vias: 1 })
    m.waypoints[1] = { ...m.waypoints[0], index: 1, role: 'via' }      // a duplicate
    delete m.flights
    const tr = buildTrajectory(m)
    expect(tr.flights.length).toBe(1)
    expect(Number.isFinite(tr.duration)).toBe(true)
    expect(tr.photos.count).toBe(m.stats.photo_count)
  })

  it('notes an assumed takeoff point', () => {
    const m = JSON.parse(JSON.stringify(missions.area_flat))
    m.takeoff.assumed = true
    expect(buildTrajectory(m).notes.join(' ')).toMatch(/No takeoff point is set/)
  })
})

describe('timeline structure', () => {
  it('phases tile the timeline without gaps, in a sensible order', () => {
    for (const name of ALL) {
      const tr = build(name)
      expect(tr.phases[0].t0).toBe(0)
      expect(tr.phases.at(-1).t1).toBeCloseTo(tr.duration, 6)
      for (let i = 1; i < tr.phases.length; i++) expect(tr.phases[i].t0).toBeCloseTo(tr.phases[i - 1].t1, 6)
      const keys = tr.phases.filter(p => p.flight === 0).map(p => p.key)
      expect(keys[0]).toBe('preflight')
      expect(keys.indexOf('takeoff')).toBeLessThan(keys.indexOf('survey'))
      expect(keys.lastIndexOf('survey')).toBeLessThan(keys.lastIndexOf('landing'))
    }
  })

  it('every phase id has a label and says whether the aircraft is flying', () => {
    expect(PHASE_INFO.length).toBe(Object.keys(PHASE).length)
    expect(PHASE_INFO[PHASE.SURVEY]).toMatchObject({ key: 'survey', airborne: true })
    expect(PHASE_INFO[PHASE.BATTERY].airborne).toBe(false)
  })

  it('distance grows monotonically and maps back to time', () => {
    const tr = build('area_terrain')
    let prev = -1
    for (const s of sweep(tr, 0.5)) { expect(s.distance).toBeGreaterThanOrEqual(prev - 1e-9); prev = s.distance }
    expect(tr.sample(tr.duration).distance).toBeCloseTo(tr.distance, 6)
    for (const d of [0, 10, 333.3, 2500, tr.distance]) {
      const t = tr.timeAtDistance(d)
      expect(tr.sample(t).distance).toBeCloseTo(d, 4)
    }
    // The first moment at that distance: just before, less had been flown.
    const t = tr.timeAtDistance(2500)
    expect(tr.sample(t - 0.05).distance).toBeLessThan(2500)
  })

  it('ground preparation and landing take the planner\'s 20 s overhead', () => {
    const tr = build('area_flat')
    const of = key => tr.phases.filter(p => p.key === key).reduce((s, p) => s + (p.t1 - p.t0), 0)
    expect(of('preflight') + of('landed')).toBeCloseTo(20, 6)
  })

  it('options override the motion model', () => {
    const slow = build('area_flat', { dynamics: { accel: 1 } })
    expect(slow.duration).toBeGreaterThan(build('area_flat').duration)
    const none = build('multi_battery', { dynamics: { batterySwap: 0 } })
    expect(none.duration).toBeCloseTo(none.flightTime, 6)
  })
})
