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
  if (plugin?.plugin_type !== 'User') return 'System'
  // A user row installed from the marketplace, as opposed to a manual upload.
  return plugin?.source === 'Marketplace' ? 'Marketplace' : 'Custom'
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

// A completed run whose artifact is a 3D model (GLB) for the model viewer.
export function isModelRun(run) {
  return run?.output_kind === 'model' && run?.status === 'Completed' && !!run?.output_file
}

// Route to the 3D viewer showing a run's model instead of the task's own.
export function runModelRoute(projectId, taskName, runName) {
  return `/project/${encodeURIComponent(projectId)}/task/${encodeURIComponent(taskName)}/model?run=${encodeURIComponent(runName)}`
}

// Live progress text for a run row: "42% · decimating" while running, else null.
export function runProgressText(run) {
  if (!run || !['Queued', 'Running'].includes(run.status)) return null
  const pct = Math.round(Number(run.progress) || 0)
  const message = (run.progress_message || '').trim()
  if (!pct && !message) return null
  return message ? `${pct}% · ${message}` : `${pct}%`
}

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

// What makes two runs "the same result": the plugin and, for a comparison
// run, the survey it was compared with (mirrors the backend's one-run-per-slot
// rule, so October-vs-September and October-vs-August both keep a row/layer).
export function runSlotKey(run) {
  return run?.reference_task ? `${run.plugin}|${run.reference_task}` : `${run?.plugin}`
}

// Newest run per plugin (per plugin + comparison survey for comparison runs).
// Input is usually newest-first (list_runs orders by creation desc); creation
// is compared so ordering bugs can't show an old run.
export function latestRunPerPlugin(runs) {
  const bySlot = new Map()
  for (const run of runs || []) {
    const key = runSlotKey(run)
    const current = bySlot.get(key)
    if (!current || String(run.creation || '') > String(current.creation || '')) {
      bySlot.set(key, run)
    }
  }
  return [...bySlot.values()]
}

// --- comparison plugins: a run that also reads a second survey -------------

// An input the plugin reads from the comparison ("reference") survey.
export const isReferenceInput = spec => spec?.survey === 'reference'

// A plugin compares two surveys when any of its inputs comes from the second one.
export function needsReferenceSurvey(plugin) {
  return (plugin?.inputs || []).some(isReferenceInput)
}

// "Site A (2 Oct 2026)": a task as an option in the comparison picker.
export function surveyLabel(task) {
  if (!task) return ''
  const title = task.title || task.name
  const day = String(task.creation || '').slice(0, 10)
  return day ? `${title} (${day})` : title
}

// The surveys a task can be compared with: the other completed tasks of the
// same project (`tasks` is the project's task list). The backend enforces the
// same rule plus the organization; this only decides what is offered.
export function referenceChoices(tasks, currentTask) {
  return (tasks || [])
    .filter(t => t && t.name !== currentTask?.name && t.status === 'Completed')
    .map(t => ({ value: t.name, label: surveyLabel(t) }))
}

// Row/layer label of a run: the plugin, plus what it was compared with.
export function runLabel(pluginLabel, run, tasks) {
  if (!run?.reference_task) return pluginLabel
  const other = (tasks || []).find(t => t.name === run.reference_task)
  return `${pluginLabel} vs ${other?.title || run.reference_task}`
}

// Numbers a plugin reports for people: `metadata.summary` is a list of
// {label, value} rows and `metadata.warnings` a list of sentences. Any plugin
// may write them; the run row shows them as they are. Tolerates the metadata
// arriving as a JSON string (list_runs) or an object (get_run).
export function runSummary(run) {
  let meta = run?.output_metadata
  if (typeof meta === 'string') {
    try { meta = JSON.parse(meta) } catch { meta = null }
  }
  const rows = (Array.isArray(meta?.summary) ? meta.summary : [])
    .filter(r => r && r.label != null && r.value != null)
    .map(r => ({ label: String(r.label), value: String(r.value) }))
  const warnings = (Array.isArray(meta?.warnings) ? meta.warnings : [])
    .filter(w => typeof w === 'string' && w.trim())
  return { rows, warnings }
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
// datasets its survey actually has, and which one is picked by default (the
// first available, mirroring the server). Optional inputs get a "None" option;
// a required input with nothing available is flagged so the dialog can say so.
// Inputs of a comparison plugin marked `survey: "reference"` are resolved
// against `referenceTask` (the survey picked to compare with) instead of `task`.
export function inputChoices(inputs, task, referenceTask = null) {
  return (inputs || []).map(spec => {
    const datasets = Array.isArray(spec.datasets) ? spec.datasets : []
    const reference = isReferenceInput(spec)
    const source = reference ? referenceTask : task
    const available = datasets.filter(d => !!source?.[d])
    const optional = !!spec.optional
    const options = available.map(d => ({ value: d, label: DATASET_LABELS[d] || d }))
    if (optional) options.push({ value: '', label: 'None' })
    return {
      name: spec.name,
      label: spec.label || spec.name,
      optional,
      reference,
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
