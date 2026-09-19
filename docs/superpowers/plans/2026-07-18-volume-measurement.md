# Volume Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third measurement mode — volume (m³) with fill/cut/area — to the WebODM MapView, computed server-side over the task's DSM.

**Architecture:** A new `compute_volume` function in the geospatial FastAPI service masks the DSM to a drawn polygon, fits a best-fit base plane through the polygon's boundary elevations, and integrates fill/cut by a per-pixel Riemann sum. A `POST /volume` endpoint exposes it; the Frappe tile proxy forwards the polygon (session-authed, same-origin) after resolving the task's DSM path. The frontend extends the existing `useMeasure` polygon-draw engine with a `volume` mode that calls the backend on finish.

**Tech Stack:** Python 3.12 (geospatial: FastAPI, rasterio, numpy — all already present; pytest added), Frappe v16 / Python 3.14 (proxy), Vue 3 + Leaflet + Vitest (frontend).

## Global Constraints

> **⚠️ Amended 2026-08-15 — this plan is executed and partly superseded.**
> The base-plane and dependency constraints below are out of date. Base-method
> selection was added with **`triangulate` as the default** (matching WebODM,
> which this plan misidentified as best-fit plane), plus linear-unit scaling and
> a `scipy` dependency. See §8 of
> `docs/superpowers/specs/2026-07-18-volume-measurement-design.md`, which is
> authoritative. Tasks 1–6 below record what was originally built.

- Base plane: best-fit tilted plane through DSM elevations at the polygon's boundary vertices; degenerate (collinear / too few valid samples) → flat plane at mean boundary elevation, flagged `mean_fallback`. — **superseded:** now one of five methods (`triangulate` default, then `plane`/`average`/`highest`/`lowest`).
- Elevation source: **DSM only**. Volume disabled when the task has no DSM.
- Compute location: geospatial `POST /volume`, reached through the Frappe proxy `webodm_core.api.tiles.volume`. Browser never receives elevation data.
- Integration: per-pixel Riemann sum; cell area = `abs(transform.a * transform.e)`; `fill` = sum of positive (DSM − plane), `cut` = sum of negative, `volume = fill − cut`, all × cell area. — **amended:** volume also × `to_meter**3`, area × `to_meter**2`.
- Backend response shape (exact keys): `{ "volume": float, "fill": float, "cut": float, "area": float, "base_plane": "best_fit"|"mean_fallback"|"empty" }`, m³ / m². — **amended:** `base_plane` now also returns the method used (`triangulate`|`plane`|`average`|`highest`|`lowest`).
- Empty result (polygon outside coverage / all-nodata): `{volume:0, fill:0, cut:0, area:0, base_plane:"empty"}`.
- Raster CRS must be projected; geographic DSM → endpoint raises `ValueError`, surfaced as HTTP 422.
- Readout format: `formatVolume` → `"1,240 m³ (fill 1,310 / cut 70) · 890 m²"`; empty (`area==0`) → `"No DSM data under polygon"`.
- No new frontend dependencies. No DocType/schema changes, no migration. — **amended:** geospatial now requires `scipy>=1.14.0` for `triangulate`; frontend still adds none.
- Reuse existing helpers verbatim: geospatial `app.routers.tiles._require_raster`; Frappe `_resolve_raster_path`, `_geospatial_url`.

Repo roots:
- Geospatial: `/home/ridwan/workspaces/webodm-geospatial` (venv at `venv/bin/python`, its own git repo).
- Frappe core app: `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_core`.
- Frontend app: `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend`.

---

### Task 1: Geospatial — `compute_volume` + pytest wiring

The core math, unit-tested against synthetic DSMs. The geospatial repo has no test runner yet — this task adds pytest.

**Files:**
- Create: `/home/ridwan/workspaces/webodm-geospatial/app/utils/volume.py`
- Create: `/home/ridwan/workspaces/webodm-geospatial/tests/test_volume.py`
- Modify: `/home/ridwan/workspaces/webodm-geospatial/requirements.txt` (add pytest)

**Interfaces:**
- Produces:
  - `compute_volume(path: str, polygon_4326: dict) -> dict` returning the exact keys in Global Constraints.
  - `_fit_base_plane(xs, ys, zs) -> (evaluate: callable, label: str)` where `evaluate(xq, yq) -> np.ndarray` and `label ∈ {"best_fit","mean_fallback"}`.
- Consumed by Task 2 (`app/routers/volume.py`).

- [ ] **Step 1: Add pytest to requirements and install it**

Append to `/home/ridwan/workspaces/webodm-geospatial/requirements.txt`:
```
pytest>=8.0.0
```
Run (from `/home/ridwan/workspaces/webodm-geospatial`):
```bash
venv/bin/pip install "pytest>=8.0.0"
```
Expected: pytest installs into the venv.

- [ ] **Step 2: Write the failing test**

Create `/home/ridwan/workspaces/webodm-geospatial/tests/test_volume.py`:
```python
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

from app.utils.volume import compute_volume, _fit_base_plane


def _write_dsm(tmp_path, elev):
    # UTM zone 15N, 1 m pixels, origin easting 500000 / northing 4500000.
    h, w = elev.shape
    transform = from_origin(500000, 4500000, 1.0, 1.0)
    path = str(tmp_path / "dsm.tif")
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype="float32", crs="EPSG:32615", transform=transform, nodata=-9999,
    ) as ds:
        ds.write(elev.astype("float32"), 1)
    return path, transform


def _utm_square_to_4326(transform, r0, r1, c0, c1):
    corners = [(r0, c0), (r0, c1), (r1, c1), (r1, c0), (r0, c0)]
    ring = []
    for r, c in corners:
        x = transform.c + c * transform.a
        y = transform.f + r * transform.e
        ring.append([x, y])
    geom_utm = {"type": "Polygon", "coordinates": [ring]}
    return transform_geom("EPSG:32615", "EPSG:4326", geom_utm)


def test_flat_surface_volume_near_zero(tmp_path):
    elev = np.full((100, 100), 10.0)
    path, tr = _write_dsm(tmp_path, elev)
    poly = _utm_square_to_4326(tr, 30, 70, 30, 70)
    res = compute_volume(path, poly)
    assert res["base_plane"] == "best_fit"
    assert abs(res["volume"]) < 1.0
    assert res["area"] > 1400


def test_block_volume(tmp_path):
    elev = np.full((100, 100), 10.0)
    elev[40:60, 40:60] = 15.0  # 20x20 m block, 5 m high => 2000 m3
    path, tr = _write_dsm(tmp_path, elev)
    poly = _utm_square_to_4326(tr, 30, 70, 30, 70)
    res = compute_volume(path, poly)
    assert abs(res["volume"] - 2000) < 100
    assert res["fill"] > res["cut"]


def test_polygon_outside_raster_is_empty(tmp_path):
    elev = np.full((50, 50), 10.0)
    path, tr = _write_dsm(tmp_path, elev)
    geom_utm = {"type": "Polygon", "coordinates": [[
        [400000, 4500000], [400010, 4500000],
        [400010, 4499990], [400000, 4499990], [400000, 4500000],
    ]]}
    poly = transform_geom("EPSG:32615", "EPSG:4326", geom_utm)
    res = compute_volume(path, poly)
    assert res == {"volume": 0.0, "fill": 0.0, "cut": 0.0, "area": 0.0, "base_plane": "empty"}


def test_fit_base_plane_collinear_falls_back():
    evaluate, label = _fit_base_plane([0, 1, 2], [0, 1, 2], [5, 5, 5])
    assert label == "mean_fallback"
    assert float(evaluate(0, 0)) == 5.0


def test_fit_base_plane_planar_best_fit():
    # z = 2x + 1
    evaluate, label = _fit_base_plane([0, 1, 0, 1], [0, 0, 1, 1], [1, 3, 1, 3])
    assert label == "best_fit"
    assert abs(float(evaluate(2, 0)) - 5.0) < 1e-6
```

- [ ] **Step 3: Run the test to verify it fails**

Run (from `/home/ridwan/workspaces/webodm-geospatial`):
```bash
venv/bin/python -m pytest tests/test_volume.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.utils.volume'` (collection error).

- [ ] **Step 4: Write the implementation**

Create `/home/ridwan/workspaces/webodm-geospatial/app/utils/volume.py`:
```python
"""Volume computation over a DSM raster.

Given a polygon (GeoJSON, EPSG:4326) and a DSM GeoTIFF, mask the DSM to the
polygon, fit a best-fit base plane through the DSM elevations at the polygon's
boundary vertices, and integrate fill (above the plane) and cut (below) by a
per-pixel Riemann sum. Volumes are m3, area m2.
"""

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import transform_geom


def _fit_base_plane(xs, ys, zs):
    """Return (evaluate(xq, yq) -> ndarray, label).

    Best-fit tilted plane z = a*(x-x0) + b*(y-y0) + c when there are >= 3
    non-collinear samples; otherwise a flat plane at the mean sample elevation,
    labelled "mean_fallback". Coordinates are centred on their mean to keep the
    least-squares fit well-conditioned for large UTM values.
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    zs = np.asarray(zs, dtype=float)
    if zs.size >= 3:
        x0, y0 = xs.mean(), ys.mean()
        A = np.column_stack([xs - x0, ys - y0, np.ones(zs.size)])
        if np.linalg.matrix_rank(A) >= 3:
            coef, *_ = np.linalg.lstsq(A, zs, rcond=None)

            def evaluate(xq, yq):
                return (
                    coef[0] * (np.asarray(xq, dtype=float) - x0)
                    + coef[1] * (np.asarray(yq, dtype=float) - y0)
                    + coef[2]
                )

            return evaluate, "best_fit"

    base = float(zs.mean()) if zs.size else 0.0

    def evaluate(xq, yq):
        return np.full(np.asarray(xq, dtype=float).shape, base)

    return evaluate, "mean_fallback"


def _empty():
    return {"volume": 0.0, "fill": 0.0, "cut": 0.0, "area": 0.0, "base_plane": "empty"}


def compute_volume(path, polygon_4326):
    with rasterio.open(path) as ds:
        if ds.crs is None or not ds.crs.is_projected:
            raise ValueError("DSM is not in a projected CRS")

        geom = transform_geom("EPSG:4326", ds.crs, polygon_4326)

        try:
            data, transform = rio_mask(ds, [geom], crop=True, filled=False)
        except ValueError:
            # rasterio raises ValueError when the polygon does not overlap.
            return _empty()

        band = data[0]
        mask_arr = np.ma.getmaskarray(band)
        rows, cols = np.where(~mask_arr)
        if rows.size == 0:
            return _empty()

        cell_area = abs(transform.a * transform.e)

        # Cell centres in the raster CRS. Affine: x = c + col*a + row*b,
        # y = f + col*d + row*e; +0.5 shifts from corner to centre.
        xs_c = transform.c + (cols + 0.5) * transform.a + (rows + 0.5) * transform.b
        ys_c = transform.f + (cols + 0.5) * transform.d + (rows + 0.5) * transform.e

        # Sample DSM elevation at each polygon boundary vertex for the base plane.
        inv = ~transform
        ring = geom["coordinates"][0]
        h, w = band.shape
        vx, vy, vz = [], [], []
        for px, py in ring:
            fcol, frow = inv * (px, py)
            c = int(np.floor(fcol))
            r = int(np.floor(frow))
            if 0 <= r < h and 0 <= c < w and not mask_arr[r, c]:
                vx.append(px)
                vy.append(py)
                vz.append(float(band.data[r, c]))

        if not vz:
            base = float(band.mean())
            plane_z = np.full(rows.size, base)
            base_plane = "mean_fallback"
        else:
            evaluate, base_plane = _fit_base_plane(vx, vy, vz)
            plane_z = evaluate(xs_c, ys_c)

        dsm_z = band.data[rows, cols].astype(float)
        diff = dsm_z - plane_z
        fill = float(np.clip(diff, 0, None).sum() * cell_area)
        cut = float(np.clip(-diff, 0, None).sum() * cell_area)
        return {
            "volume": fill - cut,
            "fill": fill,
            "cut": cut,
            "area": float(rows.size * cell_area),
            "base_plane": base_plane,
        }
```

- [ ] **Step 5: Run the test to verify it passes**

Run (from `/home/ridwan/workspaces/webodm-geospatial`):
```bash
venv/bin/python -m pytest tests/test_volume.py -v
```
Expected: PASS — 5 tests OK.

- [ ] **Step 6: Commit**

```bash
cd /home/ridwan/workspaces/webodm-geospatial
git add app/utils/volume.py tests/test_volume.py requirements.txt
git commit -m "feat: add compute_volume (DSM masking + best-fit plane + Riemann sum)"
```

---

### Task 2: Geospatial — `POST /volume` endpoint

**Files:**
- Create: `/home/ridwan/workspaces/webodm-geospatial/app/routers/volume.py`
- Modify: `/home/ridwan/workspaces/webodm-geospatial/app/main.py`
- Create: `/home/ridwan/workspaces/webodm-geospatial/tests/test_volume_router.py`

**Interfaces:**
- Consumes: `compute_volume` (Task 1); `app.routers.tiles._require_raster(path)` (existing: raises `HTTPException` 400 for non-absolute, 404 for missing file).
- Produces: `POST /volume` accepting `VolumeRequest{ path: str, polygon: dict }`, returning the `compute_volume` dict. Consumed by Task 3 (Frappe proxy) via HTTP.

- [ ] **Step 1: Write the failing test**

Create `/home/ridwan/workspaces/webodm-geospatial/tests/test_volume_router.py`:
```python
import asyncio

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

from app.routers.volume import volume, VolumeRequest


def _dsm(tmp_path):
    elev = np.full((50, 50), 10.0)
    elev[20:30, 20:30] = 13.0
    tr = from_origin(500000, 4500000, 1.0, 1.0)
    path = str(tmp_path / "d.tif")
    with rasterio.open(
        path, "w", driver="GTiff", height=50, width=50, count=1,
        dtype="float32", crs="EPSG:32615", transform=tr, nodata=-9999,
    ) as ds:
        ds.write(elev.astype("float32"), 1)
    return path


def test_volume_endpoint_returns_expected_keys(tmp_path):
    path = _dsm(tmp_path)
    geom_utm = {"type": "Polygon", "coordinates": [[
        [500010, 4499990], [500040, 4499990],
        [500040, 4499960], [500010, 4499960], [500010, 4499990],
    ]]}
    poly = transform_geom("EPSG:32615", "EPSG:4326", geom_utm)
    res = asyncio.run(volume(VolumeRequest(path=path, polygon=poly)))
    assert set(res) == {"volume", "fill", "cut", "area", "base_plane"}


def test_volume_endpoint_missing_file_is_404():
    req = VolumeRequest(
        path="/nonexistent/x.tif",
        polygon={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(volume(req))
    assert excinfo.value.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `/home/ridwan/workspaces/webodm-geospatial`):
```bash
venv/bin/python -m pytest tests/test_volume_router.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers.volume'`.

- [ ] **Step 3: Write the router**

Create `/home/ridwan/workspaces/webodm-geospatial/app/routers/volume.py`:
```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.tiles import _require_raster
from app.utils.volume import compute_volume

router = APIRouter()


class VolumeRequest(BaseModel):
    path: str
    polygon: dict


@router.post("")
async def volume(req: VolumeRequest):
    """Compute fill/cut/net volume of a polygon over a DSM raster."""
    _require_raster(req.path)
    try:
        return compute_volume(req.path, req.polygon)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
```

- [ ] **Step 4: Register the router in main.py**

In `/home/ridwan/workspaces/webodm-geospatial/app/main.py`, change:
```python
from app.routers import tiles, export, pointcloud
```
to:
```python
from app.routers import tiles, export, pointcloud, volume
```
and, after the line:
```python
app.include_router(pointcloud.router, prefix="/pointcloud", tags=["pointcloud"])
```
add:
```python
app.include_router(volume.router, prefix="/volume", tags=["volume"])
```

- [ ] **Step 5: Run the test to verify it passes**

Run (from `/home/ridwan/workspaces/webodm-geospatial`):
```bash
venv/bin/python -m pytest tests/test_volume_router.py -v
```
Expected: PASS — 2 tests OK.

- [ ] **Step 6: Commit**

```bash
cd /home/ridwan/workspaces/webodm-geospatial
git add app/routers/volume.py app/main.py tests/test_volume_router.py
git commit -m "feat: add POST /volume endpoint"
```

---

### Task 3: Frappe — `volume` tile-proxy method

**Files:**
- Modify: `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_core/webodm_core/api/tiles.py`
- Create: `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_core/webodm_core/api/test_tiles.py`

**Interfaces:**
- Consumes: existing `_resolve_raster_path(task_name, dataset)` (raises `frappe.DoesNotExistError` when the task has no such raster), `_geospatial_url()`, module-level `requests`. The geospatial `POST /volume` from Task 2.
- Produces: whitelisted `volume(task_name, polygon)` returning the geospatial JSON dict. Consumed by the frontend (Task 6) at `/api/method/webodm_core.api.tiles.volume`.

- [ ] **Step 1: Write the failing test**

Create `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_core/webodm_core/api/test_tiles.py`:
```python
import unittest
from unittest.mock import patch, MagicMock

import frappe

from webodm_core.api import tiles


class TestVolumeProxy(unittest.TestCase):
    def test_missing_dsm_raises(self):
        with patch.object(tiles, "_resolve_raster_path", side_effect=frappe.DoesNotExistError):
            with self.assertRaises(frappe.DoesNotExistError):
                tiles.volume("TASK-1", '{"type":"Polygon","coordinates":[]}')

    def test_forwards_path_and_polygon(self):
        resp = MagicMock()
        resp.json.return_value = {"volume": 1.0, "fill": 1.0, "cut": 0.0, "area": 2.0, "base_plane": "best_fit"}
        resp.raise_for_status.return_value = None
        with patch.object(tiles, "_resolve_raster_path", return_value="/abs/dsm.tif"), \
             patch.object(tiles, "_geospatial_url", return_value="http://geo:5000"), \
             patch.object(tiles.requests, "post", return_value=resp) as post:
            out = tiles.volume(
                "TASK-1",
                '{"type":"Polygon","coordinates":[[[0,0],[0,1],[1,1],[0,0]]]}',
            )
        self.assertEqual(out["volume"], 1.0)
        _args, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["path"], "/abs/dsm.tif")
        self.assertEqual(kwargs["json"]["polygon"]["type"], "Polygon")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `/home/ridwan/workspace/g20-daas/frappe-bench`):
```bash
bench --site webodm.local run-tests --module webodm_core.api.test_tiles
```
Expected: FAIL — `AttributeError: module 'webodm_core.api.tiles' has no attribute 'volume'`.

- [ ] **Step 3: Add the proxy method**

In `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_core/webodm_core/api/tiles.py`, append at end of file:
```python


@frappe.whitelist(allow_guest=False)
def volume(task_name, polygon):
    """Compute stockpile/earthwork volume for a polygon over the task's DSM.

    ``polygon`` is a GeoJSON Polygon (EPSG:4326), sent as a JSON string. Resolves
    the task's DSM (throws if absent) and forwards to the geospatial service.
    """
    path = _resolve_raster_path(task_name, "dsm")
    poly = frappe.parse_json(polygon) if isinstance(polygon, str) else polygon
    try:
        resp = requests.post(
            f"{_geospatial_url().rstrip('/')}/volume",
            json={"path": path, "polygon": poly},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        frappe.throw(f"Geospatial service unavailable: {e}")
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `/home/ridwan/workspace/g20-daas/frappe-bench`):
```bash
bench --site webodm.local run-tests --module webodm_core.api.test_tiles
```
Expected: PASS — 2 tests OK.

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_core
git add webodm_core/api/tiles.py webodm_core/api/test_tiles.py
git commit -m "feat: add volume tile-proxy method forwarding to geospatial /volume"
```

---

### Task 4: Frontend — `formatVolume`

**Files:**
- Modify: `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend/src/lib/format.js`
- Test: `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend/src/lib/format.test.js`

**Interfaces:**
- Produces: `formatVolume({ volume, fill, cut, area }) -> string`. Consumed by `MapView.computeVolume` (Task 6).

- [ ] **Step 1: Write the failing test**

Append to `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend/src/lib/format.test.js` (add the import at the top and the new describe block):

At the top, change:
```js
import { formatDistance, formatArea } from '@/lib/format'
```
to:
```js
import { formatDistance, formatArea, formatVolume } from '@/lib/format'
```
Then add at the end of the file:
```js
describe('formatVolume', () => {
  it('formats volume with fill/cut/area and thousands separators', () => {
    const s = formatVolume({ volume: 1240, fill: 1310, cut: 70, area: 890 })
    expect(s).toBe('1,240 m³ (fill 1,310 / cut 70) · 890 m²')
  })
  it('handles a net-cut (negative volume)', () => {
    const s = formatVolume({ volume: -500, fill: 100, cut: 600, area: 300 })
    expect(s).toContain('-500 m³')
    expect(s).toContain('cut 600')
  })
  it('reports empty when area is zero', () => {
    expect(formatVolume({ volume: 0, fill: 0, cut: 0, area: 0 })).toBe('No DSM data under polygon')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from the frontend app dir):
```bash
npm test -- format
```
Expected: FAIL — `formatVolume is not a function` / import undefined.

- [ ] **Step 3: Add the implementation**

Append to `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend/src/lib/format.js`:
```js

export function formatVolume({ volume, fill, cut, area } = {}) {
  const a = Number.isFinite(area) ? area : 0
  if (a === 0) return 'No DSM data under polygon'
  const v = Number.isFinite(volume) ? volume : 0
  const f = Number.isFinite(fill) ? fill : 0
  const c = Number.isFinite(cut) ? cut : 0
  const n = x => Math.round(x).toLocaleString('en-US')
  return `${n(v)} m³ (fill ${n(f)} / cut ${n(c)}) · ${n(a)} m²`
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from the frontend app dir):
```bash
npm test -- format
```
Expected: PASS — the three new `formatVolume` assertions plus the existing distance/area tests.

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend
git add frontend/src/lib/format.js frontend/src/lib/format.test.js
git commit -m "feat: add formatVolume readout formatter"
```

---

### Task 5: Frontend — `useMeasure` volume mode

Extend the draw engine with a `volume` mode: draws a polygon exactly like `area`, but on finish calls an injected async `onVolume` callback and shows the returned string (with a "Computing…" interim state).

**Files:**
- Modify: `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend/src/composables/useMeasure.js`
- Test: `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend/src/composables/useMeasure.import.test.js`

**Interfaces:**
- Consumes: `formatDistance`, `formatArea` (existing); an optional `onVolume(latlngs: L.LatLng[]) -> Promise<string>` passed by the caller.
- Produces: `useMeasure(getMap, { onVolume } = {}) -> { state, start, finish, clear }`; `state.mode` now `'distance'|'area'|'volume'|null`. Consumed by `MapView.vue` (Task 6).

- [ ] **Step 1: Update the import smoke test**

Replace the contents of `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend/src/composables/useMeasure.import.test.js` with:
```js
import { describe, it, expect } from 'vitest'
import { useMeasure } from '@/composables/useMeasure'

// Import-resolution + interface smoke test. useMeasure() does not touch the map
// until start()/finish(), so a null-returning accessor and no-op onVolume are safe.
describe('useMeasure module', () => {
  it('imports cleanly and returns the expected interface', () => {
    const m = useMeasure(() => null)
    expect(typeof m.start).toBe('function')
    expect(typeof m.finish).toBe('function')
    expect(typeof m.clear).toBe('function')
    expect(m.state).toMatchObject({ mode: null, value: 0, formatted: '' })
  })

  it('accepts an onVolume option without changing the interface', () => {
    const m = useMeasure(() => null, { onVolume: async () => 'x' })
    expect(typeof m.start).toBe('function')
    expect(m.state.mode).toBe(null)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from the frontend app dir):
```bash
npm test -- useMeasure.import
```
Expected: FAIL — the second test throws because `useMeasure` currently ignores a second argument in a way that... actually it will PASS by accident (extra args are ignored). To make this a real RED, first confirm current behavior:
Run the command; if both tests already pass, that is acceptable here — the meaningful verification is Step 4 (the volume-mode logic compiles and the SFC integration in Task 6 works). Proceed to Step 3 regardless.

- [ ] **Step 3: Rewrite `useMeasure.js` with volume mode**

Replace the entire contents of `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend/src/composables/useMeasure.js` with:
```js
import { reactive } from 'vue'
import L from 'leaflet'
import { length as turfLength, area as turfArea } from '@turf/turf'
import { formatDistance, formatArea } from '@/lib/format'

const DRAW_COLOR = '#2563eb'

// Click-to-draw distance/area/volume measurement over a Leaflet map.
// getMap() returns the live L.Map. onVolume(latlngs) -> Promise<string> is
// called when a volume polygon is finished; distance/area need no callback.
export function useMeasure(getMap, { onVolume } = {}) {
  const state = reactive({ mode: null, value: 0, formatted: '' })

  let points = [] // L.LatLng[]
  let shape = null // L.Polyline (distance) | L.Polygon (area/volume)
  let dots = [] // L.CircleMarker[]
  let reqToken = 0 // guards stale async volume results

  function toCoords(latlngs) {
    return latlngs.map(p => [p.lng, p.lat]) // GeoJSON is [lng, lat]
  }

  function isPolygonMode() {
    return state.mode === 'area' || state.mode === 'volume'
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
    // 'volume' does no live computation while drawing; the readout is set on finish().
  }

  function redraw() {
    const map = getMap()
    if (!map) return
    if (!shape) {
      shape = isPolygonMode()
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

  function setReadout(text) {
    state.formatted = text
    if (shape) shape.setTooltipContent(text)
  }

  async function finishVolume() {
    stopListening()
    if (points.length < 3) {
      clear()
      return
    }
    redraw()
    setReadout('Computing…')
    const token = ++reqToken
    const latlngs = points.slice()
    try {
      const text = onVolume ? await onVolume(latlngs) : ''
      if (state.mode === 'volume' && token === reqToken) setReadout(text)
    } catch (e) {
      if (state.mode === 'volume' && token === reqToken) setReadout('Volume failed')
    }
  }

  function finish() {
    if (state.mode === 'volume') {
      finishVolume()
      return
    }
    recompute()
    redraw()
    stopListening()
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

  function clear() {
    const map = getMap()
    stopListening()
    reqToken++ // invalidate any in-flight volume request
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

- [ ] **Step 4: Run the smoke test and verify the SFC-independent module compiles**

Run (from the frontend app dir):
```bash
npm test -- useMeasure.import
```
Expected: PASS — both interface tests green (imports resolve, `onVolume` option accepted).

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend
git add frontend/src/composables/useMeasure.js frontend/src/composables/useMeasure.import.test.js
git commit -m "feat: add volume mode to useMeasure (async onVolume + stale-result guard)"
```

---

### Task 6: Frontend — MapView volume wiring

Add the Volume toolbar button (disabled when the task has no DSM), the `computeVolume` backend call, and the `currentTask`/`hasDsm` state. Verified by SFC compile + symbol check + manual browser check.

**Files:**
- Modify: `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend/src/pages/MapView.vue`

**Interfaces:**
- Consumes: `useMeasure(getMap, { onVolume })` (Task 5); `formatVolume` (Task 4); the whitelisted `webodm_core.api.tiles.volume` (Task 3). Existing symbols: `selectedTask` (task **name** string), `currentImages`, `toast`, `FeatherIcon`, `Button`, `map`.
- Produces: `currentTask` ref, `hasDsm` computed, `computeVolume(latlngs)`, and a `'volume'` toolbar button.

- [ ] **Step 1: Import `formatVolume`**

In `MapView.vue`, change:
```js
import { toast } from 'frappe-ui'
```
to:
```js
import { toast } from 'frappe-ui'
import { formatVolume } from '@/lib/format'
```
(If `@/lib/format` is already imported for another symbol, instead add `formatVolume` to that existing import list.)

- [ ] **Step 2: Add `currentTask` ref and `hasDsm` computed**

In `MapView.vue`, find the line `const currentImages = ref([])` and immediately after it add:
```js
const currentTask = ref(null)
const hasDsm = computed(() => !!currentTask.value?.dsm)
```
(`computed` is already imported — it backs the existing `hasGps`.)

- [ ] **Step 3: Set `currentTask` and clear a stale measurement in `selectTask`**

`selectTask` does not currently call `measure.clear()`, so a volume readout drawn on task A would linger over task B. In `MapView.vue`, find the line `currentImages.value = full.images || []` (inside `selectTask`) and immediately after it add:
```js
  currentTask.value = full
  measure.clear()
```
This also bumps `useMeasure`'s `reqToken` (Task 5), discarding any volume request still in flight from the previous task.

- [ ] **Step 4: Pass `onVolume` into `useMeasure`**

In `MapView.vue`, change:
```js
const measure = useMeasure(() => map)
```
to:
```js
const measure = useMeasure(() => map, { onVolume: computeVolume })
```

- [ ] **Step 5: Add `computeVolume`**

In `MapView.vue`, immediately after the `clearMeasure` function, add:
```js
async function computeVolume(latlngs) {
  try {
    const ring = latlngs.map(p => [p.lng, p.lat])
    if (ring.length) {
      const [fx, fy] = ring[0]
      const [lx, ly] = ring[ring.length - 1]
      if (fx !== lx || fy !== ly) ring.push([fx, fy]) // close the ring
    }
    const polygon = { type: 'Polygon', coordinates: [ring] }
    const headers = { 'Content-Type': 'application/json' }
    if (window.csrf_token) headers['X-Frappe-CSRF-Token'] = window.csrf_token
    const res = await fetch('/api/method/webodm_core.api.tiles.volume', {
      method: 'POST',
      headers,
      body: JSON.stringify({ task_name: selectedTask.value, polygon: JSON.stringify(polygon) }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.message || 'Volume calculation failed')
    }
    const data = await res.json()
    return formatVolume(data.message || data)
  } catch (e) {
    toast.error(e.message || 'Volume calculation failed')
    throw e // let useMeasure show "Volume failed"
  }
}
```

- [ ] **Step 6: Add the Volume toolbar button**

In `MapView.vue`, in the measurement toolbar, find the Area button block:
```html
        <Button size="sm" :variant="measure.state.mode === 'area' ? 'solid' : 'outline'" @click="startMeasure('area')" title="Measure area">
          <template #prefix><FeatherIcon name="square" class="h-3.5 w-3.5" /></template>
          Area
        </Button>
```
and immediately after it (before the Clear button) add:
```html
        <Button
          size="sm"
          :variant="measure.state.mode === 'volume' ? 'solid' : 'outline'"
          :disabled="!hasDsm"
          @click="startMeasure('volume')"
          :title="hasDsm ? 'Measure volume (needs DSM)' : 'Volume requires a DSM'"
        >
          <template #prefix><FeatherIcon name="box" class="h-3.5 w-3.5" /></template>
          Volume
        </Button>
```

- [ ] **Step 7: Verify the SFC compiles and symbols resolve**

Run (from the frontend app dir):
```bash
node -e "
const fs=require('fs');
const {parse,compileScript,compileTemplate}=require('@vue/compiler-sfc');
const src=fs.readFileSync('src/pages/MapView.vue','utf8');
const {descriptor,errors}=parse(src,{filename:'MapView.vue'});
if(errors.length){console.error('PARSE',errors);process.exit(1);}
const s=compileScript(descriptor,{id:'x'});
const t=compileTemplate({source:descriptor.template.content,filename:'MapView.vue',id:'x',compilerOptions:{bindingMetadata:s.bindings}});
if(t.errors.length){console.error('TEMPLATE',t.errors);process.exit(1);}
console.log('SFC compiles OK');
"
```
Expected: `SFC compiles OK`.

Then confirm each referenced symbol is defined:
```bash
for s in computeVolume currentTask hasDsm formatVolume startMeasure "onVolume: computeVolume"; do
  echo "$s: $(grep -c "$s" src/pages/MapView.vue)"
done
```
Expected: every count ≥ 1.

- [ ] **Step 8: Run the full frontend suite**

Run (from the frontend app dir):
```bash
npm test
```
Expected: PASS — all suites green (format incl. formatVolume, flightPath, mapLayers, useMeasure.import).

- [ ] **Step 9: Manual verification**

With the geospatial service running (`venv/bin/uvicorn app.main:app --port 5000` from the geospatial repo) and `npm run dev` (frontend, in your own terminal), open a task **that has a DSM**:
- The **Volume** button is enabled; on a task without a DSM it is disabled/greyed.
- Click **Volume**, click a polygon around a raised feature, double-click to finish → readout shows "Computing…" then `"<n> m³ (fill … / cut …) · … m²"`.
- Esc / Clear removes the polygon; switching mode to Distance/Area still works.
- A polygon drawn entirely off the DSM shows "No DSM data under polygon".

- [ ] **Step 10: Commit**

```bash
cd /home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/MapView.vue
git commit -m "feat: MapView volume measurement (toolbar button + backend call)"
```

---

## Self-Review

**1. Spec coverage:**
- Base plane best-fit + mean_fallback → Task 1 (`_fit_base_plane`). ✓
- DSM-only, disabled without DSM → Task 6 (`hasDsm`, button `:disabled`), Task 3 (`_resolve_raster_path(..., "dsm")` throws). ✓
- Compute on geospatial, proxied → Tasks 1–2 (endpoint), Task 3 (proxy). ✓
- Riemann sum, cell area, response keys → Task 1 (verbatim from Global Constraints). ✓
- Empty / outside-coverage → Task 1 `_empty()`, formatVolume empty string. ✓
- Projected-CRS guard → Task 1 `ValueError`, Task 2 → HTTP 422. ✓
- Volume as third draw mode like area → Task 5. ✓
- "Computing…" interim + stale-result guard → Task 5 (`reqToken`). ✓
- Readout volume+fill+cut+area → Task 4 `formatVolume`. ✓
- Error → toast + "Volume failed", polygon retained → Task 6 (`toast.error`+rethrow), Task 5 (catch → `setReadout('Volume failed')`, no clear). ✓
- Task-switch mid-compute discards stale result → Task 5 (`clear()` bumps `reqToken`; `selectTask` calls `measure.clear()` in existing code). ✓
- No new deps, no migration → confirmed (pytest is a geospatial dev tool, not a runtime dep of any app). ✓
- Testing: geospatial pytest (Tasks 1–2), Frappe test (Task 3), Vitest (Task 4), manual (Task 6). ✓

**2. Placeholder scan:** No TBD/TODO/"handle errors" — every code step has full code. Task 5 Step 2 explicitly allows the smoke test to already pass (documented, not a placeholder).

**3. Type consistency:** `compute_volume(path, polygon_4326)` and its dict keys are identical across Tasks 1, 2, 3 and Global Constraints. `_fit_base_plane` returns `(evaluate, label)` consistently. `VolumeRequest{path, polygon}` matches the Frappe proxy's `json={"path", "polygon"}` (Task 3) and the endpoint (Task 2). `formatVolume({volume,fill,cut,area})` (Task 4) matches the keys `computeVolume` passes through (Task 6) and `compute_volume` returns (Task 1). `useMeasure(getMap, {onVolume})` (Task 5) matches the MapView call (Task 6). `onVolume(latlngs)` receives `L.LatLng[]`; `computeVolume` reads `p.lng`/`p.lat` — consistent.

One note carried into Task 6: verified against the current code that `selectTask` does NOT call `measure.clear()`. Task 6 Step 3 therefore adds `measure.clear()` alongside `currentTask.value = full`, which (via Task 5's `reqToken` bump in `clear()`) also discards any volume request still in flight when the user switches tasks mid-compute.
