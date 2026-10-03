import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp, nextTick } from 'vue'

// Regression: the map panel's "Point cloud" button calls `pointCloudRoute` from
// `@/lib/potree`. When that import is missing the click throws
// `ReferenceError: pointCloudRoute is not defined` and the viewer never opens
// (the router never sees the push).
const push = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'P1' }, query: {} }),
  useRouter: () => ({ push }),
}))
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import MapView from './MapView.vue'

const TASK = {
  name: 'T1',
  title: 'Task 1',
  status: 'Completed',
  progress: 100,
  point_cloud: '/private/files/model.laz',
  images: [],
  dataset: 'D1',
  dataset_summary: { title: 'Dataset', image_count: 0 },
}

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

beforeEach(() => {
  push.mockReset()
  global.window.csrf_token = 'x'
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  global.fetch = vi.fn((url) => {
    const href = String(url)
    let body = { message: {} }
    if (href.includes('list_tasks')) body = { message: [TASK] }
    else if (href.includes('get_task_progress')) body = { message: TASK }
    else if (href.includes('list_runs')) body = { message: [] }
    else if (href.includes('list_plugins')) body = { message: [] }
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
})

describe('MapView point cloud button', () => {
  it('opens the viewer at the point-cloud route for a task with a point cloud', async () => {
    const el = mount()
    await flush()

    const card = el.querySelector('.cursor-pointer')
    expect(card).toBeTruthy()
    card.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flush()

    const button = el.querySelector('button[title="Open the point cloud in the 3D viewer"]')
    expect(button).toBeTruthy()
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flush()

    expect(push).toHaveBeenCalledWith('/project/P1/task/T1/model?source=pointcloud')
  })
})
