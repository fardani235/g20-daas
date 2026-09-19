// Fetch wrappers for user profile and password APIs.

function h(json = false) {
  const headers = {}
  if (json) headers['Content-Type'] = 'application/json'
  if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
  return headers
}

async function unwrap(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.message || err._server_messages || 'Request failed')
  }
  const data = await res.json()
  return data.message !== undefined ? data.message : data
}

export async function getCurrentUser() {
  const res = await fetch('/api/method/webodm_core.api.user.get_profile', {
    method: 'GET',
    headers: h(),
  })
  return unwrap(res)
}

export async function updateUser(fields) {
  const res = await fetch('/api/method/webodm_core.api.user.update_profile', {
    method: 'POST',
    headers: h(true),
    body: JSON.stringify(fields),
  })
  return unwrap(res)
}

export async function changePassword(oldPassword, newPassword) {
  const res = await fetch('/api/method/frappe.core.doctype.user.user.update_password', {
    method: 'POST',
    headers: h(true),
    body: JSON.stringify({
      old_password: oldPassword,
      new_password: newPassword,
      logout_all_sessions: 0,
    }),
  })
  return unwrap(res)
}
