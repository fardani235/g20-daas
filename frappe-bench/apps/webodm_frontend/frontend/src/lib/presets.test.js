import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { listPresets, savePreset, fetchOptions } from '@/lib/presets'

describe('presets lib', () => {
  beforeEach(() => {
    global.window.csrf_token = 'tok'
    global.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ message: [{ preset_name: 'X', options: [] }] }),
    }))
  })
  afterEach(() => { vi.restoreAllMocks() })

  it('listPresets unwraps the message envelope', async () => {
    const out = await listPresets()
    expect(out).toEqual([{ preset_name: 'X', options: [] }])
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/method/webodm_core.api.presets.list_presets',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('savePreset sends CSRF header and JSON body', async () => {
    await savePreset({ preset_name: 'X', options: [{ name: 'dsm', value: true }] })
    const [, opts] = global.fetch.mock.calls[0]
    expect(opts.method).toBe('POST')
    expect(opts.headers['X-Frappe-CSRF-Token']).toBe('tok')
    expect(JSON.parse(opts.body).preset_name).toBe('X')
  })

  it('fetchOptions calls the options endpoint', async () => {
    await fetchOptions()
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/method/webodm_core.api.presets.options',
      expect.objectContaining({ method: 'GET' }),
    )
  })
})
