import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from webodm_core.webodm_core.processing.node_client import NodeODMClient, NodeODMError


class TestGetOptions(unittest.TestCase):
    def test_get_options_calls_options_endpoint(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c, "_get", return_value=[{"name": "dsm", "type": "bool"}]) as g:
            out = c.get_options()
        g.assert_called_once_with("options")
        self.assertEqual(out, [{"name": "dsm", "type": "bool"}])


class TestErrorBodies(unittest.TestCase):
    """NodeODM returns HTTP 200 with {"error": ...} for most failures; the
    client must surface those as NodeODMError instead of returning them."""

    def _resp(self, payload, status=200):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = payload
        r.raise_for_status.return_value = None
        return r

    def test_get_raises_on_error_key(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c._session, "get", return_value=self._resp({"error": "Invalid uuid"})):
            with self.assertRaises(NodeODMError) as ctx:
                c.task_info("nope")
        self.assertIn("Invalid uuid", str(ctx.exception))

    def test_post_raises_on_error_key(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c._session, "post", return_value=self._resp({"error": "Need at least 1 file.", "noRetry": True})):
            with self.assertRaises(NodeODMError):
                c._post("task/new/upload/x")

    def test_post_wraps_transport_errors(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c._session, "post", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(NodeODMError):
                c._post("task/cancel", data={"uuid": "x"})


class TestTaskLifecycleEndpoints(unittest.TestCase):
    """NodeODM's cancel/remove/restart take the task uuid in the request BODY at a
    flat path (POST /task/cancel {uuid}), not a path-style URL (POST
    /task/<uuid>/cancel). The path-style call 404s, so a "cancel" never reaches the
    node and ODM keeps running. See the node's swagger."""

    def test_task_cancel_posts_uuid_in_body(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c, "_post", return_value={"success": True}) as p:
            c.task_cancel("abc-123")
        p.assert_called_once_with("task/cancel", data={"uuid": "abc-123"})

    def test_task_remove_posts_uuid_in_body(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c, "_post", return_value={"success": True}) as p:
            c.task_remove("abc-123")
        p.assert_called_once_with("task/remove", data={"uuid": "abc-123"})

    def test_task_restart_posts_uuid_in_body(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c, "_post", return_value={"success": True}) as p:
            c.task_restart("abc-123")
        p.assert_called_once_with("task/restart", data={"uuid": "abc-123"})


class TestCreateTaskStreamsOneImageAtATime(unittest.TestCase):
    """Regression: the old single POST /task/new built one multipart body from
    every image's bytes, so a 500-image flight meant the whole dataset in RAM.
    The chunked init/upload/commit flow must send exactly one file per request."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = []
        for i in range(3):
            p = os.path.join(self.tmp.name, f"img{i}.jpg")
            with open(p, "wb") as fh:
                fh.write(b"\xff\xd8" + bytes([i]) * 64)
            self.paths.append((f"img{i}.jpg", p))

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_upload_per_image_commit(self):
        c = NodeODMClient("localhost", 3000)
        seen_files = []

        def fake_post(path, data=None, files=None, timeout=None):
            if path == "task/new/init":
                # Must be multipart parts, not urlencoded `data`: NodeODM's init
                # route only parses multipart and would drop name/options.
                self.assertIsNone(data)
                self.assertEqual(files["options"], (None, json.dumps([{"name": "dsm", "value": True}])))
                self.assertEqual(files["name"], (None, "Flight 1"))
                return {"uuid": "U-1"}
            if path.startswith("task/new/upload/"):
                self.assertEqual(path, "task/new/upload/U-1")
                self.assertEqual(len(files), 1, "exactly one image per upload request")
                field, (fname, fh, ctype) = files[0]
                self.assertEqual(field, "images")
                seen_files.append((fname, fh.read()))
                self.assertEqual(timeout, c.transfer_timeout)
                return {"success": True}
            if path == "task/new/commit/U-1":
                return {"uuid": "U-1"}
            self.fail(f"unexpected POST {path}")

        with patch.object(c, "_post", side_effect=fake_post) as p:
            result = c.create_task(self.paths, [{"name": "dsm", "value": True}], name="Flight 1")

        self.assertEqual(result, {"uuid": "U-1"})
        self.assertEqual([call.args[0] for call in p.call_args_list],
                         ["task/new/init", "task/new/upload/U-1", "task/new/upload/U-1",
                          "task/new/upload/U-1", "task/new/commit/U-1"])
        self.assertEqual([f for f, _ in seen_files], ["img0.jpg", "img1.jpg", "img2.jpg"])
        for (_, data), (_, path) in zip(seen_files, self.paths):
            with open(path, "rb") as fh:
                self.assertEqual(data, fh.read())

    def test_init_without_uuid_is_an_error(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c, "_post", return_value={}):
            with self.assertRaises(NodeODMError):
                c.create_task(self.paths)

    def test_upload_error_aborts_before_commit(self):
        c = NodeODMClient("localhost", 3000)
        calls = []

        def fake_post(path, data=None, files=None, timeout=None):
            calls.append(path)
            if path == "task/new/init":
                return {"uuid": "U-2"}
            raise NodeODMError("upload failed")

        with patch.object(c, "_post", side_effect=fake_post):
            with self.assertRaises(NodeODMError):
                c.create_task(self.paths)
        self.assertNotIn("task/new/commit/U-2", calls)

    def test_no_images_is_an_error(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c, "_post", return_value={"uuid": "U-3"}):
            with self.assertRaises(NodeODMError):
                c.create_task([])


class _FakeStreamingResponse:
    def __init__(self, body: bytes, content_type: str, chunk=7):
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.content = body
        self._chunk = chunk

    def raise_for_status(self):
        pass

    def json(self):
        return json.loads(self._body)

    def iter_content(self, chunk_size=None):
        for i in range(0, len(self._body), self._chunk):
            yield self._body[i:i + self._chunk]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDownloadAssetStreamsToDisk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = os.path.join(self.tmp.name, "all.zip")

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_body_to_dest_and_cleans_part_file(self):
        c = NodeODMClient("localhost", 3000)
        body = os.urandom(1000)
        with patch.object(c._session, "get", return_value=_FakeStreamingResponse(body, "application/zip")) as g:
            out = c.download_asset("U-1", "all.zip", self.dest)
        self.assertEqual(out, self.dest)
        with open(self.dest, "rb") as fh:
            self.assertEqual(fh.read(), body)
        self.assertFalse(os.path.exists(self.dest + ".part"))
        self.assertTrue(g.call_args.kwargs["stream"])
        self.assertEqual(g.call_args.kwargs["timeout"], c.transfer_timeout)

    def test_json_error_body_raises_and_leaves_no_file(self):
        c = NodeODMClient("localhost", 3000)
        body = json.dumps({"error": "Asset not ready"}).encode()
        with patch.object(c._session, "get", return_value=_FakeStreamingResponse(body, "application/json; charset=utf-8")):
            with self.assertRaises(NodeODMError) as ctx:
                c.download_asset("U-1", "all.zip", self.dest)
        self.assertIn("Asset not ready", str(ctx.exception))
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(self.dest + ".part"))

    def test_transport_error_raises_nodeodm_error(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c._session, "get", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(NodeODMError):
                c.download_asset("U-1", "all.zip", self.dest)
        self.assertFalse(os.path.exists(self.dest + ".part"))


if __name__ == "__main__":
    unittest.main()
