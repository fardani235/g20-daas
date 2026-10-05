"""Elevation profile along a transect, computed from a task's Potree octree.

Given a polyline in the cloud's CRS and a corridor width, this module reads
every point of the cloud that falls inside the corridor (through
:mod:`app.utils.potree_octree`, so only the octree nodes the corridor
touches are fetched) and summarises them in bins along the line:

* **ground** — with a classified cloud, the median elevation of the ASPRS
  class-2 points in the bin. Without classification (ODM's default unless
  ``pc-classify`` is on) the 5th percentile of each bin is a robust *low
  envelope* — but under a roof or a dense canopy that envelope is the object,
  not the terrain. A one-dimensional progressive morphological filter
  (Zhang et al. 2003, applied along the line) removes features narrower than
  ``ground_window`` metres whose relief exceeds what the terrain slope allows;
  those bins have no measured ground and are bridged like a gap (below).
* **top** — the highest non-noise point in the bin (classes 7 and 18 are
  noise and excluded when the cloud is classified).
* **height** — top minus ground. Under a building or a dense canopy there
  are no ground points; the ground is linearly interpolated across such a
  gap (up to ``ground_fill_max`` metres, flagged as ``ground_fill``) so the
  height can still be read, and the chart draws it dashed.
* **classes** — one layer per classification code other than 0/1
  (unclassified), 2 (ground) and 7/18 (noise): per-bin count, min, max,
  median and height above ground, so vegetation, buildings, wires, towers
  and bridge decks read as separate bands.
* **gaps** — a bin with no points stays ``null`` in every series; nothing is
  invented.

Scale: the number of points under a corridor is known from the hierarchy
*before* any point is read (``numPoints`` per node), so when it exceeds the
budget (``PROFILE_MAX_POINTS``) the deepest octree levels are dropped first,
which coarsens the sampling uniformly instead of truncating the line; the
response says so (``sampling.full_resolution``, ``sampling.spacing``). The
bin step is never finer than the sampled spacing, so a coarse sample does
not read as a row of artificial gaps.

Environment:

    PROFILE_MAX_POINTS   point budget per request (default 5,000,000)
    PROFILE_MAX_BINS     bins per request; the step is widened to respect it (default 2000)
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

import numpy as np

from app.utils.potree_octree import Node, Octree

MAX_VERTICES = 50
MIN_WIDTH, MAX_WIDTH = 0.05, 200.0
MIN_STEP = 0.01
TARGET_BINS = 500
GROUND_CLASS = 2
NOISE_CLASSES = (7, 18)
NON_LAYER_CLASSES = frozenset({0, 1, GROUND_CLASS, *NOISE_CLASSES})
LOW_PERCENTILE = 5.0
# Grades are measured over at least this horizontal distance (noise on a
# single bin pair would otherwise dominate "steepest grade").
GRADE_WINDOW_M = 2.0
SMOOTH_WINDOW = 3
DEFAULT_GROUND_FILL_MAX = 50.0
# Ascent / descent use a hysteresis of this size: a change only counts once it
# has accumulated to this much, so per-bin noise does not add up to metres of
# fictitious climbing (what GPS track tools do).
CLIMB_THRESHOLD = 0.25
# Unclassified clouds: morphological ground filter along the line.
DEFAULT_GROUND_WINDOW = 20.0  # widest object (m) that is removed from the low envelope
PMF_SLOPE = 0.3  # terrain relief allowed per metre of window growth (30 %)
PMF_DH0 = 0.3  # plus this much (m), i.e. the noise of the low envelope


class ProfileError(ValueError):
    """Bad transect / parameters (message is safe to show)."""


def max_points() -> int:
    try:
        return max(10_000, int(os.environ.get("PROFILE_MAX_POINTS") or 5_000_000))
    except ValueError:
        return 5_000_000


def max_bins() -> int:
    try:
        return max(10, int(os.environ.get("PROFILE_MAX_BINS") or 2000))
    except ValueError:
        return 2000


# ----------------------------------------------------------------- transect


@dataclass
class Transect:
    vertices: np.ndarray  # (K+1, 2) after dropping zero-length segments
    starts: np.ndarray  # (K,) distance along the line at each segment start
    lengths: np.ndarray  # (K,)
    dirs: np.ndarray  # (K, 2) unit directions
    normals: np.ndarray  # (K, 2) left normals

    @property
    def length(self) -> float:
        return float(self.starts[-1] + self.lengths[-1])

    @property
    def segments(self) -> int:
        return int(len(self.lengths))


def build_transect(line) -> Transect:
    """Validate a ``[[x, y], ...]`` polyline (cloud CRS) and precompute its segments."""
    try:
        pts = np.asarray([[float(p[0]), float(p[1])] for p in line], dtype=np.float64)
    except (TypeError, ValueError, IndexError):
        raise ProfileError("line must be a list of [x, y] pairs") from None
    if pts.ndim != 2 or len(pts) < 2:
        raise ProfileError("line needs at least two vertices")
    if len(pts) > MAX_VERTICES:
        raise ProfileError(f"line has {len(pts)} vertices; at most {MAX_VERTICES} are allowed")
    if not np.all(np.isfinite(pts)):
        raise ProfileError("line coordinates must be finite numbers")
    keep = [0]
    for i in range(1, len(pts)):
        if np.hypot(*(pts[i] - pts[keep[-1]])) > 1e-9:
            keep.append(i)
    pts = pts[keep]
    if len(pts) < 2:
        raise ProfileError("line has zero length")
    d = np.diff(pts, axis=0)
    lengths = np.hypot(d[:, 0], d[:, 1])
    dirs = d / lengths[:, None]
    normals = np.stack([-dirs[:, 1], dirs[:, 0]], axis=1)
    starts = np.concatenate([[0.0], np.cumsum(lengths)[:-1]])
    return Transect(vertices=pts, starts=starts, lengths=lengths, dirs=dirs, normals=normals)


def corridor_rectangles(t: Transect, width: float) -> list[np.ndarray]:
    """The four corners of each segment's corridor rectangle, (4, 2) each."""
    half = width / 2
    out = []
    for i in range(t.segments):
        a, b = t.vertices[i], t.vertices[i + 1]
        n = t.normals[i] * half
        out.append(np.array([a + n, b + n, b - n, a - n]))
    return out


def _project(points: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
    p = points @ axis
    return float(p.min()), float(p.max())


def box_intersects_rectangle(bmin, bmax, rect: np.ndarray, axes: np.ndarray) -> bool:
    """Separating-axis test between an axis-aligned XY box and an oriented rectangle.

    ``axes`` are the rectangle's edge directions (its own normals); the box
    contributes the X and Y axes. The Z extent of the node is irrelevant: the
    corridor is a vertical slab.
    """
    corners = np.array([[bmin[0], bmin[1]], [bmax[0], bmin[1]], [bmax[0], bmax[1]], [bmin[0], bmax[1]]])
    for axis in (np.array([1.0, 0.0]), np.array([0.0, 1.0]), axes[0], axes[1]):
        lo1, hi1 = _project(corners, axis)
        lo2, hi2 = _project(rect, axis)
        if hi1 < lo2 or hi2 < lo1:
            return False
    return True


def make_intersects(t: Transect, width: float):
    rects = corridor_rectangles(t, width)
    axes = [np.array([t.dirs[i], t.normals[i]]) for i in range(t.segments)]

    def intersects(bmin, bmax) -> bool:
        return any(box_intersects_rectangle(bmin, bmax, r, ax) for r, ax in zip(rects, axes))

    return intersects


def assign_to_segments(x: np.ndarray, y: np.ndarray, t: Transect, width: float) -> tuple[np.ndarray, np.ndarray]:
    """Distance along the line of each point and a mask of the points inside the corridor.

    A point near an interior vertex may fall in two segments' rectangles; it
    is assigned to the segment it is closest to, so it is counted once.
    """
    half = width / 2
    best_dist = np.full(len(x), np.nan)
    best_off = np.full(len(x), np.inf)
    for i in range(t.segments):
        ax, ay = t.vertices[i]
        rx, ry = x - ax, y - ay
        along = rx * t.dirs[i, 0] + ry * t.dirs[i, 1]
        across = np.abs(rx * t.normals[i, 0] + ry * t.normals[i, 1])
        inside = (along >= 0) & (along <= t.lengths[i]) & (across <= half) & (across < best_off)
        best_off[inside] = across[inside]
        best_dist[inside] = t.starts[i] + along[inside]
    mask = np.isfinite(best_dist)
    return best_dist, mask


# ------------------------------------------------------------------- budget


def choose_levels(nodes: list[Node], budget: int) -> tuple[list[Node], int, int]:
    """Keep whole levels, shallowest first, until the point budget is reached.

    Returns ``(kept, max_level_kept, deepest_level_available)``. The root
    level is always kept.
    """
    if not nodes:
        return [], 0, 0
    deepest = max(n.level for n in nodes)
    per_level = np.zeros(deepest + 1, dtype=np.int64)
    for n in nodes:
        per_level[n.level] += n.num_points
    cumulative = np.cumsum(per_level)
    fits = np.nonzero(cumulative <= budget)[0]
    max_level = int(fits[-1]) if len(fits) else 0
    kept = [n for n in nodes if n.level <= max_level]
    return kept, max_level, deepest


# ---------------------------------------------------------------- binning


def nice_step(value: float) -> float:
    """Round up to 1, 2 or 5 x 10^k (so axis ticks read well)."""
    if value <= 0:
        return MIN_STEP
    mag = 10 ** math.floor(math.log10(value))
    for m in (1, 2, 5, 10):
        if m * mag >= value - 1e-12:
            return m * mag
    return 10 * mag


def choose_step(length: float, requested: float | None, spacing: float, bins_cap: int) -> tuple[float, int]:
    """Bin width: requested (else ~TARGET_BINS over the line), never finer than the
    sampled spacing, never more than ``bins_cap`` bins. Returns ``(step, bins)``."""
    if requested is not None:
        step = float(requested)
        if not math.isfinite(step) or step < MIN_STEP:
            raise ProfileError(f"step must be at least {MIN_STEP} m")
    else:
        step = nice_step(length / TARGET_BINS)
    step = max(step, spacing, MIN_STEP)
    if length / step > bins_cap:
        step = nice_step(length / bins_cap)
    bins = max(1, int(math.ceil(length / step - 1e-9)))
    return step, bins


def group_stats(keys: np.ndarray, z: np.ndarray, groups: int, percentiles: tuple[float, ...] = ()) -> dict:
    """Per-group count / min / max / mean / percentiles with one sort.

    ``keys`` are integer group ids in ``[0, groups)``. Percentiles use the
    nearest-rank (floor) element of each sorted group, so ``p50`` of a group
    of one is that one value. Groups without points are ``NaN``.
    """
    counts = np.bincount(keys, minlength=groups).astype(np.int64)
    out = {"count": counts}
    nan = np.full(groups, np.nan)
    has = counts > 0
    if not len(z):
        out.update({"min": nan.copy(), "max": nan.copy(), "mean": nan.copy()})
        for q in percentiles:
            out[f"p{int(q):02d}"] = nan.copy()
        return out
    order = np.lexsort((z, keys))
    zs = z[order]
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    mn, mx = nan.copy(), nan.copy()
    mn[has] = zs[starts[has]]
    mx[has] = zs[starts[has] + counts[has] - 1]
    mean = nan.copy()
    mean[has] = np.bincount(keys, weights=z, minlength=groups)[has] / counts[has]
    out.update({"min": mn, "max": mx, "mean": mean})
    for q in percentiles:
        arr = nan.copy()
        idx = starts[has] + np.floor(q / 100.0 * (counts[has] - 1)).astype(np.int64)
        arr[has] = zs[idx]
        out[f"p{int(q):02d}"] = arr
    return out


def interpolate_gaps(values: np.ndarray, step: float, max_gap: float) -> np.ndarray:
    """Linear fill of interior NaN runs no longer than ``max_gap`` metres.

    Returns an array holding only the filled values (NaN elsewhere), so the
    caller can keep measured and interpolated samples apart.
    """
    filled = np.full_like(values, np.nan)
    valid = np.isfinite(values)
    if valid.sum() < 2:
        return filled
    idx = np.arange(len(values))
    i = 0
    n = len(values)
    while i < n:
        if valid[i]:
            i += 1
            continue
        j = i
        while j < n and not valid[j]:
            j += 1
        if i > 0 and j < n and (j - i) * step <= max_gap + 1e-9:
            filled[i:j] = np.interp(idx[i:j], [i - 1, j], [values[i - 1], values[j]])
        i = j
    return filled


def filter_ground(low: np.ndarray, step: float, max_window: float, *, slope: float = PMF_SLOPE,
                  dh0: float = PMF_DH0) -> np.ndarray:
    """Progressive morphological filter (1-D) over a low-envelope series.

    Opens the series (minimum then maximum filter) with windows growing
    3, 5, 9, 17… bins up to ``max_window`` metres. Where an opening lowers
    the surface by more than the relief a terrain slope of ``slope`` could
    produce over the window growth (plus ``dh0``), the bin is an object —
    a roof, a canopy — and is flagged. Returns a boolean mask of such bins;
    terrain wider than ``max_window`` or steeper than ``slope`` is kept as
    ground, which is the usual trade-off of this family of filters.
    """
    from scipy.ndimage import maximum_filter1d, minimum_filter1d

    n = len(low)
    flagged = np.zeros(n, dtype=bool)
    valid = np.isfinite(low)
    if max_window <= 0 or valid.sum() < 3 or n < 3:
        return flagged
    idx = np.arange(n)
    surface = np.interp(idx, idx[valid], low[valid])  # the filter needs a dense series
    w_prev, w = 1, 3
    while True:
        opened = maximum_filter1d(minimum_filter1d(surface, w), w)
        dh = slope * (w - w_prev) * step + dh0
        hit = (surface - opened) > dh
        flagged |= hit
        surface = np.where(hit, opened, surface)
        if w * step >= max_window:
            break
        w_prev, w = w, 2 * w - 1
    flagged &= valid
    # Object fringes: the threshold grows with the window, so the partly raised
    # bins at the edge of a wide object (a canopy edge, an eave) survive the
    # opening. Grow each flagged run outwards while the step down to the next
    # bin is steeper than terrain can be.
    thr = slope * step + dh0
    for _ in range(n):
        inner = ~flagged[1:-1]
        rising_left = inner & flagged[2:] & ~flagged[:-2] & ((low[1:-1] - low[:-2]) > thr)
        rising_right = inner & flagged[:-2] & ~flagged[2:] & ((low[1:-1] - low[2:]) > thr)
        grow = np.zeros(n, dtype=bool)
        grow[1:-1] = (rising_left | rising_right)
        grow &= valid
        if not grow.any():
            break
        flagged |= grow
    return flagged


def smooth_median(values: np.ndarray, window: int = SMOOTH_WINDOW) -> np.ndarray:
    """NaN-aware running median; a NaN centre stays NaN."""
    if window <= 1 or len(values) < window:
        return values.copy()
    half = window // 2
    out = values.copy()
    for i in range(len(values)):
        if not np.isfinite(values[i]):
            continue
        win = values[max(0, i - half):i + half + 1]
        win = win[np.isfinite(win)]
        out[i] = float(np.median(win))
    return out


def climb_totals(g: np.ndarray, threshold: float = CLIMB_THRESHOLD) -> tuple[float, float]:
    """Ascent and descent with hysteresis; a gap resets the anchor (its jump is not counted)."""
    ascent = descent = 0.0
    anchor = None
    for v in g:
        if not np.isfinite(v):
            anchor = None
            continue
        if anchor is None:
            anchor = v
            continue
        if v - anchor >= threshold:
            ascent += v - anchor
            anchor = v
        elif anchor - v >= threshold:
            descent += anchor - v
            anchor = v
    return float(ascent), float(descent)


def climb_stats(ground: np.ndarray, step: float) -> dict:
    """Ascent, descent, steepest grade and ground-following length from a ground series."""
    g = smooth_median(ground)
    valid = np.isfinite(g)
    out = {"ascent": None, "descent": None, "max_grade_pct": None, "max_grade_at": None,
           "mean_grade_pct": None, "length_3d": None}
    if valid.sum() < 2:
        return out
    out["ascent"], out["descent"] = climb_totals(g)
    # Ground-following length: consecutive valid bins contribute their slope distance, gaps the plain step.
    both = valid[1:] & valid[:-1]
    dz = (g[1:] - g[:-1])[both]
    out["length_3d"] = float(np.hypot(step, dz).sum() + step * (~both).sum())
    # Steepest grade over a window of GRADE_WINDOW_M that contains no gap.
    w = max(1, int(round(GRADE_WINDOW_M / step)))
    if len(g) > w:
        gaps = np.concatenate([[0], np.cumsum(~valid)])
        clean = (gaps[w + 1:] - gaps[:-w - 1]) == 0  # window i..i+w fully valid
        if clean.any():
            grades = (g[w:] - g[:-w])[clean] / (w * step) * 100.0
            k = int(np.argmax(np.abs(grades)))
            at = (np.nonzero(clean)[0][k] + w / 2) * step
            out["max_grade_pct"] = float(grades[k])
            out["max_grade_at"] = float(at)
    first, last = np.nonzero(valid)[0][[0, -1]]
    if last > first:
        out["mean_grade_pct"] = float((g[last] - g[first]) / ((last - first) * step) * 100.0)
    return out


def gap_ranges(count: np.ndarray, step: float) -> list[list[float]]:
    """``[start, end]`` distances of every run of empty bins."""
    out = []
    empty = count == 0
    i = 0
    while i < len(empty):
        if not empty[i]:
            i += 1
            continue
        j = i
        while j < len(empty) and empty[j]:
            j += 1
        out.append([round(i * step, 3), round(j * step, 3)])
        i = j
    return out


def _series(arr: np.ndarray, digits: int = 3) -> list:
    """JSON-safe list: rounded floats, ``None`` for NaN."""
    out = []
    for v in arr.tolist():
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            out.append(None)
        elif isinstance(v, float):
            out.append(round(v, digits))
        else:
            out.append(v)
    return out


def _argmax_at(values: np.ndarray, distance: np.ndarray):
    if not np.isfinite(values).any():
        return None, None
    i = int(np.nanargmax(values))
    return round(float(values[i]), 3), round(float(distance[i]), 3)


def _argmin_at(values: np.ndarray, distance: np.ndarray):
    if not np.isfinite(values).any():
        return None, None
    i = int(np.nanargmin(values))
    return round(float(values[i]), 3), round(float(distance[i]), 3)


# ---------------------------------------------------------------- profile


def compute_profile(octree: Octree, line, *, width: float = 1.0, step: float | None = None,
                    point_budget: int | None = None, bins_cap: int | None = None,
                    ground_fill_max: float = DEFAULT_GROUND_FILL_MAX,
                    ground_window: float = DEFAULT_GROUND_WINDOW) -> dict:
    """The elevation profile of ``line`` (``[[x, y], ...]`` in the cloud's CRS).

    ``width`` is the full corridor width in CRS units; ``step`` the bin size
    (auto when omitted); ``ground_window`` the morphological filter window for
    unclassified clouds (0 disables it). See the module docstring.
    """
    started = time.monotonic()
    width = float(width)
    if not math.isfinite(width) or not (MIN_WIDTH <= width <= MAX_WIDTH):
        raise ProfileError(f"width must be between {MIN_WIDTH} and {MAX_WIDTH} m")
    budget = point_budget or max_points()
    bins_cap = bins_cap or max_bins()
    t = build_transect(line)

    # 1. Nodes under the corridor (every level), then whole-level trimming to the budget.
    candidates = octree.select_nodes(make_intersects(t, width))
    nodes, max_level, deepest = choose_levels(candidates, budget)
    points_available = int(sum(n.num_points for n in candidates))
    full_resolution = max_level >= deepest
    spacing = octree.level_spacing(max_level) if nodes else octree.spacing

    # 2. Read the points of those nodes and keep the ones inside the corridor.
    records = octree.read_nodes(nodes)
    x, y, z = octree.positions(records)
    distance, inside = assign_to_segments(x, y, t, width)
    dist = distance[inside]
    z = z[inside]
    cls = records["classification"][inside].astype(np.int64).ravel() if octree.has_attribute("classification") else None
    del records, x, y

    # 3. Binning.
    step_used, nbins = choose_step(t.length, step, spacing, bins_cap)
    centres = (np.arange(nbins) + 0.5) * step_used
    bin_of = np.minimum((dist / step_used).astype(np.int64), nbins - 1)

    # Classification is "available" when more than the unclassified codes occur.
    classified = cls is not None and bool(np.any(~np.isin(cls, (0, 1))))
    has_ground_class = classified and bool(np.any(cls == GROUND_CLASS))
    if classified:
        keep = ~np.isin(cls, NOISE_CLASSES)
    else:
        keep = np.ones(len(z), dtype=bool)

    all_stats = group_stats(bin_of[keep], z[keep], nbins, percentiles=(LOW_PERCENTILE,))
    count = all_stats["count"]
    top = all_stats["max"]

    if has_ground_class:
        g_mask = cls == GROUND_CLASS
        g_stats = group_stats(bin_of[g_mask], z[g_mask], nbins, percentiles=(50,))
        ground = g_stats["p50"]
        ground_method = "class2_median"
    else:
        low = all_stats[f"p{int(LOW_PERCENTILE):02d}"]
        objects = filter_ground(low, step_used, ground_window)
        ground = np.where(objects, np.nan, low)
        ground_method = "low_percentile_filtered" if ground_window > 0 else "low_percentile"
    ground_fill = interpolate_gaps(ground, step_used, ground_fill_max)
    ground_any = np.where(np.isfinite(ground), ground, ground_fill)
    height = top - ground_any
    height = np.where(np.isfinite(height), np.maximum(height, 0.0), np.nan)

    # 4. Class layers.
    layers = []
    if classified:
        codes = sorted(int(c) for c in np.unique(cls) if int(c) not in NON_LAYER_CLASSES)
        for code in codes:
            m = cls == code
            s = group_stats(bin_of[m], z[m], nbins, percentiles=(50,))
            h = s["max"] - ground_any
            h = np.where(np.isfinite(h), np.maximum(h, 0.0), np.nan)
            hmax, hmax_at = _argmax_at(h, centres)
            layers.append({
                "code": code,
                "points": int(m.sum()),
                "bins": int((s["count"] > 0).sum()),
                "count": _series(s["count"]),
                "min": _series(s["min"]),
                "max": _series(s["max"]),
                "median": _series(s["p50"]),
                "height": _series(h),
                "max_height": hmax,
                "max_height_at": hmax_at,
            })

    # 5. Summary numbers.
    gmin, gmin_at = _argmin_at(ground_any, centres)
    gmax, gmax_at = _argmax_at(ground_any, centres)
    tmax, tmax_at = _argmax_at(top, centres)
    hmax, hmax_at = _argmax_at(height, centres)
    valid_g = np.nonzero(np.isfinite(ground_any))[0]
    with_data = int((count > 0).sum())
    stats = {
        "length": round(t.length, 3),
        "segments": t.segments,
        "bins": nbins,
        "bins_with_data": with_data,
        "coverage": round(with_data / nbins, 4) if nbins else 0.0,
        "points": int(len(z)),
        "ground_start": round(float(ground_any[valid_g[0]]), 3) if len(valid_g) else None,
        "ground_end": round(float(ground_any[valid_g[-1]]), 3) if len(valid_g) else None,
        "ground_min": gmin, "ground_min_at": gmin_at,
        "ground_max": gmax, "ground_max_at": gmax_at,
        "top_max": tmax, "top_max_at": tmax_at,
        "height_max": hmax, "height_max_at": hmax_at,
        "height_mean": round(float(np.nanmean(height)), 3) if np.isfinite(height).any() else None,
        **climb_stats(ground_any, step_used),
    }
    for k in ("ascent", "descent", "max_grade_pct", "max_grade_at", "mean_grade_pct", "length_3d"):
        if stats[k] is not None:
            stats[k] = round(stats[k], 3)

    vertices = [{"distance": round(float(d), 3), "x": round(float(v[0]), 3), "y": round(float(v[1]), 3)}
                for d, v in zip(np.concatenate([t.starts, [t.length]]), t.vertices)]
    # Bin centres in the CRS, for the 3D marker.
    seg_index = np.minimum(np.searchsorted(t.starts, centres, side="right") - 1, t.segments - 1)
    along = centres - t.starts[seg_index]
    cx = t.vertices[seg_index, 0] + t.dirs[seg_index, 0] * along
    cy = t.vertices[seg_index, 1] + t.dirs[seg_index, 1] * along

    return {
        "crs": octree.projection,
        "line": [[float(v[0]), float(v[1])] for v in t.vertices],
        "vertices": vertices,
        "width": width,
        "step": step_used,
        "length": round(t.length, 3),
        "bins": nbins,
        "distance": _series(centres),
        "x": _series(cx),
        "y": _series(cy),
        "count": _series(count),
        "min": _series(all_stats["min"]),
        "max": _series(all_stats["max"]),
        "ground": _series(ground),
        "ground_fill": _series(ground_fill),
        "top": _series(top),
        "height": _series(height),
        "classes": layers,
        "gaps": gap_ranges(count, step_used),
        "classification": {
            "available": classified,
            "ground_method": ground_method,
            "noise_excluded": list(NOISE_CLASSES) if classified else [],
            "ground_fill_max": ground_fill_max,
            "ground_window": None if has_ground_class else ground_window,
            "climb_threshold": CLIMB_THRESHOLD,
        },
        "sampling": {
            "points_read": int(sum(n.num_points for n in nodes)),
            "points_available": points_available,
            "points_in_corridor": int(len(z)),
            "nodes_read": len(nodes),
            "nodes_available": len(candidates),
            "bytes_read": int(octree.bytes_read),
            "requests": int(octree.requests),
            "full_resolution": bool(full_resolution),
            "max_level": int(max_level),
            "deepest_level": int(deepest),
            "spacing": round(float(spacing), 4),
            "step_requested": step,
            "point_budget": int(budget),
        },
        "stats": stats,
        "duration_s": round(time.monotonic() - started, 3),
    }
