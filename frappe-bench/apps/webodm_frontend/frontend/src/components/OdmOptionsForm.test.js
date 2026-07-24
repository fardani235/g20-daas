import { describe, it, expect, afterEach } from 'vitest'
import { createApp, reactive } from 'vue'
import OdmOptionsForm from './OdmOptionsForm.vue'
import { fieldType } from '@/composables/useOdmOptions'

// Mount helper (no @vue/test-utils in this project): render into a detached
// jsdom node and return it for assertions.
let mounted = []
function mount(props) {
  const el = document.createElement('div')
  const app = createApp(OdmOptionsForm, props)
  app.mount(el)
  mounted.push(app)
  return el
}
afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
})

const catalog = [
  { name: 'auto-boundary', type: 'bool' },       // General
  { name: 'pc-quality', type: 'enum', domain: ['low', 'high'], value: 'low' }, // Point Cloud
  { name: 'weird-new-flag', type: 'bool' },      // Advanced
]

describe('OdmOptionsForm', () => {
  it('renders a <details> section per non-empty group, in category order', () => {
    const el = mount({ catalog, modelValue: reactive({}), fieldType })
    // Summary shows the group name plus a "(count)" badge; assert the leading name.
    const names = [...el.querySelectorAll('details > summary')].map(s => s.textContent.trim().split(' (')[0])
    expect(names).toEqual(['General', 'Point Cloud', 'Advanced'])
  })

  it('expands only General by default', () => {
    const el = mount({ catalog, modelValue: reactive({}), fieldType })
    const details = [...el.querySelectorAll('details')]
    expect(details[0].open).toBe(true)   // General
    expect(details[1].open).toBe(false)  // Point Cloud
    expect(details[2].open).toBe(false)  // Advanced
  })

  it('renders a field control for each option', () => {
    const el = mount({ catalog, modelValue: reactive({}), fieldType })
    expect(el.querySelectorAll('input[type="checkbox"]').length).toBe(2) // auto-boundary, weird-new-flag
    expect(el.querySelectorAll('select').length).toBe(1)                 // pc-quality
  })
})
