# MapView Features — Design

**Date:** 2026-07-15
**Status:** Approved (design)
**Scope:** Add five interactive features to the WebODM MapView page.

## 1. Overview

MapView currently shows an OpenStreetMap basemap, GPS image markers, and raster
tile overlays (orthophoto/DSM/DTM) with a simple checkbox Layers panel. This
design adds:

1. **Measurement** — distance (polyline) and area (polygon) with live labels.
2. **Basemap switcher** — OSM Streets / Esri Satellite / Esri Topographic.
3. **Drone flight path** — polyline through image capture points, ordered by
   EXIF capture time.
4. **Layer opacity + controls** — per-raster show/hide and opacity.
5. **Image-markers toggle** — show/hide GPS markers.

The work is frontend-heavy. The only backend change is widening EXIF extraction
to capture per-image altitude and capture time (to order the flight path).

## 2. Architecture

```
MapView.vue  (orchestration + Layout-A UI)
  ├── src/lib/mapLayers.js      basemap definitions + createBasemap()
  ├── src/lib/format.js         formatDistance() / formatArea()  (metric+imperial)
  └── src/composables/useMeasure.js   click-to-draw measure engine (Turf math)

webodm_core/api/task.py
  └── _extract_photo_meta()     lat/lng + altitude + capture_time from EXIF
WebODM Task Image DocType
  └── + capture_time (Datetime, read-only), altitude (Float, read-only)
```

- **Map engine:** Leaflet 1.9.4 (already used). No new map framework.
- **New dependency:** `@turf/turf` (frontend) for geodesic length/area. No new
  backend dependencies.
- **Data flow:** `MapView` already fetches the full task (images with lat/lng).
  `capture_time`/`altitude` ride along in that same payload — no new API
  endpoint. Flight path and markers derive from the in-memory images array.
  Basemaps and measurement are pure client-side.

### Layout (approved: Option A)

- **Top-right stacked panel** (collapsible): Basemap radio → Layers (per-raster
  checkbox + opacity range) → Show toggles (Image markers, Flight path).
- **Top-left measurement toolbar:** Distance / Area / Clear buttons + live
  readout.
- Existing task sidebar (left) is unchanged.

## 3. Components & Interfaces

### 3.1 Backend

**`WebODM Task Image` DocType** — add two read-only, nullable fields:
`capture_time` (Datetime), `altitude` (Float). Applied via `bench migrate`;
existing rows backfill NULL.

**`_extract_gps` → `_extract_photo_meta`** (`webodm_core/api/task.py`) — widen the
existing helper to return `{lat, lng, altitude, capture_time}` from a single
`Image.open`:
- `altitude` from GPS IFD `GPSAltitude` (tag 6), sign from `GPSAltitudeRef`.
- `capture_time` from EXIF `DateTimeOriginal` (tag 36867), parsed to a Frappe
  datetime string.
- Each field independently optional; a malformed/absent tag yields `None` and
  never blocks upload (reuse existing try/except-per-field pattern).
`upload_images` writes the extra fields to the image row when present.

### 3.2 Frontend

**`src/lib/mapLayers.js`** — pure config/factory. Exports `BASEMAPS` (array of
`{id, label, url, attribution, maxZoom}` for `osm`, `esri-satellite`,
`esri-topo`) and `createBasemap(id) → L.tileLayer`. No state.

**`src/composables/useMeasure.js`** — measurement engine, decoupled from Vue
render. Given a Leaflet `map`, exposes:
- `start(mode)` where mode is `'distance' | 'area'`
- `finish()`, `clear()`
- reactive `result` = `{ mode, value, formatted }` (value in metres or m²)

Owns click/dblclick/Esc handlers, the in-progress `L.polyline`/`L.polygon`,
vertex dots, and a live tooltip. Uses `@turf/length` and `@turf/area`. Fully
tears down handlers/layers on `clear()` and on component unmount. Knows nothing
about the panel UI.

**`src/lib/format.js`** — `formatDistance(m)` → m/km; `formatArea(m2)` → m²/ha
and acres. Pure, unit-testable.

**`MapView.vue`** — orchestration only. Adds Layout-A UI and thin functions:
- `setBasemap(id)` — swap base layer, preserve overlays/measurements.
- `setOverlayOpacity(key, value)` / existing `toggleOverlay`.
- `toggleMarkers()` — show/hide the image-marker feature group.
- `buildFlightPath(images)` — sort images (below), render `L.polyline` +
  numbered start/end markers + direction arrowheads (CSS `L.divIcon`); toggle
  add/remove.

**Flight-path ordering:** sort by `capture_time` when present; fall back to
natural-numeric filename sort (so `DJI_0018 < DJI_0100`). If only some images
have times, timed ones order first (by time), then the rest by filename.

## 4. Edge Cases & Error Handling

- **No-GPS task** (e.g. pre-EXIF-fix tasks): flight-path and marker toggles are
  disabled with a hint; nothing renders; no errors.
- **Missing `capture_time`:** filename fallback (above).
- **Old tasks without new fields:** nullable read-only fields; migrate backfills
  NULL; tasks keep working without altitude/time.
- **Basemap tiles fail (Esri offline/rate-limited):** blank base tiles only;
  orthophoto overlay and measurements unaffected. Switching basemap never drops
  overlays.
- **Degenerate measurement:** single-point distance / <3-point area reads 0 or is
  ignored on finish. Esc/Clear fully removes layers and map click listeners.
- **Opacity:** persists per-layer within a session; re-selecting a task resets to
  defaults (orthophoto 100%).
- **Backend EXIF:** malformed tag → `None`, never a failed upload. No new API
  surface, so no new auth/permission paths.

## 5. Testing

- **Backend:** unit-test `_extract_photo_meta` against (a) a fixture image with
  known GPS/altitude/time and (b) a stripped image (all `None`). Frappe test
  runner.
- **Frontend pure logic:** Vitest for `format.js` conversions and the flight-path
  sort (deterministic). If Vitest is not yet wired up in the frontend, note it
  and provide manual verification steps as a fallback.
- **Manual/E2E (Playwright):** reuse the Brighton Beach task — measurement sanity
  against the known ~187 m × 124 m extent, basemap switching, flight-path
  ordering, opacity sliders, and toggles.

## 6. Out of Scope (YAGNI)

Deferred to possible follow-up specs:
- Vertex drag-editing of measurements.
- Persisting measurements/annotations to the DB.
- 3D/altitude visualization of the flight path.
- Colormap / hillshade / HSV raster controls.
- KMZ / measurement export.

## 7. Dependencies & Migration

- Add `@turf/turf` to `webodm_frontend/frontend/package.json`.
- `bench migrate` to add the two DocType fields.
- No changes to the geospatial service or tile proxy.
