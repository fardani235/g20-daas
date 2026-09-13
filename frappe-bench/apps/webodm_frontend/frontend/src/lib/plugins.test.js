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
})
