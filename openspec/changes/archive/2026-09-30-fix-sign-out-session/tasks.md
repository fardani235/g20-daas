## 1. Shared CSRF-aware request helper

- [x] 1.1 Add a shared POST handler (e.g. `src/lib/api.js`) that always attaches `X-Frappe-CSRF-Token` from `window.csrf_token`, JSON-encodes the body, and throws a decoded error (via `lib/utils.js: frappeErrorMessage`) on a non-OK response; verify with a unit test covering header present, header omitted when no token, and error decoding.
- [x] 1.2 Export a `logout()` (or `postMethod('logout')`) built on that handler and confirm the existing test pattern (`global.window.csrf_token = 'x'`) still drives it — token is read lazily at call time, not at import.

## 2. Session state clearing

- [x] 2.1 Add a `clearSession()` to `src/lib/session.js` that nulls `window.csrf_token` and resets `loggedIn`; verify with a unit test that both are reset.
- [x] 2.2 Extend `src/lib/session.test.js` so `clearSession()` followed by a stubbed guest `get_logged_user` leaves `loggedIn === false` — verify the new assertions pass.

## 3. Sign-out flow

- [x] 3.1 Replace the inline `fetch` in `components/AppLayout.vue` with the shared POST handler, then on success: `clearSession()`, `await refreshSession()`, and navigate to the public entry point. Verify with a component test that the request carries the CSRF header and that `loggedIn` is false after sign-out.
- [x] 3.2 On a non-OK response or network error, show a `toast.error(...)` (via `lib/toast.js`) and do not clear state or navigate; verify with a test that a stubbed 400 leaves `loggedIn` unchanged and emits the error toast.

## 4. Route-guard regression coverage

- [x] 4.1 Add a test that a signed-out visitor (guest `get_logged_user`) entering `/dashboard` and `/projects` resolves to the `/login` redirect with the attempted route preserved — verify `resolveNavigation` returns `{ name: 'Login', query: { redirect } }` and the organization lookup is skipped.
- [x] 4.2 Add a test asserting that immediately after a successful sign-out, a protected route does not render `AppLayout` — verify the guard's redirect together with the `layoutFor(meta, loggedIn)` decision (the route would be `app` layout, so the redirect is what keeps the shell off screen).

## 5. Guest-safe token bootstrap

- [x] 5.1 In `src/App.vue`, only attempt the `csrf.get_token` fetch when the visitor is signed in (guard on `loggedIn`); verify with a test that a guest mount issues no `get_token` request, so the `403` disappears from the network log.

## 6. Verification

- [x] 6.1 Run `npm test` in `frappe-bench/apps/webodm_frontend/frontend` and confirm the full vitest suite passes with the new tests.
- [x] 6.2 Manual browser check against the running stack: sign out, confirm `POST /api/method/logout` returns 200 (not 400), `GET /api/method/frappe.auth.get_logged_user` returns `Guest`, `/dashboard` and `/projects` redirect to `/login`, and `/marketplace` renders signed-out chrome.
