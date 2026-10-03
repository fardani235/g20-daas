# Tasks: Dataset Library

## 1. Data model

- [x] 1.1 Add `WebODM Dataset` (title, description, image_count, total_size,
  created_by, images, organization) and child `WebODM Dataset Image` with
  the former task-image fields; remove `webodm_task_image`.
- [x] 1.2 Replace `WebODM Task.images` with the required `dataset` Link.
- [x] 1.3 Register `WebODM Dataset` in `permission_query_conditions`,
  `has_permission` and the stamping `doc_events`; verify
  `test_tenant_doctype_coverage` passes.

## 2. Controllers

- [x] 2.1 `WebODMDataset`: `created_by`, non-empty rule, fixed images,
  derived summary, `on_trash` (in-use refusal naming tasks, object delete by
  key, blob + thumbnail cleanup).
- [x] 2.2 `WebODMTask.validate`: dataset must exist and share the org;
  `on_trash` deletes only `raw/` + `assets/`.
- [x] 2.3 `tenancy_hooks`: patch-only `organization_from_source` bypass for
  migration copies.

## 3. Storage

- [x] 3.1 `storage.dataset_prefix/dataset_key`, `task_output_prefixes`;
  `delete_task_objects` and `reset_outputs` scoped to outputs.
- [x] 3.2 `assets.sync_inputs(dataset)`, `sync_task_inputs`,
  `input_sources` via the dataset, `delete_dataset_objects`, `sync_pending`
  over `WebODM Dataset Image`.
- [x] 3.3 `cache.storage_key_for_file_url` and `_candidates` (busy = any
  running/queued task references the dataset) over dataset images.
- [x] 3.4 `plugins.files._safe_private_name` checks the File table too.

## 4. API and processing

- [x] 4.1 `webodm_core/datasets.py`: EXIF extraction (moved from
  `api/task.py`), `create_from_uploads`, summaries, `referencing_tasks`,
  thumbnails.
- [x] 4.2 `api/dataset.py`: `list_datasets`, `get_dataset`, `create_dataset`,
  `update_dataset`, `delete_dataset`, `thumbnail`.
- [x] 4.3 `api/task.py`: `upload_images` two modes (dataset | files),
  `_task_payload` with `dataset_summary` + `images`, `list_tasks`.
- [x] 4.4 `task_runner._get_task_images` → `assets.input_sources`;
  `compute.request_for_task` uses `datasets.task_image_count`.

## 5. Migration

- [x] 5.1 `patches.backfill_task_datasets` (idempotent; `report`, `rollback`).
- [x] 5.2 `patches.drop_task_image_table` (verify, then drop).
- [x] 5.3 Run `bench migrate` on a site with seeded legacy tasks; verify
  datasets, keys, Files, `task.dataset`, table dropped.

## 6. Frontend

- [x] 6.1 `lib/datasets.js` client + helpers with tests.
- [x] 6.2 `pages/Datasets.vue` (list, upload-to-create, delete with in-use
  alert) + test; `pages/DatasetDetail.vue` (images, edit, delete, tasks).
- [x] 6.3 Routes `/datasets`, `/datasets/:id`; nav tab; `nav.test.js`.
- [x] 6.4 `MapView.vue`: `list_tasks`, dataset picker (existing | upload),
  `get_task_progress` on select, thumbnail URLs for cards and markers;
  `Console.vue` shows the dataset.

## 7. Verification

- [x] 7.1 `api/test_dataset.py` (24), `patches/test_backfill_task_datasets.py`
  (4), updated relay / download / deletion / compute tests; full
  `webodm_core` suite on the throwaway site.
- [x] 7.2 Frontend vitest (273) and `vite build`.
- [x] 7.3 HTTP smoke (curl) and browser smoke (playwright-core + Chrome)
  against a web container with the built SPA.

## 8. Documentation

- [x] 8.1 `openspec/specs/datasets/spec.md`, object-storage amendments,
  this change.
- [x] 8.2 `docs/datasets/{README,migration}.md`, AGENTS.md Phase 19,
  SPEC.md, TODO.md, README updates.
