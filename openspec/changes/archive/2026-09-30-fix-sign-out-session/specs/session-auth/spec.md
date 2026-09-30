## Purpose

Defines how the single-page application establishes, reflects and terminates an
authenticated browser session: what signing out guarantees, how a refused
sign-out is reported, and the contract that authenticated-only routes cannot be
reached once the session has ended.

## ADDED Requirements

### Requirement: Sign-out terminates the authenticated session

Signing out SHALL end the server-side session for the current browser. The
sign-out request MUST be accepted by the server as a valid state-changing
request for the authenticated session (it MUST carry whatever proof the server
requires for such requests) and MUST result in the session no longer being
authenticated. A sign-out that the server refuses MUST NOT be reported to the
user as a successful sign-out.

#### Scenario: Successful sign-out ends the session

- **WHEN** an authenticated user signs out and the server accepts the request
- **THEN** a subsequent check of the current user reports an unauthenticated
  (guest) session

#### Scenario: Sign-out is accepted despite CSRF protection

- **WHEN** an authenticated user signs out
- **THEN** the sign-out request includes the session's CSRF token and the
  server responds with success rather than a rejected-request error

#### Scenario: Refused sign-out is not silently ignored

- **WHEN** the sign-out request fails (rejected by the server or a network
  error)
- **THEN** the user is shown that signing out failed, the session is not
  treated as ended, and the user is not redirected as though it succeeded

#### Scenario: Cached auth state is cleared on sign-out

- **WHEN** sign-out succeeds
- **THEN** the client discards its cached session credentials and its cached
  signed-in indicator, so no later view treats the visitor as signed in

### Requirement: Protected routes are unreachable without an authenticated session

Routes marked as requiring authentication SHALL NOT render their content to a
visitor whose session is not authenticated. Such a visitor MUST be sent to the
sign-in page, and the destination they attempted MUST be preserved so it can be
used after signing in.

#### Scenario: Signed-out visitor is redirected to sign-in

- **WHEN** a visitor without an authenticated session opens a
  protected route such as `/dashboard`, `/projects` or `/settings`
- **THEN** the visitor is redirected to the sign-in page and the protected
  content is not rendered

#### Scenario: Intended destination is preserved

- **WHEN** a signed-out visitor is redirected to sign-in from a protected route
- **THEN** the sign-in flow can return them to that route after a successful
  sign-in

#### Scenario: No protected route renders the signed-in shell after sign-out

- **WHEN** a user signs out and then opens any protected route
- **THEN** the signed-in application shell is not shown and the visitor is
  treated as signed out

### Requirement: Public surfaces stay guest-safe

Pages reachable without a session SHALL NOT depend on authenticated-only
requests to render, and MUST NOT produce failing authenticated bootstrap
requests for a visitor who is not signed in.

#### Scenario: Guest browsing a public page

- **WHEN** a visitor without a session opens a public page such as the landing
  page or the marketplace
- **THEN** the page renders in its signed-out form and no authenticated-only
  request made by the app fails

#### Scenario: Guest returns to public browsing after sign-out

- **WHEN** a user signs out and returns to a public page
- **THEN** the page renders its signed-out form and offers the sign-in entry
  point
