## 1. Image resolution core

- [x] 1.1 Create `scripts/k8s-upgrade.py` (Python 3, `#!/usr/bin/env python3`) that loads the effective `images` map and resolves each `repository` + `tag` to a digest via `docker buildx imagetools inspect`; verify by running `scripts/k8s-upgrade.py --check -f infra/helm/webodm/values.dev.yaml` and seeing every image with its resolved digest
- [x] 1.2 Abort with a non-zero exit and the image name when a tag cannot be resolved; verify with a unit test that stubs resolution to fail for one image and asserts the failure names it
- [x] 1.3 Fail with a clear message when PyYAML or `docker`/`helm` is unavailable, before any registry query; verify by running with a stripped `PATH` and confirming the preflight message

## 2. Values layering

- [x] 2.1 Deep-merge the `-f` values files in the order given (later wins), plus `--set` entries for image fields, to produce the effective `images` map; verify with two fixture values files where the later overrides an image tag and assert the resolved tag is the higher-precedence one
- [x] 2.2 Support `--skip <name>` to leave a named image floating; verify the skipped image is absent from `--check` output
- [x] 2.3 Skip (do not crash on) an `images` entry missing `repository` or `tag`, reporting it; verify with a fixture containing an incomplete entry

## 3. Applying digests

- [x] 3.1 Generate a mode-600 temporary values file containing only `images.<name>.digest`, pass it last on the `helm upgrade` argument list, and remove it on exit via a `trap`; verify by inspecting a `--dry-run` that the rendered image references are `repository:tag@digest` with the freshly resolved digest
- [x] 3.2 Confirm a committed stale `digest` in an overlay is overridden by the resolved one; verify by setting a deliberately wrong digest in a fixture overlay and asserting the dry-run output carries the resolved digest
- [x] 3.3 Pass through all other operator arguments (release, namespace, chart, extra values) and confirm no tracked file is modified after a run; verify with `git status --porcelain` empty and a dry-run showing unrelated values unchanged

## 4. Modes

- [x] 4.1 Implement `--check`: resolve, print `repository`, `tag`, and `digest` per image, exit non-zero on any resolution failure, and do not contact the cluster; verify against a values file and a stubbed failure case
- [x] 4.2 Implement `--dry-run`: pass `helm upgrade --dry-run` so rendered manifests can be reviewed with resolved digests; verify the command emits manifests and makes no cluster change
- [x] 4.3 Print the resolved digest set in a form that matches `helm -n <ns> get values <release>`; verify the digests from a `--dry-run` appear in the release's stored values after an actual deploy (integration check on a scratch release)

## 5. Tests and CI

- [x] 5.1 Add `scripts/tests/test_k8s_upgrade.py` covering resolution, precedence, stale-digest override, skip, and failure paths with resolution mocked; verify with `python -m pytest scripts/tests -q`
- [x] 5.2 Extend the `helm-chart` job in `.github/workflows/build-and-push.yml` to install PyYAML and run the script tests; verify the job passes on a PR
- [x] 5.3 Confirm the existing `python-syntax` job byte-compiles `scripts/k8s-upgrade.py` (tracked `*.py`); verify by grepping the job output for the file or running `python -m py_compile scripts/k8s-upgrade.py`

## 6. Documentation

- [x] 6.1 Update `docs/deployment/kubernetes.md` §5 (Upgrade) to lead with the script-based workflow (`scripts/k8s-upgrade.py -f ...`), document `--check`/`--dry-run`/`--skip`, and keep the committed-digest edit path as the fallback
- [x] 6.2 Note the new audit path (`helm get values`) and the Python 3 + PyYAML + docker + helm prerequisites in the same section; verify the commands in the text run as written
