// Fetch wrappers for the presets/settings backend. Each returns the unwrapped
// Frappe `message` payload. POSTs carry the CSRF token like the rest of the app.

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

function get(method) {
  return fetch(`/api/method/${method}`, { method: 'GET', headers: headers() }).then(unwrap)
}

function post(method, body) {
  return fetch(`/api/method/${method}`, {
    method: 'POST',
    headers: headers(true),
    body: JSON.stringify(body || {}),
  }).then(unwrap)
}

export const listPresets = () => get('webodm_core.api.presets.list_presets')
export const fetchOptions = () => get('webodm_core.api.presets.options')
export const savePreset = payload => post('webodm_core.api.presets.save', payload)
export const deletePreset = name => post('webodm_core.api.presets.delete', { name })
export const whoami = () => get('webodm_core.api.session.whoami')
export const getSettings = () => get('webodm_core.api.settings.get')
export const saveSettings = fields => post('webodm_core.api.settings.save', fields)
