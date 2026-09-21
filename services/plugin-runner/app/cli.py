"""Run a plugin locally exactly the way the sandbox does, without Frappe or HTTP.

    python -m app.cli path/to/plugin-dir-or.zip \\
        --input raster=/data/dsm.tif --param threshold=120 --output /tmp/out.tif

Params are parsed as JSON when possible (``--param threshold=120`` is a number,
``--param mode=fast`` a string). ``--output-kind`` defaults to the manifest's
``output_kind``. Prints the result JSON (metadata + log tail) on success and the
plugin error on failure, with exit code 1.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

from app import sandbox


def _kv(pairs, parse_json):
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"expected name=value, got {item!r}")
        key, value = item.split("=", 1)
        if parse_json:
            try:
                value = json.loads(value)
            except ValueError:
                pass
        out[key] = value
    return out


def _package(src: str, tmp: str) -> str:
    """Return a zip path for ``src`` (zipping a plugin directory if needed)."""
    if os.path.isdir(src):
        base = os.path.join(tmp, "plugin")
        return shutil.make_archive(base, "zip", root_dir=src)
    return os.path.abspath(src)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plugin", help="plugin directory or .zip package")
    ap.add_argument("--input", action="append", metavar="NAME=PATH", help="input file (repeatable)")
    ap.add_argument("--param", action="append", metavar="KEY=VALUE", help="parameter (repeatable)")
    ap.add_argument("--output", required=True, help="where to write the plugin's output")
    ap.add_argument("--output-kind", choices=("raster", "vector"), default=None)
    ap.add_argument("--timeout", type=int, default=None, help="seconds (default: manifest / runner default)")
    args = ap.parse_args(argv)

    inputs = {k: os.path.abspath(v) for k, v in _kv(args.input, parse_json=False).items()}
    params = _kv(args.param, parse_json=True)
    output = os.path.abspath(args.output)

    tmp = tempfile.mkdtemp(prefix="plugin-run-")
    try:
        package = _package(args.plugin, tmp)
        manifest = {}
        if os.path.isdir(args.plugin):
            with open(os.path.join(args.plugin, sandbox.MANIFEST_NAME), encoding="utf-8") as f:
                manifest = json.load(f)
        run_dir = os.path.join(tmp, "run")
        os.makedirs(run_dir)
        result = sandbox.run_package(
            package, inputs, params, output, run_dir,
            output_kind=args.output_kind or manifest.get("output_kind", "raster"),
            timeout_seconds=args.timeout or manifest.get("timeout_seconds"),
        )
    except (sandbox.PluginError, sandbox.SandboxError, OSError, ValueError) as e:
        print(f"plugin failed: {e}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
