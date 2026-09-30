import { describe, it, expect, vi, afterEach } from 'vitest'
import { createApp, ref, nextTick } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'

const rows = ref([])
const deleteDataset = vi.fn(async () => ({ deleted: 'x' }))

vi.mock('@/lib/datasets', async importOriginal => {
  const actual = await importOriginal()
  return {
    ...actual,
    listDatasets: () => Promise.resolve(rows.value),
    deleteDataset: (...a) => deleteDataset(...a),
    createDataset: vi.fn(async () => ({ name: 'NEW', title: 'New' })),
  }
})

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from '@/lib/toast'
import Datasets from './Datasets.vue'

let mounted = []
async function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/datasets', component: Datasets },
      { path: '/datasets/:id', component: { template: '<div />' } },
    ],
  })
  router.push('/datasets')
  await router.isReady()
  const app = createApp(Datasets)
  app.use(router)
  app.mount(el)
  mounted.push(app)
  await nextTick(); await nextTick(); await nextTick()
  return el
}

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  deleteDataset.mockClear()
  toast.error.mockClear()
  toast.success.mockClear()
  vi.restoreAllMocks()
})

const used = { name: 'DS1', title: 'North field', image_count: 42, total_size: 5 * 1024 * 1024,
  task_count: 2, created_by: 'a@example.com', creation: '2026-09-30 10:00:00' }
const unused = { name: 'DS2', title: 'Spare', image_count: 1, total_size: 1024, task_count: 0,
  created_by: 'a@example.com', creation: '2026-09-29 10:00:00' }

describe('Datasets page', () => {
  it('lists datasets with image count, size and usage', async () => {
    rows.value = [unused, used]
    const el = await mount()
    const trs = el.querySelectorAll('[data-dataset-row]')
    expect(trs).toHaveLength(2)
    // newest first
    expect(trs[0].textContent).toContain('North field')
    expect(trs[0].textContent).toContain('42 images')
    expect(trs[0].textContent).toContain('5.0 MB')
    expect(trs[0].textContent).toContain('2 tasks')
    expect(trs[1].textContent).toContain('unused')
  })

  it('shows the empty state', async () => {
    rows.value = []
    const el = await mount()
    expect(el.textContent).toContain('No datasets yet')
  })

  it('surfaces the "still used by a task" refusal as guidance, not a toast error', async () => {
    rows.value = [used]
    deleteDataset.mockImplementationOnce(async () => {
      throw new Error('Dataset "North field" is still used by 2 task(s): "Flight 1", "Flight 2". Delete those tasks first.')
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const el = await mount()
    el.querySelector('[data-dataset-row] button[title]:last-of-type').click()
    await nextTick(); await nextTick(); await nextTick()
    const alert = el.querySelector('[data-in-use-alert]')
    expect(alert).not.toBeNull()
    expect(alert.textContent).toContain('Flight 1')
    expect(alert.textContent).toContain('Flight 2')
    expect(toast.error).not.toHaveBeenCalled()
    expect(deleteDataset).toHaveBeenCalledWith('DS1')
  })

  it('deletes an unreferenced dataset and refreshes', async () => {
    rows.value = [unused]
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const el = await mount()
    el.querySelector('[data-dataset-row] button[title]:last-of-type').click()
    await nextTick(); await nextTick(); await nextTick()
    expect(deleteDataset).toHaveBeenCalledWith('DS2')
    expect(toast.success).toHaveBeenCalled()
    expect(el.querySelector('[data-in-use-alert]')).toBeNull()
  })

  it('does nothing when the confirmation is declined', async () => {
    rows.value = [unused]
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const el = await mount()
    el.querySelector('[data-dataset-row] button[title]:last-of-type').click()
    await nextTick()
    expect(deleteDataset).not.toHaveBeenCalled()
  })
})
