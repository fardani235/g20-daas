"""Synthetic fixtures: one construction site surveyed twice.

The site is defined analytically in world coordinates (UTM 33N), so the two
"surveys" can be rendered on different grids, extents and coordinate systems
and still describe the same ground — exactly what two processing runs do.

Ground: a gentle slope with smooth undulations and three flat-roofed
buildings (they give the horizontal alignment something to hold on to).

What happens between the two dates (``CHANGES``), in site metres from the
south-west corner:

- pit        30 x 20 m, 2.0 m deep           cut  1200 m³
- stockpile  20 x 20 m, 1.5 m high           fill  600 m³
- trench     40 x 1 m, 1.0 m deep            cut    40 m³ (thin: must survive the noise filters)
- speck      1 x 1 m, 0.5 m high             below the minimum area: dropped
- trees      15 x 15 m of green that grew 3 m  vegetation, not earthwork
- paving     12 x 12 m resurfaced, same height  appearance only

Independent-run artefacts are options of ``survey()``: a constant height
offset, a tilt, a horizontal shift, noise, a different resolution / extent /
CRS, and a darker exposure for the second orthophoto.
"""

import os
import sys

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import Resampling, calculate_default_transform, reproject
from scipy import ndimage

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

SITE_CRS = "EPSG:32633"
ORIGIN = (500000.0, 4500000.0)   # south-west corner of the site
SITE = (200.0, 160.0)            # width, height in metres

# name: (x0, y0, x1, y1, height change in metres)
CHANGES = {
    "pit": (30.0, 30.0, 60.0, 50.0, -2.0),
    "stockpile": (100.0, 40.0, 120.0, 60.0, 1.5),
    "trench": (40.0, 100.0, 80.0, 101.0, -1.0),
    "speck": (150.0, 120.0, 151.0, 121.0, 0.5),
}
TREES = (140.0, 30.0, 155.0, 45.0, 3.0)
PAVING = (110.0, 100.0, 122.0, 112.0)
BUILDINGS = [(20.0, 110.0, 32.0, 125.0, 6.0), (150.0, 70.0, 165.0, 80.0, 4.5), (85.0, 15.0, 95.0, 22.0, 5.0)]

FILL_M3 = 600.0
CUT_M3 = 1240.0


def _inside(x, y, box):
    return (x >= box[0]) & (x < box[2]) & (y >= box[1]) & (y < box[3])


def ground(x, y):
    """Height of the unchanged site at site coordinates (metres)."""
    z = 100.0 + 0.02 * x + 0.01 * y + 0.4 * np.sin(x / 23.0) * np.cos(y / 31.0)
    for b in BUILDINGS:
        z = np.where(_inside(x, y, b), z + b[4], z)
    return z


def surface(x, y, changed: bool, trees: bool = True):
    z = ground(x, y)
    if changed:
        for box in CHANGES.values():
            z = np.where(_inside(x, y, box), z + box[4], z)
        if trees:
            z = np.where(_inside(x, y, TREES), z + TREES[4], z)
    return z


def colour(x, y, changed: bool, gain: float = 1.0):
    """RGB in [0, 1]: textured soil, grey roofs, a green tree patch, a resurfaced square."""
    rng = np.random.default_rng(7)
    texture = ndimage.gaussian_filter(rng.normal(0, 1, x.shape), 1.5) * 0.12
    stripes = 0.03 * np.sin(x / 1.7) * np.sin(y / 2.3)
    r = 0.55 + texture + stripes
    g = 0.45 + texture + stripes
    b = 0.35 + texture + stripes
    for bl in BUILDINGS:
        inside = _inside(x, y, bl)
        r, g, b = np.where(inside, 0.6, r), np.where(inside, 0.6, g), np.where(inside, 0.62, b)
    trees = _inside(x, y, TREES)
    r, g, b = np.where(trees, 0.2, r), np.where(trees, 0.5 + 0.5 * texture, g), np.where(trees, 0.15, b)
    if changed:
        paving = _inside(x, y, PAVING)
        r, g, b = np.where(paving, 0.25, r), np.where(paving, 0.27, g), np.where(paving, 0.33, b)
    return np.clip(np.stack([r, g, b]) * gain, 0, 1)


def _grid_coords(res, extent):
    x0, y0, x1, y1 = extent
    w, h = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    transform = from_origin(ORIGIN[0] + x0, ORIGIN[1] + y1, res, res)
    xs = x0 + (np.arange(w) + 0.5) * res
    ys = y1 - (np.arange(h) + 0.5) * res
    xx, yy = np.meshgrid(xs, ys)
    return xx, yy, transform, w, h


def _write(path, data, transform, crs=SITE_CRS, nodata=None, alpha=False):
    count = data.shape[0] if data.ndim == 3 else 1
    profile = {"driver": "GTiff", "width": data.shape[-1], "height": data.shape[-2], "count": count,
               "dtype": str(data.dtype), "crs": crs, "transform": transform,
               # Tiled like the platform's rasters (COGs); strip files warp far slower.
               "tiled": True, "blockxsize": 256, "blockysize": 256}
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data if data.ndim == 3 else data[None])
        if alpha:
            dst.colorinterp = [rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                               rasterio.enums.ColorInterp.blue, rasterio.enums.ColorInterp.alpha]
    return path


def survey(tmp_path, name, *, changed=False, res=0.25, extent=(0.0, 0.0, 200.0, 160.0), offset=0.0,
           shift=(0.0, 0.0), tilt=(0.0, 0.0), noise=0.02, seed=1, trees=True, gain=1.0,
           border=1.0, with_ortho=True, dome=0.0):
    """Render one survey: ``{"dsm": path, "orthophoto": path}``.

    ``shift`` (east, north, metres) displaces the whole survey: the feature
    that is really at ``x`` appears at ``x + shift``. ``tilt`` is the height
    error in metres from one side of the site to the other (east-west,
    north-south). ``border`` metres of nodata frame the data.
    """
    xx, yy, transform, w, h = _grid_coords(res, extent)
    sx, sy = xx - shift[0], yy - shift[1]
    z = surface(sx, sy, changed, trees=trees) + offset
    z += tilt[0] * (xx / SITE[0] - 0.5) + tilt[1] * (yy / SITE[1] - 0.5)
    if dome:
        z += dome * (((xx / SITE[0] - 0.5) ** 2 + (yy / SITE[1] - 0.5) ** 2) * 4.0)
    if noise:
        rng = np.random.default_rng(seed)
        # Slightly correlated, like dense-matching noise.
        z += ndimage.gaussian_filter(rng.normal(0, 1, z.shape), 0.8) * noise * 2.4
    data = z.astype("float32")
    edge = int(round(border / res))
    if edge:
        mask = np.ones(data.shape, dtype=bool)
        mask[edge:-edge, edge:-edge] = False
        data[mask] = -9999.0
    out = {"dsm": _write(str(tmp_path / f"{name}_dsm.tif"), data, transform, nodata=-9999.0)}
    if with_ortho:
        rgb = (colour(sx, sy, changed, gain) * 255).astype("uint8")
        alpha = np.full((1, h, w), 255, dtype="uint8")
        if edge:
            alpha[0][mask] = 0
        out["orthophoto"] = _write(str(tmp_path / f"{name}_orthophoto.tif"),
                                   np.concatenate([rgb, alpha]), transform, alpha=True)
    return out


def reproject_to(src_path, dst_path, dst_crs, resampling=Resampling.bilinear):
    """A copy of ``src_path`` in another CRS (what a differently configured run would produce)."""
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds)
        profile = src.profile.copy()
        profile.update(crs=CRS.from_user_input(dst_crs), transform=transform, width=width, height=height)
        with rasterio.open(dst_path, "w", **profile) as dst:
            for i in range(1, src.count + 1):
                reproject(rasterio.band(src, i), rasterio.band(dst, i), src_transform=src.transform,
                          src_crs=src.crs, dst_transform=transform, dst_crs=dst_crs,
                          src_nodata=src.nodata, dst_nodata=src.nodata, resampling=resampling)
    return dst_path


def request_for(tmp_path, this, other, *, params=None, output="out.geojson", orthos=True, context=None):
    """A sandbox request comparing ``this`` survey with the ``other`` (reference) one."""
    inputs = {"surface": this["dsm"], "reference_surface": other["dsm"]}
    if orthos and "orthophoto" in this and "orthophoto" in other:
        inputs["orthophoto"] = this["orthophoto"]
        inputs["reference_orthophoto"] = other["orthophoto"]
    return {
        "inputs": inputs,
        "params": params or {},
        "context": context if context is not None else {
            "task": {"name": "t-oct", "title": "Site A — 1 October"},
            "reference_task": {"name": "t-sep", "title": "Site A — 1 September"},
            "inputs": {"surface": {"dataset": "dsm", "survey": "primary"},
                       "reference_surface": {"dataset": "dsm", "survey": "reference"}},
        },
        "output_path": str(tmp_path / output),
        "result_path": str(tmp_path / "result.json"),
        "progress_path": str(tmp_path / "progress.json"),
        "work_dir": str(tmp_path),
    }


@pytest.fixture(scope="session")
def pair(tmp_path_factory):
    """The standard pair: September (before) and October (after, +0.83 m, other grid)."""
    d = tmp_path_factory.mktemp("pair")
    before = survey(d, "sep", changed=False, res=0.25, seed=1)
    after = survey(d, "oct", changed=True, res=0.20, extent=(4.0, 3.0, 196.0, 158.0),
                   offset=0.83, seed=2, gain=0.85)
    return {"before": before, "after": after, "dir": d}
