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
| GET | `/tiles/tile/{z}/{x}/{y}.png?path=&kind=` | ✅ | XYZ tile PNG; `kind` = `orthophoto`\|`dsm`\|`dtm` |
| POST | `/export/cogify` | ✅ | Convert raster → COG (in place), return georef |
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
`bounds_4326` `[minx,miny,maxx,maxy]`, `band_count`, `width`, `height`.

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
│   └── pointcloud.py    # export, to-potree (stub)
├── models/
│   └── task.py          # Pydantic request models
└── utils/
    ├── raster.py        # is_cog, to_cog, read_georef, tile_info, render_tile
    └── storage.py       # file path resolution

Dockerfile
requirements.txt
```
