import { describe, it, expect, afterEach } from 'vitest'
import { createApp, nextTick, ref, h } from 'vue'
import UploadDropzone from './UploadDropzone.vue'

let mounted = []
function mount(props = {}) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const files = ref(props.modelValue || [])
  const events = { gcp: [], ignored: [] }
  const app = createApp({
    render: () => h(UploadDropzone, {
      ...props,
      modelValue: files.value,
      'onUpdate:modelValue': v => { files.value = v },
      onGcp: f => events.gcp.push(f),
      onIgnored: list => events.ignored.push(...list),
    }),
  })
  app.mount(el)
  mounted.push(app)
  return { el, files, events }
}

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
})

const f = (name, size = 4, type = '') => new File([new Uint8Array(size)], name, { type })

function drop(el, files) {
  const ev = new Event('drop', { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'dataTransfer', { value: { files, items: [] } })
  el.querySelector('[data-dropzone]').dispatchEvent(ev)
}

describe('UploadDropzone', () => {
  it('accepts dropped images, reports a dropped gcp file and skips the rest', async () => {
    const { el, files, events } = mount()
    drop(el, [f('IMG_1.JPG'), f('gcp_list.txt'), f('notes.pdf')])
    await nextTick(); await nextTick()
    expect(files.value.map(x => x.name)).toEqual(['IMG_1.JPG'])
    expect(events.gcp.map(x => x.name)).toEqual(['gcp_list.txt'])
    expect(events.ignored.map(x => x.name)).toEqual(['notes.pdf'])
    await nextTick()
    expect(el.querySelector('[data-dropzone-summary]').textContent).toContain('1 image selected')
    expect(el.querySelector('[data-dropzone-ignored]').textContent).toContain('1 file skipped')
  })

  it('merges a second drop without duplicates and clears on request', async () => {
    const { el, files } = mount({ modelValue: [f('a.jpg', 8)] })
    drop(el, [f('a.jpg', 8), f('b.jpg', 8)])
    await nextTick(); await nextTick()
    expect(files.value.map(x => x.name)).toEqual(['a.jpg', 'b.jpg'])
    await nextTick()
    el.querySelector('[data-dropzone-summary] button').click()
    await nextTick()
    expect(files.value).toEqual([])
  })

  it('highlights while dragging and ignores drops when disabled', async () => {
    const { el, files } = mount({ disabled: true })
    const zone = el.querySelector('[data-dropzone]')
    zone.dispatchEvent(new Event('dragenter', { bubbles: true, cancelable: true }))
    await nextTick()
    expect(zone.className).not.toContain('border-primary ')
    drop(el, [f('a.jpg')])
    await nextTick(); await nextTick()
    expect(files.value).toEqual([])

    const live = mount()
    const zone2 = live.el.querySelector('[data-dropzone]')
    zone2.dispatchEvent(new Event('dragenter', { bubbles: true, cancelable: true }))
    await nextTick()
    expect(zone2.className).toContain('border-primary')
    expect(zone2.textContent).toContain('Drop to add')
    zone2.dispatchEvent(new Event('dragleave', { bubbles: true, cancelable: true }))
    await nextTick()
    expect(zone2.textContent).toContain('Drop images here')
  })

  it('takes files from the hidden picker too', async () => {
    const { el, files } = mount()
    const input = el.querySelector('input[type=file]:not([webkitdirectory])')
    Object.defineProperty(input, 'files', { value: [f('c.png', 2, 'image/png')], configurable: true })
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(files.value.map(x => x.name)).toEqual(['c.png'])
  })
})
