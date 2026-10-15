import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import fixture from '@/lib/__fixtures__/flightplan.json'

const getExportOptions = vi.fn()
const exportPlan = vi.fn()
const saveBlob = vi.fn()
vi.mock('@/lib/flightplan', async importOriginal => {
  const actual = await importOriginal()
  return {
    ...actual,
    getExportOptions: (...a) => getExportOptions(...a),
    exportPlan: (...a) => exportPlan(...a),
    saveBlob: (...a) => saveBlob(...a),
  }
})
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

import { toast } from '@/lib/toast'
import ExportDialog from './ExportDialog.vue'

const flush = async () => {
  for (let i = 0; i < 6; i++) await nextTick()
  await new Promise(r => setTimeout(r, 0))
  await nextTick()
}

let mounted = []
async function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const open = ref(false)
  const app = createApp({
    render: () => h(ExportDialog, { open: open.value, plan: { name: 'PLAN1', title: 'North field' },
      'onUpdate:open': v => { open.value = v } }),
  })
  app.mount(el)
  mounted.push(app)
  open.value = true          // the dialog loads its options when it opens
  await flush()
  return { open }
}

const q = sel => document.body.querySelector(sel)
const qa = sel => [...document.body.querySelectorAll(sel)]
const clone = v => JSON.parse(JSON.stringify(v))

beforeEach(() => {
  getExportOptions.mockReset()
  exportPlan.mockReset()
  saveBlob.mockReset()
  getExportOptions.mockImplementation(async () => clone(fixture.export))
  exportPlan.mockImplementation(async () => ({ blob: new Blob(['x']), filename: 'north-field-dji-wpml.kmz', files: 1 }))
})

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  toast.error.mockClear()
  toast.success.mockClear()
})

describe('ExportDialog', () => {
  it('offers the formats this aircraft can use and explains the ones it cannot', async () => {
    await mount()
    expect(getExportOptions).toHaveBeenCalledWith('PLAN1', {})
    const rows = Object.fromEntries(qa('[data-format]').map(n => [n.dataset.format, n]))
    expect(rows['dji-wpml'].dataset.available).toBe('true')
    expect(rows['dji-wpml'].textContent).toContain('Supported')
    expect(rows['dji-wpml'].textContent).toContain('DJI Pilot 2')
    // A Mavic 3E cannot use MAVLink or Litchi files: shown, disabled, with the reason.
    for (const id of ['qgc-plan', 'mavlink-wpl', 'litchi-csv']) {
      expect(rows[id].dataset.available).toBe('false')
      expect(rows[id].querySelector('input').disabled).toBe(true)
      expect(rows[id].textContent).toMatch(/does not read this format/)
    }
    expect(rows['kml-boundary'].textContent).toContain('Boundary import')
    expect(rows.geojson.textContent).toContain('Reference')
    // The flyable format for the aircraft is preselected.
    expect(rows['dji-wpml'].querySelector('input').checked).toBe(true)
  })

  it('requires the warnings to be acknowledged before a mission file downloads', async () => {
    await mount()
    const download = q('[data-export-download]')
    expect(download.disabled).toBe(true)
    const detail = q('[data-export-detail]')
    expect(detail.textContent).toContain('beyond the 500 m')           // the plan's own warning is repeated here
    const ack = q('[data-export-acknowledge] input')
    expect(q('[data-export-acknowledge]').textContent).toContain('not verified')
    ack.click()
    await flush()
    expect(download.disabled).toBe(false)
    download.click()
    await flush()
    expect(exportPlan).toHaveBeenCalledWith('PLAN1', {
      format: 'dji-wpml', trigger: 'distance', acknowledged: true, firmware: undefined,
    })
    expect(saveBlob).toHaveBeenCalledTimes(1)
    expect(saveBlob.mock.calls[0][1]).toBe('north-field-dji-wpml.kmz')
    expect(toast.success).toHaveBeenCalled()
  })

  it('boundary and reference files download without acknowledgement', async () => {
    await mount()
    q('[data-format="kml-boundary"] input').click()
    await flush()
    expect(q('[data-export-acknowledge]')).toBeNull()
    expect(q('[data-export-detail]').textContent).toContain('Enter these in the flight app')
    const download = q('[data-export-download]')
    expect(download.disabled).toBe(false)
    download.click()
    await flush()
    expect(exportPlan).toHaveBeenCalledWith('PLAN1', {
      format: 'kml-boundary', trigger: undefined, acknowledged: false, firmware: undefined,
    })
  })

  it('a blocked plan gives no mission file and says why', async () => {
    getExportOptions.mockImplementation(async () => clone(fixture.export_blocked))
    await mount()
    expect(q('[data-export-blocked]').textContent).toContain('Flight speed 18 m/s is above')
    expect(q('[data-export-acknowledge]')).toBeNull()
    expect(q('[data-export-download]').disabled).toBe(true)
    // The boundary stays available so the area can be planned in the vendor app.
    q('[data-format="kml-boundary"] input').click()
    await flush()
    expect(q('[data-export-download]').disabled).toBe(false)
  })

  it('changing the photo trigger re-asks the server and resets the acknowledgement', async () => {
    await mount()
    q('[data-export-acknowledge] input').click()
    await flush()
    const select = q('#export-trigger')
    expect([...select.options].map(o => o.value)).toEqual(['distance', 'time', 'waypoint', 'manual'])
    select.value = 'waypoint'
    select.dispatchEvent(new Event('change'))
    await flush()
    expect(getExportOptions).toHaveBeenLastCalledWith('PLAN1', expect.objectContaining({ 'dji-wpml': 'waypoint' }))
    expect(q('[data-export-acknowledge] input').checked).toBe(false)
    expect(q('[data-export-download]').disabled).toBe(true)
  })

  it('shows how to load the file and the notice', async () => {
    await mount()
    expect(q('[data-export-detail] ol').textContent).toContain('DJI Pilot 2')
    expect(q('[data-export-dialog]').textContent).toContain('not verified flight-safety information')
  })

  it('surfaces a refusal from the server', async () => {
    exportPlan.mockRejectedValueOnce(new Error('This plan has warnings that must be acknowledged'))
    await mount()
    q('[data-export-acknowledge] input').click()
    await flush()
    q('[data-export-download]').click()
    await flush()
    expect(toast.error).toHaveBeenCalledWith('This plan has warnings that must be acknowledged')
    expect(saveBlob).not.toHaveBeenCalled()
  })

  it('reports a failure to load the options', async () => {
    getExportOptions.mockRejectedValueOnce(new Error('This plan has no computed mission yet'))
    await mount()
    expect(document.body.textContent).toContain('This plan has no computed mission yet')
    expect(q('[data-export-download]').disabled).toBe(true)
  })
})
