## 1. Theme foundation

- [x] 1.1 Add `preconnect` hints for `fonts.googleapis.com` / `fonts.gstatic.com` and the Space Grotesk, Plus Jakarta Sans, and JetBrains Mono stylesheet to `frontend/index.html`; verify the three families load in the browser dev tools Network panel and the page still renders if the request is blocked.
- [x] 1.2 Extend `frontend/tailwind.config.js` with `fontFamily.display` (Space Grotesk), `fontFamily.sans` (Plus Jakarta Sans), `fontFamily.mono` (JetBrains Mono, keeping a system fallback), and the `radar-sweep` / `scan-line` / `pulse-ring` keyframes and animations; verify classes like `font-display`, `font-mono`, `animate-pulse-ring` resolve in the built CSS.
- [x] 1.3 Update both server blocks in `infra/caddy/Caddyfile` CSP to allow `https://fonts.googleapis.com` in `style-src` and `https://fonts.gstatic.com` in `font-src`; verify the deployed page loads the fonts with no CSP console errors.

## 2. Flight simulation engine

- [x] 2.1 Create `frontend/src/composables/useFlightSim.js` implementing drone state, waypoint physics (advance/wrap, smooth heading, speed), rotor/scan angles, telemetry publishing, and coordinate mapping to canvas space; verify unit tests cover waypoint advance, wrap-around, and click-to-route target changes.
- [x] 2.2 Implement the canvas renderer in the composable for the mapping scene: background per layer (optical orthophoto grid, thermal, point cloud/DSM), waypoint path and markers, drone drawing, scan field, and deliverable markers; verify it renders a frame in a stubbed-context test without throwing.
- [x] 2.3 Implement lifecycle handling in the composable: `init`/`resize` with `devicePixelRatio`, `start`/`stop` for the `requestAnimationFrame` loop, teardown that cancels the frame, and a static-frame branch when `prefers-reduced-motion` matches; verify tests assert the loop stops on teardown and under reduced motion.
- [x] 2.4 Implement interaction handlers: `setLayer`, `togglePlay`, `reset`, `selectAt`/`placeWaypoint` from canvas clicks, and point-in-marker hit-testing; verify tests assert layer selection, pause/resume state, reset to initial waypoints, and click hit-testing.

## 3. Hero presentation

- [x] 3.1 Create `frontend/src/components/HeroFlightSim.vue`: canvas with `role="img"` accessible name, top telemetry bar, bottom status ticker, and the mapping-domain deliverable/detail panel; verify the component mounts in a component test with a stubbed canvas and shows telemetry text.
- [x] 3.2 Add the overlay controls to `HeroFlightSim.vue` — pause/resume, reset, and optical/thermal/point-cloud layer buttons — as native buttons with accessible names bound to the composable; verify each control toggles its state in a component test.
- [x] 3.3 Wire visibility and motion handling in the component: `IntersectionObserver`, `visibilitychange`, `ResizeObserver`, and `prefers-reduced-motion` change listener, with teardown on unmount; verify a test asserts the loop is stopped when not intersecting and restored when visible.
- [x] 3.4 Apply the dark identity styling and responsive sizing to the component (bordered `slate-950` panel, cyan accents, mono labels, aspect-ratio canvas with no overflow); verify visually at mobile and desktop widths.

## 4. Landing page refactor

- [x] 4.1 Rewrite `frontend/src/pages/Landing.vue` presentation to the aeropulse identity (dark `slate-950` base, cyan/gradient headlines, glow accents, mono eyebrow labels, rounded bordered cards, sticky translucent nav with mobile menu) while preserving the existing G20 Tech/WebODM copy, pipeline, capabilities, testimonials, pricing plans, CTAs, and footer links; verify every section and anchor still resolves.
- [x] 4.2 Replace the hero's `SurveyFlight` usage with `HeroFlightSim` and remove the now-unused import/`onHeroPhase` wiring; verify the old component is no longer referenced by `Landing.vue` and the hero renders the new simulation.
- [x] 4.3 Confirm the landing stays dark while the app theme is light and that no other page changes: run the dev server, toggle the app theme, and verify `/` renders dark while `/about` and `/contact` are unchanged.
- [x] 4.4 Keep `SurveyFlight.vue` and `useSurveyFlight.js` in the tree, unused, and verify nothing else imports them.

## 5. Tests and verification

- [x] 5.1 Add Vitest tests for the composable and hero component under `src/composables/` / `src/components/`; verify `npm run test` passes (including the pre-existing suite).
- [x] 5.2 Build the SPA (`npm run build`) and verify it succeeds with no missing-font or unresolved-import errors.
- [x] 5.3 Smoke-test the served landing page: sections render in order, nav anchors scroll, click-to-route and layer switching work, and the page is usable with `prefers-reduced-motion` enabled.
