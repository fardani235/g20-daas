# Change Detection plugin

A **user plugin** (see the [user plugin guide](user-plugin-guide.md)) that
compares **two surveys of the same site** — a *before* and an *after* — and
answers the question a construction team asks every month: *what changed
between these two dates, and how much?* Source:
[`plugins/change-detection/`](../../plugins/change-detection/).

| | |
|---|---|
| Inputs | From **two surveys of one project**: an elevation surface each (`dsm` or `dtm`) and, optionally, an orthophoto each. The run is started on one survey; the other is picked in the run dialog |
| Output | `change-detection`: GeoJSON `FeatureCollection` (EPSG:4326) of change regions, classed `fill` / `cut` / `vegetation` / `appearance`, each with area, volume, volume uncertainty, mean and largest height change (`render_kind: change`). `change-detection-raster`: the elevation difference itself as a float32 GeoTIFF |
| Numbers | Share of the site that changed (of the area both surveys cover), cut / fill / net volume with uncertainty, the height offset that was removed, the smallest change that counts, a reliability grade with its reasons |
| Method | Classical raster analysis: common grid → horizontal and vertical co-registration measured on unchanged ground → shift-tolerant difference against a noise-derived level of detection → connected regions. **No machine learning, no models, nothing to download** |
| Runs in | the plugin sandbox (`services/plugin-runner`): numpy, scipy, rasterio, shapely — all already in the image |
| Needs | the platform's *comparison inputs* (`"survey": "reference"` in a manifest input, §8) — part of the same change |

"Progress" in this version means **how much of the site changed**, not a
percentage of planned work: there is no design model to measure against.

---

## 1. Install

```bash
cd plugins/change-detection
tools/build.sh        # -> plugins/change-detection-1.0.0.zip, plugins/change-detection-raster-1.0.0.zip
```

Upload on the **Plugins** page (organization owner) or with the API:

```bash
BASE=https://your.webodm.host
curl -c cj.txt -X POST "$BASE/api/method/login" -F usr=you@example.com -F pwd=secret
CSRF=$(curl -b cj.txt -s "$BASE/api/method/webodm_core.api.csrf.get_token" | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"])')
curl -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -F "file=@../change-detection-1.0.0.zip" \
     "$BASE/api/method/webodm_core.api.plugins.upload_plugin"
```

The two packages contain the same code and differ only in `plugin.json`
(the pattern of the Semantic Segmentation plugin). Install the first for the
map layer and the numbers; add the second if you want the difference raster
in GIS. They install as `<org-slug>.change-detection` and
`<org-slug>.change-detection-raster` and can also be published as
[marketplace](../marketplace/README.md) products.

The platform (Frappe app and frontend) must include the comparison-inputs
change (§8). An older platform ignores the manifest key `survey` and feeds
both inputs from the same task; the plugin notices (*The two elevation
surfaces are identical: the same survey was given twice*) and fails the run
rather than report "nothing changed".

---

## 2. Run it

1. Open the project on the map and select the survey you want the result on
   — normally the **newest** one.
2. *Analysis → Change Detection*. The dialog shows **Compare with**: every
   other completed survey of the project. Pick the earlier one.
3. *The comparison survey is the…* — `before` (default) when you picked an
   earlier survey, `after` if you started from the older survey and picked
   the newer one. This is the only place where "which is which" is decided:
   **fill** means the ground is higher in the *after* survey, **cut** lower.
4. Choose the surface for each survey (DSM by default; use the same kind for
   both) and leave the orthophotos selected if the surveys have them.
5. *Run*. The run row reads *Change Detection vs &lt;other survey&gt;*; when it
   completes, **Results** on the row shows the numbers and any warnings, and
   a *Change Detection vs …* layer appears in the Layers panel (red = cut,
   blue = fill, green = vegetation, amber = looks different). Click a region
   for its own area and volume. *Download* gives the GeoJSON.

Comparing the same survey with two different earlier ones keeps both results
(October vs September **and** October vs August); re-running the same pair
replaces the previous result.

### API

```bash
curl -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"plugin": "acme.change-detection", "task": "<october task>", "reference_task": "<september task>",
       "params": {"reference_role": "before"}}' \
  "$BASE/api/method/webodm_core.api.plugins.run_plugin"
```

`reference_task` must be a completed task of the **same project and
organization** (§8). `inputs` may pick datasets per input as for any plugin,
e.g. `{"surface": "dtm", "reference_surface": "dtm"}`.

### Local, without the platform

```bash
cd services/plugin-runner
python -m app.cli ../../plugins/change-detection \
    --input surface=oct_dsm.tif --input reference_surface=sep_dsm.tif \
    --input orthophoto=oct_ortho.tif --input reference_orthophoto=sep_ortho.tif \
    --output /tmp/change.geojson          # or /tmp/change.tif with plugin-raster.json
```

---

## 3. Parameters

Defaults are meant to be left alone; every one has a sentence in the dialog.

| Parameter | Default | Meaning |
|---|---|---|
| `reference_role` | `before` | Whether the comparison survey is the earlier (`before`) or the later (`after`) one |
| `vertical_alignment` | `auto` | `auto`: measure the height offset between the surveys on unchanged ground and remove it. `tilt`: also remove a tilt across the site. `none`: trust both surveys' heights as they are (both tied to the same ground control / RTK); the offset is still measured and reported |
| `horizontal_alignment` | `auto` | `auto`: apply a horizontal shift when sliding one surface over the other makes them agree clearly better. `off`: compare in place |
| `max_shift_m` | `3` | Largest horizontal shift searched. A larger one is reported, not corrected |
| `confidence` | `95` | How sure a height change must be, given the measured noise: 80 / 95 / 99 % (1.28 / 1.96 / 2.58 σ) |
| `min_height_change_m` | `0` | `0` = automatic: `confidence` × measured uncertainty, never below 0.05 m. A value raises the bar; it cannot lower it below what the noise allows |
| `min_area_m2` | `4` | Connected patches of change smaller than this are noise |
| `registration_tolerance_m` | `-1` | A cell counts as changed only if it differs from *everything within this distance* in the other survey. `-1` = one grid cell (wider when a shift was found but not corrected), `0` = off |
| `edge_buffer_m` | `2` | Strip trimmed from the border of the common area (reconstruction edges are unreliable) |
| `resolution_m` | `0` | `0` = the coarser survey's own resolution, coarsened automatically above 8 megapixels. Volumes do not depend on it |
| `vegetation_filter` | `true` | Report height change on green vegetation as its own class instead of cut/fill (needs an orthophoto) |
| `appearance_change` | `true` | Flag places that only look different (needs both orthophotos). Regions package only |
| `mask_insignificant` | `true` | Raster package only: empty cells where nothing significant changed (off = the full difference) |

---

## 4. Results

### The layer

| Class | Colour | Meaning | Counted in volumes |
|---|---|---|---|
| `fill` | blue | Surface is higher in the *after* survey | yes |
| `cut` | red | Surface is lower | yes |
| `vegetation` | green | Height changed where an orthophoto shows green vegetation (growth, mowing, clearing) | no — reported separately |
| `appearance` | amber | Looks different, height unchanged (paving, markings, materials) | no — it has no volume |

Feature properties: `class`, `label`, `color`, `area` (m²) and, for the
height classes, `volume_m3`, `volume_uncertainty_m3` (1 σ),
`below_detection_m3`, `mean_change_m`, `max_change_m` (signed, after −
before). At most 2000 regions are drawn (largest first); the totals always
include all of them.

### The numbers (`Results` on the run row)

```
Compared                        Site A — 1 September (before) → Site A — 1 October (after)
Area both surveys cover         27,714 m²
Changed                         4.0% of that area (1,105 m²)
Fill                            600 m³ ± 1
Cut                             1,240 m³ ± 1
Net                             -640 m³ ± 1 (more cut than fill)
Smallest height change counted  0.05 m (95% confidence)
Height offset removed           +0.83 m (noise ±0.02 m)
Vegetation (not counted)        240 m²
Looks different, same height    144 m²
Reliability                     High
```

(This is the test suite's synthetic site, whose true volumes are 600 and
1 240 m³; its error bars are tiny because synthetic surveys have no
systematic error. §6 has a run on real terrain.)

- **Changed** is the area of the cut and fill regions divided by the area
  *both* surveys cover (after the edge buffer). Vegetation and
  appearance-only regions are not in it.
- **± values** are one standard deviation (§6). Read them as a lower bound on
  the real uncertainty, not as a guarantee.
- **Reliability** is `High`, `Medium` or `Low` with the reason next to it;
  all reasons and warnings are listed under the numbers. *Low* means "do not
  quote these volumes"; *Medium* means "check the reason first".

The complete audit trail is in the run's metadata (`get_run` →
`output_metadata`): `change` (all totals), `alignment.vertical` (offset,
noise, share of unchanged ground, second candidate offset, the 6 × 6 table of
per-tile offsets, tilt, systematic uncertainty, noise correlation),
`alignment.horizontal` (shift, how much it improved the agreement, whether it
was applied), `detection` (level of detection and what set it, tolerance,
minimum area), `photos`, `grid`, `reliability`, `warnings`, `parameters`,
stage timings and peak memory.

### The raster package

`change-detection-raster` writes the difference *after − before* in metres
(height offset removed) as a single-band float32 GeoTIFF in the working CRS,
nodata −9999, with the offset and the level of detection in its tags. By
default only cut, fill and vegetation regions carry a value. On the map it is
drawn with the generic terrain ramp (which is not centred on zero), so it is
meant for download and GIS work; the regions package is the one to look at.

---

## 5. How it works, and why

Each step answers one of the ways two surveys can differ without the ground
having moved. Everything is measured **from the two surveys themselves**; no
step needs a parameter tuned to the site.

### 5.1 Common ground (`cdplugin/grid.py`)

Two processing runs never share a grid.

- **CRS**: the CRS of the survey the run belongs to (so the result lines up
  with that survey's layers); a local UTM zone if every input is geographic,
  because areas and volumes need metres. The other survey is warped into it.
- **Extent**: the intersection of the two footprints, minus `edge_buffer_m`.
  Ground only one survey covers is not compared and not counted in the
  "changed" share.
- **Resolution**: the *coarser* of the two surveys' resolutions — comparing
  finer would invent detail — and coarser still when the grid would exceed
  8 megapixels (the sandbox has 2 GB). Downsampling uses **average**
  resampling, which conserves volume: block mean × block area is the same at
  5 cm and at 25 cm (verified: test `average_resampling_conserves_the_mean_height`,
  `volumes_do_not_depend_on_the_comparison_resolution`).

### 5.2 Horizontal co-registration (`align.estimate_horizontal_shift`)

Without ground control two surveys can sit a metre apart sideways, and every
building edge and stockpile flank then looks like a tall, thin change.

One surface is slid over the other in whole-cell steps (within
`max_shift_m`) and the position where they agree best is kept. "Agree" is the
mean absolute height difference about its median, **truncated** at 10 noise
sigmas: every misplaced edge counts, while ground that really changed costs
the same at every shift and so cannot pull the estimate. The planar trend of
the difference is removed first, otherwise a *tilt* between the surveys looks
like a shift on sloping ground. The estimate is refined to a fraction of a
cell with a parabola.

A shift is **applied only if** the agreement improves by at least 5 % *and*
two interleaved halves of the samples independently find the same position;
on flat or featureless ground each half finds its own noise and nothing is
applied. The correction is done by re-reading the comparison survey with a
shifted grid, so it is resampled once, not twice. A shift at the edge of the
search range is reported and *not* applied.

Only a translation is estimated. Residual wobble below about one cell is left
to the tolerance in §5.4.

### 5.3 Vertical co-registration (`align.fit_stable_peak`, `analyse_tiles`)

Without ground control, absolute height is only as good as the drone's GPS
and barometer: one survey can sit decimetres or metres above the other. If
that is not removed the whole site looks like fill.

- **The offset is the mode, not the mean.** The height difference of
  unchanged ground piles up at one value (the offset); real change spreads
  over a wide range. The plugin takes the *tallest peak* of the difference
  distribution (half-sample mode, then a median within ±3 σ of it). A mean or
  median would be dragged by a large excavation; the mode is not, even when
  less than half of the overlap is unchanged (test
  `offset_ignores_a_large_excavation`: 45 % of the site dug out, offset
  recovered to 1 cm).
- **The noise is the width of that peak** — measured, not assumed.
- **How constant is the offset?** The same fit is repeated on a 6 × 6 tiling.
  The largest group of tiles that agree with one surface — the global offset,
  or a tilted plane when clearly more tiles agree with that — is the
  *unchanged ground*. Its spread is the **systematic uncertainty**; its plane
  exposes a **tilt**; the tiles outside it are where the site changed
  wholesale (they are not allowed to inflate the uncertainty, so a platform
  raised over 40 % of the site is still measured).
- **Tilt** is reported and — in `auto` — *not* removed: the bar for counting
  a change is raised by the drift instead, and the run is graded *Medium*
  with the suggestion to use `vertical_alignment = tilt`. Removing a tilt
  automatically would also remove a real planar grade, which the two surfaces
  alone cannot tell apart.
- **`none`** is for surveys tied to the same ground control: nothing is
  removed. If most of the overlap then differs by more than the noise, the
  run says so, because that is either a real site-wide change or a datum
  problem.

### 5.4 Real change versus noise (`cdplugin/detect.py`)

1. **Registration wobble → shift-tolerant difference.** A cell counts as
   raised only if it is above the *highest* value within the tolerance radius
   in the other survey, and as lowered only if below the *lowest*. By
   construction a pure shift of up to the tolerance produces exactly zero
   (test `a_pure_shift_within_the_tolerance_is_not_change`). The test is made
   from both surveys' point of view, so a thin wall that appears and one that
   disappears are both kept.
2. **Height noise → level of detection.** The tolerant difference must exceed
   `k × sqrt(noise² + systematic²)` (`k` from `confidence`), and never less
   than 0.05 m: a photogrammetric surface is not trustworthy to a few
   centimetres however quiet it looks.
3. **Speckle → minimum area.** What passes is grouped into connected regions
   (8-connectivity); regions under `min_area_m2` are dropped. No
   morphological opening is used — it would erase trenches narrower than
   three cells (test: a 1 m trench survives, a 1 m² speck does not).
4. **Giving the rim back.** The tolerance also shaves the rim off every real
   change. Cells within the tolerance radius + 1 of an established region
   are added back when their plain difference reaches the level of
   detection. It stops there on purpose: following the plain difference
   further lets a region creep into its surroundings (measured on real
   terrain: +6 % volume, +13 % with a lower rim threshold), so beyond the
   rim the tolerant test stays in charge.

**Volumes** are the sum of the plain, offset-corrected difference over a
region's cells × cell area. Nothing is interpolated or modelled.

### 5.5 What the orthophotos add (`cdplugin/visual.py`) — secondary

Photos differ in light, shadows and season, so they never touch a volume and
never block the elevation result: an unreadable orthophoto, or any failure in
this stage, becomes a warning and the run continues.

- **Vegetation**: green cover from the Excess Green index on
  brightness-normalised colour, from either date, grown by a couple of cells
  for ragged canopy edges. Height change there is its own class. This matters
  on a DSM, where a tree line that grew or was cleared would otherwise be
  the largest "earthwork" on site.
- **Appearance-only change**: where the height did *not* change, two
  illumination-tolerant cues are compared — colour as chromaticity
  (brightness divided out) and structure as local correlation of brightness
  in a 7 × 7 window. Both are judged against how much they vary on ground
  the elevation says is unchanged, so the thresholds calibrate themselves: a
  darker exposure flags nothing (tested), and if more than 35 % of the site
  is still flagged the pair is declared not comparable and the class is
  dropped.

Without an elevation pair the plugin still runs on two orthophotos alone and
returns appearance regions without volumes, graded *Low*.

### Why this approach and not another

| Alternative | Why not (for this version) |
|---|---|
| Mean or median of the difference as the offset | Biased by exactly the thing being measured: a big excavation moves it |
| Requiring ground control instead of estimating the offset | Most of our drone surveys have none; `vertical_alignment = none` is there for those that do |
| Nuth & Kääb slope/aspect co-registration | The standard for glacier DEMs; needs sloping stable terrain with varied aspect, which a flat construction site does not have |
| ICP / point-cloud M3C2 | Handles walls and overhangs properly, but is far heavier, harder to explain, and earthworks are 2.5-D. A candidate for a later version |
| Thresholding the plain difference | Floods the result at every edge with half a cell of misregistration (test `without_tolerance_a_small_shift_floods_the_result`) |
| Machine-learned change detection on the photos | Excluded by the brief; needs training data and breaks on light/season |

---

## 6. How far to trust the numbers

### Volume uncertainty

Each volume carries a 1 σ uncertainty with three parts added in quadrature:

- **random** — cell noise, corrected for the fact that neighbouring cells
  share their error (the number of cells per independent sample is measured
  by comparing single cells with 8 × 8 block means on unchanged ground);
- **systematic** — what is left of the height offset shifts every cell of a
  region the same way, so it scales with *area* and does not average out;
  across regions it adds linearly, which is why the site totals are less
  certain than the regions individually;
- **edge** — a region ends where the change falls below the level of
  detection, not where the change ends. The difference summed over a 5 m ring
  around the region measures what was left out (a stockpile's shallow toe).
  It is not added to the volume, because the ring also holds whatever else
  is next to the region, but it widens the error bar when it points the same
  way as the region.

What the error bar does **not** contain: a wrong choice between two candidate
offsets, warping that no plane explains, objects that are not earth (§7), and
change too thin to detect anywhere. Those are what the reliability grade and
the warnings are for.

### Reliability grade

| Grade | Triggered by |
|---|---|
| **Low** | less than 25 % of the overlap looks unchanged · two candidate offsets of similar weight · a DSM compared with a DTM · photos only |
| **Medium** | 25–50 % unchanged · a second, smaller part of the site at another level (if *that* is the stable ground, the result is inverted) · more than 25 % of the tiles at a different level than the rest · offset drifts across the site (tilt not removed) · horizontal shift found but not corrected · noise above 0.15 m · heights trusted (`none`) although most of the overlap differs · site too small to check the offset's consistency |
| **High** | none of the above |

### What was verified

130 automated tests on a synthetic site surveyed twice (different resolution,
extent, CRS, exposure; known pit, stockpile, trench, speck, tree growth and
resurfacing). On that site cut and fill are recovered within 2 %, the height
offset within 5 mm, a 1.8 m horizontal shift within 0.3 m.

One run on real data through the sandbox CLI under the 2 GB limit: a real
43 ha survey (14 716 × 11 640 px DSM at 5 cm, plus orthophoto) against a
second survey **derived from it** — re-gridded to 8 cm on a smaller footprint,
shifted 0.43 m, raised 1.37 m, with added noise, a 40 × 25 × 2.5 m pit and a
Gaussian mound of 3 m.

| | truth | reported |
|---|---|---|
| Height offset | +1.370 m | +1.374 m |
| Horizontal shift | 0.43 m | 0.37 m (applied) |
| Cut (pit) | 2 500 m³ | 2 461 m³ ± 15 (−1.6 %) |
| Fill (mound with a long shallow toe) | 1 206 m³ | 1 123 m³ ± 59 (−6.9 %) |
| Run | | 0.22 m grid (7.9 Mpx), 23 s, peak 0.77 GB |

Two honest readings of that table. The fill is the hard case for any
thresholded method — a third of the mound's footprint is below the level of
detection — and its error bar (which includes the edge term) covers it only
at 1.4 σ. The cut is off by more than its stated ±15: **the error bars are
lower bounds.** And this is *not* a validation on two independent flights:
the second survey shares the first one's reconstruction errors, so the
"unchanged ground" in it is quieter and better behaved than two real flights
would be. **Before quoting accuracy to customers, run it on a real pair with
independently measured volumes.**

---

## 7. Where it will struggle

Stated plainly, most important first.

1. **A change that covers most of the site cannot be told from a height
   offset.** If 80 % of the site was raised by 0.5 m, the surfaces alone are
   equally well explained by "the surveys are 0.5 m apart and 20 % was dug
   out" — and that is what `auto` will report, inverted, graded only
   *Medium* (it sees a second, smaller level and says so). The fix is not
   algorithmic: tie both surveys to ground control and use
   `vertical_alignment = none`.
2. **Thin change over a large area is invisible.** Anything below the level
   of detection (5 cm at best) is not counted: 3 cm of topsoil stripped over
   a hectare is 300 m³ the plugin will not report. Volumes are a floor for
   that reason, and the edge term does not cover it.
3. **Warped surveys.** Without ground control ODM surfaces can dome or tilt
   by decimetres. A tilt is detected and can be removed on request; a dome is
   not modelled — its extremes show up as broad, shallow cut/fill regions. The
   run is graded *Medium* when many tiles disagree, and the per-tile offset
   table in the metadata shows the pattern, but the plugin cannot separate a
   dome from real broad earthworks.
4. **The surface is not the ground.** On a DSM, everything that stands on the
   site is "surface": parked machines, containers, material stacks, a
   building going up, a crane that moved. They are real change and are
   reported as cut or fill; nothing here can tell earth from objects. Use the
   DTM for both surveys to suppress them — at the price of the DTM filter's
   own artefacts (it can also remove part of a stockpile).
5. **Vegetation is only caught when it is green and there is an orthophoto.**
   Dry grass, bare branches and crops after harvest pass as ground.
6. **Only a horizontal translation is corrected** — not rotation or scale —
   and only when the site has relief to hold on to. Shifts under about one
   cell are absorbed by the tolerance, which in turn makes detection slightly
   pessimistic on rough ground and steep slopes.
7. **Sharp, small features are lost on large sites.** Above 8 megapixels the
   grid is coarsened (43 ha → 0.22 m); features narrower than a cell or
   smaller than `min_area_m2` disappear.
8. **Water, glass, fresh asphalt, deep shadow** reconstruct poorly; the
   resulting holes are skipped, the resulting spikes can pass as change.
9. **Appearance-only change is a hint, not a measurement.** Shadow edges,
   wet patches, vehicles and seasonal colour are flagged alongside real
   resurfacing.
10. **Heights are assumed to be metres** even if the CRS is not.

---

## 8. Platform change: comparison inputs

Change detection needs two surveys; a plugin used to see one. The change is
deliberately small and knows nothing about change detection:

- **Manifest.** An input may say `"survey": "reference"` (default
  `"primary"`). A plugin with at least one such input is a comparison
  plugin; nothing else about it is special.
- **Run request.** `run_plugin` takes `reference_task` for those plugins (and
  refuses it for others). Inputs resolve exactly as before, each against its
  own survey.
- **Run record.** `WebODM Plugin Run.reference_task` (Link, read-only).
  The run still belongs to one task — the one it was started on — and its
  result is shown there.
- **Plugin request.** `context.reference_task` (same shape as
  `context.task`) and `context.inputs` (`{name: {dataset, survey}}`, so a
  plugin can tell a DSM from a DTM). The sandbox service is unchanged: it
  still receives named input files.
- **One result per pair**, and deleting either survey deletes the
  comparison (it could otherwise be neither explained nor reproduced).
- **Run panel.** Any plugin may put `summary` (`[{label, value}]`) and
  `warnings` (`[str]`) in its result metadata; the run row shows them under
  *Results*. Comparison runs are labelled *… vs &lt;survey&gt;*.

### Who may compare what

A user can only compare two surveys they could open side by side on the same
page: the reference must be **readable by the caller, in the caller's
organization, in the same project as the run's task, completed, and not the
task itself**. A survey that does not exist and one that belongs to another
organization get the same answer (*Comparison survey not found*), so task
ids cannot be probed.

The rule is enforced in three places, because each can be reached without
the others:

| Layer | Why it is needed |
|---|---|
| `api.plugins.run_plugin` (`_reference_task_for`) | the normal path; rejects before a run exists |
| `WebODM Plugin Run.validate` | a run created or edited through Desk or `/api/resource` never passes the API; `organization` is stamped from the session first, so a payload cannot claim another tenant |
| `plugins.runner.execute_run` (`_reference_task`) | the worker has no session and stages files with permissions bypassed; it re-checks the stored pair before copying anything into the sandbox |

Tests: `api/test_plugin_comparison.py` (22), including a foreign survey from
both sides, missing-vs-foreign indistinguishability, a run inserted directly,
a tampered run reaching the worker, and the deletion cascade.

---

## 9. What would make it materially better

- **Ground control.** The platform already supports GCP files per dataset.
  Two GCP-tied surveys with `vertical_alignment = none` remove limitation 1
  and most of 3. A natural next step is to pass each task's georeferencing
  quality in `context` so the default can follow it.
- **User-marked stable areas.** Letting the user outline ground known not to
  have moved (the access road, a neighbouring car park) would make the offset
  estimate independent of how much of the site changed. It needs geometry
  parameters, which plugins do not have yet.
- **A design surface as a third input** turns "how much changed" into "how
  much of the plan is done" — real progress.
- **Point-cloud comparison (M3C2)** for vertical faces and structures.
- **Running as a system operation** in the geospatial service would lift the
  2 GB / 8 megapixel ceiling for very large sites.

---

## 10. References

- Bickel, D. R. & Frühwirth, R. (2006). *On a fast, robust estimator of the
  mode.* Computational Statistics & Data Analysis 50. (half-sample mode)
- Lane, S. N., Westaway, R. M. & Hicks, D. M. (2003). *Estimation of erosion
  and deposition volumes in a large, gravel-bed, braided river using
  synoptic remote sensing.* ESPL 28. (level of detection)
- Wheaton, J. M. et al. (2010). *Accounting for uncertainty in DEMs from
  repeat topographic surveys.* ESPL 35. (DEM-of-difference uncertainty)
- Anderson, S. W. (2019). *Uncertainty in quantitative analyses of
  topographic change: error propagation and the role of thresholding.* ESPL
  44. (systematic vs random error, thresholding bias)
- Höhle, J. & Höhle, M. (2009). *Accuracy assessment of digital elevation
  models by means of robust statistical methods.* ISPRS J. 64. (NMAD)
- Nuth, C. & Kääb, A. (2011). *Co-registration and bias corrections of
  satellite elevation data sets.* The Cryosphere 5. (considered, §5)
- Woebbecke, D. M. et al. (1995). *Color indices for weed identification.*
  Trans. ASAE 38. (Excess Green)
- Wang, Z. et al. (2004). *Image quality assessment: from error visibility
  to structural similarity.* IEEE TIP 13. (structure term)
