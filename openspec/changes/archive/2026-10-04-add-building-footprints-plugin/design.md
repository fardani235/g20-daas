## Context

See `proposal.md` for motivation and `specs/building-footprints/spec.md` for
the behaviour contract. Current state that shapes this design:

- User plugins (`user-plugins` spec) are zip packages run by
  `services/plugin-runner` as a child process with `RLIMIT_AS` 2 GB, a
  scrubbed single-threaded environment, no network, and a fixed library set
  (numpy, rasterio, shapely, Pillow, onnxruntime, laspy, …). Vector outputs
  are GeoJSON 4326; `render_kind: segmentation` draws per-`class` filled
  polygons with a legend (`lib/segmentation.js`), `detections` draws outlines.
- `plugins/semantic-segmentation` and `plugins/object-detection` established
  the conventions: model cards in `models/*.json`, `tools/update_manifest.py`
  regenerating the manifest's model enum, `tools/fetch_models.py` fetching
  weights at build time with sha-256, `tools/build.sh`, Hann-blended tiling
  with a rolling row-band accumulator, `WarpedVRT` reads from the matching
  overview, GDAL cache at 128 MB, no pre-run validation.
- The HOT DINOv3 building model (`kshitijrajsharma/dinov3-hot-buildings`,
  Apache-2.0) is already exported end-to-end to ONNX: `image[1,3,256,256]` →
  `logits[1,3,256,256]`, channels 0 = mask logit, 1 = boundary logit,
  2 = signed distance (tanh; positive inside). Inspection of the graph showed
  **no normalisation and no activations inside** (the input feeds the
  patch-embedding Conv directly; the output is the raw 1×1 head Conv,
  resized) — so mean/std and sigmoid/tanh are the plugin's job, declared on
  the card. On a real orthophoto, mask>0.5 and distance>0 agree with
  correlation 0.91 with the card's normalisation vs 0.83 without. Two
  exports exist: ViT-S (220 MB) and ViT-L (1.4 GB, over the 1 GB
  extracted-package limit).

## Goals / Non-Goals

**Goals**

- One polygon per building, touching buildings separated, GeoJSON 4326 with
  class/score/area, drawn by the existing segmentation renderer.
- Model-card driven: a different export is a card edit.
- Stay inside the sandbox: hard memory budget with an early, actionable
  failure; flat-memory tiling; checksum-verified weights fetched at build.
- Same failure style and tooling as the sibling plugins.

**Non-goals**

- DSM/height fusion, roof classification, heights, training, GPU, frontend
  changes, shipping the ViT-L export.

## Decisions

### D1: Instance separation = watershed on the signed-distance plane

**Decision:** Threshold the mask probability into blobs, then split each blob
with a marker-controlled watershed on the *negative* distance plane
(`skimage.segmentation.watershed`, 8-connected, constrained to the blob).

**Rationale:** The distance head peaks once per building and dips at shared
walls; flooding from one marker per peak assigns the dip to a boundary. This
is the standard recipe for touching instances and what the HOT pipeline the
model was trained for does. **Alternatives:** boundary-channel subtraction
(mask − boundary, then label) — depends on a thin, complete boundary ring
and loses pixels; connected components only — merges touching buildings;
per-instance models (Mask R-CNN) — a different, far heavier model.

### D2: Seeds — local maxima for small blobs, *extended* maxima for large ones

**Decision:** Blobs ≥ `large_blob_area_px` are seeded with the regional maxima
of the h-maxima transform `R_f(f − h)` (`skimage.morphology.reconstruction`
+ `local_maxima`); smaller blobs with local maxima at least
`seed_min_distance` apart, computed as pixels within 0.02 of the maximum of
their (2·d+1)² neighbourhood and labelled 8-connected.

**Rationale:** Prominence (h) is robust to the ripples a large flat roof
produces; min-distance maxima are cheap and adequate for small blobs. Both
are implemented so that a *plateau or crest* is one seed. Verified on
synthetic data: `skimage.morphology.h_maxima` returns only the exact-top
pixels of each dome (8 disconnected seeds for a ridge rippling by 0.001;
1 with the extended-maxima formulation), and `peak_local_max` thins a crest
longer than `min_distance` to several coordinates — both split one building.
**Alternatives:** h_maxima/peak_local_max as-is (over-segmentation);
h-maxima everywhere (slower on tens of thousands of small blobs; exposed as
`large_blob_area_px = 1` for users who want it).

### D3: Per-component processing, in-place ids

**Decision:** Label blobs once (`ndimage.label`, int32), iterate
`find_objects`, do seeding and watershed on each blob's bounding-box crop,
and write instance ids back into the same int32 plane (new ids start above
the blob count so they never collide), compacting with a LUT at the end.
Tiny blobs are dropped before any seeding.

**Rationale:** Memory is bounded by the largest blob, not the raster — a
full-raster `watershed` would need ~30 bytes/pixel (float64 copies) and blow
the 2 GB budget at any useful size — and avoids a second full-size label
plane. **Alternatives:** full-raster watershed (memory); separate output
plane (+4 bytes/pixel).

### D4: Memory budget fails early instead of coarsening

**Decision:** `DEFAULT_MAX_PIXELS = 50 000 000`; a grid above it raises before
any read with the resolution to use, computed so that re-running at the
suggestion fits (the sqrt estimate is checked against the per-axis ceil of
the real grid and nudged).

**Rationale:** Per pixel the plugin holds probability (uint8), distance
(float32 — float16 creates exact plateaus that break seeding), the mask and
the int32 labels: ~10 bytes/pixel, ~500 MB at the cap, plus a ~650 MB ONNX
session, inside 2 GB. The segmentation plugin coarsens silently; here the
user should decide, because footprint geometry quality depends on the
resolution. **Alternative:** silent coarsening with a warning (rejected per
the task brief).

### D5: Gaussian-weighted stitching, two planes, uint8 + float32

**Decision:** 2-D Gaussian weights (σ = side/8, floor 1e-3) over overlapping
256 px windows, blended per row band (`PlaneBlender`, generalising the
segmentation plugin's `BandBlender` from arg-max to a weighted mean of
planes). Default overlap 64 px (stride 192); 128 documented as the
smoothest. Only the mask and distance planes are kept.

**Rationale:** nnU-Net's recipe hides seams; row-band accumulation keeps
memory flat; the boundary channel is not needed by the watershed and would
cost another plane. **Alternatives:** Hann taper (equivalent in spirit,
Gaussian matches the literature for this model class); overlap 128 default
(2.3× windows, ~2× run time on CPU).

### D6: Regularisation guarded by neighbour overlap

**Decision:** Replace a footprint by its minimum rotated rectangle when it
fills ≥ `regularize_area_threshold` of it *and* the rectangle adds ≤
`regularize_overlap_tol_m2` of new overlap with neighbours, measured against
the original outlines (`STRtree`), so results do not depend on order.
Threshold 1 disables.

**Rationale:** Rasterised buildings are staircases; rectangles are the right
prior for most, but a rectangle snapped over a terraced neighbour would
swallow it. **Alternatives:** full orthogonalisation (Douglas-Peucker +
right-angle snapping) — more complex, deferred; no regularisation.

### D7: Card describes the graph contract, plugin applies it

**Decision:** The card carries `onnx.input/output/input_size/channels/scale/
mean/std` and `onnx.outputs.{mask,boundary,distance}.{channel,activation}`.
The plugin validates the graph against it at load (tensor names, NCHW,
3 input channels, static size equals `input_size`, enough output channels)
and the output shape at the first window, and fails with what to fix.

**Rationale:** Makes a different export (ViT-L, fine-tuned decoder, another
mask+distance network) a card edit; keeps "unexpected output shape" an
actionable run failure, as the brief requires. **Alternative:** hard-coding
the HOT contract.

### D8: ONNX Runtime threading fallback

**Decision:** Open the session with ORT defaults; if the thread pool cannot be
created (`pthread_create … EAGAIN` under the sandbox's `RLIMIT_NPROC`), warn
and retry with `intra_op_num_threads = 1`.

**Rationale:** Observed when running the sandbox CLI on a developer machine
(NPROC counts the user's processes). In the container the default works and
is ~1.5× faster than single-threaded.

## Risks / Trade-offs

- CPU cost: ~2.6 s per 256 px window in the sandbox (UperNet decoder); a
  43 ha survey at 0.25 m was 180 windows / 477 s, peak RSS 762 MB.
  Mitigations: overview reads, skipping empty windows (51 of 180 there),
  `resolution_m`/`overlap` knobs, one-hour timeout.
- Domain shift: trained on OAM imagery of the Global South; on a European
  village the footprints score 0.66–0.78 (quartiles) and fill 0.63–0.75 of
  their rectangle, so the 0.85 regularisation default rarely triggers there
  (kept: lowering it to 0.75 would also regularise L-shapes). Terraced rows
  and semi-detached pairs were separated correctly; a large textureless
  industrial roof came out fragmented. The parameters are exposed; the card
  is replaceable.
- Package size: 190 MB zipped leaves no room for a second large model in the
  same package.

## Migration Plan

1. Merge; CI rebuilds the plugin-runner image with scipy/scikit-image.
2. Operators pull/pin the new runner image (compose digest / Helm tag).
3. `tools/fetch_models.py && tools/build.sh`, upload the zip per organization.

## Open Questions

- Orthogonalisation of non-rectangular outlines (right-angle snapping) —
  follow-up if users ask for cadastral-grade geometry.
- Per-building height from the DSM as an optional second input — natural
  extension once footprints exist.
