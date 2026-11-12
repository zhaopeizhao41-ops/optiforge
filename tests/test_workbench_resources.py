"""Workbench MCP resources and change notifications (SIMULATED demo service; never opens Zemax)."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import anyio

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import server  # noqa: E402
import workbench_demo_service as demo_service  # noqa: E402
from core import workbench_resources as resources  # noqa: E402
from core.workbench_control import WorkbenchControlError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


class MappingTests(unittest.TestCase):
    def test_events_map_to_the_resources_they_can_change(self):
        R = resources
        self.assertEqual(R.uris_for_events([{"kind": "state_changed"}]), [R.STATUS, R.MODEL, R.LAYOUT_PNG])
        self.assertEqual(R.uris_for_events([{"kind": "finished", "tool": "__analysis__"}, {"kind": "analysis_changed"}]),
                         [R.STATUS, R.ANALYSIS, R.ANALYSIS_PNG])
        self.assertEqual(R.uris_for_events([{"kind": "finished", "tool": "zemax_design_workflow"}]),
                         [R.STATUS, R.STAGES, R.STAGES_PNG])
        self.assertEqual(R.uris_for_events([{"kind": "trace_finished"}]), [R.LAYOUT_PNG])
        self.assertEqual(R.uris_for_events([{"kind": "queued"}, {"kind": "started"}, {"kind": "finished", "tool": "x"}]), [R.STATUS])
        self.assertEqual(R.uris_for_events([{"kind": "something_new"}, "garbage"]), [R.STATUS])
        self.assertEqual(R.uris_for_events([], reset=True), list(R.ALL))
        self.assertEqual(R.uris_for_events([]), [])


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event.uri)


class FakeClient:
    def __init__(self, instance, batches):
        self.instance, self.batches, self.calls = instance, list(batches), []

    def events(self, instance, after, timeout_ms):
        self.calls.append((after, timeout_ms))
        if not self.batches:
            raise WorkbenchControlError("WORKBENCH_UNAVAILABLE", "gone")
        return self.batches.pop(0)


class WatcherTests(unittest.TestCase):
    def run_watcher(self, factory, seconds=0.6):
        bus = FakeBus()
        watcher = resources.ResourceWatcher(bus, client_factory=factory, timeout_ms=0, retry_s=0.05)

        async def main():
            with anyio.move_on_after(seconds):
                await watcher.run()
        with patch.object(resources, "COALESCE_S", 0.01):
            anyio.run(main)
        return bus.events

    def test_connect_is_quiet_and_one_operation_is_announced_once(self):
        client = FakeClient("one", [
            {"events": [{"kind": "state_changed"}], "next_seq": 7},  # connect: cursor only, never published
            {"events": [{"kind": "queued"}, {"kind": "started"}], "next_seq": 9},
            {"events": [{"kind": "finished", "tool": "zemax_run_optimization"}, {"kind": "state_changed"}], "next_seq": 11},
        ])
        clients = [client]
        published = self.run_watcher(lambda: clients[0] if clients else (_ for _ in ()).throw(
            WorkbenchControlError("WORKBENCH_CONFIG_MISSING", "x")))
        self.assertEqual(published[:3], [resources.STATUS, resources.MODEL, resources.LAYOUT_PNG])
        self.assertEqual(published.count(resources.STATUS), 1)
        self.assertEqual(client.calls[0], (0, 0))
        self.assertEqual(client.calls[1][0], 7)
        self.assertEqual(client.calls[2], (9, 0))

    def test_new_service_instance_announces_everything_and_errors_retry(self):
        first = FakeClient("one", [{"events": [], "next_seq": 1}])
        second = FakeClient("two", [{"events": [], "next_seq": 1}, {"events": [], "next_seq": 1}])
        queue = [first, second]

        def factory():
            if not queue:
                raise WorkbenchControlError("WORKBENCH_UNAVAILABLE", "offline")
            return queue.pop(0)
        published = self.run_watcher(factory)
        self.assertEqual(published[:len(resources.ALL)], list(resources.ALL))

    def test_unconfigured_watcher_publishes_nothing(self):
        def factory():
            raise WorkbenchControlError("WORKBENCH_CONFIG_MISSING", "x")
        self.assertEqual(self.run_watcher(factory, 0.3), [])


class ResourceReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = demo_service.Demo(0, pause=False)
        cls.demo.start_server()
        cls.demo.requirements()
        cls.demo.proposal()
        cls.demo.confirm()
        cls.demo.stages()
        cls.env = {"OPTIFORGE_SERVICE_URL": f"http://127.0.0.1:{cls.demo.server.server_port}",
                   "OPTIFORGE_SERVICE_FILE": str(cls.demo.service_file)}

    @classmethod
    def tearDownClass(cls):
        cls.demo.server.shutdown()
        cls.demo.server.server_close()
        cls.demo.engine.executor.shutdown()
        cls.demo.close(keep=False)

    def test_json_resources_read_through_the_server_functions(self):
        with patch.dict(os.environ, self.env):
            status = json.loads(server.workbench_status_resource())
            model = json.loads(server.workbench_model_resource())
            stages = json.loads(server.workbench_stages_resource())
            analysis = json.loads(server.workbench_analysis_resource())
        self.assertEqual(status["active_project"], demo_service.PROJECT)
        self.assertEqual(status["resources"], list(resources.ALL))
        self.assertEqual(status["workflow"]["stage"], "handoff")
        self.assertTrue(any(task["kind"] == "model_handoff" for task in status["workflow"]["tasks"]))
        self.assertNotIn(self.demo.server.token, json.dumps(status))
        self.assertEqual(model["model"]["name"], "Cooke 三片式 · SIMULATED")
        self.assertEqual(len(stages["stages"]), 4)
        self.assertIn(analysis["overall"], {"PASS", "WARN", "FAIL", "ERROR", "INFO"})

    def test_png_resources_and_placeholder_when_unconfigured(self):
        with patch.dict(os.environ, self.env):
            for function in (server.workbench_layout_png, server.workbench_stages_png, server.workbench_analysis_png):
                self.assertTrue(function().startswith(PNG))
        with patch.dict(os.environ, {"OPTIFORGE_SERVICE_URL": "", "OPTIFORGE_SERVICE_FILE": ""}):
            self.assertTrue(server.workbench_layout_png().startswith(PNG))
            self.assertEqual(json.loads(server.workbench_status_resource())["code"], "WORKBENCH_CONFIG_MISSING")

    def test_dropped_client_does_not_print_a_traceback(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            try:
                raise ConnectionResetError(10054, "reset")
            except ConnectionResetError:
                self.demo.server.handle_error(None, ("127.0.0.1", 1))
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
