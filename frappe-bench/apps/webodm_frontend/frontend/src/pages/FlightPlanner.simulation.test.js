import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { createApp, nextTick, reactive } from 'vue'
import fixture from '@/lib/__fixtures__/flightplan.json'
import simFixture from '@/lib/__fixtures__/flightsim.json'

// "Simulate flight" in the planner: the button, what it sends, that it works
// on an unsaved plan and on a saved one, and that the planner is exactly as
// it was once the simulation is closed. The planner's own behaviour is
// covered by FlightPlanner.test.js; the simulator's by its own tests.

const routeParams = reactive({ id: 'P1', planId: 'new' })
const routeQuery = reactive({})
const push = vi.fn()
const replace = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: routeParams, query: routeQuery }),
  useRouter: () => ({ push, replace }),
  onBeforeRouteLeave: () => {},
}))
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))
vi.mock('@/lib/datasets', () => ({ listTasks: vi.fn(async () => []) }))

const editor = { onChange: null }
vi.mock('@/composables/useShapeEditor', async () => {
  const { reactive: r } = await import('vue')
  return {
    useShapeEditor: (_getMap, { onChange }) => {
      editor.onChange = onChange
      const state = r({ drawing: false, type: 'area', count: 0 })
      return {
        state, start: () => {}, finish: () => true, cancel: () => {}, clear: () => {}, setShape: () => {},
        setEditable: () => {}, bounds: () => null, destroy: () => {}, getVertices: () => [],
      }
    },
  }
})
vi.mock('@/lib/planLayers', async () => {
  const L = (await import('leaflet')).default
  return {
    buildPlanLayers: vi.fn(() => ({ group: { addTo() { return this } }, bounds: null })),
    takeoffIcon: () => L.divIcon({ html: '<div>H</div>', className: 'plan-badge' }),
  }
})

// vi.mock is hoisted above the imports, so the stand-ins it closes over are too.
const api = vi.hoisted(() => ({ getCatalog: vi.fn(), getElevationSources: vi.fn(), previewPlan: vi.fn(), savePlan: vi.fn(), getPlan: vi.fn() }))
vi.mock('@/lib/flightplan', async importOriginal => {
  const actual = await importOriginal()
  const out = { ...actual }
  for (const key of Object.keys(api)) out[key] = (...a) => api[key](...a)
  return out
})

const terrainApi = vi.hoisted(() => ({ getSimulationTerrain: vi.fn() }))
vi.mock('@/lib/flightsim/api', () => ({ getSimulationTerrain: (...a) => terrainApi.getSimulationTerrain(...a) }))

// Leaflet's vector renderer cannot mount in jsdom (as for the plan's own layers),
// so the simulation's map layer is a stand-in that records what it is asked to draw.
const mapLayer = vi.hoisted(() => ({ created: 0, destroyed: 0, frames: [] }))
vi.mock('@/lib/flightsim/mapLayer', () => ({
  createSimMapLayer: () => {
    mapLayer.created++
    return {
      update: state => mapLayer.frames.push(state.t),
      setCoverageVisible: () => {}, setFootprintVisible: () => {}, setFollow: () => {},
      destroy: () => { mapLayer.destroyed++ },
    }
  },
}))

// The 3D pane needs WebGL; a stand-in records that it was shown and with what.
const view3d = vi.hoisted(() => ({ mounts: 0, simulation: null }))
vi.mock('@/components/flightsim/SimView3D.vue', async () => {
  const { h, onMounted } = await import('vue')
  return {
    __esModule: true,
    default: {
      props: ['simulation', 'onFrame', 'onCoverage', 'coverageCanvas', 'coverageMode', 'basemap', 'ortho'],
      setup(props) {
        onMounted(() => { view3d.mounts++; view3d.simulation = props.simulation })
        return () => h('div', { 'data-sim-3d': 'ready' })
      },
    },
  }
})

import FlightPlanner from './FlightPlanner.vue'
// The simulator is loaded on demand; having it loaded already keeps the tests off the module loader's clock.
import '@/components/flightsim/SimSession.vue'

const clone = v => JSON.parse(JSON.stringify(v))
const VERTICES = [
  { lat: -6.8214, lng: 107.6073 }, { lat: -6.8214, lng: 107.6127 }, { lat: -6.8186, lng: 107.6127 }, { lat: -6.8186, lng: 107.6073 },
]

let mounted = []
async function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(FlightPlanner)
  app.component('router-link', { template: '<a><slot /></a>' })
  app.mount(el)
  mounted.push(app)
  await settle()
  return el
}

async function settle(ms = 400) {
  for (let i = 0; i < 6; i++) await nextTick()
  await vi.advanceTimersByTimeAsync(ms)
  for (let i = 0; i < 6; i++) await nextTick()
}

async function draw(el) {
  editor.onChange(VERTICES, { final: true })
  await settle()
  return el
}

beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = () => ({
    createImageData: (w, hgt) => ({ width: w, height: hgt, data: new Uint8ClampedArray(w * hgt * 4) }),
    putImageData: () => {},
    fillRect: () => {},
  })
})

beforeEach(() => {
  vi.useFakeTimers()
  routeParams.id = 'P1'
  routeParams.planId = 'new'
  delete routeQuery.simulate
  editor.onChange = null
  push.mockReset()
  replace.mockReset()
  Object.assign(view3d, { mounts: 0, simulation: null })
  Object.assign(mapLayer, { created: 0, destroyed: 0, frames: [] })
  for (const fn of Object.values(api)) fn.mockReset()
  api.getCatalog.mockImplementation(async () => clone(fixture.catalog))
  api.getElevationSources.mockImplementation(async () => [{ id: 'auto', label: 'Open Topo Data', note: '' }, { id: 'none', label: 'None', note: '' }])
  api.previewPlan.mockImplementation(async () => ({ ok: true, mission: clone(fixture.mission) }))
  api.savePlan.mockImplementation(async ({ title, definition }) => ({
    name: 'PLAN1', title, description: '', project: 'P1', definition, mission: clone(fixture.mission),
  }))
  terrainApi.getSimulationTerrain.mockReset()
  terrainApi.getSimulationTerrain.mockResolvedValue({ terrain: null, pending: false, message: null })
  global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ data: { name: 'P1', title: 'Quarry' } }) }))
  global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
  window.confirm = vi.fn(() => true)
})

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('Simulate flight in the planner', () => {
  it('is offered once there is a mission, not before', async () => {
    const el = await mount()
    const button = el.querySelector('[data-plan-simulate]')
    expect(button.textContent).toContain('Simulate flight')
    expect(button.disabled).toBe(true)
    expect(button.title).toBe('Draw the survey first')
    await draw(el)
    expect(button.disabled).toBe(false)
    expect(button.title).toMatch(/an estimate, not a flight-safety check/)
    expect(el.querySelector('[data-sim-dock]')).toBeNull()
    expect(terrainApi.getSimulationTerrain).not.toHaveBeenCalled()
  })

  it('opens on an unsaved plan, asking for the elevation grid of the definition on screen', async () => {
    const el = await draw(await mount())
    el.querySelector('[data-plan-simulate]').click()
    await settle()
    expect(terrainApi.getSimulationTerrain).toHaveBeenCalledTimes(1)
    const sent = terrainApi.getSimulationTerrain.mock.calls[0][0]
    // The very definition the preview was computed from.
    expect(sent.definition).toEqual(api.previewPlan.mock.calls.at(-1)[0].definition)
    expect(sent.project).toBe('P1')
    expect(api.savePlan).not.toHaveBeenCalled()                          // simulating never saves
    expect(el.querySelector('[data-flight-sim]').dataset.simStatus).toBe('ready')
    expect(el.querySelector('[data-sim-controls]')).not.toBeNull()
    expect(el.querySelectorAll('[data-sim-tile]').length).toBe(10)
    expect(view3d.mounts).toBe(1)
    expect(el.querySelector('[data-plan-simulate]').getAttribute('aria-pressed')).toBe('true')
    expect(el.querySelector('[data-plan-simulate]').textContent).toContain('Simulating')
    expect(mapLayer.created).toBe(1)                                     // the aircraft is on the planner's map too
    expect(mapLayer.frames).toEqual([0])
    // The planner's map and its panel are still there.
    expect(el.querySelector('[data-plan-map]')).not.toBeNull()
    expect(el.querySelector('[data-plan-estimates]')).not.toBeNull()
  })

  it('simulates the mission the planner shows, photo for photo', async () => {
    const el = await draw(await mount())
    el.querySelector('[data-plan-simulate]').click()
    await settle()
    const mission = fixture.mission
    expect(view3d.simulation.trajectory.photos.count).toBe(mission.stats.photo_count)
    expect(view3d.simulation.trajectory.flights.length).toBe(mission.flights.length)
    expect(Math.abs(view3d.simulation.trajectory.distance - mission.stats.total_distance_m)).toBeLessThan(1)
    expect(el.querySelector('[data-sim-tile="photos"]').textContent).toContain(`of ${mission.stats.photo_count}`)
  })

  it('closing it gives the planner back unchanged, and sends nothing', async () => {
    const el = await draw(await mount())
    const before = el.querySelector('[data-plan-estimates]').innerHTML
    const previews = api.previewPlan.mock.calls.length
    el.querySelector('[data-plan-simulate]').click()
    await settle()
    el.querySelector('[data-sim-play]').click()
    await settle(50)
    el.querySelector('[data-sim-exit]').click()
    await settle()
    expect(el.querySelector('[data-sim-dock]')).toBeNull()
    expect(mapLayer.destroyed).toBe(1)                                   // nothing of it left on the map
    expect(el.querySelector('[data-plan-simulate]').getAttribute('aria-pressed')).toBe('false')
    expect(el.querySelector('[data-plan-estimates]').innerHTML).toBe(before)
    expect(api.previewPlan.mock.calls.length).toBe(previews)             // the plan was not recomputed
    expect(api.savePlan).not.toHaveBeenCalled()
    expect(el.querySelector('[data-plan-save]').disabled).toBe(true)     // still nothing new to save (no title yet)
  })

  it('does not make the plan look edited', async () => {
    api.getPlan.mockImplementation(async () => ({
      name: 'PLAN1', title: 'North field', description: '', project: 'P1',
      definition: clone(fixture.definition), mission: clone(fixture.mission),
    }))
    routeParams.planId = 'PLAN1'
    const el = await mount()
    expect(el.textContent).toContain('Saved')
    el.querySelector('[data-plan-simulate]').click()
    await settle()
    el.querySelector('[data-sim-play]').click()
    await settle(50)
    el.querySelector('[data-sim-exit]').click()
    await settle()
    expect(el.textContent).not.toContain('Unsaved changes')
    expect(api.previewPlan).not.toHaveBeenCalled()                       // a saved plan is simulated as stored
  })

  it('opens straight into the simulation from the plan list (?simulate=1)', async () => {
    api.getPlan.mockImplementation(async () => ({
      name: 'PLAN1', title: 'North field', description: '', project: 'P1',
      definition: clone(fixture.definition), mission: clone(fixture.mission),
    }))
    routeParams.planId = 'PLAN1'
    routeQuery.simulate = '1'
    const el = await mount()
    await settle()
    expect(el.querySelector('[data-flight-sim]').dataset.simStatus).toBe('ready')
    // The saved plan's own shape (the page rounds coordinates to 1e-7 degree).
    const sent = terrainApi.getSimulationTerrain.mock.calls[0][0].definition.geometry.coordinates[0]
    const stored = fixture.definition.geometry.coordinates[0]
    expect(sent.length).toBe(stored.length)
    sent.forEach((p, i) => { expect(p[0]).toBeCloseTo(stored[i][0], 6); expect(p[1]).toBeCloseTo(stored[i][1], 6) })
    expect(view3d.mounts).toBe(1)
  })

  it('uses the plan\'s elevation grid when the server has one', async () => {
    terrainApi.getSimulationTerrain.mockResolvedValue({ terrain: simFixture.terrain.area_slope, pending: false, message: null })
    api.previewPlan.mockImplementation(async () => ({ ok: true, mission: clone(simFixture.missions.area_slope) }))
    const el = await draw(await mount())
    el.querySelector('[data-plan-simulate]').click()
    await settle()
    expect(view3d.simulation.terrain.rows).toBe(simFixture.terrain.area_slope.rows)
    expect(el.querySelector('[data-sim-tile="clearance"]').textContent).toMatch(/0 m.*estimate/)   // on the ground at the takeoff point
    expect(el.querySelector('[data-sim-profile-lowest]')).not.toBeNull()
  })

  it('follows the plan when it is edited while the simulation is open', async () => {
    const el = await draw(await mount())
    el.querySelector('[data-plan-simulate]').click()
    await settle()
    expect(view3d.simulation.trajectory.photos.count).toBe(fixture.mission.stats.photo_count)
    // The user changes the plan; the server answers with a different mission.
    api.previewPlan.mockImplementation(async () => ({ ok: true, mission: clone(simFixture.missions.multi_battery) }))
    const altitude = el.querySelector('[data-plan-altitude] input') || el.querySelector('#fp-altitude')
    altitude.value = '90'
    altitude.dispatchEvent(new Event('input'))
    await settle()
    await settle()
    expect(view3d.simulation.trajectory.flights.length).toBe(3)
    expect(el.querySelectorAll('[data-sim-flight]').length).toBe(3)
    expect(el.querySelector('[data-flight-sim]').dataset.simTime).toBe('0.00')
  })

  it('closes itself when the plan stops computing', async () => {
    const el = await draw(await mount())
    el.querySelector('[data-plan-simulate]').click()
    await settle()
    api.previewPlan.mockImplementation(async () => ({ ok: false, error: { message: 'The survey area is too small to plan', field: 'geometry' } }))
    const altitude = el.querySelector('#fp-altitude')
    altitude.value = '91'
    altitude.dispatchEvent(new Event('input'))
    await settle()
    await settle()
    expect(el.querySelector('[data-sim-dock]')).toBeNull()
    expect(el.querySelector('[data-plan-simulate]').disabled).toBe(true)
    expect(el.querySelector('[data-plan-error]').textContent).toContain('too small')
  })
})
