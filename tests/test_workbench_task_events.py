"""Bounded task_changed events derived from successful public workflow results."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workbench_engine import EngineWorker
from core.workbench_service import WorkbenchHTTPServer
from core.workbench_state import TASK_FIELDS, WorkbenchState
from core import design_workflow as f
import tools
from tools import project_manager as projects


def task(request_id="request_0001", status="pending", version=1, kind="initial_proposal", **extra):
    value = {
        "project_name": "task_event_project",
        "request_id": request_id,
        "kind": kind,
        "status": status,
        "version": version,
        "stale": False,
        "lease_expired": False,
        "user_text": "研究需求全文",
        "result_text": "不应出现在事件中",
        "token_hash": "secret-hash",
        "lease_token": "secret-lease",
        "worker_id": "worker-secret",
    }
    value.update(extra)
    return value


class TaskEventTests(unittest.TestCase):
    def setUp(self):
        self.state = WorkbenchState(event_limit=8, history_limit=2, queue_limit=8)
        self.project = "task_event_project"
        self.registry = {
            "zemax_design_workflow": lambda **arguments: {"status": "success", "tasks": [task()]},
            "zemax_list_design_tasks": lambda project_name: {"status": "success", "tasks": [task()]},
            "zemax_claim_design_task": lambda **arguments: {"status": "success", "task": task(status="claimed", version=2)},
            "zemax_update_design_task": lambda **arguments: {"status": "success", "task": task(status="completed", version=3)},
        }
        self.engine = EngineWorker(self.registry, state=self.state, native_enabled=False,
                                   metadata=lambda: {"active_project": self.project,
                                                     "model_project": self.project,
                                                     "proposal_confirmed": False})
        self.instance = self.state.context()["service_instance_id"]

    def tearDown(self):
        self.engine.executor.shutdown()
        self.state.close()

    def call(self, name, arguments=None):
        with patch("tools.project_manager.get_active_project_name", return_value=self.project), \
                patch("tools.system_tools.get_current_design_proposal", return_value={"user_confirmed_to_simulate": False}):
            return self.engine.call_envelope(name, arguments or {}, expected_context=self.state.context())

    def task_events(self):
        return [event for event in self.state.events if event.get("kind") == "task_changed"]

    def test_submit_emits_one_bounded_task_event_without_sensitive_fields(self):
        self.call("zemax_design_workflow", {"action": "submit", "payload": {
            "request_id": "request_0001", "kind": "initial_proposal", "user_text": "需求"}})
        events = self.task_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "task_changed")
        self.assertEqual(event["design_task"]["request_id"], "request_0001")
        self.assertEqual(event["design_task"]["status"], "pending")
        self.assertTrue(set(event["design_task"]).issubset(set(TASK_FIELDS)))
        encoded = json.dumps(event)
        for forbidden in ("secret-hash", "secret-lease", "worker-secret", "不应出现", "user_text", "result_text"):
            self.assertNotIn(forbidden, encoded)
        for key in ("seq", "service_instance_id", "state_version"):
            self.assertIn(key, event)

    def test_same_version_idempotent_write_does_not_repeat(self):
        arguments = {"action": "submit", "payload": {
            "request_id": "request_0001", "kind": "initial_proposal", "user_text": "需求"}}
        self.call("zemax_design_workflow", arguments)
        self.call("zemax_design_workflow", arguments)
        self.assertEqual(len(self.task_events()), 1)

    def test_claim_and_update_emit_status_changes_and_failure_does_not(self):
        self.call("zemax_claim_design_task", {"project_name": self.project, "request_id": "request_0001",
                                              "version": 1, "worker_id": "worker"})
        self.assertEqual(self.task_events()[-1]["design_task"]["status"], "claimed")
        self.call("zemax_update_design_task", {"project_name": self.project, "request_id": "request_0001",
                                               "version": 2, "lease_token": "token", "status": "completed",
                                               "result_proposal_hash": "hash"})
        self.assertEqual(self.task_events()[-1]["design_task"]["status"], "completed")
        self.registry["zemax_update_design_task"] = lambda **arguments: {"status": "error", "message": "rejected"}
        self.call("zemax_update_design_task", {"project_name": self.project, "request_id": "request_0001",
                                               "version": 3, "lease_token": "token", "status": "failed"})
        self.assertFalse([event for event in self.task_events()
                          if event["design_task"].get("status") == "failed"
                          and event["design_task"].get("version") == 3])

    def test_first_submit_with_history_marks_existing_states(self):
        self.registry["zemax_design_workflow"] = lambda **arguments: {
            "status": "success",
            "tasks": [
                task("old_completed", status="completed", version=4, stale=True),
                task("old_stale", status="pending", version=1, stale=True),
                task("new_request", status="pending", version=1),
            ]}
        self.call("zemax_design_workflow", {"action": "submit", "payload": {
            "request_id": "new_request", "kind": "initial_proposal", "user_text": "需求"}})
        events = self.task_events()
        self.assertEqual([event["design_task"]["request_id"] for event in events],
                         ["old_completed", "old_stale", "new_request"])
        self.assertEqual(events[0]["design_task"]["status"], "completed")
        self.assertTrue(events[0]["design_task"]["stale"])
        self.assertTrue(events[1]["design_task"]["stale"])
        self.assertEqual(events[2]["design_task"]["status"], "pending")

    def test_task_cache_evicts_old_signatures_and_reset_contract(self):
        for index in range(300):
            self.state.publish_task_changes([task(f"request_{index:04d}")])
        self.assertEqual(len(self.state.task_signatures), 256)
        self.assertNotIn(("task_event_project", "request_0000"), self.state.task_signatures)
        self.assertLessEqual(len(self.state.events), 8)
        new_state = WorkbenchState(event_limit=8)
        reset = new_state.wait_events(self.instance, 0, 0)
        self.assertTrue(reset["reset_required"])
        self.assertEqual(reset["events"], [])

    def test_read_only_list_does_not_emit_task_changed(self):
        self.call("zemax_list_design_tasks", {"project_name": self.project})
        self.assertEqual(self.task_events(), [])

    def test_event_history_and_task_cache_are_bounded_and_reset_visible(self):
        for index in range(10):
            self.registry["zemax_design_workflow"] = (
                lambda index=index, **arguments: {"status": "success", "tasks": [task(f"request_{index:04d}")]})
            self.call("zemax_design_workflow", {"action": "submit", "payload": {
                "request_id": f"request_{index:04d}", "kind": "initial_proposal", "user_text": "需求"}})
        self.assertEqual(len(self.state.events), 8)
        self.assertLessEqual(len(self.state.task_signatures), 256)
        reset = self.state.wait_events(self.instance, 0, 0)
        self.assertTrue(reset["reset_required"])
        self.assertEqual(reset["events"], [])
        old_instance = self.instance
        new_state = WorkbenchState(event_limit=8)
        self.assertNotEqual(new_state.context()["service_instance_id"], old_instance)
        self.assertEqual(new_state.read()["event_seq"], 0)

    def test_workflow_retry_and_confirm_events(self):
        self.registry["zemax_design_workflow"] = lambda **arguments: {
            "status": "success", "tasks": [task(status="pending", version=2)]}
        self.call("zemax_design_workflow", {"action": "retry", "payload": {"request_id": "request_0001", "version": 1}})
        self.assertEqual(self.task_events()[-1]["design_task"]["version"], 2)
        self.registry["zemax_design_workflow"] = lambda **arguments: {
            "status": "success", "tasks": [task(status="pending", version=1, kind="model_handoff", request_id="handoff_0001")]}
        self.call("zemax_design_workflow", {"action": "confirm", "payload": {"request_id": "handoff_0001"}})
        self.assertEqual(self.task_events()[-1]["design_task"]["kind"], "model_handoff")

    def test_workflow_view_does_not_emit(self):
        self.registry["zemax_design_workflow"] = lambda **arguments: {"status": "success", "tasks": [task()]}
        self.call("zemax_design_workflow", {"action": "view"})
        self.assertEqual(self.task_events(), [])


class RealCollabTaskEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-event-real-")
        self.addCleanup(self.temp.cleanup)
        for name, value in [
            ("OUTPUT_BASE_DIR", self.temp.name),
            ("ACTIVE_PROJECT_FILE", str(Path(self.temp.name) / ".active.json")),
        ]:
            patcher = patch.object(projects, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        native = patch("core.zos_session.ZOSSession.get_instance",
                       side_effect=AssertionError("unexpected native session"))
        native.start()
        self.addCleanup(native.stop)
        projects.set_active_project("real_task_project")
        self.state = WorkbenchState(event_limit=8)
        self.state.publish(metadata={"active_project": "real_task_project",
                                     "model_project": None, "proposal_confirmed": False},
                           snapshot=None, validity="unavailable", context_valid=True)
        self.engine = EngineWorker({
            "zemax_design_workflow": tools.zemax_design_workflow,
            "zemax_claim_design_task": tools.zemax_claim_design_task,
            "zemax_update_design_task": tools.zemax_update_design_task,
        }, state=self.state, native_enabled=False)
        self.project = "real_task_project"

    def tearDown(self):
        self.engine.executor.shutdown()
        self.state.close()

    def task_events(self):
        return [event for event in self.state.events if event.get("kind") == "task_changed"]

    def test_real_collaboration_engine_submit_claim_progress(self):
        specs = {"efl_mm": 50, "f_number": 2.8, "fov_or_sensor": "像面直径 11 mm",
                 "wavelength_range": "400–700 nm"}
        f.save_requirements(self.project, f.view(self.project)["context"], "imaging_lens",
                            specs, {key: "user" for key in specs},
                            user_prompt="隔离软件测试，非真实镜头设计")
        view = f.view(self.project)
        submit = self.engine.call_envelope("zemax_design_workflow", {
            "action": "submit", "project_name": self.project,
            "payload": {"request_id": "real_request_0001", "kind": "initial_proposal",
                        "user_text": "隔离软件测试，非真实镜头设计"},
            "expected_workflow": view["context"],
        }, expected_context=self.state.context())
        self.assertEqual(submit["result"]["status"], "success")
        submitted = self.task_events()[-1]["design_task"]
        self.assertEqual(submitted["request_id"], "real_request_0001")
        self.assertEqual(submitted["status"], "pending")
        claim = self.engine.call_envelope("zemax_claim_design_task", {
            "project_name": self.project, "request_id": submitted["request_id"],
            "version": submitted["version"], "worker_id": "real-software-test", "lease_seconds": 300,
        }, expected_context=self.state.context())
        self.assertEqual(claim["result"]["status"], "success")
        claimed = self.task_events()[-1]["design_task"]
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(claimed["version"], submitted["version"] + 1)
        progress = self.engine.call_envelope("zemax_update_design_task", {
            "project_name": self.project, "request_id": claimed["request_id"],
            "version": claimed["version"], "lease_token": claim["result"]["lease_token"],
            "status": "progress", "result_text": "隔离进度",
        }, expected_context=self.state.context())
        self.assertEqual(progress["result"]["status"], "success")
        updated = self.task_events()[-1]["design_task"]
        self.assertEqual(updated["status"], "claimed")
        self.assertEqual(updated["version"], claimed["version"] + 1)
        self.assertTrue(set(updated).issubset(set(TASK_FIELDS)))
        self.assertNotIn("lease_token", updated)
        self.assertNotIn("token_hash", updated)
        self.assertNotIn("result_text", updated)


class HTTPTaskEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-event-http-")
        self.addCleanup(self.temp.cleanup)
        self.state = WorkbenchState(event_limit=8)
        self.project = "http_task_project"
        self.registry = {"zemax_design_workflow": lambda **arguments: {
            "status": "success", "tasks": [task(request_id="http_request")]}}
        self.engine = EngineWorker(self.registry, state=self.state, native_enabled=False,
                                   metadata=lambda: {"active_project": self.project,
                                                     "model_project": self.project,
                                                     "proposal_confirmed": False})
        self.server = WorkbenchHTTPServer(("127.0.0.1", 0), self.engine, dist=Path(self.temp.name))
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.instance = self.state.context()["service_instance_id"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.engine.executor.shutdown()

    def test_api_events_exposes_task_changed(self):
        with patch("tools.project_manager.get_active_project_name", return_value=self.project), \
                patch("tools.system_tools.get_current_design_proposal",
                      return_value={"user_confirmed_to_simulate": False}):
            self.engine.call_envelope("zemax_design_workflow", {
                "action": "submit", "payload": {"request_id": "http_request",
                                                "kind": "initial_proposal", "user_text": "需求"}},
                expected_context=self.state.context())
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request("GET", f"/api/events?instance_id={self.instance}&after_seq=0&timeout_ms=0",
                     headers={"X-OptiForge-Token": self.server.token})
        response = conn.getresponse()
        payload = json.loads(response.read())
        conn.close()
        self.assertEqual(response.status, 200)
        task_events = [event for event in payload["events"] if event.get("kind") == "task_changed"]
        self.assertEqual(len(task_events), 1)
        self.assertEqual(task_events[0]["design_task"]["request_id"], "http_request")


if __name__ == "__main__":
    unittest.main()
