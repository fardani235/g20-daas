"""Idempotently write the bench/site config a container needs before Frappe starts.

Runs at every container start (all roles) with the bench venv interpreter,
reading only environment variables. Writes:

- ``sites/apps.txt`` / ``sites/apps.json`` if the sites volume is still empty
  (the image bakes them, but the named volume masks that tree).
- ``sites/common_site_config.json``: postgres superuser login (``bench
  new-site`` needs it), auth-embedded redis URLs, geospatial service URL,
  plugin runner URL + sandbox dir, ``default_site``. Existing keys not managed
  here are preserved.
- ``sites/<SITE_NAME>/site_config.json``: ``max_file_size`` (drone uploads
  exceed Frappe's 25 MB default), only if the site exists.

Never touches the database; safe to run concurrently from several roles.
"""

import json
import os
import shutil
import sys
import urllib.parse

BENCH = os.environ.get("FRAPPE_BENCH_ROOT", "/workspace/frappe-bench")
SITES = os.path.join(BENCH, "sites")
BAKED_SITES = "/workspace/sites-baked"

MAX_FILE_SIZE = int(os.environ.get("FRAPPE_MAX_FILE_SIZE", 10 * 1024 * 1024 * 1024))


def _env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        sys.exit(f"[configure] {name} must be set")
    return v


def _int_env(name, default):
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"[configure] {name} must be an integer, got {raw!r}")


def _redis_url(host, port, password):
    auth = f":{urllib.parse.quote(password, safe='')}@" if password else ""
    return f"redis://{auth}{host}:{port}"


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_if_changed(path, data):
    current = _load(path)
    if current == data:
        return False
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return True


def seed_sites_dir():
    os.makedirs(SITES, exist_ok=True)
    for name in ("apps.txt", "apps.json"):
        dst = os.path.join(SITES, name)
        src = os.path.join(BAKED_SITES, name)
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copyfile(src, dst)
            print(f"[configure] seeded sites/{name} from image")


def common_site_config(site):
    path = os.path.join(SITES, "common_site_config.json")
    cfg = _load(path)
    cfg.update({
        # `bench` CLI (used by operators inside the container) refuses to run as
        # root unless frappe_user says so. The entrypoint itself does not use it.
        "frappe_user": "root",
        "restart_supervisor_on_update": False,
        "restart_systemd_on_update": False,
        "rebase_on_pull": False,
        "shallow_clone": True,
        "background_workers": int(_env("FRAPPE_BACKGROUND_WORKERS", "1")),
        # Dev-only asset watcher; the image's build-time config has it on.
        "live_reload": _env("FRAPPE_LIVE_RELOAD", "0") in ("1", "true", "True"),
        "default_site": site,
        "socketio_port": int(_env("FRAPPE_SOCKETIO_PORT", "9000")),
        "geospatial_url": _env("GEOSPATIAL_URL", "http://geospatial:5000"),
        # User plugin sandbox (services/plugin-runner); the dir is the shared
        # `plugin_sandbox` volume, mounted at the same path in the runner.
        "plugin_runner_url": _env("PLUGIN_RUNNER_URL", "http://plugin-runner:5001"),
        "plugin_sandbox_dir": _env("PLUGIN_SANDBOX_DIR", "/sandbox"),
        # On-demand compute (webodm_core.webodm_core.processing.compute). Empty
        # provisioner_url = no provider: tasks run on the static node as before.
        "provisioner_url": _env("PROVISIONER_URL", ""),
        "compute_max_instances": _int_env("WEBODM_COMPUTE_MAX_INSTANCES", 5),
        "compute_max_instances_per_org": _int_env("WEBODM_COMPUTE_MAX_INSTANCES_PER_ORG", 2),
        "compute_provision_timeout_seconds": _int_env("WEBODM_COMPUTE_PROVISION_TIMEOUT_SECONDS", 900),
        "compute_max_lifetime_seconds": _int_env("WEBODM_COMPUTE_MAX_LIFETIME_SECONDS", 43200),
        "compute_orphan_grace_seconds": _int_env("WEBODM_COMPUTE_ORPHAN_GRACE_SECONDS", 600),
        "compute_large_task_images": _int_env("WEBODM_COMPUTE_LARGE_TASK_IMAGES", 500),
        # Object storage (webodm_core.storage). Non-secret settings only: the
        # bucket empty = host disk is the only store. Credentials stay in the
        # process environment (WEBODM_S3_ACCESS_KEY_ID / _SECRET_ACCESS_KEY)
        # and are never written to any config file.
        "storage_bucket": _env("WEBODM_S3_BUCKET", ""),
        "storage_prefix": _env("WEBODM_S3_PREFIX", ""),
        "storage_region": _env("WEBODM_S3_REGION", ""),
        "storage_endpoint_url": _env("WEBODM_S3_ENDPOINT_URL", ""),
        "storage_force_path_style": _env("WEBODM_S3_FORCE_PATH_STYLE", "0") in ("1", "true", "True"),
        "storage_presign_ttl": _int_env("WEBODM_S3_PRESIGN_TTL", 900),
        "storage_cache_max_bytes": _int_env("WEBODM_CACHE_MAX_BYTES", 50 * 1024 * 1024 * 1024),
        "storage_cache_idle_seconds": _int_env("WEBODM_CACHE_IDLE_SECONDS", 7 * 24 * 3600),
        "root_login": _env("FRAPPE_ROOT_USER", "frappe_admin"),
        "root_password": _env("FRAPPE_ROOT_PASSWORD", required=True),
        "use_redis_auth": bool(_env("REDIS_CACHE_PASSWORD") or _env("REDIS_QUEUE_PASSWORD")),
        "redis_cache": _env("FRAPPE_REDIS_CACHE", required=True),
        "redis_queue": _env("FRAPPE_REDIS_QUEUE", required=True),
    })
    if _write_if_changed(path, cfg):
        print("[configure] wrote sites/common_site_config.json")


def site_config(site):
    path = os.path.join(SITES, site, "site_config.json")
    if not os.path.exists(path):
        return  # site not created yet (init role will do it)
    cfg = _load(path)
    if cfg.get("max_file_size") != MAX_FILE_SIZE:
        cfg["max_file_size"] = MAX_FILE_SIZE
        _write_if_changed(path, cfg)
        print(f"[configure] set max_file_size={MAX_FILE_SIZE} on {site}")


def main():
    site = _env("SITE_NAME", required=True)
    seed_sites_dir()
    common_site_config(site)
    site_config(site)


if __name__ == "__main__":
    main()
