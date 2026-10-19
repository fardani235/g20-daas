# On-Demand Processing — delta

## ADDED Requirements

### Requirement: Lifecycle facts for metering

A compute instance SHALL record, each at most once, the instant a job was
accepted by its node (`dispatched_at`), the instant the platform first asked
for it to be destroyed (`released_at`) and why (`release_reason`). These
facts SHALL be written by statements that cannot fail the dispatch or the
teardown they describe, and SHALL NOT be read by the task pipeline.

#### Scenario: Dispatch

- **WHEN** a node on an on-demand instance accepts a task
- **THEN** the instance's `dispatched_at` is set, and a later dispatch does not change it

#### Scenario: Release

- **WHEN** a task reaches a terminal state and its instance is released
- **THEN** `released_at` is the time of that first request and `release_reason` names the task's outcome, whether or not the destroy succeeds

#### Scenario: Stamp fails

- **WHEN** writing one of these facts raises
- **THEN** the dispatch or teardown proceeds exactly as it would have and the error is logged

## MODIFIED Requirements

### Requirement: Safety sweep

A periodic sweep SHALL destroy instances whose task is done or gone, that
exceeded their lifetime budget (failing the task), that never became ready
within the provisioning timeout, whose earlier destroy failed, and
provider-side instances carrying this deployment's tags with no live record
past a grace period. The sweep's queries SHALL be valid on the deployed
database; a query the database rejects stops the whole sweep.

#### Scenario: Orphan

- **WHEN** the provider lists a managed instance with no live record older than the orphan grace
- **THEN** the sweep destroys it and logs the handle

#### Scenario: Lifetime budget

- **WHEN** an instance passes its `expires_at`
- **THEN** it is destroyed and its Running task is marked Failed with the reason

#### Scenario: Failed instance left running

- **WHEN** an instance was marked Failed and the process died before destroying it
- **THEN** the next sweep finds it (Failed, a handle, no termination time), destroys it and records the termination time
