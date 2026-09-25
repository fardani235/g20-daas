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

- **WHEN** images are uploaded
- **THEN** they land on the host first (EXIF extraction unchanged) and are copied to `inputs/` with the key recorded on each image row

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

### Requirement: Eviction

A periodic reaper SHALL evict cache blobs idle longer than a configured age
and then least-recently-used blobs down to a byte budget, only for blobs with
an object copy, never for tasks that are Queued, Provisioning or Running.

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
