# Image Pinning Specification

## Purpose

Resolve the container images a Helm release will run to immutable digests from
the registry at deploy time and apply them to the release, so operators never
hand-edit digests and a deployment cannot silently run a mix of old and new
images.

## Requirements

### Requirement: Resolve configured image tags to digests at deploy time

For every image entry in the effective values for a release, the deployment
command SHALL resolve the image's repository and tag to the digest the registry
currently serves, without requiring any committed file to be edited.

#### Scenario: All tags resolve

- **WHEN** an operator runs the deployment command for a release whose values define image repository/tag pairs
- **THEN** each image's tag is resolved to its current registry digest
- **AND** the resolved set is applied to the release

#### Scenario: An image tag cannot be resolved

- **WHEN** any configured image's tag cannot be resolved from its registry
- **THEN** the command SHALL abort without changing the release
- **AND** SHALL name the image whose resolution failed

#### Scenario: Private registry credentials

- **WHEN** an image is hosted in a private registry
- **THEN** resolution SHALL use the operator's existing container registry credentials, and a missing or invalid credential SHALL be reported as a resolution failure

### Requirement: Resolved digests override stale committed digests

Because a digest in an image reference takes precedence over its tag, the
deployment command SHALL ensure the resolved digest replaces any digest already
present for that image in the effective values, so a bumped tag is never masked
by an old pin.

#### Scenario: Committed digest is stale relative to its tag

- **WHEN** the effective values pin an image to a digest that no longer matches its tag
- **THEN** the deployed reference SHALL use the digest resolved for the tag at deploy time, not the stale committed digest

#### Scenario: Tag is bumped but the digest is not

- **WHEN** an operator changes only an image's tag in the values
- **THEN** the deployment SHALL run the build that tag points at

### Requirement: Deploy without editing committed files

Applying resolved digests SHALL NOT require modifying any file tracked in
version control.

#### Scenario: Deployment from a clean working tree

- **WHEN** the deployment command runs
- **THEN** the release is updated with the resolved digests
- **AND** no tracked file (compose, values, or manifest) is modified

### Requirement: The applied pins are recoverable from the release

The digests actually applied to a release SHALL be retrievable from the release
record after deployment, so an operator can audit or reproduce what a release
ran.

#### Scenario: Audit a deployed release

- **WHEN** an operator inspects the release's stored values after a deployment
- **THEN** the digests applied by the deployment command are present

### Requirement: Check mode

The deployment command SHALL provide a mode that resolves and reports the
images without changing the release, so a deployment can be rehearsed or
validated.

#### Scenario: Rehearse a deployment

- **WHEN** an operator runs the command in check mode
- **THEN** the resolved repository/tag/digest set is reported
- **AND** the release is not modified

#### Scenario: Check mode detects an unresolvable image

- **WHEN** check mode encounters an image whose tag cannot be resolved
- **THEN** it SHALL exit non-zero and report the failing image

### Requirement: Values layering matches `helm upgrade`

The command SHALL accept the same values layering an operator uses for
`helm upgrade` — an environment overlay plus site-specific overrides — and
resolve images from the merged, highest-precedence result.

#### Scenario: Site override wins over the overlay

- **WHEN** a site override file sets an image tag that the environment overlay also sets
- **THEN** the resolved and applied image SHALL reflect the site override
