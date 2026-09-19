# Plugin Catalog Specification

## Purpose

Defines the catalog of available analysis plugins and controls which
organizations may see, enable, and configure them.

## Requirements

### Requirement: Plugin catalog source of truth

The geospatial service SHALL expose a read-only catalog of analysis operations.
Each operation MUST have a stable identifier and describe its label, description,
version, parameter schema, output kind (raster or vector), and the task inputs it
requires.

#### Scenario: Catalog is available

- **WHEN** a client requests the analysis catalog
- **THEN** it receives every registered operation including its id, label,
  description, version, parameter schema, output kind, and required task inputs

#### Scenario: Source unavailable during sync

- **WHEN** the geospatial service is unreachable during a catalog sync
- **THEN** the previously synced catalog remains intact and the sync is reported
  as failed

### Requirement: Catalog synchronization

The system SHALL synchronize the geospatial catalog into persisted catalog
records, creating new records for new operations and preserving run history for
operations that disappear.

#### Scenario: New operation appears

- **WHEN** the geospatial catalog contains an operation not yet persisted
- **THEN** a new catalog record is created, platform-enabled by default and not
  enabled for any organization

#### Scenario: Operation removed upstream

- **WHEN** a persisted operation is no longer returned by the catalog
- **THEN** it is marked unavailable and cannot be newly enabled, while existing
  run history is preserved

### Requirement: Per-organization enablement

An organization member SHALL only be able to run plugins their organization has
enabled, and only organization admins may change enablement or settings.

#### Scenario: Enabled plugin visible

- **WHEN** an organization member opens the plugins view
- **THEN** plugins enabled for that organization are listed as usable and others
  as unavailable

#### Scenario: Non-admin cannot change enablement

- **WHEN** a non-admin organization member attempts to enable, disable, or
  reconfigure a plugin
- **THEN** the change is rejected

### Requirement: Platform kill switch

A platform admin SHALL be able to disable a plugin for all organizations. A
platform-disabled plugin MUST NOT be runnable by any organization regardless of
per-organization enablement.

#### Scenario: Platform disables a plugin

- **WHEN** a platform admin disables a plugin
- **THEN** it becomes unavailable to every organization and any attempt to run it
  is rejected

#### Scenario: Organization settings preserved across toggle

- **WHEN** a platform admin disables and later re-enables a plugin
- **THEN** each organization's prior enablement and settings are preserved

### Requirement: Plugin configuration validation

Organization plugin settings SHALL be validated against the operation's parameter
schema before storage.

#### Scenario: Invalid setting rejected

- **WHEN** an organization admin saves settings that violate the parameter schema
- **THEN** the save is rejected with a validation error and no settings are stored

#### Scenario: Valid setting accepted

- **WHEN** an organization admin saves settings matching the parameter schema
- **THEN** the settings are stored and used as defaults for that organization's
  runs
