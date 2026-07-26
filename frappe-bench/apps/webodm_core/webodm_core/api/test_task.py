import io
import json
import unittest

from PIL import Image

from webodm_core.api.task import _gps_to_decimal, _extract_photo_meta


def _plain_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 120, 120)).save(buf, format="JPEG")
    return buf.getvalue()


def _jpeg_with_datetime(value="2024:05:01 10:20:30"):
    im = Image.new("RGB", (8, 8), (10, 20, 30))
    exif = im.getexif()
    exif[306] = value  # DateTime (base IFD) — reliably round-trips through PIL
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class TestExtractPhotoMeta(unittest.TestCase):
    def test_gps_to_decimal_north(self):
        self.assertAlmostEqual(_gps_to_decimal((40, 34, 30), "N"), 40.575, places=3)

    def test_gps_to_decimal_west_is_negative(self):
        self.assertAlmostEqual(_gps_to_decimal((73, 30, 0), "W"), -73.5, places=3)

    def test_plain_image_yields_all_none(self):
        meta = _extract_photo_meta(_plain_jpeg())
        self.assertEqual(
            meta,
            {"lat": None, "lng": None, "altitude": None, "capture_time": None},
        )

    def test_capture_time_parsed_to_frappe_datetime(self):
        meta = _extract_photo_meta(_jpeg_with_datetime())
        self.assertEqual(meta["capture_time"], "2024-05-01 10:20:30")

    def test_garbage_bytes_never_raise(self):
        meta = _extract_photo_meta(b"not an image")
        self.assertEqual(meta["lat"], None)
        self.assertEqual(meta["capture_time"], None)

    def test_malformed_datetime_degrades_to_none(self):
        # A truthy-but-unparseable DateTime tag must not survive into the row,
        # or task.save() would raise on the Datetime field and block the batch.
        meta = _extract_photo_meta(_jpeg_with_datetime("not-a-date"))
        self.assertIsNone(meta["capture_time"])


class TestAutoStart(unittest.TestCase):
    def test_auto_start_helper_enqueues_when_enabled(self):
        from unittest.mock import patch
        from webodm_core.api import task as task_api
        with patch("webodm_core.api.settings.get") as gs, patch("frappe.enqueue") as enq:
            gs.return_value = {"auto_start_processing": 1}
            task_api._maybe_autostart("SOME-TASK")
            enq.assert_called_once()

    def test_auto_start_helper_noop_when_disabled(self):
        from unittest.mock import patch
        from webodm_core.api import task as task_api
        with patch("webodm_core.api.settings.get") as gs, patch("frappe.enqueue") as enq:
            gs.return_value = {"auto_start_processing": 0}
            task_api._maybe_autostart("SOME-TASK")
            enq.assert_not_called()


class TestEncodeProcessingOptions(unittest.TestCase):
    # The dispatch read-path (task_runner.process_task) does: if the stored value
    # is a str, parse_json it once, then _build_node_options. On PostgreSQL a JSON
    # column auto-parses one level on read. So the full chain from a double-encoded
    # scalar back to the original is: parse_json (Postgres read) -> parse_json
    # (dispatch) -> original. These tests assert that round-trip and delete-safety.
    def _roundtrip(self, encoded):
        import frappe
        pg_read = frappe.parse_json(encoded)   # Postgres auto-parse (1 level)
        # On Postgres this is already a str; dispatch parse_json's it to the object.
        return frappe.parse_json(pg_read) if isinstance(pg_read, str) else pg_read

    def test_list_options_roundtrip(self):
        from webodm_core.api.task import _encode_processing_options
        opts = [{"name": "dsm", "value": True}, {"name": "feature-quality", "value": "ultra"}]
        encoded = _encode_processing_options(json.dumps(opts))
        self.assertIsInstance(encoded, str)
        self.assertEqual(self._roundtrip(encoded), opts)

    def test_encoded_value_is_a_string_scalar_not_a_list(self):
        # Delete-safety on Postgres: the column must hold a JSON string, not an
        # array (an array reloads as a Python list and breaks the delete snapshot).
        import frappe
        from webodm_core.api.task import _encode_processing_options
        encoded = _encode_processing_options(json.dumps([{"name": "dsm", "value": True}]))
        self.assertIsInstance(frappe.parse_json(encoded), str)

    def test_dict_options_still_supported(self):
        from webodm_core.api.task import _encode_processing_options
        opts = {"orthophoto": True, "dsm": False}
        encoded = _encode_processing_options(json.dumps(opts))
        self.assertEqual(self._roundtrip(encoded), opts)

    def test_empty_or_invalid_returns_none(self):
        from webodm_core.api.task import _encode_processing_options
        self.assertIsNone(_encode_processing_options(None))
        self.assertIsNone(_encode_processing_options(""))
        self.assertIsNone(_encode_processing_options("not json"))
        self.assertIsNone(_encode_processing_options(json.dumps(42)))


if __name__ == "__main__":
    unittest.main()
