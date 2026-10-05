import { describe, it, expect, afterEach, vi } from 'vitest'
import { createApp, h, reactive, nextTick } from 'vue'
import ProfilePanel from './ProfilePanel.vue'

const PROFILE = {
  length: 42.5, step: 0.5, bins: 85, width: 2,
  vertices: [{ distance: 0, x: 0, y: 0 }, { distance: 42.5, x: 42.5, y: 0 }],
  distance: Array.from({ length: 85 }, (_, i) => i * 0.5 + 0.25),
  x: Array.from({ length: 85 }, (_, i) => i * 0.5 + 0.25), y: new Array(85).fill(0),
  count: new Array(85).fill(5),
  min: new Array(85).fill(100), max: new Array(85).fill(101),
  ground: new Array(85).fill(100), ground_fill: new Array(85).fill(null),
  top: new Array(85).fill(101), height: new Array(85).fill(1),
  classes: [{ code: 5, points: 300, count: new Array(85).fill(1), min: new Array(85).fill(100.5), max: new Array(85).fill(101), height: new Array(85).fill(1), max_height: 1 }],
  gaps: [],
  classification: { available: true, ground_method: 'class2_median' },
  sampling: { full_resolution: false, spacing: 0.3, points_read: 1000, points_available: 5000 },
  stats: { length: 42.5, length_3d: 42.6, bins: 85, bins_with_data: 85, coverage: 1, points: 425, ground_start: 100, ground_end: 100,
    ground_min: 100, ground_max: 100, height_max: 1, height_max_at: 3, height_mean: 1, ascent: 0, descent: 0, max_grade_pct: 0, max_grade_at: 1, mean_grade_pct: 0 },
}

let apps = []
function mount(extra = {}) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const events = []
  const props = reactive({ profile: PROFILE, loading: false, error: null, settings: { width: 2, step: 0, exaggeration: 'auto' }, taskName: 't1', hoverIndex: null, ...extra })
  const handlers = Object.fromEntries(['update', 'hover', 'select', 'close', 'retry'].map(n => [`on${n[0].toUpperCase()}${n.slice(1)}`, (...a) => events.push([n, ...a])]))
  const app = createApp({ render: () => h(ProfilePanel, { ...props, ...handlers }) })
  app.mount(el)
  apps.push(app)
  return { el, props, events }
}
afterEach(() => { apps.forEach(a => a.unmount()); apps = []; document.body.innerHTML = ''; vi.restoreAllMocks() })

const setSelect = async (select, value) => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); await nextTick() }

describe('ProfilePanel', () => {
  it('shows the title, stats, legend and notes for a profile', () => {
    const { el } = mount()
    expect(el.querySelector('[data-profile-title]').textContent).toBe('Profile · 42.5 m · 2 m corridor')
    const stats = el.querySelector('[data-profile-stats]').textContent
    expect(stats).toMatch(/Length42\.5 m/)
    expect(stats).toMatch(/Max height1\.00 m/)
    expect(el.querySelector('[data-profile-legend]').textContent).toMatch(/High vegetation/)
    const notes = el.querySelector('[data-profile-notes]').textContent
    expect(notes).toMatch(/classified ground/)
    expect(notes).toMatch(/Sampled at 0\.3 m spacing/)
    expect(el.querySelector('[data-profile-chart]')).not.toBeNull()
  })

  it('toggles a class layer off and on from the legend', async () => {
    const { el } = mount()
    const button = el.querySelector('[data-profile-legend] button')
    expect(el.querySelectorAll('[data-class-layer]')).toHaveLength(1)
    button.click()
    await nextTick()
    expect(el.querySelectorAll('[data-class-layer]')).toHaveLength(0)
    expect(button.getAttribute('aria-pressed')).toBe('false')
    button.click()
    await nextTick()
    expect(el.querySelectorAll('[data-class-layer]')).toHaveLength(1)
  })

  it('emits setting updates, close and retry', async () => {
    const { el, events, props } = mount()
    await setSelect(el.querySelector('select[aria-label="Corridor width"]'), '5')
    await setSelect(el.querySelector('select[aria-label="Bin step"]'), '0.5')
    await setSelect(el.querySelector('select[aria-label="Vertical scale"]'), '1')
    expect(events).toEqual([['update', 'width', 5], ['update', 'step', 0.5], ['update', 'exaggeration', 1]])
    el.querySelector('button[aria-label="Close the profile"]').click()
    await nextTick()
    expect(events.at(-1)).toEqual(['close'])
    props.profile = null
    props.error = 'Geospatial service unavailable'
    await nextTick()
    expect(el.querySelector('[data-profile-error]').textContent).toMatch(/unavailable/)
    el.querySelector('[data-profile-error] button').click()
    await nextTick()
    expect(events.at(-1)).toEqual(['retry'])
  })

  it('shows the loading and empty states', async () => {
    const { el, props } = mount({ profile: null, loading: true })
    expect(el.querySelector('[data-profile-loading]').textContent).toMatch(/Reading the point cloud/)
    expect(el.querySelector('[data-profile-stats]')).toBeNull()
    props.loading = false
    await nextTick()
    expect(el.textContent).toMatch(/Click two or more points/)
  })

  it('tells users of unclassified clouds how to get layers', () => {
    const { el } = mount({ profile: { ...PROFILE, classes: [], classification: { available: false, ground_method: 'low_percentile_filtered' } } })
    expect(el.querySelector('[data-profile-notes]').textContent).toMatch(/pc-classify/)
  })

  it('downloads a CSV of the profile', async () => {
    const { el } = mount()
    const urls = []
    URL.createObjectURL = vi.fn(blob => { urls.push(blob); return 'blob:x' })
    URL.revokeObjectURL = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    el.querySelector('button[title="Download the profile as CSV"]').click()
    await nextTick()
    expect(click).toHaveBeenCalledTimes(1)
    expect(urls).toHaveLength(1)
    const text = await new Promise(resolve => { const fr = new FileReader(); fr.onload = () => resolve(fr.result); fr.readAsText(urls[0]) })
    expect(text).toMatch(/^distance_m,x,y,count,ground_m/)
  })
})
