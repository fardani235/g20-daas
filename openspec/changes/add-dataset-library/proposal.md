# Proposal: Dataset Library

## Why

An uploaded image set lives only inside the task it was uploaded for
(`WebODM Task.images` → `WebODM Task Image`). Running the same photos with
different options — a second preset, a DSM this time, a re-run after a
plugin update — means uploading gigabytes again, storing them twice and
paying for both copies in object storage. The images are the expensive,
slow-to-move part of a survey; the task is the cheap part. They should be
separate records with separate lifecycles.

## What Changes

- **New DocType `WebODM Dataset`** (organization-scoped: title, description,
  derived `image_count` / `total_size`, `created_by`) with child
  **`WebODM Dataset Image`** carrying exactly the fields `WebODM Task Image`
  had (file, filename, size, `storage_key`, latitude / longitude / altitude /
  capture time). `WebODM Task Image` is retired.
- **`WebODM Task.dataset`** (Link, required) replaces the task's `images`
  table. Processing, input sync, dispatch, the periodic backfill, the
  private-file cache lookup and the eviction candidate list all read images
  through the dataset (organization from the parent dataset).
- **Creation**: a Datasets page uploads images to create a dataset; the
  task dialog either picks an existing dataset or uploads new images, which
  become the dataset the task points at. Empty datasets are rejected; images
  are fixed once a dataset exists (title/description stay editable).
- **Deletion contract**: a task delete removes only that task's `raw/` +
  `assets/` objects — never inputs, which are shared. A referenced dataset
  cannot be deleted (the error names the tasks); an unreferenced one deletes
  its images by the key on each row, so migrated `tasks/<task>/inputs/` keys
  and new `datasets/<id>/inputs/` keys are handled alike. Datasets outlive
  tasks.
- **Object storage keeps working exactly as now** — same client, boundary
  check, cache-first reads and serving-cache fill; new uploads are keyed
  `orgs/<slug>/datasets/<id>/inputs/...`; a dataset image is "busy" while any
  task referencing the dataset is Queued / Provisioning / Running.
- **Thumbnails**: a server-side, disk-cached JPEG thumbnail endpoint replaces
  `<img src=original>` in the map view and dataset pages.
- **Staged, reversible migration**: additive DocTypes + nullable link →
  idempotent backfill (one dataset per task, rows/metadata copied, image
  Files re-attached, keys kept, `task.dataset` set) → verified drop of the
  legacy table that refuses to run while any task is unaccounted for.
- **Frontend**: Datasets tab, list + detail pages (edit, delete with the
  in-use refusal surfaced), dataset picker in the task dialog, map view fed
  from the dataset's images.

## Out of scope

Reusing task outputs as inputs, editing a dataset's images after creation,
dataset versioning, cross-organization sharing.
