# Object Storage Specification

## Purpose

Makes S3-compatible object storage the system of record for task inputs and
outputs, with the host keeping a disposable serving cache; nothing is correct
only because the cache is warm.

## Requirements

### Requirement: Canonical store

When a bucket is configured, uploaded imagery and every task output SHALL be
stored in object storage under an organization-namespaced key; the host copy
SHALL be a cache.

#### Scenario: Upload

- **WHEN** images are uploaded (as a dataset, or as the upload that creates a task's dataset)
- **THEN** they land on the host first (EXIF extraction unchanged) and are copied to `orgs/<slug>/datasets/<id>/inputs/` with the key recorded on each `WebODM Dataset Image` row

#### Scenario: Migrated dataset keeps its keys

- **WHEN** a dataset was split out of a pre-library task
- **THEN** its rows keep their `orgs/<slug>/tasks/<task>/inputs/` keys and every read, sync and delete accepts them, because the organization namespace is what the boundary check enforces

#### Scenario: No bucket configured

- **WHEN** `storage_bucket` is empty
- **THEN** the host-disk pipeline runs unchanged

### Requirement: Output relay without host disk

Completed outputs SHALL be relayed from the node into object storage without
touching the host disk, rasters SHALL be converted to Cloud-Optimized GeoTIFFs
S3 → S3 by the geospatial service, and every output SHALL be written through
to the serving cache and recorded with its key, size, etag and COG flag.

#### Scenario: Successful relay

- **WHEN** a node reports COMPLETED
- **THEN** `all.zip` streams to `raw/`, members are unpacked through range reads, rasters are converted into `assets/*.tif` as COGs, other assets are stored as-is, and the task becomes Completed only after all outputs are in storage

#### Scenario: Transient failure

- **WHEN** storage, the node or the conversion fails transiently
- **THEN** the task stays Running and the next poll resumes from the last completed step

#### Scenario: Conversion keeps failing

- **WHEN** COG conversion fails until the poll budget is about to run out
- **THEN** the raw raster is stored as the asset and the task completes

### Requirement: Cache-first, storage-second serving

Tiles, the 3D viewer, downloads and plugin inputs SHALL resolve from the
serving cache when present and from object storage otherwise.

#### Scenario: Cold tiles

- **WHEN** a raster's cache copy is absent
- **THEN** the tile proxy passes the `s3://` URI to the geospatial service, which reads the COG by range requests, and a background cache fill is started

#### Scenario: Cold private file

- **WHEN** a logged-in user requests an evicted `/private/files/<name>` that has an object copy
- **THEN** the blob is re-materialised before Frappe serves it

#### Scenario: Plugin staging

- **WHEN** a plugin run needs an evicted input
- **THEN** the input is fetched into the cache and staged into the sandbox, which has no network egress of its own

### Requirement: Re-processing after eviction

A task SHALL be re-processable after its cache copies are gone.

#### Scenario: Evicted inputs

- **WHEN** a task is restarted and an image is missing on disk
- **THEN** the image is streamed to the node directly from object storage

#### Scenario: Previous outputs are replaced

- **WHEN** a Completed, Failed or Cancelled task is restarted
- **THEN** its previous output fields, asset rows, metadata rows, cached files and `raw/` + `assets/` objects are removed before the task is queued, inputs are kept, and the next completion records only the new run's outputs

#### Scenario: Inputs are shared

- **WHEN** a task is reset or deleted
- **THEN** no object under any `inputs/` prefix is touched — the images belong to the task's dataset, which other tasks may reference

### Requirement: Eviction

A periodic reaper SHALL evict cache blobs idle longer than a configured age
and then least-recently-used blobs down to a byte budget, only for blobs with
an object copy, never for tasks that are Queued, Provisioning or Running. A
dataset image SHALL count as busy while any task referencing its dataset is
in one of those states.

#### Scenario: Shared dataset in use

- **WHEN** one of two tasks reading the same dataset is Running
- **THEN** none of that dataset's images is evicted

#### Scenario: Host-only blob

- **WHEN** a blob has no recorded object key
- **THEN** it is never evicted

### Requirement: Organization isolation

Every object key SHALL live under the owning organization's prefix and every
read or write with an organization context SHALL refuse a key outside it.

#### Scenario: Tampered key

- **WHEN** a row's key points into another organization's prefix
- **THEN** the read is refused with an organization-boundary error

### Requirement: Identities

Provisioning, writing outputs, reading and converting rasters, and signing
access SHALL use separate least-privilege identities; credentials SHALL come
from the environment or secrets, never from site config or the database.

#### Scenario: Geospatial identity

- **WHEN** the geospatial service writes
- **THEN** it can only write COGs under `assets/` and cannot delete or read `inputs/`

### Requirement: Backfill

Host-only blobs (legacy tasks, uploads made while storage was down) SHALL be
copied to object storage by a bounded periodic job so the whole site converges
to storage as the system of record.

#### Scenario: Legacy task

- **WHEN** a Completed task has outputs on disk but no asset rows with keys
- **THEN** the backfill uploads them and records the keys

#### Scenario: Unsynced dataset image

- **WHEN** a `WebODM Dataset Image` row has no key
- **THEN** the backfill uploads it under the dataset's prefix (getting the organization from the parent dataset) and records the key; a row whose file is not attached to that dataset is skipped and logged rather than stalling the job
