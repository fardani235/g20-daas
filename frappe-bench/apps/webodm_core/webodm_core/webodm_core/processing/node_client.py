import json
import os
from collections.abc import Iterable
from urllib.parse import urljoin

import requests


class NodeODMError(Exception):
    pass


# Metadata calls (info/options/task status) are small and should fail fast.
DEFAULT_TIMEOUT = 30
# Transfers (per-image upload, all.zip download) can legitimately take minutes
# on slow links; (connect, read) tuple so a dead node still fails quickly.
DEFAULT_TRANSFER_TIMEOUT = (30, 600)
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


class NodeODMClient:
    def __init__(
        self,
        hostname: str,
        port: int,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        transfer_timeout=DEFAULT_TRANSFER_TIMEOUT,
    ):
        self.base_url = f"http://{hostname}:{port}"
        self.token = token
        self.timeout = timeout
        self.transfer_timeout = transfer_timeout
        self._session = requests.Session()
        if token:
            self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _url(self, path: str) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))

    @staticmethod
    def _check_error(payload, what: str):
        # NodeODM reports most failures as HTTP 200 + {"error": "..."} (see its
        # `die`/res.json({error}) handlers), so a status check alone is not enough.
        if isinstance(payload, dict) and payload.get("error"):
            raise NodeODMError(f"{what} failed: {payload['error']}")
        return payload

    def _get(self, path: str):
        try:
            r = self._session.get(self._url(path), timeout=self.timeout)
            r.raise_for_status()
            return self._check_error(r.json(), f"GET {path}")
        except requests.RequestException as e:
            raise NodeODMError(f"GET {path} failed: {e}")

    def _post(self, path: str, data=None, files=None, timeout=None):
        try:
            r = self._session.post(
                self._url(path), data=data, files=files, timeout=timeout or self.timeout
            )
            r.raise_for_status()
            return self._check_error(r.json(), f"POST {path}")
        except requests.RequestException as e:
            raise NodeODMError(f"POST {path} failed: {e}")

    def info(self) -> dict:
        return self._get("info")

    def version(self) -> str:
        return self._get("version")

    def get_options(self) -> list:
        return self._get("options")

    def create_task(
        self,
        images: Iterable[tuple[str, str]],
        options: list[dict] | None = None,
        name: str | None = None,
    ) -> dict:
        """Create a task from on-disk images using NodeODM's chunked flow.

        ``images`` is an iterable of ``(filename, absolute_path)``. Instead of a
        single ``POST /task/new`` carrying every image in one multipart body
        (which forced the whole dataset into worker RAM), this drives
        ``/task/new/init`` -> one ``/task/new/upload/{uuid}`` per image ->
        ``/task/new/commit/{uuid}``. Only one image is in flight at a time, so
        peak memory is one image rather than the sum of all of them.
        """
        # NodeODM parses /task/new/init with multer().none(), i.e. it only reads
        # multipart/form-data. A urlencoded body is silently ignored and the task
        # is created with no name and default options, so send the fields as
        # multipart parts (filename=None makes requests emit a plain form field).
        fields = {}
        if options:
            fields["options"] = json.dumps(options)
        if name:
            fields["name"] = name
        init_parts = {k: (None, v) for k, v in fields.items()} or None

        init = self._post("task/new/init", files=init_parts)
        uuid = init.get("uuid") if isinstance(init, dict) else None
        if not uuid:
            raise NodeODMError(f"task/new/init returned no uuid: {init}")

        uploaded = 0
        for filename, path in images:
            with open(path, "rb") as fh:
                self._post(
                    f"task/new/upload/{uuid}",
                    files=[("images", (filename, fh, "image/jpeg"))],
                    timeout=self.transfer_timeout,
                )
            uploaded += 1
        if uploaded == 0:
            raise NodeODMError("create_task called with no images")

        result = self._post(f"task/new/commit/{uuid}", timeout=self.transfer_timeout)
        if not (isinstance(result, dict) and result.get("uuid")):
            raise NodeODMError(f"task/new/commit returned no uuid: {result}")
        return result

    def task_info(self, task_id: str) -> dict:
        return self._get(f"task/{task_id}/info")

    def task_output(self, task_id: str, line: int = 0) -> list[str]:
        r = self._session.get(
            self._url(f"task/{task_id}/output"),
            params={"line": str(line)},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def task_cancel(self, task_id: str) -> dict:
        # NodeODM expects the uuid in the body at a flat path (POST /task/cancel),
        # NOT a path-style /task/<uuid>/cancel — the latter 404s and the cancel
        # never reaches the node.
        return self._post("task/cancel", data={"uuid": task_id})

    def task_remove(self, task_id: str) -> dict:
        return self._post("task/remove", data={"uuid": task_id})

    def task_restart(self, task_id: str) -> dict:
        return self._post("task/restart", data={"uuid": task_id})

    def download_asset(self, task_id: str, asset: str, dest_path: str) -> str:
        """Stream ``asset`` (e.g. ``all.zip``) to ``dest_path`` and return it.

        Never holds the body in memory: chunks go straight to ``dest_path.part``
        which is renamed on success. NodeODM signals "not ready"/"invalid asset"
        as an HTTP 200 JSON body, so that case is detected and raised.
        """
        part = dest_path + ".part"
        try:
            with self._session.get(
                self._url(f"task/{task_id}/download/{asset}"),
                timeout=self.transfer_timeout,
                stream=True,
            ) as r:
                r.raise_for_status()
                if "json" in (r.headers.get("Content-Type") or "").lower():
                    err = r.json() if r.content else {}
                    raise NodeODMError(
                        f"Download {asset} failed: {err.get('error', 'unexpected JSON response')}"
                    )
                with open(part, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                        if chunk:
                            fh.write(chunk)
            os.replace(part, dest_path)
            return dest_path
        except requests.RequestException as e:
            raise NodeODMError(f"Download {asset} failed: {e}")
        finally:
            try:
                os.remove(part)
            except OSError:
                pass

    def find_best_node(self, nodes: list[dict]) -> dict | None:
        best = None
        for n in nodes:
            try:
                client = NodeODMClient(n["hostname"], n["port"], n.get("token"))
                info = client.info()
                queue = info.get("taskQueue", 0)
                max_imgs = info.get("maxImages", 0)
                if best is None or queue < best["queue"]:
                    best = {"node": n, "info": info, "queue": queue, "max_images": max_imgs}
            except NodeODMError:
                continue
        return best
