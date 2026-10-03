## Why

The Helm chart's production overlay pins image digests by hand, and the only
helper (`scripts/pin-images.sh`) updates `docker-compose.yml` alone. After CI
pushes new images, an operator must look up each new digest and edit
`values.prod.yaml` image by image. That is slow, easy to get wrong (a stale
`digest:` silently wins over a bumped tag), and easy to leave partially done —
so a deployment can run a mix of old and new images.

Resolving the current digest at deploy time removes the hand-editing and gives
a single command that always deploys the images the tags point at now, with
Helm's release record as the audit trail.

## What Changes

- Add a deploy-time image-pinning script (`scripts/k8s-upgrade.py`)
  that resolves each configured image tag to a digest with
  `docker buildx imagetools inspect` — the same mechanism `scripts/pin-images.sh`
  already uses — and passes the resolved digests to `helm upgrade` without
  editing any committed file.
- The script targets the Helm values an operator already deploys with
  (`values.prod.yaml` / `values.dev.yaml` plus site overrides), resolves every
  `images.<name>` entry, and applies the digests via Helm `--set` or a
  temporary values file.
- Resolution and application are decoupled from CI: no workflow change, no
  write token, no bot commit. The pinned set that was actually deployed is
  recoverable from `helm -n <ns> get values <release>`.
- Add a dry-run / check mode so the resolution can be rehearsed or validated
  without deploying.
- Document the new workflow in `docs/deployment/kubernetes.md`.

## Capabilities

### New Capabilities

- `image-pinning`: resolving the images a Helm release deploys from the
  registry at deploy time, applying them to a release without editing a
  committed values file, and recovering the applied pins from the release.

### Modified Capabilities

<!-- None. docker-compose pinning is unchanged; no main spec's requirements change. -->

## Impact

- New: `scripts/k8s-upgrade.py` (deploy-time digest resolution + `helm upgrade`).
- Docs: `docs/deployment/kubernetes.md` §5 (Upgrade) gains the script-based path.
- `infra/helm/webodm/values.prod.yaml` keeps its digests as a fallback, but the
  script becomes the preferred path; the two must agree when both are used.
- No CI workflow, image, API, or database changes.
- Operators need `docker` (buildx) and `helm` on the machine that deploys.
