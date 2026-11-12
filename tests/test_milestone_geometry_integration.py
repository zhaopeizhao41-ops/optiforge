"""Real workflow decorators and EngineWorker with temporary data and fake optics."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import test_design_milestones as legacy
from core import design_workflow as flow
from core import milestone_geometry_artifacts as artifacts
from core.design_milestones import OBSERVATION
from core.workbench_state import WorkbenchState
from tools.collaboration_tools import zemax_design_workflow


class GeometryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fx = legacy.MilestoneTests(methodName="runTest")
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        original = self.fx.engine.registry["zemax_get_workbench_snapshot"]

        def capture():
            result = original()
            result["model"]["surfaces"] = [
                {"index": 0, "radius": "Infinity", "thickness": "Infinity", "material": "", "type": "Standard"},
                {"index": 1, "radius": self.fx.value, "thickness": 1, "material": "N-BK7", "type": "Standard"},
                {"index": 2, "radius": -20, "thickness": 9, "material": "", "type": "Standard"},
                {"index": 3, "radius": "Infinity", "thickness": 0, "material": "", "type": "Standard"},
            ]
            result["layout"] = {"status": "ready", "plane": "YZ", "units": "mm", "issues": [], "rays": [],
                "surfaces": [
                    {"index": index, "vertex_z": z, "is_stop": index == 1, "is_image": index == 3,
                     "points": [[z, -2], [z, 2]]} for index, z in ((1, 0), (2, 1), (3, 10))],
                "elements": [{"front": 1, "back": 2, "material": "N-BK7",
                              "points": [[0, -2], [0, 2], [1, 2], [1, -2]]}]}
            return result

        self.fx.engine.registry["zemax_get_workbench_snapshot"] = capture
        self.fx.observe()

    @property
    def path(self):
        return Path(self.fx.temp.name) / ".design-workflow.sqlite3"

    def table_count(self):
        with closing(sqlite3.connect(self.path)) as db:
            found = db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (artifacts.TABLE,)).fetchone()
            return db.execute(f"SELECT count(*) FROM {artifacts.TABLE}").fetchone()[0] if found else None

    def archive(self, **extra):
        self.fx.start()
        self.operation = self.fx.optical()
        self.before_captures = self.fx.captures
        # A synthetic metadata-only publication advances the current state while
        # preserving the operation cache's original source_state_version.
        state = self.fx.engine.state.read()
        self.fx.engine.state.publish(metadata={key: state[key] for key in
            ("active_project", "model_project", "proposal_confirmed")}, snapshot=state["snapshot"],
            validity="valid", changed=True)
        result = self.fx.report([self.operation["operation_id"]], archive_geometry=True, **extra)
        self.assertEqual(result["status"], "success", result)
        self.record = self.fx.attempt(result)
        self.ref = self.record["geometry_artifact"]
        return result

    def read(self, **extra):
        context = self.fx.engine.state.context()
        scope = {"service_instance_id": context["service_instance_id"], "project_name": self.fx.project}
        return self.fx.engine.call_envelope("zemax_design_workflow", {"action": "milestone_artifact",
            "project_name": self.fx.project, "payload": {"source": scope, "artifact_id": self.ref["id"], **extra}})

    def test_explicit_archive_and_default_report_remain_distinct(self):
        self.fx.start()
        operation = self.fx.optical()
        result = self.fx.report([operation["operation_id"]])
        self.assertEqual(result["status"], "success", result)
        self.assertNotIn("geometry_artifact", self.fx.attempt(result))
        self.assertIsNone(self.table_count())
        self.fx.start("attempt-2", "stage_2")
        first, last = self.fx.optical(), self.fx.optical("zemax_run_hammer")
        captures = self.fx.captures
        result = self.fx.report([last["operation_id"], first["operation_id"]], attempt="attempt-2", archive_geometry=True)
        self.assertEqual(result["status"], "success", result)
        record = self.fx.attempt(result, stage=1)
        self.assertEqual(record["geometry_artifact"]["operation_id"], last["operation_id"])
        self.assertEqual(self.fx.captures, captures)
        self.assertEqual(self.table_count(), 1)
        self.assertNotIn("payload_json", json.dumps(result))
        self.assertIsNone(OBSERVATION.get())
        self.assertIsNone(artifacts.GEOMETRY_READER.get())
        self.assertIsNone(self.fx.engine.executor.submit(artifacts.GEOMETRY_READER.get).result())
        self.fx.native.assert_not_called()

    def test_read_is_persistent_without_model_approval_or_cache_and_does_not_publish(self):
        self.archive()
        captures = self.fx.captures
        stored = self.path.read_bytes()
        self.fx.engine.state.optical_history.entries.clear()
        # An entirely new state/service has no native model or old cache.
        self.fx.engine.state = WorkbenchState()
        self.fx.engine.state.publish(metadata={"active_project": self.fx.project, "model_project": None,
                                              "proposal_confirmed": False}, snapshot=None,
                                     validity="unavailable", context_valid=False)
        self.fx.engine.native_enabled = False
        before = self.fx.engine.state.context()
        with patch.object(self.fx.engine.state, "publish", side_effect=AssertionError("read published metadata")), \
             patch.object(self.fx.engine.state, "publish_task_changes", side_effect=AssertionError("read published tasks")), \
             patch.object(flow, "view", side_effect=AssertionError("read called workflow view")), \
             patch.object(flow, "transaction", side_effect=AssertionError("read called write transaction")):
            envelope = self.read()
        result = envelope["result"]
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["artifact"], self.ref)
        self.assertGreater(self.record["end_source"]["source_state_version"], self.ref["source"]["source_state_version"])
        self.assertNotEqual(result["source"]["service_instance_id"], self.ref["source"]["service_instance_id"])
        self.assertEqual(before, self.fx.engine.state.context())
        self.assertEqual(stored, self.path.read_bytes())
        self.assertEqual(captures, self.fx.captures)
        self.assertEqual(hashlib.sha256(result["payload_json"].encode()).hexdigest(), self.ref["sha256"])
        self.assertEqual(len(result["payload_json"].encode()), self.ref["bytes"])

    def test_report_validates_bool_authorization_cas_lease_and_no_forged_payload(self):
        self.fx.start()
        operation = self.fx.optical()
        before = self.fx.task()["version"]
        for extra in ({"archive_geometry": 1}, {"archive_geometry": "true"},
                      {"archive_geometry": True, "status": "failed"},
                      {"archive_geometry": True, "version": before - 1},
                      {"archive_geometry": True, "lease_token": "private-invalid"},
                      {"archive_geometry": True, "snapshot": {}},
                      {"archive_geometry": True, "source": {}}):
            result = self.fx.report([operation["operation_id"]], **extra)
            self.assertEqual(result["status"], "error", result)
        self.assertIsNone(self.table_count())
        self.assertEqual(self.fx.task()["version"], before)
        self.assertIsNone(self.fx.engine.executor.submit(artifacts.GEOMETRY_READER.get).result())
        # Revoke the actual temporary proposal; the ordinary gate must still win.
        flow.revoke(self.fx.project, "synthetic-test-revocation")
        self.assertEqual(self.fx.report([operation["operation_id"]], archive_geometry=True)["status"], "error")
        self.assertIsNone(self.table_count())

    def test_cache_mismatch_and_missing_reader_cannot_archive(self):
        self.fx.start()
        operation = self.fx.optical()
        entry, snapshot, size = next(iter(self.fx.engine.state.optical_history.entries.values()))
        entry["source"]["source_state_version"] += 1
        result = self.fx.report([operation["operation_id"]], archive_geometry=True)
        self.assertEqual(result["code"], "MILESTONE_ARTIFACT_INVALID", result)
        self.assertIsNone(self.table_count())
        self.assertIsNone(self.fx.engine.executor.submit(artifacts.GEOMETRY_READER.get).result())
        entry["source"]["source_state_version"] -= 1
        expected = self.fx.context()
        payload = {"request_id": "handoff", "version": self.fx.task()["version"], "lease_token": self.fx.claim["lease_token"],
                   "attempt_id": "attempt-1", "status": "needs_review", "operation_ids": [operation["operation_id"]],
                   "note": "直调隔离软件测试", "archive_geometry": True}
        token = OBSERVATION.set(self.fx.engine.state.workflow_observation())
        try:
            result = zemax_design_workflow("milestone_report", self.fx.project, payload, expected)
        finally:
            OBSERVATION.reset(token)
        self.assertEqual(result["code"], "MILESTONE_ARTIFACT_SOURCE_REQUIRED", result)
        self.assertIsNone(self.table_count())

    def test_failure_after_artifact_insert_rolls_back_reference_version_and_table(self):
        self.fx.start()
        operation = self.fx.optical()
        before = self.path.read_bytes()
        version = self.fx.task()["version"]
        original = flow._put_task

        def reject(db, task):
            if task.get("milestone_attempts", [{}])[-1].get("geometry_artifact"):
                raise sqlite3.OperationalError("private-path-and-sql-error")
            return original(db, task)

        with patch.object(flow, "_put_task", side_effect=reject):
            result = self.fx.report([operation["operation_id"]], archive_geometry=True)
        self.assertEqual(result["code"], "MILESTONE_ARTIFACT_UNAVAILABLE", result)
        self.assertNotIn("private-path", result["message"])
        self.assertEqual(self.fx.task()["version"], version)
        self.assertEqual(self.fx.attempt()["declared_status"], "running")
        self.assertIsNone(self.table_count())
        self.assertEqual(before, self.path.read_bytes())
        self.assertIsNone(self.fx.engine.executor.submit(artifacts.GEOMETRY_READER.get).result())

    def test_read_missing_scope_wrong_service_and_project_are_safe(self):
        self.archive()
        self.assertEqual(self.read(source={})["result"]["code"], "MILESTONE_ARTIFACT_SOURCE_REQUIRED")
        self.assertEqual(self.read(source={"service_instance_id": "other", "project_name": self.fx.project})["result"]["code"],
                         "WORKBENCH_INSTANCE_MISMATCH")
        self.assertEqual(self.read(source={"service_instance_id": self.fx.engine.state.context()["service_instance_id"],
                                         "project_name": "other"})["result"]["code"], "WORKFLOW_PROJECT_MISMATCH")
        self.assertEqual(self.read(artifact_id="../private")["result"]["code"], "MILESTONE_INPUT_INVALID")
        self.assertEqual(zemax_design_workflow("milestone_artifact", self.fx.project,
            {"source": {"service_instance_id": "fake", "project_name": self.fx.project}, "artifact_id": self.ref["id"]})["code"],
            "MILESTONE_ARTIFACT_SOURCE_REQUIRED")

    def test_cache_resolver_is_detached_exact_unique_and_does_not_capture(self):
        self.fx.start()
        operation = self.fx.optical()
        state = self.fx.engine.state
        entry, snapshot, size = next(iter(state.optical_history.entries.values()))
        source = deepcopy(entry["source"])
        captures = self.fx.captures
        detached = state.geometry_for_operation(operation["operation_id"], source)
        detached["snapshot"]["model"]["name"] = "changed detached copy"
        self.assertNotEqual(snapshot["model"].get("name"), "changed detached copy")
        with self.assertRaises(flow.WorkflowError) as wrong:
            state.geometry_for_operation(operation["operation_id"], {**source, "source_state_version": 999})
        self.assertEqual(wrong.exception.code, "MILESTONE_ARTIFACT_INVALID")
        state.optical_history.entries["duplicate-test-entry"] = (deepcopy(entry), deepcopy(snapshot), size)
        with self.assertRaises(flow.WorkflowError) as duplicate:
            state.geometry_for_operation(operation["operation_id"], source)
        self.assertEqual(duplicate.exception.code, "MILESTONE_ARTIFACT_INVALID")
        state.optical_history.entries.clear()
        with self.assertRaises(flow.WorkflowError) as missing:
            state.geometry_for_operation(operation["operation_id"], source)
        self.assertEqual(missing.exception.code, "MILESTONE_ARTIFACT_NOT_FOUND")
        self.assertEqual(captures, self.fx.captures)


if __name__ == "__main__":
    unittest.main()
