"""From a building mask and a signed-distance plane to one label per building.

Thresholding the mask gives connected blobs; where buildings touch (terraces,
dense informal settlements, annexes) one blob covers several buildings. The
model's third channel is a signed distance to the nearest building edge
(positive inside), which peaks once per building and dips at the shared wall.
A marker-controlled watershed on the *negative* distance (Beucher & Meyer
1993; Soille, "Morphological Image Analysis") therefore splits the blob along
those dips — the standard recipe for touching instances (the same idea as
U-Net's distance/border targets and the HOT fAIr pipeline this model comes
from).

Seeds:

* small blobs — local maxima of the distance at least ``seed_min_distance``
  pixels apart: pixels within ``LOCAL_MAX_TOLERANCE`` of the maximum of their
  (2·min_distance+1)² neighbourhood, labelled 8-connected so a plateau or the
  crest along a roof is one seed. (``skimage.feature.peak_local_max`` thins
  such crests to single coordinates — a crest longer than ``seed_min_distance``
  would then yield several seeds and split one building — and the tolerance
  absorbs the ~0.001 ripples the model leaves along a crest.)
* large blobs (area ≥ ``large_blob_area_px``) — *extended maxima* with
  prominence ``h_maxima_depth``: the regional maxima of the h-maxima transform
  ``R_f(f − h)`` (Soille, §6.3.5; ``skimage.morphology.reconstruction`` +
  ``local_maxima``). Every dome that rises at least ``h`` above the saddle to
  its neighbours yields exactly one connected seed, whatever the ripples on
  top of it. ``skimage.morphology.h_maxima`` itself is *not* used: it marks
  only the exact-top pixels of each dome, which on a rippled crest are many
  disconnected single pixels.

A blob with one seed (or none) is one building. Everything runs per connected
component on its bounding box, so memory is bounded by the largest blob rather
than the raster, and tiny components are dropped before any work is done.
"""

from dataclasses import asdict, dataclass

import numpy as np

from .errors import FootprintError

OUTSIDE = -2.0  # below the tanh range, so no seed can land outside a blob
LOCAL_MAX_TOLERANCE = 0.02  # small-blob seeding: ripples smaller than this (on the -1..1 scale) do not split a crest


def _libs():
    try:
        from scipy import ndimage
        from skimage.morphology import local_maxima, reconstruction
        from skimage.segmentation import watershed
    except ImportError as e:
        raise FootprintError(
            "scipy and scikit-image are not available in the plugin sandbox (needed for the watershed that "
            "separates touching buildings). Ask the platform operator to update the plugin-runner image."
        ) from e
    return ndimage, local_maxima, reconstruction, watershed


_EIGHT = np.ones((3, 3), dtype=bool)


@dataclass
class InstanceStats:
    components: int = 0          # connected blobs above the mask threshold
    dropped_small: int = 0       # blobs below min_area_px, removed before seeding
    large_blobs: int = 0         # blobs seeded with h-maxima
    seeds: int = 0               # total markers
    split: int = 0               # blobs that produced more than one building
    instances: int = 0           # buildings

    def as_dict(self) -> dict:
        return asdict(self)


def separate_instances(mask: np.ndarray, dist: np.ndarray, *, seed_min_distance: int, large_blob_area_px: int,
                       h_maxima_depth: float, min_area_px: int, log=None) -> tuple[np.ndarray, InstanceStats]:
    """Label buildings in ``mask`` using ``dist``. Returns (int32 labels 1..N, stats)."""
    ndimage, local_maxima, reconstruction, watershed = _libs()
    stats = InstanceStats()
    labels, n = ndimage.label(mask, structure=_EIGHT)
    labels = labels.astype("int32", copy=False)
    stats.components = int(n)
    if n == 0:
        return labels, stats

    objects = ndimage.find_objects(labels)
    next_id = n + 1  # new ids never collide with component ids still present in the array
    report_every = max(1, n // 10)
    for i, sl in enumerate(objects, 1):
        if sl is None:
            continue
        crop = labels[sl]
        sub = crop == i
        area = int(sub.sum())
        if area < max(1, min_area_px):
            crop[sub] = 0
            stats.dropped_small += 1
            continue
        d = np.asarray(dist[sl], dtype="float32").copy()
        d[~sub] = OUTSIDE

        if area >= large_blob_area_px:
            stats.large_blobs += 1
            hmax = reconstruction(d - h_maxima_depth, d, method="dilation")
            seeds = local_maxima(hmax, connectivity=2, allow_borders=True) & sub & (d > 0)
        else:
            size = 2 * max(1, seed_min_distance) + 1
            local_max = ndimage.maximum_filter(d, size=size, mode="constant", cval=OUTSIDE)
            seeds = (d >= local_max - LOCAL_MAX_TOLERANCE) & sub & (d > 0)
        markers, n_seeds = ndimage.label(seeds, structure=_EIGHT)
        stats.seeds += int(n_seeds)

        if n_seeds <= 1:
            crop[sub] = next_id
            next_id += 1
        else:
            ws = watershed(-d, markers, mask=sub, connectivity=2)
            region = ws > 0
            crop[region] = ws[region] + (next_id - 1)
            crop[sub & ~region] = 0  # unreachable pixels (none with 8-connectivity); never leave a stale id
            next_id += int(n_seeds)
            stats.split += 1
        if log and i % report_every == 0:
            log(f"  {i}/{n} blobs")

    # Compact the ids: component ids (≤ n) that survive were all reassigned, so
    # anything still ≤ n is a dropped blob → 0; new ids become 1..N in raster order.
    labels[labels <= n] = 0
    present = np.unique(labels)
    present = present[present > 0]
    lut = np.zeros(int(labels.max()) + 1 if present.size else 1, dtype="int32")
    lut[present] = np.arange(1, present.size + 1, dtype="int32")
    labels = lut[labels]
    stats.instances = int(present.size)
    return labels, stats
