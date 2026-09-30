## Context

See `proposal.md` for motivation. The relevant current state:

- The SPA talks to Frappe with `fetch` calls spread across pages and
  `src/lib/*.js`. Most live behind a per-file helper that attaches
  `X-Frappe-CSRF-Token` from `window.csrf_token` and unwraps the response
  (`lib/user.js`, `lib/marketplace.js`, `lib/plugins.js`, `lib/organization.js`,
  `lib/presets.js`).
- Sign-out is the exception: `components/AppLayout.vue` posts directly to
  `/api/method/logout` with no headers and ignores the result.
- Frappe validates CSRF before the handler runs
  (`frappe/auth.py: HTTPRequest.validate_csrf_token`), so the request is
  rejected with `400 Invalid Request` and the session survives.
- `lib/session.js` holds the shared `loggedIn` ref consumed by `App.vue` and
  refreshed by the router guard. `window.csrf_token` is set at login
  (`pages/Login.vue`) and on app mount (`App.vue`) and is session-bound.
- `/api/method/webodm_core.api.csrf.get_token` is `allow_guest=False`, but
  `App.vue` attempts it on every load when the token is unset, so guests get a
  `403`.

## Goals / Non-Goals

**Goals:**
- Sign-out ends the server session and leaves no client state that treats the
  visitor as signed in.
- A refused sign-out is visible to the user.
- The protected-route contract from `specs/session-auth/spec.md` holds after
  sign-out, with a regression test.
- Remove the structural cause: a state-changing request made outside the
  CSRF-aware path.

**Non-Goals:**
- A global response interceptor that signs the user out when any API call
  returns 401/403 (expiry, another tab, admin revocation). Separate follow-up.
- Server-side route enforcement; APIs are already permission-checked.
- Changing how tokens are issued or stored beyond clearing them on sign-out.

## Decisions

### 1. Route sign-out through a shared CSRF-aware POST helper

Add a small shared helper (e.g. `lib/api.js`: `getMethod` / `postMethod`) that
always attaches `X-Frappe-CSRF-Token`, JSON-encodes the body, and throws a
decoded error on a non-OK response. Sign out uses it; the existing per-file
helpers are candidates to migrate onto it but are not required by this change.

- **Why:** the bug exists precisely because sign-out was the one inline POST.
  Centralising the header makes the same omission structurally hard.
- **Alternatives considered:**
  - *Inline the header in `AppLayout.logout()`* — smallest diff, but leaves the
    trap in place and duplicates the token snippet a sixth time.
  - *Introduce a full API-client abstraction with generated methods* — more
    churn than this defect justifies; migration would touch many pages.

### 2. Clear client state explicitly and re-read it from the server

After the helper reports success: set `window.csrf_token = null`, then
`await refreshSession()` (which sets `loggedIn` from the server) before
navigating to the public entry point. Add a `clearSession()` (or equivalent) to
`lib/session.js` so this lives with the rest of the session state.

- **Why:** the token is invalidated by the server when the session ends, and
  `loggedIn` must not linger as `true`. Re-reading rather than assuming keeps
  the single source of truth (the server) authoritative.
- **Alternative considered:** just set `loggedIn.value = false` locally. Works,
  but leaves the possibility of masking a sign-out that did not actually take
  effect; the re-read catches that.

### 3. Surface a failed sign-out; do not navigate as if it succeeded

On a non-OK or network failure, show an error toast (via `lib/toast.js`, the
single toast import site) and stay put. Keep the shared `loggedIn` value as it
was, since the session may still be live.

- **Why:** sign-out is the one action whose silent failure is a security and
  trust defect. The current `try { ... } catch {}` makes a 400 indistinguishable
  from success.
- **Note:** this is also what makes the bug observable in future — a regression
  will show an error instead of a phantom re-login.

### 4. Move the router guard to a testable module; add regression coverage

The guard's redirect behaviour is correct — the defect was upstream of it — so
its logic does not change. It is extracted from `main.js` into
`src/lib/routeGuard.js` (`resolveNavigation(to)`) and `main.js` applies the
returned redirect. This lets the protected-route contract be regression-tested
without mounting the app.

- **Why:** testing "a signed-out visitor is redirected" requires the guard to be
  reachable; an inline closure in `main.js` cannot be imported.
- **Alternatives considered:**
  - *Leave the guard inline and cover only `refreshSession`/`layoutFor`* — would
    not exercise the redirect decision itself.
  - *Extract the whole router (routes + guard) into `src/router.js`* — more
    churn than the contract test needs.
  - *A pre-navigate guard that force-clears state* — not needed once sign-out
    invalidates the session.

### 5. Keep the CSRF bootstrap off public pages

Only attempt the `csrf.get_token` fetch when the visitor is signed in (guard it
on `loggedIn`), instead of firing it on every mount for guests.

- **Why:** removes the guest `403` noise without widening an endpoint.
- **Alternative considered:** make `csrf.get_token` `allow_guest=True`. Rejected
  — a guest token is not needed (login is a guest-safe whitelisted call) and
  widening an endpoint for cosmetic log noise is the wrong trade.

## Risks / Trade-offs

- **[`loggedIn` re-read races the navigation]** → `await refreshSession()`
  before `router.push`, so the layout decision in `App.vue` is based on the
  post-sign-out value.
- **[Helper migration creep]** → the change only requires sign-out to use the
  helper; migrating the other per-file helpers is optional and can be a
  follow-up to keep the diff reviewable.
- **[A sign-out that partially succeeds: server ends the session but the
  response is lost (network drop after commit)]** → the user sees a failure
  toast and may retry; a retry is harmless (logout is idempotent for a guest),
  and the next `refreshSession` reports the truth. The re-read on a later
  navigation is the safety net.
- **[Existing tests mock `window.csrf_token`]** → the shared helper must read it
  lazily at call time (as today) so the existing test pattern keeps working.

## Migration Plan

Pure frontend change; no data migration. Deploy with the SPA build. Rollback is
the previous SPA bundle. Once deployed, the previously-orphaned sessions remain
valid until they expire — re-signing-out with the fixed build (or clearing the
cookie) ends them.

## Open Questions

None blocking. The follow-up for global session-expiry handling is deliberately
deferred and recorded as a non-goal rather than left open.
