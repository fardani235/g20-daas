"""On-demand compute: the app side of ephemeral NodeODM instances.

The app never talks to a cloud. It talks to the **provisioner** service
(``services/provisioner``) over a small internal HTTP API, the same way it
talks to the geospatial and plugin-runner services, and keeps the lifecycle in
``WebODM Compute Instance`` records. The provisioner holds no state: it hands
back a handle and forgets. Which provider produced a node (AWS today, vast.ai
next) is invisible here — adding a provider is a provisioner + config change.

Lifecycle, one record per task run::

    Requested --POST /instances--> Provisioning --NodeODM answers--> Ready
        |                              |                               |
        +------ error / timeout -------+-------> Failed                +--> Terminating --> Terminated

* ``request_for_task`` creates the record and asks for an instance (honouring
  the global / per-org concurrency caps).
* ``check_provisioning`` (called by the every-minute sweep for tasks in the
  ``Provisioning`` state) polls the provisioner and either flips the instance
  to Ready and re-dispatches the task, or fails it on timeout/error so the
  task's normal dispatch backoff takes over.
* ``release`` destroys the instance once the task is terminal. Best-effort:
  a failed destroy is retried by the sweep and never fails the task.
* ``reap`` is the safety net against a runaway bill: it destroys instances
  whose task is done or gone, that outlived their budget, that never came up,
  whose destroy failed earlier, and provider-side orphans nothing records.

Config (site config, from ``WEBODM_COMPUTE_*`` env via configure_site.py):
``provisioner_url``, ``compute_max_instances``, ``compute_max_instances_per_org``,
``compute_provision_timeout_seconds``, ``compute_max_lifetime_seconds``,
``compute_orphan_grace_seconds``, ``compute_large_task_images``.
Credentials live in the provisioner only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

import frappe
import requests
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

DEFAULT_MAX_INSTANCES = 5
DEFAULT_MAX_INSTANCES_PER_ORG = 2
DEFAULT_PROVISION_TIMEOUT = 15 * 60
DEFAULT_MAX_LIFETIME = 12 * 3600
DEFAULT_ORPHAN_GRACE = 10 * 60
DEFAULT_LARGE_TASK_IMAGES = 500
MAX_DESTROY_ATTEMPTS_BEFORE_ALERT = 5

LIVE_STATUSES = ("Requested", "Provisioning", "Ready", "Terminating")
COUNTED_STATUSES = ("Requested", "Provisioning", "Ready")  # what the caps count
TERMINAL_TASK_STATUSES = ("Completed", "Failed", "Cancelled")

PROVISION_CHECK_JOB = "webodm_core.webodm_core.processing.compute.check_provisioning"


class ProvisionerError(Exception):
    """The provisioner answered with an error (bad class, provider rejected...)."""


class ProvisionerUnavailable(ProvisionerError):
    """The provisioner could not be reached. Callers fall back or retry."""


class CapacityExceeded(Exception):
    """A concurrency cap is reached; the task should wait, not fail."""


# -- node tokens ----------------------------------------------------------------

NODE_TOKEN_SECRET_ENV = "WEBODM_NODE_TOKEN_SECRET"


def node_token(instance_name: str) -> str:
    """The per-run NodeODM bearer token for ``instance_name`` — derived, never stored.

    ``HMAC-SHA256(secret, "webodm-node-token:<site>:<instance name>")``, URL-safe
    base64 (43 chars). The secret comes from the process environment only
    (``WEBODM_NODE_TOKEN_SECRET``, a Docker secret); the database holds just
    the instance name, so a database read — even together with site config —
    yields no node credential. Anything that needs to talk to a node (dispatch,
    poll, cancel, console, the readiness probe) recomputes the token on the
    spot. Rotating the secret invalidates the tokens of live nodes: do it when
    no instance is Ready (runbook §8).
    """
    secret = os.environ.get(NODE_TOKEN_SECRET_ENV, "")
    if len(secret) < 32:
        raise ProvisionerError(
            f"{NODE_TOKEN_SECRET_ENV} is not set (or shorter than 32 characters); "
            "on-demand nodes cannot be provisioned without it"
        )
    msg = f"webodm-node-token:{frappe.local.site}:{instance_name}".encode()
    digest = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


# -- config -------------------------------------------------------------------


def provisioner_url() -> str | None:
    return (frappe.conf.get("provisioner_url") or "").rstrip("/") or None


def enabled() -> bool:
    """A provisioner URL is configured. Whether it has a provider is asked live."""
    return bool(provisioner_url())


def _conf_int(key: str, default: int) -> int:
    return cint(frappe.conf.get(key) or 0) or default


def max_instances() -> int:
    return _conf_int("compute_max_instances", DEFAULT_MAX_INSTANCES)


def max_instances_per_org() -> int:
    return _conf_int("compute_max_instances_per_org", DEFAULT_MAX_INSTANCES_PER_ORG)


def provision_timeout() -> int:
    return _conf_int("compute_provision_timeout_seconds", DEFAULT_PROVISION_TIMEOUT)


def max_lifetime() -> int:
    return _conf_int("compute_max_lifetime_seconds", DEFAULT_MAX_LIFETIME)


def orphan_grace() -> int:
    return _conf_int("compute_orphan_grace_seconds", DEFAULT_ORPHAN_GRACE)


# -- provisioner client -------------------------------------------------------


class ProvisionerClient:
    """HTTP client for the provisioner's internal API. No provider knowledge."""

    def __init__(self, base_url: str | None = None, timeout: int = 30):
        self.base_url = (base_url or provisioner_url() or "").rstrip("/")
        self.timeout = timeout
        if not self.base_url:
            raise ProvisionerUnavailable("provisioner_url is not configured")

    def _request(self, method: str, path: str, **kw):
        headers = kw.pop("headers", {}) or {}
        # Shared secret for the provisioner API: environment only, never site
        # config, the database or logs.
        token = os.environ.get("PROVISIONER_API_TOKEN")
        if token:
headers["Authorization"] = f"Bearer {token}"
        try:
            resp = requests.request(
                method, f"{self.base_url}{path}", timeout=kw.pop("timeout", self.timeout),
                headers=headers, **kw,
            )
        except requests.RequestException as e:
            raise ProvisionerUnavailable(f"provisioner unreachable: {e.__class__.__name__}") from None
        if resp.status_code >= 500:
            raise ProvisionerUnavailable(f"provisioner error {resp.status_code}")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text[:200]
            raise ProvisionerError(f"provisioner rejected {method} {path}: {detail or resp.status_code}")
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            raise ProvisionerError(f"provisioner returned a non-JSON body for {method} {path}") from None

    def provider(self) -> dict:
        """``{"enabled", "provider", "classes": {name: {...}}, "default_class", ...}``."""
        return self._request("GET", "/provider")

    def create(self, instance_class: str, token: str, labels: dict, max_lifetime_seconds: int) -> dict:
        return self._request(
            "POST", "/instances",
            json={
                "instance_class": instance_class,
                "token": token,
                "labels": labels,
                "max_lifetime_seconds": int(max_lifetime_seconds),
            },
            timeout=120,
        )

    def describe(self, handle: str, token: str | None = None) -> dict:
        headers = {"X-Node-Token": token} if token else {}
        return self._request("GET", f"/instances/{handle}", headers=headers)

    def destroy(self, handle: str) -> dict:
        return self._request("DELETE", f"/instances/{handle}", timeout=60)

    def list_managed(self) -> list[dict]:
        data = self._request("GET", "/instances")
        return data.get("instances", []) if isinstance(data, dict) else list(data or [])


# -- instance class -----------------------------------------------------------


def instance_class_for(options, image_count: int = 0, available: dict | None = None,
                       default: str = "cpu") -> str:
    """Pick the instance class for a task from its processing options.

    The rule is deliberately simple and provider-free: ``cpu`` unless the task
    is heavy (ultra feature/point-cloud quality, or more images than
    ``compute_large_task_images``), which asks for ``cpu-large``. A class the
    provisioner does not offer degrades to the provider's default. Users never
    pick a provider; the GPU provider will reuse this rule later.
    """
    opts = _options_map(options)
    heavy = False
    for key in ("feature-quality", "pc-quality"):
        if str(opts.get(key, "")).lower() == "ultra":
            heavy = True
    if image_count and image_count > _conf_int("compute_large_task_images", DEFAULT_LARGE_TASK_IMAGES):
        heavy = True
    wanted = "cpu-large" if heavy else "cpu"
    if available is not None and wanted not in available:
        return default if default in available else (next(iter(available), default) if available else default)
    return wanted


def _options_map(options) -> dict:
    if isinstance(options, str):
        try:
            options = frappe.parse_json(options)
        except Exception:
            return {}
    if isinstance(options, str):
        try:
            options = frappe.parse_json(options)
        except Exception:
            return {}
    if isinstance(options, list):
        return {o.get("name"): o.get("value") for o in options if isinstance(o, dict) and "name" in o}
    return dict(options) if isinstance(options, dict) else {}


# -- caps ---------------------------------------------------------------------


def live_instance_counts(org: str | None) -> tuple[int, int]:
    """``(global, org)`` counts of instances that hold or will hold a machine."""
    total = frappe.db.count("WebODM Compute Instance", {"status": ["in", list(COUNTED_STATUSES)]})
    per_org = 0
    if org:
        per_org = frappe.db.count(
            "WebODM Compute Instance", {"status": ["in", list(COUNTED_STATUSES)], "organization": org}
        )
    return total, per_org


def check_capacity(org: str | None):
    total, per_org = live_instance_counts(org)
    if total >= max_instances():
        raise CapacityExceeded(f"global compute cap reached ({total}/{max_instances()})")
    if org and per_org >= max_instances_per_org():
        raise CapacityExceeded(f"organization compute cap reached ({per_org}/{max_instances_per_org()})")


# -- lifecycle ----------------------------------------------------------------


def ready_instance_for(task):
    """The task's compute instance if it is Ready with an endpoint, else None."""
    name = getattr(task, "compute_instance", None)
    if not name:
        return None
    inst = frappe.db.get_value(
        "WebODM Compute Instance", name,
        ["name", "status", "hostname", "port", "provider"], as_dict=True,
    )
    if not inst or inst.status != "Ready" or not inst.hostname:
        return None
    return inst


def node_for_instance(instance_name: str) -> dict | None:
    """A node dict (hostname/port/token) for ``NodeODMClient`` from an instance record."""
    inst = frappe.db.get_value(
        "WebODM Compute Instance", instance_name,
        ["name", "status", "hostname", "port"], as_dict=True,
    )
    if not inst or not inst.hostname or inst.status not in ("Ready", "Terminating"):
        return None
    return {"name": inst.name, "hostname": inst.hostname, "port": inst.port,
            "token": node_token(inst.name), "compute_instance": inst.name}


def request_for_task(task) -> str:
    """Ask the provisioner for an instance for ``task``; return the record name.

    Raises ``CapacityExceeded`` (wait), ``ProvisionerUnavailable`` (fall back
    to a static node or retry) or ``ProvisionerError`` (no provider / class
    rejected: fall back). On success the task is *not* updated here — the
    caller flips it to ``Provisioning`` — but the instance record exists in
    ``Provisioning`` with its handle.
    """
    check_capacity(task.organization)
    client = ProvisionerClient()
    info = client.provider()
    if not info.get("enabled"):
        raise ProvisionerError("no compute provider configured")
    classes = info.get("classes") or {}
    default_class = info.get("default_class") or (next(iter(classes)) if classes else "cpu")
    instance_class = instance_class_for(
        task.processing_options, len(task.images or []), available=classes or None, default=default_class,
    )
    lifetime = min(max_lifetime(), cint(info.get("max_lifetime_seconds") or 0) or max_lifetime())
    # Fail before creating anything if the token secret is missing: the node
    # would boot with a token nobody can reproduce.
    node_token("preflight")

    inst = frappe.get_doc({
        "doctype": "WebODM Compute Instance",
        "task": task.name,
        "organization": task.organization,
        "status": "Requested",
        "instance_class": instance_class,
        "requested_at": now_datetime(),
        "max_lifetime_seconds": lifetime,
        "expires_at": add_to_date(now_datetime(), seconds=lifetime),
        "provider": info.get("provider"),
    })
    inst.insert(ignore_permissions=True)
    frappe.db.commit()  # the record must survive even if the create call below dies

    # The token is a function of the record's name; it is baked into the node
    # at boot and never written anywhere on our side.
    token = node_token(inst.name)
    try:
        created = client.create(
            instance_class, token,
            labels={"task": task.name, "org": task.organization or "", "site": frappe.local.site},
            max_lifetime_seconds=lifetime,
        )
    except ProvisionerError as e:
        inst.db_set({"status": "Failed", "last_error": str(e)[:1000], "terminated_at": now_datetime()})
        raise

    inst.db_set({
        "status": "Provisioning",
        "handle": created.get("handle"),
        "provider": created.get("provider") or info.get("provider"),
        "instance_class": created.get("instance_class") or instance_class,
        "estimated_hourly_cost": float(created.get("hourly_cost") or 0),
    })
    return inst.name


def enqueue_provision_check(task_name: str):
    frappe.enqueue(
        PROVISION_CHECK_JOB, queue="short", job_id=f"webodm:provision:{task_name}",
        deduplicate=True, task_name=task_name,
    )


def update_provisioning_tasks():
    """Scheduler: poll every task waiting for a node."""
    for name in frappe.get_all("WebODM Task", filters={"status": "Provisioning"}, pluck="name"):
        enqueue_provision_check(name)


def check_provisioning(task_name: str):
    """Advance one Provisioning task: Ready -> dispatch; failed/timed out -> backoff."""
    from webodm_core.webodm_core.processing import task_runner

    task = frappe.get_doc("WebODM Task", task_name)
    if task.status != "Provisioning":
        return
    if not task.compute_instance or not frappe.db.exists("WebODM Compute Instance", task.compute_instance):
        _requeue(task, "compute instance record missing")
        return
    inst = frappe.get_doc("WebODM Compute Instance", task.compute_instance)

    if inst.status == "Ready":
        task_runner.enqueue_process(task.name)
        return
    if inst.status in ("Failed", "Terminated", "Terminating"):
        _requeue(task, f"compute instance {inst.status.lower()}: {inst.last_error or 'no detail'}")
        return

    # Budget first: a node that never comes up must not run the meter forever.
    started = get_datetime(inst.requested_at or inst.creation)
    if started and (now_datetime() - started).total_seconds() > provision_timeout():
        _mark_failed(inst, f"provisioning timed out after {provision_timeout()}s")
        destroy_instance(inst)
        _requeue(task, "provisioning timed out")
        return

    if not inst.handle:
        # Requested but the create call never came back: nothing to describe.
        return

    try:
        state = ProvisionerClient().describe(inst.handle, token=node_token(inst.name))
    except ProvisionerUnavailable as e:
        inst.db_set({"last_error": str(e)[:1000]})
        return  # try again next sweep; the timeout above bounds this
    except ProvisionerError as e:
        _mark_failed(inst, str(e))
        destroy_instance(inst)
        _requeue(task, f"provisioner error: {e}")
        return

    status = (state.get("status") or "").lower()
    if status == "ready":
        inst.db_set({
            "status": "Ready",
            "hostname": state.get("hostname"),
            "port": cint(state.get("port") or 3000),
            "ready_at": now_datetime(),
            "last_error": None,
        })
        task_runner.enqueue_process(task.name)
    elif status in ("failed", "terminated"):
        _mark_failed(inst, f"instance {status}: {state.get('detail') or 'no detail'}")
        destroy_instance(inst)
        _requeue(task, f"compute instance {status}")
    # anything else ("pending", "booting"...): still coming up, check again next sweep


def _requeue(task, reason: str):
    """Send a Provisioning task back to Queued through the normal dispatch backoff."""
    from webodm_core.webodm_core.processing import task_runner

    task.db_set({"status": "Queued", "compute_instance": None})
    task_runner._defer_dispatch(task, f"provisioning failed: {reason}")


def _mark_failed(inst, error: str):
    inst.db_set({"status": "Failed", "last_error": error[:1000]})


def destroy_instance(inst) -> bool:
    """Ask the provisioner to destroy ``inst``. Returns True when confirmed.

    Never raises: a failed destroy leaves the record in ``Terminating`` for the
    sweep to retry (whatever state it was in), with the error recorded. An
    instance without a handle never existed at the provider and is closed out
    directly. A record that was ``Failed`` (timeout, provider error) closes as
    ``Failed`` so the reason stays visible; everything else closes as
    ``Terminated``.
    """
    if inst.status == "Terminated" or (inst.status == "Failed" and inst.get("terminated_at")):
        return True
    final = "Failed" if inst.status == "Failed" else "Terminated"
    if not inst.handle:
        _close(inst, final)
        return True
    try:
        ProvisionerClient().destroy(inst.handle)
    except ProvisionerError as e:
        attempts = cint(inst.destroy_attempts) + 1
        inst.db_set({
            "status": "Terminating",
            "destroy_attempts": attempts,
            "last_error": f"destroy failed: {e}"[:1000],
        })
        title = "WebODM Compute" if attempts < MAX_DESTROY_ATTEMPTS_BEFORE_ALERT else "WebODM Compute ALERT"
        frappe.log_error(f"{inst.name} ({inst.handle}): destroy attempt {attempts} failed: {e}", title)
        return False
    _close(inst, final)
    return True


def _close(inst, status: str):
    ended = now_datetime()
    started = get_datetime(inst.ready_at or inst.requested_at or inst.creation)
    hours = max((ended - started).total_seconds(), 0) / 3600 if started else 0
    inst.db_set({
        "status": status,
        "terminated_at": ended,
        "estimated_cost": round(float(inst.estimated_hourly_cost or 0) * hours, 4),
    })


def release_for_task(task) -> None:
    """Give the task's instance back. Best-effort; never raises, never blocks the task."""
    name = getattr(task, "compute_instance", None)
    if not name:
        return
    try:
        if not frappe.db.exists("WebODM Compute Instance", name):
            return
        inst = frappe.get_doc("WebODM Compute Instance", name)
        if inst.status in ("Terminated",):
            return
        destroy_instance(inst)
    except Exception as e:
        frappe.log_error(f"{task.name}: releasing compute failed: {e}", "WebODM Compute")


# -- sweep ------------------------------------------------------------------


def reap() -> dict:
    """Destroy every instance that should not be running. Safety net; idempotent.

    Rules, in order: task gone or terminal; task moved to another instance;
    lifetime budget exceeded (fails the task too); never came up within the
    provisioning timeout (task requeued); destroy retry for Terminating rows;
    provider-side orphans that no live record explains and that are older
    than the orphan grace period.
    """
    from webodm_core.webodm_core.processing import task_runner

    stats = {"checked": 0, "destroyed": 0, "orphans": 0, "errors": 0}
    now = now_datetime()
    rows = frappe.get_all(
        "WebODM Compute Instance", filters={"status": ["in", list(LIVE_STATUSES)]}, pluck="name",
    )
    # Failed records whose destroy never got confirmed (process died between
    # marking and destroying) must not leak a machine either.
    rows += frappe.get_all(
        "WebODM Compute Instance",
        filters={"status": "Failed", "terminated_at": ["is", "not set"], "handle": ["!=", ""]},
        pluck="name",
    )
    for name in dict.fromkeys(rows):
        stats["checked"] += 1
        try:
            inst = frappe.get_doc("WebODM Compute Instance", name)
            task = frappe.get_doc("WebODM Task", inst.task) if frappe.db.exists("WebODM Task", inst.task) else None

            if inst.status == "Terminating":
                stats["destroyed"] += int(destroy_instance(inst))
                continue
            if task is None or task.status in TERMINAL_TASK_STATUSES or getattr(task, "compute_instance", None) != inst.name:
                stats["destroyed"] += int(destroy_instance(inst))
                continue
            if inst.expires_at and now >= get_datetime(inst.expires_at):
                _mark_failed(inst, "max lifetime exceeded")
                destroy_instance(inst)
                stats["destroyed"] += 1
                if task.status in ("Running", "Provisioning", "Queued"):
                    task_runner._fail(task, f"compute instance exceeded its {inst.max_lifetime_seconds}s lifetime budget")
                continue
            if inst.status in ("Requested", "Provisioning"):
                started = get_datetime(inst.requested_at or inst.creation)
                if started and (now - started).total_seconds() > provision_timeout():
                    _mark_failed(inst, f"provisioning timed out after {provision_timeout()}s")
                    destroy_instance(inst)
                    stats["destroyed"] += 1
                    if task.status == "Provisioning":
                        _requeue(task, "provisioning timed out")
        except Exception as e:
            stats["errors"] += 1
            frappe.log_error(f"reaper: {name}: {e}", "WebODM Compute")

    stats["orphans"] = _reap_orphans(now)
    return stats


def _reap_orphans(now) -> int:
    """Destroy provider-side instances that no live record explains."""
    if not enabled():
        return 0
    try:
        managed = ProvisionerClient().list_managed()
    except ProvisionerError as e:
        frappe.log_error(f"reaper: could not list managed instances: {e}", "WebODM Compute")
        return 0
    if not managed:
        return 0
    live = set(frappe.get_all(
        "WebODM Compute Instance", filters={"status": ["in", list(LIVE_STATUSES)], "handle": ["!=", ""]},
        pluck="handle",
    ))
    destroyed = 0
    grace = orphan_grace()
    for item in managed:
        handle = item.get("handle")
        if not handle or handle in live:
            continue
        if (item.get("status") or "").lower() in ("terminated", "shutting-down"):
            continue
        launched = item.get("launched_at")
        try:
            age = (now - get_datetime(launched)).total_seconds() if launched else grace + 1
        except Exception:
            age = grace + 1
        if age < grace:
            continue  # may be a create call still in flight
        try:
            ProvisionerClient().destroy(handle)
            destroyed += 1
            frappe.log_error(f"reaper: destroyed orphan {handle} (age {int(age)}s)", "WebODM Compute")
        except ProvisionerError as e:
            frappe.log_error(f"reaper: orphan {handle} destroy failed: {e}", "WebODM Compute ALERT")
    return destroyed


def reap_job():
    """Scheduler entry point; never raises."""
    try:
        if frappe.db.count("WebODM Compute Instance", {"status": ["in", list(LIVE_STATUSES)]}) == 0 and not enabled():
            return
        stats = reap()
        if stats["destroyed"] or stats["orphans"] or stats["errors"]:
            frappe.logger("webodm").info(f"compute reaper: {stats}")
    except Exception as e:
        frappe.log_error(f"compute reaper failed: {e}", "WebODM Compute")
