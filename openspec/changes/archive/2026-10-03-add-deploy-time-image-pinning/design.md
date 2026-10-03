## Context

See `proposal.md` for motivation. The relevant current state:

- `infra/helm/webodm/values.prod.yaml` pins every image as
  `repository` + `tag` + `digest`; the chart helper `webodm.image`
  (`templates/_helpers.tpl`) renders `repository:tag@digest`.
- `scripts/pin-images.sh` already resolves a tag to a digest with
  `docker buildx imagetools inspect <repo:tag>` for `docker-compose.yml` — that
  command is the proven resolution mechanism and needs no registry credential
  handling of its own (it uses the docker credential store).
- CI pushes mutable tags (`1`, `2`, `<frappe-version>`, `latest`) plus
  immutable `-<sha>` tags. Operators deploy with
  `helm upgrade ... -f values.prod.yaml -f <site>.yaml` and there is no
  GitOps controller in the loop.

The constraint that shapes the whole design: a digest in an image reference
wins over the tag, so a stale `digest:` in the effective values masks a bumped
tag. Whatever applies resolved digests must be the highest-precedence values
layer.

## Goals / Non-Goals

**Goals:**

- One command that resolves every image the chart would deploy and applies the
  digests to a release, with no committed-file edit.
- Resolution reflects the same values layering as the `helm upgrade` it wraps.
- A check mode that reports the resolved set and fails on an unresolvable image.
- The applied digests are readable afterwards from the Helm release.

**Non-Goals:**

- No CI workflow change, no bot commit, no write token (that was option 2 in
  the exploration and is explicitly deferred).
- Not a GitOps controllers, ArgoCD/Flux, or an OCI-chart publisher.
- Does not manage non-image values, secrets, or the database.
- Does not make a rolling deployment transactional; `helm rollback` semantics
  are unchanged.

## Decisions

### Resolution mechanism: `docker buildx imagetools inspect`

Reuse exactly what `pin-images.sh` does. It resolves remote manifests without
pulling layers, works across registries, and inherits docker's credential
store, which satisfies the private-registry requirement for free.

Alternatives: `skopeo inspect` (extra binary), `crane` (extra binary), talking
to the registry API directly (credential handling and token dance we would
otherwise avoid). All rejected as extra dependencies for no benefit.

### Reading the images to resolve: merge the operator's own `-f` files

The command computes the effective values the way Helm does: start from the
chart's own `values.yaml` (which is where `repository` lives — an overlay such
as `values.prod.yaml` sets only `tag`/`digest`), then deep-merge the operator's
`-f` files in order (later files win), then apply `--set` image fields. It
resolves each `images.<name>` entry from that merged map (all of them,
including community images such as `postgres`/`redis`/`nodeodm`, so a
deployment is fully reproducible), with a `--skip <name>` escape hatch for
entries an operator wants left floating.

Alternatives considered:

- **`helm template` then parse rendered `image:` lines.** This is the literal
  truth of what Kubernetes receives, but mapping a rendered reference back to
  the `images.<name>` values key (needed to override the digest) is ambiguous
  because several workloads share one image. Rejected.
- **Static list of the chart's image keys.** Repositories and tags can be
  overridden in site values, so the keys alone are not enough; the effective
  repository/tag still has to be read from the merged values. The merged map is
  the minimal input that covers both.

### Language: Python 3 with PyYAML (`scripts/k8s-upgrade.py`)

The values shape is nested YAML, and deep-merge plus precedence handling is
error-prone in shell. Python 3 is present wherever `helm` and `docker` are, and
`yaml` is the one added dependency; the script checks for it and fails with a
clear message if missing. This is a deliberate departure from the bash in
`pin-images.sh`, driven by the data shape.

### Applying digests: a generated, highest-precedence values file

The command writes a temporary values file containing only
`images.<name>.digest` for the resolved set, passes it last on the `helm`
argument list (so it overrides any committed digest), and removes it on exit
via a trap. This centres precedence in one place and avoids a long, quoting-
sensitive `--set` chain.

Alternatives: `--set images.<name>.digest=...` per image (long, quoting-prone,
but no temp file), `--set-json` with one JSON blob (compact but awkward to read
and easy to get wrong). The temp file is safer and lets `--check` print exactly
what would be applied. The file holds digests only, never secrets, and is
created mode `600`.

### Check mode and dry-run

`--check` performs resolution and prints the `repository`, `tag`, and resolved
`digest` per image, exits non-zero if any image fails to resolve, and touches
neither the release nor disk beyond the registry queries. A separate
`--dry-run` passes `helm upgrade --dry-run` so the rendered manifests can be
reviewed with the resolved digests in place.

### Automatic migration on a Frappe image change

No special handling: `webodm.bootstrap.jobName` hashes the rendered Frappe image
reference, so a changed digest produces a new `frappe-init-<hash>` Job, which
runs `migrate`, and the new Frappe pods wait for it. This is existing chart
behaviour and a reason to pin the Frappe digest through the same command.

## Risks / Trade-offs

- **[Resolution is a network call; the registry can be down or rate-limit.]**
  → Abort before touching the release, name the failing image; never deploy a
  partial set. A transient failure is safe to retry by re-running.
- **[Mutable tags mean two operators can resolve different builds.]** → That is
  the intended deploy-time semantics; the applied digests in the release record
  are the contract, and `--check` makes the choice visible before applying.
- **[Pinning `nodeodm:latest` by digest makes the build reproducible but
  possibly older than a fresh pull.]** → Expected; `--skip nodeodm` leaves it
  floating if an operator prefers the node to track upstream.
- **[PyYAML dependency.]** → Explicit preflight check with a clear error; it is
  the only added requirement and is widely available.
- **[A generated values file could be left behind on a hard kill.]** → `trap`
  cleanup plus a `600` mode and digests-only content keep any residue harmless.

## Migration Plan

1. Land the script and the docs update; no cluster or image change.
2. Operators switch from hand-editing `values.prod.yaml` digests to running the
   command; the committed digests remain a working fallback and should still be
   kept roughly current.
3. Rollback of a bad deploy is unchanged: `helm rollback`, or redeploy with the
   previously recorded digests (`helm get values`).
