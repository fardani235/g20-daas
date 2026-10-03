## ADDED Requirements

### Requirement: Point-cloud octree artifacts

The Potree octree derived from a task's point cloud SHALL be a canonical task
artifact stored under the task's organization-namespaced prefix
(`orgs/<slug>/tasks/<task>/potree/`), served cache-first and object-storage
second, evictable only while an object copy exists, removed when the task is
deleted or re-processed, and never written under an `inputs/` prefix.

#### Scenario: Stored under the organization namespace

- **WHEN** a point cloud is converted for a task and a bucket is configured
- **THEN** the octree files are stored under `orgs/<slug>/tasks/<task>/potree/` and the task records the octree's keys and size

#### Scenario: Cold octree

- **WHEN** the local octree copy is absent but an object copy exists
- **THEN** the octree files are streamed from object storage by range and a background cache fill is started, so the next open is warm

#### Scenario: Eviction

- **WHEN** an octree's cache copy is idle and an object copy exists
- **THEN** it may be evicted like other derived artifacts and the viewer re-fetches it on the next open; an octree with no object copy is never evicted

#### Scenario: Deletion and re-processing

- **WHEN** a task is deleted or restarted and produces a new point cloud
- **THEN** its previous `potree/` objects, on-disk octree and conversion summary are removed and the conversion state is reset, while `inputs/` is untouched

#### Scenario: Organization boundary

- **WHEN** a recorded octree key points into another organization's prefix
- **THEN** storage refuses to read, convert or delete it with an organization-boundary error
