# WebODM Geospatial Service

Standalone FastAPI microservice for raster tile serving and COG processing.
Part of the WebODM Frappe rework.

The service is **stateless**: it never touches Frappe storage or auth. Frappe
resolves a task's raster (permission-checked) to either an absolute path on the
shared volume — the host serving cache — or an `s3://bucket/key` URI, and
passes that to this service. Local paths are read from disk; object URIs are
read straight from S3 through GDAL's `/vsis3/` with HTTP range requests, which
is what makes tiling a Cloud-Optimized GeoTIFF in object storage cheap (a few
small reads per tile, never a download). Point cloud endpoints are stubbed
pending Phase 4.

Object storage is opt-in and environment-configured (`S3_BUCKETS` allow-list,
`S3_ENDPOINT_URL` for MinIO-style services, `AWS_REGION`, `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` or their `*_FILE` variants, `COG_SCRATCH_DIR`); see
`app/utils/objectstore.py` and `docs/on-demand-processing/configuration.md`.
Without it every path must be local, exactly as before.

## API

| Method | Endpoint | Status | Notes |
|---|---|---|---|
| GET | `/health` | ✅ | Liveness check |
| GET | `/tiles/info?path=` | ✅ | Bounds (EPSG:4326), zoom range, band stats |
| GET | `/tiles/tile/{z}/{x}/{y}.png?path=&kind=` | ✅ | XYZ tile PNG; `kind` = `orthophoto`\|`dsm`\|`dtm` |
| POST | `/export/cogify` | ✅ | Convert raster → COG (in place, or S3 → S3), return georef + `metadata` |
| GET | `/raster/metadata?path=` | ✅ | Header-only raster metadata (size, bands, CRS, transform, blocks, overviews…) |
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

Request: `{ "path": "/abs/path/raster.tif", "output_path": null }`
(`output_path` optional; defaults to converting in place, idempotent if already a
COG; `dst_path` is accepted as the older name). Both fields take an absolute path
or an `s3://bucket/key` URI. With an object source `output_path` is required;
the COG is built in `COG_SCRATCH_DIR` (container scratch, never the shared
volume) and uploaded, so an S3 → S3 conversion touches no host disk. The
embedded `metadata` is read from the *output*, i.e. from the S3 object.

Response: `path`, `is_cog`, `epsg`, `wkt`, `extent` (GeoJSON Polygon, EPSG:4326),
`bounds_4326` `[minx,miny,maxx,maxy]`, `band_count`, `width`, `height`, and
`metadata` (the `/raster/metadata` document of the output, or `null` if reading it failed).

### `/raster/metadata`

`GET /raster/metadata?path=/abs/path/raster.tif` — everything GDAL knows from the
file header, without reading pixels (milliseconds for a multi-GB orthophoto):

| Key | Meaning |
|---|---|
| `driver`, `file_size` | GDAL driver short name (`GTiff`), size on disk in bytes |
| `width`, `height`, `band_count`, `dtype`, `dtypes` | Raster grid; `dtype` is band 1 |
| `crs` | `{epsg, wkt, units, is_geographic, is_projected}` — all `null`/false without a CRS |
| `georeference` | `full` \| `no_crs` \| `no_transform` \| `none` |
| `geotransform` | 6 numbers in GDAL order `[x0, xres, xrot, y0, yrot, -yres]` (`null` without a transform) |
| `pixel_size` | `[xres, yres]` absolute, in CRS units |
| `bounds`, `bounds_4326`, `extent` | Native `[minx,miny,maxx,maxy]`; EPSG:4326 bounds and GeoJSON Polygon when `georeference` is `full` |
| `nodata` | Dataset nodata, `null` when unset; NaN is the string `"nan"` |
| `is_tiled`, `block_size` | Internal tiling and `[width, height]` of a block (band 1) |
| `compression`, `interleave`, `predictor` | From the TIFF image structure (`deflate`, `pixel`, `2`…) |
| `overviews`, `overview_count` | Decimation factors of band 1's overviews |
| `is_cog` | rio-cogeo validation result (GeoTIFF only) |
| `color_interpretation`, `has_colormap` | Per-band names (`red`, `alpha`, `gray`, `palette`…); colour table present |
| `bands` | Per band: `index`, `dtype`, `color_interpretation`, `nodata`, `overviews`, `block_size` |
| `software`, `area_or_point` | `TIFFTAG_SOFTWARE` (e.g. `ODM 3.5.6`) and `AREA_OR_POINT` |

Errors: 400 relative path, 404 missing file, 422 GDAL cannot open the file.
A raster without a CRS is not an error (`georeference: "no_crs"`).

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
    ├── raster.py        # is_cog, to_cog, read_georef, tile_info, render_tile
    └── storage.py       # file path resolution

Dockerfile
requirements.txt
```
