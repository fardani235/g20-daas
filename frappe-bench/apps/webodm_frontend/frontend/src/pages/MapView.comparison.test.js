import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp, nextTick } from 'vue'

// A comparison plugin (an input with `survey: "reference"`) needs a second
// survey of the project: the run dialog must offer the other completed
// surveys, send the choice as `reference_task`, resolve the comparison
// survey's inputs against *that* survey, and the run row must say what was
// compared and show the numbers the plugin reports.
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'P1' }, query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import MapView from './MapView.vue'

const base = { progress: 100, images: [], dataset: 'D1', dataset_summary: { title: 'Dataset', image_count: 0 } }
const TASKS = [
  { ...base, name: 'T3', title: 'October', status: 'Completed', creation: '2026-10-01 08:00:00', dsm: '/private/files/t3_dsm.tif' },
  { ...base, name: 'T2', title: 'September', status: 'Completed', creation: '2026-09-01 08:00:00', dtm: '/private/files/t2_dtm.tif' },
  { ...base, name: 'T1', title: 'August (processing)', status: 'Running', creation: '2026-08-01 08:00:00' },
]
const CHANGE = {
  name: 'acme.change-detection', op_id: 'acme.change-detection', label: 'Change Detection', runnable: true,
  output_kind: 'vector', render_kind: 'change', settings: {},
  params_schema: { type: 'object', properties: { reference_role: { type: 'string', enum: ['before', 'after'], default: 'before' } } },
  inputs: [
    { name: 'surface', label: 'Surface — this survey', datasets: ['dsm', 'dtm'] },
    { name: 'reference_surface', label: 'Surface — comparison survey', datasets: ['dsm', 'dtm'], survey: 'reference' },
  ],
}
const CONTOURS = {
  name: 'contours', op_id: 'contours', label: 'Contours', runnable: true, output_kind: 'vector',
  render_kind: 'vector', settings: {}, params_schema: { type: 'object', properties: {} },
  inputs: [{ name: 'raster', datasets: ['dsm'] }],
}
const RUN = {
  name: 'R1', plugin: 'acme.change-detection', task: 'T3', reference_task: 'T2', status: 'Completed',
  creation: '2026-10-02 09:00:00', output_kind: 'vector', render_kind: 'change', output_file: '/private/files/r1.geojson',
  output_metadata: JSON.stringify({
    summary: [{ label: 'Fill', value: '604 m³ ± 12' }, { label: 'Cut', value: '1,240 m³ ± 20' }],
    warnings: ['the height offset between the surveys is not constant'],
  }),
}

let runs = []
let posted = []
let mounted = []
function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(MapView)
  app.component('router-link', { template: '<a><slot /></a>' })
  app.mount(el)
  mounted.push({ app, el })
  return el
}
const flush = async () => {
  for (let i = 0; i < 8; i++) await nextTick()
  await new Promise((r) => setTimeout(r, 0))
}
const click = async (node) => {
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flush()
}
const buttonByText = (text) =>
  [...document.body.querySelectorAll('button')].find((b) => b.textContent.trim() === text)

beforeEach(() => {
  runs = []
  posted = []
  global.window.csrf_token = 'x'
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  global.fetch = vi.fn((url, options = {}) => {
    const href = String(url)
    let body = { message: {} }
    if (href.includes('list_tasks')) body = { message: TASKS }
    else if (href.includes('get_task_progress')) {
      const name = JSON.parse(options.body || '{}').task_name
      body = { message: TASKS.find((t) => t.name === name) }
    } else if (href.includes('list_runs')) body = { message: runs }
    else if (href.includes('list_plugins')) body = { message: [CHANGE, CONTOURS] }
    else if (href.includes('run_plugin')) {
      posted.push(JSON.parse(options.body))
      runs = [RUN]
      body = { message: { run: 'R1', status: 'Queued' } }
    } else if (href.includes('get_run_geojson')) body = { message: { type: 'FeatureCollection', features: [] } }
    else if (href.includes('WebODM%20Project')) body = { data: { name: 'P1', title: 'P' } }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
  })
})
afterEach(() => {
  mounted.forEach(({ app, el }) => {
    app.unmount()
    el.remove()
  })
  mounted = []
  document.body.innerHTML = ''
})

async function openTask(el) {
  await flush()
  await click(el.querySelector('.cursor-pointer'))   // the newest task: October
}

describe('MapView comparison runs', () => {
  it('offers the other completed surveys and sends the chosen one', async () => {
    const el = mount()
    await openTask(el)
    await click(buttonByText('Change Detection'))

    const picker = document.body.querySelector('#run-reference')
    expect(picker).toBeTruthy()
    // September only: not October itself, not the survey that is still processing.
    expect([...picker.querySelectorAll('option')].map((o) => [o.value, o.textContent.trim()])).toEqual([
      ['T2', 'September (2026-09-01)'],
    ])
    // The comparison survey's input is resolved against September (it has a DTM, no DSM).
    const inputs = document.body.querySelector('[data-testid="run-inputs"]').textContent
    expect(inputs).toContain('Surface — comparison survey')
    expect(inputs).toContain('DTM')

    await click(buttonByText('Run'))
    expect(posted).toHaveLength(1)
    expect(posted[0]).toMatchObject({
      plugin: 'acme.change-detection',
      task: 'T3',
      reference_task: 'T2',
      inputs: { surface: 'dsm', reference_surface: 'dtm' },
      params: { reference_role: 'before' },
    })
  })

  it('shows what was compared and the numbers the plugin reports', async () => {
    const el = mount()
    await openTask(el)
    await click(buttonByText('Change Detection'))
    await click(buttonByText('Run'))

    const row = el.querySelector('[data-run="R1"]')
    expect(row).toBeTruthy()
    expect(row.textContent).toContain('Change Detection vs September')
    expect(row.querySelector('[data-run-summary]')).toBeNull()

    await click(row.querySelector('[data-run-summary-toggle]'))
    const summary = row.querySelector('[data-run-summary]')
    expect(summary.textContent).toContain('Fill')
    expect(summary.textContent).toContain('604 m³ ± 12')
    expect(summary.textContent).toContain('1,240 m³ ± 20')
    expect(summary.textContent).toContain('the height offset between the surveys is not constant')
  })

  it('leaves ordinary plugins alone', async () => {
    const el = mount()
    await openTask(el)
    await click(buttonByText('Contours'))
    expect(document.body.querySelector('#run-reference')).toBeNull()
    await click(buttonByText('Run'))
    expect(posted[0]).toMatchObject({ plugin: 'contours', task: 'T3', inputs: { raster: 'dsm' } })
    expect('reference_task' in posted[0]).toBe(false)
  })

  it('blocks the run when there is nothing to compare with', async () => {
    const original = TASKS.splice(1, 2)               // October is the only survey
    try {
      const el = mount()
      await openTask(el)
      await click(buttonByText('Change Detection'))
      expect(document.body.querySelector('#run-reference')).toBeNull()
      expect(document.body.querySelector('[data-testid="run-reference"]').textContent)
        .toContain('no other completed survey')
      expect(buttonByText('Run').disabled).toBe(true)
    } finally {
      TASKS.push(...original)
    }
  })
})
