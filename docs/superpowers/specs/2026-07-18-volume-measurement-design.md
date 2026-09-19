# Volume Measurement — Design

**Date:** 2026-07-18 (amended 2026-08-15)
**Status:** Implemented; amended — see §8 Amendment
**Scope:** Add a third measurement mode — volume (m³) — to the WebODM MapView,
alongside the existing distance and area measurements.

> **Amendment 2026-08-15.** The original design asserted that a best-fit tilted
> plane was "WebODM's default behavior". **That was wrong** — WebODM's measure
> plugin defaults to `triangulate`. Base-method selection has since been added
> with `triangulate` as the default, plus linear-unit scaling. Sections 1–7 below
> are the original design and retain the incorrect claim in context; **§8 is
> authoritative** where they disagree.

## 1. Overview

MapView's measurement toolbar currently offers **Distance** and **Area** — both
pure client-side geometry over lat/lng points. **Volume** is fundamentally
different: it requires real elevation values from the task's **DSM** (Digital
Surface Model), which the browser does not have (it only receives rendered RGB
tiles). Volume is therefore a **server-side computation over the DSM raster**,
exposed as one new geospatial endpoint and proxied through Frappe, reusing the
existing tile-proxy pattern.

Decisions locked during design:

- **Base plane:** best-fit (tilted) plane fitted through DSM elevations sampled
  at the polygon's boundary vertices — WebODM's default behavior. Fill is above
  the plane, cut is below.
  > ❌ **Superseded (§8):** the "WebODM's default behavior" claim is incorrect.
  > WebODM defaults to `triangulate`; best-fit plane is its `plane` option.
- **Elevation source:** DSM only. Volume is unavailable (button disabled) when
  the task has no DSM.
- **Compute location:** a new `POST /volume` endpoint on the geospatial FastAPI
  service; the frontend reaches it through the session-authed Frappe tile proxy.
- **Integration algorithm:** per-pixel Riemann sum (Option A) — matches ODM,
  reuses rasterio + numpy already in the geospatial venv, accuracy bounded by
  DSM resolution.
- **Drawing UX:** a third mode on the existing toolbar; draws like Area
  (polygon, double-click to finish), then calls the backend.
- **Readout:** net volume + fill + cut + ground area.

## 2. Architecture

```
MapView.vue  (measurement toolbar: Distance | Area | Volume | Clear)
  └── useMeasure.js  — extended with a 'volume' mode: draws a polygon like
                       'area', but on finish() calls an injected onVolume(latlngs)
                       callback and shows the returned readout.

Frontend --POST--> Frappe tile proxy (same-origin, session-authed)
  webodm_core/api/tiles.py :: volume(task_name, polygon)
     - resolves the task's DSM to an absolute path (existing _resolve_raster_path)
     - throws DoesNotExistError if the task has no DSM
     - forwards { path, polygon } to the geospatial service

Frappe --POST--> geospatial FastAPI
  app/routers/volume.py :: POST /volume  { path, polygon (GeoJSON, EPSG:4326) }
     └── app/utils/volume.py :: compute_volume(path, polygon_4326)
            - reproject polygon 4326 -> raster CRS
            - rasterio.mask the DSM to the polygon (crop, unfilled)
            - fit best-fit plane from boundary-vertex elevations
            - per-pixel Riemann sum -> fill, cut, net volume, area
     returns { volume, fill, cut, area, base_plane }
```

**Data flow:** the frontend already holds the drawn polygon as Leaflet lat/lng
points. On finishing a volume polygon, `useMeasure` passes those coordinates to
a `computeVolume(latlngs)` callback supplied by `MapView.vue`, which POSTs to the
Frappe proxy. The proxy resolves the DSM path (enforcing task read-permission via
`frappe.get_doc`) and forwards to the geospatial `/volume` endpoint. All raster
work stays in Python where rasterio/GDAL live; the browser only ever sends
coordinates and receives four numbers.

**Why this shape:** it reuses `_resolve_raster_path` and the same-origin
session-authed proxy verbatim, keeps the browser free of elevation data, and
isolates the volume math in one testable function (`compute_volume`) that takes a
path + polygon and returns numbers.

## 3. Components & Interfaces

### 3.1 Geospatial service (Python 3.12 venv, rasterio/numpy already present)

**`app/utils/volume.py`** (new):
```python
def compute_volume(path: str, polygon_4326: dict) -> dict
```
- `polygon_4326`: GeoJSON Polygon, ring of `[lng, lat]` in EPSG:4326.
- Opens the DSM with rasterio; verifies the raster CRS is **projected** (ODM
  output is UTM); if geographic, raises an error the API surfaces.
- Reprojects the polygon ring 4326 → raster CRS (`rasterio.warp.transform_geom`).
- `rasterio.mask.mask(ds, [geom], crop=True, filled=False)` → masked elevation
  window + affine transform. Nodata and outside-polygon cells are masked out.
- **Base plane:** sample DSM elevation at each polygon boundary vertex (raster
  CRS, via the dataset affine); least-squares fit `z = a·x + b·y + c`
  (numpy `lstsq`). Degenerate fit (collinear / too few valid samples) → flat
  plane at the mean boundary elevation, flagged `base_plane:"mean_fallback"`.
- **Riemann sum:** cell ground area = `abs(transform.a * transform.e)` (pixel
  width × height in projected units). For each unmasked cell,
  `diff = dsm − plane(x, y)`; accumulate
  `fill += max(diff, 0) * cellArea`, `cut += max(−diff, 0) * cellArea`.
  `volume = fill − cut`; `area = valid_cell_count * cellArea`.
- Returns `{ "volume": float, "fill": float, "cut": float, "area": float,
  "base_plane": "best_fit" | "mean_fallback" | "empty" }`, m³ / m².
- Zero valid cells (polygon outside coverage / all-nodata) →
  `{volume:0, fill:0, cut:0, area:0, base_plane:"empty"}`.

**`app/routers/volume.py`** (new): `POST /volume` with a pydantic body
`{ path: str, polygon: dict }`. Validates the raster exists by importing and
calling the existing `app.routers.tiles._require_raster(path)` (module-level
helper: rejects non-absolute paths with 400, missing files with 404), then calls
`compute_volume`, returns the dict. Registered in `app/main.py` alongside the
existing routers.

### 3.2 Frappe proxy — `webodm_core/api/tiles.py`

Add:
```python
@frappe.whitelist(allow_guest=False)
def volume(task_name, polygon)
```
- `polygon` arrives as a JSON string (POST body); parse with `frappe.parse_json`.
- Resolve the DSM with the existing `_resolve_raster_path(task_name, "dsm")`
  (throws `DoesNotExistError` if the task has no DSM — defense in depth beyond the
  disabled button).
- `requests.post(f"{_geospatial_url()}/volume", json={"path": path,
  "polygon": polygon}, timeout=120)`; raise for status; return `resp.json()`.
- Lives next to `serve`/`info`; dataset is fixed to `dsm`.

### 3.3 Frontend

**`src/composables/useMeasure.js`** — extend the mode union to
`'distance' | 'area' | 'volume'`. Volume draws exactly like area (polygon,
double-click to finish). Signature becomes `useMeasure(getMap, { onVolume } = {})`
— `onVolume` is an optional async callback `(latlngs) => Promise<string>`; when
absent, distance/area behave unchanged. On `finish()` in volume mode:
- if `< 3` points, ignore (same as area);
- otherwise set `state.formatted = 'Computing…'`, capture a request token, call
  `onVolume(points)`, and on resolve — only if the mode is still `volume` and the
  token still current — write the returned string into `state.formatted`
  (`state.value` holds the net volume). On reject, `state.formatted = 'Volume failed'`.

**`src/pages/MapView.vue`**:
- `currentTask` ref — set to the full task record inside `selectTask` (right
  where `currentImages` is already set). MapView currently keeps only
  `selectedTask` (a task **name** string) plus the fetched `full` object; volume
  needs the record's `dsm` field, so store it in a `currentTask` ref alongside
  `currentImages`.
- `hasDsm` computed — `!!currentTask.value?.dsm`; mirrors the existing `hasGps`
  disabled-toggle pattern.
- `computeVolume(latlngs)` — builds a GeoJSON Polygon ring (`[lng, lat]`, closed),
  POSTs to `webodm_core.api.tiles.volume` with `task_name` + `polygon` (session
  cookie / CSRF as in existing calls), and returns `formatVolume(result)`.
- Instantiates `useMeasure(() => map, { onVolume: computeVolume })`.
- Adds a **Volume** toolbar button, disabled + greyed when `!hasDsm`, active-state
  styled like Distance/Area.

**`src/lib/format.js`** — add `formatVolume({ volume, fill, cut, area })` → e.g.
`"1,240 m³ (fill 1,310 / cut 70) · 890 m²"`. Pure, unit-tested. Handles net-cut
(negative volume) and zero/empty results.

## 4. Edge Cases & Error Handling

- **No DSM on task:** Volume button disabled + greyed (`hasDsm`); proxy also
  throws `DoesNotExistError` if invoked anyway.
- **Polygon partly/fully outside DSM coverage or all-nodata:** endpoint returns
  the `empty` result; readout shows "No DSM data under polygon", not an error.
- **Degenerate polygon (<3 points):** `useMeasure` ignores the finish; no backend
  call.
- **Degenerate base-plane fit:** fall back to mean-elevation flat plane
  (`mean_fallback`).
- **Backend/network failure or timeout:** `computeVolume` catches, `toast.error`s,
  sets readout to "Volume failed"; the drawn polygon stays for retry.
- **DSM not in a projected CRS:** endpoint errors; UI surfaces
  "DSM not in a projected CRS" (unlikely for ODM/UTM output).
- **Task switch / new measurement mid-compute:** `measure.clear()` runs on task
  change; a stale in-flight response is discarded via the mode + request-token
  guard.

## 5. Testing

- **Geospatial unit tests (pytest):** `compute_volume` against synthetic DSM
  fixtures written with rasterio —
  (a) flat raster over a polygon → volume ≈ 0;
  (b) known-height rectangular block above a flat base → volume ≈ height × area
  within tolerance;
  (c) polygon fully outside coverage → `empty` result;
  (d) collinear boundary → `mean_fallback`.
  Deterministic; no ODM data required.
- **Frappe proxy (Frappe test runner):** `volume()` throws when the task has no
  DSM (mock task); forwards path + polygon (mock `requests.post`).
- **Frontend pure logic (Vitest):** `formatVolume` — normal, net-cut (negative),
  and zero/empty cases; thousands separators.
- **Manual/E2E:** a task with a DSM — draw a polygon over a known feature,
  confirm a plausible m³ and the "Computing…" → result transition; confirm the
  Volume button is disabled on a DSM-less task.

## 6. Out of Scope (YAGNI)

Deferred to possible follow-ups:
- Custom/typed base elevation or alternate base-plane methods (fixed to best-fit).
- DTM-based volume; per-measurement elevation-source selection.
- Volume export / reporting; cross-section profiles.
- Persisting measured volumes to the DB.

## 7. Dependencies & Migration

- **No new dependencies.** Geospatial service already has rasterio + numpy;
  frontend adds no packages (reuses the existing draw engine and fetch pattern).
  > ⚠️ **Superseded (§8):** `triangulate` needs `scipy` in the geospatial venv.
- **No DocType/schema changes**, no migration. The DSM field already exists on
  `WebODM Task`.
- **New files:** `app/utils/volume.py`, `app/routers/volume.py` (geospatial);
  changes to `app/main.py`, `webodm_core/api/tiles.py`,
  `src/composables/useMeasure.js`, `src/pages/MapView.vue`, `src/lib/format.js`.

---

## 8. Amendment 2026-08-15 — Base methods & WebODM parity

**Authoritative** where it conflicts with §1–7.

### 8.1 What the original design got wrong

§1 claimed best-fit plane is "WebODM's default behavior". Verified against
WebODM source at `coreplugins/measure/volume.py` (`calc_volume`) and
`coreplugins/measure/api.py`:

- WebODM's default is **`triangulate`** — `scipy.interpolate.griddata(...,
  method='linear')`, i.e. a TIN through the boundary-vertex elevations. It is the
  default in the serializer (`api.py:15`) and in the UI
  (`MeasurePopup.jsx:33`, persisted in `localStorage` under `measure_base_method`).
- Best-fit plane is WebODM's **`plane`** option (`scipy.optimize.curve_fit` on
  `m1·x + m2·y + b`), mathematically equivalent to our `lstsq` fit.

Consequence of the error: on non-planar ground our number diverged materially
from stock WebODM. Measured on a stockpile over sinusoidal terrain with an
8-vertex polygon — triangulate **1099.30 m³** vs plane **259.15 m³** (WebODM's
own two methods on identical input). A user cross-checking against WebODM would
reasonably conclude one tool was broken.

Also worth recording: **WebODM's volume tool is 2D Leaflet over the DSM raster**,
not the 3D viewer. Potree's `BoxVolume`/`SphereVolume` are point-cloud *clipping*
shapes with no m³ readout. Our architecture already matched theirs.

### 8.2 Base methods (all five, mirroring WebODM)

`compute_volume(path, polygon_4326, base_method="triangulate")`:

| Method | Base surface | Notes |
|---|---|---|
| `triangulate` | Linear TIN through boundary samples | **Default.** Follows uneven ground |
| `plane` | Least-squares tilted plane | The original §1 behavior |
| `average` | Flat at mean boundary elevation | |
| `highest` | Flat at max boundary elevation | |
| `lowest` | Flat at min boundary elevation | |

Invalid method → `ValueError` → HTTP 422. Degenerate `triangulate` (fewer than
3 distinct sample points) falls back to a flat mean base, labelled
`mean_fallback`. `base_plane` in the response now returns the method actually
used, so it is no longer only `best_fit`/`mean_fallback`/`empty`.

**Parity check:** our `triangulate` agrees with WebODM's scipy implementation to
**0.045%** (1099.79 vs 1099.30 m³); the residual is entirely WebODM's
`all_touched=True` boundary ring — 7 extra cells on a 1202-cell polygon.

### 8.3 Linear-unit scaling

Volumes are now multiplied by `to_meter**3` and area by `to_meter**2`.

WebODM reads **band** units only (`ds.units`, via `get_rasterio_to_meters_factor`
in `app/geoutils.py`), which GDAL leaves as `(None,)` even for genuinely
foot-based CRSs such as EPSG:2229 — so WebODM's own foot handling rarely fires.
We check band units first for parity, then fall back to `crs.linear_units`.
Previously a DSM in feet was reported ≈35× too large (`1/0.3048³`).

### 8.4 Deliberate divergences from WebODM (kept)

1. **Signed net + fill/cut.** WebODM returns a single `np.abs(volume)`, so a
   2000 m³ pile and a 2000 m³ pit are indistinguishable. We keep signed net plus
   separate `fill`/`cut`.
2. **Convex-hull holes filled from the nearest sample.** Cells outside the TIN's
   hull come back NaN; WebODM drops them via `nansum`, silently shrinking the
   measured area. We fill from the nearest boundary sample so the whole polygon
   is integrated.
3. **`all_touched=False`** (rasterio default). WebODM uses `True`, including any
   boundary-touched pixel — ~2.5% more area on a 40×40 m polygon, so WebODM's
   volumes run marginally larger. Matters most for small or thin polygons.
4. **Out-of-bounds vertices.** WebODM raises `"Points are out of bounds"`; we
   accept the polygon and fall back to `mean_fallback`.

### 8.5 Boundary-vertex sampling (documenting shipped behavior)

The implementation deviates from the original plan at
`app/utils/volume.py:79-97`, and this was previously undocumented. The plan
sampled boundary vertices only where `not mask_arr[r, c]`, but boundary vertices
legitimately land on edge pixels that `crop=True` masks out despite carrying
valid DSM data (`filled=False` preserves `band.data` under the crop mask).
Sampling `band.data` and rejecting only true nodata avoids frequent spurious
`mean_fallback`.

This also sidesteps a real fragility in WebODM: when its `rowcol` lookup lands on
an `all_touched` NaN cell, `zs` contains NaN → the fitted base is all-NaN →
`nansum` returns **0.0 with no error**, silently reporting zero volume. Observed
while replicating `calc_volume` with pixel-corner-aligned polygons.

### 8.6 API & UI changes

- **Geospatial:** `VolumeRequest` gains `method: str = "triangulate"`; blank is
  coerced to the default (WebODM's serializer likewise allows blank).
- **Frappe proxy:** `volume(task_name, polygon, method=None)` validates against
  `VOLUME_BASE_METHODS` *before* calling the service, so a bad value fails fast
  with a `ValidationError` instead of a downstream error.
- **Frontend:** `src/lib/volumeMethods.js` holds the method list, default, and
  `localStorage` load/save (same `measure_base_method` key as WebODM); unknown
  stored values fall back to the default. MapView shows a `Select` in volume mode
  only. `useMeasure` gains `recomputeVolume()` so changing the method re-measures
  the existing polygon instead of forcing a redraw; it reuses the same
  mode + request-token staleness guard.

### 8.7 New dependency

`scipy>=1.14.0` in the geospatial `requirements.txt` — `griddata` backs
`triangulate`. Still no new frontend packages, no DocType/schema change.
