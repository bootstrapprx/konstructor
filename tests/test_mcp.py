import json
import os
import tempfile
import unittest
from pathlib import Path

from konstructor.mcp_server import MCPServer
from konstructor.workflow import stage_artifacts


class MCPTests(unittest.TestCase):
    def test_protocol_lists_tools_and_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["KONSTRUCTOR_HOME"] = tmp
            root = Path(tmp)
            server = MCPServer(root)
            initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertIn("capabilities", initialized["result"])
            tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
            self.assertIn("konstructor_changeset_apply", [tool["name"] for tool in tools])
            with self.assertRaises(ValueError):
                stage_artifacts(root, "escape", [{"path": "../outside", "action": "add", "content": "x"}])

    def test_stage_artifacts_keeps_target_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["KONSTRUCTOR_HOME"] = tmp
            root = Path(tmp)
            (root / "existing.txt").write_text("before")
            cs = stage_artifacts(root, "edit", [
                {"path": "existing.txt", "action": "modify", "content": "after"},
                {"path": "new.txt", "action": "add", "content": "new"},
            ])
            self.assertEqual((root / "existing.txt").read_text(), "before")
            self.assertTrue((Path(cs.staging) / "new.txt").is_file())


if __name__ == "__main__":
    unittest.main()
