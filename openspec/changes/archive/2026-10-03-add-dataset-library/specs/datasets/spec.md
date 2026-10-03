# Dataset Library Specification

## Purpose

Makes uploaded input imagery a first-class, reusable, organization-scoped
record — a `WebODM Dataset` — that any number of tasks reference, so running
another task over the same photos never means uploading them again. Tasks
own their outputs; datasets own the inputs.

## Requirements

### Requirement: Dataset record

A `WebODM Dataset` SHALL belong to exactly one organization and SHALL carry a
title, a description, the number of images and total bytes it holds, and who
created it. Its images SHALL be `WebODM Dataset Image` child rows with the
file, filename, size, object-storage key and photo details (latitude,
longitude, altitude, capture time). `WebODM Dataset Image` replaces
`WebODM Task Image`.

#### Scenario: Summary is derived

- **WHEN** a dataset is saved
- **THEN** `image_count` and `total_size` equal the number of image rows and the sum of their sizes

#### Scenario: Creator is recorded

- **WHEN** a member uploads a dataset
- **THEN** `created_by` is that member; a dataset split out of a pre-library task records that task's owner

### Requirement: Organization scoping

Datasets SHALL be tenant-owned like projects and tasks: stamped with the
actor's organization at insert, listed and read only within that
organization, and present in both permission hook maps.

#### Scenario: Other organization

- **WHEN** a member of organization B lists datasets or requests one of organization A's by name
- **THEN** A's datasets are absent from the list and the direct request is denied

### Requirement: Creation

A dataset SHALL be creatable from an upload on the Datasets page, and a task
SHALL be creatable either over an existing dataset or from an upload that
becomes a new dataset. A dataset with no images MUST be rejected.

#### Scenario: Upload from the library page

- **WHEN** a member uploads images with a title
- **THEN** a dataset exists with one row per image, EXIF photo details read from the stored bytes, and the files attached to the dataset

#### Scenario: Task over an existing dataset

- **WHEN** a task is created with `dataset=<name>` and no files
- **THEN** the task references that dataset and no dataset is created

#### Scenario: Task from an upload

- **WHEN** a task is created with files
- **THEN** a dataset is created from them first and the task references it

#### Scenario: Empty dataset

- **WHEN** a dataset is created with no images, through the API or directly
- **THEN** the creation is rejected and nothing is written

#### Scenario: Neither or both inputs

- **WHEN** a task is created with neither a dataset nor files, or with both
- **THEN** the request is rejected

### Requirement: Images are fixed

Once a dataset exists its image rows MUST NOT be added to, removed or
repointed; the title and description MAY be edited. Changing the inputs
means creating a new dataset.

#### Scenario: Row change refused

- **WHEN** a save adds, removes or changes the file of an image row
- **THEN** the save is rejected and the rows are unchanged

#### Scenario: Metadata edit

- **WHEN** a save changes only the title or description
- **THEN** it succeeds

### Requirement: Every task references one dataset

`dataset` SHALL be mandatory on `WebODM Task`, the task SHALL store no image
rows of its own, and the dataset MUST belong to the task's organization.
Processing SHALL resolve a task's images from its dataset. The task-progress
payload SHALL include the dataset summary and its image rows (with thumbnail
URLs) so the map view keeps its thumbnails and GPS markers.

#### Scenario: Missing dataset

- **WHEN** a task is inserted without `dataset`
- **THEN** the insert fails the mandatory check

#### Scenario: Cross-organization dataset

- **WHEN** a task names a dataset of another organization
- **THEN** the insert is refused

#### Scenario: Dispatch

- **WHEN** a Queued task is dispatched
- **THEN** the images sent to the node are the rows of its dataset, cache first and object storage second

### Requirement: Deletion order

Deleting a task MUST NOT delete its dataset or the dataset's images; it
removes only the task's own outputs (`raw/` and `assets/`). A dataset that
any task references MUST NOT be deletable, and the error SHALL name the
task(s). Deleting an unreferenced dataset SHALL remove its stored images by
the object key recorded on each row (whatever prefix the key lives under)
together with the attached files and cached thumbnails. Datasets outlive
tasks.

#### Scenario: Task delete

- **WHEN** the last task using a dataset is deleted
- **THEN** the dataset, its rows, its files and its objects remain, and the dataset is listed as unused

#### Scenario: Referenced dataset

- **WHEN** a dataset used by tasks "A" and "B" is deleted, by any client
- **THEN** the delete is refused with a message naming "A" and "B"

#### Scenario: Unreferenced dataset with mixed keys

- **WHEN** an unreferenced dataset whose rows point at `datasets/<id>/inputs/...` and at a migrated `tasks/<task>/inputs/...` key is deleted
- **THEN** both objects are deleted, a key outside the organization's namespace is skipped, and no other task's outputs are touched

### Requirement: Thumbnails

The map view and the dataset pages SHALL show dataset images through a
server-side thumbnail endpoint that serves small JPEGs (a fixed set of
sizes), caches them on disk, rebuilds them from the original — cache first,
object storage second — and responds with private, long-lived cache
headers and conditional-GET support. Originals are never sent to the
browser for previews.

#### Scenario: Cold thumbnail

- **WHEN** a thumbnail is requested for an image whose cache copy was evicted
- **THEN** the original is re-materialised from object storage and the thumbnail is built and cached

#### Scenario: Unauthorised

- **WHEN** a guest or a member of another organization requests a thumbnail
- **THEN** the request is denied

### Requirement: Staged, reversible migration

The move from task-owned images to datasets SHALL happen in stages: first
the additive DocTypes and a nullable `dataset` link; then an idempotent
backfill creating one dataset per existing task (title from the task),
copying its rows and metadata, moving the image file attachments to the
dataset, keeping object keys as they are and setting `task.dataset`; and
only after that backfill is verified, `dataset` becomes required and the old
task-image table is dropped. Reverting the code before the drop MUST leave
the data intact and usable.

#### Scenario: Backfill

- **WHEN** the backfill runs on a site with pre-library tasks
- **THEN** every task gets a dataset with its rows, keys and photo details, its image files are attached to the dataset, its output files stay on the task, and a second run changes nothing

#### Scenario: Drop refused

- **WHEN** the drop step finds a task without a dataset or a dataset with fewer rows than the legacy table holds for its task
- **THEN** it raises, the migrate stops, and the legacy table is untouched

#### Scenario: Fresh site

- **WHEN** the migration runs on a site that never had the legacy table
- **THEN** both steps are no-ops
