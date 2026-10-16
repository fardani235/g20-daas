import { describe, it, expect } from 'vitest'
import {
  cornerSpeed, lastAtOrBefore, planSpeeds, profileAt, profileTimeAt, segmentProfile, smoothstep,
} from './kinematics'
import { angleToBearing, bearingToAngle, dubinsPath, loiterCircles, sampleDubins } from './dubins'

describe('segmentProfile', () => {
  it('accelerates, cruises and brakes: a 400 m line at 10 m/s and 2.5 m/s² takes L/v + v/a', () => {
    const p = segmentProfile(400, 0, 0, 10, 2.5)
    expect(p.vp).toBe(10)
    expect(p.ta).toBe(4)
    expect(p.td).toBe(4)
    expect(p.da).toBe(20)
    expect(p.T).toBeCloseTo(400 / 10 + 10 / 2.5, 9)
  })

  it('never reaches cruise on a short hop', () => {
    const p = segmentProfile(40, 0, 0, 10, 2.5)
    expect(p.vp).toBeCloseTo(Math.sqrt(2.5 * 40), 9)             // 10 m/s would need 40 m just to speed up and stop
    expect(p.tc).toBe(0)
    expect(p.T).toBeCloseTo(2 * Math.sqrt(40 / 2.5), 9)
  })

  it('carries speed through: no braking between two pieces of one line', () => {
    const p = segmentProfile(100, 10, 10, 10, 2.5)
    expect([p.ta, p.td]).toEqual([0, 0])
    expect(p.T).toBe(10)
  })

  it('pulls an unreachable exit speed to what the distance allows', () => {
    const up = segmentProfile(5, 0, 10, 10, 2.5)                  // 5 m is not enough to reach 10 m/s
    expect(up.v1).toBeCloseTo(5, 9)
    const down = segmentProfile(5, 10, 0, 10, 2.5)                // nor to stop from it
    expect(down.v1).toBeCloseTo(Math.sqrt(75), 9)
  })

  it('a piece with no length takes no time', () => {
    expect(segmentProfile(0, 3, 3, 10, 2.5).T).toBe(0)
  })

  it('position and time are inverses of each other, everywhere', () => {
    for (const [d, v0, v1, vmax] of [[400, 0, 0, 10], [40, 0, 0, 10], [120, 4, 7, 12], [60, 9, 2, 9], [300, 0, 15, 15]]) {
      const p = segmentProfile(d, v0, v1, vmax, 2.5)
      expect(profileAt(p, 0).s).toBe(0)
      expect(profileAt(p, p.T).s).toBeCloseTo(d, 9)
      expect(profileAt(p, p.T).v).toBeCloseTo(p.v1, 9)
      let last = -1
      for (let k = 0; k <= 200; k++) {
        const t = (k / 200) * p.T
        const { s, v } = profileAt(p, t)
        expect(s).toBeGreaterThanOrEqual(last - 1e-9)             // never goes backwards
        expect(v).toBeLessThanOrEqual(vmax + 1e-9)                // never faster than allowed
        expect(profileTimeAt(p, s)).toBeCloseTo(t, 6)
        last = s
      }
    }
  })

  it('speed is continuous and its slope is the acceleration', () => {
    const p = segmentProfile(300, 2, 5, 12, 2.5)
    const dt = 1e-3
    for (let t = dt; t < p.T - dt; t += 0.05) {
      const a = (profileAt(p, t + dt).v - profileAt(p, t - dt).v) / (2 * dt)
      expect(Math.abs(a)).toBeLessThanOrEqual(2.5 + 1e-6)
      const v = (profileAt(p, t + dt).s - profileAt(p, t - dt).s) / (2 * dt)
      expect(v).toBeCloseTo(profileAt(p, t).v, 2)
    }
  })
})

describe('planSpeeds', () => {
  it('starts and ends at rest and cruises in between', () => {
    const v = planSpeeds([100, 100, 100], [10, 10, 10], null, 2.5)
    expect(Array.from(v)).toEqual([0, 10, 10, 0])
  })

  it('slows in time for a corner and for a slower piece ahead', () => {
    const lengths = [200, 10, 200]
    const v = planSpeeds(lengths, [10, 10, 4], [Infinity, Infinity, 2, Infinity], 2.5)
    expect(v[2]).toBe(2)                                          // the corner's limit
    // One piece back the aircraft is already slowing: no faster than it can still brake from.
    expect(v[1]).toBeCloseTo(Math.sqrt(2 * 2 + 2 * 2.5 * 10), 9)
    for (let i = 0; i < lengths.length; i++) {
      expect(Math.abs(v[i + 1] ** 2 - v[i] ** 2)).toBeLessThanOrEqual(2 * 2.5 * lengths[i] + 1e-9)
    }
  })

  it('never enters a piece faster than that piece allows', () => {
    const v = planSpeeds([50, 50], [12, 3], null, 2.5, 0, 0)
    expect(v[1]).toBeLessThanOrEqual(3)
  })
})

describe('cornerSpeed', () => {
  it('is unlimited straight on and walking pace at a right angle', () => {
    expect(cornerSpeed(0, 2.5, 0.8)).toBe(Infinity)
    expect(cornerSpeed(90, 2.5, 0.8)).toBeCloseTo(2.5 * 0.8 / (2 * Math.SQRT1_2), 9)
    expect(cornerSpeed(-90, 2.5, 0.8)).toBe(cornerSpeed(90, 2.5, 0.8))
    expect(cornerSpeed(10, 2.5, 0.8)).toBeGreaterThan(cornerSpeed(45, 2.5, 0.8))
    expect(cornerSpeed(180, 2.5, 0.8)).toBeCloseTo(1, 9)
  })
})

describe('helpers', () => {
  it('smoothstep eases between 0 and 1', () => {
    expect([smoothstep(-1), smoothstep(0), smoothstep(0.5), smoothstep(1), smoothstep(2)]).toEqual([0, 0, 0.5, 1, 1])
  })

  it('lastAtOrBefore is a binary search', () => {
    const a = Float64Array.from([0, 1, 1, 4, 9])
    expect(lastAtOrBefore(a, -1)).toBe(-1)
    expect(lastAtOrBefore(a, 0)).toBe(0)
    expect(lastAtOrBefore(a, 1)).toBe(2)
    expect(lastAtOrBefore(a, 5)).toBe(3)
    expect(lastAtOrBefore(a, 100)).toBe(4)
    expect(lastAtOrBefore(a, 100, 2)).toBe(1)
  })
})

describe('Dubins turns', () => {
  // Deterministic pseudo-random poses.
  let seed = 11
  const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648 }

  it('arrives at the target position and heading, never tighter than the radius', () => {
    for (let i = 0; i < 400; i++) {
      const r = 20 + rnd() * 80
      const x1 = (rnd() - 0.5) * 800
      const y1 = (rnd() - 0.5) * 800
      const th0 = rnd() * Math.PI * 2
      const th1 = rnd() * Math.PI * 2
      const path = dubinsPath(0, 0, th0, x1, y1, th1, r)
      const pts = sampleDubins(path, Math.PI / 90)
      const end = pts[pts.length - 1]
      expect(Math.hypot(end[0] - x1, end[1] - y1)).toBeLessThan(1e-6)
      // It ends heading th1: the last step points that way.
      const prev = pts[pts.length - 2]
      const final = Math.atan2(end[1] - prev[1], end[0] - prev[0])
      expect(Math.abs(Math.atan2(Math.sin(final - th1), Math.cos(final - th1)))).toBeLessThan(0.03)
      expect(path.length).toBeGreaterThanOrEqual(Math.hypot(x1, y1) - 1e-6)
      // Curvature: three consecutive samples never bend tighter than the radius.
      for (let k = 1; k < pts.length - 1; k++) {
        const [ax, ay] = pts[k - 1]
        const [bx, by] = pts[k]
        const [cx, cy] = pts[k + 1]
        const cross = Math.abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
        const abc = Math.hypot(bx - ax, by - ay) * Math.hypot(cx - bx, cy - by) * Math.hypot(cx - ax, cy - ay)
        if (cross > 1e-9) expect(abc / (2 * cross)).toBeGreaterThan(r * 0.995)
      }
    }
  })

  it('a straight shot is a straight line', () => {
    const path = dubinsPath(0, 0, 0, 500, 0, 0, 50)
    expect(path.length).toBeCloseTo(500, 6)
    expect(path.pieces.every(p => p.type === 'S')).toBe(true)
  })

  it('turns between neighbouring lines closer than a turn diameter with a bulb, not a hairpin', () => {
    // Lines 50 m apart, 60 m turn radius: the aircraft cannot simply swing round.
    const path = dubinsPath(0, 0, 0, 0, 50, Math.PI, 60)
    expect(['RLR', 'LRL']).toContain(path.word)
    expect(path.length).toBeGreaterThan(50 + Math.PI * 60)          // longer than the planner's approximation
    // Lines further apart than the diameter: half a circle and a straight.
    const wide = dubinsPath(0, 0, 0, 0, 200, Math.PI, 60)
    expect(wide.length).toBeCloseTo(Math.PI * 60 + 80, 6)
  })

  it('degenerates gracefully', () => {
    expect(dubinsPath(0, 0, 0, 0, 0, 0, 50).length).toBeCloseTo(0, 9)
    expect(sampleDubins(dubinsPath(0, 0, 0, 0, 0, 0, 50))).toEqual([[0, 0]])
    expect(dubinsPath(0, 0, 0, 100, 0, 0, 0).length).toBe(100)       // no radius: a straight line
  })

  it('loiter circles come back to where they started', () => {
    const pts = loiterCircles(10, 20, 0.3, 50, 2)
    const end = pts[pts.length - 1]
    expect(Math.hypot(end[0] - 10, end[1] - 20)).toBeLessThan(1e-6)
    let length = Math.hypot(pts[0][0] - 10, pts[0][1] - 20)
    for (let i = 1; i < pts.length; i++) length += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
    expect(length).toBeCloseTo(2 * 2 * Math.PI * 50, -1)
  })

  it('converts between compass bearings and mathematical angles', () => {
    expect(bearingToAngle(90)).toBeCloseTo(0, 12)
    expect(bearingToAngle(0)).toBeCloseTo(Math.PI / 2, 12)
    for (const b of [0, 45, 90, 180, 270, 359]) expect(angleToBearing(bearingToAngle(b))).toBeCloseTo(b, 9)
  })
})
