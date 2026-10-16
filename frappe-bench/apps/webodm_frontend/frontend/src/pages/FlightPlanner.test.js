import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp, nextTick, reactive } from 'vue'
import fixture from '@/lib/__fixtures__/flightplan.json'

// The planner page: form state -> definition -> server preview -> estimates
// and findings. Leaflet's vector renderer cannot mount in jsdom, so the shape
// editor and the mission layers are stand-ins; everything between them (what
// is sent, when, and what is shown) is the real page.

const routeParams = reactive({ id: 'P1', planId: 'new' })
const push = vi.fn()
const replace = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: routeParams }),
  useRouter: () => ({ push, replace }),
  onBeforeRouteLeave: () => {},
}))
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))
vi.mock('@/lib/datasets', () => ({ listTasks: vi.fn(async () => []) }))

const editor = { onChange: null, shape: null, started: [] }
vi.mock('@/composables/useShapeEditor', async () => {
  const { reactive: r } = await import('vue')
  return {
    useShapeEditor: (_getMap, { onChange }) => {
      editor.onChange = onChange
      const state = r({ drawing: false, type: 'area', count: 0 })
      return {
        state,
        start: type => { editor.started.push(type); state.drawing = true; state.type = type },
        finish: () => { state.drawing = false; return true },
        cancel: () => { state.drawing = false },
        clear: () => {},
        setShape: (type, vertices) => { editor.shape = { type, vertices } },
        setEditable: () => {},
        bounds: () => null,
        destroy: () => {},
        getVertices: () => [],
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

const api = {
  getCatalog: vi.fn(), getElevationSources: vi.fn(), previewPlan: vi.fn(), savePlan: vi.fn(), getPlan: vi.fn(),
  duplicatePlan: vi.fn(), getExportOptions: vi.fn(),
}
vi.mock('@/lib/flightplan', async importOriginal => {
  const actual = await importOriginal()
  const out = { ...actual }
  for (const key of ['getCatalog', 'getElevationSources', 'previewPlan', 'savePlan', 'getPlan', 'duplicatePlan', 'getExportOptions']) {
    out[key] = (...a) => api[key](...a)
  }
  return out
})

import { toast } from '@/lib/toast'
import FlightPlanner from './FlightPlanner.vue'

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

// Let pending promises and the preview debounce run.
async function settle(ms = 400) {
  for (let i = 0; i < 6; i++) await nextTick()
  await vi.advanceTimersByTimeAsync(ms)
  for (let i = 0; i < 6; i++) await nextTick()
}

async function draw(el, vertices = VERTICES) {
  editor.onChange(vertices, { final: true })
  await settle()
  return el
}

function setInput(el, selector, value) {
  const input = el.querySelector(selector)
  input.value = String(value)
  input.dispatchEvent(new Event('input'))
}

function setSelect(el, selector, value) {
  const select = el.querySelector(selector)
  select.value = value
  select.dispatchEvent(new Event('change'))
}

const lastPreview = () => api.previewPlan.mock.calls.at(-1)[0]

beforeEach(() => {
  vi.useFakeTimers()
  routeParams.id = 'P1'
  routeParams.planId = 'new'
  editor.onChange = null
  editor.shape = null
  editor.started = []
  push.mockReset()
  replace.mockReset()
  for (const fn of Object.values(api)) fn.mockReset()
  api.getCatalog.mockImplementation(async () => clone(fixture.catalog))
  api.getElevationSources.mockImplementation(async () => [
    { id: 'auto', label: 'Open Topo Data · mapzen', note: 'Global ~30 m elevation model.' },
    { id: 'task:T1:dsm', label: 'Survey 1 · Surface model (DSM)', note: 'This project\'s own survey.' },
    { id: 'none', label: 'None (assume flat ground)', note: '' },
  ])
  api.previewPlan.mockImplementation(async () => ({ ok: true, mission: clone(fixture.mission) }))
  api.savePlan.mockImplementation(async ({ title, definition }) => ({
    name: 'PLAN1', title, description: '', project: 'P1', definition, mission: clone(fixture.mission),
  }))
  global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ data: { name: 'P1', title: 'Quarry' } }) }))
  global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
  window.confirm = vi.fn(() => true)
})

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  vi.useRealTimers()
  toast.error.mockClear()
  toast.success.mockClear()
})

describe('FlightPlanner', () => {
  it('starts with a hint, a default aircraft and no request', async () => {
    const el = await mount()
    expect(el.querySelector('[data-plan-hint]').textContent).toContain('Find your site with the search box')
    expect(el.querySelector('[data-plan-drone]').value).toBe('dji-mavic-3e')
    expect(el.querySelector('[data-plan-camera]').value).toBe('dji-mavic-3e-wide')
    expect(el.querySelector('[data-plan-support]').textContent).toContain('DJI Pilot 2')
    expect(el.querySelector('[data-plan-altitude] input').value).toBe('100')
    // The resolution read-out is derived from the height and the camera.
    expect(el.querySelector('[data-plan-gsd] input').value).toBe('2.73')
    expect(api.previewPlan).not.toHaveBeenCalled()
    expect(el.querySelector('[data-plan-save]').disabled).toBe(true)
    expect(el.querySelector('[data-plan-status]').textContent).toBe('Not computed')
  })

  it('computes as soon as the shape is complete and shows estimates as estimates', async () => {
    const el = await mount()
    await draw(el, VERTICES.slice(0, 2))
    expect(api.previewPlan).not.toHaveBeenCalled()                    // two corners are not an area
    await draw(el)
    const req = lastPreview()
    expect(req.project).toBe('P1')
    expect(req.definition.type).toBe('area')
    expect(req.definition.geometry.coordinates[0]).toHaveLength(5)
    expect(req.definition.drone).toBe('dji-mavic-3e')
    expect(req.definition.params).toEqual({})                         // nothing changed from the defaults
    const estimates = Object.fromEntries([...el.querySelectorAll('[data-estimate]')]
      .map(n => [n.dataset.estimate, n.querySelector('dd').textContent]))
    expect(estimates.photos).toBe('196')
    expect(estimates.area).toBe('18.0 ha')
    expect(estimates.batteries).toBe('1')
    expect(el.querySelector('[data-plan-estimates]').textContent).toContain('Planning estimates')
    expect(el.querySelector('[data-plan-status]').textContent).toBe('2 warnings to review')
    expect(el.querySelector('[data-plan-terrain]').textContent).toContain('(estimate)')
    expect(el.querySelectorAll('[data-finding]').length).toBe(fixture.mission.issues.length)
    expect(el.querySelector('[data-assurance-topic="airspace"]').dataset.status).toBe('not_checked')
  })

  it('answers from cached terrain first, then loads it', async () => {
    const pending = clone(fixture.mission)
    pending.terrain_pending = true
    pending.terrain = null
    api.previewPlan.mockImplementation(async ({ terrain }) =>
      ({ ok: true, mission: terrain === 'cached' ? pending : clone(fixture.mission) }))
    const el = await mount()
    await draw(el)
    expect(api.previewPlan.mock.calls.map(c => c[0].terrain)).toEqual(['cached', 'fetch'])
    expect(el.querySelector('[data-plan-terrain]')).not.toBeNull()
  })

  it('debounces edits and ignores a stale answer', async () => {
    const el = await mount()
    await draw(el)
    api.previewPlan.mockClear()
    setInput(el, '#fp-front', 75)
    await vi.advanceTimersByTimeAsync(100)
    setInput(el, '#fp-front', 70)
    await vi.advanceTimersByTimeAsync(100)
    setInput(el, '#fp-side', 60)
    await settle()
    expect(api.previewPlan).toHaveBeenCalledTimes(1)
    expect(lastPreview().definition.params).toEqual({ front_overlap: 70, side_overlap: 60 })

    // A slow first answer must not overwrite a newer one.
    const slow = clone(fixture.mission)
    slow.stats.photo_count = 999
    let release
    api.previewPlan.mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ ok: true, mission: slow }) }))
    setInput(el, '#fp-front', 72)
    await settle()
    setInput(el, '#fp-front', 74)
    await settle()
    release()
    await settle()
    expect(el.querySelector('[data-estimate="photos"] dd').textContent).toBe('196')
  })

  it('height and resolution are one linked input', async () => {
    const el = await mount()
    await draw(el)
    setInput(el, '[data-plan-gsd] input', 2)
    await settle()
    expect(el.querySelector('[data-plan-altitude] input').value).toBe('73.2')      // derived from 2 cm/px
    let params = lastPreview().definition.params
    expect(params).toEqual({ altitude_from: 'gsd', gsd_cm: 2 })

    setInput(el, '[data-plan-altitude] input', 60)
    await settle()
    expect(el.querySelector('[data-plan-gsd] input').value).toBe('1.64')
    params = lastPreview().definition.params
    expect(params).toEqual({ altitude_m: 60 })
  })

  it('changing the aircraft changes the camera choices and recomputes', async () => {
    const el = await mount()
    await draw(el)
    setSelect(el, '[data-plan-drone]', 'dji-matrice-350-rtk')
    await settle()
    const camera = el.querySelector('[data-plan-camera]')
    expect(camera.value).toBe('dji-zenmuse-p1-35mm')
    expect([...camera.options].map(o => o.value)).toContain('dji-zenmuse-p1-50mm')
    expect(lastPreview().definition).toMatchObject({ drone: 'dji-matrice-350-rtk', camera: 'dji-zenmuse-p1-35mm' })
    // Same height, different camera: the resolution read-out follows.
    expect(el.querySelector('[data-plan-gsd] input').value).toBe('1.25')

    setSelect(el, '[data-plan-camera]', 'dji-zenmuse-p1-50mm')
    await settle()
    expect(lastPreview().definition.camera).toBe('dji-zenmuse-p1-50mm')

    // An aircraft that cannot fly missions says so before any export.
    setSelect(el, '[data-plan-drone]', 'dji-mini-3')
    await settle()
    expect(el.querySelector('[data-plan-support]').textContent).toMatch(/No autonomous waypoint flight/)
    setSelect(el, '[data-plan-drone]', 'wingtra-wingtraone-gen2')
    await settle()
    expect(el.querySelector('[data-plan-support]').textContent).toBe('WingtraPilot plans its own lines from the boundary.')
  })

  it('corridor mode asks for a width and sends a line', async () => {
    const el = await mount()
    await draw(el)
    el.querySelector('[data-plan-type] [data-type="corridor"]').click()
    await settle()
    expect(editor.shape.type).toBe('corridor')                        // the corners carry over
    expect(el.querySelector('#fp-width')).not.toBeNull()
    expect(el.querySelector('#fp-direction')).toBeNull()              // no sweep direction for a corridor
    setInput(el, '#fp-width', 80)
    await settle()
    const d = lastPreview().definition
    expect(d.type).toBe('corridor')
    expect(d.geometry.type).toBe('LineString')
    expect(d.corridor_width_m).toBe(80)
  })

  it('terrain mode and elevation source are part of the definition', async () => {
    const el = await mount()
    await draw(el)
    el.querySelector('[data-plan-altitude-mode] [data-mode="terrain"]').click()
    await settle()
    expect(lastPreview().definition.params).toEqual({ altitude_mode: 'terrain' })
    expect(el.querySelector('#fp-tolerance')).not.toBeNull()
    expect(el.querySelector('label[for="fp-altitude"]').textContent).toBe('Height above ground')
    const source = el.querySelector('[data-plan-elevation]')
    expect([...source.options].map(o => o.value)).toEqual(['auto', 'task:T1:dsm', 'none'])
    setSelect(el, '[data-plan-elevation]', 'task:T1:dsm')
    await settle()
    expect(lastPreview().definition.elevation_source).toBe('task:T1:dsm')
  })

  it('shows why a plan cannot be computed and marks the field', async () => {
    api.previewPlan.mockImplementation(async () => ({
      ok: false, error: { message: 'Flight height must be at least 5 m', field: 'altitude_m' },
    }))
    const el = await mount()
    await draw(el)
    expect(el.querySelector('[data-plan-error]').textContent).toBe('Flight height must be at least 5 m')
    expect(el.querySelector('[data-plan-altitude] input').getAttribute('aria-invalid')).toBe('true')
    expect(el.querySelector('[data-estimate]')).toBeNull()
    expect(el.querySelector('[data-plan-save]').disabled).toBe(true)
  })

  it('saves into the project and becomes that plan', async () => {
    const el = await mount()
    await draw(el)
    expect(el.querySelector('[data-plan-save]').disabled).toBe(true)  // no title yet
    setInput(el, '[data-plan-title]', 'North field')
    await settle()
    el.querySelector('[data-plan-save]').click()
    await settle()
    const sent = api.savePlan.mock.calls[0][0]
    expect(sent).toMatchObject({ name: undefined, project: 'P1', title: 'North field' })
    expect(sent.definition.geometry.type).toBe('Polygon')
    expect(replace).toHaveBeenCalledWith('/project/P1/plans/PLAN1')
    expect(toast.success).toHaveBeenCalledWith('Flight plan saved')
    expect(el.textContent).toContain('Saved')
    expect(el.querySelector('[data-plan-save]').disabled).toBe(true)  // nothing to save

    // A later change makes it dirty again and saves under the same name.
    setInput(el, '#fp-side', 75)
    await settle()
    expect(el.textContent).toContain('Unsaved changes')
    el.querySelector('[data-plan-save]').click()
    await settle()
    expect(api.savePlan.mock.calls[1][0].name).toBe('PLAN1')
  })

  it('export saves first: files are made from the saved plan', async () => {
    api.getExportOptions.mockImplementation(async () => clone(fixture.export))
    const el = await mount()
    await draw(el)
    el.querySelector('[data-plan-export]').click()
    await settle()
    expect(api.savePlan).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('Give the plan a title first'))

    setInput(el, '[data-plan-title]', 'North field')
    await settle()
    el.querySelector('[data-plan-export]').click()
    await settle()
    expect(api.savePlan).toHaveBeenCalledTimes(1)
    expect(api.getExportOptions).toHaveBeenCalledWith('PLAN1', {})
    expect(document.body.querySelector('[data-export-dialog]')).not.toBeNull()
  })

  it('opens a saved plan as saved, without recomputing', async () => {
    routeParams.planId = 'PLAN9'
    api.getPlan.mockImplementation(async () => ({
      name: 'PLAN9', title: 'Access road survey', description: '', project: 'P1',
      definition: { ...clone(fixture.definition), params: { altitude_m: 100 } }, mission: clone(fixture.mission),
    }))
    const el = await mount()
    expect(api.getPlan).toHaveBeenCalledWith('PLAN9')
    expect(el.querySelector('[data-plan-title]').value).toBe('Access road survey')
    expect(editor.shape.vertices).toHaveLength(4)
    expect(el.querySelector('[data-estimate="photos"] dd').textContent).toBe('196')
    expect(api.previewPlan).not.toHaveBeenCalled()
    expect(el.textContent).toContain('Saved')
    // Editing it previews against the saved plan (so a deleted profile's snapshot still resolves).
    setInput(el, '#fp-front', 75)
    await settle()
    expect(lastPreview().plan).toBe('PLAN9')
  })

  it('goes back to the list when the plan cannot be opened', async () => {
    routeParams.planId = 'GONE'
    api.getPlan.mockRejectedValue(new Error('Not permitted'))
    await mount()
    expect(toast.error).toHaveBeenCalledWith('Not permitted')
    expect(replace).toHaveBeenCalledWith('/project/P1/plans')
  })

  it('starts drawing the selected shape and explains how', async () => {
    const el = await mount()
    el.querySelector('[data-plan-draw]').click()
    await settle()
    expect(editor.started).toEqual(['area'])
    expect(el.querySelector('[data-plan-draw-hint]').textContent).toContain('Click each corner on the map')
    expect(el.querySelector('[data-plan-finish]')).not.toBeNull()
  })
})
