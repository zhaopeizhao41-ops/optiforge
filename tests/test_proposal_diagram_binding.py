"""Optional review diagrams must bind approvals without invalidating legacy hashes."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import design_workflow as flow


class ProposalDiagramBindingTests(unittest.TestCase):
    def setUp(self):
        self.draft = {"revision": 1, "submitted_revision": 1,
                      "audit": {"revision": 1, "result": {"is_complete": True}}}
        self.proposal = {"project_name": "diagram_test", "target_specs": {"efl_mm": 50},
                         "proposal_revision": 1, "requirements_revision": 1,
                         "requirements_snapshot": deepcopy(self.draft), "research_task_id": "research"}
        self.diagram = {"schema_version": 1, "nodes": [
            {"kind": "object", "label": "物方"},
            {"kind": "lens_group", "label": "前组", "element_count": 2},
            {"kind": "image", "label": "像方"}]}

    def test_legacy_missing_and_none_preserve_stored_hash(self):
        # Exact old digest for a fixed on-disk fixture, independent of new helper behavior.
        legacy_keys = ("project_name", "target_specs", "initial_structure_source",
                       "optical_theory_analysis", "glass_selection_rationale", "merit_function_strategy",
                       "internal_air_spacing_budget", "mechanical_constraints", "topology", "risks",
                       "proposal_revision", "requirements_revision", "requirements_snapshot", "research_task_id")
        old_hash = hashlib.sha256(flow.encoded({key: self.proposal.get(key) for key in legacy_keys}).encode()).hexdigest()
        self.assertEqual(flow.proposal_hash(self.proposal), old_hash)
        self.proposal.update(structure_diagram=None, proposal_hash=old_hash)
        self.assertEqual(flow.proposal_hash(self.proposal), old_hash)
        self.assertTrue(flow._binding_valid(self.draft, self.proposal))

    def test_changed_review_diagram_invalidates_binding_and_authorized_handoff(self):
        self.proposal["structure_diagram"] = deepcopy(self.diagram)
        self.proposal["proposal_hash"] = flow.proposal_hash(self.proposal)
        self.proposal["user_confirmed_to_simulate"] = True
        self.draft["approval"] = self.proposal["proposal_hash"]
        task = {"status": "pending", "requirements_revision": 1, "kind": "model_handoff",
                "proposal_hash": self.proposal["proposal_hash"]}
        self.assertTrue(flow._binding_valid(self.draft, self.proposal))
        self.assertTrue(flow._task_valid(task, self.draft, self.proposal))
        self.proposal["structure_diagram"]["nodes"][1]["element_count"] = 3
        self.assertFalse(flow._binding_valid(self.draft, self.proposal))
        self.assertFalse(flow._task_valid(task, self.draft, self.proposal))

    def test_diagram_addition_and_removal_require_fresh_review(self):
        legacy_hash = flow.proposal_hash(self.proposal)
        self.proposal["structure_diagram"] = deepcopy(self.diagram)
        diagram_hash = flow.proposal_hash(self.proposal)
        self.assertNotEqual(diagram_hash, legacy_hash)
        self.proposal["proposal_hash"] = diagram_hash
        del self.proposal["structure_diagram"]
        self.assertFalse(flow._binding_valid(self.draft, self.proposal))


if __name__ == "__main__":
    unittest.main()
