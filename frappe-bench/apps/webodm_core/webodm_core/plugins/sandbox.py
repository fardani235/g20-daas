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

import json
import os
import secrets
import shutil
import threading

import frappe
import requests
from frappe.utils import get_site_path

from webodm_core.plugins.files import abs_path_for_attached_file

# Room for staging and transfer on top of the plugin's own wall-clock limit.
_HTTP_GRACE_SECONDS = 60
# How often the progress file a plugin may write is checked while /run blocks.
PROGRESS_POLL_SECONDS = 2.0
PROGRESS_FILE = "progress.json"


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


def read_progress(run_dir: str) -> tuple[int, str] | None:
    """``(percent, message)`` from the plugin's progress file, or None if absent/unreadable."""
    try:
        with open(os.path.join(run_dir, PROGRESS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        percent = int(max(0, min(100, float(data.get("percent", 0)))))
    except (TypeError, ValueError):
        return None
    message = data.get("message")
    return percent, (str(message)[:140] if message is not None else "")


def _post_with_progress(url: str, payload: dict, timeout: int, run_dir: str, on_progress):
    """POST ``payload`` while polling the run directory's progress file.

    ``/run`` is synchronous, so the request runs in a helper thread and this
    (the Frappe) thread watches ``progress.json`` — both containers mount the
    sandbox volume — and forwards changes to ``on_progress(percent, message)``.
    Only the calling thread touches Frappe.
    """
    box = {}

    def worker():
        try:
            box["response"] = requests.post(url, json=payload, timeout=timeout)
        except Exception as e:  # re-raised in the caller's thread
            box["error"] = e

    thread = threading.Thread(target=worker, name="plugin-runner-post", daemon=True)
    thread.start()
    last = None
    while True:
        thread.join(PROGRESS_POLL_SECONDS if on_progress else None)
        if on_progress:
            current = read_progress(run_dir)
            if current and current != last:
                last = current
                try:
                    on_progress(*current)
                except Exception:
                    frappe.log_error(title="WebODM Plugin Run", message=frappe.get_traceback())
        if not thread.is_alive():
            break
    if "error" in box:
        raise box["error"]
    return box["response"]


def run_user_plugin(
    plugin,
    inputs: dict[str, str],
    params: dict,
    output_path: str,
    timeout: int = 300,
    *,
    context: dict | None = None,
    on_progress=None,
) -> dict:
    """Execute ``plugin`` (a ``WebODM Plugin`` doc of type User) in the sandbox.

    ``inputs`` maps input names to absolute paths of the task's files and
    ``output_path`` is where the artifact must end up. ``context`` is passed
    to the plugin as read-only task information; ``on_progress(percent,
    message)`` is called from this thread whenever the plugin publishes
    progress. Returns the runner's result dict (``metadata`` incl.
    georeferencing, ``log``).
    """
    if not plugin.package:
        raise PluginRunnerError(f"plugin '{plugin.name}' has no package")

    run_dir = _new_run_dir()
    try:
        package = os.path.join(run_dir, "package.zip")
        shutil.copyfile(
            abs_path_for_attached_file(plugin.package, attached_to_doctype="WebODM Plugin",
                                       attached_to_name=plugin.name),
            package,
        )
        os.chmod(package, 0o644)
        staged_inputs = _stage_inputs(run_dir, inputs)
        sandbox_output = os.path.join(run_dir, "output" + os.path.splitext(output_path)[1])

        url = f"{plugin_runner_url().rstrip('/')}/run"
        try:
            resp = _post_with_progress(
                url,
                {
                    "package_path": package,
                    "inputs": staged_inputs,
                    "params": params,
                    "context": context or {},
                    "output_path": sandbox_output,
                    "output_kind": plugin.output_kind,
                    "run_dir": run_dir,
                    "timeout_seconds": int(timeout),
                },
                int(timeout) + _HTTP_GRACE_SECONDS,
                run_dir,
                on_progress,
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
