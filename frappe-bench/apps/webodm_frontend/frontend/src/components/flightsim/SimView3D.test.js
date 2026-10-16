import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import fixture from '@/lib/__fixtures__/flightsim.json'
import { createSimulation } from '@/lib/flightsim'

// The 3D pane's wiring: what it asks of the scene and when. The scene itself
// needs a GPU and is exercised by e2e/flight-sim.e2e.mjs; here it is a
// stand-in that records the calls.

const scene = vi.hoisted(() => ({ instance: null, created: [], fail: null }))
vi.mock('@/lib/flightsim/scene', () => {
  class WebGLUnavailable extends Error {}
  return {
    WebGLUnavailable,
    createSimScene: (container, simulation, options) => {
      if (scene.fail === 'webgl') throw new WebGLUnavailable('no context')
      if (scene.fail) throw new Error(scene.fail)
      const calls = { update: [], setMode: [], setLayers: [], setImagery: [], coverageChanged: 0, disposed: 0 }
      let mode = options.mode
      scene.instance = {
        calls,
        options,
        simulation,
        container,
        update: state => calls.update.push(state),
        setMode: m => { mode = m; calls.setMode.push(m) },
        setLayers: l => calls.setLayers.push(l),
        setImagery: i => calls.setImagery.push(i),
        coverageChanged: () => { calls.coverageChanged++ },
        gimbalFrame: () => (mode === 'gimbal' ? { width: 0.8, height: 0.6 } : null),
        snapshot: () => ({ mode }),
        dispose: () => { calls.disposed++ },
      }
      scene.created.push(scene.instance)
      return scene.instance
    },
  }
})

import SimView3D from './SimView3D.vue'

// Raw, as the controller hands them over: a simulation is never made reactive.
const simulation = markRaw(createSimulation(fixture.missions.area_terrain, fixture.terrain.area_terrain))
const flat = markRaw(createSimulation(fixture.missions.area_flat, null))
const BASEMAP = { id: 'esri-satellite', url: 'https://tiles.example/{z}/{y}/{x}', maxZoom: 19, attribution: 'Tiles &copy; Esri' }

let mounted = []
async function mount(initial = {}) {
  const frames = new Set()
  const coverage = new Set()
  const props = reactive({
    simulation,
    onFrame: fn => { frames.add(fn); fn(simulation.sample(0), 'subscribe'); return () => frames.delete(fn) },
    onCoverage: fn => { coverage.add(fn); return () => coverage.delete(fn) },
    coverageCanvas: document.createElement('canvas'),
    coverageMode: 'covered',
    basemap: BASEMAP,
    ortho: null,
    ...initial,
  })
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp({ render: () => h(SimView3D, props) })
  app.mount(el)
  mounted.push({ app, el })
  await nextTick()
  return { el, props, frames, coverage, q: sel => el.querySelector(sel), emit: state => frames.forEach(fn => fn(state, 'tick')) }
}

beforeEach(() => {
  Object.assign(scene, { instance: null, created: [], fail: null })
})
afterEach(() => {
  for (const { app, el } of mounted) { app.unmount(); el.remove() }
  mounted = []
})

describe('SimView3D', () => {
  it('starts the scene with the simulation, the shared coverage canvas, the basemap and the follow camera', async () => {
    const { q, props } = await mount()
    const s = scene.instance
    expect(s.simulation).toBe(simulation)
    expect(s.container).toBe(q('[data-sim-3d-host]'))
    expect(s.options.coverageCanvas).toBe(props.coverageCanvas)
    expect(s.options.basemap).toEqual(BASEMAP)
    expect(s.options.mode).toBe('follow')
    expect(s.options.layers).toMatchObject({ path: true, photos: true, footprint: true, coverage: 'covered' })
    expect(q('[data-sim-3d]').getAttribute('data-sim-3d')).toBe('ready')
    expect(q('[data-sim-3d]').getAttribute('data-sim-3d-mode')).toBe('follow')
    expect(typeof q('[data-sim-3d-host]').simSnapshot).toBe('function')
  })

  it('draws every frame it is handed, starting with the current one', async () => {
    const { emit } = await mount()
    const calls = scene.instance.calls
    expect(calls.update.length).toBe(1)                                  // drawn as soon as it subscribed
    const state = simulation.sample(120)
    emit(state)
    expect(calls.update.at(-1)).toBe(state)                              // the same object the map gets
    expect(calls.update.length).toBe(2)
  })

  it('offers five cameras and switches between them', async () => {
    const { el, q } = await mount()
    const buttons = [...el.querySelectorAll('[data-sim-camera]')]
    expect(buttons.map(b => b.dataset.simCamera)).toEqual(['follow', 'free', 'top', 'fpv', 'gimbal'])
    expect(buttons.map(b => b.textContent.trim())).toEqual(['Follow', 'Free', 'Top-down', 'FPV', 'Camera'])
    q('[data-sim-camera="top"]').click()
    await nextTick()
    expect(scene.instance.calls.setMode).toEqual(['top'])
    expect(q('[data-sim-camera="top"]').getAttribute('aria-pressed')).toBe('true')
    expect(q('[data-sim-3d]').getAttribute('data-sim-3d-mode')).toBe('top')
  })

  it('frames one photo when looking through the survey camera', async () => {
    const { el, q, emit } = await mount()
    const frame = () => el.querySelector('.border-emerald-400\\/90')
    expect(frame()).toBeNull()
    q('[data-sim-camera="gimbal"]').click()
    await nextTick()
    expect(frame().style.width).toBe('80%')
    expect(frame().style.height).toBe('60%')
    q('[data-sim-camera="follow"]').click()
    emit(simulation.sample(1))
    await nextTick()
    expect(frame()).toBeNull()
  })

  it('switches layers, the coverage view and the imagery on the scene', async () => {
    const { el, q, props } = await mount()
    const calls = scene.instance.calls
    q('[data-sim-3d-layers]').click()
    await nextTick()
    const boxes = [...el.querySelectorAll('input[type=checkbox]')]
    expect(boxes.length).toBe(4)
    boxes[2].checked = false                                             // photos taken
    boxes[2].dispatchEvent(new Event('change'))
    await nextTick()
    expect(calls.setLayers.at(-1)).toMatchObject({ photos: false, path: true })
    props.coverageMode = 'overlap'
    await nextTick()
    expect(calls.setLayers.at(-1)).toEqual({ coverage: 'overlap' })
    props.ortho = { task: 'T1', title: 'Survey 1' }
    await nextTick()
    expect(calls.setImagery.at(-1)).toEqual({ basemap: BASEMAP, ortho: { task: 'T1', title: 'Survey 1' } })
    props.basemap = { ...BASEMAP, id: 'osm' }
    await nextTick()
    expect(calls.setImagery.length).toBe(2)
  })

  it('refreshes the coverage texture when the shared canvas is repainted', async () => {
    const { coverage } = await mount()
    const before = scene.instance.calls.coverageChanged
    coverage.forEach(fn => fn())
    expect(scene.instance.calls.coverageChanged).toBe(before + 1)
  })

  it('says what the ground is, and when it is flat for lack of data', async () => {
    const withTerrain = await mount()
    withTerrain.q('[data-sim-3d-layers]').click()
    await nextTick()
    expect(withTerrain.el.textContent).toMatch(/Test DEM, about 30 m resolution, ±5 m nominal\. Heights are drawn to scale\./)
    expect(withTerrain.q('[data-sim-3d-flat]')).toBeNull()
    const without = await mount({ simulation: flat })
    expect(without.q('[data-sim-3d-flat]').textContent).toMatch(/No elevation data: the ground is drawn flat at the takeoff height/)
  })

  it('reports imagery that is loading or could not be loaded, and credits the tiles', async () => {
    const { q, el } = await mount()
    scene.instance.options.onStatus({ imagery: 'loading', tiles: 16, loaded: 3, failed: 0 })
    await nextTick()
    expect(q('[data-sim-3d-imagery]').textContent).toBe('Loading imagery… 3/16')
    scene.instance.options.onStatus({ imagery: 'unavailable', tiles: 16, loaded: 0, failed: 16 })
    await nextTick()
    expect(q('[data-sim-3d-imagery]').textContent).toMatch(/could not be loaded; the ground is shown plain/)
    scene.instance.options.onStatus({ imagery: 'ready', tiles: 16, loaded: 16, failed: 0 })
    await nextTick()
    expect(q('[data-sim-3d-imagery]')).toBeNull()
    expect(el.textContent).toContain('Tiles © Esri')
  })

  it('without WebGL it says so and leaves the simulation to the map', async () => {
    scene.fail = 'webgl'
    const { q, frames } = await mount()
    expect(q('[data-sim-3d]').getAttribute('data-sim-3d')).toBe('error')
    expect(q('[role="alert"]').textContent).toMatch(/cannot show 3D graphics \(WebGL is not available\)\. The simulation continues on the map\./)
    expect(frames.size).toBe(0)                                          // nothing subscribed, nothing to break
    expect(q('[data-sim-camera]')).toBeNull()
  })

  it('any other failure to start is reported the same way', async () => {
    scene.fail = 'shader compile failed'
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { q } = await mount()
    expect(q('[role="alert"]').textContent).toMatch(/3D view could not be started\. The simulation continues on the map\./)
    expect(error).toHaveBeenCalled()
    error.mockRestore()
  })

  it('unsubscribes and frees the scene when it goes away', async () => {
    const { frames, coverage } = await mount()
    const s = scene.instance
    expect(frames.size).toBe(1)
    mounted.pop().app.unmount()
    expect(frames.size).toBe(0)
    expect(coverage.size).toBe(0)
    expect(s.calls.disposed).toBe(1)
  })
})
