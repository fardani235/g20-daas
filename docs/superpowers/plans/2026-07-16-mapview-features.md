# MapView Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five interactive features to the WebODM MapView — distance/area measurement, a basemap switcher, a drone flight path, per-raster opacity controls, and an image-markers toggle.

**Architecture:** Frontend-heavy. Pure logic (`format.js`, `flightPath.js`, `mapLayers.js`) is extracted into `src/lib/` and unit-tested with Vitest; the measurement engine lives in `src/composables/useMeasure.js`; `MapView.vue` is the orchestration layer holding the Layout-A UI. The only backend change widens EXIF extraction to capture per-image `altitude` and `capture_time` (used to order the flight path), stored on two new nullable, read-only fields of the `WebODM Task Image` DocType.

**Tech Stack:** Vue 3 (Composition API), Leaflet 1.9.4, `@turf/turf` (geodesic length/area), Vitest + jsdom (frontend tests), Frappe v16 / Python 3.14 (backend), PIL/Pillow (EXIF), Frappe test runner (backend tests).

## Global Constraints

- Map engine is Leaflet 1.9.4 — no new map framework.
- Only one new frontend runtime dependency: `@turf/turf`. No new backend dependencies.
- No new API endpoint: `capture_time`/`altitude` ride along in the existing full-task payload (`/api/resource/WebODM Task/<name>`). Flight path and markers derive from the in-memory images array.
- Backend EXIF fields are independently optional: a malformed/absent tag yields `None` and never blocks an upload.
- New DocType fields are nullable and read-only; existing rows backfill `NULL` via `bench migrate`.
- Basemaps: OSM Streets, Esri Satellite, Esri Topographic. Layout: Option A (single stacked top-right panel + measurement toolbar top-left). Orthophoto default opacity 100%, shown by default.
- Switching basemap must never drop overlays or measurements.
- Import alias `@` resolves to `frontend/src` (configured in `vite.config.js` and to be mirrored in `vitest.config.js`).

Paths in this plan are relative to the repo root `/home/ridwan/workspace/g20-daas/`. The frontend app root is `frappe-bench/apps/webodm_frontend/frontend/`; the backend app root is `frappe-bench/apps/webodm_core/`.

---

### Task 1: Wire up Vitest for frontend unit tests

The frontend has no test runner. Add Vitest + jsdom so the pure-logic tasks that follow (format, flight-path sort, basemap config) can be test-driven. This task's deliverable is a green run of one trivial smoke test.

**Files:**
- Modify: `frappe-bench/apps/webodm_frontend/frontend/package.json`
- Create: `frappe-bench/apps/webodm_frontend/frontend/vitest.config.js`
- Create: `frappe-bench/apps/webodm_frontend/frontend/src/lib/smoke.test.js` (temporary; deleted in Step 6)

**Interfaces:**
- Produces: an `npm test` script running `vitest run`; a jsdom test environment; `@` → `src` alias available in tests. Later tasks add `src/**/*.test.js` files that this config picks up.

- [ ] **Step 1: Install Vitest and jsdom**

Run (from `frappe-bench/apps/webodm_frontend/frontend/`):
```bash
npm install --save-dev vitest@^2.1.0 jsdom@^25.0.0
```
Expected: `package-lock.json` updated; `vitest` and `jsdom` appear under `devDependencies`.

- [ ] **Step 2: Add the test script to package.json**

In `frappe-bench/apps/webodm_frontend/frontend/package.json`, change the `"scripts"` block from:
```json
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
```
to:
```json
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
```

- [ ] **Step 3: Create the Vitest config**

Create `frappe-bench/apps/webodm_frontend/frontend/vitest.config.js`:
```js
import { defineConfig } from 'vitest/config'
import path from 'path'

// Standalone from vite.config.js so tests don't load the frappe-ui/icons
// build plugins. jsdom is required because src/lib/mapLayers.js imports
// Leaflet, which touches window/document at construction time.
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.js'],
  },
})
```

- [ ] **Step 4: Create a temporary smoke test**

Create `frappe-bench/apps/webodm_frontend/frontend/src/lib/smoke.test.js`:
```js
import { describe, it, expect } from 'vitest'

describe('vitest wiring', () => {
  it('runs a test', () => {
    expect(1 + 1).toBe(2)
  })
})
```

- [ ] **Step 5: Run the smoke test**

Run (from `frappe-bench/apps/webodm_frontend/frontend/`):
```bash
npm test
```
Expected: PASS — `1 passed` for `src/lib/smoke.test.js`.

- [ ] **Step 6: Delete the smoke test and commit**

```bash
cd frappe-bench/apps/webodm_frontend/frontend
rm src/lib/smoke.test.js
git add package.json package-lock.json vitest.config.js
git commit -m "test: wire up Vitest + jsdom for frontend unit tests"
```

---

### Task 2: `format.js` — human-readable distance/area formatters

Pure functions used by the measurement engine and its live readout.

**Files:**
- Create: `frappe-bench/apps/webodm_frontend/frontend/src/lib/format.js`
- Test: `frappe-bench/apps/webodm_frontend/frontend/src/lib/format.test.js`

**Interfaces:**
- Produces: `formatDistance(meters: number) → string` and `formatArea(m2: number) → string`. Consumed by `useMeasure.js` (Task 7).

- [ ] **Step 1: Write the failing test**

Create `frappe-bench/apps/webodm_frontend/frontend/src/lib/format.test.js`:
```js
import { describe, it, expect } from 'vitest'
import { formatDistance, formatArea } from '@/lib/format'

describe('formatDistance', () => {
  it('shows metres under 1 km', () => {
    expect(formatDistance(500)).toBe('500.0 m')
  })
  it('shows kilometres at or above 1 km', () => {
    expect(formatDistance(1500)).toBe('1.50 km')
  })
  it('clamps invalid input to zero', () => {
    expect(formatDistance(-5)).toBe('0.0 m')
    expect(formatDistance(NaN)).toBe('0.0 m')
  })
})

describe('formatArea', () => {
  it('shows square metres with acres under 1 ha', () => {
    const s = formatArea(5000)
    expect(s).toContain('5000.0 m²')
    expect(s).toContain('ac')
  })
  it('shows hectares with acres at or above 1 ha', () => {
    const s = formatArea(20000)
    expect(s).toContain('2.00 ha')
    expect(s).toContain('ac')
  })
  it('clamps invalid input to zero', () => {
    expect(formatArea(-1)).toBe('0.0 m²')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frappe-bench/apps/webodm_frontend/frontend/`):
```bash
npm test -- format
```
Expected: FAIL — cannot resolve `@/lib/format`.

- [ ] **Step 3: Write minimal implementation**

Create `frappe-bench/apps/webodm_frontend/frontend/src/lib/format.js`:
```js
// Human-readable formatting for map measurements. Pure, no dependencies.

const ACRES_PER_M2 = 1 / 4046.8564224

export function formatDistance(meters) {
  const m = Number.isFinite(meters) && meters > 0 ? meters : 0
  if (m < 1000) return `${m.toFixed(1)} m`
  return `${(m / 1000).toFixed(2)} km`
}

export function formatArea(m2) {
  const a = Number.isFinite(m2) && m2 > 0 ? m2 : 0
  const acres = a * ACRES_PER_M2
  if (a < 10000) return `${a.toFixed(1)} m² (${acres.toFixed(2)} ac)`
  return `${(a / 10000).toFixed(2)} ha (${acres.toFixed(2)} ac)`
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
npm test -- format
```
Expected: PASS — all 6 assertions green.

- [ ] **Step 5: Commit**

```bash
cd frappe-bench/apps/webodm_frontend/frontend
git add src/lib/format.js src/lib/format.test.js
git commit -m "feat: add distance/area formatters for measurement"
```

---

### Task 3: `flightPath.js` — order images for the flight path

Pure ordering logic: prefer EXIF `capture_time`, fall back to natural-numeric filename order, and drop images without usable GPS.

**Files:**
- Create: `frappe-bench/apps/webodm_frontend/frontend/src/lib/flightPath.js`
- Test: `frappe-bench/apps/webodm_frontend/frontend/src/lib/flightPath.test.js`

**Interfaces:**
- Consumes: image objects shaped `{ filename, latitude, longitude, capture_time }` (subset of a `WebODM Task Image` row).
- Produces: `sortImagesByCapture(images: object[]) → object[]` — GPS-bearing images only, timed ones first (ascending time), then untimed by natural filename order. Consumed by `buildFlightPath` in `MapView.vue` (Task 9).

- [ ] **Step 1: Write the failing test**

Create `frappe-bench/apps/webodm_frontend/frontend/src/lib/flightPath.test.js`:
```js
import { describe, it, expect } from 'vitest'
import { sortImagesByCapture } from '@/lib/flightPath'

const gps = { latitude: 40.5, longitude: -73.9 }

describe('sortImagesByCapture', () => {
  it('orders timed images by capture_time ascending', () => {
    const out = sortImagesByCapture([
      { filename: 'b.jpg', capture_time: '2024-05-01 10:05:00', ...gps },
      { filename: 'a.jpg', capture_time: '2024-05-01 10:01:00', ...gps },
    ])
    expect(out.map(i => i.filename)).toEqual(['a.jpg', 'b.jpg'])
  })

  it('orders untimed images by natural filename order', () => {
    const out = sortImagesByCapture([
      { filename: 'DJI_0100.jpg', ...gps },
      { filename: 'DJI_0018.jpg', ...gps },
      { filename: 'DJI_0002.jpg', ...gps },
    ])
    expect(out.map(i => i.filename)).toEqual([
      'DJI_0002.jpg', 'DJI_0018.jpg', 'DJI_0100.jpg',
    ])
  })

  it('places timed images before untimed', () => {
    const out = sortImagesByCapture([
      { filename: 'z_untimed.jpg', ...gps },
      { filename: 'a_timed.jpg', capture_time: '2024-05-01 10:00:00', ...gps },
    ])
    expect(out.map(i => i.filename)).toEqual(['a_timed.jpg', 'z_untimed.jpg'])
  })

  it('drops images without usable GPS', () => {
    const out = sortImagesByCapture([
      { filename: 'ok.jpg', ...gps },
      { filename: 'nogps.jpg' },
      { filename: 'zero.jpg', latitude: 0, longitude: 0 },
    ])
    expect(out.map(i => i.filename)).toEqual(['ok.jpg'])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
npm test -- flightPath
```
Expected: FAIL — cannot resolve `@/lib/flightPath`.

- [ ] **Step 3: Write minimal implementation**

Create `frappe-bench/apps/webodm_frontend/frontend/src/lib/flightPath.js`:
```js
// Order drone images for the flight-path polyline.
// Prefer EXIF capture_time; fall back to natural-numeric filename order.

function naturalChunks(name) {
  // "DJI_0018.jpg" -> ["DJI_", "0018", ".jpg"] so numeric runs compare as numbers.
  return String(name || '').match(/(\d+|\D+)/g) || []
}

function compareNatural(a, b) {
  const ka = naturalChunks(a.filename)
  const kb = naturalChunks(b.filename)
  const n = Math.min(ka.length, kb.length)
  for (let i = 0; i < n; i++) {
    const x = ka[i]
    const y = kb[i]
    const nx = Number(x)
    const ny = Number(y)
    const bothNum = !Number.isNaN(nx) && !Number.isNaN(ny) && x.trim() !== '' && y.trim() !== ''
    if (bothNum) {
      if (nx !== ny) return nx - ny
    } else if (x !== y) {
      return x < y ? -1 : 1
    }
  }
  return ka.length - kb.length
}

function hasGps(img) {
  const lat = parseFloat(img.latitude)
  const lng = parseFloat(img.longitude)
  return !Number.isNaN(lat) && !Number.isNaN(lng) && !(lat === 0 && lng === 0)
}

export function sortImagesByCapture(images) {
  const usable = (images || []).filter(hasGps)
  const timed = usable.filter(img => img.capture_time)
  const untimed = usable.filter(img => !img.capture_time)
  timed.sort((a, b) => {
    if (a.capture_time < b.capture_time) return -1
    if (a.capture_time > b.capture_time) return 1
    return compareNatural(a, b)
  })
  untimed.sort(compareNatural)
  return [...timed, ...untimed]
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
npm test -- flightPath
```
Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
cd frappe-bench/apps/webodm_frontend/frontend
git add src/lib/flightPath.js src/lib/flightPath.test.js
git commit -m "feat: add flight-path image ordering (capture_time + filename fallback)"
```

---

### Task 4: `mapLayers.js` — basemap definitions and factory

Config + factory for the three basemaps.

**Files:**
- Create: `frappe-bench/apps/webodm_frontend/frontend/src/lib/mapLayers.js`
- Test: `frappe-bench/apps/webodm_frontend/frontend/src/lib/mapLayers.test.js`

**Interfaces:**
- Produces:
  - `BASEMAPS` — array of `{ id, label, url, attribution, maxZoom }` for `'osm'`, `'esri-satellite'`, `'esri-topo'`.
  - `getBasemapDef(id: string) → object` — the matching def, or the first (`osm`) as fallback.
  - `createBasemap(id: string) → L.TileLayer`.
- Consumed by `MapView.vue` (Task 8) for the basemap radio and `setBasemap`.

- [ ] **Step 1: Write the failing test**

Create `frappe-bench/apps/webodm_frontend/frontend/src/lib/mapLayers.test.js`:
```js
import { describe, it, expect } from 'vitest'
import { BASEMAPS, getBasemapDef, createBasemap } from '@/lib/mapLayers'

describe('BASEMAPS', () => {
  it('defines exactly the three approved basemaps', () => {
    expect(BASEMAPS.map(b => b.id)).toEqual(['osm', 'esri-satellite', 'esri-topo'])
  })
  it('gives every basemap a url and label', () => {
    for (const b of BASEMAPS) {
      expect(b.url).toMatch(/\{z\}.*\{x\}.*\{y\}|\{z\}.*\{y\}.*\{x\}/)
      expect(b.label.length).toBeGreaterThan(0)
    }
  })
})

describe('getBasemapDef', () => {
  it('resolves a known id', () => {
    expect(getBasemapDef('esri-satellite').label).toBe('Satellite')
  })
  it('falls back to osm for an unknown id', () => {
    expect(getBasemapDef('nope').id).toBe('osm')
  })
})

describe('createBasemap', () => {
  it('returns a Leaflet tile layer with the def url', () => {
    const layer = createBasemap('osm')
    expect(layer).toBeTruthy()
    expect(layer._url).toBe(getBasemapDef('osm').url)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
npm test -- mapLayers
```
Expected: FAIL — cannot resolve `@/lib/mapLayers`.

- [ ] **Step 3: Write minimal implementation**

Create `frappe-bench/apps/webodm_frontend/frontend/src/lib/mapLayers.js`:
```js
import L from 'leaflet'

// Approved basemaps: OSM Streets, Esri Satellite, Esri Topographic.
export const BASEMAPS = [
  {
    id: 'osm',
    label: 'Streets',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
  },
  {
    id: 'esri-satellite',
    label: 'Satellite',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri, Maxar, Earthstar Geographics',
    maxZoom: 19,
  },
  {
    id: 'esri-topo',
    label: 'Topographic',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19,
  },
]

export function getBasemapDef(id) {
  return BASEMAPS.find(b => b.id === id) || BASEMAPS[0]
}

export function createBasemap(id) {
  const def = getBasemapDef(id)
  return L.tileLayer(def.url, {
    attribution: def.attribution,
    maxZoom: def.maxZoom,
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
npm test -- mapLayers
```
Expected: PASS — all tests green (Leaflet loads under jsdom).

- [ ] **Step 5: Commit**

```bash
cd frappe-bench/apps/webodm_frontend/frontend
git add src/lib/mapLayers.js src/lib/mapLayers.test.js
git commit -m "feat: add basemap definitions and factory (OSM + Esri satellite/topo)"
```

---

### Task 5: Add `capture_time` and `altitude` fields to the WebODM Task Image DocType

Two nullable, read-only fields so the flight path can order by capture time and (future) display altitude.

**Files:**
- Modify: `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_task_image/webodm_task_image.json`

**Interfaces:**
- Produces: `capture_time` (Datetime) and `altitude` (Float) columns on `WebODM Task Image`, written by `upload_images` (Task 6) and read by the frontend full-task payload.

- [ ] **Step 1: Add the fields to `field_order`**

In `webodm_task_image.json`, change the `field_order` array from:
```json
 "field_order": [
  "image",
  "filename",
  "file_size",
  "gps_section",
  "latitude",
  "longitude"
 ],
```
to:
```json
 "field_order": [
  "image",
  "filename",
  "file_size",
  "gps_section",
  "latitude",
  "longitude",
  "altitude",
  "capture_time"
 ],
```

- [ ] **Step 2: Add the field definitions**

In the same file, in the `"fields"` array, change the `longitude` entry (the last field object) from:
```json
  {
   "fieldname": "longitude",
   "fieldtype": "Float",
   "label": "Longitude",
   "allow_null": 1
  }
 ],
```
to:
```json
  {
   "fieldname": "longitude",
   "fieldtype": "Float",
   "label": "Longitude",
   "allow_null": 1
  },
  {
   "fieldname": "altitude",
   "fieldtype": "Float",
   "label": "Altitude (m)",
   "read_only": 1,
   "allow_null": 1
  },
  {
   "fieldname": "capture_time",
   "fieldtype": "Datetime",
   "label": "Capture Time",
   "read_only": 1,
   "allow_null": 1
  }
 ],
```

- [ ] **Step 3: Apply the migration**

Run (from `frappe-bench/`):
```bash
bench --site webodm.local migrate
```
Expected: migration completes; `WebODM Task Image` schema now has `altitude` and `capture_time` columns (existing rows are `NULL`).

- [ ] **Step 4: Verify the columns exist**

Run (from `frappe-bench/`):
```bash
bench --site webodm.local execute frappe.db.get_table_columns --kwargs "{'doctype': 'WebODM Task Image'}"
```
Expected: output list includes `altitude` and `capture_time`.

- [ ] **Step 5: Commit**

```bash
cd frappe-bench/apps/webodm_core
git add webodm_core/webodm_core/doctype/webodm_task_image/webodm_task_image.json
git commit -m "feat: add altitude and capture_time fields to WebODM Task Image"
```

---

### Task 6: Widen EXIF extraction to `_extract_photo_meta` and persist altitude/capture_time

Replace `_extract_gps` with `_extract_photo_meta`, extracting lat/lng, altitude, and capture time in one `Image.open`, and write the new fields in `upload_images`. Each field is independently optional and never blocks an upload.

**Files:**
- Modify: `frappe-bench/apps/webodm_core/webodm_core/api/task.py:68-99` (replace `_extract_gps`) and `:186-196` (upload wiring inside `upload_images`)
- Test: `frappe-bench/apps/webodm_core/webodm_core/api/test_task.py`

**Interfaces:**
- Consumes: raw image bytes (`content: bytes`).
- Produces:
  - `_gps_to_decimal(dms, ref) → float` — module-level DMS→decimal helper.
  - `_extract_photo_meta(content: bytes) → dict` with keys `lat`, `lng`, `altitude`, `capture_time` (each `None` when absent/malformed).
- `upload_images` writes `latitude`/`longitude`/`altitude`/`capture_time` onto each `WebODM Task Image` row when present.

- [ ] **Step 1: Write the failing test**

Create `frappe-bench/apps/webodm_core/webodm_core/api/test_task.py`:
```python
import io
import unittest

from PIL import Image

from webodm_core.api.task import _gps_to_decimal, _extract_photo_meta


def _plain_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 120, 120)).save(buf, format="JPEG")
    return buf.getvalue()


def _jpeg_with_datetime(value="2024:05:01 10:20:30"):
    im = Image.new("RGB", (8, 8), (10, 20, 30))
    exif = im.getexif()
    exif[306] = value  # DateTime (base IFD) — reliably round-trips through PIL
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class TestExtractPhotoMeta(unittest.TestCase):
    def test_gps_to_decimal_north(self):
        self.assertAlmostEqual(_gps_to_decimal((40, 34, 30), "N"), 40.575, places=3)

    def test_gps_to_decimal_west_is_negative(self):
        self.assertAlmostEqual(_gps_to_decimal((73, 30, 0), "W"), -73.5, places=3)

    def test_plain_image_yields_all_none(self):
        meta = _extract_photo_meta(_plain_jpeg())
        self.assertEqual(
            meta,
            {"lat": None, "lng": None, "altitude": None, "capture_time": None},
        )

    def test_capture_time_parsed_to_frappe_datetime(self):
        meta = _extract_photo_meta(_jpeg_with_datetime())
        self.assertEqual(meta["capture_time"], "2024-05-01 10:20:30")

    def test_garbage_bytes_never_raise(self):
        meta = _extract_photo_meta(b"not an image")
        self.assertEqual(meta["lat"], None)
        self.assertEqual(meta["capture_time"], None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frappe-bench/`):
```bash
bench --site webodm.local run-tests --module webodm_core.api.test_task
```
Expected: FAIL — `ImportError: cannot import name '_gps_to_decimal'` (and `_extract_photo_meta`).

- [ ] **Step 3: Replace `_extract_gps` with the widened helper**

In `frappe-bench/apps/webodm_core/webodm_core/api/task.py`, replace the entire `_extract_gps` function (lines 68–99, from `def _extract_gps(content: bytes):` through its closing `return None, None` / `except` block) with:
```python
def _gps_to_decimal(dms, ref):
    deg, min_, sec = dms
    decimal = float(deg) + float(min_) / 60 + float(sec) / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


def _extract_photo_meta(content: bytes):
    """Extract georeferencing/timing metadata from an image's EXIF.

    Returns a dict with keys ``lat``, ``lng``, ``altitude`` (metres, signed),
    and ``capture_time`` (Frappe ``YYYY-MM-DD HH:MM:SS`` string). Every field is
    independently optional: a missing or malformed tag yields ``None`` and never
    raises, so a bad tag can never block an upload.
    """
    meta = {"lat": None, "lng": None, "altitude": None, "capture_time": None}

    try:
        img = Image.open(io.BytesIO(content))
        exif = img.getexif()
    except Exception:
        return meta
    if not exif:
        return meta

    # --- GPS: latitude / longitude / altitude (GPS IFD 34853) ---
    try:
        gps_ifd = exif.get_ifd(34853)
        if gps_ifd:
            gps_info = {}
            for k, v in gps_ifd.items():
                tag = GPSTAGS.get(k)
                if tag:
                    gps_info[tag] = v

            if "GPSLatitude" in gps_info and "GPSLongitude" in gps_info:
                meta["lat"] = _gps_to_decimal(
                    gps_info["GPSLatitude"], gps_info.get("GPSLatitudeRef", "N")
                )
                meta["lng"] = _gps_to_decimal(
                    gps_info["GPSLongitude"], gps_info.get("GPSLongitudeRef", "E")
                )

            if "GPSAltitude" in gps_info:
                try:
                    alt = float(gps_info["GPSAltitude"])
                    ref = gps_info.get("GPSAltitudeRef", 0)
                    # GPSAltitudeRef == 1 (or b"\x01") means below sea level.
                    if ref in (1, b"\x01"):
                        alt = -alt
                    meta["altitude"] = round(alt, 3)
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    # --- Capture time: DateTimeOriginal (Exif IFD), then DateTime (base IFD) ---
    try:
        dto = None
        exif_ifd = exif.get_ifd(34665)  # ExifIFD
        if exif_ifd:
            dto = exif_ifd.get(36867)  # DateTimeOriginal
        if not dto:
            dto = exif.get(306)  # DateTime
        if dto:
            # EXIF "YYYY:MM:DD HH:MM:SS" -> Frappe "YYYY-MM-DD HH:MM:SS".
            s = str(dto).strip()
            date_part, _, time_part = s.partition(" ")
            date_part = date_part.replace(":", "-")
            meta["capture_time"] = (date_part + " " + time_part).strip()
    except Exception:
        pass

    return meta
```

- [ ] **Step 4: Wire the new fields into `upload_images`**

In the same file, inside `upload_images`, replace the block (currently lines ~186–196):
```python
        lat, lng = _extract_gps(content)

        img_row = {
            "image": file_doc.file_url,
            "filename": file_name,
            "file_size": file_size,
        }
        if lat is not None and lng is not None:
            img_row["latitude"] = lat
            img_row["longitude"] = lng
        task.append("images", img_row)
```
with:
```python
        meta = _extract_photo_meta(content)

        img_row = {
            "image": file_doc.file_url,
            "filename": file_name,
            "file_size": file_size,
        }
        if meta["lat"] is not None and meta["lng"] is not None:
            img_row["latitude"] = meta["lat"]
            img_row["longitude"] = meta["lng"]
        if meta["altitude"] is not None:
            img_row["altitude"] = meta["altitude"]
        if meta["capture_time"]:
            img_row["capture_time"] = meta["capture_time"]
        task.append("images", img_row)
```

- [ ] **Step 5: Run test to verify it passes**

Run (from `frappe-bench/`):
```bash
bench --site webodm.local run-tests --module webodm_core.api.test_task
```
Expected: PASS — 5 tests OK.

- [ ] **Step 6: Commit**

```bash
cd frappe-bench/apps/webodm_core
git add webodm_core/api/task.py webodm_core/api/test_task.py
git commit -m "feat: extract altitude and capture_time from EXIF on upload"
```

---

### Task 7: `useMeasure.js` — click-to-draw measurement engine

A Vue composable that owns the Leaflet draw handlers, in-progress geometry, vertex dots, live tooltip, and reactive result. Uses Turf for geodesic length/area. Knows nothing about the panel UI.

**Files:**
- Create: `frappe-bench/apps/webodm_frontend/frontend/src/composables/useMeasure.js`
- Modify: `frappe-bench/apps/webodm_frontend/frontend/package.json` (adds `@turf/turf`)

**Interfaces:**
- Consumes: `getMap: () => L.Map | null` (accessor, because `MapView.vue` holds `map` in a module-scoped `let`); `formatDistance`/`formatArea` from `@/lib/format`.
- Produces: `useMeasure(getMap) → { state, start, finish, clear }` where
  - `state` is a reactive `{ mode: 'distance'|'area'|null, value: number, formatted: string }`
  - `start(mode)`, `finish()`, `clear()` are functions.
- Consumed by `MapView.vue` (Task 9).

- [ ] **Step 1: Install Turf**

Run (from `frappe-bench/apps/webodm_frontend/frontend/`):
```bash
npm install @turf/turf@^7.1.0
```
Expected: `@turf/turf` appears under `dependencies` in `package.json`.

- [ ] **Step 2: Write the composable**

Create `frappe-bench/apps/webodm_frontend/frontend/src/composables/useMeasure.js`:
```js
import { reactive } from 'vue'
import L from 'leaflet'
import { length as turfLength, area as turfArea } from '@turf/turf'
import { formatDistance, formatArea } from '@/lib/format'

const DRAW_COLOR = '#2563eb'

// Click-to-draw distance/area measurement over a Leaflet map.
// getMap() returns the live L.Map (MapView holds it in a module-scoped let).
export function useMeasure(getMap) {
  const state = reactive({ mode: null, value: 0, formatted: '' })

  let points = [] // L.LatLng[]
  let shape = null // L.Polyline (distance) | L.Polygon (area)
  let dots = [] // L.CircleMarker[]

  function toCoords(latlngs) {
    return latlngs.map(p => [p.lng, p.lat]) // GeoJSON is [lng, lat]
  }

  function recompute() {
    if (state.mode === 'distance') {
      if (points.length < 2) {
        state.value = 0
        state.formatted = formatDistance(0)
        return
      }
      const gj = { type: 'Feature', geometry: { type: 'LineString', coordinates: toCoords(points) } }
      state.value = turfLength(gj, { units: 'kilometers' }) * 1000
      state.formatted = formatDistance(state.value)
    } else if (state.mode === 'area') {
      if (points.length < 3) {
        state.value = 0
        state.formatted = formatArea(0)
        return
      }
      const ring = toCoords(points)
      ring.push(ring[0]) // close the polygon
      const gj = { type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] } }
      state.value = turfArea(gj)
      state.formatted = formatArea(state.value)
    }
  }

  function redraw() {
    const map = getMap()
    if (!map) return
    if (!shape) {
      shape = state.mode === 'area'
        ? L.polygon(points, { color: DRAW_COLOR, weight: 2, fillOpacity: 0.1 })
        : L.polyline(points, { color: DRAW_COLOR, weight: 2 })
      shape.addTo(map)
    } else {
      shape.setLatLngs(points)
    }
    for (const d of dots) map.removeLayer(d)
    dots = points.map(p =>
      L.circleMarker(p, { radius: 4, color: DRAW_COLOR, fillColor: '#fff', fillOpacity: 1, weight: 2 }).addTo(map)
    )
    if (shape) {
      shape.bindTooltip(state.formatted || '', { permanent: true, direction: 'top', className: 'measure-tooltip' })
      shape.openTooltip(points[points.length - 1])
    }
  }

  function onClick(e) {
    points.push(e.latlng)
    recompute()
    redraw()
  }

  function onDblClick(e) {
    L.DomEvent.stop(e)
    finish()
  }

  function onKey(e) {
    if (e.key === 'Escape') clear()
  }

  function stopListening() {
    const map = getMap()
    if (map) {
      map.off('click', onClick)
      map.off('dblclick', onDblClick)
      map.doubleClickZoom.enable()
    }
    document.removeEventListener('keydown', onKey)
  }

  function start(mode) {
    clear()
    state.mode = mode
    const map = getMap()
    if (!map) return
    map.doubleClickZoom.disable()
    map.on('click', onClick)
    map.on('dblclick', onDblClick)
    document.addEventListener('keydown', onKey)
  }

  function finish() {
    recompute()
    redraw()
    stopListening()
  }

  function clear() {
    const map = getMap()
    stopListening()
    if (map) {
      if (shape) map.removeLayer(shape)
      for (const d of dots) map.removeLayer(d)
    }
    shape = null
    dots = []
    points = []
    state.mode = null
    state.value = 0
    state.formatted = ''
  }

  return { state, start, finish, clear }
}
```

- [ ] **Step 3: Verify it imports cleanly (build check)**

There is no DOM/Leaflet interaction test harness for this composable; correctness is verified manually in Task 9. Confirm it at least parses and its dependencies resolve by building the frontend:
```bash
cd frappe-bench/apps/webodm_frontend/frontend
npm run build
```
Expected: build succeeds with no "failed to resolve import" errors for `@turf/turf`, `@/lib/format`, or `leaflet`.

- [ ] **Step 4: Commit**

```bash
cd frappe-bench/apps/webodm_frontend/frontend
git add package.json package-lock.json src/composables/useMeasure.js
git commit -m "feat: add useMeasure composable (Turf geodesic distance/area)"
```

---

### Task 8: MapView — stacked control panel (basemap switcher, opacity, markers toggle)

Rework the existing top-right Layers panel into the Layout-A stacked panel: Basemap radio → Layers (checkbox + opacity slider per raster) → Show (Image markers). Swap the hardcoded OSM basemap for the `mapLayers` factory. Flight-path and measurement wiring come in Task 9.

**Files:**
- Modify: `frappe-bench/apps/webodm_frontend/frontend/src/pages/MapView.vue`

**Interfaces:**
- Consumes: `BASEMAPS`, `createBasemap` from `@/lib/mapLayers`.
- Produces (used by Task 9's template additions): the `map` module-scoped `let`, a `currentImages` ref, and a `hasGps` computed. Also `setBasemap(id)`, `setOverlayOpacity(key, value)`, `toggleMarkers()`, and `showMarkers` ref.

Verification is manual (Leaflet UI). Steps below give exact edits and a manual check.

- [ ] **Step 1: Update imports and refs**

In `MapView.vue`, change the script imports (lines 132–137) from:
```js
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Badge, FeatherIcon } from 'frappe-ui'
import L from 'leaflet'
import { toast } from 'frappe-ui'
```
to:
```js
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Badge, FeatherIcon } from 'frappe-ui'
import L from 'leaflet'
import { toast } from 'frappe-ui'
import { BASEMAPS, createBasemap } from '@/lib/mapLayers'
```

- [ ] **Step 2: Add state for basemap, markers, images, and the base-layer handle**

In `MapView.vue`, immediately after the line `const overlays = ref([])` (line 150), add:
```js
const currentBasemap = ref('osm')
const showMarkers = ref(true)
const currentImages = ref([])
const hasGps = computed(() =>
  currentImages.value.some(img => {
    const lat = parseFloat(img.latitude)
    const lng = parseFloat(img.longitude)
    return !isNaN(lat) && !isNaN(lng) && !(lat === 0 && lng === 0)
  })
)
```
Then, after the line `let imageMarkers = null` (line 153), add:
```js
let baseLayer = null
```

- [ ] **Step 3: Add opacity to overlay entries and add `setOverlayOpacity`**

In `loadOverlays`, change the overlay push (line 239) from:
```js
    overlays.value.push({ key, label: DATASET_LABELS[key], visible })
```
to:
```js
    overlays.value.push({ key, label: DATASET_LABELS[key], visible, opacity: 100 })
```
Then, immediately after the `toggleOverlay` function (after line 251), add:
```js
function setOverlayOpacity(o, value) {
  o.opacity = Number(value)
  const layer = overlayLayers[o.key]
  if (layer) layer.setOpacity(o.opacity / 100)
}
```

- [ ] **Step 4: Add `setBasemap` and `toggleMarkers`**

In `MapView.vue`, immediately after `setOverlayOpacity` (from Step 3), add:
```js
function setBasemap(id) {
  currentBasemap.value = id
  if (!map) return
  if (baseLayer) map.removeLayer(baseLayer)
  baseLayer = createBasemap(id)
  baseLayer.addTo(map)
  baseLayer.bringToBack() // keep orthophoto/overlays on top
}

function toggleMarkers() {
  showMarkers.value = !showMarkers.value
  if (!map || !imageMarkers) return
  if (showMarkers.value) imageMarkers.addTo(map)
  else map.removeLayer(imageMarkers)
}
```

- [ ] **Step 5: Respect `showMarkers` in `plotImageMarkers` and record images in `selectTask`**

In `plotImageMarkers`, change the block (lines 191–194) from:
```js
  if (hasGps) {
    imageMarkers.addTo(map)
    map.fitBounds(bounds, { padding: [50, 50] })
  }
```
to:
```js
  if (hasGps) {
    if (showMarkers.value) imageMarkers.addTo(map)
    map.fitBounds(bounds, { padding: [50, 50] })
  }
```
(Note: the local variable `hasGps` inside `plotImageMarkers` shadows the new computed of the same name; this is intentional and unchanged.)

Then in `selectTask`, change (line 267) from:
```js
  plotImageMarkers(full.images)
  loadOverlays(full)
```
to:
```js
  currentImages.value = full.images || []
  plotImageMarkers(full.images)
  loadOverlays(full)
```

- [ ] **Step 6: Use the basemap factory in `onMounted`**

In `onMounted`, change (lines 420–423) from:
```js
  map = L.map('map').setView([0, 0], 2)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(map)
```
to:
```js
  map = L.map('map').setView([0, 0], 2)
  baseLayer = createBasemap(currentBasemap.value)
  baseLayer.addTo(map)
```

- [ ] **Step 7: Replace the Layers panel with the stacked control panel**

In the template, replace the entire overlay-panel block (lines 84–93):
```html
      <div class="absolute top-4 right-4 z-[1000] space-y-2 flex flex-col items-end">
        <Button variant="outline" size="sm" @click="zoomToFit">Zoom To Fit</Button>
        <div v-if="overlays.length" class="bg-white dark:bg-gray-800 rounded-lg shadow-md border dark:border-gray-700 p-3 min-w-[140px]">
          <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Layers</p>
          <label v-for="o in overlays" :key="o.key" class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 py-0.5 cursor-pointer">
            <input type="checkbox" :checked="o.visible" @change="toggleOverlay(o)" class="rounded dark:bg-gray-700" />
            {{ o.label }}
          </label>
        </div>
      </div>
```
with:
```html
      <div class="absolute top-4 right-4 z-[1000] space-y-2 flex flex-col items-end">
        <Button variant="outline" size="sm" @click="zoomToFit">Zoom To Fit</Button>
        <div class="bg-white dark:bg-gray-800 rounded-lg shadow-md border dark:border-gray-700 w-[200px] text-sm">
          <!-- Basemap -->
          <div class="p-3 border-b dark:border-gray-700">
            <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Basemap</p>
            <label v-for="b in BASEMAPS" :key="b.id" class="flex items-center gap-2 text-gray-700 dark:text-gray-300 py-0.5 cursor-pointer">
              <input type="radio" name="basemap" :value="b.id" :checked="currentBasemap === b.id" @change="setBasemap(b.id)" />
              {{ b.label }}
            </label>
          </div>
          <!-- Layers + opacity -->
          <div v-if="overlays.length" class="p-3 border-b dark:border-gray-700">
            <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Layers</p>
            <div v-for="o in overlays" :key="o.key" class="py-1">
              <label class="flex items-center gap-2 text-gray-700 dark:text-gray-300 cursor-pointer">
                <input type="checkbox" :checked="o.visible" @change="toggleOverlay(o)" class="rounded dark:bg-gray-700" />
                {{ o.label }}
              </label>
              <input
                v-if="o.visible"
                type="range" min="0" max="100" step="5"
                :value="o.opacity"
                @input="setOverlayOpacity(o, $event.target.value)"
                class="w-full mt-1"
              />
            </div>
          </div>
          <!-- Show toggles -->
          <div class="p-3">
            <p class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Show</p>
            <label
              class="flex items-center gap-2 py-0.5"
              :class="hasGps ? 'text-gray-700 dark:text-gray-300 cursor-pointer' : 'text-gray-400 dark:text-gray-600 cursor-not-allowed'"
            >
              <input type="checkbox" :checked="showMarkers" :disabled="!hasGps" @change="toggleMarkers" class="rounded dark:bg-gray-700" />
              Image markers
            </label>
          </div>
        </div>
      </div>
```

- [ ] **Step 8: Manual verification**

Start the dev server / bench and open a project MapView with a completed, georeferenced task (the Brighton Beach task).
Run (from `frappe-bench/apps/webodm_frontend/frontend/`, in the user's own terminal):
```bash
npm run dev
```
Verify:
- Basemap radio switches between Streets / Satellite / Topographic; the orthophoto overlay stays on top and does not disappear when switching.
- Each visible raster shows an opacity slider; dragging it fades that raster only.
- "Image markers" checkbox hides/shows the GPS markers; it is disabled (greyed) for a task with no GPS.

- [ ] **Step 9: Commit**

```bash
cd frappe-bench/apps/webodm_frontend/frontend
git add src/pages/MapView.vue
git commit -m "feat: MapView stacked panel with basemap switcher, layer opacity, markers toggle"
```

---

### Task 9: MapView — flight path and measurement toolbar

Add the drone flight-path layer (ordered polyline + A/B endpoint markers + direction arrows recomputed on zoom) with a Show toggle, and the top-left measurement toolbar wired to `useMeasure`.

**Files:**
- Modify: `frappe-bench/apps/webodm_frontend/frontend/src/pages/MapView.vue`

**Interfaces:**
- Consumes: `useMeasure` from `@/composables/useMeasure`; `sortImagesByCapture` from `@/lib/flightPath`; the `map`, `currentImages`, `hasGps`, and `L` symbols added/available in Task 8.
- Produces: `measure` (from `useMeasure`), `showFlightPath` ref, `toggleFlightPath()`, `buildFlightPath(images)`, `removeFlightPath()`.

Verification is manual.

- [ ] **Step 1: Add imports for the composable and sort helper**

In `MapView.vue`, after the line `import { BASEMAPS, createBasemap } from '@/lib/mapLayers'` (added in Task 8), add:
```js
import { sortImagesByCapture } from '@/lib/flightPath'
import { useMeasure } from '@/composables/useMeasure'
```

- [ ] **Step 2: Instantiate the measure engine and flight-path state**

In `MapView.vue`, after the line `let baseLayer = null` (added in Task 8), add:
```js
let flightPathLayer = null
const showFlightPath = ref(false)
const measure = useMeasure(() => map)
```

- [ ] **Step 3: Add flight-path build/remove/toggle functions**

In `MapView.vue`, immediately after the `toggleMarkers` function (from Task 8), add:
```js
function flightEndpointIcon(label, color) {
  return L.divIcon({
    className: 'flight-endpoint',
    html: `<div style="background:${color};color:#fff;border-radius:9999px;width:20px;height:20px;`
      + `display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;`
      + `box-shadow:0 1px 3px rgba(0,0,0,.4)">${label}</div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
  })
}

function flightArrowIcon(angleDeg) {
  return L.divIcon({
    className: 'flight-arrow',
    html: `<div style="transform:rotate(${angleDeg}deg);color:#f59e0b;font-size:14px;line-height:1">▲</div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  })
}

// Arrow markers are placed in screen space, so recompute them whenever the
// map is zoomed while the flight path is shown.
let flightLatLngs = []
let flightArrows = []

function drawFlightArrows() {
  if (!map || !flightPathLayer) return
  for (const a of flightArrows) flightPathLayer.removeLayer(a)
  flightArrows = []
  for (let i = 0; i < flightLatLngs.length - 1; i++) {
    const p1 = map.latLngToLayerPoint(flightLatLngs[i])
    const p2 = map.latLngToLayerPoint(flightLatLngs[i + 1])
    const angle = Math.atan2(p2.x - p1.x, -(p2.y - p1.y)) * 180 / Math.PI // 0° = north (▲ up)
    const mid = [
      (flightLatLngs[i][0] + flightLatLngs[i + 1][0]) / 2,
      (flightLatLngs[i][1] + flightLatLngs[i + 1][1]) / 2,
    ]
    const arrow = L.marker(mid, { icon: flightArrowIcon(angle), interactive: false })
    arrow.addTo(flightPathLayer)
    flightArrows.push(arrow)
  }
}

function removeFlightPath() {
  if (map) map.off('zoomend', drawFlightArrows)
  if (map && flightPathLayer) map.removeLayer(flightPathLayer)
  flightPathLayer = null
  flightArrows = []
  flightLatLngs = []
}

function buildFlightPath(images) {
  removeFlightPath()
  if (!map) return
  const ordered = sortImagesByCapture(images)
  if (ordered.length < 2) return
  flightLatLngs = ordered.map(img => [parseFloat(img.latitude), parseFloat(img.longitude)])
  flightPathLayer = L.featureGroup()
  L.polyline(flightLatLngs, { color: '#f59e0b', weight: 2, opacity: 0.9 }).addTo(flightPathLayer)
  L.marker(flightLatLngs[0], { icon: flightEndpointIcon('A', '#16a34a') }).addTo(flightPathLayer)
  L.marker(flightLatLngs[flightLatLngs.length - 1], { icon: flightEndpointIcon('B', '#dc2626') }).addTo(flightPathLayer)
  flightPathLayer.addTo(map)
  drawFlightArrows()
  map.on('zoomend', drawFlightArrows)
}

function toggleFlightPath() {
  showFlightPath.value = !showFlightPath.value
  if (showFlightPath.value) buildFlightPath(currentImages.value)
  else removeFlightPath()
}
```

- [ ] **Step 4: Rebuild the flight path on task change; measurement functions**

In `selectTask`, change the block added in Task 8 (lines around 267) from:
```js
  currentImages.value = full.images || []
  plotImageMarkers(full.images)
  loadOverlays(full)
```
to:
```js
  currentImages.value = full.images || []
  plotImageMarkers(full.images)
  loadOverlays(full)
  if (showFlightPath.value) buildFlightPath(currentImages.value)
```
Then, immediately after the `toggleFlightPath` function (from Step 3), add:
```js
function startMeasure(mode) {
  measure.start(mode)
}

function clearMeasure() {
  measure.clear()
}
```

- [ ] **Step 5: Tear down measurement and flight path on unmount**

In `onUnmounted`, change (lines 426–431) from:
```js
onUnmounted(() => {
  if (map) {
    map.remove()
    map = null
  }
})
```
to:
```js
onUnmounted(() => {
  measure.clear()
  removeFlightPath()
  if (map) {
    map.remove()
    map = null
  }
})
```

- [ ] **Step 6: Add the flight-path toggle to the Show section**

In the template's Show section (added in Task 8), immediately after the "Image markers" `<label>...</label>` block, add:
```html
            <label
              class="flex items-center gap-2 py-0.5"
              :class="hasGps ? 'text-gray-700 dark:text-gray-300 cursor-pointer' : 'text-gray-400 dark:text-gray-600 cursor-not-allowed'"
            >
              <input type="checkbox" :checked="showFlightPath" :disabled="!hasGps" @change="toggleFlightPath" class="rounded dark:bg-gray-700" />
              Flight path
            </label>
```

- [ ] **Step 7: Add the top-left measurement toolbar**

In the template, immediately after the `<div id="map" class="h-full w-full"></div>` line (line 83), add:
```html
      <div class="absolute top-4 left-4 z-[1000] bg-white dark:bg-gray-800 rounded-lg shadow-md border dark:border-gray-700 p-2 flex items-center gap-2">
        <Button size="sm" :variant="measure.state.mode === 'distance' ? 'solid' : 'outline'" @click="startMeasure('distance')" title="Measure distance">
          <template #prefix><FeatherIcon name="minus" class="h-3.5 w-3.5" /></template>
          Distance
        </Button>
        <Button size="sm" :variant="measure.state.mode === 'area' ? 'solid' : 'outline'" @click="startMeasure('area')" title="Measure area">
          <template #prefix><FeatherIcon name="square" class="h-3.5 w-3.5" /></template>
          Area
        </Button>
        <Button size="sm" variant="ghost" @click="clearMeasure" title="Clear measurement">
          <FeatherIcon name="trash-2" class="h-3.5 w-3.5" />
        </Button>
        <span v-if="measure.state.formatted" class="text-sm font-medium text-gray-700 dark:text-gray-200 pl-1">
          {{ measure.state.formatted }}
        </span>
      </div>
```

- [ ] **Step 8: Manual verification**

With `npm run dev` running and the Brighton Beach task open:
- Toggle "Flight path": an orange polyline appears through image points with green **A** / red **B** endpoints and orange arrowheads; arrowheads stay oriented after zooming.
- Click **Distance**, click several points on the map, double-click to finish — the readout shows a plausible length (~187 m across the known extent). **Esc** or the trash button clears it and re-enables map double-click zoom.
- Click **Area**, click ≥3 points, double-click — the readout shows m²/ha and acres.
- Switching basemap and toggling layers does not remove an in-progress or finished measurement or the flight path.

- [ ] **Step 9: Commit**

```bash
cd frappe-bench/apps/webodm_frontend/frontend
git add src/pages/MapView.vue
git commit -m "feat: MapView drone flight path and distance/area measurement toolbar"
```

---

## Self-Review

**1. Spec coverage:**
- Measurement (distance + area) → Tasks 2 (format), 7 (engine), 9 (toolbar). ✓
- Basemap switcher (OSM/Esri satellite/topo) → Tasks 4 (config), 8 (UI + swap). ✓
- Drone flight path ordered by capture time → Tasks 3 (sort), 5+6 (capture_time backend), 9 (render). ✓
- Layer opacity + controls → Task 8 (per-raster checkbox + slider). ✓
- Image-markers toggle → Task 8. ✓
- Backend EXIF widening (altitude + capture_time, optional, non-blocking) → Tasks 5, 6. ✓
- Layout A (stacked top-right panel + top-left measure toolbar) → Tasks 8, 9. ✓
- Edge cases: no-GPS task disables flight-path/markers toggles (Task 8/9 `hasGps`); missing capture_time filename fallback (Task 3); old rows nullable (Task 5); basemap failure leaves overlays (Task 8 keeps overlays on switch); degenerate measurement reads 0 / Esc clears (Task 7); opacity resets on task reselect (Task 8 rebuilds `overlays` with `opacity: 100` in `loadOverlays`). ✓
- Testing: Vitest wired (Task 1); pure logic unit-tested (Tasks 2–4); backend unit test (Task 6); manual/E2E steps (Tasks 8–9). ✓
- Dependencies: `@turf/turf` added (Task 7); `bench migrate` (Task 5). ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N" — every code step contains full code. ✓

**3. Type consistency:** `getMap`/`map` accessor consistent between Task 7 (`useMeasure(getMap)`) and Task 9 (`useMeasure(() => map)`); `measure.state.{mode,value,formatted}` consistent; `sortImagesByCapture` name consistent across Tasks 3 and 9; `setOverlayOpacity(o, value)` / `toggleOverlay(o)` take the overlay object consistently; `removeFlightPath`/`buildFlightPath`/`drawFlightArrows` names consistent within Task 9; backend `_extract_photo_meta` dict keys `lat`/`lng`/`altitude`/`capture_time` consistent between Task 6 impl, test, and upload wiring. ✓

One note: `plotImageMarkers` has a local `hasGps` boolean that shadows the module `hasGps` computed added in Task 8 — flagged in Task 8 Step 5 as intentional; the computed is only referenced in the template, the local only inside the function, so there is no collision.
