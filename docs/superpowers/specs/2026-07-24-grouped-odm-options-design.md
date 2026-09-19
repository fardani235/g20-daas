# Grouped ODM Options in Preset & Upload Dialogs

**Date:** 2026-07-24
**Status:** Approved (design)

## Goal

Render NodeODM's flat list of ~81 processing options as collapsible **category
sections** (WebODM-style) in both the Presets dialog and the upload dialog,
instead of one long ungrouped list.

## Context

- NodeODM's `GET /options` returns ~81 options, each with only
  `name, type, value, domain, help` — **no category/group metadata**. Any
  grouping must be defined by us.
- The identical field-render block (checkbox / select / number / text bound to
  `values[opt.name]`) is currently duplicated in:
  - `src/pages/Presets.vue` (create/edit preset modal)
  - `src/pages/MapView.vue` (upload dialog)
- `src/composables/useOdmOptions.js` already provides `catalog`, `fieldType`,
  `load`, `seedEnumDefaults`. This work does **not** change it.

## Decisions (from brainstorming)

1. **Grouping style:** collapsible categories (not search, not flat).
2. **Mapping source:** a hand-authored static frontend map (category → option
   names), with an `Advanced` catch-all for anything unlisted.
3. **Scope:** both dialogs, via a shared extracted component.
4. **Default expand state:** `General` expanded; all other sections collapsed.

## Architecture

### 1. Category mapping — `src/lib/odmCategories.js`

Owns the static category definitions and a **pure** grouping function.

```js
export const ODM_CATEGORIES = [
  { name: 'General', options: [
    'auto-boundary', 'auto-boundary-distance', 'boundary', 'crop',
    'fast-orthophoto', 'feature-quality', 'feature-type', 'min-num-features',
    'matcher-neighbors', 'matcher-order', 'matcher-type', 'sfm-algorithm',
    'max-concurrency', 'use-exif', 'ignore-gsd',
  ]},
  { name: 'Elevation (DEM)', options: [
    'dsm', 'dtm', 'dem-resolution', 'dem-decimation', 'dem-euclidean-map',
    'dem-gapfill-steps', 'smrf-scalar', 'smrf-slope', 'smrf-threshold',
    'smrf-window',
  ]},
  { name: 'Orthophoto', options: [
    'orthophoto-resolution', 'orthophoto-compression', 'orthophoto-cutline',
    'orthophoto-kmz', 'orthophoto-no-tiled', 'orthophoto-png', 'skip-orthophoto',
    'cog', 'build-overviews', 'tiles',
  ]},
  { name: 'Point Cloud', options: [
    'pc-quality', 'pc-classify', 'pc-copc', 'pc-csv', 'pc-ept', 'pc-filter',
    'pc-las', 'pc-rectify', 'pc-sample', 'pc-skip-geometric',
  ]},
  { name: 'Meshing & Texturing', options: [
    'mesh-octree-depth', 'mesh-size', 'use-3dmesh', 'skip-3dmodel',
    'texturing-keep-unseen-faces', 'texturing-single-material',
    'texturing-skip-global-seam-leveling', '3d-tiles', 'gltf',
  ]},
  { name: 'Georeferencing & Camera', options: [
    'cameras', 'camera-lens', 'force-gps', 'gps-accuracy', 'gps-z-offset',
    'use-fixed-camera-params', 'use-hybrid-bundle-adjustment', 'rolling-shutter',
    'rolling-shutter-readout', 'sfm-no-partial',
  ]},
  { name: 'Radiometric & Multispectral', options: [
    'radiometric-calibration', 'primary-band', 'skip-band-alignment',
    'bg-removal', 'sky-removal',
  ]},
  { name: 'Split-Merge', options: [
    'split', 'split-overlap', 'sm-cluster', 'sm-no-align', 'merge',
  ]},
]

// Group the live catalog into rendered sections.
//  - Preserves ODM_CATEGORIES order.
//  - An option not named in any category falls into a trailing 'Advanced'
//    section (so new/unknown options are never dropped).
//  - Categories (incl. Advanced) with zero matching catalog entries are omitted.
//  - Every catalog option appears exactly once.
// Returns: [{ name: string, options: CatalogEntry[] }]
export function groupOptions(catalog) { /* ... */ }
```

> The exact per-option assignment above is the starting map; minor
> recategorization during implementation is fine. Correctness of the *mechanism*
> (order, catch-all, no drops/dupes, empty-omit) is what the tests pin down —
> not the specific bucket each name lands in.

Remaining ungrouped names (e.g. `end-with`, `rerun-from`, `no-gpu`,
`optimize-disk-space`, `video-limit`, `video-resolution`, `sm-*` leftovers)
naturally fall to **Advanced**.

### 2. Shared component — `src/components/OdmOptionsForm.vue`

Extract the duplicated field block into one component.

**Props:**
- `catalog: Array` — the raw option catalog (`odm.catalog.value`)
- `modelValue: Object` — the values map (`v-model`)
- `fieldType: Function` — passed in from the composable

**Behavior:**
- Calls `groupOptions(catalog)` to get sections.
- Renders each section with native `<details>`/`<summary>` (accessible, no JS
  state needed). `General` gets the `open` attribute; others closed.
- Inside each section, the existing per-option row renders verbatim:
  checkbox / select / number / text, each bound to `modelValue[opt.name]`.
- Writes propagate through `v-model` (`update:modelValue`) — since the values
  object is a shared reactive reference, mutating `modelValue[opt.name]`
  directly matches today's behavior; component keeps parent as source of truth.

### 3. Integration

- **Presets.vue** — replace the `v-for="opt in odm.catalog.value"` block with:
  `<OdmOptionsForm :catalog="odm.catalog.value" v-model="values"
   :field-type="odm.fieldType" />`
- **MapView.vue** upload dialog — replace the parallel block with the same
  component bound to `uploadValues` / `uploadOdm`.
- `useOdmOptions.js`, `toOptionsArray()`, and `seedEnumDefaults()` are
  **unchanged**. Grouping is a pure view concern over the existing catalog, so
  save/submit serialization is byte-for-byte identical.

## Testing (TDD)

`src/lib/odmCategories.test.js` — the real logic:
1. options map into their declared category, preserving category order
2. an unlisted option lands in the `Advanced` catch-all
3. a category with no matching catalog entries is omitted
4. `Advanced` is omitted when every catalog option is mapped
5. every catalog option appears exactly once across all returned sections
   (no drops, no duplicates)

`OdmOptionsForm` — light import/smoke test in the existing vitest setup
(consistent with `useMeasure.import.test.js`); heavy DOM assertions not
warranted for view-only markup.

## Out of Scope (YAGNI)

- No backend change (NodeODM has no category metadata; no new DocType).
- No search box.
- No persistence of expand/collapse state across dialog opens.

## Error Handling / Edge Cases

- Empty or failed catalog → component renders nothing; loading/error states stay
  in the parent (as today).
- New ODM options after an ODM/NodeODM upgrade → automatically surface under
  `Advanced`, never silently lost.
- Known soft spot: the static map is hand-authored, so a newly-added
  everyday option sits in `Advanced` until the map is updated. Accepted — the
  catch-all guarantees nothing disappears.
