## 1. Platform: comparison inputs

- [x] 1.1 `plugins/package.py`: `survey` on manifest inputs (`primary` | `reference`), kept only when `reference`; `reference_input_names`
- [x] 1.2 `api/plugins.py`: `_reference_task_for` (readable, same organization, same project, completed, not itself; missing ≡ foreign), per-survey `_resolve_inputs`, run slot = plugin + task + reference, `reference_task` in `list_runs` / `get_run`
- [x] 1.3 `WebODM Plugin Run`: `reference_task` Link (indexed, read-only) + `validate` enforcing the same-organization / same-project rule on the record
- [x] 1.4 `plugins/runner.py`: `_reference_task` re-check, inputs staged from their own task, `context.reference_task`, `context.inputs`
- [x] 1.5 `WebODM Task.delete_plugin_runs`: runs that reference the task go too
- [x] 1.6 Tests `api/test_plugin_comparison.py` (manifest, eligibility, isolation at API / document / worker, staging, context, slots, deletion); full `webodm_core` suite green; checks shown to fail when the three isolation checks are removed

## 2. Frontend

- [x] 2.1 `lib/plugins.js`: `needsReferenceSurvey`, `referenceChoices`, per-survey `inputChoices`, `runSlotKey`, `runLabel`, `runSummary`
- [x] 2.2 `lib/change.js`: fixed class colours, legend, popup (escaped)
- [x] 2.3 `MapView.vue`: "Compare with" picker, `reference_task` in the request, row/layer label, *Results* (summary + warnings), `render_kind: change`
- [x] 2.4 Tests: `plugins.test.js`, `change.test.js`, `MapView.comparison.test.js`; full vitest + `vite build`

## 3. Plugin

- [x] 3.1 `cdplugin/grid.py`: working CRS, overlap, coarser resolution, pixel budget with automatic coarsening, overview-level `WarpedVRT` reads (average when shrinking), shifted reads
- [x] 3.2 `cdplugin/align.py`: half-sample mode + peak fit, tile consensus (constant / plane), block noise, horizontal shift search
- [x] 3.3 `cdplugin/detect.py`: shift-tolerant difference, level of detection, established regions, rim restoration, labelling
- [x] 3.4 `cdplugin/visual.py`: vegetation mask, appearance change
- [x] 3.5 `cdplugin/regions.py`: region statistics, three-part uncertainty, totals, GeoJSON
- [x] 3.6 `cdplugin/pipeline.py` + `main.py`: stages, never-blocking photo stage, reliability grade, summary, metadata; `output.py` for both packages
- [x] 3.7 `plugin.json`, `plugin-raster.json`, `tools/build.sh`, README

## 4. Tests and validation

- [x] 4.1 Synthetic site surveyed twice (analytic ground, known pit / stockpile / trench / speck / trees / paving; options for offset, tilt, shift, noise, resolution, extent, CRS, exposure)
- [x] 4.2 Unit tests per module; end-to-end through `run()` and `main.py`
- [x] 4.3 Both built packages accepted by `package.inspect_package` in the Frappe image
- [x] 4.4 Real 43 ha survey vs a derived second survey through the sandbox CLI under RLIMIT_AS 2 GB
- [ ] 4.5 Two independent real flights with independently measured volumes (no such pair available)
- [ ] 4.6 Browser smoke of the run dialog and layer on a running stack

## 5. Docs

- [x] 5.1 `docs/plugins/change-detection.md` (method, numbers, reliability, limits, platform change)
- [x] 5.2 `docs/plugins/user-plugin-guide.md` (comparison plugins, `context`, `summary` / `warnings`), runner README, repository README, SPEC.md §6, AGENTS.md, CI step
