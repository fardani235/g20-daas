import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import fixture from '@/lib/__fixtures__/flightsim.json'

// The simulator as the planner uses it: wrapped around the page's map, with
// the dock (controls, dashboard, profile) below and the 3D pane beside. The
// 3D pane needs a GPU, so a stand-in records what it is handed; everything
// else - the controller, the clock, the 2D map layer, the panels - is real.

const view3d = { mounts: 0, unmounts: 0, frames: [], props: null }
vi.mock('@/components/flightsim/SimView3D.vue', async () => {
  const { h: hh, onBeforeUnmount, onMounted } = await import('vue')
  return {
    __esModule: true,
    default: {
      name: 'SimView3D',
      props: ['simulation', 'onFrame', 'onCoverage', 'coverageCanvas', 'coverageMode', 'basemap', 'ortho'],
      setup(props) {
        let stop = null
        view3d.props = props
        onMounted(() => { view3d.mounts++; stop = props.onFrame(state => view3d.frames.push(state)) })
        onBeforeUnmount(() => { view3d.unmounts++; stop?.() })
        return () => hh('div', { 'data-sim-3d': 'ready', 'data-coverage': props.coverageMode })
      },
    },
  }
})

const terrainApi = { getSimulationTerrain: vi.fn() }
vi.mock('@/lib/flightsim/api', () => ({ getSimulationTerrain: (...a) => terrainApi.getSimulationTerrain(...a) }))

import FlightSimulator from './FlightSimulator.vue'
// The shell loads the session on demand; having it loaded already keeps the tests off the module loader's clock.
import './SimSession.vue'

const { missions } = fixture
const DEFINITION = { type: 'area', elevation_source: 'auto', drone: 'dji-mavic-3e' }

// Time and animation frames under the test's control.
let now = 0
let frames = []
function frame(ms = 16) {
  now += ms
  const pending = frames
  frames = []
  for (const cb of pending) cb(now)
}

beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = () => ({
    createImageData: (w, hgt) => ({ width: w, height: hgt, data: new Uint8ClampedArray(w * hgt * 4) }),
    putImageData: () => {},
    fillRect: () => {},
  })
})

function fakeMap() {
  const layers = new Set()
  const handlers = {}
  const panes = {}
  return {
    layers,
    addLayer(l) { layers.add(l); return this },
    removeLayer(l) { layers.delete(l); return this },
    hasLayer: l => layers.has(l),
    getPane: name => panes[name],
    createPane: name => (panes[name] = document.createElement('div')),
    on(event, fn) { handlers[event] = fn },
    off(event) { delete handlers[event] },
    panTo: vi.fn(),
    invalidateSize: vi.fn(),
    fitBounds: vi.fn(),
  }
}

// Let promises and the lazily loaded 3D pane resolve.
const settle = async (n = 8) => {
  for (let i = 0; i < n; i++) { await Promise.resolve(); await nextTick() }
  await new Promise(resolve => setTimeout(resolve, 0))
  await nextTick()
}
let mounted = []

async function mount(initial = {}) {
  const map = fakeMap()
  const props = reactive({
    active: false, mission: missions.area_terrain, definition: DEFINITION, project: 'P1', getMap: () => map,
    basemap: 'esri-satellite', ortho: null, ...initial,
  })
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp({
    render: () => h(FlightSimulator, {
      ...props,
      'onUpdate:active': v => { props.active = v },
    }, { default: () => h('div', { 'data-the-map': '' }, 'the planner map') }),
  })
  app.mount(el)
  mounted.push({ app, el })
  await settle()
  const q = sel => el.querySelector(sel)
  const all = sel => [...el.querySelectorAll(sel)]
  // A tile's label, value and detail, separated by spaces.
  const tile = key => [...(q(`[data-sim-tile="${key}"]`)?.children || [])].map(c => c.textContent.trim()).join(' ')
  const time = () => Number(q('[data-flight-sim]').dataset.simTime)
  return { el, q, all, props, map, tile, time }
}

beforeEach(() => {
  now = 0
  frames = []
  Object.assign(view3d, { mounts: 0, unmounts: 0, frames: [], props: null })
  vi.stubGlobal('requestAnimationFrame', cb => { frames.push(cb); return frames.length })
  vi.stubGlobal('cancelAnimationFrame', () => { frames = [] })
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  terrainApi.getSimulationTerrain.mockReset()
  terrainApi.getSimulationTerrain.mockResolvedValue({ terrain: fixture.terrain.area_terrain, pending: false, message: null })
})

afterEach(() => {
  for (const { app, el } of mounted) { app.unmount(); el.remove() }
  mounted = []
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('closed', () => {
  it('is just the planner\'s map: no dock, no 3D, no request', async () => {
    const { q, map } = await mount()
    expect(q('[data-the-map]').textContent).toBe('the planner map')
    expect(q('[data-sim-dock]')).toBeNull()
    expect(q('[data-sim-3d]')).toBeNull()
    expect(q('[data-flight-sim]').dataset.simStatus).toBe('off')
    expect(terrainApi.getSimulationTerrain).not.toHaveBeenCalled()
    expect(map.layers.size).toBe(0)
  })
})

describe('opening', () => {
  it('asks for the plan\'s elevation grid, then shows the dock, the map layer and the 3D pane', async () => {
    let deliver
    terrainApi.getSimulationTerrain.mockReturnValue(new Promise(resolve => { deliver = resolve }))
    const { q, all, props, map } = await mount()
    props.active = true
    await settle()
    expect(q('[data-sim-loading]')).not.toBeNull()                       // waiting for the grid
    expect(q('[data-sim-controls]')).toBeNull()
    deliver({ terrain: fixture.terrain.area_terrain, pending: false, message: null })
    await settle()
    expect(terrainApi.getSimulationTerrain).toHaveBeenCalledWith({ definition: DEFINITION, project: 'P1' })
    expect(q('[data-flight-sim]').dataset.simStatus).toBe('ready')
    expect(q('[data-sim-loading]')).toBeNull()
    expect(q('[data-sim-controls]')).not.toBeNull()
    expect(all('[data-sim-tile]').length).toBe(10)
    expect(q('[data-sim-profile] svg')).not.toBeNull()
    expect(q('[data-sim-3d]')).not.toBeNull()
    expect(view3d.mounts).toBe(1)
    expect(view3d.props.simulation.mission).toBe(missions.area_terrain)
    expect(view3d.props.basemap.id).toBe('esri-satellite')
    // The planner's map is still there, with the simulation's layer on it and the whole flight in view.
    expect(q('[data-the-map]')).not.toBeNull()
    expect(map.layers.size).toBeGreaterThan(0)
    expect(map.fitBounds).toHaveBeenCalledTimes(1)
    const bounds = map.fitBounds.mock.calls[0][0]
    expect(bounds.contains([missions.area_terrain.takeoff.lat, missions.area_terrain.takeoff.lng])).toBe(true)
    expect(map.invalidateSize).toHaveBeenCalled()
  })

  it('says, wherever it is open, that this is an estimate and not telemetry or a safety check', async () => {
    const { q, props } = await mount()
    props.active = true
    await settle()
    const notice = q('[data-sim-notice]').textContent
    expect(notice).toMatch(/Simulation only/)
    expect(notice).toMatch(/not flight telemetry/)
    expect(notice).toMatch(/does not verify that the flight is safe/)
    expect(q('[data-sim-dock]').textContent).toMatch(/Estimate, not telemetry/)
    expect(q('[data-sim-tile="clearance"]').textContent).toMatch(/estimate/)
    expect(q('[data-sim-tile="battery"]').textContent).toMatch(/estimate/)
    expect(q('[data-sim-dock]').textContent).not.toMatch(/\b(verified|cleared|safe to fly)\b/i)
  })

  it('starts on the ground at the takeoff point, at time zero, paused', async () => {
    const { q, props, tile, time } = await mount()
    props.active = true
    await settle()
    expect(time()).toBe(0)
    expect(q('[data-flight-sim]').dataset.simPlaying).toBe('false')
    expect(tile('phase')).toMatch(/Pre-flight/)
    expect(tile('altitude')).toMatch(/0 m/)
    expect(tile('photos')).toMatch(/0 of 315/)
    expect(q('[data-sim-clock]').textContent.replace(/\s+/g, ' ').trim()).toMatch(/^0:00 \/ \d+:\d\d$/)
  })

  it('shows an unusable plan as a message, not a broken page', async () => {
    const { q, props } = await mount({ mission: { waypoints: [], legs: [] } })
    props.active = true
    await settle()
    expect(q('[data-sim-error]').textContent).toMatch(/no computed route/)
    expect(q('[data-sim-controls]')).toBeNull()
    expect(view3d.mounts).toBe(0)
    expect(q('[data-the-map]')).not.toBeNull()
  })

  it('still simulates when the elevation grid cannot be loaded, and says what ground it assumes', async () => {
    terrainApi.getSimulationTerrain.mockResolvedValue({ terrain: null, pending: false, message: 'Elevation data could not be loaded (timeout).' })
    const { q, props, tile } = await mount({ mission: { ...missions.area_flat, elevation_source: 'auto' } })
    props.active = true
    await settle()
    expect(q('[data-sim-controls]')).not.toBeNull()
    expect(tile('clearance')).toMatch(/not known/)
    expect(q('[data-sim-notes]').textContent).toMatch(/No elevation data: flat ground at the takeoff height is assumed/)
    expect(q('[data-sim-notes]').textContent).toMatch(/could not be loaded \(timeout\)/)
  })
})

describe('playback controls', () => {
  async function opened(initial) {
    const ctx = await mount(initial)
    ctx.props.active = true
    await settle()
    return ctx
  }

  it('plays and pauses; time moves only while playing', async () => {
    const { q, time } = await opened()
    q('[data-sim-play]').click()
    await nextTick()
    expect(q('[data-flight-sim]').dataset.simPlaying).toBe('true')
    expect(q('[data-sim-play]').getAttribute('aria-pressed')).toBe('true')
    frame(100); frame(100)
    await nextTick()
    expect(time()).toBeCloseTo(0.2, 2)
    q('[data-sim-play]').click()
    frame(5000)
    await nextTick()
    expect(q('[data-flight-sim]').dataset.simPlaying).toBe('false')
    expect(time()).toBeCloseTo(0.2, 2)
  })

  it('plays faster at a higher speed', async () => {
    const { q, time } = await opened()
    const speed = q('[data-sim-speed]')
    expect([...speed.options].map(o => o.value)).toEqual(['0.5', '1', '2', '4', '8', '16', '32', '64'])
    speed.value = '32'
    speed.dispatchEvent(new Event('change'))
    q('[data-sim-play]').click()
    for (let i = 0; i < 10; i++) frame(100)
    await nextTick()
    expect(time()).toBeCloseTo(32, 1)
  })

  it('scrubbing the timeline moves every panel to that moment', async () => {
    const { q, tile, time } = await opened()
    const scrub = q('[data-sim-scrub]')
    expect(Number(scrub.max)).toBeCloseTo(view3d.props.simulation.duration, 6)
    const tr = view3d.props.simulation.trajectory
    const target = (tr.legTimes.t0[4] + tr.legTimes.t1[4]) / 2
    scrub.value = String(target)
    scrub.dispatchEvent(new Event('input'))
    await nextTick()
    expect(time()).toBeCloseTo(target, 1)
    expect(tile('phase')).toMatch(/Survey line 5 of 9/)
    expect(tile('speed')).toMatch(/10\.0 m\/s/)
    expect(tile('photos')).toMatch(/camera on/)
    expect(Number(tile('clearance').match(/(\d+) m/)[1])).toBeGreaterThan(70)
    // The profile's cursor is at the distance flown.
    const sim = view3d.props.simulation
    const cursorX = Number(q('[data-sim-profile-cursor]').dataset.x)
    expect(cursorX).toBeCloseTo(4 + (sim.sample(time()).distance / sim.trajectory.distance) * 412, 0)
  })

  it('stop returns to the start; restart plays again from it', async () => {
    const { q, time } = await opened()
    const scrub = q('[data-sim-scrub]')
    scrub.value = '300'
    scrub.dispatchEvent(new Event('input'))
    q('[data-sim-play]').click()
    frame(100)
    q('[data-sim-stop]').click()
    await nextTick()
    expect(time()).toBe(0)
    expect(q('[data-flight-sim]').dataset.simPlaying).toBe('false')
    scrub.value = '300'
    scrub.dispatchEvent(new Event('input'))
    q('[data-sim-restart]').click()
    await nextTick()
    expect(time()).toBe(0)
    expect(q('[data-flight-sim]').dataset.simPlaying).toBe('true')
  })

  it('the space bar toggles playback from the controls', async () => {
    const { q } = await opened()
    q('[data-sim-controls]').dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }))
    await nextTick()
    expect(q('[data-flight-sim]').dataset.simPlaying).toBe('true')
    q('[data-sim-controls]').dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }))
    await nextTick()
    expect(q('[data-flight-sim]').dataset.simPlaying).toBe('false')
    q('[data-sim-controls]').dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))
    await nextTick()
    expect(q('[data-sim-tile="phase"]').textContent).toMatch(/Landed/)
  })

  it('a click on the elevation profile jumps to that point of the flight', async () => {
    const { q, time } = await opened()
    const svg = q('[data-sim-profile] svg')
    svg.getBoundingClientRect = () => ({ left: 100, top: 0, width: 420, height: 104 })
    const sim = view3d.props.simulation
    // Half way across the chart is half the distance.
    svg.dispatchEvent(new MouseEvent('pointerdown', { clientX: 100 + 210, bubbles: true }))
    await nextTick()
    expect(sim.sample(time()).distance).toBeCloseTo(sim.trajectory.distance / 2, -1)
    expect(q('[data-sim-profile-readout]').textContent).toMatch(/km flown · aircraft \d+ m elevation · ground \d+ m/)
    expect(q('[data-sim-profile-lowest]').textContent).toMatch(/Lowest clearance \d+ m/)
  })

  it('the profile reads the aircraft\'s own height while it climbs straight up, not the height at the end of the climb', async () => {
    const { q, time } = await opened()
    const sim = view3d.props.simulation
    const climb = sim.trajectory.phases.find(p => p.key === 'takeoff')
    const scrub = q('[data-sim-scrub]')
    scrub.value = String((climb.t0 + climb.t1) / 2)                      // half way up, no ground covered yet
    scrub.dispatchEvent(new Event('input'))
    await nextTick()
    const state = sim.sample(time())
    expect(state.distance).toBe(0)
    expect(state.alt).toBeGreaterThan(20)
    expect(state.alt).toBeLessThan(60)
    expect(q('[data-sim-profile-readout]').textContent).toContain(`aircraft ${Math.round(state.amsl)} m elevation`)
    expect(q('[data-sim-tile="altitude"]').textContent).toContain(`${Math.round(state.alt)} m`)
  })

  it('a multi-battery mission shows its flights on the timeline and jumps to them', async () => {
    const { q, all, tile, time } = await opened({ mission: missions.multi_battery, definition: { ...DEFINITION, elevation_source: 'none' } })
    expect(terrainApi.getSimulationTerrain).not.toHaveBeenCalled()       // planned without elevation data
    expect(all('[data-sim-flight]').length).toBe(3)
    expect(all('[data-sim-tile]').length).toBe(11)                       // plus the per-flight clock
    q('[data-sim-flight="1"]').click()
    await nextTick()
    const flights = view3d.props.simulation.trajectory.flights
    expect(time()).toBeCloseTo(flights[1].t0, 1)
    expect(tile('phase')).toMatch(/Pre-flight.*Flight 2 of 3/)
    expect(tile('battery')).toMatch(/100 %/)
    // Just before it, the battery is being changed.
    const scrub = q('[data-sim-scrub]')
    scrub.value = String(flights[1].t0 - 20)
    scrub.dispatchEvent(new Event('input'))
    await nextTick()
    expect(tile('phase')).toMatch(/Battery change before flight 2/)
  })
})

describe('views', () => {
  async function opened() {
    const ctx = await mount()
    ctx.props.active = true
    await settle()
    return ctx
  }

  it('the 2D map layer and the 3D pane are handed the very same state each frame', async () => {
    const { q, map } = await opened()
    // Find the simulation's aircraft marker on the map through the layer group.
    const group = [...map.layers].find(l => typeof l.getLayers === 'function')
    const marker = group.getLayers().find(l => typeof l.getLatLng === 'function' && l.options.icon)
    q('[data-sim-speed]').value = '16'
    q('[data-sim-speed]').dispatchEvent(new Event('change'))
    q('[data-sim-play]').click()
    for (let i = 0; i < 20; i++) {
      frame(33)
      const state = view3d.frames.at(-1)                                 // what the 3D pane just drew
      const at = marker.getLatLng()                                      // what the map just drew
      expect(at.lat).toBe(state.lat)
      expect(at.lng).toBe(state.lng)
    }
    expect(view3d.frames.length).toBeGreaterThan(20)
    expect(new Set(view3d.frames).size).toBe(1)                          // one state object, reused: nothing to drift
    // And a scrub lands both on the same moment.
    const scrub = q('[data-sim-scrub]')
    scrub.value = '444'
    scrub.dispatchEvent(new Event('input'))
    expect(view3d.frames.at(-1).t).toBe(444)
    expect(marker.getLatLng().lat).toBe(view3d.frames.at(-1).lat)
  })

  it('switches between the map, both and 3D without losing the map', async () => {
    const { q } = await opened()
    const pane = q('[data-sim-map-pane]')
    expect(q('[data-layout="split"]').getAttribute('aria-pressed')).toBe('true')
    q('[data-layout="2d"]').click()
    await settle()
    expect(q('[data-sim-3d]')).toBeNull()
    expect(view3d.unmounts).toBe(1)
    expect(pane.style.display).toBe('')
    q('[data-layout="3d"]').click()
    await settle()
    expect(q('[data-sim-3d]')).not.toBeNull()
    expect(pane.style.display).toBe('none')                              // hidden, not destroyed
    expect(q('[data-the-map]')).not.toBeNull()
    expect(q('[data-sim-follow-map]')).toBeNull()                        // nothing to follow on a hidden map
    q('[data-layout="split"]').click()
    await settle()
    expect(pane.style.display).toBe('')
  })

  it('one coverage setting drives both views, with a legend for the overlap colours', async () => {
    const { q, map } = await opened()
    const overlays = () => [...map.layers].filter(l => typeof l.getBounds === 'function' && typeof l.getLayers !== 'function').length
    expect(overlays()).toBe(1)                                           // covered so far, on the map
    expect(q('[data-sim-3d]').dataset.coverage).toBe('covered')
    const select = q('[data-sim-coverage]')
    select.value = 'overlap'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    expect(q('[data-sim-3d]').dataset.coverage).toBe('overlap')
    expect(q('[data-sim-overlap-legend]').textContent).toMatch(/1 photo.*9 or more/)
    select.value = 'off'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    expect(overlays()).toBe(0)
    expect(q('[data-sim-3d]').dataset.coverage).toBe('off')
    expect(q('[data-sim-overlap-legend]')).toBeNull()
  })

  it('keeps the map on the aircraft when asked', async () => {
    const { q, map } = await opened()
    const box = q('[data-sim-follow-map]')
    box.checked = true
    box.dispatchEvent(new Event('change'))
    await nextTick()
    q('[data-sim-scrub]').value = '200'
    q('[data-sim-scrub]').dispatchEvent(new Event('input'))
    const state = view3d.frames.at(-1)
    expect(map.panTo).toHaveBeenLastCalledWith([state.lat, state.lng], { animate: false })
  })

  it('compares itself with the plan and lists what it assumed', async () => {
    const { q } = await opened()
    const rows = [...q('[data-sim-compare]').querySelectorAll('tbody tr')].map(r => [...r.children].map(c => c.textContent.trim()))
    expect(rows.map(r => r[0])).toEqual(['Flight time', 'Distance', 'Photos', 'Batteries', 'Lowest clearance'])
    expect(rows[2].slice(1)).toEqual(['315', '315'])
    expect(q('[data-sim-coverage-figures]').textContent).toMatch(/Survey area covered\s*\d+ %/)
    expect(q('[data-sim-notes]').textContent).toMatch(/Ground: Test DEM, about 30 m resolution/)
    expect(q('[data-sim-notes]').textContent).toMatch(/Battery is time in the air over the aircraft's rated endurance/)
  })
})

describe('closing and changing', () => {
  it('Exit asks the page to close it; closing takes everything off the map', async () => {
    const { q, props, map } = await mount()
    props.active = true
    await settle()
    q('[data-sim-play]').click()
    q('[data-sim-exit]').click()
    await settle()
    expect(props.active).toBe(false)
    expect(q('[data-sim-dock]')).toBeNull()
    expect(q('[data-sim-3d]')).toBeNull()
    expect(view3d.unmounts).toBe(1)
    expect(map.layers.size).toBe(0)
    expect(q('[data-the-map]')).not.toBeNull()
    // No clock left running.
    const drawn = view3d.frames.length
    frame(100)
    expect(view3d.frames.length).toBe(drawn)
  })

  it('rebuilds from the start when the plan underneath changes', async () => {
    const { q, props, time, tile } = await mount()
    props.active = true
    await settle()
    q('[data-sim-scrub]').value = '300'
    q('[data-sim-scrub]').dispatchEvent(new Event('input'))
    await nextTick()
    expect(time()).toBe(300)
    props.mission = { ...missions.area_slope }
    terrainApi.getSimulationTerrain.mockResolvedValue({ terrain: fixture.terrain.area_slope, pending: false, message: null })
    await settle()
    expect(terrainApi.getSimulationTerrain).toHaveBeenCalledTimes(2)
    expect(time()).toBe(0)
    expect(tile('photos')).toMatch(/0 of 133/)
    expect(view3d.mounts).toBe(2)                                        // a fresh scene for the new plan
    expect(view3d.props.simulation.mission.stats.photo_count).toBe(133)
  })

  it('closes when the plan disappears while it is open', async () => {
    const { q, props, map } = await mount()
    props.active = true
    await settle()
    props.mission = null
    await settle()
    expect(q('[data-sim-controls]')).toBeNull()
    expect(map.layers.size).toBe(0)
    expect(q('[data-flight-sim]').dataset.simStatus).toBe('idle')
  })

  it('never changes the mission it was given', async () => {
    const before = JSON.stringify(missions.area_terrain)
    const { q, props } = await mount()
    props.active = true
    await settle()
    q('[data-sim-play]').click()
    for (let i = 0; i < 30; i++) frame(100)
    q('[data-sim-scrub]').value = '700'
    q('[data-sim-scrub]').dispatchEvent(new Event('input'))
    q('[data-sim-exit]').click()
    await settle()
    expect(JSON.stringify(missions.area_terrain)).toBe(before)
  })
})
