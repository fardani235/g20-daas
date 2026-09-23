// Fetch wrappers for the analysis plugin backend (catalog, enablement, runs,
// output tiles/GeoJSON/download). Mirrors lib/presets.js and lib/organization.js.

import { frappeErrorMessage } from './utils'

function headers(json = false) {
  const h = {}
  if (json) h['Content-Type'] = 'application/json'
  if (window.csrf_token) h['X-Frappe-CSRF-Token'] = window.csrf_token
  return h
}

async function unwrap(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(frappeErrorMessage(err))
  }
  const data = await res.json()
  return data.message !== undefined ? data.message : data
}

function query(params) {
  const entries = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null)
  if (!entries.length) return ''
  return '?' + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&')
}

function get(method, params) {
  return fetch(`/api/method/${method}${query(params)}`, { method: 'GET', headers: headers() }).then(unwrap)
}

function post(method, body) {
  return fetch(`/api/method/${method}`, {
    method: 'POST',
    headers: headers(true),
    body: JSON.stringify(body || {}),
  }).then(unwrap)
}

export const listPlugins = () => get('webodm_core.api.plugins.list_plugins')
export const savePluginSetting = payload => post('webodm_core.api.plugins.save_plugin_setting', payload)
export const removePlugin = plugin => post('webodm_core.api.plugins.remove_plugin', { plugin })

// Upload a user plugin package (.zip). Multipart, so no JSON content type —
// the browser sets the boundary itself.
export function uploadPlugin(file) {
  const body = new FormData()
  body.append('file', file, file.name)
  return fetch('/api/method/webodm_core.api.plugins.upload_plugin', {
    method: 'POST',
    headers: headers(),
    body,
  }).then(unwrap)
}

// Whether the caller may upload/remove/enable plugins: organization owners and
// platform admins (mirrors the backend's _require_org_admin).
export function canManagePlugins(me) {
  return !!(me && (me.is_platform_admin || me.org_role === 'Owner'))
}

// Short badge text for a catalog entry's origin.
export function pluginTypeLabel(plugin) {
  return plugin?.plugin_type === 'User' ? 'Custom' : 'System'
}
export const runPlugin = payload => post('webodm_core.api.plugins.run_plugin', payload)
export const listRuns = task => get('webodm_core.api.plugins.list_runs', task ? { task } : undefined)
export const getRun = name => get('webodm_core.api.plugins.get_run', { name })
export const cancelRun = name => post('webodm_core.api.plugins.cancel_run', { name })
export const getRunGeojson = name => get('webodm_core.api.plugins.get_run_geojson', { run_name: name })
export const runInfo = runName => get('webodm_core.api.tiles.run_info', { run_name: runName })

// Leaflet XYZ template for a run's raster output.
export const runTileUrl = runName =>
  `/api/method/webodm_core.api.tiles.serve_run?run_name=${encodeURIComponent(runName)}&z={z}&x={x}&y={y}`

// Direct download URL for a run's output artifact.
export const runDownloadUrl = runName =>
  `/api/method/webodm_core.api.plugins.download_run_output?run_name=${encodeURIComponent(runName)}`

// Vector outputs above this many features are not drawn client-side; they stay
// downloadable and the UI warns instead of freezing the map.
export const MAX_VECTOR_FEATURES = 5000

// Whether a vector overlay is small enough to draw client-side.
export function shouldRenderVector(count, cap = MAX_VECTOR_FEATURES) {
  return (Number(count) || 0) <= cap
}

// Default parameter values declared by an operation's JSON schema, keyed by name.
export function schemaDefaults(schema) {
  const out = {}
  const properties = schema?.properties || {}
  for (const [name, spec] of Object.entries(properties)) {
    if (spec && spec.default !== undefined) out[name] = spec.default
  }
  return out
}

// Newest run per plugin. Input is usually newest-first (list_runs orders by
// creation desc); creation is compared so ordering bugs can't show an old run.
export function latestRunPerPlugin(runs) {
  const byPlugin = new Map()
  for (const run of runs || []) {
    const current = byPlugin.get(run.plugin)
    if (!current || String(run.creation || '') > String(current.creation || '')) {
      byPlugin.set(run.plugin, run)
    }
  }
  return [...byPlugin.values()]
}
// Human labels for the task datasets a plugin input can draw from.
export const DATASET_LABELS = {
  orthophoto: 'Orthophoto',
  dsm: 'DSM',
  dtm: 'DTM',
  point_cloud: 'Point cloud',
  model: '3D model',
}

// One row per plugin input for the run dialog: which of the input's accepted
// datasets the task actually has, and which one is picked by default (the
// first available, mirroring the server). Optional inputs get a "None" option;
// a required input with nothing available is flagged so the dialog can say so.
export function inputChoices(inputs, task) {
  return (inputs || []).map(spec => {
    const datasets = Array.isArray(spec.datasets) ? spec.datasets : []
    const available = datasets.filter(d => !!task?.[d])
    const optional = !!spec.optional
    const options = available.map(d => ({ value: d, label: DATASET_LABELS[d] || d }))
    if (optional) options.push({ value: '', label: 'None' })
    return {
      name: spec.name,
      label: spec.label || spec.name,
      optional,
      options,
      value: available[0] || '',
      missing: !optional && !available.length,
      // What the input would accept, for the "task has none of" message.
      accepts: datasets.map(d => DATASET_LABELS[d] || d).join(', '),
    }
  })
}

// The `inputs` payload for run_plugin from the dialog's choices. Inputs left
// at their server default are still sent so the stored run reflects exactly
// what the user saw; optional inputs set to "None" are sent as null.
export function inputsPayload(choices) {
  const out = {}
  for (const c of choices || []) out[c.name] = c.value || null
  return out
}

// ---------------------------------------------------------------------------
// 3D model outputs (output_kind "model": a GLB opened in the model viewer)
// ---------------------------------------------------------------------------

export const isModelRun = run => run?.output_kind === 'model'

// Where a run's output should be fetched from for download. Model outputs use
// the private file URL directly (Frappe streams it with the session cookie);
// the download endpoint buffers the whole file in memory, fine for a GeoJSON
// but not for a multi-hundred-MB GLB.
export function runDownloadHref(run) {
  if (isModelRun(run) && run.output_file) return run.output_file
  return runDownloadUrl(run.name)
}

// SPA route of the 3D viewer for a task, optionally showing a plugin run's model.
export function modelViewerPath(projectId, taskId, runName) {
  const base = `/project/${encodeURIComponent(projectId)}/task/${encodeURIComponent(taskId)}/model`
  return runName ? `${base}?run=${encodeURIComponent(runName)}` : base
}

// Newest completed model run per plugin, newest first.
export function completedModelRuns(runs) {
  return latestRunPerPlugin(runs)
    .filter(r => isModelRun(r) && r.status === 'Completed' && r.output_file)
    .sort((a, b) => String(b.creation || '').localeCompare(String(a.creation || '')))
}
