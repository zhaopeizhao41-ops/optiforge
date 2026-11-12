"""UI control contract tests: state machine, HTTP auth/identity, ACK and expiry."""
import http.client
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workbench_control import WorkbenchControlClient, WorkbenchControlError
from core.workbench_engine import EngineWorker
from core.workbench_service import WorkbenchHTTPServer, write_service_file
from core.workbench_ui import (COMMAND_TTL_MS, MAX_SESSIONS, SESSION_LEASE_MS,
                               WorkbenchUIController, WorkbenchUIError)


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.ui = WorkbenchUIController(clock=self.clock)
        self.session = "ui_session_0001"

    def test_navigate_ack_applied_and_status_receipt(self):
        self.ui.register(self.session, "design")
        result = self.ui.navigate(self.session, "analysis", "request_0001")
        self.assertEqual(result["status"], "accepted")
        command_id = result["command_id"]
        pending = self.ui.heartbeat(self.session, "design")["command"]
        self.assertEqual(pending["command_id"], command_id)
        ack = self.ui.ack(self.session, command_id, "applied", "analysis")
        self.assertEqual(ack["receipt"]["status"], "applied")
        self.assertEqual(ack["receipt"]["actual_page"], "analysis")
        status = self.ui.status()
        self.assertEqual(status["commands"][0]["status"], "applied")
        self.assertEqual(status["frontends"][0]["page"], "analysis")

    def test_pending_command_survives_history_trim(self):
        self.ui.register(self.session, "design")
        for index in range(70):
            page = "analysis" if index % 2 == 0 else "design"
            command = self.ui.navigate(self.session, page, f"request_{index:04d}")
            self.ui.ack(self.session, command["command_id"], "applied", page)
        pending = self.ui.navigate(self.session, "design", "request_pending_keep")
        status = self.ui.status()
        self.assertIn(pending["command_id"], [item["command_id"] for item in status["pending"]])
        self.assertLessEqual(len(status["commands"]), 64)

    def test_remaining_uses_monotonic_and_session_capacity_is_bounded(self):
        self.ui.register(self.session, "design")
        self.ui.navigate(self.session, "analysis", "request_remaining")
        self.clock.value += 1
        beat = self.ui.heartbeat(self.session, "design")["command"]
        self.assertLess(beat["remaining_ms"], 8000)
        self.assertGreater(beat["remaining_ms"], 0)
        for index in range(MAX_SESSIONS - 1):
            self.ui.register(f"ui_cap_{index:04d}", "design")
        with self.assertRaises(WorkbenchUIError) as raised:
            self.ui.register("ui_cap_overflow", "design")
        self.assertEqual(raised.exception.code, "UI_SESSIONS_FULL")
        status = self.ui.status()
        self.assertLessEqual(len(status["frontends"]), MAX_SESSIONS)
        self.assertLessEqual(len(status["pending"]), MAX_SESSIONS)

    def test_invalid_ack_status_type_is_structured(self):
        self.ui.register(self.session, "design")
        command = self.ui.navigate(self.session, "analysis", "request_bad_ack_type")
        for status in ({}, [], 3, None):
            with self.assertRaises(WorkbenchUIError) as raised:
                self.ui.ack(self.session, command["command_id"], status, "analysis")
            self.assertEqual(raised.exception.code, "INVALID_ACK_STATUS")

    def test_two_sessions_isolated_and_manual_page_visible(self):
        first, second, fresh = "ui_session_000a", "ui_session_000b", "ui_session_000c"
        self.ui.register(first, "design")
        self.ui.register(second, "design")
        command = self.ui.navigate(first, "analysis", "request_isolation")
        self.assertIsNone(self.ui.heartbeat(second, "design")["command"])
        self.assertEqual(self.ui.heartbeat(first, "design")["command"]["command_id"], command["command_id"])
        self.ui.heartbeat(second, "workflow")
        pages = {item["ui_session_id"]: item["page"] for item in self.ui.status()["frontends"]}
        self.assertEqual(pages[second], "workflow")
        self.ui.register(fresh, "design")
        self.assertIsNone(self.ui.heartbeat(fresh, "design")["command"])
        with self.assertRaises(WorkbenchUIError) as raised:
            self.ui.ack(fresh, command["command_id"], "applied", "analysis")
        self.assertEqual(raised.exception.code, "COMMAND_UNKNOWN")

    def test_request_id_idempotent_and_conflict(self):
        self.ui.register(self.session, "design")
        first = self.ui.navigate(self.session, "analysis", "request_same")
        again = self.ui.navigate(self.session, "analysis", "request_same")
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["command_id"], first["command_id"])
        with self.assertRaises(WorkbenchUIError) as raised:
            self.ui.navigate(self.session, "workflow", "request_same")
        self.assertEqual(raised.exception.code, "REQUEST_ID_CONFLICT")

    def test_one_pending_command_and_expiry_never_replays(self):
        self.ui.register(self.session, "design")
        first = self.ui.navigate(self.session, "analysis", "request_pending")
        with self.assertRaises(WorkbenchUIError) as raised:
            self.ui.navigate(self.session, "workflow", "request_second")
        self.assertEqual(raised.exception.code, "COMMAND_PENDING")
        self.clock.value += COMMAND_TTL_MS / 1000 + 1
        with self.assertRaises(WorkbenchUIError) as raised:
            self.ui.ack(self.session, first["command_id"], "applied", "analysis")
        self.assertEqual(raised.exception.code, "COMMAND_EXPIRED")
        status = self.ui.status()
        self.assertEqual(status["commands"][0]["status"], "expired")

    def test_unknown_offline_and_bad_page(self):
        with self.assertRaises(WorkbenchUIError) as raised:
            self.ui.navigate("ui_missing_0001", "analysis", "request_missing")
        self.assertEqual(raised.exception.code, "UI_SESSION_UNKNOWN")
        self.ui.register(self.session, "design")
        self.clock.value += SESSION_LEASE_MS / 1000 + 1
        with self.assertRaises(WorkbenchUIError) as raised:
            self.ui.navigate(self.session, "analysis", "request_offline")
        self.assertEqual(raised.exception.code, "UI_SESSION_OFFLINE")
        with self.assertRaises(WorkbenchUIError) as raised:
            self.ui.navigate(self.session, "settings", "request_badpage")
        self.assertEqual(raised.exception.code, "UNKNOWN_PAGE")

    def test_actual_page_mismatch_rejected_and_reason(self):
        self.ui.register(self.session, "design")
        command = self.ui.navigate(self.session, "analysis", "request_mismatch")
        with self.assertRaises(WorkbenchUIError) as raised:
            self.ui.ack(self.session, command["command_id"], "applied", "workflow")
        self.assertEqual(raised.exception.code, "ACTUAL_PAGE_MISMATCH")
        receipt = self.ui.ack(self.session, command["command_id"], "rejected", "design", "用户未切换")["receipt"]
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["reason"], "用户未切换")


class ClientValidationTests(unittest.TestCase):
    def test_bad_port_is_url_error_not_uncaught(self):
        with patch.dict(os.environ, {"OPTIFORGE_SERVICE_URL": "http://127.0.0.1:99999/",
                                     "OPTIFORGE_SERVICE_FILE": "unused.json"}, clear=False):
            with self.assertRaises(WorkbenchControlError) as raised:
                WorkbenchControlClient.from_environment()
        self.assertEqual(raised.exception.code, "WORKBENCH_URL_INVALID")

    def test_malformed_credentials_are_structured(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "service.json"
            for payload in ([], {"token": 3, "service_instance_id": "i", "protocol_version": 2},
                            {"token": "t", "service_instance_id": "i"},
                            {"token": "t", "service_instance_id": "i", "protocol_version": True},
                            {"token": "t", "service_instance_id": "i", "protocol_version": 2, "url": "http://127.0.0.1:1/"}):
                path.write_text(json.dumps(payload), encoding="utf-8")
                with patch.dict(os.environ, {"OPTIFORGE_SERVICE_URL": "http://127.0.0.1:1/",
                                             "OPTIFORGE_SERVICE_FILE": str(path)}, clear=False):
                    with self.assertRaises(WorkbenchControlError) as raised:
                        WorkbenchControlClient.from_environment()
                self.assertIn(raised.exception.code, {"WORKBENCH_CREDENTIALS_INVALID",
                                                      "WORKBENCH_PROTOCOL_INCOMPATIBLE",
                                                      "WORKBENCH_CREDENTIALS_MISMATCH"})

    def test_redirect_is_refused_without_following(self):
        hits = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                hits.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/evil")
                self.end_headers()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            with patch.dict(os.environ, {"OPTIFORGE_SERVICE_URL": url,
                                         "OPTIFORGE_SERVICE_FILE": "unused.json"}, clear=False):
                with patch.object(Path, "read_text", return_value=json.dumps({
                        "token": "t", "service_instance_id": "i", "protocol_version": 2, "url": url})):
                    client = WorkbenchControlClient.from_environment()
            with self.assertRaises(WorkbenchControlError):
                client.status()
            self.assertNotIn("/evil", hits)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


class HTTPControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workbench-control-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.credentials = self.root / "service.json"
        self.engine = EngineWorker({"zemax_get_workbench_snapshot": lambda: {
            "status": "success", "schema_version": 1, "revision": "r1",
            "model": {"surfaces": []}, "layout": {}}}, metadata=lambda: {
            "active_project": "control_test", "model_project": "control_test", "proposal_confirmed": False})
        self.server = WorkbenchHTTPServer(("127.0.0.1", 0), self.engine, dist=self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        write_service_file(self.server, self.credentials)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.env = {"OPTIFORGE_SERVICE_URL": self.url, "OPTIFORGE_SERVICE_FILE": str(self.credentials)}
        self.instance = self.engine.state.context()["service_instance_id"]
        self.session = "ui_session_http"

    def stop(self):
        self.engine.state.close()
        self.server.shutdown()
        self.server.server_close()
        self.engine.executor.shutdown()

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request("POST", path, json.dumps(body), {"X-OptiForge-Token": self.server.token,
                                                      "Content-Type": "application/json"})
        response = conn.getresponse()
        result = json.loads(response.read())
        conn.close()
        return response.status, result

    def client(self):
        with patch.dict(os.environ, self.env, clear=False):
            return WorkbenchControlClient.from_environment()

    def test_status_has_no_token_and_works_without_frontend(self):
        status = self.client().status()
        self.assertEqual(status["protocol_version"], 2)
        self.assertEqual(status["service_instance_id"], self.instance)
        self.assertNotIn("token", json.dumps(status).lower())
        self.assertEqual(status["frontends"], [])

    def test_missing_config_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(WorkbenchControlError) as raised:
                WorkbenchControlClient.from_environment()
        self.assertEqual(raised.exception.code, "WORKBENCH_CONFIG_MISSING")

    def test_navigate_heartbeat_ack_applied(self):
        status, _ = self.post("/api/ui/session", {"protocol_version": 2, "service_instance_id": self.instance, "ui_session_id": self.session, "page": "design"})
        self.assertEqual(status, 200)
        client = self.client()
        accepted = client.navigate(self.instance, self.session, "design", "request_http")
        self.assertEqual(accepted["status"], "accepted")
        command_id = accepted["command_id"]
        _, beat = self.post("/api/ui/heartbeat", {"protocol_version": 2, "service_instance_id": self.instance, "ui_session_id": self.session, "page": "design"})
        self.assertEqual(beat["command"]["command_id"], command_id)
        _, ack = self.post("/api/ui/ack", {"protocol_version": 2, "service_instance_id": self.instance, "ui_session_id": self.session,
                                          "command_id": command_id, "status": "applied", "actual_page": "design"})
        self.assertEqual(ack["receipt"]["status"], "applied")
        receipt = client.status()["commands"][0]
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["actual_page"], "design")

    def test_instance_mismatch_and_wrong_token(self):
        client = self.client()
        with self.assertRaises(WorkbenchControlError) as raised:
            client.events("other-instance", 0, 0)
        self.assertEqual(raised.exception.code, "WORKBENCH_INSTANCE_MISMATCH")
        credentials = json.loads(self.credentials.read_text())
        credentials["token"] = "wrong"
        with patch.dict(os.environ, self.env, clear=False):
            bad = WorkbenchControlClient(self.url, credentials)
        with self.assertRaises(WorkbenchControlError) as raised:
            bad.status()
        self.assertEqual(raised.exception.code, "WORKBENCH_CREDENTIALS_MISMATCH")

    def test_ui_requires_protocol_and_instance(self):
        status, result = self.post("/api/ui/session", {"ui_session_id": self.session, "page": "design"})
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "WORKBENCH_PROTOCOL_INCOMPATIBLE")
        status, result = self.post("/api/ui/session", {
            "protocol_version": 2, "service_instance_id": "other", "ui_session_id": self.session, "page": "design"})
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "SERVICE_INSTANCE_MISMATCH")

    def test_analysis_page_is_always_addressable(self):
        status, result = self.post("/api/ui/session", {
            "protocol_version": 2, "service_instance_id": self.instance,
            "ui_session_id": self.session, "page": "analysis"})
        self.assertEqual(status, 200, result)
        heartbeat = self.post("/api/ui/heartbeat", {
            "protocol_version": 2, "service_instance_id": self.instance,
            "ui_session_id": self.session, "page": "analysis"})
        self.assertEqual(heartbeat[0], 200)

    def test_client_rejects_non_string_page(self):
        with self.assertRaises(WorkbenchControlError) as raised:
            self.client().navigate(self.instance, self.session, {"page": "analysis"}, "request_dict_page")
        self.assertEqual(raised.exception.code, "UNKNOWN_PAGE")

    def test_unsupported_workflow_page_is_explicit(self):
        status, result = self.post("/api/ui/session", {
            "protocol_version": 2, "service_instance_id": self.instance, "ui_session_id": self.session, "page": "workflow"})
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "PAGE_UNAVAILABLE")

    def test_malformed_page_types_are_structured_errors(self):
        for page in ({"bad": 1}, ["design"], 3, None):
            status, result = self.post("/api/ui/session", {
                "protocol_version": 2, "service_instance_id": self.instance,
                "ui_session_id": self.session, "page": page})
            self.assertEqual(status, 409)
            self.assertIn(result["code"], {"UNKNOWN_PAGE", "INVALID_UI_SESSION_ID"})
        status, _ = self.post("/api/ui/session", {
            "protocol_version": 2, "service_instance_id": self.instance, "ui_session_id": self.session, "page": "design"})
        self.assertEqual(status, 200)

    def test_terminal_navigation_and_duplicate_ack_are_idempotent(self):
        self.post("/api/ui/session", {"protocol_version": 2, "service_instance_id": self.instance,
                                      "ui_session_id": self.session, "page": "design"})
        accepted = self.client().navigate(self.instance, self.session, "design", "request_terminal")
        command_id = accepted["command_id"]
        self.post("/api/ui/ack", {"protocol_version": 2, "service_instance_id": self.instance,
                                  "ui_session_id": self.session, "command_id": command_id,
                                  "status": "applied", "actual_page": "design"})
        repeat = self.client().navigate(self.instance, self.session, "design", "request_terminal")
        self.assertEqual(repeat["status"], "applied")
        self.assertTrue(repeat["idempotent"])
        self.assertEqual(repeat["command_id"], command_id)
        duplicate = self.post("/api/ui/ack", {"protocol_version": 2, "service_instance_id": self.instance,
                                              "ui_session_id": self.session, "command_id": command_id,
                                              "status": "applied", "actual_page": "design"})[1]
        self.assertTrue(duplicate["idempotent"])
        status = self.client().status()
        frontend = next(item for item in status["frontends"] if item["ui_session_id"] == self.session)
        self.assertEqual(frontend["page"], "design")
        self.assertEqual(status["commands"][0]["actual_page"], "design")

    def test_events_reject_bool_cursor(self):
        with self.assertRaises(WorkbenchControlError) as raised:
            self.client().events(self.instance, True, 0)
        self.assertEqual(raised.exception.code, "INVALID_EVENT_CURSOR")

    def test_mcp_tool_functions_roundtrip(self):
        import server
        with patch.dict(os.environ, self.env, clear=False):
            status = json.loads(server.optiforge_workbench_status())
            self.assertEqual(status["service_instance_id"], self.instance)
            self.assertEqual(status["frontends"], [])
            self.assertNotIn("token", json.dumps(status).lower())
            self.post("/api/ui/session", {"protocol_version": 2, "service_instance_id": self.instance, "ui_session_id": self.session, "page": "design"})
            accepted = json.loads(server.optiforge_workbench_navigate(
                self.instance, self.session, "design", "request_tool"))
            self.assertEqual(accepted["status"], "accepted")
            command_id = accepted["command_id"]
            self.post("/api/ui/ack", {"protocol_version": 2, "service_instance_id": self.instance, "ui_session_id": self.session,
                                      "command_id": command_id, "status": "applied", "actual_page": "design"})
            receipt = json.loads(server.optiforge_workbench_status())["commands"][0]
            self.assertEqual(receipt["status"], "applied")
            events = json.loads(server.optiforge_workbench_events(self.instance, 0, 0))
            self.assertEqual(events["service_instance_id"], self.instance)

    def test_events_reuse_existing_stream(self):
        client = self.client()
        result = client.events(self.instance, 0, 0)
        self.assertEqual(result["service_instance_id"], self.instance)
        self.assertIsInstance(result["events"], list)
        self.assertIn("next_seq", result)
        reset = client.events(self.instance, 999999, 0)
        self.assertTrue(reset["reset_required"])
        self.assertEqual(reset["events"], [])


if __name__ == "__main__":
    unittest.main()
