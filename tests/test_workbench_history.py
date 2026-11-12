"""Session geometry-history software tests; fake tools only, no native or server."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workbench_history import HistoryError, WorkbenchHistory
from core.workbench_state import WorkbenchState
from core.workbench_engine import EngineWorker
from core.workbench_service import WorkbenchHandler


def snapshot(value=1, project="a", configuration=1):
    return {"status": "success", "schema_version": 1, "revision": str(value), "observed_at": "fixture",
            "model": {"project": project, "configuration": configuration, "units": "mm",
                      "surfaces": [{"index": 1, "radius": value}]},
            "layout": {"status": "ready", "surfaces": []}, "capabilities": {"ray_trace": False}}


def publish(state, value=1, project="a", **extra):
    state.publish(metadata={"active_project": project, "model_project": project, "proposal_confirmed": False},
                  snapshot=snapshot(value, project), validity="valid", **extra)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.state = WorkbenchState()
        publish(self.state, replace=True)
        self.instance = self.state.context()["service_instance_id"]

    def entries(self, project="a"):
        return self.state.history_list(self.instance, project)["entries"]

    def test_success_snapshot_source_and_deep_immutability(self):
        self.assertTrue(self.state.record_history("op-1", "zemax_run_optimization"))
        entry = self.entries()[0]
        self.assertEqual(entry["label"], "自动优化")
        self.assertEqual(entry["source"]["configuration"], 1)
        self.assertEqual(entry["source"]["model_revision"], 1)
        detail = self.state.history_detail(self.instance, "a", entry["id"])
        detail["snapshot"]["model"]["surfaces"][0]["radius"] = 999
        entry["source"]["configuration"] = 99
        self.state.data["snapshot"]["model"]["surfaces"][0]["radius"] = 88
        retained = self.state.history_detail(self.instance, "a", entry["id"])
        self.assertEqual(retained["snapshot"]["model"]["surfaces"][0]["radius"], 1)
        self.assertEqual(retained["entry"]["source"]["configuration"], 1)
        self.assertEqual(retained["snapshot"]["sync"]["model_id"], retained["entry"]["source"]["model_id"])

    def test_no_plain_read_or_duplicate_operation_entry(self):
        before = self.state.read()
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.state.read(), before)
        self.state.record_history("same", "zemax_apply_prescription")
        self.state.record_history("same", "zemax_apply_prescription")
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.state.read()["history_revision"], 1)

    def test_summary_does_not_clone_snapshot_payloads(self):
        self.state.record_history("op", "zemax_load_file")
        class MustNotCopy:
            def __deepcopy__(self, memo):
                raise AssertionError("Summary cloned a full snapshot")
        self.state.optical_history.entries[self.entries()[0]["id"]][1]["canary"] = MustNotCopy()
        listing = self.state.history_list(self.instance, "a")
        self.assertEqual(len(listing["entries"]), 1)
        self.assertNotIn("snapshot", listing)
        self.assertEqual(listing["storage"], "session")

    def test_eviction_count_and_unknown_entry_are_explicit(self):
        self.state.optical_history = WorkbenchHistory(max_entries=2)
        ids = []
        for index in range(3):
            publish(self.state, index + 2, model_changed=True)
            self.state.record_history("op-" + str(index), "zemax_surface_operations")
            ids.append(self.entries()[0]["id"])
        self.assertEqual([entry["id"] for entry in self.entries()], list(reversed(ids[1:])))
        with self.assertRaises(HistoryError) as context:
            self.state.history_detail(self.instance, "a", ids[0])
        self.assertEqual(context.exception.status, 404)
        self.assertEqual(context.exception.code, "HISTORY_NOT_FOUND")

    def test_total_byte_budget_evicts_without_exceeding_limits(self):
        self.state.record_history("measure", "zemax_load_file")
        size = self.state.history_list(self.instance, "a")["limits"]["total_bytes"]
        self.state.optical_history = WorkbenchHistory(max_entries=12, max_total_bytes=size * 2 + 20)
        for index in range(10):
            self.state.record_history("op-" + str(index), "zemax_load_file")
        limits = self.state.history_list(self.instance, "a")["limits"]
        self.assertLess(limits["entry_count"], 10)
        self.assertLessEqual(limits["total_bytes"], limits["max_total_bytes"])

    def test_oversize_snapshot_is_skipped_and_existing_entry_preserved(self):
        self.state.record_history("old", "zemax_load_file")
        self.state.optical_history.max_entry_bytes = 8
        self.assertFalse(self.state.record_history("large", "zemax_run_hammer"))
        listing = self.state.history_list(self.instance, "a")
        self.assertEqual(len(listing["entries"]), 1)
        self.assertEqual(listing["recording"]["code"], "HISTORY_ENTRY_TOO_LARGE")
        self.assertEqual(listing["recording"]["operation_id"], "large")

    def test_invalid_or_unobserved_sources_are_not_retained(self):
        mutations = [lambda state: state.update(validity="uncertain"),
                     lambda state: state.update(context_valid=False),
                     lambda state: state.update(error="capture failed"),
                     lambda state: state.update(snapshot=None),
                     lambda state: state.update(model_project="other"),
                     lambda state: state["snapshot"]["sync"].update(state_version=999),
                     lambda state: state["snapshot"]["model"].update(project="other"),
                     lambda state: state["snapshot"]["model"].update(configuration=True),
                     lambda state: state.update(model_id=None)]
        original = self.state.read()
        for index, mutate in enumerate(mutations):
            data = deepcopy(original)
            mutate(data)
            self.assertFalse(WorkbenchHistory().record(data, "op" + str(index), "zemax_run_hammer"))

    def test_scope_switch_and_cross_project_detail_are_rejected(self):
        self.state.record_history("a", "zemax_load_file")
        a_id = self.entries()[0]["id"]
        publish(self.state, project="b", replace=True)
        self.state.record_history("b", "zemax_load_file")
        self.assertEqual(len(self.entries("b")), 1)
        with self.assertRaises(HistoryError) as wrong_project:
            self.state.history_list(self.instance, "a")
        self.assertEqual(wrong_project.exception.code, "WORKFLOW_PROJECT_MISMATCH")
        with self.assertRaises(HistoryError) as cross:
            self.state.history_detail(self.instance, "b", a_id)
        self.assertEqual(cross.exception.status, 404)
        with self.assertRaises(HistoryError) as wrong_instance:
            self.state.history_list("stale", "b")
        self.assertEqual(wrong_instance.exception.code, "WORKBENCH_INSTANCE_MISMATCH")

    def test_restart_is_empty_and_does_not_adopt_previous_instance(self):
        self.state.record_history("a", "zemax_load_file")
        restarted = WorkbenchState()
        publish(restarted, replace=True)
        self.assertEqual(restarted.history_list(restarted.context()["service_instance_id"], "a")["entries"], [])
        with self.assertRaises(HistoryError):
            restarted.history_list(self.instance, "a")

    def test_private_fields_and_non_json_values_are_not_retained(self):
        self.state.data["snapshot"]["token"] = "private"
        self.state.data["snapshot"]["model"]["lease_token"] = "private-lease"
        self.state.record_history("safe", "zemax_load_file")
        detail = self.state.history_detail(self.instance, "a", self.entries()[0]["id"])
        self.assertNotIn("private", json.dumps(detail))
        self.state.data["snapshot"]["model"]["radius"] = float("nan")
        self.assertFalse(self.state.record_history("bad-json", "zemax_load_file"))
        self.assertEqual(self.state.history_list(self.instance, "a")["recording"]["code"], "HISTORY_SNAPSHOT_INVALID")


class EngineHistoryTests(unittest.TestCase):
    def setUp(self):
        self.value, self.captures = 1, 0
        self.project, self.model_project = "a", "a"
        self.result = {"status": "success"}
        self.capture_fails = False
        self.session = object()
        def mutate():
            self.value += 1
            return deepcopy(self.result)
        self.engine = EngineWorker({"zemax_get_workbench_snapshot": self.capture,
            "zemax_surface_operations": mutate, "zemax_run_optimization": mutate,
            "zemax_load_file": lambda filepath: {"status": "success"}},
            metadata=lambda: {"active_project": self.project, "model_project": self.model_project,
                              "proposal_confirmed": False, "session_identity": self.session})
        self.addCleanup(self.engine.executor.shutdown)
        self.engine.call("zemax_get_workbench_snapshot")

    def capture(self):
        self.captures += 1
        if self.capture_fails:
            raise RuntimeError("synthetic capture unavailable")
        return snapshot(self.value, self.model_project)

    def write(self, tool="zemax_surface_operations", **arguments):
        with patch("core.zos_session.ZOSSession.get_instance", side_effect=AssertionError("native opened")):
            return self.engine.call(tool, arguments, expected_context=self.engine.state.context())

    def listing(self):
        return self.engine.state.history_list(self.engine.state.context()["service_instance_id"], self.project)

    def test_success_records_once_with_no_additional_capture_or_read_poll(self):
        before = self.captures
        self.write("zemax_run_optimization")
        self.assertEqual(self.captures, before + 1)
        entries = self.listing()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tool"], "zemax_run_optimization")
        self.engine.call("zemax_get_workbench_snapshot")
        self.assertEqual(self.listing()["entries"], entries)

    def test_failed_rejected_rolled_back_and_warning_never_record(self):
        for result in ({"status": "error", "rolled_back": True},
                       {"status": "rejected"}, {"status": "success", "rolled_back": True},
                       {"status": "warning", "variables": 0},
                       {"status": "error", "code": "PROPOSAL_NOT_CONFIRMED"}):
            self.result = result
            self.write()
            self.assertEqual(self.listing()["entries"], [])

    def test_success_with_failed_capture_never_reuses_previous_snapshot(self):
        self.capture_fails = True
        self.assertEqual(self.write()["status"], "success")
        self.assertEqual(self.listing()["entries"], [])
        self.assertIsNone(self.engine.state.read()["snapshot"])

    def test_wrong_project_or_configuration_does_not_record(self):
        self.model_project = "b"
        self.write()
        self.assertEqual(self.listing()["entries"], [])

    def test_storage_failure_and_size_skip_keep_original_operation_success(self):
        self.engine.state.optical_history.max_entry_bytes = 8
        self.assertEqual(self.write()["status"], "success")
        self.assertEqual(self.listing()["recording"]["code"], "HISTORY_ENTRY_TOO_LARGE")
        with patch.object(self.engine.state.optical_history, "record", side_effect=RuntimeError("cache failure")):
            self.assertEqual(self.write()["status"], "success")
        self.assertEqual(self.listing()["recording"]["code"], "HISTORY_RECORDING_FAILED")

    def test_replace_history_has_new_model_id_and_keeps_previous_read_only_entry(self):
        self.write()
        first = self.listing()["entries"][0]
        self.write("zemax_load_file", filepath="new-fixture.zmx")
        latest, previous = self.listing()["entries"]
        self.assertNotEqual(latest["source"]["model_id"], previous["source"]["model_id"])
        self.assertEqual(previous, first)


class HandlerTests(unittest.TestCase):
    """Execute real handler routes in memory; no listening socket is created."""
    def setUp(self):
        self.state = WorkbenchState()
        publish(self.state, replace=True)
        self.state.record_history("fixture", "zemax_apply_prescription")
        self.instance = self.state.context()["service_instance_id"]

    def request(self, path):
        response = []
        handler = object.__new__(WorkbenchHandler)
        handler.path = path
        handler.server = SimpleNamespace(engine=SimpleNamespace(state=self.state))
        handler._allowed = lambda authenticate=True: True
        handler._json = lambda value, status=200: response.append((status, value))
        handler.do_GET()
        return response[0]

    def url(self, entry="", **query):
        return "/api/history" + ("/" + entry if entry else "") + "?" + urlencode({
            "instance_id": self.instance, "project_name": "a", **query})

    def test_list_and_detail_only_read_history_and_preserve_state(self):
        before = self.state.read()
        with patch("core.zos_session.ZOSSession.get_instance", side_effect=AssertionError("native opened")):
            code, listing = self.request(self.url())
            self.assertEqual(code, 200)
            code, detail = self.request(self.url(listing["entries"][0]["id"]))
            self.assertEqual(code, 200)
        self.assertEqual(detail["entry"], listing["entries"][0])
        self.assertEqual(self.state.read(), before)

    def test_required_scope_conflict_and_eviction_codes(self):
        for path in ("/api/history", "/api/history?project_name=a",
                     self.url() + "&project_name=a", self.url(extra="bad")):
            code, result = self.request(path)
            self.assertEqual((code, result["code"]), (400, "HISTORY_SCOPE_REQUIRED"))
        self.assertEqual(self.request(self.url(instance_id="old"))[1]["code"], "WORKBENCH_INSTANCE_MISMATCH")
        self.assertEqual(self.request(self.url(project_name="b"))[1]["code"], "WORKFLOW_PROJECT_MISMATCH")
        self.assertEqual(self.request(self.url("missing"))[0], 404)


if __name__ == "__main__":
    unittest.main()
