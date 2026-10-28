"""State and operation-boundary regressions. Fake tools only; no OpticStudio."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workbench_effects import TOOL_EFFECTS, ToolEffect, check_tool_coverage
from core.workbench_engine import EngineWorker
from core.workbench_state import WorkbenchState


def fixture_snapshot(value=1):
    return {"status": "success", "schema_version": 1, "revision": str(value), "observed_at": "test",
            "model": {"surfaces": [{"index": 0}], "name": "fixture", "value": value}, "layout": {}}


class StateTests(unittest.TestCase):
    def publish(self, state, value=1, **options):
        return state.publish(metadata={"active_project": "a", "model_project": "a", "proposal_confirmed": False},
                             snapshot=fixture_snapshot(value), validity="valid", **options)

    def test_all_exports_have_exactly_one_effect(self):
        import tools
        check_tool_coverage(tools.__all__)
        with self.assertRaises(RuntimeError):
            check_tool_coverage([*tools.__all__, "future_tool"])
        for name in ("zemax_export_cad", "zemax_export_optical_drawing", "zemax_export_prescription_for_cad"):
            self.assertTrue(TOOL_EFFECTS[name].protected)
            self.assertEqual(TOOL_EFFECTS[name].kind, "context")

    def test_identity_counters_snapshot_and_cursor_are_atomic(self):
        state = WorkbenchState()
        self.publish(state, replace=True)
        first = state.read()
        self.publish(state)
        self.assertEqual(first, state.read())
        self.publish(state, model_changed=True)
        second = state.read()
        self.assertEqual(second["model_id"], first["model_id"])
        self.assertEqual(second["model_revision"], first["model_revision"] + 1)
        self.assertEqual(second["state_version"], first["state_version"] + 1)
        self.assertEqual(second["snapshot"]["sync"], second["snapshot_context"])
        self.assertEqual(second["snapshot_context"]["state_version"], second["state_version"])
        self.publish(state, replace=True)
        self.assertNotEqual(state.read()["model_id"], first["model_id"])
        second["snapshot"]["model"]["value"] = 99
        self.assertEqual(state.read()["snapshot"]["model"]["value"], 1)

    def test_tasks_do_not_change_versions_and_pending_survives_history_trimming(self):
        state = WorkbenchState(history_limit=2)
        waiting = state.enqueue("waiting", "mcp")
        for _ in range(4):
            operation = state.enqueue("read", "mcp")
            state.start(operation)
            state.finish(operation, "succeeded", {"status": "warning"})
        data = state.read()
        self.assertEqual(data["state_version"], 0)
        self.assertEqual(len(data["tasks"]["recent"]), 2)
        self.assertEqual(data["tasks"]["pending"][0]["operation_id"], waiting)

    def test_event_overflow_future_cursor_and_restart_request_reset(self):
        state = WorkbenchState(event_limit=2)
        operation = state.enqueue("read", "mcp")
        state.start(operation)
        state.finish(operation, "succeeded", {})
        instance = state.context()["service_instance_id"]
        for after, identity in ((0, instance), (99, instance), (3, "old-instance"), (-1, instance)):
            self.assertTrue(state.wait_events(identity, after, 0)["reset_required"])
        events = state.wait_events(instance, 1, 0)
        self.assertEqual([event["seq"] for event in events["events"]], [2, 3])
        self.assertFalse(events["reset_required"])

    def test_queue_is_bounded_and_shutdown_wakes_waiter(self):
        state = WorkbenchState(queue_limit=1)
        self.assertIsNotNone(state.enqueue("one", "mcp"))
        self.assertIsNone(state.enqueue("two", "mcp"))
        with ThreadPoolExecutor() as pool:
            future = pool.submit(state.wait_events, state.context()["service_instance_id"], state.seq, 20000)
            state.close()
            self.assertTrue(future.result(timeout=1)["closed"])

    def test_context_requires_all_fields_and_checks_project_and_instance(self):
        state = WorkbenchState()
        original = state.context()
        self.assertEqual(state.validate_context(None)["code"], "WORKBENCH_CONTEXT_REQUIRED")
        self.assertIsNone(state.validate_context(original))
        for key in original:
            changed = {**original, key: "different"}
            self.assertEqual(state.validate_context(changed)["code"], "WORKBENCH_CONTEXT_STALE")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.value = 1
        self.hidden = 0
        self.session = object()
        self.project = "a"
        self.model_project = "a"
        self.confirmed = False
        self.captures = 0
        self.capture_failure = False
        self.writes = 0
        def mutate():
            self.writes += 1
            self.value += 1
            return {"status": "success"}
        self.registry = {"zemax_get_workbench_snapshot": self.capture, "zemax_get_system_data": lambda: {"status": "success"},
                         "zemax_surface_operations": mutate, "zemax_set_surface_params": self.hidden_write,
                         "zemax_load_file": lambda filepath: {"status": "success"},
                         "zemax_save_file": lambda: {"status": "success"},
                         "zemax_lookup_manual": lambda: {"answer": "legacy response without status"}}
        self.engine = EngineWorker(self.registry, metadata=self.metadata)
        self.addCleanup(self.engine.executor.shutdown)
        self.engine.call("zemax_get_workbench_snapshot")

    def metadata(self):
        return {"active_project": self.project, "model_project": self.model_project,
                "proposal_confirmed": self.confirmed, "session_identity": self.session, "recovery_error": None}

    def capture(self):
        self.captures += 1
        if self.capture_failure:
            raise RuntimeError("fixture capture failed")
        return fixture_snapshot(self.value)

    def hidden_write(self):
        self.hidden += 1
        return {"status": "success"}

    def call_write(self, name="zemax_surface_operations", **arguments):
        return self.engine.call(name, arguments, expected_context=self.engine.state.context())

    def test_read_heartbeat_and_knowledge_do_not_advance_versions_or_create_session(self):
        before = self.engine.state.read()
        for _ in range(3):
            self.engine.call("zemax_get_workbench_snapshot")
            self.engine.state.wait_events(before["service_instance_id"], self.engine.state.seq, 0)
        self.engine.call("zemax_lookup_manual")
        after = self.engine.state.read()
        for key in ("model_id", "model_revision", "state_version"):
            self.assertEqual(before[key], after[key])
        with patch("core.zos_session.ZOSSession.get_instance", side_effect=AssertionError("native opened")):
            empty = EngineWorker({"zemax_lookup_manual": self.registry["zemax_lookup_manual"]})
            self.addCleanup(empty.executor.shutdown)
            self.assertIn("answer", empty.call("zemax_lookup_manual"))
            self.assertIsNone(empty.state.read()["model_id"])

    def test_write_and_invisible_changes_publish_once_each(self):
        old = self.engine.state.read()
        self.call_write()
        middle = self.engine.state.read()
        self.call_write("zemax_set_surface_params")
        latest = self.engine.state.read()
        self.assertEqual(middle["snapshot"]["revision"], latest["snapshot"]["revision"])
        self.assertEqual(latest["model_revision"], old["model_revision"] + 2)
        self.assertEqual(latest["state_version"], old["state_version"] + 2)
        events = self.engine.state.wait_events(old["service_instance_id"], old["event_seq"], 0)["events"]
        self.assertEqual([row["kind"] for row in events], ["queued", "started", "state_changed", "finished"] * 2)
        self.assertEqual(self.captures, 3)

    def test_same_path_load_new_and_save_keep_correct_identity(self):
        old = self.engine.state.context()
        for _ in range(2):
            self.call_write("zemax_load_file", filepath="same.zmx")
            new = self.engine.state.context()
            self.assertNotEqual(old["model_id"], new["model_id"])
            old = new
        self.call_write("zemax_save_file")
        self.assertEqual(old["model_id"], self.engine.state.context()["model_id"])
        for tool in ("zemax_new_file", "zemax_load_template"):
            self.registry[tool] = lambda: {"status": "success"}
            self.call_write(tool)
            self.assertNotEqual(old["model_id"], self.engine.state.context()["model_id"])
            old = self.engine.state.context()

    def test_project_metadata_read_does_not_reopen_a_closed_native_session(self):
        self.session = None
        self.registry["zemax_get_project"] = lambda: {"project": self.project}
        with patch.object(self.engine, "_capture", side_effect=AssertionError("metadata query reopened native engine")):
            self.engine.call("zemax_get_project")
        self.assertIsNone(self.engine.state.read()["snapshot"])
        self.assertEqual(self.engine.state.read()["validity"], "unavailable")

    def test_two_clients_and_queued_old_target_cannot_write_new_model(self):
        old = self.engine.state.context()
        self.call_write("zemax_load_file", filepath="b.zmx")
        result = self.engine.call("zemax_surface_operations", expected_context=old)
        self.assertEqual(result["code"], "WORKBENCH_CONTEXT_STALE")
        self.assertEqual(self.writes, 0)
        self.assertEqual(self.engine.call("zemax_surface_operations")["code"], "WORKBENCH_CONTEXT_REQUIRED")

    def test_context_is_checked_at_execution_not_at_enqueue(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_load(filepath):
            entered.set()
            self.assertTrue(release.wait(2))
            return {"status": "success"}
        self.registry["zemax_load_file"] = blocked_load
        context = self.engine.state.context()
        _, loading = self.engine.submit("zemax_load_file", {"filepath": "b"}, expected_context=context)
        self.assertTrue(entered.wait(1))
        _, writing = self.engine.submit("zemax_surface_operations", expected_context=context)
        release.set()
        loading.result(2)
        self.assertEqual(writing.result(2)["result"]["code"], "WORKBENCH_CONTEXT_STALE")
        self.assertEqual(self.writes, 0)

    def test_gate_rejection_does_not_read_or_advance_and_warning_is_success(self):
        self.registry["zemax_surface_operations"] = lambda: {"status": "error", "code": "PROPOSAL_NOT_CONFIRMED"}
        before = self.engine.state.read()
        self.call_write()
        after = self.engine.state.read()
        self.assertEqual(before["state_version"], after["state_version"])
        self.assertEqual(self.captures, 1)
        self.registry["zemax_run_optimization"] = lambda: {"status": "warning", "message": "No variables"}
        self.assertEqual(self.call_write("zemax_run_optimization")["status"], "warning")
        self.assertEqual(self.engine.state.read()["tasks"]["recent"][0]["status"], "succeeded")

    def test_save_quality_gate_rejects_without_invalidating_known_model(self):
        self.registry["zemax_save_file"] = lambda: {"status": "error", "code": "QUALITY_GATE_FAILURE", "message": "pre-save quality gate"}
        before = self.engine.state.read()
        self.assertEqual(self.call_write("zemax_save_file")["code"], "QUALITY_GATE_FAILURE")
        after = self.engine.state.read()
        for key in ("state_version", "model_revision", "model_id", "validity"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after["tasks"]["recent"][0]["status"], "rejected")

    def test_failed_rollback_keeps_identity_and_publishes_recovered_snapshot(self):
        self.registry["zemax_surface_operations"] = lambda: {"status": "error", "rolled_back": True}
        before = self.engine.state.read()
        self.call_write()
        after = self.engine.state.read()
        self.assertEqual(after["model_id"], before["model_id"])
        self.assertGreater(after["model_revision"], before["model_revision"])
        self.assertEqual(after["validity"], "valid")
        self.assertEqual(after["tasks"]["recent"][0]["status"], "failed")

    def test_calculation_and_configuration_restore_failure_stays_uncertain_after_refresh(self):
        for tool in ("zemax_confocal_response", "zemax_run_fft_mtf"):
            self.registry[tool] = lambda: {"status": "error", "message": "restore unknown"}
            self.call_write(tool)
            self.assertEqual(self.engine.state.read()["validity"], "uncertain")
            self.engine.call("zemax_get_workbench_snapshot")
            self.assertEqual(self.engine.state.read()["validity"], "uncertain")
            self.call_write("zemax_load_file", filepath="clean.zmx")
            self.assertEqual(self.engine.state.read()["validity"], "valid")

    def test_snapshot_failure_keeps_write_success_and_never_repeats_write(self):
        self.capture_failure = True
        result = self.call_write()
        self.assertEqual(result["status"], "success")
        failed = self.engine.state.read()
        self.assertIsNone(failed["snapshot"])
        self.assertEqual(failed["validity"], "unavailable")
        self.assertIn("视图读取失败", failed["tasks"]["recent"][0]["view_error"])
        self.capture_failure = False
        self.engine.call("zemax_get_workbench_snapshot")
        self.assertEqual(self.engine.state.read()["validity"], "valid")
        self.assertEqual(self.writes, 1)

    def test_later_rollback_cannot_erase_an_earlier_unresolved_model(self):
        self.registry["zemax_confocal_response"] = lambda: {"status": "error", "message": "defocus restore unknown"}
        self.call_write("zemax_confocal_response")
        self.assertEqual(self.engine.state.read()["validity"], "uncertain")
        self.registry["zemax_surface_operations"] = lambda: {"status": "error", "rolled_back": True}
        self.call_write()
        self.assertEqual(self.engine.state.read()["validity"], "uncertain")
        self.engine.call("zemax_get_workbench_snapshot")
        self.assertEqual(self.engine.state.read()["validity"], "uncertain")
        self.call_write("zemax_load_file", filepath="known-baseline.zmx")
        self.assertEqual(self.engine.state.read()["validity"], "valid")

    def test_metadata_failure_cannot_hide_partial_native_write_uncertainty(self):
        normal_metadata = self.engine.metadata
        def partial_write():
            self.hidden += 1  # Deliberately absent from the visible snapshot hash.
            self.engine.metadata = lambda: (_ for _ in ()).throw(RuntimeError("metadata unavailable"))
            return {"status": "error", "message": "partial native write"}
        self.registry["zemax_surface_operations"] = partial_write
        self.assertEqual(self.call_write()["status"], "error")
        self.assertFalse(self.engine.state.read()["context_valid"])
        self.engine.metadata = normal_metadata
        self.engine.call("zemax_get_workbench_snapshot")
        recovered_context = self.engine.state.read()
        self.assertTrue(recovered_context["context_valid"])
        self.assertEqual(recovered_context["validity"], "uncertain")
        self.assertEqual(self.hidden, 1)

    def test_first_observation_is_not_proof_of_a_fresh_clean_session_after_failure(self):
        self.engine.seen_session = None
        self.engine.uncertainty = "first native operation failed before identity could be read"
        self.engine.call("zemax_get_workbench_snapshot")
        self.assertEqual(self.engine.state.read()["validity"], "uncertain")
        self.session = object()  # A verifiably different native lifetime can clear it.
        self.engine.call("zemax_get_workbench_snapshot")
        self.assertEqual(self.engine.state.read()["validity"], "valid")

    def test_native_session_recreation_is_new_identity(self):
        old = self.engine.state.context()
        self.session = object()
        self.engine.call("zemax_get_workbench_snapshot")
        self.assertNotEqual(old["model_id"], self.engine.state.context()["model_id"])

    def test_metadata_failure_blocks_writes_until_real_project_identity_is_observed(self):
        normal_metadata = self.engine.metadata
        def load_b(filepath):
            self.project = self.model_project = "b"
            self.engine.metadata = lambda: (_ for _ in ()).throw(RuntimeError("metadata unavailable"))
            return {"status": "success"}
        self.registry["zemax_load_file"] = load_b
        self.assertEqual(self.call_write("zemax_load_file", filepath="b.zmx")["status"], "success")
        self.assertFalse(self.engine.state.read()["context_valid"])
        cached_context = self.engine.state.context()
        blocked = self.engine.call("zemax_surface_operations", expected_context=cached_context)
        self.assertEqual(blocked["code"], "WORKBENCH_STATE_UNCERTAIN")
        self.assertEqual(self.writes, 0)
        self.engine.metadata = normal_metadata
        self.engine.call("zemax_get_workbench_snapshot")
        self.assertEqual(self.engine.state.context()["model_project"], "b")
        self.assertTrue(self.engine.state.read()["context_valid"])
        self.assertEqual(self.engine.call("zemax_surface_operations", expected_context=cached_context)["code"], "WORKBENCH_CONTEXT_STALE")
        self.assertEqual(self.call_write()["status"], "success")
        self.assertEqual(self.writes, 1)

    def test_refresh_observed_optical_change_advances_model_revision_but_path_does_not(self):
        old = self.engine.state.read()
        self.value += 1
        self.engine.call("zemax_get_workbench_snapshot")
        changed = self.engine.state.read()
        self.assertEqual(changed["model_revision"], old["model_revision"] + 1)
        self.assertEqual(changed["model_id"], old["model_id"])
        original = self.engine.capture
        self.engine.capture = lambda: {**fixture_snapshot(self.value), "model": {**fixture_snapshot(self.value)["model"], "filepath": "saved-as.zmx"}}
        self.engine.call("zemax_get_system_data")
        self.assertEqual(self.engine.state.read()["model_revision"], changed["model_revision"])
        self.engine.capture = original

    def test_project_changes_are_independent_from_model_ownership_including_export_failure(self):
        for tool in ("zemax_export_cad", "zemax_export_optical_drawing", "zemax_export_prescription_for_cad"):
            def export():
                self.project += "b"
                return {"status": "error", "message": "export failed after project switch"}
            self.registry[tool] = export
            before = self.engine.state.context()
            self.call_write(tool)
            after = self.engine.state.read()
            self.assertNotEqual(after["active_project"], before["active_project"])
            self.assertEqual(after["model_project"], "a")
            self.assertGreater(after["state_version"], before["state_version"])
            self.assertEqual(self.engine.call(tool, expected_context=before)["code"], "WORKBENCH_CONTEXT_STALE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
