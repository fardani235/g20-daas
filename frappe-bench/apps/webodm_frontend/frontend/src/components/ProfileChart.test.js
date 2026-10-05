import { describe, it, expect, afterEach } from 'vitest'
import { createApp, h, reactive, nextTick } from 'vue'
import ProfileChart from './ProfileChart.vue'

// 6 bins of 1 m: a gap at bin 2, a building (class 6) at bins 3-4 over
// interpolated ground, a wire (class 14) at bin 5.
const PROFILE = {
  length: 6, step: 1, bins: 6, width: 1,
  vertices: [{ distance: 0, x: 0, y: 0 }, { distance: 3, x: 3, y: 0 }, { distance: 6, x: 6, y: 0 }],
  distance: [0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
  x: [0.5, 1.5, 2.5, 3.5, 4.5, 5.5], y: [0, 0, 0, 0, 0, 0],
  count: [10, 12, 0, 20, 22, 9],
  min: [100, 100.1, null, 108, 108.1, 100.5],
  max: [100.2, 100.3, null, 108.2, 108.3, 112.5],
  ground: [100, 100.1, null, null, null, 100.5],
  ground_fill: [null, null, 100.2, 100.3, 100.4, null],
  top: [100.2, 100.3, null, 108.2, 108.3, 112.5],
  height: [0.2, 0.2, null, 7.9, 7.9, 12],
  classes: [
    { code: 6, points: 42, count: [0, 0, 0, 20, 22, 0], min: [null, null, null, 108, 108.1, null], max: [null, null, null, 108.2, 108.3, null], height: [null, null, null, 7.9, 7.9, null], max_height: 7.9 },
    { code: 14, points: 3, count: [0, 0, 0, 0, 0, 3], min: [null, null, null, null, null, 112.4], max: [null, null, null, null, null, 112.5], height: [null, null, null, null, null, 12], max_height: 12 },
  ],
  gaps: [[2, 3]],
  classification: { available: true, ground_method: 'class2_median' },
  sampling: { full_resolution: true },
  stats: { length: 6 },
}

let apps = []
function mount(extra = {}) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const events = []
  const props = reactive({ profile: PROFILE, exaggeration: 'auto', hiddenClasses: [], hoverIndex: null, ...extra })
  const app = createApp({
    render: () => h(ProfileChart, { ...props, onHover: i => events.push(['hover', i]), onSelect: i => events.push(['select', i]) }),
  })
  app.mount(el)
  apps.push(app)
  const svg = el.querySelector('svg')
  // jsdom has no layout: the chart falls back to 640 x 240; give the svg a matching rect.
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 640, height: 240, right: 640, bottom: 240 })
  return { el, svg, props, events }
}
afterEach(() => { apps.forEach(a => a.unmount()); apps = []; document.body.innerHTML = '' })

const move = (svg, clientX) => svg.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX, clientY: 100 }))

describe('ProfileChart', () => {
  it('draws ground, interpolated ground, top, the envelope, class layers and gaps', () => {
    const { el } = mount()
    expect(el.querySelector('[data-ground]').getAttribute('d')).toMatch(/^M/)
    expect(el.querySelector('[data-ground-fill]').getAttribute('d')).toMatch(/^M/)
    // The top line breaks at the gap: two sub-paths.
    expect(el.querySelector('[data-top]').getAttribute('d').match(/M/g)).toHaveLength(2)
    expect(el.querySelector('[data-envelope]').getAttribute('d')).toContain('Z')
    expect(el.querySelectorAll('[data-class-layer]')).toHaveLength(2)
    expect(el.querySelectorAll('[data-gap]')).toHaveLength(1)
    // Interior polyline vertex marker (one, at 3 m).
    expect(el.querySelectorAll('line[stroke-dasharray="2 3"]')).toHaveLength(1)
    expect(el.textContent).toMatch(/× vertical|1 : 1/)
    expect(el.querySelector('svg').getAttribute('aria-label')).toMatch(/6\.0 m long/)
  })

  it('hides toggled class layers and honours the vertical scale setting', async () => {
    const { el, props } = mount({ hiddenClasses: [14] })
    expect(el.querySelectorAll('[data-class-layer]')).toHaveLength(1)
    expect(el.querySelector('[data-class-layer="6"]')).not.toBeNull()
    props.exaggeration = 1
    await nextTick()
    expect(el.querySelector('[data-exaggeration]').textContent.trim()).toBe('1 : 1')
  })

  it('emits hover with the bin under the pointer and clears it on leave', async () => {
    const { el, svg, events } = mount()
    // Plot area: left margin 52, right margin 14 -> 574 px for 6 m. Bin 3 is 3..4 m.
    move(svg, 52 + (3.5 / 6) * 574)
    await nextTick()
    expect(events).toEqual([['hover', 3]])
    const readout = el.querySelector('[data-profile-readout]')
    expect(readout.textContent).toMatch(/3\.5 m/)
    expect(readout.textContent).toMatch(/ground100\.30 m ~/) // interpolated ground is marked
    expect(readout.textContent).toMatch(/Building/)
    expect(el.querySelector('[data-hover]')).not.toBeNull()
    // Same bin again: no duplicate event.
    move(svg, 52 + (3.6 / 6) * 574)
    await nextTick()
    expect(events).toHaveLength(1)
    // Outside the line (in the left margin): hover cleared.
    move(svg, 10)
    await nextTick()
    expect(events[1]).toEqual(['hover', null])
    svg.dispatchEvent(new MouseEvent('pointerleave', { bubbles: true }))
    await nextTick()
    expect(events).toHaveLength(2)
  })

  it('shows "no data" over a gap and emits select on click', async () => {
    const { el, svg, events } = mount()
    move(svg, 52 + (2.5 / 6) * 574)
    await nextTick()
    expect(el.querySelector('[data-profile-readout]').textContent).toMatch(/no data/)
    svg.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 52 + (5.5 / 6) * 574, clientY: 100 }))
    await nextTick()
    expect(events.at(-1)).toEqual(['select', 5])
  })

  it('follows an externally driven hover index', async () => {
    const { el, props } = mount({ hoverIndex: 5 })
    expect(el.querySelector('[data-profile-readout]').textContent).toMatch(/Wire/)
    props.hoverIndex = null
    await nextTick()
    expect(el.querySelector('[data-profile-readout]')).toBeNull()
  })
})
