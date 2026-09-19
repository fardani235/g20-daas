## Why

The public landing page (the first thing every visitor sees at `/`) still uses the
generic light/dark app theme and a muted blue survey animation that does not match
the product's "drone data as a service" pitch. The `aeropulse` project at
`~/workspaces/aeropulse` already has a sharper, dark, cyan-accented marketing
identity and a compelling interactive drone simulation, so this change ports that
theme and that hero animation onto the WebODM landing page.

## What Changes

- Reskin the whole landing page (`Landing.vue`) to the aeropulse theme: dark
  slate-950 base, cyan/sky/blue accent, gradient headlines, ambient glows, mono
  eyebrow labels and telemetry styling, rounded-3xl bordered cards, and a sticky
  translucent nav. G20 Tech / WebODM copy and section content (pipeline,
  capabilities, testimonials, pricing, final CTA, footer) are kept.
- Add the aeropulse display type system to the SPA: Space Grotesk (display),
  Plus Jakarta Sans (body), JetBrains Mono (technical) via `index.html` font
  links plus Tailwind `fontFamily` tokens, and port the theme's keyframe utilities
  (radar sweep, scan line, pulse ring).
- **BREAKING (visual only, landing page):** the landing page becomes permanently
  dark and ignores the app's light/dark toggle. In-app pages keep the existing
  theme behavior.
- Replace the hero animation: remove the `SurveyFlight` usage from the hero and
  add a Vue port of aeropulse's interactive canvas drone flight simulation, with
  content retargeted to the WebODM drone-mapping domain (survey waypoints,
  orthophoto/elevation/point-cloud layers, telemetry HUD) instead of energy-grid
  inspection. The simulation keeps waypoint routing, click-to-route, sensor/layer
  switching, pause/resume/reset, a live telemetry readout, and a detection detail
  panel.
- Keep `SurveyFlight.vue` and `composables/useSurveyFlight.js` in the repository
  unused (explicit decision), so the old animation remains available for reference.
- Respect accessibility: the animation is labeled and non-essential (the page
  reads without it), canvas controls are real buttons, and reduced-motion
  preference stops autoplay animation.

## Capabilities

### New Capabilities

- `landing-page`: the public marketing landing page's theme, sections, hero
  simulation behavior, interactions, and accessibility/reduced-motion contract.

### Modified Capabilities

- (none — no existing spec's requirements change; this is a new public-facing
  capability)

## Impact

- **`webodm_frontend`**: `src/pages/Landing.vue` (full rewrite of markup/styles,
  same route and copy), new hero simulation component under `src/components/`,
  `index.html` (font preconnect/links), `tailwind.config.js` (font families and
  keyframe animations), `src/index.css` (theme utility classes if not tailwind-native).
  `SurveyFlight.vue` / `useSurveyFlight.js` become unused but are retained.
- **Assets/build**: three Google Fonts families loaded from `fonts.googleapis.com`;
  no new npm dependency (the canvas simulation is plain JS, `lucide-vue-next` is
  already present, and aeropulse's `motion` library is not needed for the canvas).
- **No backend, API, or DocType changes.** No route changes. No change to any
  authenticated page.
