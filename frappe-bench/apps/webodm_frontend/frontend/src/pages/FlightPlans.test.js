import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp, nextTick, reactive } from 'vue'

const routeParams = reactive({})
const push = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: routeParams }),
  useRouter: () => ({ push }),
}))
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

// vi.mock is hoisted above the imports, so the stand-ins it closes over are too.
const api = vi.hoisted(() => ({ listPlans: vi.fn(), deletePlan: vi.fn(), duplicatePlan: vi.fn(), getExportOptions: vi.fn() }))
vi.mock('@/lib/flightplan', async importOriginal => {
  const actual = await importOriginal()
  const out = { ...actual }
  for (const key of Object.keys(api)) out[key] = (...a) => api[key](...a)
  return out
})

import { toast } from '@/lib/toast'
import FlightPlans from './FlightPlans.vue'

const PLANS = [
  { name: 'A1', title: 'North field', description: 'Block 1', project: 'P1', project_title: 'Quarry', plan_type: 'area',
    drone_label: 'DJI Mavic 3 Enterprise (M3E)', camera_label: 'DJI Mavic 3E wide camera', gsd_cm: 2.73, area_m2: 120000,
    distance_m: 3624, duration_s: 500, photo_count: 133, batteries: 1, issue_errors: 0, issue_warnings: 0,
    modified: '2026-10-15 10:00:00', created_by: 'a@example.com' },
  { name: 'B2', title: 'Access road', description: '', project: 'P2', project_title: 'Road works', plan_type: 'corridor',
    drone_label: 'DJI Mini 4 Pro', camera_label: 'DJI Mini 4 Pro camera (12 MP)', gsd_cm: 1.9, area_m2: 96000,
    distance_m: 5200, duration_s: 1500, photo_count: 410, batteries: 2, issue_errors: 1, issue_warnings: 2,
    modified: '2026-10-14 09:00:00', created_by: 'b@example.com' },
]

const flush = async () => {
  for (let i = 0; i < 8; i++) await nextTick()
  await new Promise(r => setTimeout(r, 0))
  await nextTick()
}

let mounted = []
async function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(FlightPlans)
  app.component('router-link', { props: ['to'], template: '<a :href="to"><slot /></a>' })
  app.mount(el)
  mounted.push(app)
  await flush()
  return el
}

const q = sel => document.body.querySelector(sel)

beforeEach(() => {
  delete routeParams.id
  push.mockReset()
  for (const fn of Object.values(api)) fn.mockReset()
  api.listPlans.mockImplementation(async project => PLANS.filter(p => !project || p.project === project))
  api.deletePlan.mockImplementation(async () => ({ deleted: 'A1' }))
  api.duplicatePlan.mockImplementation(async (name, { title }) => ({ name: 'C3', title }))
  global.fetch = vi.fn(async () => ({
    ok: true, json: async () => ({ data: [{ name: 'P1', title: 'Quarry' }, { name: 'P2', title: 'Road works' }] }),
  }))
})

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  toast.success.mockClear()
  toast.error.mockClear()
})

describe('FlightPlans', () => {
  it('lists every plan with its aircraft, estimates and check status', async () => {
    const el = await mount()
    expect(api.listPlans).toHaveBeenCalledWith(undefined)
    const rows = [...el.querySelectorAll('[data-plan-row]')]
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('North field')
    expect(rows[0].textContent).toContain('Quarry')
    expect(rows[0].textContent).toContain('DJI Mavic 3 Enterprise (M3E)')
    expect(rows[0].textContent).toContain('12.0 ha · 2.73 cm/px')
    expect(rows[0].textContent).toContain('133 photos · 8 min · 1 battery')
    expect(rows[0].textContent).toContain('No problems found')
    expect(rows[1].textContent).toContain('Corridor')
    expect(rows[1].textContent).toContain('2 batteries')
    expect(rows[1].textContent).toContain('1 problem to fix')
    expect(rows[0].querySelector('a').getAttribute('href')).toBe('/project/P1/plans/A1')
    expect(el.textContent).toContain('not verified flight-safety information')
  })

  it('scopes to a project and plans straight into it', async () => {
    routeParams.id = 'P1'
    const el = await mount()
    expect(api.listPlans).toHaveBeenCalledWith('P1')
    expect(el.querySelectorAll('[data-plan-row]')).toHaveLength(1)
    expect(el.querySelector('h1').textContent).toBe('Flight plans · Quarry')
    el.querySelector('[data-new-plan]').click()
    await flush()
    expect(push).toHaveBeenCalledWith('/project/P1/plans/new')
  })

  it('asks which project a new plan belongs to on the global list', async () => {
    const el = await mount()
    el.querySelector('[data-new-plan]').click()
    await flush()
    const select = q('#new-plan-project')
    expect([...select.options].map(o => o.textContent.trim())).toEqual(['Choose a project…', 'Quarry', 'Road works'])
    select.value = 'P2'
    select.dispatchEvent(new Event('change'))
    await flush()
    const start = [...document.body.querySelectorAll('button')].find(b => b.textContent.includes('Start planning'))
    start.click()
    await flush()
    expect(push).toHaveBeenCalledWith('/project/P2/plans/new')
  })

  it('opens a plan straight into its flight simulation', async () => {
    api.listPlans.mockImplementation(async () => [{ ...PLANS[0], waypoint_count: 14 }, { ...PLANS[1], waypoint_count: 0 }])
    await mount()
    const computed = q('[data-plan-simulate="A1"]')
    expect(computed.title).toBe('Simulate the flight in 2D and 3D')
    expect(computed.disabled).toBe(false)
    computed.click()
    expect(push).toHaveBeenCalledWith({ path: '/project/P1/plans/A1', query: { simulate: '1' } })
    // A plan with no computed route has nothing to simulate.
    expect(q('[data-plan-simulate="B2"]').disabled).toBe(true)
  })

  it('duplicates into another project to reuse a plan', async () => {
    const el = await mount()
    el.querySelector('[data-plan-row] button[title^="Duplicate"]').click()
    await flush()
    expect(q('#dup-title').value).toBe('North field (copy)')
    const project = q('#dup-project')
    expect(project.value).toBe('P1')
    project.value = 'P2'
    project.dispatchEvent(new Event('change'))
    await flush()
    const go = [...document.body.querySelectorAll('button')].find(b => b.textContent.trim() === 'Duplicate')
    go.click()
    await flush()
    expect(api.duplicatePlan).toHaveBeenCalledWith('A1', { project: 'P2', title: 'North field (copy)' })
    expect(toast.success).toHaveBeenCalledWith('Duplicated as "North field (copy)"')
    expect(api.listPlans).toHaveBeenCalledTimes(2)
  })

  it('deletes after confirmation', async () => {
    const el = await mount()
    el.querySelector('[data-plan-row] button[title="Delete"]').click()
    await flush()
    expect(document.body.textContent).toContain('Files already exported from it are not affected')
    const confirm = [...document.body.querySelectorAll('button')].find(b => b.textContent.trim() === 'Delete')
    confirm.click()
    await flush()
    expect(api.deletePlan).toHaveBeenCalledWith('A1')
    expect(toast.success).toHaveBeenCalledWith('Flight plan deleted')
  })

  it('explains itself when there is nothing yet, and reports a load failure', async () => {
    api.listPlans.mockImplementation(async () => [])
    let el = await mount()
    expect(el.querySelector('[data-plans-empty]').textContent).toContain('No flight plans yet')
    api.listPlans.mockRejectedValue(new Error('no_organization'))
    el = await mount()
    expect(el.textContent).toContain('no_organization')
  })
})
