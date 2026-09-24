# WebODM Geospatial Service

Standalone FastAPI microservice for raster tile serving and COG processing.
Part of the WebODM Frappe rework.

The service is **stateless**: it never touches Frappe storage or auth. Frappe
resolves a task's raster to an absolute on-disk path (permission-checked) and
passes that path to this service, which reads the file directly from shared
storage. Point cloud endpoints are stubbed pending Phase 4.

## API

| Method | Endpoint | Status | Notes |
|---|---|---|---|
| GET | `/health` | ✅ | Liveness check |
| GET | `/tiles/info?path=` | ✅ | Bounds (EPSG:4326), zoom range, band stats |
| GET | `/raster/metadata?path=` | ✅ | Normalized header metadata (size, bands, dtype, CRS, geotransform, bounds, nodata, tiling, compression, overviews, COG flag) |
| GET | `/tiles/tile/{z}/{x}/{y}.png?path=&kind=` | ✅ | XYZ tile PNG; `kind` = `orthophoto`\|`dsm`\|`dtm` |
| POST | `/export/cogify` | ✅ | Convert raster → COG (in place), return georef + `metadata` |
| POST | `/export/raster` | 🚧 Stub | GeoTIFF/PNG/KMZ export |
| POST | `/export/hillshade` | 🚧 Stub | Hillshade from DEM |
| POST | `/export/colormap` | 🚧 Stub | Apply custom colormap |
| POST | `/export/formula` | 🚧 Stub | Band formula (NDVI, etc.) |
| POST | `/pointcloud/export` | 🚧 Stub | LAS/LAZ/PLY export |
| POST | `/pointcloud/to-potree` | 🚧 Stub | Potree conversion |

### Tile rendering

- **orthophoto** — rendered as RGB(A); alpha masks nodata so the area outside
  the raster is transparent.
- **dsm / dtm** — single-band DEM stretched to its min/max and colored with a
  terrain ramp.
- Tiles outside the raster return a 1×1 transparent PNG (HTTP 200), so Leaflet
  renders empty space rather than broken-image tiles.

### `/export/cogify`

Request: `{ "path": "/abs/path/raster.tif", "dst_path": null }`
(`dst_path` optional; defaults to converting in place, idempotent if already a COG).

Response: `path`, `is_cog`, `epsg`, `wkt`, `extent` (GeoJSON Polygon, EPSG:4326),
`bounds_4326` `[minx,miny,maxx,maxy]`, `band_count`, `width`, `height`, and
`metadata` — the `/raster/metadata` dict for the written COG (`null` if that
read failed; the conversion itself still succeeded).

### `/raster/metadata`

Header-only read (`rasterio.open` + IFD fields, no pixel I/O), so it costs the
same for a 50 GB orthophoto as for a fixture. Every key is always present;
fields that do not apply are `null`:

| Key | Meaning |
|---|---|
| `driver`, `file_size`, `width`, `height`, `band_count`, `dtype` | Format, bytes on disk, pixels, bands, NumPy dtype of band 1 |
| `epsg`, `crs_wkt`, `crs_units` | CRS as EPSG code, or WKT when it has none; `metre`/`degree`/... |
| `is_georeferenced` | CRS **and** a non-identity geotransform |
| `geotransform` | GDAL order `[origin_x, pixel_w, rot_x, origin_y, rot_y, -pixel_h]` |
| `pixel_size` | `[x, y]` in CRS units (positive) |
| `bounds`, `bounds_4326`, `extent` | Native `[minx,miny,maxx,maxy]`; the same in EPSG:4326; GeoJSON Polygon |
| `nodata` | Number, or the string `"nan"` (a NaN cannot travel through JSON), or `null` |
| `block_width`, `block_height`, `is_tiled` | Internal tile/strip layout |
| `compression`, `overviews`, `is_cog` | e.g. `deflate`; decimation factors `[2,4,8]`; rio-cogeo validation |
| `color_interp` | Per band, e.g. `["red","green","blue","alpha"]`, `["gray"]`, `["palette"]` |

Errors: 400 relative path, 404 missing file, 422 not a readable raster
(corrupt, truncated header, unsupported format) with GDAL's reason in `detail`.
A raster with a geotransform but no CRS is reported, not rejected
(`is_georeferenced: false`, native `bounds` set, `bounds_4326: null`).

## Quick Start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 5000
```

Interactive API docs at `http://127.0.0.1:5000/docs`.

Under the WebODM bench this runs as the `geospatial` process in the Procfile, so
`bench start` launches it alongside Frappe. Frappe locates it via the
`geospatial_url` / `webodm_geospatial_url` site-config keys (default
`http://127.0.0.1:5000`).

## Dependencies

GDAL ships bundled inside the rasterio / rio-tiler / rio-cogeo manylinux wheels —
**no system GDAL install is required**. Pinned in `requirements.txt`; tested with
rasterio 1.5, rio-tiler 9.4, rio-cogeo 7.0, numpy 2.5 on Python 3.12.

## Docker

```bash
docker build -t webodm-geospatial .
docker run -p 5000:5000 -v /data:/data webodm-geospatial
```

## Project Structure

```
app/
├── main.py              # FastAPI entrypoint + CORS; mounts routers under
│                        #   /tiles, /export, /pointcloud
├── routers/
│   ├── tiles.py         # /info, /tile/{z}/{x}/{y}.png  (rio-tiler)
│   ├── export.py        # /cogify (implemented); raster/hillshade/... (stub)
│   ├── raster.py        # /metadata (header-only raster metadata)
│   └── pointcloud.py    # export, to-potree (stub)
├── models/
│   └── task.py          # Pydantic request models
└── utils/
    ├── raster.py        # is_cog, to_cog, read_georef, read_metadata, tile_info, render_tile
    └── storage.py       # file path resolution

Dockerfile
requirements.txt
```
