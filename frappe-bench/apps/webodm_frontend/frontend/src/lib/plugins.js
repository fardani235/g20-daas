// Fetch wrappers for the analysis plugin backend (catalog, enablement, runs,
// output tiles/GeoJSON/download). Mirrors lib/presets.js and lib/organization.js.

function headers(json = false) {
  const h = {}
  if (json) h['Content-Type'] = 'application/json'
  if (window.csrf_token) h['X-Frappe-CSRF-Token'] = window.csrf_token
  return h
}

async function unwrap(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.message || 'Request failed')
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