import { describe, it, expect, afterEach } from 'vitest'
import { createApp, nextTick, reactive, h } from 'vue'
import UploadProgress from './UploadProgress.vue'
import { createUploadTracker } from '@/lib/upload'

let mounted = []
function mount(tracker, props = {}) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const cancels = []
  const app = createApp({ render: () => h(UploadProgress, { state: tracker.state, onCancel: () => cancels.push(1), ...props }) })
  app.mount(el)
  mounted.push(app)
  return { el, cancels }
}

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
})

describe('UploadProgress', () => {
  it('renders nothing while idle, then bytes / percent / eta, then the finishing phase', async () => {
    let now = 0
    const tracker = createUploadTracker(() => now, reactive)
    const { el } = mount(tracker, { label: 'Uploading 3 images' })
    expect(el.querySelector('[data-upload-progress]')).toBeNull()

    tracker.start(1000)
    now = 4000
    tracker.update({ loaded: 250, total: 1000 })
    await nextTick()
    const box = el.querySelector('[data-upload-progress]')
    expect(box.dataset.phase).toBe('uploading')
    expect(box.textContent).toContain('Uploading 3 images…')
    expect(el.querySelector('[data-upload-percent]').textContent).toBe('25%')
    expect(el.querySelector('[data-upload-detail]').textContent).toMatch(/250 B of 1000 B · 63 B\/s · 12s left/)
    expect(el.querySelector('[data-upload-cancel]')).not.toBeNull()

    tracker.update({ loaded: 1000, total: 1000 })
    await nextTick()
    expect(box.dataset.phase).toBe('processing')
    expect(box.textContent).toContain('Finishing up')
    expect(el.querySelector('[data-upload-detail]').textContent).toMatch(/server is registering/)
    expect(el.querySelector('[data-upload-percent]')).toBeNull()

    tracker.finish()
    await nextTick()
    expect(box.dataset.phase).toBe('done')
    expect(el.querySelector('[data-upload-cancel]')).toBeNull()
  })

  it('emits cancel and shows failed / cancelled states', async () => {
    const tracker = createUploadTracker(undefined, reactive)
    const { el, cancels } = mount(tracker)
    tracker.start(10)
    await nextTick()
    el.querySelector('[data-upload-cancel]').click()
    expect(cancels).toHaveLength(1)
    tracker.cancel()
    await nextTick()
    expect(el.querySelector('[data-upload-progress]').dataset.phase).toBe('cancelled')
    tracker.start(10)
    tracker.fail()
    await nextTick()
    expect(el.querySelector('[data-upload-progress]').textContent).toContain('Upload failed')
  })

  it('can hide the cancel button', async () => {
    const tracker = createUploadTracker(undefined, reactive)
    const { el } = mount(tracker, { cancellable: false })
    tracker.start(10)
    await nextTick()
    expect(el.querySelector('[data-upload-cancel]')).toBeNull()
  })
})
