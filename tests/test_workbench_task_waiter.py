"""Read-only task pull timing, identity, candidate and network-boundary tests."""
from copy import deepcopy
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workbench_control import WorkbenchControlError
from core.workbench_task_waiter import WorkbenchTaskWaiter, HTTP_TIMEOUT_SECONDS, TASK_FIELDS


INSTANCE = "service_0001"
PROJECT = "task_pull_project"


def task(**extra):
    return {"project_name": PROJECT, "request_id": "request_0001", "kind": "initial_proposal",
            "status": "pending", "version": 1, "stale": False, "lease_expired": False,
            "requirements_revision": 1, "proposal_hash": None, "model_reference": None,
            "lease_token": "must-not-leak", "user_text": "private task content", **extra}


def workflow(tasks=None):
    return {
        "status": "success", "workflow_schema_version": 1, "service_instance_id": INSTANCE, "project_name": PROJECT,
        "context": {"project_name": PROJECT, "active_project": PROJECT, "selection_revision": 1,
                    "requirements_revision": 1, "proposal_hash": None, "proposal_revision": None,
                    "authorization_revision": 0},
        "draft": {"revision": 1, "approval": None}, "proposal": None, "proposal_bound": False,
        "tasks": tasks or [], "model_reference": None,
    }


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class Control:
    """An injected stream with real blocking time represented by a fake clock."""
    instance = INSTANCE
    url = "http://127.0.0.1:12345"
    token = "credential-only"

    def __init__(self, clock, view=None):
        self.clock = clock
        self.view = view or workflow()
        self.seq = 0
        self.history = []
        self.event_calls = []
        self.observations = []
        self.network_calls = []
        self.on_wait = None
        self.after_snapshot = None
        self.after_time = None
        self.reset = False
        self.closed = False
        self.status_calls = 0

    def engine_context(self):
        return {"service_instance_id": self.instance, "active_project": PROJECT,
                "state_version": 1, "model_id": None, "model_project": None}

    def status(self):
        self.status_calls += 1
        return {"protocol_version": 2, "service_instance_id": self.instance,
                "context": self.engine_context(), "capabilities": {"collaboration": True, "workflow_schema_version": 1}}

    def publish(self, kind, **extra):
        self.seq += 1
        self.history.append({"seq": self.seq, "kind": kind,
                             "service_instance_id": self.instance, "active_project": PROJECT, **extra})

    def observe(self, project, expected):
        assert project == PROJECT
        self.clock.value += 0.001
        operation = f"view_{len(self.observations)}"
        snapshot = deepcopy(self.view)
        self.observations.append((project, deepcopy(expected)))
        for kind in ("queued", "started", "state_changed", "finished"):
            self.publish(kind, operation_id=operation, tool="zemax_design_workflow")
        if self.after_snapshot:
            callback, self.after_snapshot = self.after_snapshot, None
            callback(self)
        return {"protocol_version": 2, "context": self.engine_context(),
                "operation_id": operation, "result": snapshot}

    def events(self, instance, cursor, timeout):
        self.event_calls.append((instance, cursor, timeout))
        reset, self.reset = self.reset, False
        if reset or cursor > self.seq:
            return {"protocol_version": 2, "service_instance_id": self.instance,
                    "events": [], "next_seq": self.seq, "reset_required": True}
        events = [deepcopy(item) for item in self.history if item["seq"] > cursor]
        if not events and timeout:
            if self.on_wait:
                self.on_wait(self, timeout)
            else:
                self.clock.value += timeout / 1000
                if self.after_time:
                    callback, self.after_time = self.after_time, None
                    callback(self)
            events = [deepcopy(item) for item in self.history if item["seq"] > cursor]
        return {"protocol_version": 2, "service_instance_id": self.instance,
                "events": events, "next_seq": self.seq, "reset_required": False, "closed": self.closed}

    def _verify_instance(self):
        self.network_calls.append(("verify",))

    def _open(self, request, timeout):
        self.network_calls.append((request, timeout))
        payload = json.loads(request.data)
        return self.observe(payload["arguments"]["project_name"], payload["expected_context"])


class WaiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.control = Control(self.clock)
        self.waiter = WorkbenchTaskWaiter(self.control, observe=self.control.observe, clock=self.clock)

    def wait(self, **kwargs):
        return self.waiter.wait_for_task(INSTANCE, PROJECT, **kwargs)

    def assert_code(self, code, function):
        with self.assertRaises(WorkbenchControlError) as raised:
            function()
        self.assertEqual(raised.exception.code, code)

    def test_existing_pending_is_found_without_wait_or_write(self):
        self.control.view = workflow([task()])
        result = self.wait()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["tasks"], [{key: task()[key] for key in TASK_FIELDS}])
        self.assertEqual(result["context"], self.control.view["context"])
        self.assertEqual(len(self.control.observations), 1)
        self.assertFalse(any(timeout for _, _, timeout in self.control.event_calls))
        for secret in ("must-not-leak", "private task content", "credential-only", "lease_token", "user_text"):
            self.assertNotIn(secret, json.dumps(result))

    def test_idle_really_waits_ignores_own_events_and_rechecks_at_deadline(self):
        result = self.wait(timeout_ms=20000)
        self.assertEqual(result["status"], "idle")
        self.assertEqual(len(self.control.observations), 2)  # entry + authoritative timeout
        self.assertEqual(len([call for call in self.control.event_calls if call[2] > 0]), 1)
        self.assertGreaterEqual(result["waited_ms"], 19990)
        self.assertLessEqual(result["waited_ms"], 20000)
        self.assertLess(result["elapsed_ms"], 20010)

    def test_task_arriving_during_long_poll_is_read_promptly(self):
        def arrive(control, timeout):
            control.clock.value += 0.25
            control.view = workflow([task()])
            control.publish("task_changed", design_task=task())
        self.control.on_wait = arrive
        result = self.wait()
        self.assertEqual(result["status"], "ready")
        self.assertLess(result["elapsed_ms"], 1000)
        self.assertEqual(len(self.control.observations), 2)

    def test_task_inserted_between_snapshot_and_event_drain_is_not_swallowed(self):
        def arrive(control):
            control.view = workflow([task()])
            control.publish("task_changed", design_task=task())
        self.control.after_snapshot = arrive
        result = self.wait()
        self.assertEqual(result["status"], "ready")
        self.assertFalse(any(timeout for _, _, timeout in self.control.event_calls))
        self.assertEqual(len(self.control.observations), 2)

    def test_deadline_authority_finds_a_task_without_notification(self):
        self.control.after_time = lambda control: setattr(control, "view", workflow([task()]))
        result = self.wait(timeout_ms=1000)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["tasks"][0]["request_id"], "request_0001")
        self.assertEqual(len(self.control.observations), 2)

    def test_repeated_unrelated_events_do_not_reread_or_extend_deadline(self):
        def unrelated(control, timeout):
            control.clock.value += min(0.1, timeout / 1000)
            control.publish("finished", tool="zemax_design_workflow", operation_id="other-view")
        self.control.on_wait = unrelated
        result = self.wait(timeout_ms=1000)
        self.assertEqual(result["status"], "idle")
        self.assertEqual(len(self.control.observations), 2)
        self.assertLess(result["elapsed_ms"], 1010)
        self.assertLessEqual(len(self.control.event_calls), 25)

    def test_reread_http_time_cannot_start_a_new_wait_after_deadline(self):
        def hint(control):
            control.publish("task_changed", design_task=task(status="completed"))
        self.control.after_snapshot = hint
        original = self.control.observe
        def slow_reread(project, expected):
            if self.control.observations:
                self.clock.value += 0.2
            return original(project, expected)
        self.waiter.observe = slow_reread
        result = self.wait(timeout_ms=100)
        self.assertEqual(result["status"], "idle")
        self.assertFalse(any(timeout for _, _, timeout in self.control.event_calls))

    def test_cursor_reset_and_historical_other_project_use_current_authority(self):
        self.control.publish("state_changed", active_project="former_project")
        self.control.view = workflow([task()])
        result = self.wait(after_seq=999999, timeout_ms=0)
        self.assertTrue(result["reset_required"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["next_seq"], self.control.seq)

    def test_reset_during_wait_rechecks_snapshot(self):
        def reset(control, timeout):
            control.clock.value += 0.2
            control.view = workflow([task()])
            control.reset = True
        self.control.on_wait = reset
        result = self.wait()
        self.assertTrue(result["reset_required"])
        self.assertEqual(result["status"], "ready")

    def test_project_change_during_wait_fails_without_selecting(self):
        def changed(control, timeout):
            control.clock.value += 0.1
            control.publish("state_changed", active_project="other_project")
        self.control.on_wait = changed
        self.assert_code("WORKFLOW_PROJECT_MISMATCH", self.wait)
        self.assertEqual(len(self.control.observations), 1)

    def test_instance_change_during_wait_fails_without_retry(self):
        def replaced(control, timeout):
            control.clock.value += 0.1
            control.instance = "replacement"
            control.publish("state_changed")
        self.control.on_wait = replaced
        self.assert_code("WORKBENCH_INSTANCE_MISMATCH", self.wait)
        self.assertEqual(len(self.control.observations), 1)

    def test_closed_stream_and_connection_failure_are_explicit(self):
        self.control.closed = True
        self.assert_code("WORKBENCH_UNAVAILABLE", self.wait)
        self.control.closed = False
        with patch.object(self.control, "events", side_effect=WorkbenchControlError("WORKBENCH_UNAVAILABLE", "offline")):
            self.assert_code("WORKBENCH_UNAVAILABLE", self.wait)

    def test_strict_cursor_timeout_and_project_validation_before_network(self):
        for cursor in (True, 1.0, "1", -1, None):
            self.assert_code("INVALID_EVENT_CURSOR", lambda: self.wait(after_seq=cursor))
        for timeout in (True, 1.0, "1", -1, 20001, None):
            self.assert_code("INVALID_EVENT_TIMEOUT", lambda: self.wait(timeout_ms=timeout))
        for project in (None, "", "../escape", "a/b", "Bad Name", "CON", ".."):
            self.assert_code("WORKFLOW_PROJECT_INVALID", lambda: self.waiter.wait_for_task(INSTANCE, project))
        self.assert_code("WORKBENCH_INSTANCE_MISMATCH", lambda: self.waiter.wait_for_task("other", PROJECT))
        self.assertEqual(self.control.status_calls, 0)

    def test_incomplete_and_nonclaimable_tasks_are_not_candidates(self):
        entries = [task(status=status) for status in ("claimed", "completed", "failed", "superseded")]
        entries += [task(stale=True), task(lease_expired=True), task(stale=0), task(lease_expired=0),
                    task(project_name="other"), task(version=True), task(version=0), task(kind={}),
                    task(requirements_revision=2), task(request_id=" "), task(kind="unknown")]
        for flag in ("stale", "lease_expired"):
            missing = task(); del missing[flag]; entries.append(missing)
        self.control.view = workflow(entries)
        self.assertEqual(self.wait(timeout_ms=0)["tasks"], [])

    def test_handoff_requires_bound_explicit_authorization_and_matching_model(self):
        good = workflow([task(kind="model_handoff", proposal_hash="approved")])
        good["context"].update(proposal_hash="approved", proposal_revision=1)
        good["proposal_bound"] = True
        good["proposal"] = {"proposal_hash": "approved", "proposal_revision": 1,
                            "requirements_revision": 1, "user_confirmed_to_simulate": True}
        good["draft"]["approval"] = "approved"
        self.control.view = good
        self.assertEqual(self.wait(timeout_ms=0)["status"], "ready")
        mutations = [
            lambda view: view.update(proposal_bound=False),
            lambda view: view["proposal"].update(user_confirmed_to_simulate="true"),
            lambda view: view["proposal"].update(proposal_hash="other"),
            lambda view: view["proposal"].update(requirements_revision=2),
            lambda view: view["draft"].update(approval=None),
            lambda view: view["tasks"][0].update(proposal_hash="other"),
            lambda view: view.update(model_reference={"service_instance_id": "old", "model_project": PROJECT}),
            lambda view: view.update(model_reference={"service_instance_id": INSTANCE, "model_project": "other"}),
        ]
        for mutate in mutations:
            self.control.view = deepcopy(good); mutate(self.control.view)
            self.assertEqual(self.wait(timeout_ms=0)["tasks"], [])

    def test_surface_candidate_requires_exact_current_model_reference(self):
        reference = {"service_instance_id": INSTANCE, "model_project": PROJECT,
                     "model_id": "model", "model_revision": 3, "configuration": 1}
        self.control.view = workflow([task(kind="surface_question", model_reference=reference)])
        self.control.view["model_reference"] = reference
        self.assertEqual(self.wait(timeout_ms=0)["status"], "ready")
        self.control.view["model_reference"] = {**reference, "model_revision": 4}
        self.assertEqual(self.wait(timeout_ms=0)["status"], "idle")

    def test_instance_project_and_malformed_response_checks(self):
        for mutate, code in [
            (lambda envelope: envelope["context"].update(service_instance_id="other"), "WORKBENCH_INSTANCE_MISMATCH"),
            (lambda envelope: envelope["result"].update(service_instance_id="other"), "WORKBENCH_INSTANCE_MISMATCH"),
            (lambda envelope: envelope["result"]["context"].update(active_project="other"), "WORKFLOW_PROJECT_MISMATCH"),
            (lambda envelope: envelope["result"]["context"].pop("authorization_revision"), "WORKFLOW_RESPONSE_INVALID"),
            (lambda envelope: envelope["result"].update(tasks=None), "WORKFLOW_RESPONSE_INVALID"),
        ]:
            def observe(project, expected):
                result = self.control.observe(project, expected)
                mutate(result)
                return result
            waiter = WorkbenchTaskWaiter(self.control, observe=observe, clock=self.clock)
            self.assert_code(code, lambda: waiter.wait_for_task(INSTANCE, PROJECT, timeout_ms=0))

    def test_unknown_or_missing_workflow_schema_is_rejected_on_status_and_view(self):
        original_status = self.control.status
        for schema in (None, True, 2, "1"):
            status = original_status()
            status["capabilities"]["workflow_schema_version"] = schema
            with patch.object(self.control, "status", return_value=status):
                self.assert_code("WORKFLOW_SCHEMA_INCOMPATIBLE", lambda: self.wait(timeout_ms=0))
            self.control.view = workflow()
            self.control.view["workflow_schema_version"] = schema
            self.assert_code("WORKFLOW_SCHEMA_INCOMPATIBLE", lambda: self.wait(timeout_ms=0))
        self.control.view = workflow()
        del self.control.view["workflow_schema_version"]
        self.assert_code("WORKFLOW_SCHEMA_INCOMPATIBLE", lambda: self.wait(timeout_ms=0))

    def test_fixed_http_observation_uses_bounded_timeout_and_no_other_tool(self):
        self.control.view = workflow([task()])
        waiter = WorkbenchTaskWaiter(self.control, clock=self.clock)
        result = waiter.wait_for_task(INSTANCE, PROJECT, timeout_ms=0)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.control.network_calls[0], ("verify",))
        request, timeout = self.control.network_calls[1]
        self.assertEqual(timeout, HTTP_TIMEOUT_SECONDS)
        self.assertEqual(request.full_url, self.control.url + "/api/tool")
        self.assertEqual(json.loads(request.data)["arguments"], {"action": "view", "project_name": PROJECT})
        self.assertEqual(json.loads(request.data)["name"], "zemax_design_workflow")


class ServerToolTests(unittest.TestCase):
    def test_mcp_schema_boundary_rejects_coercion_before_configuration_access(self):
        import server
        from mcp.server.mcpserver.exceptions import ToolError
        async def invalid_arguments():
            for field, value in (("after_seq", True), ("after_seq", 1.0), ("after_seq", "1"),
                                 ("timeout_ms", True), ("timeout_ms", "10"),
                                 ("service_instance_id", 1), ("project_name", 1)):
                arguments = {"service_instance_id": INSTANCE, "project_name": PROJECT,
                             "after_seq": 0, "timeout_ms": 0, field: value}
                with patch.object(server.WorkbenchControlClient, "from_environment") as configured:
                    with self.assertRaises(ToolError):
                        await server.app.call_tool("optiforge_workbench_wait_for_task", arguments)
                configured.assert_not_called()
        asyncio.run(invalid_arguments())

    def test_missing_configuration_never_falls_back_to_local_workflow(self):
        import server
        with patch.dict(os.environ, {}, clear=True), patch.object(server, "_design_workflow") as local:
            result = json.loads(server.optiforge_workbench_wait_for_task(INSTANCE, PROJECT, timeout_ms=0))
        self.assertEqual(result["code"], "WORKBENCH_CONFIG_MISSING")
        local.assert_not_called()

    def test_thin_tool_uses_control_client_even_when_import_alias_is_local(self):
        import server
        clock = Clock(); control = Control(clock, workflow([task()]))
        with patch.object(server.WorkbenchControlClient, "from_environment", return_value=control), \
                patch.object(server, "_design_workflow", side_effect=AssertionError("local fallback")):
            result = json.loads(server.optiforge_workbench_wait_for_task(INSTANCE, PROJECT, timeout_ms=0))
        self.assertEqual(result["status"], "ready")
        self.assertTrue(control.network_calls)


if __name__ == "__main__":
    unittest.main()
