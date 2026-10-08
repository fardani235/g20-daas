## Why

Construction teams fly the same site every few weeks and process each flight
as its own survey. The surveys sit next to each other in a project with no
way to ask the one question that matters: *what changed between these two
dates, and how much?* "Change detection analytics" is advertised on the
landing page and does not exist. Analysis plugins are the place for it, but
a plugin only ever sees one survey.

## What Changes

- **Comparison inputs (platform, generic).** A manifest input may say
  `"survey": "reference"`. `run_plugin` then takes `reference_task`: a
  completed task of the same project and organization that the caller can
  read. `WebODM Plugin Run` gains `reference_task`; inputs resolve per
  survey; the worker stages each input from its own task and tells the
  plugin about both (`context.reference_task`, `context.inputs`). One result
  per (plugin, task, reference task); deleting either task deletes the run.
  The same-organization / same-project rule is enforced in the run API, on
  the run document and in the worker. The sandbox service is unchanged.
- **Run panel (generic).** A "Compare with" picker for comparison plugins;
  run rows and layers labelled *… vs &lt;survey&gt;*; any plugin's
  `metadata.summary` / `metadata.warnings` shown under *Results*; fixed
  colours, legend and popups for `render_kind: change`.
- **New user plugin `plugins/change-detection/`** (two packages from one
  code base: regions as GeoJSON, difference as GeoTIFF). Classical DEM
  differencing: common grid (CRS, overlap, coarser resolution, averaging) →
  horizontal shift search applied only when two halves of the data agree →
  height offset as the mode of the difference distribution, noise as its
  width, a 6 × 6 tile consensus for systematic uncertainty and tilt →
  shift-tolerant difference against a level of detection → connected
  regions with minimum area → areas, volumes and three-part uncertainty →
  reliability grade and plain-language summary. Orthophotos (optional)
  separate vegetation and flag appearance-only change; they never touch a
  volume or fail a run.
- **Non-goals:** machine learning, a design model / percent-complete,
  point-cloud comparison, rotation/scale co-registration, dome correction,
  user-drawn stable areas, comparing across projects.

## Impact

- Specs: new `change-detection`; added requirements in `plugin-execution`
  (Comparison runs), `user-plugins` (Reference-survey inputs),
  `plugin-outputs` (Result summary on the run row).
- Code: `webodm_core` (`plugins/package.py`, `api/plugins.py`,
  `plugins/runner.py`, `WebODM Plugin Run` doctype + controller,
  `WebODM Task.delete_plugin_runs`), frontend (`lib/plugins.js`,
  `lib/change.js`, `MapView.vue`), `plugins/change-detection/`, CI.
- Data: one new nullable, indexed Link column
  (`tabWebODM Plugin Run.reference_task`); `bench migrate` adds it. Existing
  runs and plugins are unaffected.
- Deploy order: Frappe image and frontend before the package is uploaded
  (an older platform ignores `survey`; the plugin then refuses to compare a
  survey with itself).
