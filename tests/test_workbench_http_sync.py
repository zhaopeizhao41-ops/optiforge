"""HTTP v2, event and bridge integration tests using a controllable fake engine."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.workbench_client import WorkbenchClient
from core.workbench_service import EngineWorker, WorkbenchHTTPServer, write_service_file
from core.workbench_state import CONTEXT_KEYS


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workbench-http-sync-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.credentials = self.root / "service.json"
        self.count = 0
        self.writes = 0
        self.value = 1
        self.project = "中文验收"
        self.start_server()
        self.addCleanup(self.stop_server)

    def start_server(self, port=0):
        def snapshot():
            self.count += 1
            return {"status": "success", "schema_version": 1, "revision": str(self.value),
                    "model": {"surfaces": [], "value": self.value}, "layout": {}}
        def write(**arguments):
            self.writes += 1
            self.value += 1
            return {"status": "success", "message": "changed"}
        session = object()
        self.registry = {"zemax_get_workbench_snapshot": snapshot, "zemax_surface_operations": write,
                         "zemax_load_file": lambda filepath: {"status": "success"},
                         "zemax_lookup_manual": lambda: {"text": "knowledge"}, "echo": lambda **arguments: arguments}
        self.engine = EngineWorker(self.registry, metadata=lambda: {"active_project": self.project,
            "model_project": self.project, "proposal_confirmed": False, "session_identity": session})
        self.server = WorkbenchHTTPServer(("127.0.0.1", port), self.engine)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        write_service_file(self.server, self.credentials)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        self.engine.executor.shutdown()

    def request(self, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request("GET" if body is None else "POST", path, body,
                     {"X-OptiForge-Token": self.server.token, **(headers or {})})
        response = conn.getresponse()
        result = json.loads(response.read())
        conn.close()
        return response.status, result

    def tool(self, name, expected=None):
        return self.request("/api/tool", json.dumps({"protocol_version": 2, "name": name, "arguments": {},
                            "expected_context": expected}), {"Content-Type": "application/json"})[1]

    def events(self, seq, timeout=0, instance=None):
        instance = instance or self.engine.state.context()["service_instance_id"]
        return self.request(f"/api/events?instance_id={instance}&after_seq={seq}&timeout_ms={timeout}")[1]

    def test_cache_and_heartbeat_never_read_engine_and_initializing_is_explicit(self):
        _, initial = self.request("/api/state")
        self.assertEqual(initial["validity"], "initializing")
        self.assertIsNone(initial["snapshot"])
        self.assertEqual(self.count, 0)
        self.engine.call("zemax_get_workbench_snapshot")
        _, state = self.request("/api/state")
        for _ in range(4):
            self.request("/api/state")
            self.assertEqual(self.events(state["event_seq"])["events"], [])
        self.assertEqual(self.count, 1)
        self.assertEqual(self.engine.state.read()["state_version"], state["state_version"])

    def test_long_operation_keeps_state_and_events_responsive(self):
        self.engine.call("zemax_get_workbench_snapshot")
        entered, release = threading.Event(), threading.Event()
        def wait_write():
            entered.set()
            if not release.wait(2): raise RuntimeError("fixture wait")
            return {"status": "success"}
        self.registry["zemax_surface_operations"] = wait_write
        _, future = self.engine.submit("zemax_surface_operations", expected_context=self.engine.state.context())
        try:
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            _, state = self.request("/api/state")
            events = self.events(state["event_seq"] - 1)
            self.assertLess(time.monotonic() - start, .5)
            self.assertEqual(state["tasks"]["pending"][0]["status"], "running")
            self.assertTrue(events["events"])
            self.assertEqual(self.count, 1)
        finally: release.set(); future.result(2)

    def test_refresh_coalesces_while_queued_and_returns_immediately(self):
        entered, release = threading.Event(), threading.Event()
        def hold():
            entered.set()
            if not release.wait(2): raise RuntimeError("fixture wait")
            return {}
        self.registry["hold"] = hold
        _, future = self.engine.submit("hold")
        self.assertTrue(entered.wait(1))
        try:
            responses = [self.request("/api/refresh", '{"protocol_version":2}', {"Content-Type": "application/json"}) for _ in range(3)]
            self.assertTrue(all(status == 202 for status, _ in responses))
            self.assertEqual(len({value["operation_id"] for _, value in responses}), 1)
        finally: release.set(); future.result(2)
        self.engine.refresh_future.result(2)
        self.assertEqual(self.count, 1)

    def test_protocol_and_context_are_enforced_for_direct_http(self):
        status, result = self.request("/api/tool", '{"name":"zemax_surface_operations"}', {"Content-Type": "application/json"})
        self.assertEqual((status, result["code"]), (409, "WORKBENCH_PROTOCOL_INCOMPATIBLE"))
        self.assertEqual(self.tool("zemax_surface_operations")["result"]["code"], "WORKBENCH_CONTEXT_REQUIRED")
        status, value = self.request("/api/model/import", b"fixture", {"Content-Type": "application/octet-stream", "X-File-Name": "x.zmx"})
        self.assertEqual(status, 409)
        self.assertEqual(self.writes, 0)

    def test_two_bridges_require_explicit_observation_after_other_client_changes(self):
        a = WorkbenchClient(self.url, self.credentials); b = WorkbenchClient(self.url, self.credentials)
        a.call("zemax_get_workbench_snapshot", {}); b.call("zemax_get_workbench_snapshot", {})
        self.assertEqual(a.call("zemax_surface_operations", {})["status"], "success")
        b.call("zemax_lookup_manual", {})
        self.assertEqual(b.call("zemax_surface_operations", {})["code"], "WORKBENCH_CONTEXT_STALE")
        b.call("zemax_get_workbench_snapshot", {})
        self.assertEqual(b.call("zemax_surface_operations", {})["status"], "success")
        self.assertEqual(self.writes, 2)
        self.assertEqual(set(b.context), set(CONTEXT_KEYS))

    def test_bridge_context_contains_no_snapshot_or_tasks(self):
        client = WorkbenchClient(self.url, self.credentials)
        self.assertEqual(client.call("echo", {"x": 1}), {"x": 1})
        self.assertEqual(set(client.context), set(CONTEXT_KEYS))

    def test_restart_rejects_old_writes_and_does_not_adopt_context_from_error(self):
        client = WorkbenchClient(self.url, self.credentials)
        client.call("zemax_get_workbench_snapshot", {})
        original = client.context.copy()
        port = self.server.server_port
        self.stop_server(); self.start_server(port)
        self.assertEqual(client.call("zemax_surface_operations", {})["code"], "WORKBENCH_CONTEXT_STALE")
        self.assertEqual(client.context, original)
        self.assertTrue(self.events(0, instance=original["service_instance_id"])["reset_required"])
        client.call("zemax_get_workbench_snapshot", {})
        self.assertNotEqual(client.context["service_instance_id"], original["service_instance_id"])
        self.assertEqual(client.call("zemax_surface_operations", {})["status"], "success")
        self.assertEqual(self.writes, 1)

    def test_first_state_cursor_does_not_lose_change_before_subscribe(self):
        self.engine.call("zemax_get_workbench_snapshot")
        _, first = self.request("/api/state")
        self.tool("zemax_surface_operations", self.engine.state.context())
        events = self.events(first["event_seq"])
        self.assertIn("state_changed", [event["kind"] for event in events["events"]])
        _, latest = self.request("/api/state")
        self.assertGreater(latest["state_version"], first["state_version"])

    def test_chinese_context_is_ascii_encoded_and_import_returns_own_state(self):
        self.engine.call("zemax_get_workbench_snapshot")
        context = self.engine.state.context()
        with patch("core.workbench_service.ROOT", self.root):
            status, value = self.request("/api/model/import", b"fixture", {
                "Content-Type": "application/octet-stream", "X-File-Name": urllib.parse.quote("验收.zmx"),
                "X-OptiForge-Protocol": "2", "X-Expected-Context": urllib.parse.quote(json.dumps(context, ensure_ascii=False))})
        self.assertEqual(status, 200)
        self.assertEqual(value["result"]["status"], "success")
        self.assertEqual(value["state"]["active_project"], "中文验收")
        self.assertEqual(value["state"]["snapshot_context"]["state_version"], value["context"]["state_version"])

    def test_shutdown_wakes_active_http_long_poll(self):
        entered = threading.Event()
        original = self.engine.state.wait_events
        def entered_wait(*args):
            entered.set()
            return original(*args)
        with patch.object(self.engine.state, "wait_events", side_effect=entered_wait), ThreadPoolExecutor() as clients:
            waiting = clients.submit(self.events, self.engine.state.seq, 20000)
            self.assertTrue(entered.wait(1))
            self.server.shutdown()
            self.assertTrue(waiting.result(2)["closed"])

    def test_event_wait_validation_and_no_secrets_in_notifications(self):
        self.assertEqual(self.request("/api/events?after_seq=0")[0], 400)
        self.assertEqual(self.request("/api/events?instance_id=x&after_seq=0&timeout_ms=20001")[0], 400)
        client = WorkbenchClient(self.url, self.credentials)
        client.call("echo", {"private": "secret-prescription"})
        events = self.events(0)
        encoded = json.dumps(events)
        self.assertNotIn("secret-prescription", encoded)
        self.assertNotIn(self.server.token, encoded)

    def test_late_client_response_cannot_roll_back_observed_context(self):
        client = WorkbenchClient(self.url, self.credentials)
        client.call("zemax_get_workbench_snapshot", {})
        old = client.context.copy()
        newer = {**old, "state_version": old["state_version"] + 2}
        client._adopt(newer, client.generation)
        client._adopt(old, client.generation)
        self.assertEqual(client.context, newer)
        client._adopt({**old, "state_version": 99}, client.generation - 1)
        self.assertEqual(client.context, newer)


if __name__ == "__main__": unittest.main(verbosity=2)
