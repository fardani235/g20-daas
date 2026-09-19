## Context

See `proposal.md` for motivation and `specs/landing-page/spec.md` for the
behavior contract. Constraints that shape the approach:

- The landing page is a Vue 3 SFC at
  `frappe-bench/apps/webodm_frontend/frontend/src/pages/Landing.vue`, served by
  the SPA at route `/` (public, `meta: { layout: false }`).
- The SPA is Tailwind v3 with HSL semantic tokens in `src/index.css` and
  `data-theme`/`dark` on `<html>` toggled by `useTheme.js`. The landing currently
  mixes semantic tokens (`bg-background`, `text-foreground`, `bg-card`) with a
  dark hero, so a light app theme washes out most of the page.
- `frappe-ui` is wired into the SPA; `lucide-vue-next` is already a dependency.
  There is no motion/animation library.
- The reference project (`~/workspaces/aeropulse`) is React 19 + Tailwind v4
  and uses `lucide-react`; its `HeroAnimation.tsx` is a plain-canvas simulation
  (`requestAnimationFrame`, 2D context) wrapped in JSX overlays. The canvas and
  physics are framework-agnostic; only the overlays and state are React-specific.
- Production is served through Caddy with a strict CSP
  (`infra/caddy/Caddyfile`): `style-src 'self' 'unsafe-inline' https://unpkg.com`
  and `font-src 'self' data:` — Google Fonts origins are currently blocked.
- Tests run with Vitest (`npm run test` → `vitest run`, jsdom,
  `src/**/*.test.js`).

## Goals / Non-Goals

**Goals:**

- Reproduce the aeropulse dark marketing identity (palette, type, glow, cards,
  sticky nav, section rhythm) on the WebODM landing page without altering
  authenticated app pages or global `frappe-ui` component styling.
- Ship an interactive hero flight simulation as a Vue component whose mechanics
  match aeropulse's animation but whose content is WebODM drone mapping.
- Keep the port maintainable: canvas logic separated from Vue presentation, and
  covered by unit tests where it can be tested without a real canvas.

**Non-Goals:**

- No redesign of `About.vue`, `Contact.vue`, `Login.vue`, or any authenticated
  page; they may keep the existing theme.
- No new runtime dependency, no backend/API change, and no change to pricing or
  product copy beyond section presentation.
- Not deleting `SurveyFlight.vue` / `useSurveyFlight.js` (kept unused by
  decision).
- Not adding Framer Motion or porting React components wholesale; the animation
  is reimplemented against the existing Vue/Tailwind stack.

## Decisions

### D1: Split simulation into a composable plus a presentational SFC

Add `src/composables/useFlightSim.js` (canvas loop, drone state, waypoint
physics, telemetry) and `src/components/HeroFlightSim.vue` (canvas element,
overlay HUD, control buttons, detail panel). This mirrors the existing
`SurveyFlight.vue` + `useSurveyFlight.js` pair, keeps the simulation free of Vue
reactivity in the hot render loop (state mutated in a plain object, published to
the component on a throttled interval), and lets tests exercise the physics
without a canvas.

Alternative considered: a single self-contained `.vue` file. Rejected because
canvas logic inside a reactive component makes the loop prone to re-render
flicker and harder to unit test.

### D2: Scope the theme to the landing page, don't retune global tokens

The landing will use explicit `slate-900/950`, `cyan`, `sky`, and `blue`
utilities rather than remapping `--primary`/`--background`. Global tokens stay
untouched so in-app pages and `frappe-ui` components keep working, and the
landing stays dark even when `useTheme` puts the app in light mode (spec:
"Landing renders dark in light app theme").

Alternative considered: extend the global CSS variables with an aeropulse theme.
Rejected because `index.css` documents a deliberate single-accent constraint and
global remapping would repaint the whole authenticated app.

### D3: Additive Tailwind config for fonts and effects

`tailwind.config.js` gains `fontFamily.display` (Space Grotesk) and `fontFamily.mono`
(JetBrains Mono), with `sans` set to Plus Jakarta Sans, plus the keyframes used by
the identity (`radar-sweep`, `scan-line`, `pulse-ring`). Additions are only
visible where the landing uses them. The theme's global keyframes/utilities are
kept in this config rather than `index.css` so existing pages are unaffected.

### D4: Load the identity fonts via Google Fonts and open CSP deliberately

`index.html` gets `preconnect` hints plus the Space Grotesk / Plus Jakarta Sans /
JetBrains Mono stylesheet, matching aeropulse. Because the deployed CSP blocks
those origins, both Caddy server blocks in `infra/caddy/Caddyfile` must add
`https://fonts.googleapis.com` to `style-src` and `https://fonts.gstatic.com` to
`font-src`. Spec requires graceful fallback, so if fonts are blocked or the host
is unreachable the page still renders with system fonts.

Alternative considered: self-host the webfonts in the SPA for full offline/air-gap
support. Deferred — it adds several font binaries and a bundling step; the
fallback behavior makes Google Fonts safe to ship now. If offline fidelity becomes
a requirement, switch to self-hosting without touching the page markup.

### D5: Retarget simulation content to drone mapping

Keep aeropulse's mechanics (waypoint circuit, click-to-route, pause/resume/reset,
layer switching, live telemetry, click-to-inspect detail panel) and replace its
content: a survey block with numbered survey waypoints, layers for Optical
orthophoto / Thermal / Point-cloud (DSM), and areas of interest expressed as
mapping deliverables (for example stockpile volume, building footprint, vegetation
encroachment, water body) with a confidence/metric readout. No AeroPulse branding
or energy-grid labels remain.

### D6: Resource lifecycle and reduced motion

The composable starts a `requestAnimationFrame` loop only while the hero is
intersecting (`IntersectionObserver`) and the document is visible
(`visibilitychange`), cancels the frame on teardown, and scales the backing store
by `devicePixelRatio` on resize (`ResizeObserver`). When
`prefers-reduced-motion: reduce` matches, it renders a static frame and does not
autoplay; the same listener re-evaluates if the preference changes. This preserves
the discipline of the existing `SurveyFlight` implementation.

### D7: Accessibility of a canvas simulation

Canvas is `role="img"` with an `aria-label` describing the mission; all HUD/controls
are HTML overlays, with real `<button>` elements carrying accessible names and the
detail panel rendered as text. The page is fully usable with the canvas ignored.

### D8: Testing strategy

Vitest tests target the composable (waypoint advance/wrap, pause state, layer
selection, coordinate mapping, reduced-motion branch) and the SFC's control
wiring with a stubbed canvas context, following the existing pattern in
`useMeasure.volume.test.js` (stub the browser surface, test real logic). No new
test dependency.

### D9: Keep the old animation files

`SurveyFlight.vue` and `useSurveyFlight.js` remain in the tree but are no longer
imported by the landing. Removing the import is the only coupling change; no
other page references them.

## Risks / Trade-offs

- **CSP blocks fonts in production if the Caddyfile is forgotten** → D4 makes the
  CSP edit an explicit task; the spec's graceful-fallback scenario covers the
  interim, and verification checks the rendered font in the deployed view.
- **Google Fonts unavailable on-prem/air-gapped** → system-font fallback keeps the
  page usable; self-hosting is the documented follow-up.
- **Dark landing inside a light app shell** → the landing root paints the full
  viewport (`min-h-screen bg-slate-950`), so no light chrome leaks through; only
  the landing opts out, other public pages are untouched.
- **Canvas simulation can look heavy or distract** → it pauses offscreen/on hidden
  tabs and under reduced motion, controls are explicit, and it is non-essential to
  the page.
- **Porting React state to Vue can drift from the reference** → mechanics are
  enumerated in the spec as observable scenarios; content is intentionally
  retargeted, so visual parity with aeropulse's grid scene is not a goal, only
  interaction parity.
- **Dead code retained** (`SurveyFlight`/`useSurveyFlight`) → accepted by explicit
  decision; noted in tasks so future clean-up is a known, separate action.

## Migration Plan

1. Land theme changes (Tailwind config, `index.html`, Caddyfile CSP) and the
   refactored `Landing.vue`, then the new hero component, in one change set.
2. Verify locally on the Vite dev server (`npm run dev`, port 8081) and with
   `npm run test`; verify the deployed build renders fonts under the updated CSP.
3. Rollback is a single revert: the landing is one page and the old
   `SurveyFlight` component stays in the tree, so reverting the hero is a matter of
   restoring the import.

## Open Questions

- None blocking. Final wording/metrics inside the simulation's areas of interest
  are presentation details that do not change the specs, approach, or task
  breakdown.
