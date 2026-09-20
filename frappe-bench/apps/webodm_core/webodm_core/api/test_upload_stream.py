"""_save_task_image_file streams the werkzeug upload straight to disk and keeps
the bytes exactly as uploaded (EXIF intact) regardless of site settings."""
import io

import frappe
from frappe.tests.utils import FrappeTestCase
from PIL import Image

from webodm_core.api.task import _extract_photo_meta, _save_task_image_file
from webodm_core.plugins.files import abs_path_for_file_doc


def _geotagged_jpeg() -> bytes:
    im = Image.new("RGB", (8, 8), (1, 2, 3))
    exif = im.getexif()
    exif[306] = "2024:05:01 10:20:30"
    gps = exif.get_ifd(34853)
    gps[1] = "N"; gps[2] = (52.0, 30.0, 0.0)
    gps[3] = "E"; gps[4] = (13.0, 24.0, 0.0)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class TestSaveTaskImageFile(FrappeTestCase):
    def tearDown(self):
        for f in frappe.get_all("File", filters={"attached_to_doctype": "WebODM Task",
                                                 "attached_to_name": "UPLOAD-STREAM-TEST"}, pluck="name"):
            frappe.delete_doc("File", f, ignore_permissions=True, force=True)

    def test_stream_is_saved_byte_exact_and_exif_readable_from_disk(self):
        data = _geotagged_jpeg()
        stream = io.BytesIO(data)
        stream.read(5)  # simulate a stream that was partially consumed upstream

        original = frappe.db.get_single_value("System Settings", "strip_exif_metadata_from_uploaded_images")
        frappe.db.set_single_value("System Settings", "strip_exif_metadata_from_uploaded_images", 1)
        try:
            doc = _save_task_image_file(stream, "DJI_0001.JPG", "UPLOAD-STREAM-TEST")
        finally:
            frappe.db.set_single_value("System Settings", "strip_exif_metadata_from_uploaded_images", original or 0)

        path = abs_path_for_file_doc(doc)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), data)
        self.assertEqual(doc.file_size, len(data))
        self.assertEqual(doc.attached_to_name, "UPLOAD-STREAM-TEST")

        meta = _extract_photo_meta(path)
        self.assertAlmostEqual(meta["lat"], 52.5, places=5)
        self.assertAlmostEqual(meta["lng"], 13.4, places=5)
        self.assertEqual(meta["capture_time"], "2024-05-01 10:20:30")
