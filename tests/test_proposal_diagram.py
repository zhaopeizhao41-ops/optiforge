"""Structure diagram: validation, persistence and no-write-on-reject.

Software-only: temporary output root and a native session that raises if touched.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import design_workflow as flow  # noqa: E402
from core.proposal_diagram import (  # noqa: E402
    StructureDiagramError,
    describe_structure_diagram,
    validate_structure_diagram,
)
from tools import project_manager as projects  # noqa: E402
from tools.system_tools import (  # noqa: E402
    get_current_design_proposal,
    zemax_register_design_proposal,
)

SPECS = {
    "efl_mm": 50,
    "f_number": 2.8,
    "fov_or_sensor": "像面直径 11 mm",
    "wavelength_range": "400–700 nm",
}

DIAGRAM = {
    "schema_version": 1,
    "nodes": [
        {"kind": "object", "label": "物方", "note": "平行光"},
        {"kind": "lens_group", "label": "前组", "element_count": 2},
        {"kind": "stop", "label": "光阑"},
        {"kind": "lens_group", "label": "后组", "element_count": 1},
        {"kind": "image", "label": "像方"},
    ],
    "caption": "示意，非比例",
}


def raw_register():
    """The undecorated writer, for the 'reject before any write' boundary."""
    import inspect

    return inspect.unwrap(zemax_register_design_proposal)


class ProposalDiagramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in [
            ("OUTPUT_BASE_DIR", self.temp.name),
            ("ACTIVE_PROJECT_FILE", str(Path(self.temp.name) / ".active.json")),
        ]:
            patcher = patch.object(projects, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        native = patch(
            "core.zos_session.ZOSSession.get_instance",
            side_effect=AssertionError("unexpected native session"),
        )
        self.native = native.start()
        self.addCleanup(native.stop)
        projects.set_active_project("test_a")

    # ---- pure validation -------------------------------------------------
    def test_validator_accepts_boundaries_and_normalizes(self):
        self.assertIsNone(validate_structure_diagram(None))
        smallest = {
            "schema_version": 1,
            "nodes": [
                {"kind": "object", "label": "物方"},
                {"kind": "image", "label": "像方"},
            ],
        }
        self.assertEqual(validate_structure_diagram(smallest), smallest)

        widest = {
            "schema_version": 1,
            "nodes": [{"kind": "object", "label": "物方"}]
            + [{"kind": "module", "label": "m" * 80} for _ in range(22)]
            + [{"kind": "image", "label": "像方"}],
            "caption": "c" * 500,
        }
        widest["nodes"][1]["note"] = "n" * 240
        widest["nodes"][1]["kind"] = "lens_group"
        widest["nodes"][1]["element_count"] = 64
        self.assertEqual(len(validate_structure_diagram(widest)["nodes"]), 24)

        normalized = validate_structure_diagram(DIAGRAM)
        self.assertEqual(normalized, DIAGRAM)
        self.assertIn("前组（2 片）", describe_structure_diagram(normalized))

    def test_validator_rejects_out_of_contract_shapes(self):
        def nodes(count=4, first="object", last="image"):
            middle = [{"kind": "module", "label": f"m{i}"} for i in range(count - 2)]
            return [{"kind": first, "label": "a"}] + middle + [
                {"kind": last, "label": "b"}
            ]

        cases = {
            "not object": [],
            "unknown root key": {"schema_version": 1, "nodes": nodes(), "x": 1},
            "version bool": {"schema_version": True, "nodes": nodes()},
            "version 2": {"schema_version": 2, "nodes": nodes()},
            "nodes not list": {"schema_version": 1, "nodes": {}},
            "too few": {
                "schema_version": 1,
                "nodes": [{"kind": "object", "label": "o"}],
            },
            "too many": {"schema_version": 1, "nodes": nodes(25)},
            "first not object": {"schema_version": 1, "nodes": nodes(first="module")},
            "last not image": {"schema_version": 1, "nodes": nodes(last="module")},
            "two stops": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o"},
                    {"kind": "stop", "label": "s1"},
                    {"kind": "stop", "label": "s2"},
                    {"kind": "image", "label": "i"},
                ],
            },
            "count on stop": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o"},
                    {"kind": "stop", "label": "s", "element_count": 1},
                    {"kind": "image", "label": "i"},
                ],
            },
            "bool count": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o"},
                    {"kind": "lens_group", "label": "g", "element_count": True},
                    {"kind": "image", "label": "i"},
                ],
            },
            "count zero": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o"},
                    {"kind": "lens_group", "label": "g", "element_count": 0},
                    {"kind": "image", "label": "i"},
                ],
            },
            "count too big": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o"},
                    {"kind": "lens_group", "label": "g", "element_count": 65},
                    {"kind": "image", "label": "i"},
                ],
            },
            "empty label": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "  "},
                    {"kind": "image", "label": "i"},
                ],
            },
            "long label": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o" * 81},
                    {"kind": "image", "label": "i"},
                ],
            },
            "long note": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o", "note": "n" * 241},
                    {"kind": "image", "label": "i"},
                ],
            },
            "long caption": {"schema_version": 1, "nodes": nodes(), "caption": "c" * 501},
            "unknown node key": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o", "x": 1},
                    {"kind": "image", "label": "i"},
                ],
            },
            "float count": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o"},
                    {"kind": "lens_group", "label": "g", "element_count": 2.0},
                    {"kind": "image", "label": "i"},
                ],
            },
            "caption null": {"schema_version": 1, "nodes": nodes(), "caption": None},
            "note null": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o", "note": None},
                    {"kind": "image", "label": "i"},
                ],
            },
            "lens count null": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o"},
                    {"kind": "lens_group", "label": "g", "element_count": None},
                    {"kind": "image", "label": "i"},
                ],
            },
            "stop count null": {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "o"},
                    {"kind": "stop", "label": "s", "element_count": None},
                    {"kind": "image", "label": "i"},
                ],
            },
        }
        for name, value in cases.items():
            with self.subTest(name):
                with self.assertRaises(StructureDiagramError):
                    validate_structure_diagram(value)

    def test_validator_counts_unicode_code_points(self):
        def with_label(length):
            return {
                "schema_version": 1,
                "nodes": [
                    {"kind": "object", "label": "😀" * length},
                    {"kind": "image", "label": "像方"},
                ],
            }

        self.assertEqual(len("😀"), 1)  # Python len counts one code point
        accepted = validate_structure_diagram(with_label(80))
        self.assertEqual(accepted["nodes"][0]["label"], "😀" * 80)
        with self.assertRaises(StructureDiagramError):
            validate_structure_diagram(with_label(81))

        note = {
            "schema_version": 1,
            "nodes": [
                {"kind": "object", "label": "o", "note": "😀" * 240},
                {"kind": "image", "label": "i"},
            ],
        }
        self.assertEqual(
            validate_structure_diagram(note)["nodes"][0]["note"], "😀" * 240
        )
        note["nodes"][0]["note"] = "😀" * 241
        with self.assertRaises(StructureDiagramError):
            validate_structure_diagram(note)

        caption = {
            "schema_version": 1,
            "nodes": with_label(2)["nodes"],
            "caption": "😀" * 500,
        }
        self.assertEqual(validate_structure_diagram(caption)["caption"], "😀" * 500)
        caption["caption"] = "😀" * 501
        with self.assertRaises(StructureDiagramError):
            validate_structure_diagram(caption)

    # ---- persistence through the real registration path -------------------
    def _save_and_claim(self, request_id):
        view = flow.view()
        view = flow.save_requirements(
            "test_a",
            view["context"],
            "imaging_lens",
            dict(SPECS),
            {key: "user" for key in SPECS},
            user_prompt="隔离结构示意测试",
        )
        flow.submit_intent(
            "test_a", view["context"], request_id, "initial_proposal", "结构示意测试"
        )
        return flow.claim("test_a", request_id, 1, "test-agent", 300)

    def _register(self, claim, **extra):
        view = flow.view()
        return zemax_register_design_proposal(
            project_name="test_a",
            target_specs=view["draft"]["audit"]["result"]["recognized_specs"],
            initial_structure_source="测试来源（模拟）",
            optical_theory_analysis="测试推演",
            glass_selection_rationale="测试选型",
            merit_function_strategy="测试策略",
            mechanical_constraints="测试约束",
            internal_air_spacing_budget="测试预算",
            topology="示意 / 未仿真",
            risks="仅软件测试",
            workflow_context=view["context"],
            task_claim={
                "request_id": claim["task"]["request_id"],
                "version": claim["task"]["version"],
                "lease_token": claim["lease_token"],
            },
            **extra,
        )

    def test_valid_diagram_is_persisted_and_reported(self):
        result = self._register(self._save_and_claim("r1"), structure_diagram=DIAGRAM)
        self.assertEqual(result["status"], "success", result)
        stored = get_current_design_proposal()
        self.assertEqual(stored["structure_diagram"], DIAGRAM)
        self.assertIn("前组（2 片）", result["structure_summary"])
        report = Path(stored["proposal_report_file"]).read_text(encoding="utf-8")
        self.assertIn("方案结构示意（非比例 · 未仿真）", report)
        self.assertIn("物方 → 前组（2 片） → 光阑 → 后组（1 片） → 像方", report)
        self.assertIn("不代表真实光路、通光或像质", report)
        self.native.assert_not_called()

    def test_legacy_call_without_diagram_still_works(self):
        result = self._register(self._save_and_claim("r2"))
        self.assertEqual(result["status"], "success", result)
        stored = get_current_design_proposal()
        self.assertIsNone(stored["structure_diagram"])
        self.assertIsNone(result["structure_summary"])
        # An older proposal whose JSON predates the field stays readable.
        path = Path(stored["proposal_report_file"]).parent.parent / "design_proposal.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("structure_diagram")
        path.write_text(json.dumps(data), encoding="utf-8")
        view = flow.view()
        self.assertNotIn("structure_diagram", view["proposal"])
        self.assertTrue(view["proposal_bound"])

    def test_rejected_diagram_writes_nothing_and_keeps_project(self):
        projects.set_active_project("keep_me")
        keep_dir = Path(self.temp.name) / "keep_me"
        proposal_path = keep_dir / "design_proposal.json"
        proposal_path.write_text(json.dumps({"project_name": "keep_me"}), encoding="utf-8")
        before = proposal_path.read_bytes()

        bad = {"schema_version": 1, "nodes": [{"kind": "module", "label": "x"}], "caption": 1}
        result = raw_register()(
            project_name="keep_me",
            target_specs={"efl_mm": 50},
            initial_structure_source="x",
            optical_theory_analysis="x",
            glass_selection_rationale="x",
            merit_function_strategy="x",
            structure_diagram=bad,
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "STRUCTURE_DIAGRAM_INVALID")
        self.assertEqual(projects.get_active_project_name(), "keep_me")
        self.assertEqual(proposal_path.read_bytes(), before)
        self.assertFalse((Path(self.temp.name) / "other_project").exists())

    def test_changed_diagram_needs_a_new_review(self):
        claim = self._save_and_claim("r3")
        first = self._register(claim, structure_diagram=DIAGRAM)
        flow.update_task(
            "test_a",
            "r3",
            claim["task"]["version"],
            claim["lease_token"],
            "completed",
            result_proposal_hash=first["proposal_hash"],
        )
        view = flow.view()
        self.assertEqual(view["stage"], "proposal_review")
        flow.confirm("test_a", view["context"], "h1")
        self.assertTrue(get_current_design_proposal()["user_confirmed_to_simulate"])

        changed = json.loads(json.dumps(DIAGRAM))
        changed["nodes"][2]["label"] = "系统光阑"
        revision = flow.submit_intent(
            "test_a", flow.view()["context"], "r4", "proposal_revision", "调整结构"
        )
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])
        claim = flow.claim("test_a", "r4", 1, "test-agent", 300)
        second = self._register(claim, structure_diagram=changed)
        self.assertEqual(second["status"], "success", second)
        self.assertEqual(second["proposal_revision"], 2)
        self.assertNotEqual(second["proposal_hash"], first["proposal_hash"])
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])
        self.native.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
