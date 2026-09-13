"""Make the served asset tree match the image, then drop the cached manifest.

The image bakes built assets at ``/workspace/assets-baked`` (outside the
``sites`` volume, so it is never masked). ``sites/assets`` is a container volume
that can outlive image rebuilds, so on every web start we refresh it from the
baked copy when the manifest differs, then clear Frappe's Redis ``assets_json``
cache. Without this, pages reference hashes that no longer exist (missing CSS).

Best-effort: startup must never fail because of assets.
"""

import filecmp
import os
import subprocess

BAKED = "/workspace/assets-baked"
DEST = "/workspace/frappe-bench/sites/assets"


def _manifest(path):
    return os.path.join(path, "assets.json")


def _sync():
    if not os.path.isdir(BAKED):
        print("[assets] no baked assets; skipping sync")
        return
    try:
        same = filecmp.cmp(_manifest(BAKED), _manifest(DEST), shallow=False)
    except OSError:
        same = False
    if same:
        print("[assets] sites/assets already matches image")
        return
    os.makedirs(DEST, exist_ok=True)
    subprocess.run(["cp", "-a", BAKED + "/.", DEST + "/"], check=False)
    print("[assets] synced sites/assets from image")


def _clear_cache():
    url = os.environ.get("FRAPPE_REDIS_CACHE")
    if not url:
        return
    try:
        import redis
    except Exception:
        return
    try:
        client = redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
        deleted = 0
        for key in client.scan_iter(match="*assets_json*", count=100):
            deleted += client.delete(key)
        print(f"[assets] cleared assets_json cache keys={deleted}")
    except Exception as e:  # noqa: BLE001
        print(f"[assets] cache clear skipped: {e}")


try:
    _sync()
    _clear_cache()
except Exception as e:  # noqa: BLE001
    print(f"[assets] prepare skipped: {e}")
