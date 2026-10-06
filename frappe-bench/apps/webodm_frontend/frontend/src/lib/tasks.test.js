import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  EDITABLE_STATUSES,
  canEditTask,
  duplicateTask,
  duplicateTitle,
  editBlockedReason,
  gcpSummaryText,
  getTask,
  isInFlight,
  optionsToValues,
  rerunLabel,
  rerunWarning,
  startTask,
  updateTask,
  valuesToOptions,
} from './tasks'

describe('status rules', () => {
  it('mirror the backend: editable unless queued / provisioning / running', () => {
    expect(EDITABLE_STATUSES).toEqual(['Pending', 'Completed', 'Failed', 'Cancelled'])
    for (const status of EDITABLE_STATUSES) expect(canEditTask({ status })).toBe(true)
    for (const status of ['Queued', 'Provisioning', 'Running']) {
      expect(canEditTask({ status })).toBe(false)
      expect(isInFlight({ status })).toBe(true)
      expect(editBlockedReason({ status })).toMatch(new RegExp(`Cancel the task .*${status.toLowerCase()}`))
    }
    expect(editBlockedReason({ status: 'Pending' })).toBe('')
    expect(canEditTask(null)).toBe(false)
  })

  it('prefers the server-provided can_edit flag', () => {
    expect(canEditTask({ status: 'Pending', can_edit: false })).toBe(false)
    expect(canEditTask({ status: 'Running', can_edit: true })).toBe(true)
  })

  it('names the run button and warns about replacing outputs', () => {
    expect(rerunLabel({ status: 'Pending' })).toBe('Save and start')
    expect(rerunLabel({ status: 'Completed' })).toBe('Save and re-run')
    expect(rerunLabel(null)).toBe('Save')
    expect(rerunWarning({ status: 'Pending' })).toBe('')
    expect(rerunWarning({ status: 'Completed' })).toMatch(/stay until you re-run/)
    expect(rerunWarning({ status: 'Failed' })).toMatch(/starts the task again/)
  })
})

describe('options conversion', () => {
  const list = [{ name: 'dsm', value: true }, { name: 'feature-quality', value: 'ultra' }]

  it('decodes a list, a string at any encoding depth, and a legacy dict', () => {
    expect(optionsToValues(list)).toEqual({ dsm: true, 'feature-quality': 'ultra' })
    expect(optionsToValues(JSON.stringify(list))).toEqual({ dsm: true, 'feature-quality': 'ultra' })
    expect(optionsToValues(JSON.stringify(JSON.stringify(list)))).toEqual({ dsm: true, 'feature-quality': 'ultra' })
    expect(optionsToValues({ dsm: true })).toEqual({ dsm: true })
    expect(optionsToValues(null)).toEqual({})
    expect(optionsToValues('garbage')).toEqual({})
    expect(optionsToValues([{ nope: 1 }, null])).toEqual({})
  })

  it('encodes values back to the NodeODM list, dropping blanks', () => {
    expect(valuesToOptions({ dsm: true, 'feature-quality': 'ultra', empty: '', nothing: null, undef: undefined, zero: 0, off: false }))
      .toEqual([{ name: 'dsm', value: true }, { name: 'feature-quality', value: 'ultra' }, { name: 'zero', value: 0 }, { name: 'off', value: false }])
    expect(valuesToOptions(null)).toEqual([])
  })
})

describe('duplicate title', () => {
  it('appends (copy) and counts up past existing titles', () => {
    expect(duplicateTitle('Flight')).toBe('Flight (copy)')
    expect(duplicateTitle('Flight', ['Flight (copy)'])).toBe('Flight (copy 2)')
    expect(duplicateTitle('Flight', ['Flight (copy)', 'Flight (copy 2)'])).toBe('Flight (copy 3)')
    expect(duplicateTitle('')).toBe('Task (copy)')
  })
})

describe('gcpSummaryText', () => {
  it('summarises points, crs and unknown names', () => {
    expect(gcpSummaryText(null)).toBe('')
    expect(gcpSummaryText({ points: 1 })).toBe('1 point')
    expect(gcpSummaryText({ points: 5, crs: 'EPSG:32633', unknown_images: ['x.jpg'] }))
      .toBe('5 points · EPSG:32633 · 1 image name not in the dataset')
  })
})

describe('client', () => {
  beforeEach(() => {
    window.csrf_token = 'tok'
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ message: { name: 'T2' } }) }))
  })
  afterEach(() => { delete window.csrf_token; vi.restoreAllMocks() })

  const call = i => {
    const [url, init] = fetch.mock.calls[i]
    return { url, body: JSON.parse(init.body), headers: init.headers }
  }

  it('posts JSON with the CSRF token and only the keys given', async () => {
    await updateTask('T1', { title: 'New' })
    expect(call(0).url).toBe('/api/method/webodm_core.api.task.update_task')
    expect(call(0).headers['X-Frappe-CSRF-Token']).toBe('tok')
    expect(call(0).body).toEqual({ task_name: 'T1', title: 'New' })
    await updateTask('T1', { options: [] })
    expect(call(1).body).toEqual({ task_name: 'T1', options: [] })
    await duplicateTask('T1')
    expect(call(2).url).toBe('/api/method/webodm_core.api.task.duplicate_task')
    expect(call(2).body).toEqual({ task_name: 'T1' })
    await duplicateTask('T1', { title: 'Named copy' })
    expect(call(3).body).toEqual({ task_name: 'T1', title: 'Named copy' })
    await startTask('T1')
    expect(call(4).url).toBe('/api/method/webodm_core.api.task.process_task')
    await getTask('T1')
    expect(call(5).url).toBe('/api/method/webodm_core.api.task.get_task_progress')
  })

  it('surfaces the server refusal when a task is in flight', async () => {
    fetch.mockImplementationOnce(async () => ({
      ok: false,
      json: async () => ({ _server_messages: JSON.stringify([JSON.stringify({ message: 'Task "x" is Running; cancel it before changing its settings' })]) }),
    }))
    await expect(updateTask('T1', { title: 'x' })).rejects.toThrow(/cancel it before changing/)
  })
})
