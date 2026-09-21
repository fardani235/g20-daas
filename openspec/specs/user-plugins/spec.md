# User Plugins Specification

## Purpose

Lets an organization extend the analysis catalog with its own plugins —
uploaded Python packages that run in an isolated sandbox — without changing the
core processing workflow, and without any other organization seeing them.
Complements `plugin-catalog` (system plugins) and `plugin-execution` (runs),
which apply unchanged to user plugins.

## Requirements

### Requirement: Two plugin types in one catalog

Every catalog entry SHALL have a type, `System` or `User`. System entries are
synchronized from the geospatial service and shared by all organizations; User
entries are uploaded packages owned by exactly one organization. Runs, per-org
settings and the platform kill switch SHALL work identically for both types.

#### Scenario: Catalog sync ignores user plugins

- **WHEN** the geospatial catalog is synchronized
- **THEN** User entries are neither updated nor marked unavailable, even if the
  upstream catalog is empty

#### Scenario: Kill switch applies to user plugins

- **WHEN** a platform admin platform-disables a user plugin
- **THEN** its organization can no longer run it

### Requirement: Organization-scoped visibility

A User plugin SHALL be visible, configurable, runnable and removable only by
members of the organization that uploaded it (platform admins excepted). To
other organizations it MUST be indistinguishable from a plugin that does not
exist.

#### Scenario: Other organization cannot see or use it

- **WHEN** a member of another organization lists plugins, or attempts to
  enable, run or remove the plugin by id
- **THEN** it is absent from the list and every attempt is rejected as unknown

#### Scenario: Same id in two organizations

- **WHEN** two organizations upload packages with the same manifest `id`
- **THEN** two independent catalog entries are created, one per organization

### Requirement: Package upload and validation

An organization admin SHALL be able to upload a plugin as a zip package with a
`plugin.json` manifest at its root. The package MUST be validated before
anything is installed: size limits, safe member paths (no absolute paths,
`..` or symlinks), a present entrypoint, and a manifest whose id, version,
inputs (known task datasets), parameter schema (flat object of scalars), output
kind (`raster`/`vector`), render kind and timeout are well-formed.

#### Scenario: Valid package installed and enabled

- **WHEN** an organization admin uploads a valid package for the first time
- **THEN** a User catalog entry named `<org-slug>.<id>` is created, the package
  is stored privately, and the plugin is enabled for that organization

#### Scenario: Invalid package rejected

- **WHEN** the zip is malformed, contains unsafe paths, lacks `plugin.json` or
  the manifest is invalid
- **THEN** the upload is rejected with the reason and no catalog entry is created

#### Scenario: Re-upload upgrades in place

- **WHEN** an admin uploads a package whose manifest `id` already exists for
  the organization
- **THEN** the manifest, package and version are replaced, and the
  organization's enablement, saved settings and run history are preserved

#### Scenario: Non-admin cannot upload

- **WHEN** a non-admin member attempts an upload
- **THEN** it is rejected

### Requirement: Removal

An organization admin SHALL be able to remove a User plugin. Removal MUST
delete the catalog entry, its package, its per-organization settings and all of
its runs together with their output files. System plugins MUST NOT be removable
this way.

#### Scenario: Remove deletes everything

- **WHEN** an admin removes a user plugin
- **THEN** the plugin no longer appears in any list and its runs, outputs,
  settings and package are gone

### Requirement: Isolated execution

User plugins SHALL execute in a sandbox separate from the platform's services.
The sandbox MUST NOT have access to the site's files other than per-run copies
of the plugin's declared inputs, MUST NOT have network access, and MUST bound
each plugin process's wall-clock time, memory, output size and process count.
A plugin that crashes, hangs, produces no output or produces an unreadable
output SHALL fail only its own run, with the error recorded on the run.

#### Scenario: Plugin crash

- **WHEN** the plugin process exits non-zero
- **THEN** the run is Failed and its error contains the tail of the plugin's
  stderr, while the platform and other runs are unaffected

#### Scenario: Plugin hangs

- **WHEN** the plugin exceeds its manifest timeout
- **THEN** its process group is killed and the run is Failed with a timeout error

#### Scenario: Successful run

- **WHEN** the plugin writes a valid raster or vector output and exits zero
- **THEN** the output is stored on the run with georeferencing (extent, EPSG)
  derived by the runner, the plugin's optional metadata is recorded, and no
  staged files remain in the sandbox

### Requirement: Plugin contract

The runner SHALL pass a single `request.json` path to the plugin's entrypoint
containing `inputs` (name → absolute path), `params`, `output_path`,
`result_path` and `work_dir`. The plugin SHALL write its artifact to
`output_path`, MAY write `{"metadata": {...}}` to `result_path`, and SHALL
signal success with exit status 0. Raster outputs are GeoTIFFs; vector outputs
are GeoJSON FeatureCollections in EPSG:4326.

#### Scenario: Metadata reported

- **WHEN** the plugin writes `result.json` with a `metadata` object
- **THEN** those keys appear in the run's output metadata alongside the
  runner-derived georeferencing
