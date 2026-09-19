import { describe, it, expect, vi } from 'vitest'
import { createFlightSim } from '@/composables/useFlightSim'

// jsdom has no 2D canvas. The renderer only ever calls context methods, so a
// Proxy that answers every method with a no-op (and gradients with the one
// method the code uses) is enough to exercise real drawing code without
// asserting pixel output.
function makeCtx() {
  const gradient = { addColorStop() {} }
  const calls = []
  const ctx = new Proxy(
    {},
    {
      get(target, prop) {
        if (prop === 'createLinearGradient' || prop === 'createRadialGradient') {
          return () => gradient
        }
        if (prop in target) return target[prop]
        return (...args) => {
          calls.push([prop, args])
        }
      },
      set(target, prop, value) {
        target[prop] = value
        return true
      },
    }
  )
  return { ctx, calls }
}

function makeCanvas(w = 800, h = 480) {
  const { ctx, calls } = makeCtx()
  return {
    width: 0,
    height: 0,
    clientWidth: w,
    clientHeight: h,
    __calls: calls,
    getContext: () => ctx,
    getBoundingClientRect: () => ({ width: w, height: h, left: 0, top: 0 }),
  }
}

function makeSim(overrides = {}) {
  const canvas = overrides.canvas || makeCanvas()
  const raf = vi.fn(() => 1)
  const caf = vi.fn()
  const onTelemetry = vi.fn()
  const sim = createFlightSim(canvas, {
    raf,
    caf,
    onTelemetry,
    now: () => 0,
    ...overrides,
  })
  sim.init()
  return { sim, canvas, raf, caf, onTelemetry }
}

describe('useFlightSim', () => {
  describe('waypoint physics', () => {
    it('starts at the first waypoint with the second as target', () => {
      const { sim } = makeSim()
      const state = sim.getState()
      expect(state.waypoints).toHaveLength(6)
      expect(state.targetIndex).toBe(1)
      expect(state.drone.x).toBeCloseTo(state.waypoints[0].x, 5)
      expect(state.drone.y).toBeCloseTo(state.waypoints[0].y, 5)
    })

    it('advances to the next waypoint when the current target is reached', () => {
      const { sim } = makeSim()
      const start = sim.getState().targetIndex
      let advanced = false
      for (let i = 0; i < 200 && !advanced; i++) {
        sim.advance(0.05)
        if (sim.getState().targetIndex !== start) advanced = true
      }
      expect(advanced).toBe(true)
    })

    it('wraps from the last waypoint back to the first', () => {
      const { sim } = makeSim()
      const visited = new Set([sim.getState().targetIndex])
      for (let i = 0; i < 4000; i++) {
        sim.advance(0.05)
        visited.add(sim.getState().targetIndex)
     }
      // every waypoint index is visited, including 0 after the last
      expect([...visited].sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5])
      expect(visited.has(0)).toBe(true)
    })

    it('publishes live telemetry while advancing', () => {
      const { sim, onTelemetry } = makeSim()
      sim.advance(0.05)
      expect(onTelemetry).toHaveBeenCalled()
      const latest = onTelemetry.mock.calls.at(-1)[0]
      expect(latest).toHaveProperty('altitude')
      expect(latest).toHaveProperty('speed')
      expect(latest).toHaveProperty('battery')
      expect(latest).toHaveProperty('latitude')
      expect(latest).toHaveProperty('longitude')
    })
  })

  describe('lifecycle', () => {
    it('renders a frame without throwing against a stubbed context', () => {
      const { sim, canvas } = makeSim()
      expect(() => sim.render()).not.toThrow()
      expect(canvas.__calls.some(([m]) => m === 'fillRect' || m === 'stroke')).toBe(true)
    })

    it('schedules and cancels the animation frame', () => {
      const { sim, raf, caf } = makeSim()
      expect(raf).toHaveBeenCalledTimes(1)
      expect(sim.running).toBe(true)
      sim.stop()
      expect(caf).toHaveBeenCalledWith(1)
      expect(sim.running).toBe(false)
      expect(raf).toHaveBeenCalledTimes(1)
    })

    it('releases the loop on destroy', () => {
      const { sim, caf } = makeSim()
      sim.destroy()
      expect(caf).toHaveBeenCalled()
      expect(sim.running).toBe(false)
    })

    it('does not autoplay and does not move when reduced motion is set', () => {
      const raf = vi.fn(() => 1)
      const canvas = makeCanvas()
      const sim = createFlightSim(canvas, { raf, caf: vi.fn(), reduced: true, now: () => 0 })
      sim.init()
      expect(raf).not.toHaveBeenCalled()
      expect(sim.running).toBe(false)
      const before = sim.getState().drone
      sim.advance(1)
      const after = sim.getState().drone
      expect(after.x).toBeCloseTo(before.x, 6)
      expect(after.y).toBeCloseTo(before.y, 6)
    })
  })

  describe('interactions', () => {
    it('switches the active layer and ignores unknown ids', () => {
      const { sim } = makeSim()
      expect(sim.setLayer('thermal')).toBe('thermal')
      expect(sim.getState().layer).toBe('thermal')
      expect(sim.setLayer('pointcloud')).toBe('pointcloud')
      expect(sim.setLayer('nope')).toBe('pointcloud')
    })

    it('toggles play and pause', () => {
      const { sim } = makeSim()
      expect(sim.playing).toBe(true)
      expect(sim.togglePlay()).toBe(false)
      expect(sim.getState().playing).toBe(false)
      expect(sim.getState().telemetry.speed).toBe(0)
      expect(sim.togglePlay()).toBe(true)
      expect(sim.getState().telemetry.speed).toBeGreaterThan(0)
    })

    it('routes to a new user waypoint when empty terrain is clicked', () => {
      const { sim } = makeSim()
      const before = sim.getState().waypoints.length
      // (0.9, 0.9) sits clear of every area of interest
      const result = sim.selectAt(697, 409)
      expect(result.type).toBe('waypoint')
      const state = sim.getState()
      expect(state.waypoints).toHaveLength(before + 1)
      expect(state.targetIndex).toBe(state.waypoints.length - 1)
    })

    it('selects an area of interest when its marker is clicked', () => {
      const { sim } = makeSim()
      const state = sim.getState()
      // centre of the first area of interest
      const area = state.selected
      const [x, y] = [0.38, 0.665]
      const sx = 28.8 + 0.38 * (800 - 2 * 28.8)
      const sy = 28.8 + 0.665 * (480 - 2 * 28.8)
      const result = sim.selectAt(sx, sy)
      expect(result.type).toBe('area')
      expect(result.area.id).toBe(area.id)
      expect(sim.getState().selected.id).toBe(area.id)
      expect(sim.getState().notice).toContain(area.label)
    })

    it('resets the flight plan, target, and selection', () => {
      const { sim } = makeSim()
      sim.selectAt(697, 409)
      sim.setLayer('thermal')
      sim.togglePlay()
      sim.reset()
      const state = sim.getState()
      expect(state.waypoints).toHaveLength(6)
      expect(state.targetIndex).toBe(1)
      expect(state.playing).toBe(true)
      expect(state.selected.id).toBe('aoi-stockpile')
    })

    it('does not consume a waypoint slot when an area is selected', () => {
      const { sim } = makeSim()
      sim.selectAt(311, 310)
      expect(sim.getState().waypoints).toHaveLength(6)
    })
  })
})
