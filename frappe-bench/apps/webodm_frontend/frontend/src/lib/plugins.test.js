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

  it('uploadPlugin surfaces the server rejection reason', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false,
      status: 417,
      json: () => Promise.resolve({
        exc_type: 'ValidationError',
        _server_messages: JSON.stringify([
          JSON.stringify({ message: 'Invalid plugin package: package has no plugin.json at its root' }),
        ]),
      }),
    }))
    const file = new File(['zip-bytes'], 'plugin.zip', { type: 'application/zip' })
    await expect(plugins.uploadPlugin(file))
      .rejects.toThrow('Invalid plugin package: package has no plugin.json at its root')
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

describe('inputChoices', () => {
  const task = { orthophoto: '/o.tif', dsm: '/d.tif', dtm: null }
  const inputs = [
    { name: 'ortho', datasets: ['orthophoto'], optional: true, label: 'Orthophoto' },
    { name: 'surface', datasets: ['dtm', 'dsm'] },
    { name: 'terrain', datasets: ['dtm'], optional: true },
    { name: 'cloud', datasets: ['point_cloud'] },
  ]

  it('lists only the datasets the task has, first available as default', () => {
    const [ortho, surface, terrain, cloud] = plugins.inputChoices(inputs, task)
    expect(ortho.label).toBe('Orthophoto')
    expect(ortho.options.map(o => o.value)).toEqual(['orthophoto', ''])
    expect(ortho.value).toBe('orthophoto')
    expect(surface.options.map(o => o.value)).toEqual(['dsm'])
    expect(surface.value).toBe('dsm')
    expect(surface.optional).toBe(false)
    expect(surface.missing).toBe(false)
    // optional with nothing available: only "None", not flagged missing
    expect(terrain.options.map(o => o.value)).toEqual([''])
    expect(terrain.value).toBe('')
    expect(terrain.missing).toBe(false)
    // required with nothing available is flagged
    expect(cloud.missing).toBe(true)
    expect(cloud.accepts).toBe('Point cloud')
  })

  it('inputsPayload sends every input, None as null', () => {
    const choices = plugins.inputChoices(inputs, task)
    expect(plugins.inputsPayload(choices)).toEqual({
      ortho: 'orthophoto', surface: 'dsm', terrain: null, cloud: null,
    })
  })

  it('tolerates missing inputs or task', () => {
    expect(plugins.inputChoices(undefined, task)).toEqual([])
    expect(plugins.inputChoices(inputs, null)[1].options).toEqual([])
  })
})

describe('model outputs', () => {
  const model = { name: 'R1', task: 'T1', plugin: 'acme.3d', output_kind: 'model', status: 'Completed',
    output_file: '/private/files/R1.glb', creation: '2026-09-23 10:00:00' }

  it('recognises model runs and links their private file for download', () => {
    expect(plugins.isModelRun(model)).toBe(true)
    expect(plugins.isModelRun({ output_kind: 'raster' })).toBe(false)
    expect(plugins.runDownloadHref(model)).toBe('/private/files/R1.glb')
    expect(plugins.runDownloadHref({ name: 'R2', output_kind: 'vector' })).toContain('run_name=R2')
    expect(plugins.runDownloadHref({ ...model, output_file: null })).toContain('run_name=R1')
  })

  it('builds the viewer route with and without a run', () => {
    expect(plugins.modelViewerPath('P 1', 'T1')).toBe('/project/P%201/task/T1/model')
    expect(plugins.modelViewerPath('P1', 'T1', 'R 1')).toBe('/project/P1/task/T1/model?run=R%201')
  })

  it('lists completed model runs newest first, one per plugin', () => {
    const older = { ...model, name: 'R0', creation: '2026-09-22 10:00:00' }
    const other = { ...model, name: 'R5', plugin: 'acme.other', creation: '2026-09-24 10:00:00' }
    const running = { ...model, name: 'R9', plugin: 'acme.busy', status: 'Running', output_file: null }
    const raster = { ...model, name: 'R7', plugin: 'hillshade', output_kind: 'raster' }
    const out = plugins.completedModelRuns([older, model, other, running, raster])
    expect(out.map(r => r.name)).toEqual(['R5', 'R1'])
  })
})
