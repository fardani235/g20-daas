## Why

Signing out does not end the session. The SPA posts to `/api/method/logout`
without the CSRF token, Frappe rejects it with `400 Invalid Request` before
`LoginManager.logout()` runs, and the `sid` cookie stays valid. The user is
told nothing and is redirected to the public landing page as if the sign-out
succeeded. On the next session check the server still reports the user as
authenticated, so the Marketplace tab re-renders the signed-in chrome and every
`requiresAuth` route (`/dashboard`, `/projects`, `/settings`, …) still opens —
with no password. Sign-out is the one operation where a silent failure is a
security and trust defect, not a cosmetic one.

## What Changes

- Sign-out invalidates the session for real: the logout request carries the
  CSRF token, and the client clears its cached auth state
  (`window.csrf_token`, the shared `loggedIn` ref) afterwards.
- A failed sign-out is surfaced to the user instead of swallowed; the app no
  longer pretends to have signed out when the server refused.
- The route guard's existing behaviour is made an explicit contract: after a
  successful sign-out, any `requiresAuth` route redirects to sign-in.
- Route the logout POST through a shared CSRF-aware request helper so the
  header cannot be forgotten again (logout is currently the only logged-in POST
  with no helper, which is exactly why it broke).

## Capabilities

### New Capabilities

- `session-auth`: how the single-page app establishes, reflects and terminates
  an authenticated browser session — sign-out semantics (including the failure
  case) and the contract that protected routes are unreachable once the session
  has ended.

### Modified Capabilities

_(none — no existing capability specifies session lifecycle or the
protected-route contract; `marketplace` covers guest vs. signed-in rendering
and `landing-page` covers the public page, so neither requirement changes.)_

## Impact

- **Frontend (SPA)**:
  - `components/AppLayout.vue` — `logout()` must send the CSRF token, check the
    response, and clear client auth state before navigating.
  - `lib/session.js` — a way to reset the shared `loggedIn` state on sign-out
    (and/or re-read it from the server).
  - A shared CSRF-aware POST helper, used by logout and available to the
    page-local request snippets.
  - `App.vue` — the `get_token` call on public pages returns `403` for guests
    because the endpoint is `allow_guest=False`; folded in as a small cleanup.
- **Backend**: no change required. `frappe.handler.logout`
  (`@frappe.whitelist(allow_guest=True, methods=["POST"])`) already does the
  right thing once the request passes CSRF.
- **Tests**: frontend unit tests for the guard's post-sign-out redirect and for
  logout attaching the CSRF header / clearing state.
- **Non-goals**: global handling of a session that ends server-side for other
  reasons (expiry, sign-out in another tab, admin revocation) and server-side
  route enforcement. These need a response-level 401/403 interceptor and are a
  separate follow-up; this change keeps to the sign-out defect and the route
  contract it exposes.
