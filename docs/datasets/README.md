# Dataset library

A **dataset** is a reusable set of input images that belongs to your
organization. A task processes exactly one dataset; any number of tasks can
process the same dataset, so re-running a survey with different options never
means uploading the photos again.

## Concepts

| Record | Owns | Lifecycle |
| --- | --- | --- |
| `WebODM Dataset` | the images (rows, private files, object-storage copies), title, description, creator | created by an upload; title/description editable; images fixed; deleted only when no task uses it |
| `WebODM Task` | its outputs (orthophoto, DSM/DTM, point cloud, model, `raw/` + `assets/` objects) and a **reference** to one dataset | deleting it never touches the dataset |

Rules worth knowing:

- **A dataset is never empty.** An upload without files is refused.
- **Images are fixed** once the dataset exists. You can rename it and edit
  the description, but not add or remove images — create a new dataset for a
  different input set. The optional **ground control file is the exception**:
  it can be attached, replaced or removed later (see *Ground control*).
- **Deleting a task keeps its dataset.** The dataset stays in the library
  (shown as *unused*) until you delete it yourself.
- **A dataset in use cannot be deleted.** The error names the task(s) still
  pointing at it; delete those tasks first.
- **Deleting an unreferenced dataset deletes its images** — the private
  files, the object-storage copies and the cached thumbnails.

## Using the library

**Datasets page** (`/datasets`): lists every dataset of your organization
with image count, total size, a *GCP* mark when it has ground control, how
many tasks use it and who created it. *New dataset* uploads images with a
title, description and an optional ground control file: drop the photos (or
a whole folder) on the dialog or click to choose them; the progress bar
shows bytes sent, speed and time left, then a "finishing" phase while the
server registers the images; *Cancel* aborts and leaves nothing behind. Open
a dataset to see its images (thumbnails, size, GPS position, altitude,
capture time), its ground control, the tasks using it, and to edit or delete it.

**Adding a task** (project map page → *Add Task*): choose **Existing dataset**
and pick one (the dialog says whether it has ground control), or **Upload new
images** — the upload becomes a dataset (named after the *Dataset title*
field, defaulting to the project, date and count) that the task points at,
with the same drop zone, GCP field and progress bar. Presets and processing
options work as before. See [`../tasks/README.md`](../tasks/README.md) for
what you can do with a task afterwards (edit its settings, duplicate it).

**Map view / Console**: the task card shows the dataset's image count and a
link to the dataset; the selected task's thumbnails and GPS markers come from
the dataset's images.

## Ground control

A dataset may carry **one ground control point (GCP) file** next to its
images, in ODM's `gcp_list.txt` format:

```
EPSG:32633                                   ← CRS: proj4, EPSG:<code> or e.g. "WGS84 UTM 33N"
544256.7 5320919.9 5.1 3044 2622 IMG_0001.JPG gcp1
544257.7 5320920.9 5.2 3144 2722 IMG_0002.JPG gcp2
```

(`geo_x geo_y geo_z im_x im_y image_name [gcp_name]`; blank lines and `#`
comments are ignored.)

- **Attach it with the upload** (Datasets page → *New dataset*, or the task
  dialog's *Upload new images*) — pick the file in the *Ground control*
  field, or just drop `gcp_list.txt` together with the photos. **Or later**,
  on the dataset page: *Attach GCP file* / *Replace* / *Remove*. Unlike the
  images, the GCP file **may change after the dataset exists**: it is a few
  kilobytes of measurements about the images, and re-uploading a survey to
  add or correct it is exactly the friction this avoids.
- **It is validated on upload.** A file that is not the format (a photo in
  the wrong slot, a `geo.txt`, a missing CRS line, a non-numeric column) is
  refused with the line number, and — for uploads — before any photo is
  written. The dataset page shows what was parsed: CRS, point count, and any
  **image names in the file that are not in the dataset** (ODM matches names
  exactly), plus a warning when there are fewer than three points.
- **It reaches the engine** with every task over the dataset, including
  duplicates and re-runs: dispatch appends it to the image upload under the
  fixed name `gcp_list.txt`, which NodeODM hands to ODM as `--gcp`. This is
  the contract NodeODM already has (and what self-hosted WebODM uses); no
  node API was added. A GCP file that can be read neither from the cache nor
  from object storage fails the task with a clear error rather than silently
  processing without it.
- **To run a task without the dataset's GCP**, enable ODM's `use-exif`
  option on that task — the usual way to compare with/without ground control
  over the same images.
- **Storage and lifecycle** match the images: a private File attached to the
  dataset, an object at `orgs/<slug>/datasets/<id>/inputs/gcp_<name>` once
  synced (`gcp_storage_key`), re-materialised from the bucket when the host
  copy was evicted, backfilled by the periodic sync, deleted with the
  dataset, and the previous object/file deleted on replace.
- A task started **before** a GCP change keeps the file it was given on the
  node; the record on the dataset describes the current file, not every
  past run (the task console shows what ODM actually used).

Fields on `WebODM Dataset`: `gcp_file` (Attach), `gcp_filename`,
`gcp_summary` (JSON: `crs`, `points`, `labels`, `images`, `image_count`,
`unknown_images`, `few_points`), `gcp_storage_key`. Every dataset summary
(list rows, `get_dataset`, a task's `dataset_summary`) carries a `gcp` block
with `filename`, `url`, `upload_name`, `crs`, `points`, `labels`,
`image_count`, `unknown_images`, `few_points` — or `null`.

## Thumbnails

Previews go through `GET /api/method/webodm_core.api.dataset.thumbnail?dataset=<id>&image=<row>&size=<128|256|512>`.
The first request for an image builds a small JPEG from the original (cache
first, object storage second — so it works after the cache copy was evicted),
stores it under `sites/<site>/private/thumbnails/<dataset>/`, and later
requests are served with `Cache-Control: private, max-age=604800` and ETag
support (304). Originals are never sent to the browser for previews. The
thumbnail cache is derived data: it is not part of the serving-cache
eviction and is removed with the dataset.

## API

All endpoints require a signed-in member of an organization; datasets are
org-scoped like projects and tasks.

| Method | Purpose |
| --- | --- |
| `webodm_core.api.dataset.list_datasets` | datasets of the caller's org with `image_count`, `total_size`, `task_count`, `created_by` |
| `webodm_core.api.dataset.get_dataset` (`name`) | one dataset + `images` (with `thumbnail` URLs) + `tasks` using it |
| `webodm_core.api.dataset.create_dataset` (multipart `files`, `title`, `description`, optional `gcp`) | upload → dataset |
| `webodm_core.api.dataset.update_dataset` (`name`, `title`, `description`) | edit metadata |
| `webodm_core.api.dataset.set_gcp` (multipart `name`, `gcp`) | attach or replace the ground control file; refused with the line number when it is not a GCP file |
| `webodm_core.api.dataset.remove_gcp` (`name`) | detach and delete the ground control file |
| `webodm_core.api.dataset.delete_dataset` (`name`) | delete; refused with the task names while referenced |
| `webodm_core.api.dataset.thumbnail` (`dataset`, `image`, `size`) | JPEG preview |
| `webodm_core.api.task.upload_images` (multipart: `project_id` + either `dataset` or `files` [+ `dataset_title`, `gcp`], `title`, `options`) | create a task over an existing dataset or from new uploads; `gcp` only with `files` |
| `webodm_core.api.task.list_tasks` (`project_id`) | tasks with `dataset_summary` |
| `webodm_core.api.task.get_task_progress` (`task_name`) | task + `dataset_summary` + `images` |

The `/api/resource/WebODM Dataset` REST resource is also org-scoped and
enforces the same controller rules (immutability, in-use refusal).

## Object storage

New uploads are stored under `orgs/<slug>/datasets/<id>/inputs/<row>_<file>`.
Datasets migrated from pre-library tasks keep their existing
`orgs/<slug>/tasks/<task>/inputs/...` keys — both are fine because the
organization namespace is what the boundary check enforces. Task deletion
and re-processing only ever delete a task's `raw/` and `assets/` prefixes;
dataset deletion deletes by the key recorded on each image row. A dataset
image is protected from cache eviction while any task referencing the
dataset is Queued, Provisioning or Running.

See [`migration.md`](migration.md) for moving an existing site to the
library and `openspec/specs/datasets/spec.md` for the normative behaviour.
