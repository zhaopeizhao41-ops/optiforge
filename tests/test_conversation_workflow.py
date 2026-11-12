"""Proposals registered and approved in an Agent conversation reach the workbench record.

Isolated output directory; never opens OpticStudio.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import design_workflow as flow  # noqa: E402
from core import design_milestones  # noqa: E402
from tools import project_manager as projects  # noqa: E402
from tools.system_tools import (get_current_design_proposal, zemax_confirm_design_proposal,  # noqa: E402
                                zemax_register_design_proposal)

PROJECT = "conversation_test"
SPECS = {"efl_mm": 50, "f_number": 4, "fov_or_sensor": "11 mm",
         "wavelength_range": "486.1–656.3 nm", "mtf_gate": "MTF@30 ≥ 0.3"}


class ConversationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (("OUTPUT_BASE_DIR", self.temp.name),
                            ("ACTIVE_PROJECT_FILE", str(Path(self.temp.name) / ".active.json"))):
            patcher = patch.object(projects, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        native = patch("core.zos_session.ZOSSession.get_instance",
                       side_effect=AssertionError("native session forbidden"))
        native.start()
        self.addCleanup(native.stop)
        projects.set_active_project(PROJECT)

    def register(self, specs=None, **extra):
        return zemax_register_design_proposal(
            project_name=PROJECT, target_specs=dict(SPECS if specs is None else specs),
            initial_structure_source="测试来源", optical_theory_analysis="测试推演",
            glass_selection_rationale="测试玻璃", merit_function_strategy="测试策略",
            mechanical_constraints="测试边界", internal_air_spacing_budget="测试间隔",
            topology="测试拓扑", risks="仅软件测试", **extra)

    def test_registration_writes_audited_requirements_and_binds_proposal(self):
        result = self.register()
        self.assertEqual(result["status"], "success")
        self.assertIn("已写入工作台工程记录", result["workbench_record"])
        view = flow.view(PROJECT)
        self.assertEqual(view["draft"]["origin"], "conversation")
        # Only template keys become requirements; the gate note stays in the proposal.
        self.assertNotIn("mtf_gate", view["draft"]["specs"])
        self.assertTrue(view["proposal_bound"])
        self.assertEqual(view["stage"], "proposal_review")

    def test_incomplete_requirements_stay_unbound(self):
        result = self.register({"efl_mm": 50})
        self.assertEqual(result["status"], "success")
        self.assertIn("未写入", result["workbench_record"])
        self.assertFalse(flow.view(PROJECT)["proposal_bound"])

    def test_conversation_approval_creates_and_claims_a_handoff(self):
        self.register()
        result = zemax_confirm_design_proposal(PROJECT, True)
        self.assertEqual(result["status"], "success", result)
        handoff = result["model_handoff"]
        self.assertEqual(handoff["status"], "claimed")
        self.assertTrue(handoff["lease_token"])
        self.assertTrue(get_current_design_proposal()["user_confirmed_to_simulate"])
        view = flow.view(PROJECT)
        self.assertEqual(view["stage"], "handoff")
        task = next(t for t in view["tasks"] if t["kind"] == "model_handoff")
        self.assertFalse(task["stale"])
        # The handoff is the claim milestone_start needs: verify_lease accepts it.
        with flow.transaction() as db:
            stored = flow._task(db, PROJECT, handoff["request_id"], handoff["version"])
            flow.verify_lease(stored, handoff["lease_token"])

    def test_repeat_approval_reports_the_existing_handoff(self):
        self.register()
        first = zemax_confirm_design_proposal(PROJECT, True)["model_handoff"]
        second = zemax_confirm_design_proposal(PROJECT, True)["model_handoff"]
        self.assertNotIn("lease_token", second)
        handoffs = [t for t in flow.view(PROJECT)["tasks"]
                    if t["kind"] == "model_handoff" and not t["stale"]]
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(handoffs[0]["request_id"], first["request_id"])

    def test_changed_requirements_supersede_the_old_handoff(self):
        self.register()
        old = zemax_confirm_design_proposal(PROJECT, True)["model_handoff"]
        self.register({**SPECS, "efl_mm": 60})
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])
        task = next(t for t in flow.view(PROJECT)["tasks"] if t["request_id"] == old["request_id"])
        self.assertTrue(task["stale"])

    def test_revocation_needs_no_handoff_and_closes_the_gate(self):
        self.register()
        zemax_confirm_design_proposal(PROJECT, True)
        result = zemax_confirm_design_proposal(PROJECT, False)
        self.assertNotIn("model_handoff", result)
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])

    def test_workbench_edits_take_the_draft_back_from_the_conversation(self):
        self.register()
        view = flow.view(PROJECT)
        specs = dict(view["draft"]["specs"], efl_mm=55)
        flow.save_requirements(PROJECT, view["context"], view["draft"]["system_type"],
                               specs, {k: "user" for k in specs})
        self.assertNotIn("origin", flow.view(PROJECT)["draft"])
        # Now a conversation registration must follow the claimed-task rules again.
        self.assertEqual(self.register()["status"], "error")

    def test_running_stage_sees_the_conversation_handoff_only_while_a_stage_is_open(self):
        self.register()
        zemax_confirm_design_proposal(PROJECT, True)
        self.assertIsNone(design_milestones.running_stage(PROJECT))


if __name__ == "__main__":
    unittest.main()
