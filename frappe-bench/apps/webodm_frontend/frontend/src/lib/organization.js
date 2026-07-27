// Fetch wrappers for the organization/tenancy backend. Mirrors lib/presets.js.

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
    method: 'POST', headers: headers(true), body: JSON.stringify(body || {}),
  }).then(unwrap)
}

export const getMyOrganization = () => get('webodm_core.api.organization.get_my_organization')
export const createOrganization = name => post('webodm_core.api.organization.create_organization', { name })
export const acceptInvitation = token => post('webodm_core.api.organization.accept_invitation', { token })
export const inviteMember = email => post('webodm_core.api.organization.invite_member', { email })
export const listMembers = () => get('webodm_core.api.organization.list_members')
