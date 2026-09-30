// Protected-route contract for the SPA, extracted from main.js so it can be
// regression-tested without mounting the app. The logic is unchanged: routes
// flagged `requiresAuth` are refused for a session the server reports as guest,
// and the attempted destination is preserved for after sign-in.
//
// `layout: 'auto'` routes (the marketplace) resolve the session first because
// App.vue reads `loggedIn` synchronously to pick the shell on entry.

import { getMyOrganization } from './organization'
import { refreshSession } from './session'

/**
 * Resolve a navigation against the session contract.
 *
 * Returns a route location to redirect to, or `null` to allow the navigation.
 */
export async function resolveNavigation(to) {
  if (to.meta.layout === 'auto') {
    await refreshSession()
  }
  if (to.meta.requiresAuth) {
    const signedIn = await refreshSession()
    if (!signedIn) {
      return { name: 'Login', query: { redirect: to.fullPath } }
    }
    if (to.name !== 'Onboarding') {
      try {
        const org = await getMyOrganization()
        if (!org || !org.organization) {
          return { name: 'Onboarding' }
        }
      } catch {
        return { name: 'Onboarding' }
      }
    }
  }
  return null
}
