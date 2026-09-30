// Shared Frappe method client.
//
// Every state-changing request must carry the session CSRF token: Frappe
// validates it in `HTTPRequest.validate_csrf_token` (frappe/auth.py) before the
// handler runs, so a POST without the header is rejected with 400 and the
// action never happens. Sign-out was the one inline fetch that forgot it, so
// the session survived a "successful" logout. Routing such requests through
// here makes the omission structurally hard.

import { frappeErrorMessage } from './utils'

function headers() {
  const h = { 'Content-Type': 'application/json' }
  // Read lazily: the token is set after login and cleared on sign-out, so it
  // must not be captured at import time.
  if (window.csrf_token) h['X-Frappe-CSRF-Token'] = window.csrf_token
  return h
}

async function unwrap(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(frappeErrorMessage(err))
  }
  const data = await res.json().catch(() => ({}))
  return data.message !== undefined ? data.message : data
}

/** POST a whitelisted Frappe method; resolves its `message` payload. */
export function postMethod(method, body) {
  return fetch(`/api/method/${method}`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body || {}),
  }).then(unwrap)
}

/** End the server session. The caller clears the client's cached state. */
export function logout() {
  return postMethod('logout')
}
