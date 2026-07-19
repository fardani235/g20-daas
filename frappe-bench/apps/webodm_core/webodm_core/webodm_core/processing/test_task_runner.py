import unittest

from webodm_core.webodm_core.processing.task_runner import _build_node_options


def _as_map(options):
    """NodeODM options are a list of {name, value}; flatten to a dict for assertions."""
    return {o["name"]: o["value"] for o in options}


class TestBuildNodeOptions(unittest.TestCase):
    def test_returns_nodeodm_array_shape(self):
        # NodeODM's POST /task/new requires options as an array of
        # {"name": ..., "value": ...}, NOT a bare {name: value} dict. A dict is
        # silently dropped by NodeODM's filterOptions and ODM runs with defaults.
        opts = _build_node_options({"dsm": True})
        self.assertIsInstance(opts, list)
        self.assertTrue(all(set(o) == {"name", "value"} for o in opts))

    def test_dsm_selected_forwards_dsm_flag(self):
        self.assertEqual(_as_map(_build_node_options({"dsm": True})).get("dsm"), True)

    def test_dtm_and_dsm_together(self):
        m = _as_map(_build_node_options({"dsm": True, "dtm": True}))
        self.assertEqual(m.get("dsm"), True)
        self.assertEqual(m.get("dtm"), True)

    def test_model_and_pointcloud_map_to_odm_flag_names(self):
        m = _as_map(_build_node_options({"model": True, "pointCloud": True}))
        self.assertEqual(m.get("glb"), True)
        self.assertEqual(m.get("pc-ept"), True)

    def test_orthophoto_resolution_passed_as_value(self):
        m = _as_map(_build_node_options({"orthophoto": True, "orthophotoResolution": 3}))
        self.assertEqual(m.get("orthophoto-resolution"), 3)

    def test_orthophoto_checked_does_not_skip(self):
        m = _as_map(_build_node_options({"orthophoto": True}))
        self.assertNotIn("skip-orthophoto", m)

    def test_orthophoto_unchecked_sends_skip_orthophoto(self):
        # ODM has no --orthophoto flag; the orthophoto is produced by default and
        # can only be suppressed with --skip-orthophoto.
        m = _as_map(_build_node_options({"orthophoto": False, "dsm": True}))
        self.assertEqual(m.get("skip-orthophoto"), True)

    def test_unselected_outputs_are_absent(self):
        m = _as_map(_build_node_options({"orthophoto": True, "dsm": False, "dtm": False}))
        self.assertNotIn("dsm", m)
        self.assertNotIn("dtm", m)
        self.assertNotIn("glb", m)
        self.assertNotIn("pc-ept", m)

    def test_falsy_resolution_not_sent(self):
        m = _as_map(_build_node_options({"orthophoto": True, "orthophotoResolution": None}))
        self.assertNotIn("orthophoto-resolution", m)


if __name__ == "__main__":
    unittest.main()
