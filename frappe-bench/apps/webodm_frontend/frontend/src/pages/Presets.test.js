import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp, ref, nextTick } from 'vue'

const rows = ref([])
const whoamiPayload = ref({ is_platform_admin: false })
const savePreset = vi.fn(async () => ({ name: 'X' }))

vi.mock('@/lib/presets', () => ({
  listPresets: () => Promise.resolve(rows.value),
  whoami: () => Promise.resolve(whoamiPayload.value),
  savePreset: (...a) => savePreset(...a),
  deletePreset: vi.fn(async () => ({ ok: true })),
}))

// The real composable fetches the ODM option catalog over the network.
vi.mock('@/composables/useOdmOptions', () => ({
  useOdmOptions: () => ({
    catalog: ref([]),
    loading: ref(false),
    error: ref(''),
    load: async () => {},
    seedEnumDefaults: () => {},
    fieldType: () => 'checkbox',
  }),
  fieldType: () => 'checkbox',
}))

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

import Presets from './Presets.vue'

let mounted = []
async function mount() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(Presets)
  app.mount(el)
  mounted.push(app)
  await nextTick(); await nextTick(); await nextTick()
  return el
}

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  savePreset.mockClear()
})

const systemRow = {
  name: 'Sys', preset_name: 'Sys', options: [{ name: 'dsm', value: true }],
  system: 1, organization: null, can_write: false, can_delete: false,
}
const ownRow = {
  name: 'Mine', preset_name: 'Mine', options: [],
  system: 0, organization: 'ORG-A', can_write: true, can_delete: true,
}

function titles(el) {
  return [...el.querySelectorAll('button[title]')].map(b => b.getAttribute('title'))
}

describe('Presets page', () => {
  beforeEach(() => {
    rows.value = []
    whoamiPayload.value = { is_platform_admin: false }
  })

  it('hides Edit and Delete on a system preset for a non-admin, keeps Copy', async () => {
    rows.value = [systemRow]
    const el = await mount()
    const t = titles(el)
    expect(t).toContain('Copy preset')
    expect(t).not.toContain('Edit preset')
    expect(t).not.toContain('Delete preset')
  })

  it('shows Edit and Delete on a system preset for an admin', async () => {
    rows.value = [{ ...systemRow, can_write: true, can_delete: true }]
    whoamiPayload.value = { is_platform_admin: true }
    const el = await mount()
    const t = titles(el)
    expect(t).toContain('Edit preset')
    expect(t).toContain('Delete preset')
  })

  it('shows Edit and Delete on the users own org preset', async () => {
    rows.value = [ownRow]
    const el = await mount()
    const t = titles(el)
    expect(t).toContain('Edit preset')
    expect(t).toContain('Delete preset')
  })

  it('copy submits as a new preset with a (copy) suffixed name', async () => {
    rows.value = [systemRow]
    const el = await mount()
    el.querySelector('button[title="Copy preset"]').click()
    await nextTick(); await nextTick()
    // Dialog content renders through a portal into document.body.
    const save = [...document.body.querySelectorAll('button')]
      .find(b => b.textContent.trim() === 'Save')
    save.click()
    await nextTick()
    expect(savePreset).toHaveBeenCalledWith(expect.objectContaining({
      name: null,
      preset_name: 'Sys (copy)',
      system: 0,
    }))
  })

  it('shows the System toggle only to admins', async () => {
    rows.value = [ownRow]
    const el = await mount()
    el.querySelector('button[title="Edit preset"]').click()
    await nextTick(); await nextTick()
    expect(document.body.querySelector('#preset-system')).toBeNull()

    mounted.forEach(a => a.unmount()); mounted = []
    document.body.innerHTML = ''
    whoamiPayload.value = { is_platform_admin: true }
    const el2 = await mount()
    el2.querySelector('button[title="Edit preset"]').click()
    await nextTick(); await nextTick()
    expect(document.body.querySelector('#preset-system')).not.toBeNull()
  })

  it('checks the System toggle when an admin edits a system preset', async () => {
    rows.value = [{ ...systemRow, can_write: true, can_delete: true }]
    whoamiPayload.value = { is_platform_admin: true }
    const el = await mount()
    el.querySelector('button[title="Edit preset"]').click()
    await nextTick(); await nextTick()
    const box = document.body.querySelector('#preset-system')
    expect(box).not.toBeNull()
    expect(box.checked).toBe(true)
  })

  it('demotes a system preset to org scope when an admin unchecks System', async () => {
    rows.value = [{ ...systemRow, can_write: true, can_delete: true }]
    whoamiPayload.value = { is_platform_admin: true }
    const el = await mount()
    el.querySelector('button[title="Edit preset"]').click()
    await nextTick(); await nextTick()

    const box = document.body.querySelector('#preset-system')
    box.click()  // toggles .checked and fires change, driving v-model
    await nextTick()
    expect(box.checked).toBe(false)

    const save = [...document.body.querySelectorAll('button')]
      .find(b => b.textContent.trim() === 'Save')
    save.click()
    await nextTick()
    expect(savePreset).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Sys',
      system: 0,
    }))
  })
})
