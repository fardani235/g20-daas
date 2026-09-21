"""Idempotently upsert the Frappe config files the container owns.

Runs on every container start, before the role process. Replaces the shell/
Python heredocs that used to live inside docker-compose.yml.

common_site_config.json
  * created with sane container defaults if absent;
  * root_login/root_password, redis_cache/redis_queue (auth embedded),
    geospatial_url, default_site, use_redis_auth are always set from the
    environment, so a rotated secret or a changed SITE_NAME takes effect on
    restart instead of being frozen by whatever an earlier init wrote.

sites/<SITE_NAME>/site_config.json (only if the site exists)
  * max_file_size is raised to MAX_FILE_SIZE (default 10 GiB); Frappe's 25 MB
    default is far too small for drone imagery.

Environment (see infra/frappe/entrypoint.sh): SITES_PATH, SITE_NAME,
FRAPPE_ROOT_USER, FRAPPE_ROOT_PASSWORD, REDIS_CACHE_PASSWORD,
REDIS_QUEUE_PASSWORD, REDIS_CACHE_HOST/PORT, REDIS_QUEUE_HOST/PORT,
GEOSPATIAL_URL, MAX_FILE_SIZE, FRAPPE_LIVE_RELOAD.
"""

import json
import os
import urllib.parse

SITES = os.environ.get("SITES_PATH", "/workspace/frappe-bench/sites")
SITE = os.environ["SITE_NAME"]

COMMON_DEFAULTS = {
    "frappe_user": "root",
    "restart_supervisor_on_update": False,
    "restart_systemd_on_update": False,
    "rebase_on_pull": False,
    "shallow_clone": True,
    "background_workers": 1,
    "live_reload": True,
}


def _redis_url(prefix: str, default_host: str, default_port: str) -> str:
    pwd = urllib.parse.quote(os.environ[f"{prefix}_PASSWORD"], safe="")
    host = os.environ.get(f"{prefix}_HOST", default_host)
    port = os.environ.get(f"{prefix}_PORT", default_port)
    return f"redis://:{pwd}@{host}:{port}"


def _load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path: str, cfg: dict) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def configure_common() -> None:
    path = os.path.join(SITES, "common_site_config.json")
    cfg = _load(path)
    changed = not cfg
    for k, v in COMMON_DEFAULTS.items():
        cfg.setdefault(k, v)
    managed = {
        "root_login": os.environ.get("FRAPPE_ROOT_USER", "frappe_admin"),
        "root_password": os.environ["FRAPPE_ROOT_PASSWORD"],
        "redis_cache": _redis_url("REDIS_CACHE", "redis-cache", "13000"),
        "redis_queue": _redis_url("REDIS_QUEUE", "redis-queue", "11000"),
        "use_redis_auth": True,
        "geospatial_url": os.environ.get("GEOSPATIAL_URL", "http://geospatial:5000"),
        "default_site": SITE,
        # Dev convenience; set FRAPPE_LIVE_RELOAD=false in production.
        "live_reload": os.environ.get("FRAPPE_LIVE_RELOAD", "true").lower() not in ("0", "false", "no"),
    }
    for k, v in managed.items():
        if cfg.get(k) != v:
            cfg[k] = v
            changed = True
    if changed:
        _write(path, cfg)
        print(f"[configure_site] wrote {path}")


def configure_site() -> None:
    path = os.path.join(SITES, SITE, "site_config.json")
    if not os.path.isfile(path):
        return  # not created yet; init.sh will make it and we re-run next start
    cfg = _load(path)
    want = int(os.environ.get("MAX_FILE_SIZE", 10 * 1024 * 1024 * 1024))
    if cfg.get("max_file_size") != want:
        cfg["max_file_size"] = want
        _write(path, cfg)
        print(f"[configure_site] set max_file_size={want} in {path}")


if __name__ == "__main__":
    os.makedirs(SITES, exist_ok=True)
    configure_common()
    configure_site()
