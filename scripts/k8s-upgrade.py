#!/usr/bin/env python3
"""Deploy-time image pinning for the WebODM Helm chart.

Resolves every image the chart would deploy (from the chart defaults, the
`-f` values files, and image `--set` overrides) to the digest the registry is
serving right now, then runs `helm upgrade` with those digests applied as the
highest-precedence values layer. No committed file is modified; the applied
digests are recoverable afterwards with `helm get values`.

  scripts/k8s-upgrade.py webodm infra/helm/webodm -n webodm \
      -f infra/helm/webodm/values.prod.yaml -f my-site.yaml

Options:
  -f, --values FILE    values file (repeatable; order matters, later wins)
  --set KEY=VALUE      image field override, e.g. images.caddy.tag=2-abc
                       (repeatable; also forwarded to helm)
  --skip NAME          leave image NAME floating (repeatable)
  --check              resolve and print only; no helm, no cluster
  --dry-run[=MODE]     pass --dry-run[=MODE] to helm
  --no-install         do not add --install to the helm upgrade
  -h, --help           show this help

Requires Python 3 with PyYAML, plus `docker` (with buildx) and, unless
`--check` is used, `helm`, on PATH.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

try:
    import yaml
except ImportError:  # pragma: no cover - exercised via subprocess in tests
    print(
        "error: PyYAML is required (install it with e.g. `pip install pyyaml`)",
        file=sys.stderr,
    )
    sys.exit(3)

EXIT_RESOLVE = 1
EXIT_PREFLIGHT = 3


class ResolutionError(Exception):
    """A repository:tag could not be resolved to a digest."""

    def __init__(self, ref: str, detail: str):
        self.ref = ref
        self.detail = detail
        super().__init__(f"{ref}: {detail}")


def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge ``overlay`` into ``base``; overlay wins on scalars."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def set_dotted(values: dict, dotted: str, value) -> None:
    """Set ``a.b.c=value`` in a nested mapping, creating mappings as needed."""
    parts = dotted.split(".")
    node = values
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def load_values(chart_dir: str, value_files: list[str], set_pairs: list[str]) -> dict:
    """Effective values: chart defaults <- -f files (in order) <- image --set."""
    defaults_path = os.path.join(chart_dir, "values.yaml")
    if not os.path.isfile(defaults_path):
        raise SystemExit(f"error: chart values file not found: {defaults_path}")
    with open(defaults_path, "r", encoding="utf-8") as handle:
        values = yaml.safe_load(handle) or {}
    if not isinstance(values, dict):
        raise SystemExit(f"error: {defaults_path} is not a YAML mapping")

    for path in value_files:
        if not os.path.isfile(path):
            raise SystemExit(f"error: values file not found: {path}")
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise SystemExit(f"error: values file is not a YAML mapping: {path}")
        deep_merge(values, data)

    for pair in set_pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key.startswith("images."):
            continue
        set_dotted(values, key, raw)
    return values


def collect_images(values: dict, skip: set[str]) -> list[tuple[str, str, str]]:
    """Return (name, repository, tag) for every complete, non-skipped image."""
    images = values.get("images") or {}
    entries: list[tuple[str, str, str]] = []
    if not isinstance(images, dict):
        return entries
    for name, entry in images.items():
        if name in skip:
            continue
        if not isinstance(entry, dict):
            print(f"warning: images.{name} is not a mapping; skipping", file=sys.stderr)
            continue
        repository = entry.get("repository")
        tag = entry.get("tag")
        if not repository or tag is None or str(tag) == "":
            print(
                f"warning: images.{name} has no repository/tag; skipping",
                file=sys.stderr,
            )
            continue
        entries.append((name, str(repository), str(tag)))
    return entries


def resolve_digest(ref: str) -> str:
    """Resolve ``repository:tag`` to its digest via docker's credential store."""
    try:
        proc = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", ref],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ResolutionError(ref, f"docker not available ({exc})") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise ResolutionError(ref, detail[-1] if detail else f"exit {proc.returncode}")
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Digest:"):
            return stripped.split(None, 1)[1].strip()
    raise ResolutionError(ref, "no digest in registry response")


def resolve_all(entries: list[tuple[str, str, str]]) -> tuple[dict[str, str], list[ResolutionError]]:
    resolved: dict[str, str] = {}
    errors: list[ResolutionError] = []
    for name, repository, tag in entries:
        ref = f"{repository}:{tag}"
        try:
            resolved[name] = resolve_digest(ref)
        except ResolutionError as exc:
            errors.append(exc)
    return resolved, errors


def pinned_values(resolved: dict[str, str]) -> dict:
    """The only content written to the generated values file: image digests."""
    return {"images": {name: {"digest": digest} for name, digest in resolved.items()}}


def write_pinned_file(resolved: dict[str, str]) -> str:
    handle, path = tempfile.mkstemp(prefix="webodm-images-", suffix=".yaml")
    os.chmod(path, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        yaml.safe_dump(pinned_values(resolved), stream, sort_keys=True)
    return path


def require_tool(tool: str) -> bool:
    if shutil.which(tool) is None:
        print(f"error: required tool not found on PATH: {tool}", file=sys.stderr)
        return False
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve chart images to digests at deploy time and run helm upgrade.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-f", "--values", action="append", default=[], metavar="FILE")
    parser.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--skip", action="append", default=[], metavar="NAME")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", nargs="?", const="", default=None)
    parser.add_argument("--no-install", action="store_true")
    parser.add_argument("release", help="helm release name")
    parser.add_argument("chart", help="path to the chart")
    args, helm_args = parser.parse_known_args(argv)
    args.helm_args = helm_args
    return args


def print_resolved(entries, resolved):
    width_name = max((len(name) for name, _, _ in entries), default=0)
    width_ref = max((len(f"{repo}:{tag}") for _, repo, tag in entries), default=0)
    for name, repository, tag in entries:
        ref = f"{repository}:{tag}"
        digest = resolved.get(name, "")
        print(f"{name:<{width_name}}  {ref:<{width_ref}}  {digest}")


def build_helm_command(args: argparse.Namespace, pinned_path: str) -> list[str]:
    command = ["helm", "upgrade"]
    if not args.no_install:
        command.append("--install")
    command += [args.release, args.chart]
    for path in args.values:
        command += ["-f", path]
    command += ["-f", pinned_path]
    for pair in args.sets:
        command += ["--set", pair]
    if args.dry_run is not None:
        command.append("--dry-run" if args.dry_run == "" else f"--dry-run={args.dry_run}")
    command += list(args.helm_args)
    return command


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if not require_tool("docker"):
        return EXIT_PREFLIGHT
    if not args.check and not require_tool("helm"):
        return EXIT_PREFLIGHT

    values = load_values(args.chart, args.values, args.sets)
    entries = collect_images(values, set(args.skip))
    if not entries:
        print("error: no resolvable images found (check the chart and values)", file=sys.stderr)
        return EXIT_RESOLVE

    resolved, errors = resolve_all(entries)
    for exc in errors:
        print(f"error: could not resolve {exc.ref}: {exc.detail}", file=sys.stderr)
    if errors:
        return EXIT_RESOLVE

    if args.check:
        print_resolved(entries, resolved)
        return 0

    pinned_path = write_pinned_file(resolved)
    try:
        command = build_helm_command(args, pinned_path)
        return subprocess.run(command).returncode
    finally:
        try:
            os.unlink(pinned_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
