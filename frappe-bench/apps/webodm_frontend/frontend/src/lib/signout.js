// Ending the session is more than the request: it also discards cached client
// state and must report a refused sign-out. Kept out of lib/session.js so that
// module stays pure state, and out of the component so it can be tested without
// mounting the account dropdown.

import { logout as logoutRequest } from './api'
import { clearSession, refreshSession } from './session'
import { toast } from './toast'

/**
 * Sign out. Posts the CSRF-protected logout, clears cached client state and
 * re-reads the authoritative session from the server.
 *
 * Returns false — and reports the failure — when the server refuses the
 * request, so the caller must not navigate as though it succeeded.
 */
export async function signOut() {
  try {
    await logoutRequest()
  } catch (err) {
    toast.error(err?.message || 'Could not sign out. Please try again.')
    return false
  }
  clearSession()
  await refreshSession()
  return true
}
