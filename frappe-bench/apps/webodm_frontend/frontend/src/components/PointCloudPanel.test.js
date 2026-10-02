import { describe, it, expect, afterEach } from 'vitest'
import { createApp, h, reactive } from 'vue'
import PointCloudPanel from './PointCloudPanel.vue'

const META = {
  points: 100,
  boundingBox: { min: [0, 0, 100], max: [10, 10, 120] },
  attributes: [
    { name: 'position', min: [0, 0, 100], max: [10, 10, 118] },
    { name: 'intensity', min: [0], max: [0] },
    { name: 'classification', min: [2], max: [6], histogram: [0, 0, 50, 0, 0, 0, 50, ...new Array(249).fill(0)] },
    { name: 'rgb', min: [0, 0, 0], max: [255, 255, 255] },
  ],
}
const base = { colorMode: 'rgb', pointSize: 2, pointBudget: 1_000_000, background: 'dark', elevationFilter: null, hiddenClasses: [] }

let apps = []
function mountPanel({ metadata = META, settings = {}, budgetCap = Infinity } = {}) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const updates = []
  const props = reactive({ metadata, settings: { ...base, ...settings }, budgetCap, stats: { visiblePoints: 1234, totalPoints: 100, loadingNodes: 0 } })
  const app = createApp({ render: () => h(PointCloudPanel, { ...props, onUpdate: (k, v) => updates.push([k, v]) }) })
  app.mount(el)
  apps.push(app)
  return { el, updates }
}
afterEach(() => { apps.forEach(a => a.unmount()); apps = []; document.body.innerHTML = '' })

const tick = () => new Promise(r => setTimeout(r, 0))
const setRange = async (input, value) => { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); await tick() }
const setSelect = async (select, value) => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); await tick() }

describe('PointCloudPanel', () => {
  it('shows only the colour modes and classes the cloud has', () => {
    const { el } = mountPanel()
    const modes = [...el.querySelectorAll('[role=radiogroup][aria-label="Colour by"] button')].map(b => b.textContent.trim())
    expect(modes).toEqual(['RGB', 'Elevation', 'Classification']) // intensity is all zeros
    const classes = [...el.querySelectorAll('[data-classification-filter] li')].map(li => li.textContent)
    expect(classes[0]).toContain('Ground')
    expect(classes[1]).toContain('Building')
    expect(el.querySelector('[data-elevation-filter]')).not.toBeNull()
  })

  it('hides the colour chooser and class filter when there is nothing to choose', () => {
    const metadata = { ...META, attributes: [{ name: 'position', min: [0, 0, 100], max: [10, 10, 118] }] }
    const { el } = mountPanel({ metadata })
    expect(el.querySelector('[role=radiogroup][aria-label="Colour by"]')).toBeNull()
    expect(el.querySelector('[data-classification-filter]')).toBeNull()
  })

  it('emits updates for colour, filters, size, budget and background', async () => {
    const { el, updates } = mountPanel()
    el.querySelectorAll('[role=radiogroup][aria-label="Colour by"] button')[1].click()
    await tick()
    expect(updates[0]).toEqual(['colorMode', 'elevation'])

    await setRange(el.querySelector('input[aria-label="Minimum elevation"]'), '105')
    expect(updates[1]).toEqual(['elevationFilter', [105, 118]])

    el.querySelectorAll('[data-classification-filter] input[type=checkbox]')[1].click()
    await tick()
    expect(updates[2]).toEqual(['hiddenClasses', [6]])

    await setRange(el.querySelector('input[aria-label="Point size"]'), '4')
    expect(updates[3]).toEqual(['pointSize', 4])

    await setSelect(el.querySelector('select[aria-label="Point budget"]'), '2000000')
    expect(updates[4]).toEqual(['pointBudget', 2_000_000])
    await setSelect(el.querySelector('select[aria-label="Background"]'), 'white')
    expect(updates[5]).toEqual(['background', 'white'])
  })

  it('resets the elevation filter when the full range is selected and respects the budget cap', async () => {
    const { el, updates } = mountPanel({ settings: { elevationFilter: [105, 118] }, budgetCap: 2_000_000 })
    expect([...el.querySelectorAll('select[aria-label="Point budget"] option')].map(o => o.value)).toEqual(['500000', '1000000', '2000000'])
    await setRange(el.querySelector('input[aria-label="Minimum elevation"]'), '100')
    expect(updates[0]).toEqual(['elevationFilter', null])
  })
})
