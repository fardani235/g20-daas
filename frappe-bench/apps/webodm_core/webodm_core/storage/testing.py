"""Test double for :class:`webodm_core.storage.ObjectStorage`.

An in-memory bucket with the same surface the pipeline uses, so the object
storage code paths (input sync, node -> S3 relay, cache fill, eviction) can
run in the Frappe test suite without S3, MinIO or boto3. Install it with
``use_fake_storage()``; it also flips ``storage.configured()`` on.
"""

from __future__ import annotations

import hashlib
import io
from contextlib import contextmanager
from unittest.mock import patch

import frappe

from webodm_core import storage


class FakeObjectStorage:
    def __init__(self, bucket: str = "fake-bucket"):
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.presign_ttl = 900
        self.calls: list[tuple] = []
        self.fail_next: Exception | None = None  # raise once on the next mutating call

    # -- helpers ----------------------------------------------------------

    def _maybe_fail(self, what):
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        self.calls.append(what)

    def _meta(self, key):
        data = self.objects[key]
        return {"size": len(data), "etag": hashlib.md5(data).hexdigest(),  # nosec
                "content_type": self.content_types.get(key, "")}

    # -- ObjectStorage surface --------------------------------------------

    def head(self, key):
        return self._meta(key) if key in self.objects else None

    def exists(self, key):
        return key in self.objects

    def put_path(self, local_path, key, content_type=None):
        self._maybe_fail(("put", key))
        with open(local_path, "rb") as fh:
            self.objects[key] = fh.read()
        self.content_types[key] = content_type or ""
        return self._meta(key)

    def put_stream(self, stream, key, content_type=None):
        self._maybe_fail(("put", key))
        chunks = []
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        self.objects[key] = b"".join(chunks)
        self.content_types[key] = content_type or ""
        return self._meta(key)

    def put_bytes(self, data, key, content_type=None):
        return self.put_stream(io.BytesIO(data), key, content_type)

    def get_to_path(self, key, dest_path):
        self._maybe_fail(("get", key))
        if key not in self.objects:
            raise storage.StorageError("download failed: object not found")
        import os

        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        with open(dest_path + ".part", "wb") as fh:
            fh.write(self.objects[key])
        os.replace(dest_path + ".part", dest_path)
        return dest_path

    def open_stream(self, key):
        self._maybe_fail(("get", key))
        if key not in self.objects:
            raise storage.StorageError("get failed: object not found")
        return io.BytesIO(self.objects[key])

    def read_range(self, key, start, end_inclusive):
        self.calls.append(("range", key, start, end_inclusive))
        return self.objects[key][start:end_inclusive + 1]

    def open_seekable(self, key, buffer_size=8 * 1024 * 1024):
        from webodm_core.storage.s3file import S3RangeFile

        if key not in self.objects:
            raise storage.StorageError("open failed: object not found")
        return io.BufferedReader(S3RangeFile(self, key, len(self.objects[key])), buffer_size=buffer_size)

    def copy(self, src_key, dest_key, content_type=None):
        self._maybe_fail(("copy", src_key, dest_key))
        self.objects[dest_key] = self.objects[src_key]
        self.content_types[dest_key] = content_type or self.content_types.get(src_key, "")
        return self._meta(dest_key)

    def delete(self, key):
        self._maybe_fail(("delete", key))
        self.objects.pop(key, None)

    def list_keys(self, prefix):
        return [k for k in sorted(self.objects) if k.startswith(prefix)]

    def delete_prefix(self, prefix):
        keys = self.list_keys(prefix)
        for k in keys:
            self.objects.pop(k, None)
        return len(keys)

    def presign_get(self, key, ttl=None):
        return f"https://fake/{self.bucket}/{key}?ttl={min(ttl or self.presign_ttl, self.presign_ttl)}"


@contextmanager
def use_fake_storage(bucket: str = "fake-bucket", prefix: str = ""):
    """Configure object storage with an in-memory bucket for the duration."""
    fake = FakeObjectStorage(bucket)
    conf = {"storage_bucket": bucket, "storage_prefix": prefix}

    def _conf(key, default=None):
        return conf.get(key, default)

    with patch.object(storage, "_conf", side_effect=_conf), patch.object(storage, "get", return_value=fake):
        frappe.local.webodm_object_storage = fake
        try:
            yield fake
        finally:
            frappe.local.webodm_object_storage = None
