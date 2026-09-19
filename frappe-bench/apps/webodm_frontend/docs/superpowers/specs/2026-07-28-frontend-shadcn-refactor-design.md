# Frontend Refactor: shadcn-vue + Tabbed Navigation — Design

**Date:** 2026-07-28
**Status:** Approved (design)
**Repo:** `webodm_frontend`
**Scope:** Subsystem A — UI framework migration, tabbed navigation, visual system, Landing redesign

## Context & Motivation

The current UI is judged not good enough visually, and frappe-ui is the wrong
foundation for the look we want. Investigation of the existing code found the
migration is smaller than it appears:

- The app is **already ~90% Tailwind**. `tailwind.config.js`, `postcss.config.js`,
  `@tailwindcss/forms`, `@tailwindcss/typography`, and lucide icons
  (`@iconify-json/lucide`, `unplugin-icons`) are already installed.
- `AppLayout.vue` (the sidebar nav) is hand-written Tailwind, not a frappe-ui component.
- frappe-ui's actual surface is **6 primitives plus 2 app-level wrappers**:

  | Component | Usages |
  |---|---|
  | `FeatherIcon` | 13 |
  | `Button` | 9 |
  | `Badge` | 5 |
  | `toast` | 4 |
  | `FormControl` | 2 |
  | `Alert` | 1 |
  | `FrappeUI` (main.js) / `FrappeUIProvider` (App.vue) | 1 each |

So this is a **focused primitive swap + restyle + navigation change**, not a rebuild.

### Scope decomposition (IMPORTANT)

Four subsystems were raised. Only **A** is in scope here; each other gets its own
spec → plan → build cycle:

| # | Subsystem | Status |
|---|---|---|
| **A** | shadcn-vue migration, tabbed nav, visual system, Landing redesign | **THIS SPEC** |
| **B** | Leaflet → **MapLibre** (MapView engine, `useMeasure`, `mapLayers.js`, `flightPath.js`, `@vue-leaflet/vue-leaflet`, tile/overlay wiring) | Next cycle |
| **C** | three.js → **CesiumJS** (`ModelView.vue`, Cesium asset/token setup) | After B |
| **D** | **deck.gl** analytics | Deferred (user: "future feature") |

B and C are geospatial engine swaps with their own correctness surface
(measurement accuracy, tile alignment, coordinate systems) — deliberately not
bundled with UI polish.

**Consequence for A:** changes to `MapView.vue` are kept **shallow** — chrome,
primitives, and the secondary tab row only. No restructuring of Leaflet internals
that cycle B will replace.

## Decisions (locked)

| # | Decision |
|---|----------|
| 1 | **shadcn-vue** (Radix Vue + CVA, copy-in source under `src/components/ui/`) — not hand-rolled primitives, not a hybrid. |
| 2 | **Top tab bar + secondary tab row** navigation (replaces the sidebar). Secondary row appears only on project-detail routes. |
| 3 | **Clean & professional** visual direction; **blue** accent retained (`blue-600`, already in use). |
| 4 | Landing page gets a **distinct DaaS marketing redesign**; brand stays **"G20 Tech"**. |
| 5 | **Incremental migration**, page-by-page, suite green throughout; frappe-ui removed at a final gate. |

## Section 1 — shadcn-vue Setup & Component Layer

### New dependencies
`radix-vue`, `class-variance-authority`, `clsx`, `tailwind-merge`,
`lucide-vue-next`.

### Foundation files
- `src/lib/utils.js` — the `cn()` class-merge helper (`clsx` + `tailwind-merge`).
- `src/index.css` — shadcn design-token blocks for `:root` and `.dark`
  (background, foreground, card, primary, muted, border, ring, destructive, radius).
- `tailwind.config.js` — extended to map those CSS variables to Tailwind theme
  keys, plus `darkMode: 'class'`.

### Primitive mapping (frappe-ui → shadcn-vue)
| frappe-ui today | Replacement |
|---|---|
| `Button` | `ui/button` |
| `Badge` | `ui/badge` |
| `FormControl` | `ui/input` + `ui/label` (+ `ui/select` where it is a dropdown) |
| `Alert` | `ui/alert` |
| `toast` | `ui/sonner` — wrapped so the `toast(...)` call signature stays compatible, minimizing call-site churn |
| `FeatherIcon name="x"` | `lucide-vue-next` named component imports |
| — (new) | `ui/tabs` — required by the navigation |
| — (new) | `ui/dropdown-menu` — user/theme menu + mobile nav collapse |
| — (new, optional) | `ui/dialog` — consolidates the existing hand-rolled modals in Projects/Presets/MapView |

**Icon migration note:** `FeatherIcon` is string-name driven (13 usages). Each
name maps to a lucide component import. Mechanical, but touches most pages.

## Section 2 — Tabbed Navigation

`AppLayout.vue` is rewritten: `<aside>` sidebar → top bar.

**Primary bar (authenticated pages):**
- Left: brand (icon + "G20 Tech").
- Tabs: Dashboard, Projects, Presets, Invoices, Settings, Plugins.
- Right: theme toggle + user menu (sign out) in a `ui/dropdown-menu`
  (moved out of the old sidebar footer).

**Routing contract:** tabs are built on `ui/tabs` for styling/a11y, but each tab
navigates via `router-link` and the active tab is **derived from `route.path`**
(same rule as today's `isActive`: exact match or `startsWith(path + '/')`).
Tabs must NOT hold navigation state locally — deep links and refresh must work.

**Secondary tab row:** on `/project/:id` and children
(`/project/:id/task/:taskId/model`, `/project/:id/task/:taskId/console`), a second
row shows **Map / 3D Model / Console** scoped to that project + task. Pages
without sub-sections render no second row.

**Full-bleed exception:** `MapView` keeps primary + secondary bars, but the map
fills the remaining viewport with no page padding (preserves current behavior).

**Layout-free routes:** `Landing`, `Login`, `Onboarding` keep `meta.layout = false`
— no app chrome. Unchanged.

**Responsive:** below `md`, primary tabs collapse into a `ui/dropdown-menu`
button so the bar does not overflow.

## Section 3 — Visual System

Tokens, not per-page ad-hoc classes.

- **Surfaces:** near-white `background`/`card` (light), slate-950/900 (dark).
  Subtle `border` (slate-200 / slate-800) over heavy shadows.
- **Accent:** `primary` = blue-600, `primary-foreground` white. One accent used
  decisively — primary actions, active tab, focus rings.
- **Semantic:** `muted`/`muted-foreground` secondary text; `destructive` for
  deletes; success/warning for task-status badges
  (Pending / Running / Completed / Failed / Canceled).
- **Radius:** `--radius: 0.5rem`.
- **Type:** Inter or system stack, tightened scale, clear hierarchy
  (page title → section label → body → caption).
- **Density:** generous card/table padding, consistent `gap` rhythm.

**Applied to:** page shells (shared `PageHeader`: title + description + actions),
cards, tables/lists (Projects, Presets, Invoices), forms (Settings, Onboarding,
Login), empty states, status badges.

**Dark mode:** the existing `useTheme` composable (light/dark/system cycle) is
preserved. It toggles the `.dark` class that the token blocks key off, so
components follow automatically instead of each page hand-coding `dark:` variants.

## Section 4 — Landing Page Redesign (DaaS Marketing)

Distinct, conversion-oriented; same tokens/primitives; `meta.layout = false`.

**Differentiator:** competitors lead with stock aerial photography and vague
"we fly drones" copy. This product has a real technical asset — a processing
pipeline producing orthophotos, DSM/DTM, point clouds, 3D models, with
measurable outputs (area, distance, volume). The page leads with **the product
doing the work**, not a hero photo.

**Sections:**
1. **Hero** — split layout: value proposition + primary CTA (Get started / Sign in)
   left; a live-feeling product visual (map/orthophoto panel with subtle motion,
   e.g. slow reveal or tile-load shimmer) right. Dark, high-contrast hero with the
   blue accent, popping against the lighter page below.
2. **"From flight to insight" pipeline strip** — horizontal 4-step visual:
   Capture → Process → Measure → Share. The technical-credibility beat.
3. **Capability cards** — Orthophoto, DSM/DTM, 3D model, Point cloud,
   Volume/area measurement, Team workspaces. Icon + tight copy, clean grid.
4. **Outcome band** — concrete figures over adjectives. Use only **defensible
   numbers from the real pipeline** (e.g. the verified 2,539 × 2,444 px
   georeferenced orthophoto output). Any figure that cannot be verified from the
   codebase MUST be left as an explicit `TODO(user)` placeholder in the markup —
   do not invent marketing claims.
5. **Pricing** — keep existing tiers, restyled as clean cards, one highlighted plan.
6. **Final CTA + footer.**

**Motion:** restrained — subtle scroll-reveal and hover lifts only. No parallax.
Must respect `prefers-reduced-motion`.

## Section 5 — Migration Path & Testing

### Build order (incremental, always-green)
1. **Foundation** — deps, tokens, `cn()`, generate `ui/` primitives. No visual change yet.
2. **Shared chrome** — rewrite `AppLayout.vue` (tab nav) + `PageHeader`. App now looks different.
3. **Page-by-page swap** — per page: replace frappe-ui imports with `ui/*` + lucide,
   apply token styles. One page per task, independently reviewable.
   Pages: Dashboard, Projects, Presets, Settings, Invoices, Plugins, Console,
   ModelView, NotFound, Login, Onboarding, MapView (shallow).
4. **Landing redesign** — its own task.
5. **Removal gate** — delete `frappe-ui` from `package.json`; remove `FrappeUI`
   (main.js) and `FrappeUIProvider` (App.vue).
   **Definition of done: a repo-wide grep for `frappe-ui` returns zero hits.**

### Testing
- The existing **43 vitest tests** are mostly pure logic (`format.js`,
  `mapLayers.js`, `flightPath.js`, `presets.js`, `organization.js`,
  `OdmOptionsForm`). None should break — **all 43 must stay green throughout**.
  A passing logic suite is the evidence the refactor was cosmetic.
- **Add:** a test for nav active-tab derivation (route → active primary tab; and
  whether the secondary row renders for project-detail routes). This is real
  logic worth locking down.
- **Do not add** smoke tests for generated shadcn primitives — they are vendored
  third-party source.
- **Per-task verification:** `npx vite build --mode development` (compiles all
  sources), because production `vite build` is currently broken repo-wide.

### Known blocker likely fixed as a side effect
Production `npx vite build` currently fails repo-wide with
`[frappeui-build-config-plugin] indexHtmlPath is required in buildConfig options`
(pre-existing; reproduced on commits predating the multi-tenancy branch).
Removing frappe-ui removes the `frappeui()` vite plugin that raises it.
**The final task MUST verify production `vite build` succeeds** and report the
result either way.

## Out of Scope (explicit)
- Leaflet → MapLibre (subsystem B, next cycle).
- three.js → CesiumJS (subsystem C).
- deck.gl analytics (subsystem D, deferred).
- Any backend (`webodm_core`) change.
- Restructuring MapView's Leaflet internals (replaced in B).
- New product features — this is a visual/structural refactor only.
