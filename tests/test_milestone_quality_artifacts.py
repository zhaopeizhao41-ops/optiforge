"""Stage quality archive: real workflow decorators and EngineWorker, temporary data, synthetic analysis cache."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
from core import milestone_geometry_artifacts as geometry
from core import milestone_quality_artifacts as quality
from core.analysis_data import validate_settings
from core.design_milestones import OBSERVATION
from core.workbench_analysis_history import sanitized
from core.workbench_state import WorkbenchState
from tools.collaboration_tools import zemax_design_workflow


def iso(offset_seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


class QualityArchiveTests(unittest.TestCase):
    def setUp(self):
        self.fx = legacy.MilestoneTests(methodName="runTest")
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.counter = 0
        original = self.fx.engine.registry["zemax_get_workbench_snapshot"]

        def capture():
            # Same complete SIMULATED geometry as the geometry archive integration tests.
            result = original()
            result["model"]["surfaces"] = [
                {"index": 0, "radius": "Infinity", "thickness": "Infinity", "material": "", "type": "Standard"},
                {"index": 1, "radius": self.fx.value, "thickness": 1, "material": "N-BK7", "type": "Standard"},
                {"index": 2, "radius": "Infinity", "thickness": 0, "material": "", "type": "Standard"}]
            result["layout"] = {"status": "unsupported", "plane": "YZ", "units": "mm", "surfaces": [],
                                "elements": [], "issues": ["SIMULATED 无轮廓"], "rays": []}
            return result

        self.fx.engine.registry["zemax_get_workbench_snapshot"] = capture
        self.fx.observe()

    @property
    def path(self):
        return Path(self.fx.temp.name) / ".design-workflow.sqlite3"

    def table_count(self, table=quality.TABLE):
        with closing(sqlite3.connect(self.path)) as db:
            found = db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
            return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if found else None

    def cache(self, started=None, finished=None, store=True, **extra):
        """Store a SIMULATED analysis result for the current observed model, like _execute_analysis."""
        self.counter += 1
        state = self.fx.engine.state.read()
        source = {key: state[key] for key in ("service_instance_id", "model_id", "model_revision",
                                              "active_project", "model_project")}
        source.update(configuration=state["snapshot"]["model"]["configuration"],
                      source_state_version=state["state_version"])
        result = {"analysis_schema_version": 1, "status": "success", "result_id": f"result-{self.counter}",
                  "key": f"cache-key-{self.counter}", "engine": "SIMULATED adapter, not an optical result",
                  "source": source, "started_at": started or iso(), "finished_at": finished or iso(),
                  "elapsed_ms": 10, "settings": validate_settings({"fields": [1], "analyses": ["mtf", "spot"]}),
                  "items": {"mtf": {"status": "success", "curves": [], "coverage": {"fields": [1]}},
                            "spot": {"status": "success", "fields": [], "token": "private-canary"}},
                  "overall": "INFO", "verdicts": [
                      {"analysis": "mtf", "verdict": "INFO", "criteria": "SIMULATED 诊断数据 <b>纯文本</b>"},
                      {"analysis": "spot", "verdict": "INFO", "criteria": "点列仅诊断"}],
                  "comparison_context": {"schema_version": 1, "model": {"configuration": 1}}, **extra}
        if store:
            self.fx.engine.state.store_analysis(result)
        return result

    def report(self, operation, **extra):
        return self.fx.report([operation["operation_id"]], **extra)

    def archive(self, **extra):
        self.fx.start()
        operation = self.fx.optical()
        self.result = self.cache()
        result = self.report(operation, quality_result_id=self.result["result_id"], **extra)
        self.assertEqual(result["status"], "success", result)
        self.record = self.fx.attempt(result)
        self.ref = self.record["quality_artifact"]
        return result

    def read(self, **extra):
        context = self.fx.engine.state.context()
        scope = {"service_instance_id": context["service_instance_id"], "project_name": self.fx.project}
        return self.fx.engine.call_envelope("zemax_design_workflow", {"action": "milestone_quality_artifact",
            "project_name": self.fx.project, "payload": {"source": scope, "artifact_id": self.ref["id"], **extra}})["result"]

    def assert_rejected(self, result, code, version):
        self.assertEqual(result.get("status"), "error", result)
        self.assertEqual(result.get("code"), code, result)
        self.assertIsNone(self.table_count())
        self.assertEqual(self.fx.task()["version"], version)
        self.assertEqual(self.fx.attempt()["declared_status"], "running")

    def test_explicit_result_is_archived_verbatim_without_rejudging(self):
        captures = self.fx.captures
        self.archive()
        self.assertEqual(self.fx.captures, captures + 1)  # only the optical operation's own observation
        self.assertEqual(self.table_count(), 1)
        self.assertEqual(self.ref["result_id"], self.result["result_id"])
        self.assertEqual(self.ref["analysis"], {key: self.result[key] for key in
                                                ("status", "overall", "started_at", "finished_at")})
        self.assertEqual({key: self.ref["source"][key] for key in quality.OPTICAL_KEYS}, self.result["source"])
        for key in quality.SEMANTIC_KEYS:
            self.assertEqual(self.ref["source"][key], self.record["end_source"][key])
        read = self.read()
        self.assertEqual(read["status"], "success", read)
        self.assertEqual(read["artifact"], self.ref)
        stored = json.loads(read["payload_json"])
        self.assertNotIn("private-canary", read["payload_json"])
        expected = deepcopy(self.result)
        del expected["items"]["spot"]["token"]
        self.assertEqual(stored, expected)  # INFO rows stay INFO; nothing recomputed
        self.assertEqual(hashlib.sha256(read["payload_json"].encode()).hexdigest(), self.ref["sha256"])
        self.assertNotIn("payload_json", json.dumps(self.fx.call()))
        self.assertIsNone(quality.QUALITY_READER.get())
        self.assertIsNone(self.fx.engine.executor.submit(quality.QUALITY_READER.get).result())
        self.fx.native.assert_not_called()

    def test_partial_and_error_rows_are_preserved(self):
        self.fx.start()
        operation = self.fx.optical()
        result = self.cache(status="partial", overall="ERROR", verdicts=[
            {"analysis": "mtf", "verdict": "ERROR", "evidence": "SIMULATED 缺数据", "criteria": "诊断数据"}])
        reply = self.report(operation, quality_result_id=result["result_id"])
        self.assertEqual(reply["status"], "success", reply)
        self.ref = self.fx.attempt(reply)["quality_artifact"]
        self.assertEqual(self.ref["analysis"]["status"], "partial")
        self.assertEqual(json.loads(self.read()["payload_json"])["verdicts"][0]["verdict"], "ERROR")
        self.assertEqual(self.fx.attempt(reply)["declared_status"], "needs_review")

    def test_geometry_and_quality_share_one_report_transaction(self):
        self.fx.start()
        operation = self.fx.optical()
        result = self.cache()
        version = self.fx.task()["version"]
        reply = self.report(operation, quality_result_id=result["result_id"], archive_geometry=True)
        self.assertEqual(reply["status"], "success", reply)
        record = self.fx.attempt(reply)
        self.assertIn("geometry_artifact", record)
        self.assertIn("quality_artifact", record)
        self.assertEqual(self.fx.task()["version"], version + 1)
        self.assertEqual(self.table_count(), 1)
        self.assertEqual(self.table_count(geometry.TABLE), 1)

    def test_result_computed_before_stage_start_is_rejected_even_if_cache_hit_now(self):
        before_stage = self.cache(started=iso(-60), finished=iso(-59))
        self.fx.start()
        operation = self.fx.optical()
        version = self.fx.task()["version"]
        self.assertEqual(self.report(operation, quality_result_id=before_stage["result_id"]).get("status"), "error")
        # Same revision as the report (e.g. an operation that left the model unchanged), but the
        # cache hit keeps the original computation time, which predates the stage.
        old = self.cache(started=iso(-60), finished=iso(-59))
        self.assertEqual(self.fx.engine.state.cached_analysis(old["key"])["finished_at"], old["finished_at"])
        self.assert_rejected(self.report(operation, quality_result_id=old["result_id"]),
                             "MILESTONE_QUALITY_OUTSIDE_STAGE", version)
        future = self.cache(started=iso(), finished=iso(3600))
        self.assert_rejected(self.report(operation, quality_result_id=future["result_id"]),
                             "MILESTONE_QUALITY_OUTSIDE_STAGE", version)

    def test_result_from_other_model_revision_configuration_or_service_is_rejected(self):
        self.fx.start()
        first = self.fx.optical()
        stale = self.cache()  # computed on the revision before the next optical change
        last = self.fx.optical()
        version = self.fx.task()["version"]
        self.assert_rejected(self.fx.report([first["operation_id"], last["operation_id"]],
                                            quality_result_id=stale["result_id"]), "MILESTONE_QUALITY_INVALID", version)
        for change in ({"configuration": 2}, {"service_instance_id": "other-service"},
                       {"model_id": "other-model"}, {"active_project": "other", "model_project": "other"}):
            result = self.cache(store=False)
            result["source"].update(change)
            self.fx.engine.state.store_analysis(result)
            self.assert_rejected(self.fx.report([first["operation_id"], last["operation_id"]],
                                                quality_result_id=result["result_id"]),
                                 "MILESTONE_QUALITY_INVALID", version)

    def test_missing_evicted_or_malformed_ids_and_failed_reports_are_rejected(self):
        self.fx.start()
        operation = self.fx.optical()
        version = self.fx.task()["version"]
        self.assert_rejected(self.report(operation, quality_result_id="not-cached"), "MILESTONE_QUALITY_NOT_FOUND", version)
        result = self.cache()
        self.fx.engine.state.analysis_results.clear()  # evicted before the report
        self.assert_rejected(self.report(operation, quality_result_id=result["result_id"]),
                             "MILESTONE_QUALITY_NOT_FOUND", version)
        for value in ("../private", "", 7, True, {"result_id": "x"}):
            self.assert_rejected(self.report(operation, quality_result_id=value), "MILESTONE_INPUT_INVALID", version)
        self.assert_rejected(self.report(operation, quality_result_id="x", status="failed"),
                             "MILESTONE_INPUT_INVALID", version)
        error = self.cache(status="error")
        self.assert_rejected(self.report(operation, quality_result_id=error["result_id"]),
                             "MILESTONE_QUALITY_INVALID", version)

    def test_direct_tool_call_without_engine_reader_cannot_archive(self):
        self.fx.start()
        operation = self.fx.optical()
        result = self.cache()
        payload = {"request_id": "handoff", "version": self.fx.task()["version"], "lease_token": self.fx.claim["lease_token"],
                   "attempt_id": "attempt-1", "status": "needs_review", "operation_ids": [operation["operation_id"]],
                   "note": "直调隔离软件测试", "quality_result_id": result["result_id"]}
        token = OBSERVATION.set(self.fx.engine.state.workflow_observation())
        try:
            reply = zemax_design_workflow("milestone_report", self.fx.project, payload, self.fx.context())
        finally:
            OBSERVATION.reset(token)
        self.assertEqual(reply["code"], "MILESTONE_QUALITY_SOURCE_REQUIRED", reply)
        self.assertIsNone(self.table_count())

    def test_authorization_and_lease_gates_still_win(self):
        self.fx.start()
        operation = self.fx.optical()
        result = self.cache()
        version = self.fx.task()["version"]
        for extra in ({"lease_token": "private-invalid"}, {"version": version - 1}):
            reply = self.report(operation, quality_result_id=result["result_id"], **extra)
            self.assertEqual(reply["status"], "error", reply)
        self.assertEqual(self.fx.task()["version"], version)
        flow.revoke(self.fx.project, "synthetic-test-revocation")
        self.assertEqual(self.report(operation, quality_result_id=result["result_id"])["status"], "error")
        self.assertIsNone(self.table_count())
        self.assertEqual(self.fx.attempt()["declared_status"], "running")

    def test_failure_after_insert_rolls_back_everything(self):
        self.fx.start()
        operation = self.fx.optical()
        result = self.cache()
        before, version = self.path.read_bytes(), self.fx.task()["version"]
        original = flow._put_task

        def reject(db, task):
            if task.get("milestone_attempts", [{}])[-1].get("quality_artifact"):
                raise sqlite3.OperationalError("private-path-and-sql-error")
            return original(db, task)

        with patch.object(flow, "_put_task", side_effect=reject):
            reply = self.report(operation, quality_result_id=result["result_id"], archive_geometry=True)
        self.assertEqual(reply["code"], "MILESTONE_ARTIFACT_UNAVAILABLE", reply)
        self.assertNotIn("private-path", reply["message"])
        self.assertEqual(self.fx.task()["version"], version)
        self.assertIsNone(self.table_count())
        self.assertIsNone(self.table_count(geometry.TABLE))
        self.assertEqual(before, self.path.read_bytes())

    def test_read_survives_restart_without_model_cache_or_approval(self):
        self.archive()
        stored = self.path.read_bytes()
        captures = self.fx.captures
        self.fx.engine.state = WorkbenchState()
        self.fx.engine.state.publish(metadata={"active_project": self.fx.project, "model_project": None,
                                              "proposal_confirmed": False}, snapshot=None,
                                     validity="unavailable", context_valid=False)
        self.fx.engine.native_enabled = False
        flow.revoke(self.fx.project, "synthetic-test-revocation")
        with patch.object(self.fx.engine.state, "publish", side_effect=AssertionError("read published metadata")), \
             patch.object(flow, "transaction", side_effect=AssertionError("read called write transaction")):
            result = self.read()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["artifact"], self.ref)
        self.assertNotEqual(result["source"]["service_instance_id"], self.ref["source"]["service_instance_id"])
        self.assertEqual(stored, self.path.read_bytes())
        self.assertEqual(captures, self.fx.captures)
        self.fx.native.assert_not_called()

    def test_read_rejects_tampering_bad_scope_and_wrong_kind(self):
        self.archive()
        self.assertEqual(self.read(source={})["code"], "MILESTONE_QUALITY_SOURCE_REQUIRED")
        self.assertEqual(self.read(artifact_id="../private")["code"], "MILESTONE_INPUT_INVALID")
        geometry_scope = {"service_instance_id": self.fx.engine.state.context()["service_instance_id"],
                          "project_name": self.fx.project}
        wrong_kind = self.fx.engine.call_envelope("zemax_design_workflow", {"action": "milestone_artifact",
            "project_name": self.fx.project, "payload": {"source": geometry_scope, "artifact_id": self.ref["id"]}})["result"]
        self.assertEqual(wrong_kind["status"], "error", wrong_kind)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(f"UPDATE {quality.TABLE} SET payload_json=replace(payload_json,'INFO','PASS')")
        tampered = self.read()
        self.assertEqual(tampered["code"], "MILESTONE_QUALITY_INVALID", tampered)
        self.assertNotIn(self.fx.temp.name, tampered["message"])

    def test_identical_rearchive_is_idempotent_and_different_result_conflicts(self):
        self.archive()
        task = next(t for t in flow.task_list(self.fx.project)["tasks"] if t["request_id"] == "handoff")
        with flow.transaction() as db:
            row = db.execute("SELECT data FROM tasks WHERE id=?", ("handoff",)).fetchone()
            stored_task = json.loads(row[0])
        attempt = stored_task["milestone_attempts"][0]
        cached = {self.result["result_id"]: self.fx.engine.state.get_analysis(self.result["result_id"])}
        other = deepcopy(cached[self.result["result_id"]])
        other.update(result_id="other-result", key="other-key")
        cached["other-result"] = other
        token = quality.QUALITY_READER.set(lambda result_id: deepcopy(cached.get(result_id)))
        try:
            with flow.transaction() as db:
                again = quality.archive(db, self.fx.project, stored_task, attempt,
                                        self.result["result_id"], attempt["end_source"])
            self.assertEqual(again, self.ref)
            with self.assertRaises(flow.WorkflowError) as raised:
                with flow.transaction() as db:
                    quality.archive(db, self.fx.project, stored_task, attempt, "other-result", attempt["end_source"])
            self.assertEqual(raised.exception.code, "MILESTONE_QUALITY_CONFLICT")
        finally:
            quality.QUALITY_READER.reset(token)
        self.assertEqual(self.table_count(), 1)
        self.assertEqual(task["status"], "claimed")

    def test_geometry_and_quality_share_the_project_byte_budget(self):
        self.fx.start()
        operation = self.fx.optical()
        result = self.cache()
        reply = self.report(operation, archive_geometry=True)
        self.assertEqual(reply["status"], "success", reply)
        geometry_bytes = self.fx.attempt(reply)["geometry_artifact"]["bytes"]
        quality_bytes = len(geometry.canonical(sanitized(self.fx.engine.state.get_analysis(result["result_id"]))).encode())
        self.fx.start("attempt-2", "stage_2")
        operation = self.fx.optical()
        result = self.cache()
        version = self.fx.task()["version"]
        # Fits alone, but not together with the geometry already stored for this project.
        with patch.object(geometry, "MAX_PROJECT_BYTES", geometry_bytes + quality_bytes - 1):
            reply = self.fx.report([operation["operation_id"]], attempt="attempt-2", quality_result_id=result["result_id"])
        self.assertEqual(reply.get("code"), "MILESTONE_QUALITY_CAPACITY", reply)
        self.assertIsNone(self.table_count())
        self.assertEqual(self.fx.task()["version"], version)
        with patch.object(geometry, "MAX_PROJECT_BYTES", geometry_bytes + quality_bytes + 64):
            reply = self.fx.report([operation["operation_id"]], attempt="attempt-2", quality_result_id=result["result_id"])
        self.assertEqual(reply["status"], "success", reply)


if __name__ == "__main__":
    unittest.main()
