# Frontend shadcn-vue + Tabbed Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace frappe-ui with a local shadcn-vue-style component layer, swap the sidebar for tabbed navigation, apply a token-driven visual system, and redesign the Landing page as a DaaS marketing page — with `frappe-ui` fully removed at the end.

**Architecture:** The app is already ~90% hand-written Tailwind, so this is a primitive swap plus a restyle, not a rebuild. We add a copy-in `src/components/ui/` layer (CVA variants + a `cn()` class-merge helper) backed by `radix-vue` for the components where a11y actually matters (dialog, dropdown menu), and plain semantic markup elsewhere. All navigation state is derived from `route.path` by pure functions in `src/lib/nav.js`, which are unit-tested. Pages migrate one task at a time; `frappe-ui` stays installed and working until the final removal gate.

**Tech Stack:** Vue 3.5 (`<script setup>`), vue-router 4, Tailwind CSS 3.4, vitest 2 + jsdom, `radix-vue`, `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-vue-next`, `vue-sonner`.

**Spec:** `docs/superpowers/specs/2026-07-28-frontend-shadcn-refactor-design.md`

---

## Global Constraints

These bind **every** task. Read them before starting any task.

1. **Repo:** `webodm_frontend`. All frontend paths in this plan are relative to
   `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend`
   unless the path starts with `webodm_frontend/`.
2. **All 43 existing vitest tests must stay green in every task.** They are pure
   logic tests (`format.js`, `mapLayers.js`, `flightPath.js`, `presets.js`,
   `organization.js`, `odmCategories.js`, `useOdmOptions`, `OdmOptionsForm`,
   `useMeasure.import`). A passing logic suite is the evidence the refactor was
   cosmetic. New tests added by this plan are additive.
3. **`frappe-ui` stays installed and functional until Task 15.** Do not remove
   the dependency, the `frappeui()` vite plugin, `FrappeUI`, or
   `FrappeUIProvider` before Task 15. Partially-migrated pages must keep working.
4. **Brand name is exactly `G20 Tech`.** Do not rename, reword, or restyle it to
   anything else on any page.
5. **Accent color is blue-600** (`--primary` token). One accent, used
   decisively: primary actions, active tab, focus rings.
6. **`--radius: 0.5rem`.**
7. **Do not invent marketing claims.** On the Landing page, any figure that
   cannot be verified from this codebase MUST be written as a literal
   `TODO(user)` placeholder in the markup. The one verifiable figure is the
   georeferenced orthophoto output size **2,539 × 2,444 px**.
8. **Restrained motion only** — subtle scroll-reveal and hover lifts. No
   parallax. Anything animated must be disabled under
   `@media (prefers-reduced-motion: reduce)`.
9. **MapView is touched shallowly only** — chrome, primitives, and the secondary
   tab row. Do NOT restructure Leaflet internals, `useMeasure`, `mapLayers.js`,
   or `flightPath.js`: subsystem B replaces them with MapLibre.
10. **No backend changes.** Do not touch `webodm_core`.
11. **Do not add smoke tests for the `ui/` primitives** — they are vendored
    third-party-style source. Test the logic modules (`nav.js`, `status.js`,
    `utils.js`) instead.
12. **Layout-free routes stay layout-free:** `Landing`, `Login`, `Onboarding`
    keep `meta.layout = false` (no app chrome).

### Verification recipe (use this exact sequence in every task)

Production `vite build` is **broken repo-wide** on a pre-existing frappe-ui
plugin bug (verified failing on commits predating this work), so tasks verify
with a development-mode build, which compiles all sources:

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

**CRITICAL — the build clobbers tracked files.** `vite build` writes to
`../webodm_frontend/public/frontend`, which is **committed to git**, and the
`frappeui()` plugin **overrides `--outDir`**, so you cannot redirect it. After
every build, restore those assets before staging anything:

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short   # must NOT list anything under webodm_frontend/public/frontend
```

Never `git add -A` / `git add .` in this repo. Stage explicit paths only.

### Verified environment facts (do not re-litigate these)

- Vue **suppresses attribute fallthrough for `class` when `class` is declared as
  a prop** — verified empirically in this repo's vitest setup: a component with
  `defineProps({ class: String })` rendering `:class="['base', props.class]"`
  produces `"base extra"`, not a duplicate. The `ui/` primitives rely on this.
- Registry versions confirmed available: `radix-vue@1.9.17`,
  `class-variance-authority@0.7.1`, `clsx@2.1.1`, `tailwind-merge@2.6.1`
  (v2 line — pin `^2`, since v3 targets Tailwind v4 and this repo is Tailwind
  3.4), `lucide-vue-next@1.0.0`, `vue-sonner@2.0.9`.
- `vue-sonner@2`'s `nuxt`/`@nuxt/kit`/`@nuxt/schema` peer deps are all marked
  **optional** — a plain `npm install` is clean, no Nuxt required.
- `radix-vue@1.9.17` exports used by this plan all exist and are named exactly:
  `DialogRoot`, `DialogTrigger`, `DialogPortal`, `DialogOverlay`,
  `DialogContent`, `DialogTitle`, `DialogDescription`, `DialogClose`,
  `DropdownMenuRoot`, `DropdownMenuTrigger`, `DropdownMenuPortal`,
  `DropdownMenuContent`, `DropdownMenuItem`, `DropdownMenuSeparator`,
  `DropdownMenuLabel`, `Label`, `useForwardProps`, `VisuallyHidden`.
  Note it is `Label`, **not** `LabelRoot`.
- `lucide-vue-next@1.0.0` export names used by this plan all exist and are named
  exactly: `ArrowRight`, `Box`, `Boxes`, `Building2`, `Calendar`, `Camera`,
  `Check`, `CheckCircle`, `ChevronDown`, `CircleCheck`, `CircleX`, `Clock`,
  `CloudUpload`, `Cpu`, `Download`, `FileText`, `Folder`, `Layers`,
  `LayoutGrid`, `Loader`, `LoaderCircle`, `LogOut`, `Map`, `MapPin`,
  `MapPinOff`, `Maximize`, `Menu`, `Minus`, `Monitor`, `Moon`,
  `MoreVertical`, `Mountain`, `Pencil`, `Play`, `Plus`, `Puzzle`, `RefreshCw`,
  `Ruler`, `Settings`, `Share2`, `SlidersHorizontal`, `Square`, `Sun`,
  `Terminal`, `Ticket`, `TriangleAlert`, `Trash2`, `Users`, `X`.
  **`MapOff` does NOT exist** — use `MapPinOff` (this matters in Task 8,
  `NotFound.vue`, which currently uses `FeatherIcon name="map-off"`).
- `frappe-ui/tailwind`'s preset is what currently supplies
  `@tailwindcss/forms` and `@tailwindcss/typography`. Dropping the preset
  without re-registering them locally silently unstyles every raw `<input>`
  and `<select>` in the app. Task 1 re-registers them.
- `unplugin-icons` + `@iconify-json/lucide` are installed but **completely
  unused** (no `~icons/` import anywhere). `components.d.ts` and
  `auto-imports.d.ts` are stale generated leftovers.
- `src/pages/Console.vue:44` contains a typo'd class `text-text-gray-500` that
  has never resolved to anything. Fix it to `text-muted-foreground` when you
  migrate that file (Task 12).
- `useTheme` (`src/composables/useTheme.js`) sets **both** the `.dark` class and
  `data-theme="dark"` on `<html>`. Keep `darkMode: 'class'` in the Tailwind
  config so `.dark` is the selector our tokens key off. Do not modify
  `useTheme.js`.

### Spec deviations — FLAGGED, confirm before Task 4

Three places where following the spec literally would produce worse code. Each
is called out where it applies; the controller must confirm these with the user
before Task 4 runs.

- **D1 — Navigation uses semantic `<nav>` + links, not `ui/tabs`.** The spec
  says the tab bar is "built on `ui/tabs` for styling/a11y". But ARIA's tabs
  pattern implies tab *panels* whose visibility the widget controls, and the
  spec's own stronger constraint is that tabs must NOT hold navigation state.
  Route-driven navigation styled as tabs is correctly marked up as
  `<nav>` + `<router-link aria-current="page">`. Using `radix-vue`'s
  `TabsRoot`/`TabsTrigger` here would fight the router and produce *worse*
  a11y, not better. So: **no `ui/tabs` component is created.** The visual
  result (a tab bar) is unchanged.
- **D2 — `ui/select` wraps the native `<select>`,** not `radix-vue`'s Select.
  Radix Select portals a floating listbox, which fights the `z-[10000]`
  fixed-position modals this app uses (Presets, MapView upload). A styled
  native `<select>` is robust there, keyboard-accessible for free, and is what
  the app already uses successfully.
- **D3 — Font is the system stack,** not Inter. The spec allows "Inter or
  system stack". frappe-ui was supplying `InterVar`; adding Inter would mean a
  new webfont network dependency. We set Tailwind's `fontFamily.sans` to the
  system stack already present in `src/index.css`.

---

## File Structure

**New — foundation**
| File | Responsibility |
|---|---|
| `src/lib/utils.js` | `cn()` class-merge helper (clsx + tailwind-merge). Nothing else. |
| `src/lib/nav.js` | Pure navigation model: `PRIMARY_TABS`, `activePrimaryTab`, `secondaryTabs`, `activeSecondaryTab`. The single source of truth for nav structure. |
| `src/lib/status.js` | `statusVariant()` — maps a project/task status string to a Badge variant. Replaces `statusTheme`/`statusBadge` duplicated across 5 pages. |
| `src/lib/toast.js` | Re-exports `vue-sonner`'s `toast` so call sites keep the `toast.success(...)` / `toast.error(...)` signature they already use. |

**New — component layer**
| File | Responsibility |
|---|---|
| `src/components/ui/button/Button.vue` | Button: CVA variants + `loading` state. |
| `src/components/ui/badge/Badge.vue` | Status/label badge, CVA variants. |
| `src/components/ui/input/Input.vue` | Text/number/password input, `v-model`. |
| `src/components/ui/textarea/Textarea.vue` | Multiline input, `v-model`. |
| `src/components/ui/select/Select.vue` | Styled native `<select>`, `v-model` (see D2). |
| `src/components/ui/label/Label.vue` | Form label (radix `Label`). |
| `src/components/ui/alert/Alert.vue` | Inline alert, CVA variants. |
| `src/components/ui/dialog/Dialog.vue` | Modal built on radix Dialog: focus trap, Esc, overlay. Replaces the hand-rolled `fixed inset-0` modals. |
| `src/components/ui/dropdown-menu/DropdownMenu.vue` | Menu built on radix DropdownMenu. User menu + mobile nav. |
| `src/components/ui/dropdown-menu/DropdownMenuItem.vue` | One menu item. |
| `src/components/ui/index.js` | Barrel re-export so pages do one import. |
| `src/components/PageHeader.vue` | Shared page shell header: title + description + actions slot. |

**Modified**
| File | Change |
|---|---|
| `package.json` | +5 deps (Task 1); −`frappe-ui`, −`unplugin-icons`, −`@iconify-json/lucide` (Task 15). |
| `tailwind.config.js` | Drop frappe-ui preset, map CSS-variable tokens, re-register forms/typography plugins. |
| `src/index.css` | shadcn token blocks for `:root` / `.dark`; reduced-motion guard. |
| `vite.config.js` | Task 15: drop `frappeui()` + `Icons()` plugins and the `feather-icons`/`frappe-ui` optimizeDeps entries. |
| `src/main.js` | Task 3: mount vue-sonner CSS. Task 13: add `fullBleed` route meta. Task 15: drop `FrappeUI`. |
| `src/App.vue` | Task 3: mount `<Toaster />`. Task 15: drop `FrappeUIProvider`. |
| `src/components/AppLayout.vue` | Task 6: sidebar → top tab bar + secondary tab row. |
| 13 files in `src/pages/` | Tasks 7–13: primitive swap + token restyle. |

**Deleted (Task 15):** `components.d.ts`, `auto-imports.d.ts`.

**Test files**
| File | Covers |
|---|---|
| `src/lib/utils.test.js` | `cn()` merge/conflict/conditional behavior. |
| `src/lib/nav.test.js` | Active-tab derivation, project-detail prefix matching, secondary-row construction. |
| `src/lib/status.test.js` | Status → variant mapping incl. both `Cancelled`/`Canceled` spellings. |

---

## Task 1: Foundation — dependencies, tokens, `cn()`

Installs the toolchain and the design tokens. **No visual change to any page
yet** — frappe-ui still renders everything.

**Files:**
- Modify: `package.json`
- Modify: `tailwind.config.js`
- Modify: `src/index.css`
- Create: `src/lib/utils.js`
- Test: `src/lib/utils.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces: `cn(...inputs) -> string` from `src/lib/utils.js`; Tailwind theme
  keys `background`, `foreground`, `card`, `card-foreground`, `popover`,
  `popover-foreground`, `primary`, `primary-foreground`, `secondary`,
  `secondary-foreground`, `muted`, `muted-foreground`, `accent`,
  `accent-foreground`, `destructive`, `destructive-foreground`, `success`,
  `success-foreground`, `warning`, `warning-foreground`, `border`, `input`,
  `ring`, and radius keys `rounded-lg`/`rounded-md`/`rounded-sm`.

- [ ] **Step 1: Install the dependencies**

Pin `tailwind-merge` to the v2 line — v3 targets Tailwind v4 and this repo is
Tailwind 3.4.

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npm install radix-vue@^1.9.17 class-variance-authority@^0.7.1 clsx@^2.1.1 "tailwind-merge@^2.6.1" lucide-vue-next@^1.0.0
```

Expected: `added N packages`, exit 0. Do NOT remove `frappe-ui` (Global
Constraint 3).

- [ ] **Step 2: Write the failing test for `cn()`**

Create `src/lib/utils.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { cn } from './utils'

describe('cn', () => {
  it('joins plain class strings', () => {
    expect(cn('a', 'b')).toBe('a b')
  })

  it('drops falsy values', () => {
    expect(cn('a', false && 'b', null, undefined, 'c')).toBe('a c')
  })

  it('supports conditional object syntax', () => {
    expect(cn('a', { b: true, c: false })).toBe('a b')
  })

  it('lets a later Tailwind class win over an earlier conflicting one', () => {
    // This is the whole reason tailwind-merge exists: without it the result
    // would be "px-2 px-4" and the winner would depend on CSS source order.
    expect(cn('px-2', 'px-4')).toBe('px-4')
  })

  it('keeps non-conflicting Tailwind classes', () => {
    expect(cn('px-2', 'py-4')).toBe('px-2 py-4')
  })

  it('flattens arrays', () => {
    expect(cn(['a', 'b'], 'c')).toBe('a b c')
  })
})
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
npx vitest run src/lib/utils.test.js
```

Expected: FAIL — `Failed to resolve import "./utils"` (the module does not
exist yet).

- [ ] **Step 4: Create `src/lib/utils.js`**

```js
import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge class lists, letting later Tailwind utilities override earlier
 * conflicting ones. Every ui/ primitive routes its classes through this so a
 * caller's `class` prop can override the component's own defaults.
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
npx vitest run src/lib/utils.test.js
```

Expected: PASS, 6 tests.

- [ ] **Step 6: Add the design tokens to `src/index.css`**

Replace the whole existing `:root` / `.dark` / `body` block (current lines
5–24) with the token blocks below. Keep the `@tailwind` directives at the top
and the `.page-enter-*` / `.measure-close-target` rules at the bottom exactly
as they are.

Values are HSL triplets (no `hsl()` wrapper) so Tailwind can apply opacity
modifiers like `bg-primary/50`.

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%;
    --foreground: 222 47% 11%;

    --card: 0 0% 100%;
    --card-foreground: 222 47% 11%;

    --popover: 0 0% 100%;
    --popover-foreground: 222 47% 11%;

    /* blue-600 — the single accent (Global Constraint 5) */
    --primary: 221 83% 53%;
    --primary-foreground: 0 0% 100%;

    --secondary: 210 40% 96%;
    --secondary-foreground: 222 47% 11%;

    --muted: 210 40% 96%;
    --muted-foreground: 215 16% 47%;

    --accent: 210 40% 96%;
    --accent-foreground: 222 47% 11%;

    --destructive: 0 72% 51%;
    --destructive-foreground: 0 0% 100%;

    --success: 142 71% 45%;
    --success-foreground: 0 0% 100%;

    --warning: 32 95% 44%;
    --warning-foreground: 0 0% 100%;

    --border: 214 32% 91%;
    --input: 214 32% 91%;
    --ring: 221 83% 53%;

    --radius: 0.5rem;
  }

  .dark {
    --background: 222 47% 7%;
    --foreground: 210 40% 96%;

    --card: 222 47% 11%;
    --card-foreground: 210 40% 96%;

    --popover: 222 47% 11%;
    --popover-foreground: 210 40% 96%;

    --primary: 217 91% 60%;
    --primary-foreground: 222 47% 11%;

    --secondary: 217 33% 17%;
    --secondary-foreground: 210 40% 96%;

    --muted: 217 33% 17%;
    --muted-foreground: 215 20% 65%;

    --accent: 217 33% 17%;
    --accent-foreground: 210 40% 96%;

    --destructive: 0 63% 51%;
    --destructive-foreground: 0 0% 100%;

    --success: 142 69% 42%;
    --success-foreground: 0 0% 100%;

    --warning: 32 90% 48%;
    --warning-foreground: 222 47% 11%;

    --border: 217 33% 20%;
    --input: 217 33% 20%;
    --ring: 217 91% 60%;
  }

  body {
    @apply bg-background text-foreground antialiased;
    margin: 0;
  }
}

.page-enter-active,
.page-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.page-enter-from {
  opacity: 0;
  transform: translateY(12px);
}

.page-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

/* The first vertex of a measurement is the click-to-close target. */
.measure-close-target {
  cursor: pointer;
}

/* Global Constraint 8: everything animated must yield to this. */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

- [ ] **Step 7: Rewrite `tailwind.config.js`**

Drop the frappe-ui preset and map the tokens. **Re-register `forms` and
`typography` explicitly** — the frappe-ui preset was the only thing supplying
them, and without them every raw `<input>`/`<select>`/checkbox in the app loses
its styling.

`darkMode: 'class'` matches the `.dark` class that `useTheme` toggles.
`fontFamily.sans` is the system stack (deviation D3).

```js
import forms from '@tailwindcss/forms'
import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        background: 'hsl(var(--background) / <alpha-value>)',
        foreground: 'hsl(var(--foreground) / <alpha-value>)',
        border: 'hsl(var(--border) / <alpha-value>)',
        input: 'hsl(var(--input) / <alpha-value>)',
        ring: 'hsl(var(--ring) / <alpha-value>)',
        card: {
          DEFAULT: 'hsl(var(--card) / <alpha-value>)',
          foreground: 'hsl(var(--card-foreground) / <alpha-value>)',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover) / <alpha-value>)',
          foreground: 'hsl(var(--popover-foreground) / <alpha-value>)',
        },
        primary: {
          DEFAULT: 'hsl(var(--primary) / <alpha-value>)',
          foreground: 'hsl(var(--primary-foreground) / <alpha-value>)',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary) / <alpha-value>)',
          foreground: 'hsl(var(--secondary-foreground) / <alpha-value>)',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted) / <alpha-value>)',
          foreground: 'hsl(var(--muted-foreground) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent) / <alpha-value>)',
          foreground: 'hsl(var(--accent-foreground) / <alpha-value>)',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive) / <alpha-value>)',
          foreground: 'hsl(var(--destructive-foreground) / <alpha-value>)',
        },
        success: {
          DEFAULT: 'hsl(var(--success) / <alpha-value>)',
          foreground: 'hsl(var(--success-foreground) / <alpha-value>)',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning) / <alpha-value>)',
          foreground: 'hsl(var(--warning-foreground) / <alpha-value>)',
        },
      },
      borderRadius: {
        sm: 'calc(var(--radius) - 4px)',
        md: 'calc(var(--radius) - 2px)',
        lg: 'var(--radius)',
        xl: 'calc(var(--radius) + 4px)',
      },
      keyframes: {
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(12px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.3s ease-out both',
        'slide-up': 'slide-up 0.4s ease-out both',
      },
    },
  },
  plugins: [forms, typography],
}
```

- [ ] **Step 8: Verify the whole suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `49 passed` (43 existing + 6 new). Build ends with
`✓ built in Ns`, exit 0.

Pages will look somewhat different already (frappe-ui's preset colors are gone),
and that is expected and fine — they are restyled in Tasks 7–13.

- [ ] **Step 9: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` is listed.

- [ ] **Step 10: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/package.json frontend/package-lock.json \
        frontend/tailwind.config.js frontend/src/index.css \
        frontend/src/lib/utils.js frontend/src/lib/utils.test.js
git commit -m "feat(ui): add shadcn-vue foundation — deps, design tokens, cn() helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Core primitives — Button, Badge, Input, Textarea, Select, Label, Alert

The primitive swap targets. Pure presentational components; per Global
Constraint 11 they get no smoke tests. Correctness is proven by the pages that
consume them compiling and the logic suite staying green.

**Files:**
- Create: `src/components/ui/button/Button.vue`
- Create: `src/components/ui/badge/Badge.vue`
- Create: `src/components/ui/input/Input.vue`
- Create: `src/components/ui/textarea/Textarea.vue`
- Create: `src/components/ui/select/Select.vue`
- Create: `src/components/ui/label/Label.vue`
- Create: `src/components/ui/alert/Alert.vue`
- Create: `src/components/ui/index.js`
- Create: `src/lib/status.js`
- Test: `src/lib/status.test.js`

**Interfaces:**
- Consumes: `cn()` from `src/lib/utils.js` (Task 1); Tailwind token keys from
  Task 1.
- Produces — this is the API every later task codes against:
  - `Button` props: `variant` (`'default' | 'secondary' | 'outline' | 'ghost' |
    'destructive' | 'success'`, default `'default'`), `size` (`'default' | 'sm'
    | 'lg' | 'icon'`, default `'default'`), `loading` (Boolean), `disabled`
    (Boolean), `type` (String, default `'button'`), `class` (String).
    Default slot only — **there is no `#prefix` slot**; put the icon inside the
    default slot before the label.
  - `Badge` props: `variant` (`'default' | 'secondary' | 'success' | 'warning' |
    'destructive' | 'outline'`, default `'secondary'`), `class`.
  - `Input` props: `modelValue` (String|Number), `type` (default `'text'`),
    `class`; emits `update:modelValue`. Supports `v-model` and
    `v-model.number`.
  - `Textarea` props: `modelValue` (String), `rows` (Number, default `3`),
    `class`; emits `update:modelValue`.
  - `Select` props: `modelValue` (any), `class`; emits `update:modelValue`.
    Default slot receives `<option>` elements from the caller.
  - `Label` props: `for` (String), `class`.
  - `Alert` props: `variant` (`'default' | 'destructive' | 'success' |
    'warning'`, default `'default'`), `title` (String), `class`. Default slot =
    body.
  - `statusVariant(status) -> string` from `src/lib/status.js`: a Badge variant
    name for any project/task status.
  - `src/components/ui/index.js` re-exports `Button`, `Badge`, `Input`,
    `Textarea`, `Select`, `Label`, `Alert`.

- [ ] **Step 1: Write the failing test for `statusVariant`**

This replaces `statusTheme`/`statusBadge`, which is currently copy-pasted into
five pages with inconsistent behavior. Note the app uses **both** spellings
`Cancelled` (project) and `Canceled` (task) — both must map to the same
variant, and that inconsistency is exactly why this needs a test.

Create `src/lib/status.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { statusVariant } from './status'

describe('statusVariant', () => {
  it('maps Completed to success', () => {
    expect(statusVariant('Completed')).toBe('success')
  })

  it('maps Failed to destructive', () => {
    expect(statusVariant('Failed')).toBe('destructive')
  })

  it('maps in-flight statuses to default (the accent)', () => {
    expect(statusVariant('Running')).toBe('default')
    expect(statusVariant('In Progress')).toBe('default')
  })

  it('maps waiting statuses to warning', () => {
    expect(statusVariant('Pending')).toBe('warning')
    expect(statusVariant('Queued')).toBe('warning')
  })

  it('maps both spellings of cancelled to secondary', () => {
    // Project status uses "Cancelled", task status uses "Canceled".
    expect(statusVariant('Cancelled')).toBe('secondary')
    expect(statusVariant('Canceled')).toBe('secondary')
  })

  it('maps Planned to outline', () => {
    expect(statusVariant('Planned')).toBe('outline')
  })

  it('is case-insensitive', () => {
    expect(statusVariant('completed')).toBe('success')
    expect(statusVariant('RUNNING')).toBe('default')
  })

  it('falls back to secondary for unknown, empty, and nullish input', () => {
    expect(statusVariant('Wat')).toBe('secondary')
    expect(statusVariant('')).toBe('secondary')
    expect(statusVariant(null)).toBe('secondary')
    expect(statusVariant(undefined)).toBe('secondary')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
npx vitest run src/lib/status.test.js
```

Expected: FAIL — `Failed to resolve import "./status"`.

- [ ] **Step 3: Create `src/lib/status.js`**

```js
const VARIANTS = {
  completed: 'success',
  failed: 'destructive',
  running: 'default',
  'in progress': 'default',
  pending: 'warning',
  queued: 'warning',
  cancelled: 'secondary',
  canceled: 'secondary',
  planned: 'outline',
}

/**
 * Badge variant for a project or task status. Accepts either spelling of
 * "cancelled" because WebODM Project uses "Cancelled" and WebODM Task uses
 * "Canceled". Unknown or missing statuses degrade to a neutral badge.
 */
export function statusVariant(status) {
  return VARIANTS[String(status ?? '').toLowerCase()] || 'secondary'
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
npx vitest run src/lib/status.test.js
```

Expected: PASS, 8 tests.

- [ ] **Step 5: Create `src/components/ui/button/Button.vue`**

```vue
<script setup>
import { computed } from 'vue'
import { cva } from 'class-variance-authority'
import { LoaderCircle } from 'lucide-vue-next'
import { cn } from '@/lib/utils'

// `class` is declared as a prop so Vue stops it from also landing on the root
// via attribute fallthrough; we merge it through cn() instead, which lets a
// caller override our defaults (e.g. class="w-full").
const props = defineProps({
  variant: { type: String, default: 'default' },
  size: { type: String, default: 'default' },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  type: { type: String, default: 'button' },
  class: { type: String, default: '' },
})

const button = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary/90',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
        outline: 'border border-border bg-background hover:bg-accent hover:text-accent-foreground',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
        success: 'bg-success text-success-foreground hover:bg-success/90',
      },
      size: {
        default: 'h-9 px-4 py-2 [&_svg]:size-4',
        sm: 'h-8 rounded-md px-3 text-xs [&_svg]:size-3.5',
        lg: 'h-10 rounded-md px-6 [&_svg]:size-4',
        icon: 'h-9 w-9 [&_svg]:size-4',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

const classes = computed(() =>
  cn(button({ variant: props.variant, size: props.size }), props.class),
)
</script>

<template>
  <button :type="type" :class="classes" :disabled="disabled || loading">
    <LoaderCircle v-if="loading" class="animate-spin" />
    <slot />
  </button>
</template>
```

- [ ] **Step 6: Create `src/components/ui/badge/Badge.vue`**

```vue
<script setup>
import { computed } from 'vue'
import { cva } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const props = defineProps({
  variant: { type: String, default: 'secondary' },
  class: { type: String, default: '' },
})

const badge = cva(
  'inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap',
  {
    variants: {
      variant: {
        default: 'bg-primary/10 text-primary',
        secondary: 'bg-muted text-muted-foreground',
        success: 'bg-success/10 text-success',
        warning: 'bg-warning/10 text-warning',
        destructive: 'bg-destructive/10 text-destructive',
        outline: 'border border-border text-muted-foreground',
      },
    },
    defaultVariants: { variant: 'secondary' },
  },
)

const classes = computed(() => cn(badge({ variant: props.variant }), props.class))
</script>

<template>
  <span :class="classes"><slot /></span>
</template>
```

- [ ] **Step 7: Create `src/components/ui/input/Input.vue`**

```vue
<script setup>
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  type: { type: String, default: 'text' },
  class: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const classes = computed(() =>
  cn(
    'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring disabled:cursor-not-allowed disabled:opacity-50',
    props.class,
  ),
)
</script>

<template>
  <input
    :type="type"
    :value="modelValue"
    :class="classes"
    @input="emit('update:modelValue', $event.target.value)"
  />
</template>
```

- [ ] **Step 8: Create `src/components/ui/textarea/Textarea.vue`**

```vue
<script setup>
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps({
  modelValue: { type: String, default: '' },
  rows: { type: Number, default: 3 },
  class: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const classes = computed(() =>
  cn(
    'flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring disabled:cursor-not-allowed disabled:opacity-50',
    props.class,
  ),
)
</script>

<template>
  <textarea
    :rows="rows"
    :value="modelValue"
    :class="classes"
    @input="emit('update:modelValue', $event.target.value)"
  />
</template>
```

- [ ] **Step 9: Create `src/components/ui/select/Select.vue`**

Native `<select>` per deviation D2 — it survives being rendered inside the
`z-[10000]` fixed-position modals this app uses, which a portalled floating
listbox does not.

```vue
<script setup>
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = defineProps({
  modelValue: { default: null },
  class: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const classes = computed(() =>
  cn(
    'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring disabled:cursor-not-allowed disabled:opacity-50',
    props.class,
  ),
)
</script>

<template>
  <select
    :value="modelValue"
    :class="classes"
    @change="emit('update:modelValue', $event.target.value)"
  >
    <slot />
  </select>
</template>
```

- [ ] **Step 10: Create `src/components/ui/label/Label.vue`**

```vue
<script setup>
import { computed } from 'vue'
import { Label as RadixLabel } from 'radix-vue'
import { cn } from '@/lib/utils'

const props = defineProps({
  for: { type: String, default: undefined },
  class: { type: String, default: '' },
})

const classes = computed(() =>
  cn(
    'text-sm font-medium leading-none text-foreground peer-disabled:cursor-not-allowed peer-disabled:opacity-70',
    props.class,
  ),
)
</script>

<template>
  <RadixLabel :for="props.for" :class="classes"><slot /></RadixLabel>
</template>
```

- [ ] **Step 11: Create `src/components/ui/alert/Alert.vue`**

```vue
<script setup>
import { computed } from 'vue'
import { cva } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const props = defineProps({
  variant: { type: String, default: 'default' },
  title: { type: String, default: '' },
  class: { type: String, default: '' },
})

const alert = cva('w-full rounded-lg border px-4 py-3 text-sm', {
  variants: {
    variant: {
      default: 'border-border bg-muted text-foreground',
      destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
      success: 'border-success/30 bg-success/10 text-success',
      warning: 'border-warning/30 bg-warning/10 text-warning',
    },
  },
  defaultVariants: { variant: 'default' },
})

const classes = computed(() => cn(alert({ variant: props.variant }), props.class))
</script>

<template>
  <div role="alert" :class="classes">
    <p v-if="title" class="font-medium">{{ title }}</p>
    <slot />
  </div>
</template>
```

- [ ] **Step 12: Create `src/components/ui/index.js`**

```js
export { default as Alert } from './alert/Alert.vue'
export { default as Badge } from './badge/Badge.vue'
export { default as Button } from './button/Button.vue'
export { default as Input } from './input/Input.vue'
export { default as Label } from './label/Label.vue'
export { default as Select } from './select/Select.vue'
export { default as Textarea } from './textarea/Textarea.vue'
```

- [ ] **Step 13: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `57 passed` (49 + 8 new). Build exits 0.

Note: nothing imports the new primitives yet, so Vite tree-shakes them out of
the bundle. A green build proves they at least parse. Their real proof comes in
Tasks 7–13.

- [ ] **Step 14: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 15: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/components/ui frontend/src/lib/status.js frontend/src/lib/status.test.js
git commit -m "feat(ui): add core primitives and shared status-variant mapping

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Toast layer — vue-sonner behind a compatible `toast` facade

Four pages already call `toast.success(...)` / `toast.error(...)` from frappe-ui.
Rather than rewrite 12 call sites, we expose the same surface from our own module
so migration is a one-line import change per page.

**Files:**
- Modify: `package.json` (add `vue-sonner`)
- Create: `src/lib/toast.js`
- Modify: `src/App.vue`
- Modify: `src/main.js`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `import { toast } from '@/lib/toast'` with methods
  `toast.success(message)`, `toast.error(message)`, `toast.info(message)`,
  `toast.warning(message)`, and callable `toast(message)` — the same shape
  frappe-ui's `toast` exposed, so call sites need no change beyond the import.

- [ ] **Step 1: Install vue-sonner**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npm install vue-sonner@^2.0.9
```

Its `nuxt` / `@nuxt/kit` / `@nuxt/schema` peers are all declared **optional**, so
this installs clean with no Nuxt.

- [ ] **Step 2: Create `src/lib/toast.js`**

```js
// Single import site for toasts. Re-exported (rather than imported directly in
// pages) so the toast library stays swappable without touching call sites, and
// so the `toast.success(...)` / `toast.error(...)` surface the pages already
// use keeps working unchanged.
import { toast } from 'vue-sonner'

export { toast }
```

- [ ] **Step 3: Mount the toast stylesheet in `src/main.js`**

`vue-sonner` ships its own CSS and will render unstyled without it. Add the
import directly below the existing `import './index.css'` (line 5) so our
tokens load first:

```js
import './index.css'
import 'vue-sonner/style.css'
```

Change nothing else in `main.js` in this task — `FrappeUI` stays (Global
Constraint 3).

- [ ] **Step 4: Render `<Toaster />` in `src/App.vue`**

Add the import alongside the existing imports:

```js
import { Toaster } from 'vue-sonner'
```

Then place `<Toaster />` inside `<FrappeUIProvider>`, immediately before the
`<AppLayout ... />` line, configured to match our tokens:

```vue
    <Toaster
      position="bottom-right"
      :toast-options="{
        class: 'bg-card text-card-foreground border border-border rounded-lg shadow-lg',
      }"
    />
```

Leave `FrappeUIProvider` in place — it is removed in Task 15.

- [ ] **Step 5: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `57 passed`, build exits 0.

- [ ] **Step 6: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 7: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/package.json frontend/package-lock.json \
        frontend/src/lib/toast.js frontend/src/App.vue frontend/src/main.js
git commit -m "feat(ui): add vue-sonner toasts behind a compatible toast facade

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Overlay primitives — Dialog and DropdownMenu

Four hand-rolled `fixed inset-0` modals exist today (Projects ×3, Presets ×1,
MapView ×1) with no focus trap, no Esc handling, and no scroll lock. Radix
supplies all three. The DropdownMenu is required by the Task 6 navigation.

**Files:**
- Create: `src/components/ui/dialog/Dialog.vue`
- Create: `src/components/ui/dropdown-menu/DropdownMenu.vue`
- Create: `src/components/ui/dropdown-menu/DropdownMenuItem.vue`
- Modify: `src/components/ui/index.js`

**Interfaces:**
- Consumes: `cn()` (Task 1); `src/components/ui/index.js` from Task 2.
- Produces:
  - `Dialog` props: `open` (Boolean, required), `title` (String), `description`
    (String), `class` (String — applied to the content panel, use for width e.g.
    `sm:max-w-lg`); emits `update:open` (so `v-model:open` works). Slots:
    default (body), `footer` (action row).
  - `DropdownMenu` props: `align` (`'start' | 'center' | 'end'`, default
    `'end'`), `class`. Slots: `trigger` (the clickable element), default (menu
    items).
  - `DropdownMenuItem` props: `disabled` (Boolean), `class`; emits `select`.
  - `src/components/ui/index.js` additionally re-exports `Dialog`,
    `DropdownMenu`, `DropdownMenuItem`.

- [ ] **Step 1: Create `src/components/ui/dialog/Dialog.vue`**

Radix gives us the focus trap, Esc-to-close, scroll lock, and
`aria-labelledby`/`aria-describedby` wiring that the hand-rolled modals lack.
A `<VisuallyHidden>` title is rendered when no title is supplied, because Radix
warns (and screen readers suffer) without an accessible name.

```vue
<script setup>
import { computed } from 'vue'
import {
  DialogRoot,
  DialogPortal,
  DialogOverlay,
  DialogContent,
  DialogTitle,
  DialogDescription,
  VisuallyHidden,
} from 'radix-vue'
import { cn } from '@/lib/utils'

const props = defineProps({
  open: { type: Boolean, required: true },
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  class: { type: String, default: '' },
})
const emit = defineEmits(['update:open'])

const contentClasses = computed(() =>
  cn(
    'fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-card p-6 text-card-foreground shadow-lg max-h-[85vh] overflow-y-auto focus:outline-none',
    props.class,
  ),
)
</script>

<template>
  <DialogRoot :open="open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-50 bg-black/50" />
      <DialogContent :class="contentClasses">
        <DialogTitle v-if="title" class="text-lg font-semibold text-foreground">
          {{ title }}
        </DialogTitle>
        <VisuallyHidden v-else>
          <DialogTitle>Dialog</DialogTitle>
        </VisuallyHidden>
        <DialogDescription v-if="description" class="mt-1 text-sm text-muted-foreground">
          {{ description }}
        </DialogDescription>
        <div :class="title || description ? 'mt-4' : ''">
          <slot />
        </div>
        <div v-if="$slots.footer" class="mt-6 flex justify-end gap-2">
          <slot name="footer" />
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
```

- [ ] **Step 2: Create `src/components/ui/dropdown-menu/DropdownMenu.vue`**

```vue
<script setup>
import { computed } from 'vue'
import {
  DropdownMenuRoot,
  DropdownMenuTrigger,
  DropdownMenuPortal,
  DropdownMenuContent,
} from 'radix-vue'
import { cn } from '@/lib/utils'

const props = defineProps({
  align: { type: String, default: 'end' },
  class: { type: String, default: '' },
})

const contentClasses = computed(() =>
  cn(
    'z-50 min-w-[10rem] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md',
    props.class,
  ),
)
</script>

<template>
  <DropdownMenuRoot>
    <DropdownMenuTrigger as-child>
      <slot name="trigger" />
    </DropdownMenuTrigger>
    <DropdownMenuPortal>
      <DropdownMenuContent :align="align" :side-offset="6" :class="contentClasses">
        <slot />
      </DropdownMenuContent>
    </DropdownMenuPortal>
  </DropdownMenuRoot>
</template>
```

- [ ] **Step 3: Create `src/components/ui/dropdown-menu/DropdownMenuItem.vue`**

```vue
<script setup>
import { computed } from 'vue'
import { DropdownMenuItem as RadixItem } from 'radix-vue'
import { cn } from '@/lib/utils'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  class: { type: String, default: '' },
})
const emit = defineEmits(['select'])

const classes = computed(() =>
  cn(
    'relative flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
    props.class,
  ),
)
</script>

<template>
  <RadixItem :disabled="disabled" :class="classes" @select="emit('select', $event)">
    <slot />
  </RadixItem>
</template>
```

- [ ] **Step 4: Extend `src/components/ui/index.js`**

The file must end up with exactly these ten exports, alphabetized:

```js
export { default as Alert } from './alert/Alert.vue'
export { default as Badge } from './badge/Badge.vue'
export { default as Button } from './button/Button.vue'
export { default as Dialog } from './dialog/Dialog.vue'
export { default as DropdownMenu } from './dropdown-menu/DropdownMenu.vue'
export { default as DropdownMenuItem } from './dropdown-menu/DropdownMenuItem.vue'
export { default as Input } from './input/Input.vue'
export { default as Label } from './label/Label.vue'
export { default as Select } from './select/Select.vue'
export { default as Textarea } from './textarea/Textarea.vue'
```

- [ ] **Step 5: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `57 passed`, build exits 0.

- [ ] **Step 6: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 7: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/components/ui
git commit -m "feat(ui): add accessible Dialog and DropdownMenu on radix-vue

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Navigation model — `src/lib/nav.js`

The one piece of genuinely new logic in this refactor, so it is the one piece
that gets real tests. Extracting it keeps `AppLayout.vue` (Task 6) declarative
and makes the route → active-tab rule verifiable without mounting a router.

Per the spec: the active tab is **derived from `route.path`**, never held in
local state, so deep links and refreshes work.

**Files:**
- Create: `src/lib/nav.js`
- Test: `src/lib/nav.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PRIMARY_TABS` — frozen array of `{ to, label, icon }`, `icon` being a
    lucide component. Order: Dashboard, Projects, Presets, Invoices, Settings,
    Plugins.
  - `activePrimaryTab(path) -> string | null` — the `to` of the matching tab, or
    `null`.
  - `secondaryTabs(path) -> Array<{ to, label }>` — the Map / 3D Model / Console
    row for project-detail routes; `[]` elsewhere.
  - `activeSecondaryTab(path) -> string | null` — the `to` of the matching
    secondary tab, or `null`.

- [ ] **Step 1: Write the failing tests**

Create `src/lib/nav.test.js`:

```js
import { describe, it, expect } from 'vitest'
import {
  PRIMARY_TABS,
  activePrimaryTab,
  secondaryTabs,
  activeSecondaryTab,
} from './nav'

describe('PRIMARY_TABS', () => {
  it('lists the six app sections in order', () => {
    expect(PRIMARY_TABS.map(t => t.to)).toEqual([
      '/dashboard',
      '/projects',
      '/presets',
      '/invoices',
      '/settings',
      '/plugins',
    ])
  })

  it('gives every tab a label and an icon', () => {
    for (const tab of PRIMARY_TABS) {
      expect(tab.label).toBeTruthy()
      expect(tab.icon).toBeTruthy()
    }
  })
})

describe('activePrimaryTab', () => {
  it('matches a tab exactly', () => {
    expect(activePrimaryTab('/projects')).toBe('/projects')
    expect(activePrimaryTab('/settings')).toBe('/settings')
  })

  it('matches nested paths under a tab', () => {
    expect(activePrimaryTab('/projects/anything')).toBe('/projects')
  })

  it('keeps Projects active on project-detail routes', () => {
    // A project detail page lives at /project/:id (singular), which is not a
    // prefix of /projects — so it needs an explicit rule, or the tab bar would
    // show nothing active while the user is inside a project.
    expect(activePrimaryTab('/project/abc123')).toBe('/projects')
    expect(activePrimaryTab('/project/abc123/task/t1/model')).toBe('/projects')
    expect(activePrimaryTab('/project/abc123/task/t1/console')).toBe('/projects')
  })

  it('does not treat a longer sibling segment as nested', () => {
    // "/presets-archive" must NOT activate "/presets".
    expect(activePrimaryTab('/presets-archive')).toBe(null)
  })

  it('returns null for routes outside the tab bar', () => {
    expect(activePrimaryTab('/')).toBe(null)
    expect(activePrimaryTab('/login')).toBe(null)
    expect(activePrimaryTab('/onboarding')).toBe(null)
    expect(activePrimaryTab('/nope')).toBe(null)
  })

  it('tolerates trailing slashes', () => {
    expect(activePrimaryTab('/projects/')).toBe('/projects')
  })

  it('handles empty and nullish input without throwing', () => {
    expect(activePrimaryTab('')).toBe(null)
    expect(activePrimaryTab(null)).toBe(null)
    expect(activePrimaryTab(undefined)).toBe(null)
  })
})

describe('secondaryTabs', () => {
  it('is empty on non-project routes', () => {
    expect(secondaryTabs('/dashboard')).toEqual([])
    expect(secondaryTabs('/projects')).toEqual([])
    expect(secondaryTabs('/login')).toEqual([])
    expect(secondaryTabs(null)).toEqual([])
  })

  it('offers only Map on a project route with no task selected', () => {
    // 3D Model and Console are task-scoped; without a task id in the path
    // there is nothing to link them to.
    expect(secondaryTabs('/project/p1')).toEqual([
      { to: '/project/p1', label: 'Map' },
    ])
  })

  it('offers Map, 3D Model, and Console when a task is in the path', () => {
    expect(secondaryTabs('/project/p1/task/t9/model')).toEqual([
      { to: '/project/p1', label: 'Map' },
      { to: '/project/p1/task/t9/model', label: '3D Model' },
      { to: '/project/p1/task/t9/console', label: 'Console' },
    ])
  })

  it('builds the same row from the console route', () => {
    expect(secondaryTabs('/project/p1/task/t9/console')).toEqual([
      { to: '/project/p1', label: 'Map' },
      { to: '/project/p1/task/t9/model', label: '3D Model' },
      { to: '/project/p1/task/t9/console', label: 'Console' },
    ])
  })

  it('preserves encoded ids verbatim', () => {
    // Project names are Frappe doc names and can contain encoded characters;
    // round-tripping them through the tab links must not corrupt them.
    const tabs = secondaryTabs('/project/My%20Site/task/t1/model')
    expect(tabs[0].to).toBe('/project/My%20Site')
    expect(tabs[1].to).toBe('/project/My%20Site/task/t1/model')
  })
})

describe('activeSecondaryTab', () => {
  it('marks Map active on the project root', () => {
    expect(activeSecondaryTab('/project/p1')).toBe('/project/p1')
  })

  it('marks the model tab active on the model route', () => {
    expect(activeSecondaryTab('/project/p1/task/t9/model')).toBe(
      '/project/p1/task/t9/model',
    )
  })

  it('marks the console tab active on the console route', () => {
    expect(activeSecondaryTab('/project/p1/task/t9/console')).toBe(
      '/project/p1/task/t9/console',
    )
  })

  it('returns null where there is no secondary row', () => {
    expect(activeSecondaryTab('/dashboard')).toBe(null)
    expect(activeSecondaryTab(null)).toBe(null)
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
npx vitest run src/lib/nav.test.js
```

Expected: FAIL — `Failed to resolve import "./nav"`.

- [ ] **Step 3: Create `src/lib/nav.js`**

```js
import {
  FileText,
  Folder,
  LayoutGrid,
  Puzzle,
  Settings,
  SlidersHorizontal,
} from 'lucide-vue-next'

/**
 * The primary tab bar. Order is the order shown.
 */
export const PRIMARY_TABS = Object.freeze([
  { to: '/dashboard', label: 'Dashboard', icon: LayoutGrid },
  { to: '/projects', label: 'Projects', icon: Folder },
  { to: '/presets', label: 'Presets', icon: SlidersHorizontal },
  { to: '/invoices', label: 'Invoices', icon: FileText },
  { to: '/settings', label: 'Settings', icon: Settings },
  { to: '/plugins', label: 'Plugins', icon: Puzzle },
])

// A project detail page is /project/:id — singular, so it is not a path prefix
// of the /projects tab. Matching it explicitly keeps Projects highlighted while
// the user is inside a project.
const PROJECT_DETAIL = /^\/project\/([^/]+)/
const PROJECT_TASK = /^\/project\/([^/]+)\/task\/([^/]+)\/(model|console)$/

// Strip a trailing slash so "/projects/" and "/projects" behave the same.
// Returns '' for nullish input so every caller can treat the result as a string.
function normalize(path) {
  const p = String(path ?? '')
  return p.length > 1 && p.endsWith('/') ? p.slice(0, -1) : p
}

/**
 * The `to` of the primary tab that should render as active for a path, or null.
 * Derived from the path on every call — never cached, never stored — so deep
 * links and refreshes highlight correctly.
 */
export function activePrimaryTab(path) {
  const p = normalize(path)
  if (!p) return null
  if (PROJECT_DETAIL.test(p)) return '/projects'
  for (const tab of PRIMARY_TABS) {
    // Exact match, or a genuine child segment — a prefix test alone would let
    // "/presets-archive" activate "/presets".
    if (p === tab.to || p.startsWith(tab.to + '/')) return tab.to
  }
  return null
}

/**
 * The secondary tab row for project-detail routes: [] anywhere else.
 * 3D Model and Console are task-scoped, so they only appear once the path
 * carries a task id.
 */
export function secondaryTabs(path) {
  const p = normalize(path)
  const detail = p.match(PROJECT_DETAIL)
  if (!detail) return []

  const projectId = detail[1]
  const tabs = [{ to: `/project/${projectId}`, label: 'Map' }]

  const task = p.match(PROJECT_TASK)
  if (task) {
    const taskId = task[2]
    tabs.push(
      { to: `/project/${projectId}/task/${taskId}/model`, label: '3D Model' },
      { to: `/project/${projectId}/task/${taskId}/console`, label: 'Console' },
    )
  }
  return tabs
}

/**
 * The `to` of the active secondary tab for a path, or null.
 */
export function activeSecondaryTab(path) {
  const p = normalize(path)
  const tabs = secondaryTabs(p)
  if (!tabs.length) return null
  const hit = tabs.find(t => t.to === p)
  return hit ? hit.to : null
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
npx vitest run src/lib/nav.test.js
```

Expected: PASS, 18 tests.

- [ ] **Step 5: Verify the whole suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed` (57 + 18 new), build exits 0.

- [ ] **Step 6: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 7: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/lib/nav.js frontend/src/lib/nav.test.js
git commit -m "feat(nav): add route-derived navigation model with tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Tabbed navigation — rewrite `AppLayout.vue`, add `PageHeader.vue`

The visible turning point: the sidebar becomes a top tab bar. This is the task
where the app starts looking different.

Per deviation **D1**, navigation is marked up as `<nav>` + `<router-link>` with
`aria-current="page"`, not as ARIA tabs — the links navigate, and ARIA tabs
imply panels this widget does not own.

**Files:**
- Modify: `src/components/AppLayout.vue` (full rewrite)
- Create: `src/components/PageHeader.vue`

**Interfaces:**
- Consumes: `PRIMARY_TABS`, `activePrimaryTab`, `secondaryTabs`,
  `activeSecondaryTab` from `src/lib/nav.js` (Task 5); `Button`,
  `DropdownMenu`, `DropdownMenuItem` from `@/components/ui` (Tasks 2, 4);
  `useTheme` from `src/composables/useTheme` (unchanged).
- Produces: `PageHeader` props `title` (String, required), `description`
  (String); slot `actions` (right-aligned action row). Used by Tasks 7–13.

- [ ] **Step 1: Create `src/components/PageHeader.vue`**

```vue
<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
})
</script>

<template>
  <div class="flex flex-wrap items-start justify-between gap-4">
    <div class="min-w-0">
      <h1 class="text-2xl font-semibold tracking-tight text-foreground">{{ title }}</h1>
      <p v-if="description" class="mt-1 text-sm text-muted-foreground">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="flex flex-shrink-0 items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
```

- [ ] **Step 2: Rewrite `src/components/AppLayout.vue`**

Replace the entire file. Notes on the decisions encoded here:

- The **full-bleed exception**: `MapView` manages its own scrolling and fills
  the viewport, so `<main>` drops its padding and overflow when
  `route.meta.fullBleed` is set. That flag is added to the route in Task 13; the
  computed reads it defensively so this task works before then.
- Below `md`, primary tabs collapse into a `DropdownMenu` so the bar cannot
  overflow.
- `aria-current="page"` is what conveys the active tab to assistive tech;
  the styling is decoration on top of it.

```vue
<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { LogOut, Map as MapIcon, Menu, Monitor, Moon, Sun } from 'lucide-vue-next'
import { Button, DropdownMenu, DropdownMenuItem } from '@/components/ui'
import {
  PRIMARY_TABS,
  activePrimaryTab,
  secondaryTabs,
  activeSecondaryTab,
} from '@/lib/nav'
import { useTheme } from '@/composables/useTheme'

const route = useRoute()
const router = useRouter()
const { current, cycle } = useTheme()

// All nav state is derived from the path — never stored — so deep links and
// refreshes highlight the right tab.
const activeTab = computed(() => activePrimaryTab(route.path))
const subTabs = computed(() => secondaryTabs(route.path))
const activeSubTab = computed(() => activeSecondaryTab(route.path))
const activeTabLabel = computed(
  () => PRIMARY_TABS.find(t => t.to === activeTab.value)?.label ?? 'Menu',
)

// MapView fills the viewport and scrolls internally; every other page gets the
// standard padded, scrollable shell.
const fullBleed = computed(() => route.meta?.fullBleed === true)

const themeIcon = computed(() => {
  if (current.value === 'light') return Sun
  if (current.value === 'dark') return Moon
  return Monitor
})
const themeLabel = computed(
  () => current.value.charAt(0).toUpperCase() + current.value.slice(1),
)

async function logout() {
  try {
    await fetch('/api/method/logout', { method: 'POST' })
  } catch {}
  router.push('/')
}
</script>

<template>
  <div class="flex h-screen flex-col bg-background">
    <!-- Primary bar -->
    <header class="flex-shrink-0 border-b border-border bg-card">
      <div class="flex h-14 items-center gap-4 px-4 sm:px-6">
        <router-link to="/dashboard" class="flex flex-shrink-0 items-center gap-2">
          <MapIcon class="size-5 text-primary" />
          <span class="font-semibold tracking-tight text-foreground">G20 Tech</span>
        </router-link>

        <!-- Tabs (md and up) -->
        <nav class="hidden min-w-0 flex-1 items-center gap-1 md:flex" aria-label="Main">
          <router-link
            v-for="tab in PRIMARY_TABS"
            :key="tab.to"
            :to="tab.to"
            :aria-current="activeTab === tab.to ? 'page' : undefined"
            class="relative flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :class="
              activeTab === tab.to
                ? 'text-primary after:absolute after:inset-x-2 after:-bottom-[13px] after:h-0.5 after:rounded-full after:bg-primary'
                : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
            "
          >
            <component :is="tab.icon" class="size-4" />
            {{ tab.label }}
          </router-link>
        </nav>

        <!-- Collapsed tabs (below md) -->
        <div class="flex min-w-0 flex-1 md:hidden">
          <DropdownMenu align="start">
            <template #trigger>
              <Button variant="ghost" size="sm">
                <Menu />
                {{ activeTabLabel }}
              </Button>
            </template>
            <DropdownMenuItem
              v-for="tab in PRIMARY_TABS"
              :key="tab.to"
              @select="router.push(tab.to)"
            >
              <component :is="tab.icon" />
              {{ tab.label }}
            </DropdownMenuItem>
          </DropdownMenu>
        </div>

        <!-- Right side -->
        <div class="flex flex-shrink-0 items-center gap-1">
          <Button variant="ghost" size="icon" :title="`Theme: ${themeLabel}`" @click="cycle">
            <component :is="themeIcon" />
            <span class="sr-only">Toggle theme (currently {{ themeLabel }})</span>
          </Button>
          <DropdownMenu>
            <template #trigger>
              <Button variant="ghost" size="sm">Account</Button>
            </template>
            <DropdownMenuItem @select="logout">
              <LogOut />
              Sign out
            </DropdownMenuItem>
          </DropdownMenu>
        </div>
      </div>

      <!-- Secondary bar: project-detail routes only -->
      <nav
        v-if="subTabs.length"
        class="flex items-center gap-1 border-t border-border px-4 sm:px-6"
        aria-label="Project sections"
      >
        <router-link
          v-for="tab in subTabs"
          :key="tab.to"
          :to="tab.to"
          :aria-current="activeSubTab === tab.to ? 'page' : undefined"
          class="-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :class="
            activeSubTab === tab.to
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          "
        >
          {{ tab.label }}
        </router-link>
      </nav>
    </header>

    <main
      class="flex-1"
      :class="fullBleed ? 'overflow-hidden' : 'overflow-auto p-4 sm:p-6'"
    >
      <router-view v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </router-view>
    </main>
  </div>
</template>
```

- [ ] **Step 3: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed`, build exits 0.

- [ ] **Step 4: Confirm no page title regressed to a duplicate heading**

`AppLayout` no longer renders `route.meta.title` as an `<h1>` — pages own their
own headings via `PageHeader` from Tasks 7–13. Verify the old header is gone:

```bash
grep -n "pageTitle\|meta?.title" src/components/AppLayout.vue
```

Expected: no output.

- [ ] **Step 5: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 6: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/components/AppLayout.vue frontend/src/components/PageHeader.vue
git commit -m "feat(nav): replace sidebar with top tab bar and secondary project row

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Migrate Dashboard

First page migration. Establishes the pattern every later page task follows:
swap frappe-ui imports for `@/components/ui` + lucide, replace ad-hoc
`gray-*`/`dark:` pairs with tokens, adopt `PageHeader` and `statusVariant`.

Six near-identical stat cards are collapsed into a `v-for` over a data array —
the current file repeats the same 12-line block six times with only the icon,
color, and label changing.

**Files:**
- Modify: `src/pages/Dashboard.vue`

**Interfaces:**
- Consumes: `PageHeader` (Task 6); `Badge` from `@/components/ui` (Task 2);
  `statusVariant` from `@/lib/status` (Task 2); lucide icons.
- Produces: nothing for later tasks.

- [ ] **Step 1: Replace the template**

Keep the entire `<script setup>` data-loading logic (`onMounted` fetches,
`stats`, `recentProjects`, `openProject`, `formatDate`) exactly as it is —
only the imports and the `statusTheme` helper change. Replace the `<template>`
block with:

```vue
<template>
  <div class="space-y-6">
    <PageHeader title="Dashboard" description="Processing activity across your organization." />

    <p v-if="loading" class="py-12 text-center text-muted-foreground">Loading stats…</p>

    <Alert v-else-if="error" variant="destructive" :title="error" />

    <template v-else>
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
        <div
          v-for="card in statCards"
          :key="card.label"
          class="rounded-lg border border-border bg-card p-4"
        >
          <div class="flex items-center gap-3">
            <div class="rounded-md bg-muted p-2">
              <component :is="card.icon" class="size-5" :class="card.tone" />
            </div>
            <div class="min-w-0">
              <p class="text-2xl font-semibold tracking-tight text-card-foreground">
                {{ card.value }}
              </p>
              <p class="text-xs text-muted-foreground">{{ card.label }}</p>
            </div>
          </div>
        </div>
      </div>

      <section v-if="recentProjects.length">
        <h2 class="mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground">
          Recent projects
        </h2>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          <button
            v-for="project in recentProjects"
            :key="project.name"
            type="button"
            class="rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-primary/40 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            @click="openProject(project.name)"
          >
            <div class="flex items-center gap-3">
              <div class="rounded-md bg-muted p-1.5">
                <Folder class="size-4 text-muted-foreground" />
              </div>
              <div class="min-w-0 flex-1">
                <p class="truncate text-sm font-medium text-card-foreground">
                  {{ project.title || project.name }}
                </p>
                <p class="text-xs text-muted-foreground">{{ formatDate(project.creation) }}</p>
              </div>
              <Badge :variant="statusVariant(project.status)">{{ project.status }}</Badge>
            </div>
          </button>
        </div>
      </section>
    </template>
  </div>
</template>
```

- [ ] **Step 2: Update the script block**

Replace the frappe-ui import line (`import { Badge, FeatherIcon } from
'frappe-ui'`) with the imports below, delete the local `statusTheme` function,
and add the `statCards` computed. Everything else in `<script setup>` stays.

```js
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  CircleCheck,
  CircleX,
  Clock,
  Folder,
  Layers,
  Loader,
} from 'lucide-vue-next'
import { Alert, Badge } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { statusVariant } from '@/lib/status'
```

Add this computed next to `recentProjects` (it replaces the six copy-pasted
card blocks):

```js
const statCards = computed(() => [
  { label: 'Projects', value: stats.totalProjects, icon: Folder, tone: 'text-primary' },
  { label: 'Total Tasks', value: stats.totalTasks, icon: Layers, tone: 'text-muted-foreground' },
  { label: 'Completed', value: stats.completed, icon: CircleCheck, tone: 'text-success' },
  { label: 'Failed', value: stats.failed, icon: CircleX, tone: 'text-destructive' },
  { label: 'Running', value: stats.running, icon: Loader, tone: 'text-primary' },
  { label: 'Pending', value: stats.pending, icon: Clock, tone: 'text-warning' },
])
```

- [ ] **Step 3: Confirm frappe-ui is gone from this file**

```bash
grep -n "frappe-ui\|FeatherIcon\|statusTheme" src/pages/Dashboard.vue
```

Expected: no output.

- [ ] **Step 4: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed`, build exits 0.

- [ ] **Step 5: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 6: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/Dashboard.vue
git commit -m "refactor(dashboard): migrate to ui primitives and design tokens

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Migrate NotFound, Invoices, and Plugins

Three small, low-risk pages batched together — each is a mechanical primitive
swap with no data logic, and no reviewer would sensibly approve one and reject
another.

**Files:**
- Modify: `src/pages/NotFound.vue`
- Modify: `src/pages/Invoices.vue`
- Modify: `src/pages/Plugins.vue`

**Interfaces:**
- Consumes: `Badge`, `Button` from `@/components/ui`; `PageHeader`;
  `statusVariant` from `@/lib/status`; lucide icons.
- Produces: nothing for later tasks.

- [ ] **Step 1: Rewrite `src/pages/NotFound.vue`**

`FeatherIcon name="map-off"` has no lucide equivalent — **`MapOff` does not
exist**; use `MapPinOff`. Also fix the misleading button label: it routes to
`/` (the Landing page), not the dashboard.

```vue
<script setup>
import { useRouter } from 'vue-router'
import { MapPinOff } from 'lucide-vue-next'
import { Button } from '@/components/ui'

const router = useRouter()
</script>

<template>
  <div class="flex h-full flex-col items-center justify-center p-6 text-center">
    <MapPinOff class="mb-4 size-16 text-muted-foreground/40" />
    <h1 class="mb-2 text-2xl font-semibold tracking-tight text-foreground">Page not found</h1>
    <p class="mb-6 text-muted-foreground">The page you're looking for doesn't exist.</p>
    <Button @click="router.push('/')">Back to home</Button>
  </div>
</template>
```

- [ ] **Step 2: Rewrite `src/pages/Invoices.vue`**

The local `statusClass` helper is replaced by `Badge` + a small variant map —
invoice statuses (`Paid`/`Overdue`/`Draft`) are a different vocabulary from
task statuses, so they do NOT go through `statusVariant`.

```vue
<script setup>
import { ref } from 'vue'
import { Download } from 'lucide-vue-next'
import { Badge, Button } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'

const invoices = ref([
  { id: 'INV-001', date: '2026-07-01', amount: '$49.00', status: 'Paid' },
  { id: 'INV-002', date: '2026-06-01', amount: '$49.00', status: 'Paid' },
  { id: 'INV-003', date: '2026-05-01', amount: '$49.00', status: 'Paid' },
  { id: 'INV-004', date: '2026-08-01', amount: '$49.00', status: 'Overdue' },
])

// Invoice statuses are their own vocabulary, separate from task/project status.
const INVOICE_VARIANTS = {
  Paid: 'success',
  Overdue: 'destructive',
  Draft: 'secondary',
}
const invoiceVariant = (status) => INVOICE_VARIANTS[status] || 'warning'
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Billing history" description="Invoices issued to your organization." />

    <div class="overflow-hidden rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Invoice</th>
            <th class="px-4 py-3 font-medium">Date</th>
            <th class="px-4 py-3 font-medium">Amount</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 text-right font-medium">Download</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="invoice in invoices"
            :key="invoice.id"
            class="border-b border-border transition-colors last:border-0 hover:bg-accent"
          >
            <td class="px-4 py-3 font-medium text-card-foreground">{{ invoice.id }}</td>
            <td class="px-4 py-3 text-muted-foreground">{{ invoice.date }}</td>
            <td class="px-4 py-3 text-card-foreground">{{ invoice.amount }}</td>
            <td class="px-4 py-3">
              <Badge :variant="invoiceVariant(invoice.status)">{{ invoice.status }}</Badge>
            </td>
            <td class="px-4 py-3 text-right">
              <Button variant="ghost" size="icon" :title="`Download ${invoice.id}`">
                <Download />
                <span class="sr-only">Download {{ invoice.id }}</span>
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
```

- [ ] **Step 3: Rewrite `src/pages/Plugins.vue`**

```vue
<script setup>
import { ref } from 'vue'
import { MoreVertical, Plus } from 'lucide-vue-next'
import { Badge, Button } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'

const plugins = ref([
  { name: 'Change Detection', version: '1.0.0', enabled: true },
  { name: 'Object Detection', version: '1.0.0', enabled: true },
  { name: 'Plantation Health', version: '1.0.0', enabled: false },
  { name: 'Tree Counting', version: '1.0.0', enabled: false },
  { name: 'AI Analytics', version: '1.0.0', enabled: false },
])
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Plugins" description="Extend processing with optional analysis modules.">
      <template #actions>
        <Button>
          <Plus />
          Install plugin
        </Button>
      </template>
    </PageHeader>

    <div class="overflow-hidden rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Name</th>
            <th class="px-4 py-3 font-medium">Version</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="plugin in plugins"
            :key="plugin.name"
            class="border-b border-border transition-colors last:border-0 hover:bg-accent"
          >
            <td class="px-4 py-3 font-medium text-card-foreground">{{ plugin.name }}</td>
            <td class="px-4 py-3 text-muted-foreground">{{ plugin.version }}</td>
            <td class="px-4 py-3">
              <Badge :variant="plugin.enabled ? 'success' : 'secondary'">
                {{ plugin.enabled ? 'Enabled' : 'Disabled' }}
              </Badge>
            </td>
            <td class="px-4 py-3 text-right">
              <Button variant="ghost" size="icon" :title="`Options for ${plugin.name}`">
                <MoreVertical />
                <span class="sr-only">Options for {{ plugin.name }}</span>
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
```

- [ ] **Step 4: Confirm frappe-ui is gone from all three files**

```bash
grep -n "frappe-ui\|FeatherIcon" src/pages/NotFound.vue src/pages/Invoices.vue src/pages/Plugins.vue
```

Expected: no output.

- [ ] **Step 5: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed`, build exits 0.

- [ ] **Step 6: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 7: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/NotFound.vue frontend/src/pages/Invoices.vue frontend/src/pages/Plugins.vue
git commit -m "refactor(pages): migrate NotFound, Invoices, Plugins to ui primitives

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Migrate Login and Onboarding

Both are unauthenticated/pre-org shells with `meta.layout = false`, so they own
their full-page framing. Onboarding is currently unstyled raw HTML — this is
where it gets brought up to the rest of the app.

**Files:**
- Modify: `src/pages/Login.vue`
- Modify: `src/pages/Onboarding.vue`

**Interfaces:**
- Consumes: `Alert`, `Button`, `Input`, `Label` from `@/components/ui`; lucide
  icons. `createOrganization` / `acceptInvitation` from `@/lib/organization`
  (existing, unchanged).
- Produces: nothing for later tasks.

- [ ] **Step 1: Rewrite `src/pages/Login.vue`**

frappe-ui's `FormControl` (label+input in one) becomes explicit `Label` +
`Input`, which is why each field gains an `id`. The `login()` function body is
unchanged.

```vue
<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Map as MapIcon } from 'lucide-vue-next'
import { Alert, Button, Input, Label } from '@/components/ui'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref(null)

async function login() {
  loading.value = true
  error.value = null
  try {
    const res = await fetch('/api/method/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ usr: username.value, pwd: password.value }),
    })
    const data = await res.json()
    if (data.message === 'Logged In') {
      try {
        const csrfRes = await fetch('/api/method/webodm_core.api.csrf.get_token')
        if (csrfRes.ok) {
          const { message: token } = await csrfRes.json()
          window.csrf_token = token
        }
      } catch {}
      const redirect = route.query.redirect || '/dashboard'
      router.push(redirect)
    } else {
      error.value = data.message || 'Invalid credentials'
    }
  } catch (e) {
    error.value = 'Connection failed'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-muted p-6">
    <div class="w-full max-w-sm">
      <div class="rounded-lg border border-border bg-card p-8 shadow-sm">
        <div class="mb-8 text-center">
          <MapIcon class="mx-auto mb-3 size-10 text-primary" />
          <h1 class="text-2xl font-semibold tracking-tight text-card-foreground">G20 Tech</h1>
          <p class="mt-1 text-sm text-muted-foreground">Sign in to your account</p>
        </div>
        <form class="space-y-4" @submit.prevent="login">
          <Alert v-if="error" variant="destructive" :title="error" />
          <div class="space-y-1.5">
            <Label for="username">Username</Label>
            <Input id="username" v-model="username" type="text" placeholder="Administrator" required />
          </div>
          <div class="space-y-1.5">
            <Label for="password">Password</Label>
            <Input id="password" v-model="password" type="password" placeholder="••••••••" required />
          </div>
          <Button type="submit" class="w-full" :loading="loading">Sign in</Button>
        </form>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 2: Rewrite `src/pages/Onboarding.vue`**

Keep both flows and the `doCreate` / `doAccept` logic exactly as-is; this is
purely the styling and structure.

```vue
<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Building2, Ticket } from 'lucide-vue-next'
import { Alert, Button, Input, Label } from '@/components/ui'
import { createOrganization, acceptInvitation } from '../lib/organization.js'

const router = useRouter()
const name = ref('')
const token = ref('')
const busy = ref(false)
const error = ref('')

async function doCreate() {
  busy.value = true
  error.value = ''
  try {
    await createOrganization(name.value)
    router.push({ name: 'Dashboard' })
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function doAccept() {
  busy.value = true
  error.value = ''
  try {
    await acceptInvitation(token.value)
    router.push({ name: 'Dashboard' })
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-muted p-6">
    <div class="w-full max-w-xl space-y-6">
      <div class="text-center">
        <h1 class="text-2xl font-semibold tracking-tight text-foreground">
          Set up your organization
        </h1>
        <p class="mt-2 text-sm text-muted-foreground">
          Projects, presets, and processing all live inside an organization.
          Create one, or join an existing team with an invitation.
        </p>
      </div>

      <Alert v-if="error" variant="destructive" :title="error" />

      <div class="grid gap-4 sm:grid-cols-2">
        <section class="rounded-lg border border-border bg-card p-6">
          <Building2 class="mb-3 size-6 text-primary" />
          <h2 class="font-medium text-card-foreground">Create an organization</h2>
          <p class="mt-1 text-sm text-muted-foreground">
            You'll be its owner and can invite teammates later.
          </p>
          <div class="mt-4 space-y-1.5">
            <Label for="org-name">Organization name</Label>
            <Input id="org-name" v-model="name" placeholder="Acme Surveying" />
          </div>
          <Button class="mt-4 w-full" :disabled="!name || busy" :loading="busy" @click="doCreate">
            Create organization
          </Button>
        </section>

        <section class="rounded-lg border border-border bg-card p-6">
          <Ticket class="mb-3 size-6 text-muted-foreground" />
          <h2 class="font-medium text-card-foreground">Join with an invitation</h2>
          <p class="mt-1 text-sm text-muted-foreground">
            Paste the token from your invitation email.
          </p>
          <div class="mt-4 space-y-1.5">
            <Label for="invite-token">Invitation token</Label>
            <Input id="invite-token" v-model="token" placeholder="Invitation token" />
          </div>
          <Button
            variant="outline"
            class="mt-4 w-full"
            :disabled="!token || busy"
            :loading="busy"
            @click="doAccept"
          >
            Join organization
          </Button>
        </section>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 3: Confirm frappe-ui is gone from both files**

```bash
grep -n "frappe-ui\|FeatherIcon\|FormControl" src/pages/Login.vue src/pages/Onboarding.vue
```

Expected: no output.

- [ ] **Step 4: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed`, build exits 0.

- [ ] **Step 5: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 6: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/Login.vue frontend/src/pages/Onboarding.vue
git commit -m "refactor(auth): migrate Login and Onboarding to ui primitives

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Migrate Projects — three modals become `Dialog`

The heaviest page-migration task: three hand-rolled `fixed inset-0` modals
(delete confirm, edit, create) become accessible `Dialog`s, and three
`FormControl`s become explicit `Label` + `Input`/`Textarea`/`Select`.

All fetch logic (`createProject`, `updateProject`, `deleteProject`, `onMounted`,
`csrfHeaders`) is unchanged — only imports, the removed `statusTheme`, and the
template.

**Files:**
- Modify: `src/pages/Projects.vue`

**Interfaces:**
- Consumes: `Badge`, `Button`, `Dialog`, `Input`, `Label`, `Select`, `Textarea`
  from `@/components/ui`; `PageHeader`; `statusVariant` from `@/lib/status`;
  `toast` from `@/lib/toast`; lucide icons.
- Produces: nothing for later tasks.

- [ ] **Step 1: Update the imports**

Replace the two frappe-ui import lines (currently lines 111–112) with:

```js
import { Calendar, Folder, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import {
  Badge,
  Button,
  Dialog,
  Input,
  Label,
  Select,
  Textarea,
} from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { statusVariant } from '@/lib/status'
import { toast } from '@/lib/toast'
```

Delete the local `statusTheme` function (lines 128–131) — `statusVariant`
replaces it.

- [ ] **Step 2: Replace the list portion of the template**

Everything from the opening `<div class="p-6 ...">` down to (and including) the
project grid `</div>`, i.e. the current lines 2–63, becomes:

```vue
  <div class="space-y-6">
    <PageHeader title="Projects" description="Every mapping project in your organization.">
      <template #actions>
        <Button @click="showNewProject = true">
          <Plus />
          New project
        </Button>
      </template>
    </PageHeader>

    <p v-if="loading" class="py-12 text-center text-muted-foreground">Loading projects…</p>

    <p v-else-if="error" class="py-12 text-center text-destructive">{{ error }}</p>

    <div
      v-else-if="projects.length === 0"
      class="rounded-lg border border-dashed border-border py-16 text-center"
    >
      <Folder class="mx-auto mb-4 size-12 text-muted-foreground/40" />
      <p class="text-lg text-foreground">No projects yet</p>
      <p class="mt-1 text-sm text-muted-foreground">
        Create your first project to get started.
      </p>
      <Button class="mt-6" @click="showNewProject = true">
        <Plus />
        New project
      </Button>
    </div>

    <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      <div
        v-for="project in projects"
        :key="project.name"
        class="cursor-pointer rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40"
        @click="openProject(project.name)"
      >
        <div class="mb-3 flex items-start justify-between gap-2">
          <h3 class="truncate font-medium text-card-foreground">
            {{ project.title || project.name }}
          </h3>
          <div class="flex flex-shrink-0 items-center gap-1">
            <Badge :variant="statusVariant(project.status)">{{ project.status }}</Badge>
            <Button
              variant="ghost"
              size="icon"
              class="size-7"
              title="Edit project"
              @click.stop="openEdit(project)"
            >
              <Pencil />
              <span class="sr-only">Edit {{ project.title || project.name }}</span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              class="size-7 text-muted-foreground hover:text-destructive"
              title="Delete project"
              @click.stop="confirmDelete(project)"
            >
              <Trash2 />
              <span class="sr-only">Delete {{ project.title || project.name }}</span>
            </Button>
          </div>
        </div>
        <p v-if="project.description" class="mb-3 line-clamp-2 text-sm text-muted-foreground">
          {{ project.description }}
        </p>
        <div class="flex items-center justify-between text-xs">
          <span class="flex items-center gap-1 text-muted-foreground">
            <Calendar class="size-3" />
            {{ formatDate(project.creation) }}
          </span>
          <span v-if="project._taskStatus" class="flex items-center gap-2">
            <span v-if="project._taskStatus.Pending > 0" class="text-warning">
              {{ project._taskStatus.Pending }} pending
            </span>
            <span v-if="project._taskStatus.Running > 0" class="text-primary">
              {{ project._taskStatus.Running }} running
            </span>
            <span v-if="project._taskStatus.Completed > 0" class="text-success">
              {{ project._taskStatus.Completed }} done
            </span>
            <span v-if="project._taskStatus.Failed > 0" class="text-destructive">
              {{ project._taskStatus.Failed }} failed
            </span>
          </span>
        </div>
      </div>
    </div>
```

- [ ] **Step 3: Replace all three modals with `Dialog`s**

Replace the current lines 65–104 (the three `v-if` overlay blocks) with the
following, then close the root `</div>` and `</template>` as before.

`Dialog` takes `:open` plus `@update:open`, so Esc and overlay clicks close it —
behavior the hand-rolled modals never had.

```vue
    <Dialog
      :open="!!deleteTarget"
      title="Delete project"
      class="sm:max-w-sm"
      @update:open="value => { if (!value) deleteTarget = null }"
    >
      <p class="text-sm text-muted-foreground">
        Are you sure you want to delete
        <strong class="text-foreground">{{ deleteTarget?.title || deleteTarget?.name }}</strong>?
        This also deletes its tasks and cannot be undone.
      </p>
      <template #footer>
        <Button variant="ghost" @click="deleteTarget = null">Cancel</Button>
        <Button variant="destructive" :loading="deleting" @click="deleteProject">Delete</Button>
      </template>
    </Dialog>

    <Dialog
      :open="!!editTarget"
      title="Edit project"
      @update:open="value => { if (!value) editTarget = null }"
    >
      <div class="space-y-4">
        <div class="space-y-1.5">
          <Label for="edit-title">Title</Label>
          <Input id="edit-title" v-model="editForm.title" required />
        </div>
        <div class="space-y-1.5">
          <Label for="edit-description">Description</Label>
          <Textarea id="edit-description" v-model="editForm.description" />
        </div>
        <div class="space-y-1.5">
          <Label for="edit-status">Status</Label>
          <Select id="edit-status" v-model="editForm.status">
            <option v-for="s in EDIT_STATUSES" :key="s" :value="s">{{ s }}</option>
          </Select>
        </div>
      </div>
      <template #footer>
        <Button variant="ghost" @click="editTarget = null">Cancel</Button>
        <Button :loading="editing" @click="updateProject">Save</Button>
      </template>
    </Dialog>

    <Dialog v-model:open="showNewProject" title="New project">
      <div class="space-y-4">
        <div class="space-y-1.5">
          <Label for="new-title">Title</Label>
          <Input id="new-title" v-model="form.title" required />
        </div>
        <div class="space-y-1.5">
          <Label for="new-description">Description</Label>
          <Textarea id="new-description" v-model="form.description" />
        </div>
        <div class="space-y-1.5">
          <Label for="new-status">Status</Label>
          <Select id="new-status" v-model="form.status">
            <option v-for="s in NEW_STATUSES" :key="s" :value="s">{{ s }}</option>
          </Select>
        </div>
      </div>
      <template #footer>
        <Button variant="ghost" @click="showNewProject = false">Cancel</Button>
        <Button :loading="saving" @click="createProject">Create</Button>
      </template>
    </Dialog>
  </div>
</template>
```

- [ ] **Step 4: Add the status option constants**

The old `FormControl :options="[...]"` arrays were inline. Add these next to the
other `const` declarations in `<script setup>` (they preserve the existing
difference: a new project cannot start out Completed or Cancelled):

```js
const EDIT_STATUSES = ['Planned', 'In Progress', 'Completed', 'Cancelled']
const NEW_STATUSES = ['Planned', 'In Progress']
```

- [ ] **Step 5: Confirm frappe-ui and the hand-rolled overlays are gone**

```bash
grep -n "frappe-ui\|FeatherIcon\|FormControl\|statusTheme\|fixed inset-0" src/pages/Projects.vue
```

Expected: no output.

- [ ] **Step 6: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed`, build exits 0.

- [ ] **Step 7: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 8: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/Projects.vue
git commit -m "refactor(projects): migrate to ui primitives, replace modals with Dialog

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Migrate Presets and Settings

Both are form-heavy pages sharing the same `@/lib/presets` API layer. Presets
has one hand-rolled modal that becomes a `Dialog`; Settings has raw
`<select>`/`<input>` that become `Select`/`Input` with proper `Label`s.

**Files:**
- Modify: `src/pages/Presets.vue`
- Modify: `src/pages/Settings.vue`

**Interfaces:**
- Consumes: `Badge`, `Button`, `Dialog`, `Input`, `Label`, `Select` from
  `@/components/ui`; `PageHeader`; `toast` from `@/lib/toast`; lucide icons.
  `OdmOptionsForm` and the `useOdmOptions` composable are used **unchanged**.
- Produces: nothing for later tasks.

- [ ] **Step 1: Rewrite the `Presets.vue` template**

Keep the whole `<script setup>` logic (`refresh`, `openCreate`, `openEdit`,
`toOptionsArray`, `onSave`, `onDelete`) unchanged.

```vue
<template>
  <div class="space-y-6">
    <PageHeader
      title="Processing presets"
      description="Reusable OpenDroneMap option sets for your organization."
    >
      <template #actions>
        <Button @click="openCreate">
          <Plus />
          New preset
        </Button>
      </template>
    </PageHeader>

    <div class="overflow-hidden rounded-lg border border-border bg-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-muted-foreground">
            <th class="px-4 py-3 font-medium">Name</th>
            <th class="px-4 py-3 font-medium">Options</th>
            <th class="px-4 py-3 font-medium">Scope</th>
            <th class="px-4 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in presets"
            :key="p.name"
            class="border-b border-border transition-colors last:border-0 hover:bg-accent"
          >
            <td class="px-4 py-3 font-medium text-card-foreground">{{ p.preset_name }}</td>
            <td class="px-4 py-3 text-muted-foreground">{{ p.options.length }} option(s)</td>
            <td class="px-4 py-3">
              <Badge :variant="p.system ? 'default' : 'secondary'">
                {{ p.system ? 'System' : 'Organization' }}
              </Badge>
            </td>
            <td class="px-4 py-3 text-right">
              <Button variant="ghost" size="icon" class="size-8" title="Edit preset" @click="openEdit(p)">
                <Pencil />
                <span class="sr-only">Edit {{ p.preset_name }}</span>
              </Button>
              <Button
                variant="ghost"
                size="icon"
                class="size-8 text-muted-foreground hover:text-destructive"
                title="Delete preset"
                @click="onDelete(p)"
              >
                <Trash2 />
                <span class="sr-only">Delete {{ p.preset_name }}</span>
              </Button>
            </td>
          </tr>
          <tr v-if="!presets.length">
            <td colspan="4" class="px-4 py-10 text-center text-muted-foreground">
              No presets yet.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <Dialog
      v-model:open="showModal"
      :title="`${editing ? 'Edit' : 'New'} preset`"
      class="sm:max-w-lg"
    >
      <div class="space-y-4">
        <div class="space-y-1.5">
          <Label for="preset-name">Name</Label>
          <Input id="preset-name" v-model="draft.preset_name" />
        </div>

        <p v-if="odm.error.value" class="text-sm text-destructive">{{ odm.error.value }}</p>
        <p v-else-if="odm.loading.value" class="text-sm text-muted-foreground">
          Loading options…
        </p>
        <OdmOptionsForm
          v-else
          :catalog="odm.catalog.value"
          v-model="values"
          :field-type="odm.fieldType"
        />
      </div>
      <template #footer>
        <Button variant="ghost" @click="showModal = false">Cancel</Button>
        <Button :loading="saving" @click="onSave">Save</Button>
      </template>
    </Dialog>
  </div>
</template>
```

- [ ] **Step 2: Update the `Presets.vue` imports**

Replace `import { Button, FeatherIcon, toast } from 'frappe-ui'` with:

```js
import { Pencil, Plus, Trash2 } from 'lucide-vue-next'
import { Badge, Button, Dialog, Input, Label } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { toast } from '@/lib/toast'
```

- [ ] **Step 3: Rewrite the `Settings.vue` template**

Keep the whole `<script setup>` logic (`onMounted`, `onSave`, `form`)
unchanged.

```vue
<template>
  <div class="max-w-2xl space-y-6">
    <PageHeader
      title="Settings"
      description="Defaults and limits for your organization's processing."
    />

    <section class="space-y-4 rounded-lg border border-border bg-card p-6">
      <h2 class="font-medium text-card-foreground">Processing</h2>
      <div class="space-y-1.5">
        <Label for="default-preset">Default preset</Label>
        <Select id="default-preset" v-model="form.default_preset">
          <option :value="null">None</option>
          <option v-for="p in presets" :key="p.name" :value="p.name">
            {{ p.preset_name }}
          </option>
        </Select>
        <p class="text-xs text-muted-foreground">
          Applied to new tasks when no preset is chosen at upload.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <input
          id="auto-process"
          v-model="form.auto_start_processing"
          type="checkbox"
          class="size-4 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Label for="auto-process" class="font-normal">
          Auto-start processing after upload
        </Label>
      </div>
    </section>

    <section class="space-y-4 rounded-lg border border-border bg-card p-6">
      <h2 class="font-medium text-card-foreground">Limits</h2>
      <div class="space-y-1.5">
        <Label for="max-size">Max upload size (MB)</Label>
        <Input id="max-size" v-model.number="form.max_file_size_mb" type="number" />
        <p class="text-xs text-muted-foreground">
          Capped by the platform limit set by the operator.
        </p>
      </div>
    </section>

    <section class="space-y-4 rounded-lg border border-border bg-card p-6">
      <h2 class="font-medium text-card-foreground">Notifications</h2>
      <div class="flex items-center gap-2">
        <input
          id="email-done"
          v-model="form.email_notifications"
          type="checkbox"
          class="size-4 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring"
        />
        <Label for="email-done" class="font-normal">Email notifications</Label>
      </div>
    </section>

    <div class="flex justify-end">
      <Button :loading="saving" @click="onSave">
        <Check />
        Save settings
      </Button>
    </div>
  </div>
</template>
```

- [ ] **Step 4: Update the `Settings.vue` imports**

Replace `import { Button, FeatherIcon, toast } from 'frappe-ui'` with:

```js
import { Check } from 'lucide-vue-next'
import { Button, Input, Label, Select } from '@/components/ui'
import PageHeader from '@/components/PageHeader.vue'
import { toast } from '@/lib/toast'
```

- [ ] **Step 5: Check `v-model.number` still round-trips through `Input`**

`Input` emits `$event.target.value`, which is always a string; `v-model.number`
applies Vue's number cast on the way in. Confirm the max-file-size field is
saved as a number, not a string, by reading the emitted payload:

```bash
grep -n "max_file_size_mb" src/pages/Settings.vue
```

Expected: the `onSave` body still passes `form.value.max_file_size_mb`
unchanged, and the template uses `v-model.number`. If the field is a string at
save time the backend clamp comparison would misbehave — `v-model.number` is
what prevents that, so do not drop the modifier.

- [ ] **Step 6: Confirm frappe-ui is gone from both files**

```bash
grep -n "frappe-ui\|FeatherIcon\|fixed inset-0" src/pages/Presets.vue src/pages/Settings.vue
```

Expected: no output.

- [ ] **Step 7: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed`, build exits 0.

- [ ] **Step 8: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 9: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/Presets.vue frontend/src/pages/Settings.vue
git commit -m "refactor(presets,settings): migrate to ui primitives and design tokens

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Migrate Console and ModelView

Both are task-detail pages that now sit under the secondary tab row, so both
lose their bespoke back-button headers — the tab row provides that navigation.

ModelView's three.js viewer logic is untouched. (three.js → CesiumJS is
subsystem C.)

**Files:**
- Modify: `src/pages/Console.vue`
- Modify: `src/pages/ModelView.vue`

**Interfaces:**
- Consumes: `Badge`, `Button` from `@/components/ui`; `statusVariant` from
  `@/lib/status`; lucide icons.
- Produces: nothing for later tasks.

- [ ] **Step 1: Rewrite the `Console.vue` template**

Keep all polling logic (`fetchTask`, `fetchConsole`, `startPolling`,
`refreshLogs`, `onScroll`, `scrollToBottom`, `RUNNING_STATUSES`,
`csrfHeaders`) exactly as-is. Delete the local `statusTheme` function.

Note the artifact download links collapse from five near-identical 4-line
blocks into a `v-for` over a computed list, and the `text-text-gray-500` typo
(current line 44) is fixed.

```vue
<template>
  <div class="flex h-full flex-col">
    <div class="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-border px-4 py-3">
      <h2 class="text-base font-medium text-foreground">
        {{ task?.title || task?.name || 'Task console' }}
      </h2>
      <Badge v-if="task" :variant="statusVariant(task.status)">{{ task.status }}</Badge>
      <span
        v-if="task?.node_progress != null || task?.progress != null"
        class="text-sm text-muted-foreground"
      >
        {{ Math.round(task?.node_progress ?? task?.progress) }}%
      </span>
      <span class="text-xs text-muted-foreground">
        Resolution: {{ task?.resolution || 'N/A' }} · Images: {{ task?.images?.length || 0 }}
      </span>
      <Button variant="outline" size="sm" class="ml-auto" @click="refreshLogs">
        <RefreshCw />
        Refresh
      </Button>
    </div>

    <div class="flex flex-shrink-0 flex-wrap gap-2 border-b border-border px-4 py-3">
      <template v-if="artifacts.length">
        <a
          v-for="artifact in artifacts"
          :key="artifact.label"
          :href="artifact.href"
          download
          class="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent"
        >
          <component :is="artifact.icon" class="size-3.5" />
          {{ artifact.label }}
        </a>
      </template>
      <span v-else-if="task?.status === 'Completed'" class="text-sm text-muted-foreground">
        No artifacts available
      </span>
      <span v-else class="text-sm text-muted-foreground">
        Artifacts appear when processing completes
      </span>
    </div>

    <p v-if="loading" class="flex flex-1 items-center justify-center text-muted-foreground">
      Loading task…
    </p>
    <div
      v-else
      ref="logEl"
      class="flex-1 overflow-y-auto bg-slate-950 p-4 font-mono text-sm text-emerald-400"
      @scroll="onScroll"
    >
      <div v-for="(line, i) in logs" :key="i" class="whitespace-pre-wrap">{{ line }}</div>
      <p v-if="logs.length === 0" class="text-slate-500">
        {{ RUNNING_STATUSES.includes(task?.status) ? 'Waiting for processing output…' : 'No console output for this task.' }}
      </p>
    </div>
  </div>
</template>
```

The log pane keeps literal `slate-950`/`emerald-400` rather than tokens on
purpose: a terminal is always dark, in both themes.

- [ ] **Step 2: Update the `Console.vue` script**

Replace `import { Badge, Button, FeatherIcon } from 'frappe-ui'` with:

```js
import { Box, Download, RefreshCw } from 'lucide-vue-next'
import { Badge, Button } from '@/components/ui'
import { statusVariant } from '@/lib/status'
```

Add `computed` to the existing `vue` import, delete the local `statusTheme`
function, and add the artifacts computed:

```js
// The five artifact links were five near-identical markup blocks; this drives
// them from data instead.
const artifacts = computed(() => {
  const t = task.value
  if (!t || t.status !== 'Completed') return []
  return [
    { label: 'Orthophoto', href: t.orthophoto, icon: Download },
    { label: 'DSM', href: t.dsm, icon: Download },
    { label: 'DTM', href: t.dtm, icon: Download },
    { label: 'Point Cloud', href: t.point_cloud, icon: Download },
    { label: '3D Model', href: t.model, icon: Box },
  ].filter(a => a.href)
})
```

- [ ] **Step 3: Update the `ModelView.vue` chrome**

Replace only the header `<div>` (current lines 3–18) with the block below, and
leave the entire viewer `<div ref="viewerRef">` and all three.js logic
untouched. The `<Back` button goes away — the secondary tab row handles that
navigation now.

```vue
    <div class="flex flex-shrink-0 items-center gap-3 border-b border-border px-4 py-3">
      <h2 class="text-base font-medium text-foreground">{{ task?.title || '3D viewer' }}</h2>
      <Badge v-if="task" :variant="statusVariant(task.status)">{{ task.status }}</Badge>
      <div class="ml-auto flex items-center gap-2">
        <a
          v-if="task?.model"
          :href="task.model"
          download
          class="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent"
        >
          <Download class="size-3.5" />
          Model
        </a>
        <Button variant="outline" size="sm" @click="resetCamera">
          <Maximize />
          Reset view
        </Button>
      </div>
    </div>
```

Also change the page root element's classes from
`class="h-screen flex flex-col bg-gray-900"` to
`class="flex h-full flex-col bg-background"` — it now lives inside
`AppLayout`'s `<main>`, so `h-screen` would overflow past the tab bars.

In the error overlay further down, replace
`<FeatherIcon name="alert-triangle" class="h-10 w-10 text-yellow-500 mx-auto mb-3" />`
with `<TriangleAlert class="mx-auto mb-3 size-10 text-warning" />`, and change
the two `variant="outline" size="sm" theme="gray"` Buttons to just
`variant="outline" size="sm"` (our Button has no `theme` prop).

- [ ] **Step 4: Update the `ModelView.vue` script imports**

Replace `import { Badge, Button, FeatherIcon } from 'frappe-ui'` with:

```js
import { Download, Maximize, TriangleAlert } from 'lucide-vue-next'
import { Badge, Button } from '@/components/ui'
import { statusVariant } from '@/lib/status'
```

Delete the local `statusBadge` function and update its call site in the
template to `statusVariant`.

- [ ] **Step 5: Confirm frappe-ui and the leftovers are gone**

```bash
grep -n "frappe-ui\|FeatherIcon\|statusTheme\|statusBadge\|text-text-\|theme=\"gray\"" src/pages/Console.vue src/pages/ModelView.vue
```

Expected: no output.

- [ ] **Step 6: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed`, build exits 0.

- [ ] **Step 7: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 8: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/Console.vue frontend/src/pages/ModelView.vue
git commit -m "refactor(task-views): migrate Console and ModelView to ui primitives

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Migrate MapView (shallow) and add the full-bleed route flag

**Global Constraint 9 governs this task.** Touch chrome and primitives only.
Do NOT restructure the Leaflet setup, `useMeasure`, `mapLayers.js`,
`flightPath.js`, the overlay/opacity wiring, or the basemap switcher — subsystem
B replaces those with MapLibre, and any restructuring here is wasted work that
also makes B's diff unreviewable.

**Files:**
- Modify: `src/pages/MapView.vue`
- Modify: `src/main.js` (add `fullBleed: true` to the MapView route)

**Interfaces:**
- Consumes: `Badge`, `Button`, `Dialog`, `Label`, `Select` from
  `@/components/ui`; `statusVariant` from `@/lib/status`; `toast` from
  `@/lib/toast`; lucide icons. `AppLayout`'s `fullBleed` computed (Task 6) reads
  the route meta this task sets.
- Produces: `meta.fullBleed === true` on the MapView route, which
  `AppLayout` already honors.

- [ ] **Step 1: Add the full-bleed flag to the MapView route in `src/main.js`**

The map must fill the viewport with no page padding. `AppLayout` (Task 6)
already reads this flag; this is the only routing change in the whole plan.

Change the MapView route entry (currently lines 57–62) to:

```js
  {
    path: '/project/:id',
    name: 'MapView',
    component: () => import('./pages/MapView.vue'),
    meta: { requiresAuth: true, title: 'Project', fullBleed: true },
  },
```

- [ ] **Step 2: Update the `MapView.vue` imports**

Replace `import { Button, Badge, FeatherIcon } from 'frappe-ui'` and
`import { toast } from 'frappe-ui'` with:

```js
import {
  Box,
  Check,
  CircleX,
  CloudUpload,
  Minus,
  Play,
  Square,
  Terminal,
  Trash2,
} from 'lucide-vue-next'
import { Badge, Button, Dialog, Label, Select } from '@/components/ui'
import { statusVariant } from '@/lib/status'
import { toast } from '@/lib/toast'
```

Delete the local `statusTheme` function and change its template call site to
`statusVariant`.

- [ ] **Step 3: Fix the page root so it fits inside the layout**

The root element is currently `class="h-screen flex"`. Inside `AppLayout`'s
`<main>` that overflows past the tab bars. Change it to:

```vue
  <div class="flex h-full" @mousemove="onResize" @mouseup="stopResize">
```

Also remove the sidebar's own back-button block (the `<Button variant="outline"
size="sm" @click="$router.push('/projects')">&larr; Back</Button>` and its
wrapper `<div class="p-4 border-b ...">`), keeping the `<h2>` project title —
the primary tab bar and secondary row now own that navigation. The title block
becomes:

```vue
      <div class="border-b border-border p-4">
        <h2 class="text-base font-medium text-foreground">
          {{ project?.title || project?.name }}
        </h2>
      </div>
```

- [ ] **Step 4: Convert the Buttons and icons in the task sidebar**

Every `<Button>` in this file must lose the props our Button does not have:
`theme` (no such prop — use `variant`) and the `#prefix` slot (put the icon in
the default slot). The mapping for the four task-action buttons:

| Current | Replacement |
|---|---|
| `variant="solid" size="sm" theme="green"` + `#prefix` `play` | `variant="success" size="sm"` + `<Play />` in default slot |
| `variant="outline" size="sm" theme="red"` + `#prefix` `x-circle` | `variant="outline" size="sm" class="text-destructive"` + `<CircleX />` |
| `variant="outline" size="sm" theme="gray"` + `#prefix` `terminal` | `variant="outline" size="sm"` + `<Terminal />` |
| `variant="outline" size="sm" theme="gray"` + `#prefix` `box` | `variant="outline" size="sm"` + `<Box />` |
| `variant="solid" theme="blue" class="w-full mt-4"` + `#prefix` `upload-cloud` | `class="mt-4 w-full"` + `<CloudUpload />` |

So, for example, the start-processing button becomes:

```vue
              <Button
                v-if="task.status === 'Pending'"
                variant="success"
                size="sm"
                @click.stop="startProcessing(task)"
              >
                <Play />
                Start
              </Button>
```

The per-task delete `<button>` with `<FeatherIcon name="trash-2" ... />`
becomes:

```vue
            <Button
              variant="ghost"
              size="icon"
              class="size-7 flex-shrink-0 text-muted-foreground hover:text-destructive"
              title="Delete task"
              @click.stop="confirmDeleteTask(task)"
            >
              <Trash2 />
              <span class="sr-only">Delete task</span>
            </Button>
```

- [ ] **Step 5: Convert the measurement toolbar**

The five toolbar buttons use `:variant="... ? 'solid' : 'outline'"`, and `solid`
is not one of our variants. Map `solid` → `default` throughout, and move the
`#prefix` icons into the default slot. The measure-mode buttons become:

```vue
        <Button
          size="sm"
          :variant="measure.state.mode === 'distance' ? 'default' : 'outline'"
          title="Measure distance"
          @click="startMeasure('distance')"
        >
          <Minus />
          Distance
        </Button>
        <Button
          size="sm"
          :variant="measure.state.mode === 'area' ? 'default' : 'outline'"
          title="Measure area"
          @click="startMeasure('area')"
        >
          <Square />
          Area
        </Button>
        <Button
          size="sm"
          :variant="measure.state.mode === 'volume' ? 'default' : 'outline'"
          :disabled="!hasDsm"
          :title="hasDsm ? 'Measure volume (needs DSM)' : 'Volume requires a DSM'"
          @click="startMeasure('volume')"
        >
          <Box />
          Volume
        </Button>
        <Button
          v-if="measure.state.drawing"
          size="sm"
          variant="success"
          title="Finish measurement"
          @click="finishMeasure"
        >
          <Check />
          Finish
        </Button>
        <Button size="sm" variant="ghost" title="Clear measurement" @click="clearMeasure">
          <Trash2 />
          <span class="sr-only">Clear measurement</span>
        </Button>
```

Keep the two `<span>` status readouts after them, retokenized:
`text-gray-500 dark:text-gray-400` → `text-muted-foreground`, and
`text-gray-700 dark:text-gray-200` → `text-foreground`.

- [ ] **Step 6: Retokenize the map control panels**

In the floating toolbar and the layers panel, replace the ad-hoc color pairs
with tokens. Apply these substitutions throughout the file's template:

| Current | Replacement |
|---|---|
| `bg-white dark:bg-gray-800` | `bg-card` |
| `bg-white dark:bg-gray-900` | `bg-card` |
| `border dark:border-gray-700` | `border border-border` |
| `border-r dark:border-gray-700` | `border-r border-border` |
| `border-b dark:border-gray-700` | `border-b border-border` |
| `border-t dark:border-gray-700` | `border-t border-border` |
| `text-gray-900 dark:text-gray-100` | `text-foreground` |
| `text-gray-500 dark:text-gray-400` | `text-muted-foreground` |
| `text-gray-400 dark:text-gray-500` | `text-muted-foreground` |
| `text-gray-700 dark:text-gray-300` | `text-foreground` |
| `bg-gray-50 dark:bg-gray-900` | `bg-background` |
| `hover:bg-gray-50 dark:hover:bg-gray-800` | `hover:bg-accent` |
| `bg-gray-200 dark:bg-gray-700` (resize handle) | `bg-border` |
| `border-blue-500 dark:border-blue-400 bg-blue-50 dark:bg-blue-900/30` (selected task) | `border-primary bg-primary/10` |
| `bg-blue-500` (progress bar fill) | `bg-primary` |
| `bg-gray-200 dark:bg-gray-700` (progress bar track) | `bg-muted` |

Leave the `z-[1000]` / `z-[10000]` stacking values and the
`style="transform: translateZ(0)"` hacks alone — they exist to sit above
Leaflet's own layers.

- [ ] **Step 7: Convert the upload modal to a `Dialog`**

Replace the whole `v-if="showUpload"` overlay block (current lines 174–207)
with:

```vue
    <Dialog v-model:open="showUpload" title="Add task" description="Select images to upload for processing.">
      <div class="space-y-4">
        <input
          type="file"
          multiple
          accept="image/*"
          class="block w-full text-sm text-muted-foreground file:mr-4 file:rounded-md file:border-0 file:bg-primary/10 file:px-4 file:py-2 file:text-sm file:font-medium file:text-primary hover:file:bg-primary/20"
          @change="uploadFiles"
        />

        <div class="space-y-1.5">
          <Label for="upload-preset">Preset</Label>
          <Select id="upload-preset" v-model="selectedPreset" @change="applyPreset">
            <option :value="null">None (defaults)</option>
            <option v-for="p in uploadPresets" :key="p.name" :value="p.name">
              {{ p.preset_name }}
            </option>
          </Select>
        </div>

        <p v-if="uploadOdm.error.value" class="text-xs text-destructive">
          {{ uploadOdm.error.value }}
        </p>
        <p v-else-if="uploadOdm.loading.value" class="text-xs text-muted-foreground">
          Loading options…
        </p>
        <div v-else class="max-h-64 overflow-y-auto pr-1">
          <OdmOptionsForm
            :catalog="uploadOdm.catalog.value"
            v-model="uploadValues"
            :field-type="uploadOdm.fieldType"
          />
        </div>

        <div v-if="uploading" class="rounded-md bg-primary/10 px-4 py-3 text-sm text-primary">
          {{ uploadProgress }}
        </div>
      </div>
      <template #footer>
        <Button variant="ghost" @click="showUpload = false">Cancel</Button>
      </template>
    </Dialog>
```

**Watch the `Select` here:** the old markup used `v-model` plus
`@change="applyPreset"` on a native `<select>`. Our `Select` re-emits `change`
from the native element, so both keep working — but verify `applyPreset` still
fires in Step 9.

- [ ] **Step 8: Confirm frappe-ui and incompatible props are gone**

```bash
grep -n "frappe-ui\|FeatherIcon\|statusTheme\|theme=\"\|variant=\"solid\"\|#prefix\|fixed inset-0\|h-screen" src/pages/MapView.vue
```

Expected: no output. (`theme="` catches leftover frappe-ui Button themes;
`h-screen` catches the root-element fix from Step 3.)

- [ ] **Step 9: Verify the map still works, then the suite and the build**

The measurement composable and layer helpers have unit tests that must still
pass — they are the guard that this stayed shallow:

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run src/lib/mapLayers.test.js src/lib/flightPath.test.js src/composables/useMeasure.import.test.js
npx vitest run
npx vite build --mode development
```

Expected: the targeted run passes; the full run is `75 passed`; build exits 0.

Then confirm you did not restructure map internals — this diff must show
template/import changes only, no changes to Leaflet calls:

```bash
git -C /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend \
  diff -- frontend/src/pages/MapView.vue | grep -E "^[+-].*\b(L\.|map\.|tileLayer|addLayer|removeLayer|setView|fitBounds)" || echo "NO LEAFLET LOGIC TOUCHED"
```

Expected: `NO LEAFLET LOGIC TOUCHED`. If it prints Leaflet lines, you went too
deep — revert those hunks (Global Constraint 9).

- [ ] **Step 10: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 11: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/MapView.vue frontend/src/main.js
git commit -m "refactor(mapview): shallow migration to ui primitives, full-bleed layout

Chrome, primitives and tokens only — Leaflet internals untouched, since
subsystem B replaces them with MapLibre.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Landing page redesign (DaaS marketing)

A full rewrite of `src/pages/Landing.vue`. The differentiator per the spec:
competitors lead with stock aerial photography; this page leads with **the
product doing the work** — the actual processing pipeline and its measurable
outputs.

**Global Constraint 7 is the hard rule here:** the only figure verifiable from
this codebase is the **2,539 × 2,444 px** georeferenced orthophoto. Every other
number MUST be a literal `TODO(user)` placeholder. Do not invent customer
counts, uptime percentages, hectares processed, or turnaround times.

**Files:**
- Modify: `src/pages/Landing.vue`

**Interfaces:**
- Consumes: `Badge`, `Button` from `@/components/ui`; lucide icons.
- Produces: nothing for later tasks.

- [ ] **Step 1: Rewrite `src/pages/Landing.vue`**

The existing pricing tiers (names, prices, feature lists) are carried over
verbatim — they are the user's real prices. Only their presentation changes.

```vue
<script setup>
import { Badge, Button } from '@/components/ui'
import {
  ArrowRight,
  Box,
  Camera,
  Check,
  Cpu,
  Layers,
  Map as MapIcon,
  Mountain,
  Ruler,
  Share2,
  Users,
} from 'lucide-vue-next'

// The four-step arc from raw imagery to a shareable, measurable result. This is
// the technical-credibility beat: it mirrors the real pipeline, not a metaphor.
const pipeline = [
  {
    icon: Camera,
    step: 'Capture',
    body: 'Fly your mission and upload the raw images. GPS EXIF is preserved end to end, so outputs land in real-world coordinates.',
  },
  {
    icon: Cpu,
    step: 'Process',
    body: 'An OpenDroneMap pipeline reconstructs the scene — structure from motion, dense point cloud, mesh, texture.',
  },
  {
    icon: Ruler,
    step: 'Measure',
    body: 'Draw on the map to get distance, area, and DSM-backed volume. Answers, not just pictures.',
  },
  {
    icon: Share2,
    step: 'Share',
    body: 'Give your team one workspace with per-organization projects, presets, and access.',
  },
]

const capabilities = [
  {
    icon: MapIcon,
    title: 'Georeferenced orthophotos',
    body: 'Cloud-optimized GeoTIFFs served as map tiles, aligned to real coordinates.',
  },
  {
    icon: Mountain,
    title: 'DSM & DTM',
    body: 'Digital surface and terrain models — the elevation basis for volume math.',
  },
  {
    icon: Box,
    title: 'Textured 3D models',
    body: 'Browser-viewable reconstructions. No plugin, no desktop install.',
  },
  {
    icon: Layers,
    title: 'Dense point clouds',
    body: 'Export the full reconstruction for downstream CAD and GIS work.',
  },
  {
    icon: Ruler,
    title: 'Volume, area, distance',
    body: 'Stockpile and cut/fill volumes computed against the DSM, not estimated.',
  },
  {
    icon: Users,
    title: 'Team workspaces',
    body: 'Organizations, invitations, and shared presets — isolated per tenant.',
  },
]

const plans = [
  {
    name: 'Starter',
    description: 'For individual pilots and small projects.',
    price: 'Rp 1.700.000',
    period: 'month',
    featured: false,
    features: [
      '10 GB storage',
      '100 processing credits/month',
      '~2 Gigapixels capacity',
      'Orthophoto & DSM export',
      'Email support (5 business days)',
    ],
  },
  {
    name: 'Standard',
    description: 'For teams and regular mapping operations.',
    price: 'Rp 8.500.000',
    period: 'month',
    featured: true,
    features: [
      '100 GB storage',
      '600 processing credits/month',
      '~12 Gigapixels capacity',
      'Change detection analytics',
      'Map sharing & collaboration',
      'Email support (2 business days)',
    ],
  },
  {
    name: 'Advanced',
    description: 'For enterprises with large-scale needs.',
    price: 'Rp 34.000.000',
    period: 'month',
    featured: false,
    features: [
      '600 GB storage',
      '3,000 processing credits/month',
      'API access & integrations',
      'User access control',
      'On-premise deployment',
      'Dedicated GIS expert support',
      'SLA guarantee',
    ],
  },
]

const year = new Date().getFullYear()
</script>

<template>
  <div class="min-h-screen bg-background">
    <!-- Nav -->
    <nav class="fixed top-0 z-50 w-full border-b border-border bg-background/80 backdrop-blur-md">
      <div class="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <router-link to="/" class="flex items-center gap-2">
          <MapIcon class="size-5 text-primary" />
          <span class="font-semibold tracking-tight text-foreground">G20 Tech</span>
        </router-link>
        <div class="flex items-center gap-6">
          <a href="#pipeline" class="hidden text-sm text-muted-foreground transition-colors hover:text-foreground sm:block">How it works</a>
          <a href="#capabilities" class="hidden text-sm text-muted-foreground transition-colors hover:text-foreground sm:block">Capabilities</a>
          <a href="#pricing" class="hidden text-sm text-muted-foreground transition-colors hover:text-foreground sm:block">Pricing</a>
          <router-link to="/login">
            <Button size="sm">Sign in</Button>
          </router-link>
        </div>
      </div>
    </nav>

    <!-- Hero: dark, high-contrast, split. Leads with the product, not a photo. -->
    <section class="relative overflow-hidden bg-slate-950 pt-14 text-slate-100">
      <div
        class="pointer-events-none absolute inset-0 opacity-30"
        style="background-image: radial-gradient(circle at 20% 20%, rgb(37 99 235 / 0.5), transparent 55%), radial-gradient(circle at 80% 60%, rgb(37 99 235 / 0.25), transparent 50%)"
      />
      <div class="relative mx-auto grid max-w-6xl items-center gap-12 px-6 py-20 lg:grid-cols-2 lg:py-28">
        <div class="animate-slide-up">
          <Badge variant="outline" class="border-slate-700 text-slate-300">
            Drone data as a service
          </Badge>
          <h1 class="mt-5 text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            From flight to
            <span class="text-primary">measurable answers</span>
          </h1>
          <p class="mt-5 max-w-xl text-lg text-slate-300">
            Upload raw drone imagery and get georeferenced orthophotos, elevation
            models, point clouds, and 3D reconstructions — then measure volumes
            and areas directly on the result.
          </p>
          <div class="mt-8 flex flex-wrap items-center gap-3">
            <router-link to="/login">
              <Button size="lg">
                Get started
                <ArrowRight />
              </Button>
            </router-link>
            <a href="#pipeline">
              <Button size="lg" variant="outline" class="border-slate-700 bg-transparent text-slate-100 hover:bg-slate-900">
                See how it works
              </Button>
            </a>
          </div>
        </div>

        <!-- Product panel: a mock of the actual map view, with a slow tile-load
             reveal. Deliberately not a stock aerial photo. -->
        <div class="animate-fade-in">
          <div class="overflow-hidden rounded-xl border border-slate-800 bg-slate-900 shadow-2xl">
            <div class="flex items-center gap-2 border-b border-slate-800 px-4 py-2.5">
              <span class="size-2.5 rounded-full bg-slate-700" />
              <span class="size-2.5 rounded-full bg-slate-700" />
              <span class="size-2.5 rounded-full bg-slate-700" />
              <span class="ml-2 text-xs text-slate-500">orthophoto · EPSG:32615</span>
            </div>
            <div class="relative aspect-[4/3]">
              <img
                src="/assets/webodm_frontend/frontend/images/background.png"
                alt="Georeferenced orthophoto rendered as map tiles"
                class="size-full object-cover"
              />
              <div class="absolute inset-x-4 bottom-4 rounded-lg border border-slate-700 bg-slate-950/80 px-4 py-3 backdrop-blur">
                <p class="text-xs uppercase tracking-wide text-slate-500">Measured area</p>
                <p class="mt-0.5 font-mono text-lg text-primary">2,539 × 2,444 px</p>
                <p class="text-xs text-slate-400">georeferenced output resolution</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Pipeline strip -->
    <section id="pipeline" class="border-b border-border py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          From flight to insight
        </h2>
        <p class="mx-auto mt-3 max-w-2xl text-center text-muted-foreground">
          Four steps, fully automated between them. You upload; the pipeline does
          the reconstruction; you measure the result.
        </p>
        <ol class="mt-14 grid gap-6 md:grid-cols-4">
          <li
            v-for="(stage, i) in pipeline"
            :key="stage.step"
            class="relative rounded-lg border border-border bg-card p-6"
          >
            <span class="font-mono text-xs text-muted-foreground">0{{ i + 1 }}</span>
            <component :is="stage.icon" class="mt-3 size-6 text-primary" />
            <h3 class="mt-4 font-medium text-card-foreground">{{ stage.step }}</h3>
            <p class="mt-2 text-sm text-muted-foreground">{{ stage.body }}</p>
          </li>
        </ol>
      </div>
    </section>

    <!-- Capabilities -->
    <section id="capabilities" class="bg-muted/40 py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          Every output the pipeline produces
        </h2>
        <div class="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <div
            v-for="cap in capabilities"
            :key="cap.title"
            class="rounded-lg border border-border bg-card p-6 transition-transform hover:-translate-y-0.5"
          >
            <component :is="cap.icon" class="size-6 text-primary" />
            <h3 class="mt-4 font-medium text-card-foreground">{{ cap.title }}</h3>
            <p class="mt-2 text-sm text-muted-foreground">{{ cap.body }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Outcome band.
         GLOBAL CONSTRAINT 7: only the orthophoto resolution is verifiable from
         this codebase. The other two figures are placeholders the user must fill
         in with real numbers — do NOT invent values for them. -->
    <section class="border-y border-border py-16">
      <div class="mx-auto grid max-w-5xl gap-8 px-6 text-center sm:grid-cols-3">
        <div>
          <p class="font-mono text-3xl font-semibold text-foreground">2,539 × 2,444</p>
          <p class="mt-1 text-sm text-muted-foreground">
            px georeferenced orthophoto, from 18 source images
          </p>
        </div>
        <div>
          <p class="font-mono text-3xl font-semibold text-foreground">TODO(user)</p>
          <p class="mt-1 text-sm text-muted-foreground">
            typical turnaround per mission — fill in a real measured figure
          </p>
        </div>
        <div>
          <p class="font-mono text-3xl font-semibold text-foreground">TODO(user)</p>
          <p class="mt-1 text-sm text-muted-foreground">
            hectares processed to date — fill in a real figure
          </p>
        </div>
      </div>
    </section>

    <!-- Pricing -->
    <section id="pricing" class="py-20">
      <div class="mx-auto max-w-6xl px-6">
        <h2 class="text-center text-3xl font-semibold tracking-tight text-foreground">
          Simple, transparent pricing
        </h2>
        <p class="mx-auto mt-3 max-w-2xl text-center text-muted-foreground">
          Every plan includes the full processing pipeline. Scale storage and
          credits as your operation grows.
        </p>
        <div class="mx-auto mt-14 grid max-w-5xl gap-6 lg:grid-cols-3">
          <div
            v-for="plan in plans"
            :key="plan.name"
            class="relative flex flex-col rounded-lg border bg-card p-8"
            :class="plan.featured ? 'border-primary ring-1 ring-primary' : 'border-border'"
          >
            <Badge
              v-if="plan.featured"
              variant="default"
              class="absolute -top-3 left-1/2 -translate-x-1/2 bg-primary text-primary-foreground"
            >
              Most popular
            </Badge>
            <h3 class="text-lg font-medium text-card-foreground">{{ plan.name }}</h3>
            <p class="mt-2 text-sm text-muted-foreground">{{ plan.description }}</p>
            <div class="mt-6 mb-8">
              <span class="text-3xl font-semibold tracking-tight text-card-foreground">
                {{ plan.price }}
              </span>
              <span class="text-sm text-muted-foreground">/{{ plan.period }}</span>
            </div>
            <ul class="mb-8 flex-1 space-y-3">
              <li
                v-for="feature in plan.features"
                :key="feature"
                class="flex items-start gap-2 text-sm text-muted-foreground"
              >
                <Check class="mt-0.5 size-4 flex-shrink-0 text-primary" />
                {{ feature }}
              </li>
            </ul>
            <router-link to="/login" class="block">
              <Button class="w-full" :variant="plan.featured ? 'default' : 'secondary'">
                Get started
              </Button>
            </router-link>
          </div>
        </div>
      </div>
    </section>

    <!-- Final CTA -->
    <section class="bg-slate-950 py-20 text-slate-100">
      <div class="mx-auto max-w-3xl px-6 text-center">
        <h2 class="text-3xl font-semibold tracking-tight">Put your imagery to work</h2>
        <p class="mt-4 text-slate-300">
          Create an organization, upload a mission, and measure the result.
        </p>
        <router-link to="/login" class="mt-8 inline-block">
          <Button size="lg">
            Get started
            <ArrowRight />
          </Button>
        </router-link>
      </div>
    </section>

    <footer class="border-t border-border py-8">
      <div class="mx-auto max-w-6xl px-6 text-center text-sm text-muted-foreground">
        &copy; {{ year }} G20 Tech. All rights reserved.
      </div>
    </footer>
  </div>
</template>
```

- [ ] **Step 2: Verify the `TODO(user)` placeholders are present and the brand is intact**

```bash
grep -c "TODO(user)" src/pages/Landing.vue
grep -c "G20 Tech" src/pages/Landing.vue
grep -n "frappe-ui\|FeatherIcon" src/pages/Landing.vue
```

Expected: `2` TODO placeholders, at least `2` occurrences of `G20 Tech`, and no
frappe-ui output. If you replaced either `TODO(user)` with a number you made up,
you violated Global Constraint 7 — put the placeholder back.

- [ ] **Step 3: Confirm animations respect reduced motion**

The two animation utilities used (`animate-slide-up`, `animate-fade-in`) come
from the Tailwind config in Task 1, and the global reduced-motion block in
`src/index.css` neutralizes them. Verify that guard is still present:

```bash
grep -n "prefers-reduced-motion" src/index.css
```

Expected: one match. No parallax was introduced (Global Constraint 8).

- [ ] **Step 4: Verify the suite and the build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed`, build exits 0.

- [ ] **Step 5: Restore the clobbered build output**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: nothing under `webodm_frontend/public/frontend` listed.

- [ ] **Step 6: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/Landing.vue
git commit -m "feat(landing): redesign as DaaS marketing page led by the pipeline

Unverifiable figures are left as explicit TODO(user) placeholders rather
than invented.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Removal gate — delete frappe-ui

**Definition of done: a repo-wide grep for `frappe-ui` returns zero hits.**

This task also settles the long-standing production-build blocker: the
`indexHtmlPath is required` failure comes from the `frappeui()` vite plugin,
which this task removes. Whether production `vite build` then succeeds MUST be
reported either way.

**Files:**
- Modify: `package.json`
- Modify: `vite.config.js`
- Modify: `src/main.js`
- Modify: `src/App.vue`
- Delete: `components.d.ts`
- Delete: `auto-imports.d.ts`

**Interfaces:**
- Consumes: everything from Tasks 1–14.
- Produces: a frappe-ui-free build.

- [ ] **Step 1: Confirm every source file is already migrated**

Before removing anything, prove nothing still imports it:

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
grep -rn "frappe-ui" src/
```

Expected: **no output.** If any file still matches, stop — that page's
migration task was not finished. Fix it before continuing.

- [ ] **Step 2: Remove `FrappeUI` from `src/main.js`**

Delete the import line `import { FrappeUI } from 'frappe-ui'` and the
`app.use(FrappeUI, { socketio: false })` line. The bottom of the file becomes:

```js
const app = createApp(App)
app.use(router)
app.mount('#app')
```

- [ ] **Step 3: Remove `FrappeUIProvider` from `src/App.vue`**

Delete the `import { FrappeUIProvider } from 'frappe-ui'` line and unwrap the
template — replace the `<FrappeUIProvider>` / `</FrappeUIProvider>` wrapper
element with a plain fragment-less root. Since a Vue template may have multiple
roots in Vue 3, the template becomes:

```vue
<template>
  <div
    class="fixed left-0 right-0 top-0 z-[99999] h-0.5 transition-opacity duration-200"
    :class="navigating ? 'opacity-100' : 'opacity-0'"
  >
    <div class="h-full animate-pulse bg-primary"></div>
  </div>
  <Toaster
    position="bottom-right"
    :toast-options="{
      class: 'bg-card text-card-foreground border border-border rounded-lg shadow-lg',
    }"
  />
  <AppLayout v-if="route.meta.layout !== false" />
  <router-view v-else v-slot="{ Component }">
    <Transition name="page" mode="out-in">
      <component :is="Component" />
    </Transition>
  </router-view>
</template>
```

Leave the entire `<script setup>` block as-is apart from dropping the
frappe-ui import.

- [ ] **Step 4: Clean up `vite.config.js`**

Remove the `frappeui` import, the `frappeui({...})` plugin entry, the unused
`Icons` plugin (nothing imports `~icons/` — verified), and the frappe-ui
`optimizeDeps` entries. The file becomes:

```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  base: '/assets/webodm_frontend/frontend/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  build: {
    outDir: '../webodm_frontend/public/frontend',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: path.resolve(__dirname, 'index.html'),
    },
  },
  server: {
    port: 8081,
    allowedHosts: ['webodm.local'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        headers: { Host: 'webodm.local' },
      },
      '/private': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        headers: { Host: 'webodm.local' },
      },
    },
  },
})
```

- [ ] **Step 5: Uninstall the dead dependencies**

`unplugin-icons` and `@iconify-json/lucide` go too — they were only ever wired
up by the frappe-ui plugin and no source file imports from them.

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npm uninstall frappe-ui unplugin-icons @iconify-json/lucide
```

- [ ] **Step 6: Delete the stale generated type files**

These were emitted by `unplugin-vue-components` / `unplugin-auto-import`, which
are no longer in the build:

```bash
rm -f components.d.ts auto-imports.d.ts
```

- [ ] **Step 7: The gate — repo-wide grep must return zero hits**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
grep -rn "frappe-ui" frontend/src frontend/index.html frontend/vite.config.js \
     frontend/vitest.config.js frontend/tailwind.config.js frontend/package.json
```

Expected: **no output.** This is the definition of done for the whole plan.

(`package-lock.json` may still mention transitive URLs; the gate covers source
and config, not the lockfile.)

- [ ] **Step 8: Verify the suite and the development build**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run
npx vite build --mode development
```

Expected: vitest `75 passed`, build exits 0.

- [ ] **Step 9: Test whether the production build blocker is resolved**

This is the payoff check. Run it and record the actual result:

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vite build
echo "PRODUCTION BUILD EXIT CODE: $?"
```

Expected: exit 0, ending in `✓ built in Ns`. The
`[frappeui-build-config-plugin] indexHtmlPath is required` error must be gone,
because the plugin that raised it is gone.

**Report the real outcome in your report either way.** If it still fails, do NOT
attempt a fix in this task — capture the full error text and report it as a
follow-up. A different, newly-revealed build problem is a finding, not a
failure of this task.

- [ ] **Step 10: Restore the clobbered build output**

Both builds above wrote to the tracked asset directory:

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend
git clean -fdq webodm_frontend/public/frontend
git status --short
```

Expected: the only listed changes are the files this task intentionally edited
or deleted — nothing under `webodm_frontend/public/frontend`.

- [ ] **Step 11: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/package.json frontend/package-lock.json frontend/vite.config.js \
        frontend/src/main.js frontend/src/App.vue
git rm --cached -q frontend/components.d.ts frontend/auto-imports.d.ts 2>/dev/null || true
git add -u frontend/components.d.ts frontend/auto-imports.d.ts 2>/dev/null || true
git commit -m "chore: remove frappe-ui and its dead build plugins

Repo-wide grep for frappe-ui now returns zero hits in source and config.
Also drops unplugin-icons and @iconify-json/lucide, which were never
imported by any source file.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after Task 15)

Run the whole gate one more time from a clean tree and record the results:

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend
npx vitest run                      # expect: 75 passed (43 original + 32 new)
npx vite build                      # expect: exit 0 — the blocker is gone
grep -rn "frappe-ui" src/ *.js *.json *.html   # expect: no output
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git checkout -- webodm_frontend/public/frontend && git clean -fdq webodm_frontend/public/frontend
```

Then walk the app manually and confirm:
- The top tab bar highlights the right tab on every route, including after a
  hard refresh on a deep link like `/project/<id>/task/<taskId>/console`.
- The secondary Map / 3D Model / Console row appears **only** on project-detail
  routes.
- The map still fills the viewport, and measurement (distance / area / volume)
  still works.
- Light, dark, and system themes all render correctly — no unreadable text.
- Below `md`, the primary tabs collapse into the menu button.
- Landing, Login, and Onboarding render with no app chrome.
