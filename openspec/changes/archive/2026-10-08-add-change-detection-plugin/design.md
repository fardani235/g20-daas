## Context

Plugins run on one completed task (`WebODM Plugin Run.task`), receive named
input files copied into a sandbox, and return one raster / vector / model.
Change detection needs a second task. The full method, its justification,
measured accuracy and limits are in `docs/plugins/change-detection.md`; this
file records the decisions.

## Decisions

### D1. The second survey is an attribute of an *input*, not a new kind of plugin

`"survey": "reference"` on a manifest input. Alternatives: a manifest flag
`comparison: true` with implicit `before_*` / `after_*` inputs (bakes one
use case into the platform); a run on a *pair* object (new doctype, new
listing, new permissions). The chosen form adds one optional key, one
optional request field and one nullable column; `_resolve_inputs`, staging
and the sandbox contract keep their shape. The run still belongs to one task.

### D2. The platform does not know "before" and "after"

It knows *primary* (the task the run is on) and *reference*. Chronology is a
plugin parameter (`reference_role`). Task creation dates are not capture
dates, so the platform could not decide it reliably anyway.

### D3. Same project, same organization, checked three times

A reference must be something the caller could already open on the same
page. Enforced in `run_plugin` (the path users take), in
`WebODMPluginRun.validate` (Desk and `/api/resource` do not pass the API)
and in the worker (no session, permissions bypassed while staging). Missing
and foreign tasks answer identically. Platform admins get no bypass here,
as elsewhere in `api/plugins.py`.

### D4. One result per (plugin, task, reference); delete cascades from either task

Monthly work compares the newest survey with last month's *and* with the
baseline, so the slot includes the reference. A comparison whose second
survey is deleted cannot be reproduced or explained, and the link would
block the delete: the run goes with it (same rule as for the run's own
task). Considered: keep the result and null the link — leaves rows nobody
can remove (there is no run delete in the UI).

### D5. Elevation first; photos never block

The surfaces carry volumes and are stable across light and season. Photos
are used for a vegetation mask and an appearance-only class; any failure
there is a warning.

### D6. Offset = mode of the difference distribution

Robust to change covering up to (and beyond) half of the site, parameter
free, and the same fit yields the noise. Mean/median are biased by the
change itself. Unidentifiable case (site-wide uniform change) is documented
and graded, with `vertical_alignment = none` as the way out.

### D7. Tilt is detected, not silently removed

Removing a plane automatically would remove a real planar grade. `auto`
raises the level of detection by the drift and says so; `tilt` is explicit.

### D8. Tile consensus for systematic uncertainty

The largest group of tiles agreeing with one flat or tilted surface is
"unchanged ground"; tiles outside it are real wholesale change and must not
inflate the uncertainty (first version used the spread of *all* tiles: a
platform over 42 % of the site raised the level of detection to 0.73 m and
nothing was reported).

### D9. Shift-tolerant difference for detection, plain difference for volume

Neighbourhood min/max envelope from both surveys' point of view: zero for a
pure shift within the tolerance. Volumes use the plain difference. The rim
the tolerance removes is restored within radius + 1 only (unbounded growth
overshot by 6–13 % on real terrain).

### D10. Horizontal shift: truncated-L1 search, applied only on agreement of two halves

NMAD as the cost ignored misplaced edges (< 5 % of cells): a 1.8 m shift
improved it by 18 % only. Truncated absolute deviation counts edges and
saturates on real change. The planar trend is removed first so a tilt is not
read as a shift. Applied only if the gain is ≥ 5 % and two interleaved
halves agree within a cell.

### D11. Uncertainty has an edge term; error bars are lower bounds

Random (with measured spatial correlation) + systematic (× area) + the
sub-threshold difference in a 5 m ring. Not covered: wrong offset peak,
warps, non-earth objects, thin site-wide layers — these go to the
reliability grade, warnings and documentation.

### D12. 8 megapixel working grid, coarsened automatically

Average resampling makes volumes independent of the working resolution, so
coarsening is safe for volumes and silent failure is worse than a warning.
Measured peak 0.77 GB on a 43 ha survey.

## Risks

- No validation on two independent real flights yet (the real-terrain test
  derives the second survey from the first). Stated in the documentation.
- `rasterio.Env(GDAL_CACHEMAX=128)` means 128 **bytes**; this plugin passes
  bytes. The same line in the semantic-segmentation, object-detection and
  building-footprints plugins disables their block cache (not changed here).
