"""Offline regressions for the analysis / tolerance / mechanical tool surface.

These tests need NO OpticStudio license: they exercise the MCP-facing contracts that
previously broke silently, plus the pure physics helpers that the tools rely on.

Run: python -B tests/regression_stage_three.py

What is covered:
  1. No exported tool leaks a `session` first parameter (the defect that made ten
     tools return a bind error for every call).
  2. Every `server.py` call site still binds against the real tool signature.
  3. The Step 3 -> Step 4 -> confirm gate actually opens (the workflow deadlock).
  4. Zemax math helpers: mode field radius, Fresnel reflectance, standard OD series.
"""
import ast
import inspect
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools as tools_pkg
from tools import project_manager as projects
from tools import confocal_tools, tolerance_tools, mechanical_tools
from tools.system_tools import (
    zemax_register_design_proposal,
    zemax_confirm_design_proposal,
    get_current_design_proposal,
)


class ToolSignatureContract(unittest.TestCase):
    """The defect class that broke ten tools at once."""

    def test_no_tool_exposes_session(self):
        offenders = []
        for name in tools_pkg.__all__:
            tool = getattr(tools_pkg, name)
            try:
                params = inspect.signature(tool).parameters
            except (TypeError, ValueError) as error:
                offenders.append(f"{name}: signature unavailable ({error})")
                continue
            if "session" in params:
                offenders.append(f"{name}{inspect.signature(tool)}")
        self.assertEqual(offenders, [], f"Tools must not expose `session`: {offenders}")

    def test_server_call_sites_bind(self):
        """Walk server.py and bind every call against its imported tool signature."""
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        alias = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "tools":
                for imported in node.names:
                    if imported.asname:
                        alias[imported.asname] = imported.name

        failures = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            target = alias.get(node.func.id)
            if target is None or not hasattr(tools_pkg, target):
                continue
            signature = inspect.signature(getattr(tools_pkg, target))
            try:
                signature.bind(*[None] * len(node.args),
                               **{kw.arg: None for kw in node.keywords})
            except TypeError as error:
                failures.append(f"server.py:{node.lineno} {target}: {error}")
        self.assertEqual(failures, [], f"Call sites do not match signatures: {failures}")


class ConfirmationGate(unittest.TestCase):
    """The Step 4 deadlock: registering alone must not release the gate."""

    @classmethod
    def setUpClass(cls):
        cls.output = Path(tempfile.mkdtemp(prefix="zemax-stage-three-"))
        cls.old_paths = projects.OUTPUT_BASE_DIR, projects.ACTIVE_PROJECT_FILE
        projects.OUTPUT_BASE_DIR = str(cls.output / "projects")
        projects.ACTIVE_PROJECT_FILE = str(cls.output / "projects" / ".active_project.json")

    @classmethod
    def tearDownClass(cls):
        projects.OUTPUT_BASE_DIR, projects.ACTIVE_PROJECT_FILE = cls.old_paths

    def _register(self, name, confirmed=False):
        return zemax_register_design_proposal(
            project_name=name,
            target_specs={"efl_mm": 10.0},
            initial_structure_source="fixture",
            optical_theory_analysis="fixture",
            glass_selection_rationale="fixture",
            merit_function_strategy="fixture",
            user_confirmed_to_simulate=confirmed,
        )

    def test_register_alone_leaves_gate_closed(self):
        self._register("gate_closed_case")
        proposal = get_current_design_proposal()
        self.assertIsNot(proposal.get("user_confirmed_to_simulate"), True,
                         "Registering a proposal must not authorize simulation by itself.")

    def test_confirm_opens_gate_without_reregistering(self):
        result = self._register("gate_open_case")
        self.assertEqual(result["status"], "success")
        confirm = zemax_confirm_design_proposal(user_confirmed_to_simulate=True)
        self.assertEqual(confirm["status"], "success")
        self.assertTrue(confirm["simulation_authorized"])
        self.assertIs(get_current_design_proposal().get("user_confirmed_to_simulate"), True)

    def test_confirm_can_revoke(self):
        self._register("gate_revoke_case")
        zemax_confirm_design_proposal(user_confirmed_to_simulate=True)
        revoke = zemax_confirm_design_proposal(user_confirmed_to_simulate=False)
        self.assertEqual(revoke["status"], "success")
        self.assertFalse(revoke["simulation_authorized"])
        self.assertIs(get_current_design_proposal().get("user_confirmed_to_simulate"), False)

    def test_confirm_without_proposal_fails_cleanly(self):
        projects.set_active_project("no_such_proposal_case")
        result = zemax_confirm_design_proposal(user_confirmed_to_simulate=True)
        self.assertEqual(result["status"], "error")


class OperandSlotTyping(unittest.TestCase):
    """GetOperandValue has exactly one overload whose `srf`/`wave` slots are Int32.

    pythonnet does not narrow a Python float to Int32, so an operand called with 0.0 in
    those slots raises and every caller that swallowed the exception degraded silently
    (edge thickness returned None, sag read as 0). Assert the coercion contract instead
    of relying on a live license.
    """

    def test_coerces_integer_slots(self):
        import core.operands as operands

        captured = {}

        class _OperandType:
            ETGT = "ETGT-sentinel"

        class _MFE:
            def GetOperandValue(self, op_type, *args):
                captured["args"] = args
                captured["types"] = [type(a).__name__ for a in args]
                return 1.5

        class _Editors:
            class MFE:
                MeritOperandType = _OperandType

        class _ZOSAPI:
            Editors = _Editors

        class _System:
            MFE = _MFE()

        class _Session:
            ZOSAPI = _ZOSAPI
            system = _System()

        value = operands._operand(_Session(), "ETGT", 1.0, 0.0, 0.0, 0.0)

        self.assertEqual(value, 1.5)
        self.assertEqual(len(captured["types"]), 8, "operand must fill all eight slots")
        self.assertEqual(captured["types"][:2], ["int", "int"],
                         "srf and wave must be Int32 to match the CLR overload")
        self.assertEqual(captured["types"][2:], ["float"] * 6,
                         "the six remaining slots are Double")
        self.assertEqual(captured["args"][0], 1, "a float srf must still coerce to int")


class PhysicHelpers(unittest.TestCase):
    def test_single_mode_marcuse_radius(self):
        # 4 um core, NA 0.14 at 1.55 um -> V ~ 2.27, w slightly above the core radius.
        v, w, single = confocal_tools._mode_field_radius_um(4.0, 0.14, 1.55)
        self.assertAlmostEqual(v, 2.27, places=1)
        self.assertTrue(single)
        self.assertGreater(w, 4.0)
        self.assertLess(w, 5.5)

    def test_multimode_uses_core_radius(self):
        v, w, single = confocal_tools._mode_field_radius_um(31.25, 0.22, 0.85)
        self.assertFalse(single)
        self.assertEqual(w, 31.25)
        self.assertGreater(v, 2.405)

    def test_fresnel_normal_incidence(self):
        # Bare glass-air interface reflects ~4% at normal incidence.
        self.assertAlmostEqual(tolerance_tools._fresnel_reflectance(1.5, 1.0, 0.0), 0.04, places=3)
        self.assertAlmostEqual(tolerance_tools._fresnel_reflectance(1.0, 1.5, 0.0), 0.04, places=3)

    def test_fresnel_total_internal_reflection(self):
        self.assertEqual(tolerance_tools._fresnel_reflectance(1.5, 1.0, 60.0), 1.0)

    def test_standard_od_snapping(self):
        self.assertEqual(mechanical_tools._standard_od(12.5), 16.0)
        self.assertEqual(mechanical_tools._standard_od(18.0), 25.4)
        self.assertEqual(mechanical_tools._standard_od(30.0), 30.0)
        self.assertEqual(mechanical_tools._standard_od(40.0), 40.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
