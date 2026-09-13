## Purpose

Lets users run an enabled analysis plugin against a completed task and tracks
each execution to completion.

## ADDED Requirements

### Requirement: Run eligibility

The system SHALL permit a run only when the plugin is enabled for the caller's
organization, platform-enabled, the task belongs to the same organization and is
completed, and the task provides the inputs the operation requires.

#### Scenario: Eligible run accepted

- **WHEN** a member starts an enabled plugin on a completed task in their
  organization with the required inputs present
- **THEN** a run record is created with status Queued

#### Scenario: Plugin not enabled for organization

- **WHEN** a member starts a plugin their organization has not enabled
- **THEN** the request is rejected and no run record is created

#### Scenario: Task not completed

- **WHEN** a member starts a plugin on a task that is not completed
- **THEN** the request is rejected

#### Scenario: Cross-organization task

- **WHEN** a member starts a plugin on a task owned by another organization
- **THEN** the request is rejected

#### Scenario: Missing required input

- **WHEN** a member starts a plugin whose required input is absent from the task
- **THEN** the request is rejected with an indication of the missing input

### Requirement: Parameter handling

Run parameters SHALL default to the organization's saved plugin settings and
SHALL be validated against the operation's parameter schema, with run-specific
values overriding defaults.

#### Scenario: Defaults applied

- **WHEN** a run is started without explicit parameters
- **THEN** the organization's saved settings are used

#### Scenario: Override applied

- **WHEN** a run supplies a parameter valid per the schema
- **THEN** the supplied value overrides the default for that run only

#### Scenario: Invalid parameter rejected

- **WHEN** a run supplies a parameter that violates the schema
- **THEN** the request is rejected and no run is created

### Requirement: Asynchronous execution lifecycle

Each accepted run SHALL execute asynchronously and transition through Queued,
Running, and exactly one terminal state of Completed, Failed, or Cancelled,
updating progress while running.

#### Scenario: Successful lifecycle

- **WHEN** a queued run begins and its operation succeeds
- **THEN** the run transitions Queued to Running to Completed

#### Scenario: Operation failure

- **WHEN** the analysis operation fails
- **THEN** the run transitions to Failed and records an error message

#### Scenario: Cancellation

- **WHEN** a user cancels a queued or running run
- **THEN** the run transitions to Cancelled and any partial output is discarded

#### Scenario: Progress reporting

- **WHEN** a run is running and the operation reports progress
- **THEN** the run's progress reflects the latest reported value

### Requirement: Run record retention and history

The system SHALL retain a durable record of every run, including plugin, task,
organization, parameters, status, timestamps, and output references, and SHALL
allow members to list runs for tasks in their organization.

#### Scenario: Repeated runs recorded separately

- **WHEN** the same plugin is run repeatedly on a task
- **THEN** each execution is recorded separately and remains queryable

#### Scenario: Organization-scoped visibility

- **WHEN** a member lists runs
- **THEN** only runs belonging to their organization are returned
