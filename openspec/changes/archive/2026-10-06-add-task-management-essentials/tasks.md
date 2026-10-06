# Tasks

## 1. Ground control (backend)

- [x] 1.1 `webodm_core/gcp.py`: parser for ODM's gcp_list format (CRS line, ≥6 columns, numeric checks, comments, BOM/CRLF, size/binary caps), summary with unknown images / few points; `test_gcp.py` (12)
- [x] 1.2 `WebODM Dataset`: `gcp_file`, `gcp_filename`, `gcp_summary`, `gcp_storage_key`; controller `_check_gcp_fields` (attachment of this dataset only), blob cleanup on delete
- [x] 1.3 `datasets.py`: `read_gcp_upload`, `_store_gcp` (attached_to_field), `attach_gcp` (replace drops the old File/blob/object), `remove_gcp`, `gcp_block` in `summary`, `SUMMARY_FIELDS`, `create_from_uploads(gcp_upload=)` validating before any photo is written
- [x] 1.4 `storage/assets.py`: `_sync_gcp` in `sync_inputs`, `gcp_key`, `_gcp_input_source` appended last as `gcp_list.txt`, `InputUnavailable`, `delete_dataset_gcp_object`, backfill of host-only GCPs; `storage/cache.py` lookup; `task_runner.process_task` fails on `InputUnavailable`; `node_client.upload_content_type`
- [x] 1.5 API: `create_dataset` + `upload_images` accept `gcp` (upload mode only), `set_gcp`, `remove_gcp`; `api/test_gcp.py` (18)

## 2. Edit and duplicate (backend)

- [x] 2.1 `api.task.update_task` (`EDITABLE_STATUSES`, `TaskBusy`, `db_set`), `_decode_processing_options`, `options` + `can_edit` in payloads
- [x] 2.2 `api.task.duplicate_task`, `duplicate_title`
- [x] 2.3 `api/test_task_management.py` (15) incl. delete-safety of the options column

## 3. Frontend

- [x] 3.1 `lib/upload.js` (XHR `uploadForm`, drop/folder handling, partitioning, progress maths, tracker) + tests; `lib/tasks.js` (client, status rules, option conversion) + tests; `lib/datasets.js` on the XHR uploader with `gcp`, `setDatasetGcp`, `removeDatasetGcp`, `gcpLabel`, `gcpWarnings`
- [x] 3.2 `UploadDropzone.vue`, `UploadProgress.vue`, `GcpFileField.vue`, `TaskSettingsDialog.vue` + component tests
- [x] 3.3 `MapView.vue`: drop zone + GCP field + progress + cancel in Add Task; GCP status for an existing dataset; Edit / Duplicate buttons; GCP mark; `Datasets.vue` same upload UX + GCP column; `DatasetDetail.vue` ground control section (attach / replace / remove / download, parsed summary, warnings); `Console.vue` GCP in header

## 4. Docs

- [x] 4.1 `docs/tasks/README.md` (design record + guide), `docs/datasets/README.md` ground control section + API rows, spec delta + `openspec/specs/task-management/spec.md`, SPEC.md, TODO.md, README.md, AGENTS.md
