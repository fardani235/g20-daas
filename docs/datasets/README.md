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
  different input set.
- **Deleting a task keeps its dataset.** The dataset stays in the library
  (shown as *unused*) until you delete it yourself.
- **A dataset in use cannot be deleted.** The error names the task(s) still
  pointing at it; delete those tasks first.
- **Deleting an unreferenced dataset deletes its images** — the private
  files, the object-storage copies and the cached thumbnails.

## Using the library

**Datasets page** (`/datasets`): lists every dataset of your organization
with image count, total size, how many tasks use it and who created it.
*New dataset* uploads images with a title and description. Open a dataset to
see its images (thumbnails, size, GPS position, altitude, capture time), the
tasks using it, and to edit or delete it.

**Adding a task** (project map page → *Add Task*): choose **Existing dataset**
and pick one, or **Upload new images** — the upload becomes a dataset (named
after the *Dataset title* field, defaulting to the project, date and count)
that the task points at. Presets and processing options work as before.

**Map view / Console**: the task card shows the dataset's image count and a
link to the dataset; the selected task's thumbnails and GPS markers come from
the dataset's images.

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
| `webodm_core.api.dataset.create_dataset` (multipart `files`, `title`, `description`) | upload → dataset |
| `webodm_core.api.dataset.update_dataset` (`name`, `title`, `description`) | edit metadata |
| `webodm_core.api.dataset.delete_dataset` (`name`) | delete; refused with the task names while referenced |
| `webodm_core.api.dataset.thumbnail` (`dataset`, `image`, `size`) | JPEG preview |
| `webodm_core.api.task.upload_images` (multipart: `project_id` + either `dataset` or `files` [+ `dataset_title`], `title`, `options`) | create a task over an existing dataset or from new uploads |
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
