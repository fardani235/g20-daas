"""Client for the plugin runner (user plugin sandbox).

The counterpart of ``geospatial.py`` for user plugins. The runner sees nothing
but the sandbox volume, so a run is *staged*: the package and copies of the
input files go into a fresh run directory there, the runner is called, and the
output is moved back into the site's private files. The run directory is
removed again whatever happens.

Site config keys (set by infra/frappe/configure_site.py in Docker):

- ``plugin_runner_url``   default ``http://127.0.0.1:5001``
- ``plugin_sandbox_dir``  default ``sites/<site>/private/plugin_sandbox``; must be
  the same path inside the worker and the runner containers.
"""

import os
import secrets
import shutil

import frappe
import requests
from frappe.utils import get_site_path

from webodm_core.plugins.files import abs_path_for_file_url

# Room for staging and transfer on top of the plugin's own wall-clock limit.
_HTTP_GRACE_SECONDS = 60


class PluginRunnerError(Exception):
    """The plugin failed (malformed package, crash, timeout, bad output)."""


class PluginRunnerUnavailable(PluginRunnerError):
    """The runner could not be reached or answered with a server error."""


def plugin_runner_url() -> str:
    return frappe.conf.get("plugin_runner_url") or "http://127.0.0.1:5001"


def sandbox_dir() -> str:
    return os.path.abspath(
        frappe.conf.get("plugin_sandbox_dir") or get_site_path("private", "plugin_sandbox")
    )


def _new_run_dir() -> str:
    """Create ``<sandbox>/runs/<random>`` writable by the runner's (other) uid.

    ``runs/`` is 0711 so one run cannot list its neighbours; the run directory
    itself is 0777 because the runner is a different, unprivileged user and
    has to create files in it. The name is unguessable.
    """
    runs = os.path.join(sandbox_dir(), "runs")
    os.makedirs(runs, exist_ok=True)
    os.chmod(runs, 0o711)
    run_dir = os.path.join(runs, secrets.token_hex(16))
    os.mkdir(run_dir)
    os.chmod(run_dir, 0o777)
    os.mkdir(os.path.join(run_dir, "inputs"))
    os.chmod(os.path.join(run_dir, "inputs"), 0o755)
    return run_dir


def _stage_inputs(run_dir: str, inputs: dict[str, str]) -> dict[str, str]:
    staged = {}
    for name, src in inputs.items():
        ext = os.path.splitext(src)[1] or ".dat"
        dest = os.path.join(run_dir, "inputs", f"{name}{ext}")
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o644)
        staged[name] = dest
    return staged


def _collect_output(src: str, dest: str):
    """Move the runner's output into place (the volumes differ, so usually a copy)."""
    try:
        os.replace(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def run_user_plugin(
    plugin,
    inputs: dict[str, str],
    params: dict,
    output_path: str,
    timeout: int = 300,
    context: dict | None = None,
) -> dict:
    """Execute ``plugin`` (a ``WebODM Plugin`` doc of type User) in the sandbox.

    ``inputs`` maps input names to absolute paths of the task's files and
    ``output_path`` is where the artifact must end up. ``context`` (task facts,
    see ``runner.run_context``) is forwarded to the plugin's ``request.json``.
    Returns the runner's result dict (``metadata`` incl. georeferencing, ``log``).
    """
    if not plugin.package:
        raise PluginRunnerError(f"plugin '{plugin.name}' has no package")

    run_dir = _new_run_dir()
    try:
        package = os.path.join(run_dir, "package.zip")
        shutil.copyfile(abs_path_for_file_url(plugin.package), package)
        os.chmod(package, 0o644)
        staged_inputs = _stage_inputs(run_dir, inputs)
        sandbox_output = os.path.join(run_dir, "output" + os.path.splitext(output_path)[1])

        url = f"{plugin_runner_url().rstrip('/')}/run"
        try:
            resp = requests.post(
                url,
                json={
                    "package_path": package,
                    "inputs": staged_inputs,
                    "params": params,
                    "output_path": sandbox_output,
                    "output_kind": plugin.output_kind,
                    "run_dir": run_dir,
                    "timeout_seconds": int(timeout),
                    "context": context or {},
                },
                timeout=int(timeout) + _HTTP_GRACE_SECONDS,
            )
            resp.raise_for_status()
            result = resp.json()
        except requests.HTTPError as e:
            detail = ""
            try:
                detail = e.response.json().get("detail", "")
            except Exception:
                detail = e.response.text if e.response is not None else ""
            if e.response is not None and e.response.status_code >= 500:
                raise PluginRunnerUnavailable(f"plugin runner error: {detail or e}") from e
            raise PluginRunnerError(f"plugin run failed: {detail or e}") from e
        except requests.RequestException as e:
            raise PluginRunnerUnavailable(f"plugin runner unreachable: {e}") from e

        if not os.path.isfile(sandbox_output):
            raise PluginRunnerError("plugin runner reported success but produced no output")
        _collect_output(sandbox_output, output_path)
        return result
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
