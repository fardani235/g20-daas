# On-Demand Processing Specification

## Purpose

Provisions a processing node per task on demand through a provider-neutral
provisioner service, runs the task there, and gives the node back when the
task ends; falls back to a static node when no provider is configured.

## Requirements

### Requirement: Provider seam

The system SHALL confine every cloud-specific concern (SDKs, credentials,
machine images, networking) to the provisioner service behind one `Provider`
interface (create, describe, destroy, list managed). Nothing in the task
pipeline, data model or storage layer SHALL depend on which provider produced
a node.

#### Scenario: Second provider

- **WHEN** a new provider implementation is registered in the provisioner
- **THEN** the app requires no code change beyond configuration

#### Scenario: Handles are opaque

- **WHEN** the app stores an instance
- **THEN** it stores only the provisioner's handle, endpoint and token, never a provider resource id it interprets

### Requirement: Provisioning state

A started task with no ready node SHALL enter the `Provisioning` state while an
instance is requested, and SHALL dispatch to the node once the provisioner
reports NodeODM answering on it.

#### Scenario: Node becomes ready

- **WHEN** the provisioner reports the instance ready
- **THEN** the instance record becomes Ready with hostname and port and the task dispatches to it and becomes Running

#### Scenario: Provisioning timeout

- **WHEN** the instance is not ready within the configured provisioning timeout
- **THEN** the instance is destroyed and the task returns to Queued under the existing dispatch backoff

#### Scenario: Readiness by polling

- **WHEN** the provisioner is asked to describe an instance
- **THEN** it reports ready only after NodeODM itself answers on the node's public endpoint

### Requirement: Fallback to a static node

With no provisioner configured, or when the provisioner is unreachable or has
no provider, tasks SHALL run on a configured static processing node exactly
as before.

#### Scenario: No provisioner configured

- **WHEN** `provisioner_url` is empty
- **THEN** a Queued task dispatches to the first static node and never enters Provisioning

#### Scenario: Provisioner down

- **WHEN** the provisioner cannot be reached at dispatch
- **THEN** the task dispatches to the static node if one exists, else defers with backoff

### Requirement: Concurrency caps

The system SHALL enforce a global and a per-organization cap on live compute
instances; a task blocked by a cap SHALL wait without consuming a dispatch
attempt.

#### Scenario: Cap reached

- **WHEN** the number of Requested/Provisioning/Ready instances equals the cap
- **THEN** the task stays Queued with a capacity message and is retried after a short wait

### Requirement: Instance class from options

The instance class SHALL be derived from the task's processing options and
image count; users SHALL NOT pick a provider.

#### Scenario: Heavy task

- **WHEN** a task requests ultra feature or point-cloud quality, or exceeds the large-task image threshold
- **THEN** the `cpu-large` class is requested, degrading to the provider's default if not offered

### Requirement: Release on terminal state

Completed, Failed and Cancelled SHALL release the task's instance once its
outputs are safely stored; a failed destroy SHALL be retried by the sweep and
SHALL never change the task's outcome.

#### Scenario: Destroy fails

- **WHEN** the provisioner cannot destroy the instance
- **THEN** the record becomes Terminating with the error recorded and the task's status is unaffected

### Requirement: Safety sweep

A periodic sweep SHALL destroy instances whose task is done or gone, that
exceeded their lifetime budget (failing the task), that never became ready
within the provisioning timeout, whose earlier destroy failed, and
provider-side instances carrying this deployment's tags with no live record
past a grace period.

#### Scenario: Orphan

- **WHEN** the provider lists a managed instance with no live record older than the orphan grace
- **THEN** the sweep destroys it and logs the handle

#### Scenario: Lifetime budget

- **WHEN** an instance passes its `expires_at`
- **THEN** it is destroyed and its Running task is marked Failed with the reason

### Requirement: Node security

Node endpoints SHALL be reachable only from the app host's egress address and
SHALL require a per-run bearer token; the provisioner SHALL be reachable only
inside the stack; no credential SHALL appear in site config, the database,
logs, error messages or the frontend.

#### Scenario: Node token not persisted

- **WHEN** an instance is requested
- **THEN** its per-run bearer token is derived from an environment-only secret and the record name, and no token value is written to the compute instance record or site config

#### Scenario: Error mapping

- **WHEN** a provider or storage call fails
- **THEN** the recorded error carries an error code or class name, never request parameters or keys

### Requirement: Multi-tenancy

Compute instance records SHALL be organization-scoped and visible only to the
owning organization and platform administrators.

#### Scenario: Cross-org read

- **WHEN** a member of another organization lists compute instances
- **THEN** none of this organization's records are returned
