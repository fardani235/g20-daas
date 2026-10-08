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

describe('model runs', () => {
  it('recognises completed model outputs only', () => {
    expect(plugins.isModelRun({ output_kind: 'model', status: 'Completed', output_file: '/private/files/a.glb' })).toBe(true)
    expect(plugins.isModelRun({ output_kind: 'model', status: 'Running', output_file: null })).toBe(false)
    expect(plugins.isModelRun({ output_kind: 'raster', status: 'Completed', output_file: '/x.tif' })).toBe(false)
    expect(plugins.isModelRun(null)).toBe(false)
  })

  it('builds the viewer route for a run', () => {
    expect(plugins.runModelRoute('P 1', 'task/1', 'r&2'))
      .toBe('/project/P%201/task/task%2F1/model?run=r%262')
  })

  it('formats live progress only while a run is active', () => {
    expect(plugins.runProgressText({ status: 'Running', progress: 42.4, progress_message: 'decimating' })).toBe('42% · decimating')
    expect(plugins.runProgressText({ status: 'Running', progress: 7 })).toBe('7%')
    expect(plugins.runProgressText({ status: 'Queued', progress: 0, progress_message: '' })).toBeNull()
    expect(plugins.runProgressText({ status: 'Completed', progress: 100, progress_message: 'x' })).toBeNull()
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
    expect(plugins.pluginTypeLabel({ plugin_type: 'User', source: 'Upload' })).toBe('Custom')
    expect(plugins.pluginTypeLabel({ plugin_type: 'User', source: 'Marketplace' })).toBe('Marketplace')
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

describe('comparison plugins', () => {
  const compare = {
    inputs: [
      { name: 'surface', label: 'This survey', datasets: ['dsm', 'dtm'] },
      { name: 'reference_surface', label: 'Comparison survey', datasets: ['dsm', 'dtm'], survey: 'reference' },
      { name: 'reference_orthophoto', datasets: ['orthophoto'], survey: 'reference', optional: true },
    ],
  }
  const tasks = [
    { name: 'T3', title: 'October', status: 'Completed', creation: '2026-10-01 08:00:00', dsm: '/f/a.tif' },
    { name: 'T2', title: 'September', status: 'Completed', creation: '2026-09-01 08:00:00', dtm: '/f/b.tif', orthophoto: '/f/o.tif' },
    { name: 'T1', title: 'Still processing', status: 'Running', creation: '2026-08-01 08:00:00' },
  ]

  it('knows which plugins need a second survey', () => {
    expect(plugins.needsReferenceSurvey(compare)).toBe(true)
    expect(plugins.needsReferenceSurvey({ inputs: [{ name: 'raster', datasets: ['dsm'] }] })).toBe(false)
    expect(plugins.needsReferenceSurvey(null)).toBe(false)
    expect(plugins.isReferenceInput(compare.inputs[1])).toBe(true)
    expect(plugins.isReferenceInput(compare.inputs[0])).toBe(false)
  })

  it('offers the other completed surveys of the project', () => {
    expect(plugins.referenceChoices(tasks, tasks[0])).toEqual([
      { value: 'T2', label: 'September (2026-09-01)' },
    ])
    expect(plugins.referenceChoices(tasks, tasks[1])).toEqual([
      { value: 'T3', label: 'October (2026-10-01)' },
    ])
    expect(plugins.referenceChoices([], tasks[0])).toEqual([])
    expect(plugins.referenceChoices(null, null)).toEqual([])
    expect(plugins.surveyLabel({ name: 'T9' })).toBe('T9')
  })

  it('resolves each input against its own survey', () => {
    const choices = plugins.inputChoices(compare.inputs, tasks[0], tasks[1])
    expect(choices.map(c => [c.name, c.reference, c.value, c.missing])).toEqual([
      ['surface', false, 'dsm', false],
      ['reference_surface', true, 'dtm', false],
      ['reference_orthophoto', true, 'orthophoto', false],
    ])
    // No comparison survey picked yet: its required input is missing, the optional one is just empty.
    const none = plugins.inputChoices(compare.inputs, tasks[0], null)
    expect(none.map(c => [c.name, c.value, c.missing])).toEqual([
      ['surface', 'dsm', false],
      ['reference_surface', '', true],
      ['reference_orthophoto', '', false],
    ])
    expect(plugins.inputsPayload(choices)).toEqual({
      surface: 'dsm', reference_surface: 'dtm', reference_orthophoto: 'orthophoto',
    })
  })

  it('keeps one run per plugin and comparison survey', () => {
    const runs = [
      { name: 'R4', plugin: 'change', reference_task: 'T2', creation: '2026-10-04' },
      { name: 'R3', plugin: 'change', reference_task: 'T1', creation: '2026-10-03' },
      { name: 'R2', plugin: 'change', reference_task: 'T2', creation: '2026-10-02' },
      { name: 'R1', plugin: 'contours', creation: '2026-10-01' },
    ]
    expect(plugins.latestRunPerPlugin(runs).map(r => r.name)).toEqual(['R4', 'R3', 'R1'])
    expect(plugins.runSlotKey(runs[0])).toBe('change|T2')
    expect(plugins.runSlotKey(runs[3])).toBe('contours')
  })

  it('labels a comparison run with the survey it was compared with', () => {
    expect(plugins.runLabel('Change Detection', { reference_task: 'T2' }, tasks)).toBe('Change Detection vs September')
    expect(plugins.runLabel('Change Detection', { reference_task: 'gone' }, tasks)).toBe('Change Detection vs gone')
    expect(plugins.runLabel('Contours', { plugin: 'contours' }, tasks)).toBe('Contours')
  })

  it('reads the summary rows and warnings a plugin reports', () => {
    const meta = {
      summary: [{ label: 'Fill', value: '604 m³ ± 12' }, { label: 'Broken' }, null, { label: 'Changed', value: 4 }],
      warnings: ['Check the offset', '', 7],
    }
    const expected = {
      rows: [{ label: 'Fill', value: '604 m³ ± 12' }, { label: 'Changed', value: '4' }],
      warnings: ['Check the offset'],
    }
    expect(plugins.runSummary({ output_metadata: meta })).toEqual(expected)
    expect(plugins.runSummary({ output_metadata: JSON.stringify(meta) })).toEqual(expected)
    for (const empty of [null, {}, { output_metadata: '{not json' }, { output_metadata: { summary: 'x' } }]) {
      expect(plugins.runSummary(empty)).toEqual({ rows: [], warnings: [] })
    }
  })
})
