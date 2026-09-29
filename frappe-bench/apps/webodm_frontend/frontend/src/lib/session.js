import { ref } from 'vue'

/**
 * Signed-in state shared by the router guard and App.vue.
 *
 * `loggedIn` is null until the first check resolves, then true/false. Routes
 * with `meta.layout === 'auto'` (the marketplace) render inside AppLayout when
 * signed in and as a bare page with public chrome otherwise, so the guard
 * refreshes this before entering them and the layout decision is synchronous.
 */
export const loggedIn = ref(null)

export async function refreshSession() {
  try {
    const res = await fetch('/api/method/frappe.auth.get_logged_user')
    if (!res.ok) {
      loggedIn.value = false
    } else {
      const data = await res.json()
      loggedIn.value = !!(data.message && data.message !== 'Guest')
    }
  } catch {
    loggedIn.value = false
  }
  return loggedIn.value
}

/**
 * Which shell a route renders in: 'app' (AppLayout), 'bare' (the page draws
 * its own chrome) — pure so it can be unit-tested without a router.
 */
export function layoutFor(meta, isLoggedIn) {
  if (!meta || meta.layout === undefined) return 'app'
  if (meta.layout === false) return 'bare'
  if (meta.layout === 'auto') return isLoggedIn === true ? 'app' : 'bare'
  return 'app'
}
