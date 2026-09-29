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
  const entries = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '')
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

// --- API -------------------------------------------------------------------

/** `{ products, categories, viewer }` — works signed-out. */
export const listProducts = params => get('webodm_core.api.marketplace.list_products', params)
/** Full product page incl. releases and the caller's `viewer` context. */
export const getProduct = product => get('webodm_core.api.marketplace.get_product', { product })
export const installProduct = (product, release) =>
  post('webodm_core.api.marketplace.install_product', release ? { product, release } : { product })
export const uninstallProduct = product => post('webodm_core.api.marketplace.uninstall_product', { product })
export const myEntitlements = () => get('webodm_core.api.marketplace.my_entitlements')

// --- pure helpers ----------------------------------------------------------

export const KIND_LABELS = Object.freeze({
  plugin: 'Analysis plugin',
  preset: 'Processing preset',
  basemap: 'Basemap',
  model: 'Model weights',
})

export function kindLabel(kind) {
  return KIND_LABELS[kind] || kind || ''
}

/**
 * Client-side catalog filter: every whitespace-separated term of `q` must
 * appear in the title, summary, id, publisher or a category label; `category`
 * and `kind` are exact matches. Mirrors the server's `list_products` so the
 * browse page can fetch once and filter as the user types.
 */
export function filterProducts(products, { q = '', category = '', kind = '' } = {}) {
  const terms = String(q || '').toLowerCase().split(/\s+/).filter(Boolean)
  return (products || []).filter(p => {
    if (kind && p.artifact_kind !== kind) return false
    if (category && !(p.categories || []).some(c => c.category_id === category)) return false
    if (!terms.length) return true
    const hay = [
      p.title, p.summary, p.product_id, p.publisher?.display_name,
      ...(p.categories || []).map(c => c.label),
    ].filter(Boolean).join(' ').toLowerCase()
    return terms.every(t => hay.includes(t))
  })
}

/**
 * What the install panel should offer, from the server's `viewer` block.
 * Returns one of:
 *   'guest'         — not signed in
 *   'no-org'        — signed in, no organization (cannot install)
 *   'member'        — signed in, not an org admin
 *   'unavailable'   — admin, but no installable release
 *   'install' | 'installed' | 'update'
 */
export function installState(product) {
  const viewer = product?.viewer || {}
  if (!viewer.signed_in) return 'guest'
  if (!viewer.organization) return 'no-org'
  if (!viewer.can_install) return 'member'
  if (!product.latest_release) return viewer.entitlement ? 'installed' : 'unavailable'
  if (!viewer.entitlement) return 'install'
  return viewer.entitlement.update_available ? 'update' : 'installed'
}

/**
 * Whether a signed-out visitor sees a Download button for the latest release:
 * only when the publisher opted in (and so the license permits redistribution).
 * The server also enforces this; `download_url` is null otherwise.
 */
export function downloadPolicy(product) {
  const rel = product?.latest_release
  const viewer = product?.viewer || {}
  if (!rel) return { canDownload: false, url: null, reason: 'no-release' }
  if (rel.download_url) return { canDownload: true, url: rel.download_url, reason: null }
  return {
    canDownload: false,
    url: null,
    reason: viewer.signed_in ? 'unavailable' : 'sign-in',
  }
}

export function formatVersionDate(value) {
  if (!value) return ''
  const d = new Date(String(value).replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function shortHash(hash) {
  return hash ? `${hash.slice(0, 12)}…` : ''
}

export function productRoute(productId) {
  return `/marketplace/${encodeURIComponent(productId)}`
}
