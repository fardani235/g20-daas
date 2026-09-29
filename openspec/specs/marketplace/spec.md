# Marketplace Specification

## Purpose

A global, free catalog where first-party and invited-partner publishers list
digital products — analysis plugins first, other artifact kinds later — and
organization admins install them into their organization in one click.
Browsing is global (including signed-out visitors); installation,
configuration and execution stay organization-scoped and go through the
`user-plugins` install path unchanged.

## Requirements

### Requirement: Listing layer separate from installed rows

The marketplace SHALL keep products, releases and publishers in global
records that carry no organization. The organization-scoped `WebODM Plugin`
row SHALL remain the representation of an *installed* product and SHALL
record its provenance (`source`, `product`, `release`).

#### Scenario: Catalog visible without an organization

- **WHEN** a signed-out visitor or a member of any organization lists
  products
- **THEN** every Published product of an Active publisher is returned, and
  no per-organization data (installed rows, settings, runs) is included

#### Scenario: Installed row is org-scoped

- **WHEN** an organization installs a product
- **THEN** the resulting `WebODM Plugin` row is named `<org-slug>.<id>`,
  belongs to that organization, and is invisible to every other organization

### Requirement: Publishers are curated

A publisher SHALL be `First-party` or `Partner`, created and managed by
platform admins only. There SHALL be no sign-up or self-serve onboarding. A
`Suspended` publisher's products MUST NOT be listed or installable.

#### Scenario: Non-admin cannot create a publisher

- **WHEN** an organization admin attempts to create or edit a publisher,
  product or release
- **THEN** the write is rejected

#### Scenario: Suspended publisher

- **WHEN** a publisher is set to Suspended
- **THEN** its Published products disappear from listings and an install
  attempt is rejected

### Requirement: Versioned, immutable releases

A product SHALL have releases identified by version. A release SHALL carry a
private artifact with its sha-256 and size, release notes and license
metadata. For the `plugin` kind the artifact MUST pass the user-plugin
package validation, its manifest version MUST equal the release version, and
its manifest id MUST match the product's other releases. Once a release has
been Published, its artifact, hash, manifest and license MUST NOT change; it
MAY be Yanked, which stops new installs without affecting existing ones.

#### Scenario: Publishing a valid plugin release

- **WHEN** a platform admin publishes a release whose zip passes validation
  and whose manifest version matches
- **THEN** the release stores the normalized manifest, hash and size, and
  `published_on` is set

#### Scenario: Artifact change after publish is rejected

- **WHEN** an admin replaces the artifact of a Published release
- **THEN** the save is rejected

#### Scenario: Yanked release

- **WHEN** a release is Yanked
- **THEN** it is shown as such on the product page, cannot be installed, and
  organizations that installed it keep their plugin row and runs

### Requirement: Mandatory license metadata

Every release SHALL link a license from the platform's license registry and
MAY add free-text license notes (e.g. model-weight terms). The product page
SHALL show the license and notes of every version.

#### Scenario: Release without a license

- **WHEN** a release is saved with no license
- **THEN** the save is rejected

### Requirement: Publisher-controlled anonymous download

A product SHALL have an `allow_anonymous_download` switch, off by default. It
MAY be enabled only when every Published release's license permits
redistribution, and a release whose license does not permit redistribution
MUST NOT be published under a product with the switch on.

#### Scenario: Enabling with a restrictive license

- **WHEN** an admin enables anonymous download on a product whose published
  release is licensed `Proprietary` (no redistribution)
- **THEN** the save is rejected with the license named

#### Scenario: Guest download

- **WHEN** a signed-out visitor requests a release artifact
- **THEN** it is streamed only if the product allows anonymous download and
  the release is Published; otherwise the request is denied

#### Scenario: Signed-in download

- **WHEN** a signed-in user requests a Published release artifact
- **THEN** it is streamed regardless of the anonymous switch

### Requirement: Artifact kinds

A product SHALL declare an artifact kind from a fixed registry (`plugin`,
`preset`, `basemap`, `model`). Only kinds with an installer MAY be Published.
Adding a kind SHALL require an inspector and an installer, not new tables.

#### Scenario: Unsupported kind cannot be published

- **WHEN** an admin sets a `basemap` product to Published
- **THEN** the save is rejected as not yet installable

### Requirement: Entitlement and one-click install

An organization admin SHALL be able to install a Published release of a
Published product into their own organization. Installation MUST reuse the
user-plugin install entrypoint, verify the copied artifact's sha-256 against
the release, and record an Active entitlement (organization, product,
release, installed row). Installing a newer release upgrades in place,
keeping settings and run history. Uninstalling MUST reuse the user-plugin
removal path and mark the entitlement Removed. Removing the plugin from the
Plugins page or uploading a package over the same id MUST also detach the
entitlement.

#### Scenario: Admin installs

- **WHEN** an organization admin installs a product
- **THEN** a `WebODM Plugin` row `<org-slug>.<id>` with `source =
  Marketplace` exists, is enabled, and an Active entitlement points at it

#### Scenario: Member cannot install

- **WHEN** a non-admin member or a user without an organization attempts to
  install
- **THEN** the request is rejected and nothing is created

#### Scenario: Upgrade keeps settings

- **WHEN** an organization that installed 1.0.0 with saved settings installs
  1.1.0
- **THEN** the same plugin row now carries 1.1.0, its settings and runs are
  intact, and the entitlement points at the new release

#### Scenario: Uninstall

- **WHEN** an admin uninstalls
- **THEN** the plugin row, its settings and runs are gone and the entitlement
  is Removed

### Requirement: Marketplace UI

The frontend SHALL offer `/marketplace` (browse, search, filter by category
and kind) and `/marketplace/:product` (description, versions, license, docs,
media, install panel), usable signed-out with public chrome and signed-in
with the application chrome and a Marketplace tab. Signed-out visitors SHALL
see a Download button only when the publisher allows it, and otherwise an
invitation to sign in and install. The public landing page SHALL link to the
marketplace.

#### Scenario: Signed-out visitor, download not allowed

- **WHEN** a visitor opens a product whose anonymous download is off
- **THEN** the page shows a sign-in prompt and no Download button

#### Scenario: Signed-in admin

- **WHEN** an organization admin opens a product
- **THEN** the panel offers Install (or Update / Installed / Uninstall
  according to the entitlement)
