"""Explicit milestone contracts with temporary storage and fake optics only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import design_workflow as flow
from core.design_milestones import OBSERVATION
from core.workbench_engine import EngineWorker
from tools import project_manager as projects
from tools.collaboration_tools import zemax_design_workflow, zemax_claim_design_task, zemax_update_design_task
from tools.system_tools import zemax_register_design_proposal, zemax_confirm_design_proposal, get_current_design_proposal


class MilestoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="milestone-software-")
        self.addCleanup(self.temp.cleanup)
        for name, value in (("OUTPUT_BASE_DIR", self.temp.name),
                            ("ACTIVE_PROJECT_FILE", str(Path(self.temp.name) / ".active.json"))):
            patcher = patch.object(projects, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        native = patch("core.zos_session.ZOSSession.get_instance", side_effect=AssertionError("real native forbidden"))
        self.native = native.start()
        self.addCleanup(native.stop)
        self.project = "milestone_test"
        projects.set_active_project(self.project)
        self.specs = {"efl_mm": 50, "f_number": 2.8, "fov_or_sensor": "像面直径 11 mm",
                      "wavelength_range": "400–700 nm"}
        self.save_requirements()
        flow.submit_intent(self.project, self.context(), "research", "initial_proposal", "隔离软件测试，不是真实光学设计")
        research = flow.claim(self.project, "research", 1, "test-researcher", 300)
        proposal = zemax_register_design_proposal(
            project_name=self.project, target_specs=flow.view(self.project)["draft"]["audit"]["result"]["recognized_specs"],
            initial_structure_source="模拟文献记录", optical_theory_analysis="模拟理论", glass_selection_rationale="模拟玻璃",
            merit_function_strategy="模拟策略", mechanical_constraints="模拟边界", internal_air_spacing_budget="模拟预算",
            topology="示意，未仿真", risks="软件测试禁止真实仿真", workflow_context=self.context(),
            task_claim={"request_id": "research", "version": research["task"]["version"], "lease_token": research["lease_token"]})
        self.assertEqual(proposal["status"], "success")
        flow.update_task(self.project, "research", research["task"]["version"], research["lease_token"],
                         "completed", result_proposal_hash=proposal["proposal_hash"])
        flow.confirm(self.project, self.context(), "handoff")
        self.claim = flow.claim(self.project, "handoff", 1, "test-optical-agent", 300)
        self.value, self.configuration, self.captures = 1, 1, 0
        self.capture_fails = False
        self.result = {"status": "success"}
        self.session = object()
        self.engine = self.make_engine()
        self.observe()
        self.addCleanup(lambda: self.engine.executor.shutdown())

    def make_engine(self):
        def mutate():
            self.value += 1
            return deepcopy(self.result)
        return EngineWorker({"zemax_design_workflow": zemax_design_workflow,
            "zemax_claim_design_task": zemax_claim_design_task, "zemax_update_design_task": zemax_update_design_task,
            "zemax_get_workbench_snapshot": self.capture, "zemax_run_optimization": mutate,
            "zemax_run_hammer": mutate, "zemax_apply_prescription": mutate,
            "zemax_new_file": mutate, "zemax_run_wavefront_map": mutate}, metadata=lambda: {
                "active_project": projects.get_active_project_name(), "model_project": self.project,
                "proposal_confirmed": get_current_design_proposal().get("user_confirmed_to_simulate") is True,
                "session_identity": self.session})

    def capture(self):
        self.captures += 1
        if self.capture_fails:
            raise RuntimeError("simulated capture unavailable")
        return {"status": "success", "schema_version": 1, "observed_at": "fixture",
                "model": {"project": self.project, "configuration": self.configuration, "units": "mm",
                          "surfaces": [{"index": 1, "radius": self.value}]},
                "layout": {"status": "ready", "surfaces": []}, "capabilities": {"ray_trace": False}}

    def context(self):
        return flow.view(self.project)["context"]

    def save_requirements(self, **extra):
        specs = {**self.specs, **extra}
        return flow.save_requirements(self.project, self.context(), "imaging_lens", specs,
                                     {key: "user" for key in specs}, user_prompt="隔离软件测试")

    def task(self):
        return next(t for t in flow.task_list(self.project)["tasks"] if t["request_id"] == "handoff")

    def call(self, action="view", payload=None, expected=None):
        args = {"action": action, "project_name": self.project}
        if action != "view":
            args.update(payload=payload, expected_workflow=self.context() if expected is None else expected)
        return self.engine.call_envelope("zemax_design_workflow", args, expected_context=self.engine.state.context())["result"]

    def observe(self):
        return self.engine.call("zemax_get_workbench_snapshot")

    def optical(self, tool="zemax_run_optimization"):
        return self.engine.call_envelope(tool, expected_context=self.engine.state.context())

    def start(self, attempt="attempt-1", stage="stage_1", **extra):
        payload = {"request_id": "handoff", "version": self.task()["version"],
                   "lease_token": self.claim["lease_token"], "stage_id": stage,
                   "attempt_id": attempt, "note": "显式阶段范围，非光学通过", **extra}
        return self.call("milestone_start", payload)

    def report(self, operations=None, status="needs_review", attempt="attempt-1", **extra):
        payload = {"request_id": "handoff", "version": self.task()["version"],
                   "lease_token": self.claim["lease_token"], "attempt_id": attempt, "status": status,
                   "operation_ids": [] if operations is None else operations, "note": "请核对阶段记录", **extra}
        return self.call("milestone_report", payload)

    def attempt(self, view=None, stage=0):
        return (view or self.call())["optimization_milestones"]["stages"][stage]["attempts"][0]

    def assert_error(self, result, code):
        self.assertEqual(result.get("status"), "error", result)
        self.assertEqual(result.get("code"), code, result)

    def stored(self):
        with flow.transaction() as db:
            return [tuple(row) for row in db.execute("SELECT id, data FROM tasks ORDER BY id")]

    def test_empty_and_successful_optics_never_infer_a_stage(self):
        self.optical()
        self.optical("zemax_run_hammer")
        milestone = self.call()["optimization_milestones"]
        self.assertEqual([stage["status"] for stage in milestone["stages"]], ["not_started"] * 4)
        self.assertEqual(milestone["storage"], "project")
        self.native.assert_not_called()

    def test_start_report_sources_raw_status_and_no_extra_capture(self):
        captures, lease, version = self.captures, self.task()["lease_until"], self.task()["version"]
        started = self.start()
        self.assertEqual(self.captures, captures)
        self.assertEqual(self.task()["version"], version + 1)
        self.assertEqual(self.task()["lease_until"], lease)
        initial = self.attempt(started)
        self.assertEqual(initial["status"], "running")
        self.assertEqual(initial["source"]["proposal_hash"], self.context()["proposal_hash"])
        result = self.optical()
        reported = self.report([result["operation_id"]])
        record = self.attempt(reported)
        self.assertEqual(record["status"], "needs_review")
        self.assertEqual(record["source"], initial["source"])
        self.assertGreater(record["end_source"]["model_revision"], initial["source"]["model_revision"])
        operation = record["operations"][0]
        self.assertEqual(operation["result_status"], "success")
        self.assertEqual(operation["observed_source"]["model_revision"], record["end_source"]["model_revision"])
        self.assertNotIn("snapshot", operation)
        self.assertNotIn("lease_token", json.dumps(reported))
        self.assertNotIn("token_hash", json.dumps(reported))
        before = self.stored()
        self.call(); self.call()
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.captures, captures + 1)
        self.assertIsNone(OBSERVATION.get())

    def test_cas_duplicate_ids_and_single_live_project_attempt(self):
        old_version = self.task()["version"]
        self.start()
        self.assert_error(self.start(version=old_version), "WORKFLOW_TASK_CONFLICT")
        self.assert_error(self.start(), "MILESTONE_ID_CONFLICT")
        version = self.task()["version"]
        self.assert_error(self.start("attempt-2", "stage_2"), "MILESTONE_ALREADY_RUNNING")
        self.assertEqual(self.task()["version"], version)

    def test_reports_finalize_once_and_no_passed_input(self):
        self.start()
        self.assert_error(self.report(status="passed"), "MILESTONE_INPUT_INVALID")
        self.report(status="failed")
        self.assert_error(self.report(status="failed"), "MILESTONE_ALREADY_REPORTED")
        self.assertEqual(len(self.attempt()["operations"]), 0)

    def test_direct_calls_missing_observation_fail_closed(self):
        result = zemax_design_workflow("milestone_start", self.project, {
            "request_id": "handoff", "version": self.task()["version"], "lease_token": self.claim["lease_token"],
            "stage_id": "stage_1", "attempt_id": "direct", "note": "无工作台来源"}, self.context())
        self.assert_error(result, "MILESTONE_OBSERVATION_REQUIRED")
        self.assertEqual(self.call()["optimization_milestones"]["stages"][0]["attempts"], [])

    def test_input_validation_does_not_persist(self):
        before = self.stored()
        for extra in ({"note": ""}, {"note": "x" * 2001}, {"stage_id": "stage_5"},
                      {"attempt_id": ""}, {"lease_token": "bad"}):
            self.assertEqual(self.start(**extra)["status"], "error")
        self.assertEqual(self.stored(), before)
        self.start()
        for ids in ([], ["unknown"], ["x", "x"], ["x"] * 13, "not-a-list"):
            self.assertEqual(self.report(ids)["status"], "error")
        self.assertEqual(self.attempt()["status"], "running")

    def test_prestart_evicted_and_knowledge_operations_are_rejected_atomically(self):
        earlier = self.optical()["operation_id"]
        self.start()
        self.assert_error(self.report([earlier]), "MILESTONE_EVIDENCE_INVALID")
        read = self.engine.call_envelope("zemax_design_workflow", {"action": "view", "project_name": self.project})
        self.assert_error(self.report([read["operation_id"]], status="failed"), "MILESTONE_EVIDENCE_INVALID")
        good = self.optical()["operation_id"]
        self.engine.state.optical_history.entries.clear()
        self.assert_error(self.report([good]), "MILESTONE_EVIDENCE_INVALID")
        self.assert_error(self.report([good, "absent"]), "MILESTONE_EVIDENCE_INVALID")
        self.assertEqual(self.attempt()["operations"], [])

    def test_warning_zero_variables_is_not_geometry_evidence(self):
        self.start()
        self.result = {"status": "warning", "variables": 0}
        result = self.optical()
        recent = self.engine.state.read()["tasks"]["recent"][0]
        self.assertEqual(recent["status"], "succeeded")
        self.assertEqual(recent["result_status"], "warning")
        self.assert_error(self.report([result["operation_id"]]), "MILESTONE_EVIDENCE_INVALID")
        failure = self.report([result["operation_id"]], status="failed")
        self.assertIsNone(self.attempt(failure)["operations"][0]["observed_source"])

    def test_failed_report_preserves_real_failure_and_rollback_metadata(self):
        self.start()
        self.result = {"status": "error", "code": "SIMULATED_FAILURE", "rolled_back": True}
        result = self.optical()
        view = self.report([result["operation_id"]], status="failed")
        record = self.attempt(view)
        self.assertEqual(record["declared_status"], "failed")
        self.assertEqual(record["operations"][0]["code"], "SIMULATED_FAILURE")
        self.assertTrue(record["operations"][0]["rolled_back"])
        self.assertIsNone(record["operations"][0]["observed_source"])

    def test_failed_unobserved_allowed_but_stale_and_success_report_rejected(self):
        self.start()
        self.capture_fails = True
        result = self.optical()
        self.assert_error(self.report([result["operation_id"]]), "MILESTONE_SOURCE_STALE")
        reported = self.report([result["operation_id"]], status="failed")
        record = self.attempt(reported)
        self.assertEqual(record["status"], "stale")
        self.assertEqual(record["declared_status"], "failed")
        self.assertIsNone(record["end_source"])
        self.assertIn("observation_unavailable", record["stale_reasons"])
        self.capture_fails = False
        self.observe()
        self.assertIn("observation_unavailable", self.attempt()["stale_reasons"])

    def test_current_revision_must_equal_latest_cited_observation(self):
        self.start()
        old = self.optical()["operation_id"]
        new = self.optical()["operation_id"]
        self.assertEqual(self.attempt()["status"], "running")
        self.assert_error(self.report([old]), "MILESTONE_SOURCE_STALE")
        self.assertEqual(self.attempt()["status"], "running")
        self.assertEqual(self.attempt(self.report([new, old]))["status"], "needs_review")
        self.optical()
        self.assertIn("model_revision_changed", self.attempt()["stale_reasons"])

    def test_failure_from_replaced_unobserved_model_cannot_attach_to_old_attempt(self):
        self.start()
        self.capture_fails = True
        replacement = self.optical("zemax_new_file")["operation_id"]
        self.assertIsNone(self.engine.state.workflow_observation()["source"])
        self.assertIn("model_changed", self.attempt()["stale_reasons"])
        self.assert_error(self.report([replacement], status="failed"), "MILESTONE_SOURCE_STALE")

    def test_failed_receipt_identity_is_checked_and_private_metadata_never_persists(self):
        self.start()
        operation = self.engine.state.enqueue("zemax_run_optimization", "software-fixture")
        self.engine.state.start(operation)
        self.engine.state.finish(operation, "failed", {"status": "error", "rolled_back": True})
        receipt = self.engine.state.history[0]
        original = deepcopy(receipt["completed_context"])
        for key in ("service_instance_id", "model_id", "model_project"):
            receipt["completed_context"] = {**original, key: "foreign"}
            self.assert_error(self.report([operation], status="failed"), "MILESTONE_EVIDENCE_INVALID")
        receipt["completed_context"] = original
        reported = self.report([operation], status="failed")
        self.assertEqual(self.attempt(reported)["declared_status"], "failed")
        self.assertNotIn("_context", json.dumps(reported))
        with flow.transaction() as db:
            raw = db.execute("SELECT data FROM tasks WHERE id='handoff'").fetchone()[0]
        self.assertNotIn("_context", raw)

    def test_config_and_model_replacement_cannot_report_old_attempt(self):
        self.start()
        self.configuration = 2
        self.observe()
        self.assertIn("configuration_changed", self.attempt()["stale_reasons"])
        self.assert_error(self.report(status="failed"), "MILESTONE_SOURCE_STALE")
        self.start("second", "stage_2")
        self.optical("zemax_new_file")
        record = self.attempt(stage=1)
        self.assertIn("model_changed", record["stale_reasons"])

    def test_requirements_and_reapproval_invalidate_old_records(self):
        self.start()
        zemax_confirm_design_proposal(self.project, False)
        self.assertIn("approval_revoked", self.attempt()["stale_reasons"])
        zemax_confirm_design_proposal(self.project, True, workflow_context=self.context())
        self.assertIn("authorization_changed", self.attempt()["stale_reasons"])
        # A view while revoked retires the old claim; reapproval never revives it.
        self.assert_error(self.report(status="failed"), "WORKFLOW_LEASE_INVALID")
        self.save_requirements(efl_mm=60)
        self.assertIn("requirements_changed", self.attempt()["stale_reasons"])

    def test_lease_expiry_explicit_retry_and_reclaim_never_resume_old_attempt(self):
        self.start()
        now = time.time()
        with patch("core.design_workflow.time.time", return_value=now + 400):
            self.assertIn("lease_expired", self.attempt()["stale_reasons"])
            self.assert_error(self.report(status="failed"), "WORKFLOW_LEASE_INVALID")
            retried = flow.retry_task(self.project, self.context(), "handoff", self.task()["version"])
            handoff = next(t for t in retried["tasks"] if t["request_id"] == "handoff")
            self.claim = flow.claim(self.project, "handoff", handoff["version"], "new-agent", 300)
            self.assertIn("claim_changed", self.attempt()["stale_reasons"])
            self.start("reclaimed", "stage_2")
            self.assertEqual(self.attempt(stage=1)["status"], "running")

    def test_restart_persistent_metadata_readonly_and_old_operations_unusable(self):
        self.start()
        operation = self.optical()["operation_id"]
        self.report([operation])
        persisted = self.stored()
        old_instance = self.engine.state.context()["service_instance_id"]
        self.engine.executor.shutdown()
        self.engine = self.make_engine()
        self.observe()
        current = self.call()
        self.assertNotEqual(current["optimization_milestones"]["service_instance_id"], old_instance)
        self.assertIn("service_instance_changed", self.attempt(current)["stale_reasons"])
        self.call()
        self.assertEqual(self.stored(), persisted)
        self.assertEqual(self.engine.state.read()["history_revision"], 0)

    def test_completed_handoff_does_not_invent_or_complete_stages(self):
        self.start()
        operation = self.optical()["operation_id"]
        self.report([operation])
        flow.update_task(self.project, "handoff", self.task()["version"], self.claim["lease_token"],
                         "completed", "仅交接文字，不是光学完成")
        current = self.call()["optimization_milestones"]
        self.assertEqual(current["stages"][0]["status"], "needs_review")
        self.assertEqual([s["status"] for s in current["stages"][1:]], ["not_started"] * 3)

    def test_capacity_is_bounded_and_rejects_without_deleting_history(self):
        for index in range(16):
            self.assertEqual(self.start("attempt-" + str(index))["status"], "success")
            self.assertEqual(self.report(status="failed", attempt="attempt-" + str(index))["status"], "success")
        version = self.task()["version"]
        self.assert_error(self.start("over-capacity"), "MILESTONE_CAPACITY_REACHED")
        self.assertEqual(self.task()["version"], version)
        self.assertEqual(len(self.call()["optimization_milestones"]["stages"][0]["attempts"]), 16)

    def test_observation_contains_only_bounded_detached_metadata(self):
        self.start()
        operation = self.optical()["operation_id"]
        observation = self.engine.state.workflow_observation()
        receipt = next(item for item in observation["operations"] if item["operation_id"] == operation)
        self.assertNotIn("surfaces", json.dumps(observation))
        receipt["observed_source"]["model_id"] = "mutated"
        self.assertNotEqual(self.engine.state.workflow_observation()["source"]["model_id"], "mutated")
        self.assertEqual(self.attempt(self.report([operation]))["status"], "needs_review")

    def test_calculation_partial_is_failure_metadata_never_geometry_success(self):
        self.start()
        operation = self.engine.state.enqueue("__analysis__", "software-fixture")
        self.engine.state.start(operation)
        self.engine.state.finish(operation, "succeeded", {"status": "partial", "code": "PARTIAL"})
        self.assert_error(self.report([operation]), "MILESTONE_EVIDENCE_INVALID")
        record = self.attempt(self.report([operation], status="failed"))
        self.assertEqual(record["operations"][0]["result_status"], "partial")
        self.assertIsNone(record["operations"][0]["observed_source"])


if __name__ == "__main__":
    unittest.main()
