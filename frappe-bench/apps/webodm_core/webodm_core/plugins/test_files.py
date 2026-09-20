"""Streaming File registration must be byte-exact, hashed like Frappe would
hash it, and immune to Frappe's EXIF stripping."""
import hashlib
import io
import os

import frappe
from frappe.tests.utils import FrappeTestCase
from PIL import Image

from webodm_core.plugins.files import (
    abs_path_for_file_doc,
    save_private_file_from_path,
    save_private_file_from_stream,
)


def _jpeg_with_exif() -> bytes:
    im = Image.new("RGB", (8, 8), (10, 20, 30))
    exif = im.getexif()
    exif[306] = "2024:05:01 10:20:30"
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class TestSavePrivateFileFromStream(FrappeTestCase):
    def setUp(self):
        self._created = []

    def tearDown(self):
        frappe.set_user("Administrator")
        for name in self._created:
            if frappe.db.exists("File", name):
                frappe.delete_doc("File", name, ignore_permissions=True, force=True)

    def _save(self, data: bytes, name: str, **kw):
        doc = save_private_file_from_stream(io.BytesIO(data), name, ignore_permissions=True, **kw)
        self._created.append(doc.name)
        return doc

    def test_bytes_land_on_disk_unchanged_with_size_and_md5(self):
        data = os.urandom(3 * 1024 * 1024 + 17)  # > chunk size, odd tail
        doc = self._save(data, "blob.bin")

        path = abs_path_for_file_doc(doc)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), data)
        self.assertEqual(doc.file_size, len(data))
        self.assertEqual(doc.content_hash, hashlib.md5(data).hexdigest())
        self.assertTrue(doc.is_private)
        self.assertTrue(doc.file_url.startswith("/private/files/"))
        self.assertFalse(os.path.exists(path + ".part"))

    def test_name_collision_gets_a_suffix_not_an_overwrite(self):
        a = self._save(b"first", "same_name.txt")
        b = self._save(b"second", "same_name.txt")
        self.assertNotEqual(a.file_url, b.file_url)
        with open(abs_path_for_file_doc(a), "rb") as fh:
            self.assertEqual(fh.read(), b"first")
        with open(abs_path_for_file_doc(b), "rb") as fh:
            self.assertEqual(fh.read(), b"second")

    def test_identical_content_is_not_deduplicated_into_a_shared_blob(self):
        # Frappe's save_file() would point both docs at ONE file by content hash;
        # deleting one task would then delete the other's asset. Streaming path
        # must keep them independent.
        a = self._save(b"same bytes", "dup_a.txt")
        b = self._save(b"same bytes", "dup_b.txt")
        self.assertNotEqual(a.file_url, b.file_url)

    def test_exif_survives_even_when_site_strips_exif(self):
        data = _jpeg_with_exif()
        self.assertIn(b"Exif\x00", data)
        original = frappe.db.get_single_value("System Settings", "strip_exif_metadata_from_uploaded_images")
        frappe.db.set_single_value("System Settings", "strip_exif_metadata_from_uploaded_images", 1)
        try:
            doc = self._save(data, "geotagged.jpg")
            with open(abs_path_for_file_doc(doc), "rb") as fh:
                on_disk = fh.read()
        finally:
            frappe.db.set_single_value("System Settings", "strip_exif_metadata_from_uploaded_images", original or 0)
        self.assertEqual(on_disk, data)
        self.assertIn(b"Exif\x00", on_disk)

    def test_attachment_fields_are_recorded(self):
        doc = self._save(b"x", "attached.txt", attached_to_doctype="WebODM Task", attached_to_name="NOPE-1")
        self.assertEqual(doc.attached_to_doctype, "WebODM Task")
        self.assertEqual(doc.attached_to_name, "NOPE-1")

    def test_slashes_in_name_cannot_escape_private_files(self):
        doc = self._save(b"x", "../../etc/passwd")
        path = os.path.realpath(abs_path_for_file_doc(doc))
        base = os.path.realpath(frappe.get_site_path("private", "files"))
        self.assertTrue(path.startswith(base + os.sep), path)


class TestSavePrivateFileFromPath(FrappeTestCase):
    def tearDown(self):
        for name in getattr(self, "_created", []):
            if frappe.db.exists("File", name):
                frappe.delete_doc("File", name, ignore_permissions=True, force=True)

    def test_move_registers_and_removes_source(self):
        scratch = frappe.get_site_path("private", "files", "plugin_runs")
        os.makedirs(scratch, exist_ok=True)
        src = os.path.join(scratch, "move_me.dat")
        data = os.urandom(4096)
        with open(src, "wb") as fh:
            fh.write(data)

        doc = save_private_file_from_path(src, "moved.dat", ignore_permissions=True)
        self._created = [doc.name]

        self.assertFalse(os.path.exists(src))
        with open(abs_path_for_file_doc(doc), "rb") as fh:
            self.assertEqual(fh.read(), data)
        self.assertEqual(doc.file_size, len(data))
        self.assertEqual(doc.content_hash, hashlib.md5(data).hexdigest())
