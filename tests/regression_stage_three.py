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
  5. Industry-norm formulas: Marechal/Strehl, Karow centering, concentricity, grating
     equation and spectrograph layout, spherical-mirror wavefront, Shafer coma-free angle.
"""
import ast
import inspect
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools as tools_pkg
from domain.zemax_rules import OpticalRuleCheck
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


class IndustryNormHelpers(unittest.TestCase):
    """The fabrication / spectroscopy formulas the gates and the audit both rely on.

    Each helper is the single source of the formula, so these tests pin the physics rather
    than the implementation: a wrong constant here would silently weaken every gate that
    quotes it. See domain/optical_expert_manual.md section 11.
    """

    def setUp(self):
        self.rules = OpticalRuleCheck()

    # --- Marechal / Strehl (image-quality acceptance) ------------------------------
    def test_rms_for_strehl_marechal_pairs(self):
        # S = 0.8 is the diffraction-limited threshold (the handbook figure ~lambda/13.4);
        # S = 0.82 is the classic lambda/14 Marechal number. The handbook fractions are
        # rounded, so compare within 1% rather than exactly.
        self.assertAlmostEqual(self.rules.rms_for_strehl(0.8), 1 / 13.4, delta=0.001)
        self.assertAlmostEqual(self.rules.rms_for_strehl(0.82), 1 / 14.0, delta=0.001)

    def test_rms_for_strehl_inverts_estimate_strehl_ratio(self):
        w = self.rules.rms_for_strehl(0.82)
        self.assertAlmostEqual(self.rules.estimate_strehl_ratio(w), 0.82, places=6)

    def test_rms_for_strehl_rejects_invalid_input(self):
        for bad in (0.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                self.rules.rms_for_strehl(bad)

    # --- Karow centering factor ---------------------------------------------------
    def test_karow_equiconvex_cannot_self_center(self):
        # |20/100 + 20/(-100)| = 0: a symmetric biconvex has no centering self-alignment.
        self.assertAlmostEqual(self.rules.karow_factor(20.0, 100.0, 20.0, -100.0), 0.0, places=9)

    def test_karow_meniscus_self_centers(self):
        # |20/50 + 20/(-100)| = 0.2 ... one-surface meniscus; |20/50 + 20/80| = 0.65 > 0.56.
        self.assertGreater(self.rules.karow_factor(20.0, 50.0, 20.0, 80.0), 0.56)

    def test_karow_ignores_flat_surfaces(self):
        self.assertAlmostEqual(self.rules.karow_factor(20.0, 0.0, 20.0, -100.0), 0.2, places=9)

    # --- Concentricity margin ------------------------------------------------------
    def test_concentric_convex_pair_has_small_margin(self):
        self.assertAlmostEqual(self.rules.concentricity_margin(100.0, 95.0, 3.0), 2.0, places=9)

    def test_concentricity_margin_uses_plus_ct_for_concave(self):
        # Concave pair uses |R1 - R2 + CT| = |-100 + 95 + 3| = 2; the convex branch would
        # give |R1 - R2 - CT| = 8 for the same magnitudes, so this pins the sign choice.
        self.assertAlmostEqual(self.rules.concentricity_margin(-100.0, -95.0, 3.0), 2.0, places=9)

    def test_concentricity_rule_does_not_apply_to_opposite_signs(self):
        self.assertEqual(self.rules.concentricity_margin(100.0, -100.0, 5.0), float("inf"))

    # --- Grating equation and spectrograph layout ----------------------------------
    # NOTE: lines_per_um, not lines/mm. A 600 lines/mm grating is 0.6 here.
    def test_grating_equation_600_lines_per_mm(self):
        # d(sin i + sin theta) = m*lambda at normal incidence: d = 1/0.6 = 1.6667 um,
        # lambda = 0.4 um -> sin(theta) = 0.24 -> theta = 13.886 deg.
        angle = self.rules.grating_diffraction_angle_deg(0.6, 0.4, 0.0, 1)
        self.assertAlmostEqual(angle, 13.886, places=2)

    def test_zeroth_order_is_undiffracted(self):
        self.assertEqual(self.rules.grating_diffraction_angle_deg(0.6, 0.4, 30.0, 0), -30.0)

    def test_order_that_cannot_propagate_returns_nan(self):
        # 0.6 lines/um with 1 um light: sin(theta) = 0.6 -> fine; 2 um light -> 1.2 > 1.
        self.assertTrue(math.isfinite(self.rules.grating_diffraction_angle_deg(0.6, 1.0, 0.0, 1)))
        self.assertTrue(math.isnan(self.rules.grating_diffraction_angle_deg(0.6, 2.0, 0.0, 1)))

    def test_dispersion_and_bandpass(self):
        # 600 lines/mm (d = 1.6667 um), f = 100 mm, near-normal diffraction:
        # d(lambda)/dx = d*cos(theta)/(m*f) = 16.67 nm/mm; a 0.1 mm slit -> 1.667 nm.
        disp = self.rules.reciprocal_linear_dispersion_nm_per_mm(0.6, 100.0, 0.0, 1)
        self.assertAlmostEqual(disp, 16.67, places=1)
        self.assertAlmostEqual(self.rules.spectral_bandpass_nm(0.1, disp), 1.667, places=2)

    def test_detector_focal_length_round_trips_the_span(self):
        # 1024 px at 14 um = 14.336 mm of detector; 600 lines/mm, 300 nm span -> f from the
        # geometry, then the dispersion must reproduce the span back over that width.
        f = self.rules.detector_focal_length_mm(1024, 14.0, 0.6, 20.0, 300.0, 1)
        self.assertAlmostEqual(f, 74.83, places=1)
        width_mm = 1024 * 14.0 / 1000.0
        disp = self.rules.reciprocal_linear_dispersion_nm_per_mm(0.6, f, 20.0, 1)
        self.assertAlmostEqual(disp * width_mm, 300.0, places=6)

    def test_spherical_mirror_wavefront_exceeds_rayleigh(self):
        # y = 10 mm on R = 100 mm: W = y^4/(8R^3) = 1.25 um = 2.27 waves at 0.55 um,
        # far past lambda/4 -- a plain sphere cannot carry this aperture.
        w = self.rules.mirror_spherical_wavefront_waves(10.0, 100.0, 0.55)
        self.assertAlmostEqual(w, 2.273, places=2)
        self.assertGreater(w, 0.25)

    def test_shafer_coma_free_angle_satisfies_its_own_condition(self):
        i4 = self.rules.shafer_coma_free_angle_deg(14.5, 100.0, 68.0, 45.0, 5.906)
        self.assertTrue(math.isfinite(i4))
        self.assertGreater(i4, 0.0)
        self.assertLess(i4, 89.9)
        # Residual of  sin I4 / sin I2 = (r4/r2)^2 (cos^3 I4 / cos^3 I2) (cos^3 i / cos^3 th)
        lhs = math.sin(math.radians(i4)) / math.sin(math.radians(14.5))
        rhs = (
            (68.0 / 100.0) ** 2
            * math.cos(math.radians(i4)) ** 3 / math.cos(math.radians(14.5)) ** 3
            * math.cos(math.radians(45.0)) ** 3 / math.cos(math.radians(5.906)) ** 3
        )
        self.assertAlmostEqual(lhs, rhs, places=9)

    def test_sag_matches_edge_thickness_convention(self):
        rules = OpticalRuleCheck()
        # Biconvex, R = +/-100, semi-diameter 10: sag = R - sign(R)*sqrt(R^2 - y^2)
        # = 100 - sqrt(9900) = 0.5013 mm on the convex face, -0.5013 on the concave face.
        expected = 100.0 - math.sqrt(100.0 ** 2 - 10.0 ** 2)
        self.assertAlmostEqual(rules.surface_sag_mm(100.0, 10.0), expected, places=9)
        self.assertAlmostEqual(rules.surface_sag_mm(-100.0, 10.0), -expected, places=9)
        # Both faces are positive sag, so the edge is thinner than the 5 mm center.
        self.assertAlmostEqual(rules.compute_edge_thickness(100.0, -100.0, 5.0, 10.0),
                               5.0 - 2.0 * expected, places=9)

    def test_sag_is_zero_when_the_ray_exceeds_the_hemisphere(self):
        # |y| >= |R| has no finite sagitta; the shape is reported by the steepness checks.
        self.assertEqual(self.rules.surface_sag_mm(5.0, 6.0), 0.0)
        self.assertEqual(self.rules.surface_sag_mm(5.0, 5.0), 0.0)

    # --- validate_system: fabricated summary, no OpticStudio needed -----------------
    def _summary(self, **overrides):
        summary = {
            "general": {"aperture_type": "EntrancePupilDiameter", "aperture_value": 20.0,
                        "field_type": "Angle", "ray_aiming": "Off"},
            "surfaces": [
                {"index": 0, "type": "Standard", "comment": "Object", "is_stop": False,
                 "radius": float("inf"), "thickness": 50.0, "material": "", "semi_diameter": 10.0},
                {"index": 1, "type": "Standard", "comment": "front", "is_stop": True,
                 "radius": 100.0, "thickness": 5.0, "material": "N-BK7", "semi_diameter": 10.0},
                {"index": 2, "type": "Standard", "comment": "rear", "is_stop": False,
                 "radius": -100.0, "thickness": 40.0, "material": "", "semi_diameter": 10.0},
                {"index": 3, "type": "Standard", "comment": "Image", "is_stop": False,
                 "radius": float("inf"), "thickness": 0.0, "material": "", "semi_diameter": 10.0},
            ],
            "fields": [{"index": 1, "x": 0.0, "y": 10.0}],
            "wavelengths": [{"index": 1, "wavelength_um": 0.55, "weight": 1.0, "is_primary": True}],
        }
        summary.update(overrides)
        return summary

    def test_validate_flags_karow_on_a_symmetric_biconvex(self):
        rules = OpticalRuleCheck()
        report = rules.validate_system(self._summary())
        rules_hit = {f["rule"] for f in report["findings"]}
        self.assertIn("Karow Centering Factor", rules_hit)
        # Opposite-sign radii: the concentricity rule does not apply.
        self.assertNotIn("Concentric Radii (Centering Feasibility)", rules_hit)

    def test_validate_flags_ray_aiming_for_break_before_stop(self):
        rules = OpticalRuleCheck()
        summary = self._summary()
        summary["surfaces"][1]["type"] = "CoordinateBreak"
        summary["surfaces"][1]["is_stop"] = False
        summary["surfaces"][2]["is_stop"] = True
        report = rules.validate_system(summary)
        rules_hit = {f["rule"] for f in report["findings"]}
        self.assertIn("Ray Aiming for Tilted/Decentered Stop", rules_hit)

    def test_validate_flags_near_flat_and_extreme_diameter_ratio(self):
        rules = OpticalRuleCheck()
        summary = self._summary()
        # R = 20 m over a 10 mm semi-diameter: sag ~2.5 um, i.e. a near-flat.
        summary["surfaces"][1]["radius"] = 20000.0
        summary["surfaces"][2]["radius"] = -20000.0
        summary["surfaces"][1]["thickness"] = 1.0
        report = rules.validate_system(summary)
        rules_hit = {f["rule"] for f in report["findings"]}
        self.assertIn("Near-Flat Surface (Test Plate Ambiguity)", rules_hit)
        self.assertIn("Extreme Diameter:Thickness Ratio", rules_hit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
