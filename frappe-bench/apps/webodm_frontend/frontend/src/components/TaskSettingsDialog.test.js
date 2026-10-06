import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { createApp, nextTick, ref, h } from 'vue'

const updateTask = vi.fn()
const startTask = vi.fn()
vi.mock('@/lib/tasks', async importOriginal => {
  const actual = await importOriginal()
  return { ...actual, updateTask: (...a) => updateTask(...a), startTask: (...a) => startTask(...a) }
})
vi.mock('@/lib/presets', () => ({
  listPresets: vi.fn(async () => [
    { name: 'P1', preset_name: 'High quality', options: [{ name: 'feature-quality', value: 'ultra' }, { name: 'dsm', value: true }] },
  ]),
  fetchOptions: vi.fn(async () => [
    { name: 'dsm', type: 'bool', value: false, help: 'DSM' },
    { name: 'feature-quality', type: 'enum', domain: ['low', 'medium', 'high', 'ultra'], value: 'high' },
    { name: 'orthophoto-resolution', type: 'float', value: 5 },
  ]),
}))
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

import { toast } from '@/lib/toast'
import TaskSettingsDialog from './TaskSettingsDialog.vue'

let mounted = []
async function mount(task) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const open = ref(true)
  const events = { saved: [], started: [] }
  const app = createApp({
    render: () => h(TaskSettingsDialog, {
      open: open.value,
      task,
      'onUpdate:open': v => { open.value = v },
      onSaved: p => events.saved.push(p),
      onStarted: p => events.started.push(p),
    }),
  })
  app.mount(el)
  mounted.push(app)
  for (let i = 0; i < 6; i++) await nextTick()
  await new Promise(r => setTimeout(r, 0))
  await nextTick()
  return { el, open, events }
}

// radix-vue renders the dialog in a portal on <body>
const q = sel => document.body.querySelector(sel)

beforeEach(() => {
  updateTask.mockReset()
  startTask.mockReset()
  updateTask.mockImplementation(async (name, { title, options }) => ({ name, title, options, status: 'Completed' }))
  startTask.mockImplementation(async () => 'Processing started for T1')
})

afterEach(() => {
  mounted.forEach(a => a.unmount())
  mounted = []
  document.body.innerHTML = ''
  toast.error.mockClear()
  toast.success.mockClear()
})

const completed = {
  name: 'T1', title: 'Survey', status: 'Completed',
  options: [{ name: 'dsm', value: true }, { name: 'feature-quality', value: 'high' }],
}

describe('TaskSettingsDialog', () => {
  it('pre-fills the title and the current options and names the run button by status', async () => {
    await mount(completed)
    expect(q('#task-settings-title').value).toBe('Survey')
    expect(q('[data-task-settings-rerun]').textContent).toContain('Save and re-run')
    expect(q('[data-task-settings-warning]').textContent).toMatch(/stay until you re-run/)
    const checkbox = q('[data-task-settings] input[type=checkbox]')
    expect(checkbox.checked).toBe(true)
    expect(q('[data-task-settings] select:not(#task-settings-preset)').value).toBe('high')
  })

  it('saves title and options without starting anything', async () => {
    const { el, open, events } = await mount(completed)
    q('#task-settings-title').value = 'Survey v2'
    q('#task-settings-title').dispatchEvent(new Event('input', { bubbles: true }))
    const checkbox = q('[data-task-settings] input[type=checkbox]')
    checkbox.click()
    await nextTick()
    q('[data-task-settings-save]').click()
    await new Promise(r => setTimeout(r, 0)); await nextTick()
    expect(updateTask).toHaveBeenCalledTimes(1)
    const [name, payload] = updateTask.mock.calls[0]
    expect(name).toBe('T1')
    expect(payload.title).toBe('Survey v2')
    expect(payload.options).toEqual(expect.arrayContaining([{ name: 'dsm', value: false }, { name: 'feature-quality', value: 'high' }]))
    expect(startTask).not.toHaveBeenCalled()
    expect(events.saved).toHaveLength(1)
    expect(events.started).toHaveLength(0)
    expect(open.value).toBe(false)
    expect(toast.success).toHaveBeenCalledWith('Settings saved')
    void el
  })

  it('"Save and re-run" saves then starts, and reports the queued state', async () => {
    const { events } = await mount(completed)
    q('[data-task-settings-rerun]').click()
    await new Promise(r => setTimeout(r, 0)); await nextTick()
    expect(updateTask).toHaveBeenCalledTimes(1)
    expect(startTask).toHaveBeenCalledWith('T1')
    expect(events.started[0].status).toBe('Queued')
    expect(toast.success).toHaveBeenCalledWith('Processing started for T1')
  })

  it('applying a preset replaces the option values', async () => {
    await mount(completed)
    const preset = q('#task-settings-preset')
    preset.value = 'P1'
    preset.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick(); await nextTick()
    expect(q('[data-task-settings] select:not(#task-settings-preset)').value).toBe('ultra')
    q('[data-task-settings-save]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(updateTask.mock.calls[0][1].options).toEqual(expect.arrayContaining([{ name: 'feature-quality', value: 'ultra' }, { name: 'dsm', value: true }]))
  })

  it('a pending task offers "Save and start" and no warning', async () => {
    await mount({ ...completed, status: 'Pending' })
    expect(q('[data-task-settings-rerun]').textContent).toContain('Save and start')
    expect(q('[data-task-settings-warning]')).toBeNull()
  })

  it('surfaces a server refusal and keeps the dialog open', async () => {
    updateTask.mockImplementationOnce(async () => { throw new Error('Task "Survey" is Running; cancel it before changing its settings') })
    const { open } = await mount(completed)
    q('[data-task-settings-save]').click()
    await new Promise(r => setTimeout(r, 0)); await nextTick()
    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/cancel it before/))
    expect(open.value).toBe(true)
  })
})
