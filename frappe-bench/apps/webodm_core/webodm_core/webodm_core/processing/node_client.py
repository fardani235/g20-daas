import json
import requests
from urllib.parse import urljoin


class NodeODMError(Exception):
    pass


class NodeODMClient:
    def __init__(self, hostname: str, port: int, token: str | None = None, timeout: int = 30):
        self.base_url = f"http://{hostname}:{port}"
        self.token = token
        self.timeout = timeout
        self._session = requests.Session()
        if token:
            self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _url(self, path: str) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))

    def _get(self, path: str):
        try:
            r = self._session.get(self._url(path), timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise NodeODMError(f"GET {path} failed: {e}")

    def _post(self, path: str, data=None, files=None):
        try:
            r = self._session.post(self._url(path), data=data, files=files, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise NodeODMError(f"POST {path} failed: {e}")

    def info(self) -> dict:
        return self._get("info")

    def version(self) -> str:
        return self._get("version")

    def create_task(self, images: list[tuple[str, bytes]], options: list[dict] | None = None) -> dict:
        files = [("images", (name, data, "image/jpeg")) for name, data in images]
        data = {}
        if options:
            data["options"] = json.dumps(options)
        return self._post("task/new", data=data, files=files)

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
        return self._post(f"task/{task_id}/cancel")

    def task_remove(self, task_id: str) -> dict:
        return self._post(f"task/{task_id}/remove")

    def task_restart(self, task_id: str) -> dict:
        return self._post(f"task/{task_id}/restart")

    def download_asset(self, task_id: str, asset: str) -> bytes:
        try:
            r = self._session.get(
                self._url(f"task/{task_id}/download/{asset}"),
                timeout=self.timeout,
                stream=True,
            )
            r.raise_for_status()
            content = r.content
            if content and content.startswith(b"{"):
                try:
                    err = json.loads(content)
                    if "error" in err:
                        raise NodeODMError(f"Download {asset} failed: {err['error']}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            return content
        except requests.RequestException as e:
            raise NodeODMError(f"Download {asset} failed: {e}")

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
