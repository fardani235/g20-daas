import { describe, it, expect, vi, beforeEach } from 'vitest'
import * as plugins from './plugins.js'

beforeEach(() => {
  global.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve({ message: [] }) }))
  global.window = { csrf_token: 'x' }
})

describe('plugins lib', () => {
  it('listPlugins GETs the catalog', async () => {
    await plugins.listPlugins()
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('webodm_core.api.plugins.list_plugins'),
      expect.objectContaining({ method: 'GET' }))
  })

  it('savePluginSetting POSTs to the setting endpoint', async () => {
    await plugins.savePluginSetting({ plugin: 'contours', enabled: true })
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('webodm_core.api.plugins.save_plugin_setting'),
      expect.objectContaining({ method: 'POST' }))
  })

  it('runPlugin POSTs the run payload', async () => {
    await plugins.runPlugin({ plugin: 'contours', task: 'T1' })
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('webodm_core.api.plugins.run_plugin'),
      expect.objectContaining({ method: 'POST' }))
  })

  it('listRuns scopes to a task when provided', async () => {
    await plugins.listRuns('T1')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('task=T1'),
      expect.objectContaining({ method: 'GET' }))
  })

  it('getRun and cancelRun target the run', async () => {
    await plugins.getRun('R1')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('name=R1'),
      expect.objectContaining({ method: 'GET' }))

    await plugins.cancelRun('R1')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('webodm_core.api.plugins.cancel_run'),
      expect.objectContaining({ method: 'POST' }))
  })

  it('builds tile, geojson and download URLs', () => {
    expect(plugins.runTileUrl('R1')).toContain('serve_run?run_name=R1')
    expect(plugins.runTileUrl('R1')).toContain('{z}')
    expect(plugins.getRunGeojson('R1')).toBeInstanceOf(Promise)
    expect(plugins.runDownloadUrl('R1')).toContain('run_name=R1')
  })

  it('extracts schema defaults', () => {
    const schema = {
      properties: {
        interval_m: { type: 'number', default: 5 },
        output_format: { type: 'string' },
        enabled: { type: 'boolean', default: false },
      },
    }
    expect(plugins.schemaDefaults(schema)).toEqual({ interval_m: 5, enabled: false })
    expect(plugins.schemaDefaults(null)).toEqual({})
  })

  it('caps vector features', () => {
    expect(plugins.MAX_VECTOR_FEATURES).toBeGreaterThan(0)
    expect(plugins.shouldRenderVector(10)).toBe(true)
    expect(plugins.shouldRenderVector(plugins.MAX_VECTOR_FEATURES + 1)).toBe(false)
    expect(plugins.shouldRenderVector(undefined)).toBe(true)
  })

  it('keeps the newest run per plugin', () => {
    const runs = [
      { name: 'c2', plugin: 'contours', creation: '2026-09-13 10:00:00' },
      { name: 'h1', plugin: 'hillshade', creation: '2026-09-13 09:00:00' },
      { name: 'c1', plugin: 'contours', creation: '2026-09-13 08:00:00' },
    ]
    const latest = plugins.latestRunPerPlugin(runs)
    expect(latest.map(r => r.name).sort()).toEqual(['c2', 'h1'])
    expect(plugins.latestRunPerPlugin([])).toEqual([])
  })
})

describe('user plugins', () => {
  it('uploadPlugin POSTs multipart without a JSON content type', async () => {
    const file = new File(['zip-bytes'], 'plugin.zip', { type: 'application/zip' })
    await plugins.uploadPlugin(file)
    const [url, init] = global.fetch.mock.calls.at(-1)
    expect(url).toContain('webodm_core.api.plugins.upload_plugin')
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    expect(init.body.get('file')).toBeInstanceOf(File)
    expect(init.headers['Content-Type']).toBeUndefined()
    expect(init.headers['X-Frappe-CSRF-Token']).toBe('x')
  })

  it('surfaces frappe.throw text from _server_messages on upload failure', async () => {
    const body = {
      exc_type: 'ValidationError',
      _server_messages: JSON.stringify([
        JSON.stringify({ message: 'Invalid plugin package: package has no plugin.json at its root', title: 'Message' }),
      ]),
    }
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: false, status: 417, json: () => Promise.resolve(body) }))
    await expect(plugins.uploadPlugin(new File(['x'], 'p.zip'))).rejects.toThrow(
      'Invalid plugin package: package has no plugin.json at its root')
  })

  it('errorMessage prefers message, then _server_messages, then exception', () => {
    expect(plugins.errorMessage({ message: 'direct' })).toBe('direct')
    expect(plugins.errorMessage({ _server_messages: '["{\\"message\\": \\"a\\"}", "{\\"message\\": \\"b\\"}"]' })).toBe('a\nb')
    expect(plugins.errorMessage({ exception: 'frappe.exceptions.ValidationError: boom' })).toBe('boom')
    expect(plugins.errorMessage({ _server_messages: 'not json' })).toBe('Request failed')
    expect(plugins.errorMessage({})).toBe('Request failed')
    expect(plugins.errorMessage(null, 'nope')).toBe('nope')
  })

  it('removePlugin POSTs the plugin id', async () => {
    await plugins.removePlugin('acme.elevation-mask')
    const [url, init] = global.fetch.mock.calls.at(-1)
    expect(url).toContain('webodm_core.api.plugins.remove_plugin')
    expect(JSON.parse(init.body)).toEqual({ plugin: 'acme.elevation-mask' })
  })

  it('only org owners and platform admins manage plugins', () => {
    expect(plugins.canManagePlugins({ org_role: 'Owner' })).toBe(true)
    expect(plugins.canManagePlugins({ is_platform_admin: true })).toBe(true)
    expect(plugins.canManagePlugins({ org_role: 'Member' })).toBe(false)
    expect(plugins.canManagePlugins(null)).toBe(false)
  })

  it('labels plugin origin', () => {
    expect(plugins.pluginTypeLabel({ plugin_type: 'User' })).toBe('Custom')
    expect(plugins.pluginTypeLabel({ plugin_type: 'System' })).toBe('System')
    expect(plugins.pluginTypeLabel({})).toBe('System')
  })
})
