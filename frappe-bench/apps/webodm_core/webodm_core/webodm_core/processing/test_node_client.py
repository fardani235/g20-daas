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


if __name__ == "__main__":
    unittest.main()
