# 3D Viewer UX Rework — Design Spec

**Date:** 2026-09-22
**Goal:** Make the task 3D viewer (`/project/:id/task/:taskId/model`) reliable, intuitive and usable on any screen, without changing the rendering technology.
**Approach:** Keep Three.js + GLTFLoader/DRACOLoader; restructure into a testable helper module, a composable that owns the scene, and a thin page with an explicit toolbar and states.

---

## Problems in the previous implementation

| Symptom | Cause |
|---|---|
| Terrain rendered as a vertical wall, camera "under" the model | ODM writes the GLB in the projected CRS (Z-up); it was added to a Y-up scene unrotated. |
| Tab crashes / very slow loads on laptops | A survey GLB carries five 8192² and ten 4096² JPEG atlases (~2.1 GB decoded). GLTFLoader decodes every image in parallel at full size. |
| Fans spin while idle | The render loop drew every frame regardless of changes. |
| Stale model or errors after switching tasks | No abort of in-flight downloads, no disposal of geometry/textures/blob URLs, renderer never torn down. |
| Canvas mis-sized after sidebar/fullscreen changes | Only `window.resize` was observed. |
| Users guessed the controls | Single "Reset view" button; no mode controls, no shortcuts, no help. |
| Bare loading text; generic errors; polling never cleared on route change | Ad-hoc state handling in the page. |

## Architecture

```
pages/ModelView.vue            task fetch + polling, dataset switcher, overlays, key routing
  └─ components/ModelToolbar.vue   presentational toolbar (emits intents)
  └─ composables/useModelViewer.js renderer, camera, OrbitControls, load/clear/dispose,
                                   tweens, modes, snapshot()
       └─ lib/modelViewer.js       pure math & maps (framing, presets, keys, empty states)
       └─ lib/textureBudget.js     GLTFLoader plugin: texture cap + sequential decode
```

Everything in `lib/` is unit-tested (vitest, jsdom). The composable is exercised in a real browser by `frontend/e2e/model-viewer.e2e.mjs`.

### Model placement

1. Parse with GLTFLoader (+ Draco, + texture-budget plugin).
2. Bounding box in native space. If the centre exceeds 10⁴ (UTM eastings/northings) the offset is baked into vertex buffers (float64 CPU, once) so the GPU never sees large float32 values. Otherwise the object is offset via its transform. ODM models carry a `CESIUM_RTC` centre so their coordinates are already local; three.js ignores the extension.
3. Wrap in a group rotated −90° about X (Z-up → Y-up). All framing uses the world-space box of that group.
4. Ground grid sized to a "nice" number ≥ 1.5× the footprint, placed at the model's lowest point.
5. Materials: double-sided; `KHR_materials_unlit` (what ODM emits) is excluded from tone mapping so photogrammetry colours are not washed out; anisotropy 8.

### Camera

- Perspective, 50° FOV. `framingFor(size, aspect)` fits the bounding sphere for the current aspect (portrait backs off further). Near/far, min/max orbit distance derive from it.
- OrbitControls: damping 0.12, screen-space panning, zoom to cursor, `maxPolarAngle` slightly above the horizon so the camera never goes under the ground plane.
- Presets (unit directions × fit distance): isometric (default), top-down, facing north, facing east.
- Toolbar/keyboard moves are 250–450 ms eased tweens. Damping is disabled for the tween's duration so it lands exactly; a drag cancels the tween.
- Double-click raycasts the model and re-targets the orbit on the hit point without changing view direction.
- Render on demand: the rAF loop draws only when `controls.update()` reports a change, a tween is active, or `requestRender()` was called.

### Interaction model

| Action | Mouse | Touch | Keyboard |
|---|---|---|---|
| Rotate | left-drag (Rotate mode) | one finger | — |
| Pan | right-drag, or left-drag in Pan mode | one finger in Pan mode / two fingers | ← → ↑ ↓, W A S D |
| Zoom | wheel, middle-drag, left-drag in Zoom mode | pinch | `+` `-` |
| Focus point | double-click | — | — |
| Reset / presets | toolbar | toolbar | `R` / `1` `2` `3` `4` |
| Grid / fullscreen / help | toolbar | toolbar | `G` / `F` / `?` |

The mode buttons only remap the primary button and one-finger gesture; secondary buttons keep their conventional roles. Shortcuts are ignored while typing in form fields and with modifier keys. The stage is focusable (`tabindex=0`) and takes focus on pointer-down.

### Large models

- Download is streamed with byte progress (Content-Length when available).
- **Texture budget** (`lib/textureBudget.js`): before decoding, image headers (PNG/JPEG/WebP) are read from the GLB body; the plugin picks the largest side cap from {8192 … 256} at which RGBA+mipmaps of the whole set fit the budget: 1 GB (≥ 8 GB device memory), 512 MB (≥ 4 GB), else 256 MB, and at most 384 MB on mobile. Textures are decoded two at a time with `createImageBitmap(..., resizeWidth/Height)`. For the reference survey this yields a 2048 px cap (~440 MB instead of ~2.8 GB). Progress reads "Decoding textures n/N".
- Pixel ratio is capped at 1.5 above 1.5 M triangles and 1 above 4 M.
- `clear()` disposes geometry, materials and textures and revokes blob URLs; `dispose()` also tears down controls and the renderer (`forceContextLoss`) so repeated visits do not accumulate GL contexts.
- A lost WebGL context surfaces as an error state with a retry.

### States

| State | Trigger | UI |
|---|---|---|
| Loading | task fetch, download, extract, decode, prepare | spinner, phase label, progress bar + bytes while downloading |
| Processing | task Pending/Queued/Running without a model | spinner, progress %, polls every 5 s and loads automatically when the model appears |
| No model | Completed without `model` | explanation (re-run without `skip-3dmodel`), Console + Back |
| Failed / Cancelled | terminal status without a model | explanation, Console + Back |
| Not found | task fetch fails | Back |
| Error | download/parse failure, WebGL context lost | message, Retry + Back |
| Unsupported | no WebGL | message, Download model + Back |

### Layout

- Header: back, title, status badge, dataset switcher (only when the project has > 1 task with a model), Console, Download. Wraps on narrow widths; labels collapse to icons below `sm`.
- Stage: canvas fills the remaining height (route is `fullBleed`). Toolbar floats top-left in three groups (mode · camera · display) and wraps on phones. Hint bottom-left (pointer-aware wording, auto-hides after 6 s or first interaction). Stats chip bottom-right (hidden on phones; tooltip lists vertices, textures and the texture cap).
- Fullscreen targets the stage so the toolbar stays available.

## Testing

- `npm test` — unit tests for `lib/modelViewer.js` (framing, presets, dolly/pan math, key map, mode maps, zip helpers, empty states, formatting) and `lib/textureBudget.js` (budgets, cap selection, header parsing, limiter).
- `e2e/model-viewer.e2e.mjs` — Playwright against a dev server; asserts through `window.__modelViewer.snapshot()` (dev builds only). Covers load, framing, every toolbar action, drag/wheel/double-click, all shortcuts, mode remapping, hint behaviour, error + retry, not-found, six load/leave cycles, mid-download abort, phone viewport and container resize. See `e2e/README.md`.

## Out of scope / follow-ups

- Point-cloud (LAZ) rendering; measurements; saved camera views.
- Per-user quality override (e.g. "full texture resolution" toggle) on top of the automatic budget.
- Potree is no longer planned for this page; SPEC/TRD updated accordingly.
