"""Object storage primitives that need no site: key layout and org namespacing,
the seekable range reader that lets zipfile walk an archive in S3, error
mapping (no provider details leak), and the read adapter."""

import io
import unittest
import zipfile
from unittest.mock import patch

from webodm_core import storage
from webodm_core.storage.s3file import S3RangeFile
from webodm_core.storage.testing import FakeObjectStorage


class TestKeys(unittest.TestCase):
    def test_safe_segment_strips_separators_and_traversal(self):
        self.assertEqual(storage.safe_segment("DJI_0001.JPG"), "DJI_0001.JPG")
        self.assertEqual(storage.safe_segment("../etc/passwd"), "_etc_passwd")
        self.assertEqual(storage.safe_segment("a/b\\c"), "a_b_c")
        self.assertEqual(storage.safe_segment("with space&more"), "with_space_more")
        self.assertEqual(storage.safe_segment(""), "_")

    def test_task_key_is_org_namespaced(self):
        task = type("T", (), {"name": "abc123", "organization": "Acme Org"})()
        with patch.object(storage, "org_namespace", return_value="acme-org"), \
                patch.object(storage, "_conf", side_effect=lambda k, d=None: {"storage_prefix": "prod/"}.get(k, d)):
            self.assertEqual(storage.task_prefix(task), "prod/orgs/acme-org/tasks/abc123/")
            self.assertEqual(storage.task_key(task, "inputs", "IMG 1.jpg"), "prod/orgs/acme-org/tasks/abc123/inputs/IMG_1.jpg")
            self.assertEqual(storage.task_key(task, "assets", "orthophoto.tif"), "prod/orgs/acme-org/tasks/abc123/assets/orthophoto.tif")

    def test_assert_org_key_refuses_other_namespaces(self):
        with patch.object(storage, "org_namespace", side_effect=lambda org: org.lower()), \
                patch.object(storage, "_conf", side_effect=lambda k, d=None: {"storage_prefix": ""}.get(k, d)):
            storage.assert_org_key("orgs/acme/tasks/t/assets/x.tif", "ACME")
            with self.assertRaises(storage.OrgBoundaryError):
                storage.assert_org_key("orgs/other/tasks/t/assets/x.tif", "ACME")
            with self.assertRaises(storage.OrgBoundaryError):
                storage.assert_org_key("orgs/acme-2/tasks/t/assets/x.tif", "acme")
            with self.assertRaises(storage.OrgBoundaryError):
                storage.assert_org_key("", "acme")

    def test_org_namespace_requires_org(self):
        with self.assertRaises(storage.OrgBoundaryError):
            storage.org_namespace("")

    def test_uri_roundtrip(self):
        with patch.object(storage, "_conf", side_effect=lambda k, d=None: {"storage_bucket": "b"}.get(k, d)):
            self.assertEqual(storage.uri("orgs/a/x"), "s3://b/orgs/a/x")
            self.assertEqual(storage.key_from_uri("s3://b/orgs/a/x"), "orgs/a/x")
            self.assertIsNone(storage.key_from_uri("s3://other/orgs/a/x"))
        with patch.object(storage, "_conf", return_value=None):
            self.assertFalse(storage.configured())
            with self.assertRaises(storage.StorageNotConfigured):
                storage.uri("x")

    def test_content_types(self):
        self.assertEqual(storage.content_type_for("orthophoto.tif"), "image/tiff")
        self.assertEqual(storage.content_type_for("x.laz"), "application/vnd.laszip")
        self.assertEqual(storage.content_type_for("model.glb"), "model/gltf-binary")
        self.assertEqual(storage.content_type_for("IMG.JPG"), "image/jpeg")
        self.assertEqual(storage.content_type_for("weird.bin"), "application/octet-stream")


class TestRangeFile(unittest.TestCase):
    def _store(self, data: bytes):
        fake = FakeObjectStorage()
        fake.objects["k"] = data
        return fake

    def test_seek_tell_read_semantics(self):
        fake = self._store(bytes(range(256)) * 4)
        f = S3RangeFile(fake, "k", 1024)
        self.assertTrue(f.seekable() and f.readable())
        self.assertEqual(f.seek(0, io.SEEK_END), 1024)
        self.assertEqual(f.seek(-4, io.SEEK_END), 1020)
        self.assertEqual(f.read(), bytes([252, 253, 254, 255]))
        self.assertEqual(f.read(), b"")  # EOF
        f.seek(10)
        self.assertEqual(f.read(3), bytes([10, 11, 12]))
        self.assertEqual(f.tell(), 13)
        f.seek(2, io.SEEK_CUR)
        self.assertEqual(f.read(1), bytes([15]))
        with self.assertRaises(ValueError):
            f.seek(-1)

    def test_zipfile_walks_archive_with_few_range_requests(self):
        members = {f"odm_dem/{i}.tif": bytes([i]) * (300 * 1024) for i in range(3)}
        members["odm_orthophoto/odm_orthophoto.tif"] = b"II*\x00" + b"\x01" * (3 * 1024 * 1024)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for name, data in members.items():
                z.writestr(name, data)
        fake = self._store(buf.getvalue())

        with fake.open_seekable("k", buffer_size=1024 * 1024) as fh, zipfile.ZipFile(fh) as z:
            self.assertEqual(set(z.namelist()), set(members))
            with z.open("odm_orthophoto/odm_orthophoto.tif") as src:
                out = io.BytesIO()
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
            self.assertEqual(out.getvalue(), members["odm_orthophoto/odm_orthophoto.tif"])
            self.assertEqual(z.read("odm_dem/1.tif"), members["odm_dem/1.tif"])

        ranges = [c for c in fake.calls if c[0] == "range"]
        # ~4 MB archive with a 1 MB buffer: a handful of requests, not thousands
        self.assertLess(len(ranges), 40, ranges)
        # nothing ever asked past the end of the object
        self.assertTrue(all(c[3] < len(fake.objects["k"]) for c in ranges))


class TestErrorTranslation(unittest.TestCase):
    def _client_error(self, code, status):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code, "Message": "secret-bucket-name in here"},
                            "ResponseMetadata": {"HTTPStatusCode": status}}, "GetObject")

    def test_maps_codes_without_leaking_messages(self):
        try:
            import botocore  # noqa: F401
        except ImportError:
            self.skipTest("botocore not installed")
        t = storage.ObjectStorage._translate
        self.assertIn("not found", str(t(self._client_error("NoSuchKey", 404), "get")))
        denied = t(self._client_error("AccessDenied", 403), "get")
        self.assertIn("access denied", str(denied))
        self.assertNotIn("secret-bucket-name", str(denied))
        self.assertIsInstance(t(self._client_error("InternalError", 500), "put"), storage.StorageUnavailable)
        self.assertIsInstance(t(OSError("disk"), "get"), storage.StorageUnavailable)
        self.assertNotIsInstance(t(self._client_error("NoSuchKey", 404), "get"), storage.StorageUnavailable)


class TestReadAdapter(unittest.TestCase):
    def test_read_negative_or_none_reads_all(self):
        a = storage._ReadAdapter(io.BytesIO(b"abcdef"))
        self.assertEqual(a.read(2), b"ab")
        self.assertEqual(a.read(-1), b"cdef")
        a = storage._ReadAdapter(io.BytesIO(b"xyz"))
        self.assertEqual(a.read(None), b"xyz")


if __name__ == "__main__":
    unittest.main()
