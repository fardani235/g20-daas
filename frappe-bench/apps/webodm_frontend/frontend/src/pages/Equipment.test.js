import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp, nextTick } from 'vue'
import fixture from '@/lib/__fixtures__/flightplan.json'

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

// vi.mock is hoisted above the imports, so the stand-ins it closes over are too.
const api = vi.hoisted(() => ({
  getCatalog: vi.fn(), saveDrone: vi.fn(), saveCamera: vi.fn(), deleteDrone: vi.fn(), deleteCamera: vi.fn(),
}))
vi.mock('@/lib/flightplan', async importOriginal => {
  const actual = await importOriginal()
  const out = { ...actual }
  for (const key of Object.keys(api)) out[key] = (...a) => api[key](...a)
  return out
})

import { toast } from '@/lib/toast'
import Equipment from './Equipment.vue'

const clone = v => JSON.parse(JSON.stringify(v))
const CUSTOM_CAMERA = {
  id: 'custom:cam1', docname: 'cam1', label: 'Sony RX0 II', name: 'RX0 II', manufacturer: 'Sony', source: 'custom',
  editable: true, sensor_width_mm: 13.2, sensor_height_mm: 8.8, image_width_px: 4800, image_height_px: 3200,
  focal_length_mm: 7.9, min_interval_s: 1, shutter: 'electronic', notes: '',
}
const CUSTOM_DRONE = {
  id: 'custom:d1', docname: 'd1', label: 'Acme Survey Hexa', model: 'Survey Hexa', manufacturer: 'Acme', source: 'custom',
  editable: true, airframe: 'multirotor', rtk: 'none', max_flight_time_min: 32, endurance_factor: 0.8, max_speed_ms: 16,
  max_mission_speed_ms: null, max_wind_ms: null, cameras: ['custom:cam1'], default_camera: 'custom:cam1',
  camera_choices: ['custom:cam1'], ecosystem: 'mavlink', notes: '',
  mission: { waypoints: true, per_waypoint_altitude: true, formats: [
    { id: 'qgc-plan', level: 'official', app: 'QGroundControl' },
    { id: 'mavlink-wpl', level: 'official', app: 'Mission Planner / QGroundControl' }] },
}

function catalogWithCustom() {
  const c = clone(fixture.catalog)
  c.drones.push(clone(CUSTOM_DRONE))
  c.cameras.push(clone(CUSTOM_CAMERA))
  return c
}

const flush = async () => {
  for (let i = 0; i < 8; i++) await nextTick()
  await new Promise(r => setTimeout(r, 0))
  await nextTick()
}

let mounted = []
async function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(Equipment)
  app.mount(el)
  mounted.push(app)
  await flush()
  return el
}

const q = sel => document.body.querySelector(sel)
const setInput = (sel, value) => {
  const input = q(sel)
  input.value = String(value)
  input.dispatchEvent(new Event('input'))
}
const setSelect = (sel, value) => {
  const select = q(sel)
  select.value = value
  select.dispatchEvent(new Event('change'))
}
const button = text => [...document.body.querySelectorAll('button')].find(b => b.textContent.trim() === text)

beforeEach(() => {
  push.mockReset()
  for (const fn of Object.values(api)) fn.mockReset()
  api.getCatalog.mockImplementation(async () => catalogWithCustom())
  api.saveDrone.mockImplementation(async p => ({ ...p, id: 'custom:new', label: `${p.manufacturer} ${p.model}`.trim() }))
  api.saveCamera.mockImplementation(async p => ({ ...p, id: 'custom:newcam', label: `${p.manufacturer} ${p.name}`.trim() }))
  api.deleteDrone.mockImplementation(async () => ({ deleted: 'd1' }))
  window.confirm = vi.fn(() => true)
})

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  toast.success.mockClear()
  toast.error.mockClear()
})

describe('Equipment', () => {
  it('lists aircraft with what each can be given, the organizations own first', async () => {
    const el = await mount()
    const rows = [...el.querySelectorAll('[data-drone-row]')]
    expect(rows[0].dataset.source).toBe('custom')
    expect(rows[0].textContent).toContain('Acme Survey Hexa')
    expect(rows[0].textContent).toContain('Yours')
    expect(rows[0].textContent).toContain('Sony RX0 II')                 // camera ids are shown by name
    const text = id => rows.find(r => r.textContent.includes(id)).textContent
    expect(text('DJI Mavic 3 Enterprise')).toContain('Supported')
    expect(text('DJI Mini 4 Pro')).toContain('Unofficial')
    expect(text('WingtraOne')).toContain('Boundary import')
    expect(text('DJI Mini 3')).toContain('No autonomous flight')
    expect(text('PX4 / ArduPilot multirotor')).toContain('Any camera')
    // Unknown figures are stated, not shown as zero.
    expect(text('PX4 / ArduPilot multirotor')).toContain('wind limit not published')
    expect(text('DJI Mavic 3 Enterprise')).toContain('45 min rated')
    expect(text('DJI Mavic 3 Enterprise')).toContain('≈ 27 min usable')
  })

  it('filters by name', async () => {
    const el = await mount()
    const input = el.querySelector('input[aria-label="Filter equipment"]')
    input.value = 'wingtra'
    input.dispatchEvent(new Event('input'))
    await flush()
    expect(el.querySelectorAll('[data-drone-row]')).toHaveLength(1)
    input.value = 'zzz'
    input.dispatchEvent(new Event('input'))
    await flush()
    expect(el.textContent).toContain('No aircraft matches “zzz”.')
  })

  it('lists cameras with the resolution they give', async () => {
    const el = await mount()
    el.querySelector('[data-tab="cameras"]').click()
    await flush()
    const rows = [...el.querySelectorAll('[data-camera-row]')]
    expect(rows[0].textContent).toContain('Sony RX0 II')
    const p1 = rows.find(r => r.textContent.includes('Zenmuse P1 (35 mm)'))
    expect(p1.textContent).toContain('1.25 cm/px')
    const a6000 = rows.find(r => r.textContent.includes('α6000'))
    expect(a6000.textContent).toContain('not published')
  })

  it('adds a drone: the flight app family decides what it can be given', async () => {
    const el = await mount()
    el.querySelector('[data-add-drone]').click()
    await flush()
    const save = button('Save drone')
    expect(save.disabled).toBe(true)
    expect(q('[data-drone-problems]').textContent).toContain('Choose at least one camera.')
    setInput('#drone-model', 'Mapper X8')
    setInput('#drone-time', 28)
    setInput('#drone-vmax', 14)
    q('[data-drone-cameras] input[type=checkbox]').click()               // the organization's own camera sorts first
    await flush()
    expect(save.disabled).toBe(false)

    // A DJI app needs the aircraft's WPML id before a route can be exported.
    setSelect('#drone-ecosystem', 'dji-enterprise')
    await flush()
    expect(q('[data-drone-problems]').textContent).toContain('DJI WPML aircraft id')
    expect(save.disabled).toBe(true)
    setInput('#drone-wpml', 77)
    await flush()
    save.click()
    await flush()
    const sent = api.saveDrone.mock.calls[0][0]
    expect(sent).toMatchObject({ model: 'Mapper X8', max_flight_time_min: 28, max_speed_ms: 14, ecosystem: 'dji-enterprise',
      mission: { wpml: { drone_enum: 77, drone_sub_enum: 0 } } })
    expect(sent.cameras).toHaveLength(1)
    expect(sent).not.toHaveProperty('wpml_drone_enum')
    expect(toast.success).toHaveBeenCalledWith('Drone "Mapper X8" saved')
    expect(api.getCatalog).toHaveBeenCalledTimes(2)                       // the list refreshes
  })

  it('a fixed-wing must state its turn radius', async () => {
    const el = await mount()
    el.querySelector('[data-add-drone]').click()
    await flush()
    setSelect('#drone-airframe', 'fixed-wing')
    await flush()
    expect(q('[data-drone-problems]').textContent).toContain('turn radius')
    expect(q('#drone-radius')).not.toBeNull()
  })

  it('copies a built-in aircraft into an editable profile with its capabilities', async () => {
    const el = await mount()
    const row = [...el.querySelectorAll('[data-drone-row]')].find(r => r.textContent.includes('DJI Mini 4 Pro'))
    row.querySelector('button').click()
    await flush()
    expect(q('#drone-model').value).toBe('Mini 4 Pro (copy)')
    expect(q('#drone-time').value).toBe('34')
    expect(q('#drone-ecosystem').value).toBe('dji-fly')
    expect(q('#drone-wpml').value).toBe('68')
    button('Save drone').click()
    await flush()
    const sent = api.saveDrone.mock.calls[0][0]
    expect(sent.docname).toBeNull()                                       // a new profile, not an edit
    expect(sent.ecosystem).toBe('dji-fly')
    expect(sent.cameras).toEqual(['dji-mini-4-pro-12mp', 'dji-mini-4-pro-48mp'])
  })

  it('edits and deletes the organizations own profiles only', async () => {
    const el = await mount()
    const own = el.querySelector('[data-drone-row][data-source="custom"]')
    own.querySelector('button[title="Edit"]').click()
    await flush()
    expect(q('#drone-model').value).toBe('Survey Hexa')
    button('Save drone').click()
    await flush()
    expect(api.saveDrone.mock.calls[0][0].docname).toBe('d1')
    own.querySelector('button[title="Delete"]').click()
    await flush()
    expect(window.confirm).toHaveBeenCalled()
    expect(api.deleteDrone).toHaveBeenCalledWith('d1')
    const builtin = el.querySelector('[data-drone-row][data-source="builtin"]')
    expect(builtin.querySelector('button[title="Edit"]')).toBeNull()
    expect(builtin.querySelector('button[title="Delete"]')).toBeNull()
  })

  it('adds a camera and lets the user check it against a known figure', async () => {
    const el = await mount()
    el.querySelector('[data-tab="cameras"]').click()
    await flush()
    el.querySelector('[data-add-camera]').click()
    await flush()
    expect(button('Save camera').disabled).toBe(true)
    setInput('#cam-name', 'P4P clone')
    setInput('#cam-sw', 13.2)
    setInput('#cam-sh', 8.8)
    setInput('#cam-iw', 5472)
    setInput('#cam-ih', 3648)
    setInput('#cam-f', 8.8)
    await flush()
    expect(q('[data-camera-check]').textContent).toContain('2.74 cm/px')
    expect(q('[data-camera-check]').textContent).toContain('150 × 100 m')
    button('Save camera').click()
    await flush()
    expect(api.saveCamera.mock.calls[0][0]).toMatchObject({ name: 'P4P clone', focal_length_mm: 8.8, min_interval_s: null })
  })

  it('surfaces a refusal from the server', async () => {
    api.saveDrone.mockRejectedValueOnce(new Error('Drone profile: unknown camera custom:gone'))
    const el = await mount()
    el.querySelector('[data-drone-row][data-source="custom"] button[title="Edit"]').click()
    await flush()
    button('Save drone').click()
    await flush()
    expect(toast.error).toHaveBeenCalledWith('Drone profile: unknown camera custom:gone')
    expect(q('[data-drone-form]')).not.toBeNull()                         // the dialog stays open
  })
})
