"""mcp_bridge joins the workbench only when its credential file points at a live service.

Never opens OpticStudio and never contacts a real service: the health probe is injected.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mcp_bridge  # noqa: E402

KEYS = ("OPTIFORGE_SERVICE_URL", "OPTIFORGE_SERVICE_FILE")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="bridge-"))
        self.file = self.folder / "service.json"
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for key in KEYS:
            os.environ.pop(key, None)
        self.where = patch.object(mcp_bridge, "SERVICE_FILE", self.file)
        self.where.start()

    def tearDown(self):
        self.where.stop()
        self.env.stop()

    def write(self, url):
        self.file.write_text(json.dumps({"url": url, "token": "secret"}), encoding="utf-8")

    def test_live_service_is_joined(self):
        self.write("http://127.0.0.1:8765")
        connected, reason = mcp_bridge.configure(probe=lambda url: True)
        self.assertTrue(connected)
        self.assertEqual(os.environ["OPTIFORGE_SERVICE_URL"], "http://127.0.0.1:8765")
        self.assertEqual(os.environ["OPTIFORGE_SERVICE_FILE"], str(self.file))
        self.assertNotIn("secret", reason)

    def test_stale_credential_falls_back_to_native(self):
        self.write("http://127.0.0.1:8765")
        connected, reason = mcp_bridge.configure(probe=lambda url: False)
        self.assertFalse(connected)
        self.assertIn("stale", reason)
        for key in KEYS:
            self.assertNotIn(key, os.environ)

    def test_missing_or_non_loopback_credential_is_ignored(self):
        probe = lambda url: self.fail("must not probe")  # noqa: E731
        self.assertEqual(mcp_bridge.configure(probe=probe), (False, "no credential file"))
        self.write("http://example.com:8765")
        self.assertFalse(mcp_bridge.configure(probe=probe)[0])
        self.assertNotIn("OPTIFORGE_SERVICE_URL", os.environ)

    def test_explicit_environment_wins_without_probing(self):
        os.environ["OPTIFORGE_SERVICE_URL"] = "http://127.0.0.1:9000"
        connected, reason = mcp_bridge.configure(probe=lambda url: self.fail("must not probe"))
        self.assertTrue(connected)
        self.assertEqual(reason, "environment")

    def test_health_probe_rejects_unreachable_port(self):
        # Port 9 (discard) is not a workbench; the probe must answer False, not raise.
        self.assertFalse(mcp_bridge._alive("http://127.0.0.1:9"))


if __name__ == "__main__":
    unittest.main()
