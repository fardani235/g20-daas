"""Co-registration of the two surveys and the measurement of their disagreement.

Two independently processed surveys disagree even where nothing changed:

* **vertically by a constant** — without ground control the absolute height of
  a photogrammetric model is only as good as the drone's GPS/barometer, so one
  survey can sit decimetres or metres above the other;
* **vertically by a tilt or dome** — residual camera-calibration error;
* **horizontally by a small shift** — the same GPS error sideways;
* **by noise** — matching error, a few centimetres on good ground.

Everything here is estimated *from the data*, on the ground that did not
change, and reported with its own uncertainty:

``fit_stable_peak``
    The height difference of unchanged ground piles up at one value (the
    offset), while real change spreads over a wide range. The offset is the
    **mode** of the difference distribution — not its mean or median, which a
    large excavation would drag along — and the width of that peak is the
    noise. Also returned: how much of the overlap sits in the peak and whether
    a second, competing peak exists.

``analyse_tiles``
    The same fit on a 6 x 6 tiling of the site. If the offset is truly
    constant every tile agrees. The largest group of tiles that agree with
    one flat or tilted surface is taken as the unchanged ground; its spread
    is the systematic uncertainty that a volume carries, its plane exposes a
    tilt, and the tiles outside it are where the site changed wholesale.

``block_noise``
    How strongly neighbouring cells' errors are correlated, measured by
    comparing the noise of single cells with the noise of 8 x 8 block means.
    Volume uncertainty must not pretend every cell is an independent sample.

``estimate_horizontal_shift``
    Slides one surface over the other and keeps the offset at which they
    agree best (smallest robust mean difference). Only reported as
    significant when the agreement improves and two halves of the data
    independently find the same position, so flat, featureless ground never
    produces a spurious correction.
"""

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

# Scale factor that makes the median absolute deviation estimate a standard deviation.
MAD_TO_SIGMA = 1.4826

MAX_FIT_SAMPLES = 400_000
MIN_FIT_SAMPLES = 200

# Horizontal search: at most this many cells each way; the surfaces are
# block-averaged first when the search range is longer than that.
SHIFT_MAX_RADIUS = 8
SHIFT_SAMPLES = 150_000
# A shift is believed when the disagreement at the best position is this much
# smaller than at zero shift *and* two independent halves of the samples find
# the same position (on featureless ground each half finds its own noise).
SHIFT_MIN_GAIN = 1.05
# Differences larger than this many noise sigmas all cost the same.
SHIFT_COST_CAP = 10.0

TILES_PER_AXIS = 6
TILE_MIN_CELLS = 24
TILE_MIN_SAMPLES = 500
PLANE_MIN_TILES = 6
SIGMA_SYS_MIN_TILES = 4
# Tiles agree with a surface when within 3 x the tile noise of it, and never
# tighter than this.
TILE_AGREEMENT_MIN_M = 0.03
# The no-tilt model is kept unless it explains fewer than this share of the
# tiles the best tilted plane explains.
PLANE_PREFERENCE = 0.8

NOISE_BLOCK = 8


def nmad(values: np.ndarray, center: float | None = None) -> float:
    """Normalised median absolute deviation: a standard deviation that ignores outliers."""
    if values.size == 0:
        return float("nan")
    c = float(np.median(values)) if center is None else center
    return float(MAD_TO_SIGMA * np.median(np.abs(values - c)))


def subsample(values: np.ndarray, limit: int) -> np.ndarray:
    """Every n-th value so at most ``limit`` remain (deterministic, spatially even)."""
    if values.size <= limit:
        return values
    return values[::int(math.ceil(values.size / limit))]


# --------------------------------------------------------------------------
# Vertical offset and noise
# --------------------------------------------------------------------------

@dataclass
class VerticalFit:
    offset: float            # height difference of unchanged ground (after − before)
    sigma: float             # noise of the difference on unchanged ground (1 sigma)
    stable_fraction: float   # share of the samples within 3 sigma of the offset
    ambiguity: float         # size of the second-largest peak relative to the first (0..1+)
    second_offset: float | None
    samples: int
    spread: float            # NMAD of all samples (change included), for context

    def as_dict(self) -> dict:
        return {
            "offset_m": round(self.offset, 4),
            "noise_sigma_m": round(self.sigma, 4),
            "stable_fraction": round(self.stable_fraction, 4),
            "second_peak_ratio": round(self.ambiguity, 3),
            "second_peak_offset_m": None if self.second_offset is None else round(self.second_offset, 4),
            "samples": self.samples,
        }


def half_sample_mode(sorted_values: np.ndarray) -> float:
    """Mode of a sample by repeatedly keeping the densest half (Bickel & Frühwirth 2006).

    Parameter-free, and unlike the median it stays on the tallest peak when
    less than half of the data belongs to it.
    """
    x = sorted_values
    while x.size > 3:
        w = (x.size + 1) // 2
        widths = x[w - 1:] - x[:x.size - w + 1]
        i = int(np.argmin(widths))
        x = x[i:i + w]
    if x.size == 3:
        x = x[:2] if (x[1] - x[0]) <= (x[2] - x[1]) else x[1:]
    return float(np.mean(x))


def fit_stable_peak(values: np.ndarray) -> VerticalFit:
    """Locate the peak of unchanged ground in a sample of height differences."""
    s = np.sort(subsample(values[np.isfinite(values)].astype("float64", copy=False), MAX_FIT_SAMPLES))
    n = s.size
    if n < MIN_FIT_SAMPLES:
        raise ValueError(f"too few samples ({n}) to estimate the height offset")
    spread = nmad(s)
    mode = half_sample_mode(s)

    # Bin width from the shortest interval holding 10 % of the samples: when
    # at least that much ground is unchanged the interval lies inside the peak,
    # so the bins resolve it whatever the units or the size of the offset.
    w10 = max(2, n // 10)
    shortest = float(np.min(s[w10:] - s[:-w10]))
    h = max(shortest / 8.0, 1e-4)
    half_bins = 320
    edges = mode + h * (np.arange(-half_bins, half_bins + 2) - 0.5)
    hist = np.histogram(s, bins=edges)[0].astype("float64")
    smooth = ndimage.gaussian_filter1d(hist, 1.5, mode="constant")
    p = int(np.argmax(smooth))
    centre = float(mode + h * (p - half_bins))
    half_max = smooth[p] / 2.0
    # The narrower side of the peak: real change next to the offset (a thin
    # layer of fill, say) widens one flank only.
    hw = min(_half_width(smooth, p, half_max, -1), _half_width(smooth, p, half_max, +1))
    sigma0 = max(hw * h / 1.1774, h / 2.0)

    offset, sigma = centre, sigma0
    for _ in range(3):
        lo, hi = np.searchsorted(s, [offset - 3 * sigma, offset + 3 * sigma])
        sel = s[lo:hi]
        if sel.size < 10:
            break
        offset = float(np.median(sel))
        sigma = float(np.clip(nmad(sel, offset), h / 2.0, 2.0 * sigma0))

    half = max(3.0 * sigma, 3.0 * h)
    n1 = _count(s, offset - half, offset + half)
    stable_fraction = n1 / n

    # A second peak: the densest window of the same width that does not overlap the first.
    lo_v, hi_v = float(s[int(0.005 * n)]), float(s[min(n - 1, int(0.995 * n))])
    step = max(half / 3.0, (hi_v - lo_v) / 20000.0)
    centres = np.arange(lo_v, hi_v + step, step)
    far = np.abs(centres - offset) > 2.0 * half
    ambiguity, second = 0.0, None
    if far.any():
        c = centres[far]
        counts = np.searchsorted(s, c + half, "right") - np.searchsorted(s, c - half, "left")
        j = int(np.argmax(counts))
        ambiguity = float(counts[j]) / max(n1, 1)
        second = float(c[j])
    return VerticalFit(offset=offset, sigma=sigma, stable_fraction=stable_fraction,
                       ambiguity=ambiguity, second_offset=second, samples=n, spread=spread)


def _half_width(smooth: np.ndarray, p: int, level: float, direction: int) -> float:
    """Distance in bins from the peak to where the histogram falls to ``level``."""
    i = p
    while 0 <= i + direction < smooth.size and smooth[i + direction] > level:
        i += direction
    j = i + direction
    if not (0 <= j < smooth.size):
        return float(abs(i - p)) + 0.5
    a, b = smooth[i], smooth[j]
    frac = (a - level) / (a - b) if a > b else 0.5
    return float(abs(i - p)) + float(frac)


def _count(sorted_values, lo, hi) -> int:
    return int(np.searchsorted(sorted_values, hi, "right") - np.searchsorted(sorted_values, lo, "left"))


# --------------------------------------------------------------------------
# Spatial consistency of the offset: tiles, tilt, systematic uncertainty
# --------------------------------------------------------------------------

@dataclass
class TileAnalysis:
    offsets: np.ndarray        # (ny, nx) per-tile offset minus the global one; NaN = no estimate
    used: np.ndarray           # tiles that agree with one flat or tilted surface: the unchanged ground
    model: str                 # "constant" or "plane": what those tiles agree with
    sigma_sys: float           # spread of the used tiles about the global offset (tilt included)
    sigma_sys_measured: bool   # False when too few tiles; sigma_sys then falls back to the noise
    noise: float               # typical noise inside one tile (not smeared by a tilt)
    plane: tuple[float, float, float] | None = None  # a + b*u + c*v, u/v in [-0.5, 0.5] across the grid
    tilt_m: float = 0.0        # height range of that plane across the site
    plane_residual_sigma: float = 0.0  # spread of the used tiles about the plane
    notes: list = field(default_factory=list)

    @property
    def valid_tiles(self) -> int:
        return int(np.isfinite(self.offsets).sum())

    @property
    def set_aside_fraction(self) -> float:
        """Share of the tiles that sit at a different level than the unchanged ground."""
        valid = self.valid_tiles
        return float(valid - int(self.used.sum())) / valid if valid else 0.0

    def as_dict(self) -> dict:
        return {
            "tiles": [int(self.offsets.shape[0]), int(self.offsets.shape[1])],
            "tiles_with_data": self.valid_tiles,
            "tiles_used": int(self.used.sum()),
            "tiles_set_aside_share": round(self.set_aside_fraction, 3),
            "tile_offsets_m": [[None if not np.isfinite(v) else round(float(v), 3) for v in row]
                               for row in self.offsets],
            "model": self.model,
            "systematic_sigma_m": round(self.sigma_sys, 4),
            "systematic_sigma_measured": self.sigma_sys_measured,
            "tile_noise_sigma_m": round(self.noise, 4),
            "tilt_across_site_m": round(self.tilt_m, 4),
            "plane": None if self.plane is None else [round(v, 4) for v in self.plane],
        }


def _tile_edges(size: int, per_axis: int) -> np.ndarray:
    n = int(np.clip(size // TILE_MIN_CELLS, 1, per_axis))
    return np.linspace(0, size, n + 1).astype(int)


def _consensus_plane(u, v, z, tol):
    """The plane through three tiles that the most tiles agree with (within ``tol``).

    Exhaustive over all triples — a few thousand for a 6 x 6 tiling — so the
    result is deterministic and survives up to about half of the tiles
    sitting somewhere else (a large excavation, a raised platform).
    """
    n = z.size
    idx = np.array(list(itertools.combinations(range(n), 3)))
    a, b, c = idx[:, 0], idx[:, 1], idx[:, 2]
    du1, dv1, dz1 = u[b] - u[a], v[b] - v[a], z[b] - z[a]
    du2, dv2, dz2 = u[c] - u[a], v[c] - v[a], z[c] - z[a]
    det = du1 * dv2 - du2 * dv1
    ok = np.abs(det) > 1e-6                       # skip collinear triples
    det = np.where(ok, det, 1.0)
    pu = (dz1 * dv2 - dz2 * dv1) / det
    pv = (du1 * dz2 - du2 * dz1) / det
    p0 = z[a] - pu * u[a] - pv * v[a]
    res = np.abs(p0[:, None] + pu[:, None] * u[None, :] + pv[:, None] * v[None, :] - z[None, :])
    counts = np.where(ok, (res <= tol).sum(axis=1), 0)
    best = int(np.argmax(counts))
    return (float(p0[best]), float(pu[best]), float(pv[best])), res[best] <= tol


def analyse_tiles(residual: np.ndarray, sigma: float, per_axis: int = TILES_PER_AXIS) -> TileAnalysis:
    """Fit the stable peak tile by tile on ``residual`` (difference minus the global offset).

    The *unchanged ground* is the largest group of tiles that agree with one
    surface: the global offset itself (``constant``) or, when clearly more
    tiles agree with it, a tilted plane (``plane``). Tiles outside the group
    are dominated by real change (or by warping no plane explains) and are
    set aside. The spread of the group about the global offset is the
    systematic uncertainty; with a tilt present that spread is large on
    purpose — until the tilt is removed it *is* how wrong a height can be.
    """
    h, w = residual.shape
    ys, xs = _tile_edges(h, per_axis), _tile_edges(w, per_axis)
    ny, nx = len(ys) - 1, len(xs) - 1
    offsets = np.full((ny, nx), np.nan)
    sigmas = np.full((ny, nx), np.nan)
    for iy in range(ny):
        for ix in range(nx):
            block = residual[ys[iy]:ys[iy + 1], xs[ix]:xs[ix + 1]]
            vals = block[np.isfinite(block)]
            if vals.size < TILE_MIN_SAMPLES:
                continue
            try:
                fit = fit_stable_peak(subsample(vals, 20_000))
            except ValueError:
                continue
            offsets[iy, ix], sigmas[iy, ix] = fit.offset, fit.sigma

    ok = np.isfinite(offsets)
    noise = float(np.median(sigmas[ok])) if ok.any() else float(sigma)
    tol = max(3.0 * noise, TILE_AGREEMENT_MIN_M)
    cu = ((xs[:-1] + xs[1:]) / 2.0) / w - 0.5
    cv = ((ys[:-1] + ys[1:]) / 2.0) / h - 0.5
    uu, vv = np.meshgrid(cu, cv)

    used = ok & (np.abs(offsets) <= tol)          # the constant model: tiles at the global offset
    model, plane, tilt, plane_sigma = "constant", None, 0.0, 0.0
    if ok.sum() >= PLANE_MIN_TILES:
        coef, inliers = _consensus_plane(uu[ok], vv[ok], offsets[ok], tol)
        # A tilt is only believed when clearly more tiles agree with it than with no tilt.
        if inliers.sum() >= PLANE_MIN_TILES and used.sum() < PLANE_PREFERENCE * inliers.sum():
            a = np.column_stack([np.ones(inliers.sum()), uu[ok][inliers], vv[ok][inliers]])
            coef, *_ = np.linalg.lstsq(a, offsets[ok][inliers], rcond=None)
            fitted = coef[0] + coef[1] * uu + coef[2] * vv
            used = ok & (np.abs(offsets - fitted) <= tol)
            model, plane = "plane", (float(coef[0]), float(coef[1]), float(coef[2]))
            tilt = abs(plane[1]) + abs(plane[2])
            plane_sigma = nmad((offsets - fitted)[used], 0.0)

    out = TileAnalysis(offsets=offsets, used=used, model=model, sigma_sys=float(sigma), sigma_sys_measured=False,
                       noise=noise, plane=plane, tilt_m=tilt, plane_residual_sigma=plane_sigma)
    if used.sum() >= SIGMA_SYS_MIN_TILES:
        # About zero, not about their own median: a leftover constant or tilt
        # is part of what the volumes are uncertain by.
        out.sigma_sys = nmad(offsets[used], 0.0)
        out.sigma_sys_measured = True
    else:
        out.notes.append("too few tiles of unchanged ground to measure how constant the offset is")
    return out


def plane_surface(plane: tuple[float, float, float], shape: tuple[int, int]) -> np.ndarray:
    """The fitted plane evaluated at every cell of a grid of ``shape`` (float32)."""
    h, w = shape
    u = ((np.arange(w, dtype="float32") + 0.5) / w - 0.5)[None, :]
    v = ((np.arange(h, dtype="float32") + 0.5) / h - 0.5)[:, None]
    return (plane[0] + plane[1] * u + plane[2] * v).astype("float32")


# --------------------------------------------------------------------------
# Spatial correlation of the noise
# --------------------------------------------------------------------------

def block_noise(residual: np.ndarray, sigma: float, block: int = NOISE_BLOCK) -> dict:
    """How many cells make one independent sample of the noise.

    Compares the spread of single stable cells (``sigma``) with the spread of
    ``block`` x ``block`` means over stable ground. Independent cells would
    give ``sigma / block``; correlated errors give more. ``cells_per_sample``
    is the factor by which the variance of a sum exceeds the independent case
    (1 = independent, ``block**2`` = fully correlated within a block).
    """
    fallback = {"cells_per_sample": float(block * block) / 2.0, "block_sigma_m": None, "blocks": 0,
                "measured": False}
    h, w = residual.shape
    hb, wb = h // block, w // block
    if hb < 4 or wb < 4 or sigma <= 0:
        return fallback
    r = residual[:hb * block, :wb * block]
    stable = np.isfinite(r) & (np.abs(r) <= 3.0 * sigma)
    counts = stable.reshape(hb, block, wb, block).sum(axis=(1, 3))
    sums = np.where(stable, r, 0.0).reshape(hb, block, wb, block).sum(axis=(1, 3), dtype="float64")
    full = counts >= int(0.9 * block * block)
    if full.sum() < 30:
        return fallback
    means = sums[full] / counts[full]
    block_sigma = nmad(means)
    cells = float(np.clip(block * block * (block_sigma / sigma) ** 2, 1.0, block * block))
    return {"cells_per_sample": cells, "block_sigma_m": float(block_sigma), "blocks": int(full.sum()),
            "measured": True}


# --------------------------------------------------------------------------
# Horizontal shift
# --------------------------------------------------------------------------

@dataclass
class HorizontalShift:
    estimated: bool = False
    significant: bool = False
    dx_cells: float = 0.0     # in working-grid cells: fixed(row, col) ≈ moving(row + dy, col + dx)
    dy_cells: float = 0.0
    east_m: float = 0.0       # where the moving survey sits relative to the fixed one
    north_m: float = 0.0
    gain: float = 1.0         # spread at zero shift / spread at the best shift
    search_radius_m: float = 0.0
    step_m: float = 0.0
    at_search_limit: bool = False
    note: str = ""

    @property
    def magnitude_m(self) -> float:
        return math.hypot(self.east_m, self.north_m)

    def as_dict(self) -> dict:
        return {
            "estimated": self.estimated,
            "significant": self.significant,
            "east_m": round(self.east_m, 3),
            "north_m": round(self.north_m, 3),
            "magnitude_m": round(self.magnitude_m, 3),
            "agreement_gain": round(self.gain, 3),
            "search_radius_m": round(self.search_radius_m, 3),
            "search_step_m": round(self.step_m, 3),
            "at_search_limit": self.at_search_limit,
            "note": self.note,
        }


def block_mean(a: np.ndarray, f: int) -> np.ndarray:
    """Mean over ``f`` x ``f`` blocks; a block with any missing cell is missing."""
    if f <= 1:
        return a
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    return a[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))


def estimate_horizontal_shift(fixed: np.ndarray, moving: np.ndarray, res_m: float,
                              max_shift_m: float) -> HorizontalShift:
    """Find how far ``moving`` is displaced from ``fixed`` (both on the same grid).

    Exhaustive search over whole-cell shifts within ``max_shift_m`` for the
    one that minimises the mean truncated absolute difference ``fixed −
    moving`` about its median (insensitive to the vertical offset, a tilt and
    the part of the site that really changed), refined to a fraction of a
    cell with a parabola through the neighbouring costs.
    """
    out = HorizontalShift(search_radius_m=float(max_shift_m))
    if max_shift_m <= 0:
        out.note = "search disabled"
        return out
    f = max(1, int(math.ceil(max_shift_m / (SHIFT_MAX_RADIUS * res_m))))
    fx, mv = block_mean(fixed, f), block_mean(moving, f)
    cell = res_m * f
    radius = int(min(SHIFT_MAX_RADIUS, max(1, math.ceil(max_shift_m / cell))))
    out.step_m = cell
    out.search_radius_m = radius * cell
    h, w = fx.shape
    if min(h, w) < 2 * radius + 16:
        out.note = "overlap too small to search for a shift"
        return out
    rr, cc = np.nonzero(np.isfinite(fx[radius:h - radius, radius:w - radius]))
    if rr.size < 2000:
        out.note = "too little overlapping data to search for a shift"
        return out
    step = int(math.ceil(rr.size / SHIFT_SAMPLES))
    rr, cc = rr[::step] + radius, cc[::step] + radius
    fv = fx[rr, cc]

    # A tilt between the surveys looks, on sloping ground, like a shift. Take
    # the planar trend of the difference out first (fitted once, robustly):
    # what a real shift leaves behind — misplaced edges, undulations — is not planar.
    d0 = fv - mv[rr, cc]
    good = np.isfinite(d0)
    if good.sum() < 1000:
        out.note = "too little overlapping data to search for a shift"
        return out
    design = np.column_stack([np.ones(rr.size), rr / h - 0.5, cc / w - 0.5])
    keep = good.copy()
    for _ in range(2):
        coef, *_ = np.linalg.lstsq(design[keep], d0[keep], rcond=None)
        res = d0 - design @ coef
        keep = good & (np.abs(res - np.median(res[keep])) <= 3.0 * max(nmad(res[keep]), 1e-6))
    fv = fv - design @ coef
    scale = max(nmad((fv - mv[rr, cc])[good]), 1e-4)
    cap = SHIFT_COST_CAP * scale

    size = 2 * radius + 1
    cost = np.full((size, size), np.nan)
    # The same cost on two interleaved halves of the samples, to check below
    # that both halves point to the same shift.
    halves = np.full((2, size, size), np.nan)
    parity = (np.arange(fv.size) % 2).astype(bool)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            d = fv - mv[rr + dy, cc + dx]
            ok = np.isfinite(d)
            if ok.sum() >= 0.5 * fv.size:
                # Truncated absolute deviation: every misplaced edge counts
                # (a median-based spread would not notice them), while ground
                # that really changed saturates at the cap for every shift.
                dev = np.minimum(np.abs(d - np.median(d[ok])), cap)
                cost[dy + radius, dx + radius] = float(dev[ok].mean())
                halves[0, dy + radius, dx + radius] = float(dev[ok & parity].mean())
                halves[1, dy + radius, dx + radius] = float(dev[ok & ~parity].mean())
    zero = cost[radius, radius]
    if not np.isfinite(zero):
        out.note = "too little overlapping data to search for a shift"
        return out

    iy, ix = np.unravel_index(int(np.nanargmin(cost)), cost.shape)
    best = float(cost[iy, ix])
    agree = all(
        max(abs(int(hy) - int(iy)), abs(int(hx) - int(ix))) <= 1
        for hy, hx in (np.unravel_index(int(np.nanargmin(half)), half.shape) for half in halves)
    )
    out.estimated = True
    out.gain = float(zero / best) if best > 0 else (1.0 if zero == 0 else float("inf"))
    out.at_search_limit = bool(iy in (0, size - 1) or ix in (0, size - 1))
    sy = sx = 0.0
    if not out.at_search_limit:
        sy = _parabola_vertex(cost[iy - 1, ix], best, cost[iy + 1, ix])
        sx = _parabola_vertex(cost[iy, ix - 1], best, cost[iy, ix + 1])
    out.dx_cells = (ix - radius + sx) * f
    out.dy_cells = (iy - radius + sy) * f
    out.east_m = out.dx_cells * res_m
    out.north_m = -out.dy_cells * res_m
    moved = (iy, ix) != (radius, radius)
    out.significant = bool(moved and agree and out.gain >= SHIFT_MIN_GAIN)
    if not moved:
        out.note = "the surveys agree best where they are"
    elif not out.significant:
        out.note = "no shift improves the agreement clearly (flat or featureless ground)"
    elif out.at_search_limit:
        out.note = "the best shift is at the edge of the search range; the true shift may be larger"
    return out


def _parabola_vertex(left: float, centre: float, right: float) -> float:
    denom = left - 2.0 * centre + right
    if not np.isfinite(denom) or denom <= 0:
        return 0.0
    return float(np.clip(0.5 * (left - right) / denom, -0.5, 0.5))
