import unittest
from unittest.mock import patch

from webodm_core.webodm_core.processing.node_client import NodeODMClient


class TestGetOptions(unittest.TestCase):
    def test_get_options_calls_options_endpoint(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c, "_get", return_value=[{"name": "dsm", "type": "bool"}]) as g:
            out = c.get_options()
        g.assert_called_once_with("options")
        self.assertEqual(out, [{"name": "dsm", "type": "bool"}])


if __name__ == "__main__":
    unittest.main()
