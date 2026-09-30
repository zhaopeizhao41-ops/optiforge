"""Native OpticStudio regressions; outputs stay in a temporary directory.

Run: python -B tests/regression_stage_one.py [--case path/to/design.zmx]
Requires a valid OpticStudio API license. No source design is saved/modified.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.zos_session import ZOSSession
from tools import analysis_tools as analysis
from tools import cad_export_tools as cad
from tools import optimization_tools as optimization
from tools import project_manager as projects
from tools.system_tools import zemax_load_template, zemax_register_design_proposal


class StageOneRegression(unittest.TestCase):
    case = None

    @classmethod
    def setUpClass(cls):
        cls.output = Path(tempfile.mkdtemp(prefix="zemax-stage-one-"))
        cls.old_project_paths = projects.OUTPUT_BASE_DIR, projects.ACTIVE_PROJECT_FILE
        projects.OUTPUT_BASE_DIR = str(cls.output / "projects")
        projects.ACTIVE_PROJECT_FILE = str(cls.output / "projects" / ".active_project.json")
        cls.session = ZOSSession.get_instance()
        print(f"Regression outputs: {cls.output}", flush=True)

    @classmethod
    def tearDownClass(cls):
        cls.session.close()
        projects.OUTPUT_BASE_DIR, projects.ACTIVE_PROJECT_FILE = cls.old_project_paths

    def setUp(self):
        # Stage-two modeling operations require an explicit proposal gate for the
        # active project. Register a small fixture proposal before loading a model.
        projects.set_active_project("stage_one_regression")
        proposal = projects.get_project_dir("stage_one_regression")
        if not (Path(proposal) / "design_proposal.json").exists():
            confirmed = zemax_register_design_proposal(
                project_name="stage_one_regression",
                target_specs={"fixture": "native regression"},
                initial_structure_source="fixture",
                optical_theory_analysis="fixture",
                glass_selection_rationale="fixture",
                merit_function_strategy="fixture",
                user_confirmed_to_simulate=True,
            )
            self.assertEqual(confirmed["status"], "success")
        if self.case:
            self.session.load_file(self.case)
            self.session.model_project = "stage_one_regression"
        else:
            zemax_load_template("achromat_doublet")
            self.session.model_project = "stage_one_regression"
        self.system = self.session.system
        self.zos = self.session.ZOSAPI

    def test_spot_matches_native_single_wavelength_runs(self):
        result = analysis.zemax_run_spot_diagram()
        self.assertEqual(result["status"], "success")
        for wave in range(int(self.system.SystemData.Wavelengths.NumberOfWavelengths) + 1):
            native = self.system.Analyses.New_StandardSpot()
            try:
                settings = analysis._settings(native)
                settings.Field.UseAllFields()
                if wave == 0:
                    settings.Wavelength.UseAllWavelengths()
                else:
                    settings.Wavelength.SetWavelengthNumber(wave)
                native.ApplyAndWaitForCompletion()
                data = native.GetResults().SpotData
                for field in result["fields"]:
                    f = field["field_index"]
                    actual = field if wave == 0 else field["wavelengths"][wave - 1]
                    prefix = "polychromatic_" if wave == 0 else ""
                    self.assertAlmostEqual(actual[prefix + "rms_spot_um"], float(data.GetRMSSpotSizeFor(f, 0 if wave == 0 else 1)), delta=0.000051)
                    self.assertAlmostEqual(actual[prefix + "geo_spot_um"], float(data.GetGeoSpotSizeFor(f, 0 if wave == 0 else 1)), delta=0.000051)
            finally:
                native.Close()
        selected = analysis.zemax_run_spot_diagram(field_index=len(result["fields"]))
        self.assertEqual(selected["fields"], [result["fields"][-1]])
        self.assertEqual(analysis.zemax_run_spot_diagram(field_index=0)["status"], "error")
        (self.output / "spot.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    def check_length_bounds(self):
        mfe = self.system.MFE
        col = self.zos.Editors.MFE.MeritColumn
        glass = [i for i in range(1, self.system.LDE.NumberOfSurfaces - 1)
                 if self.system.LDE.GetSurfaceAt(i).Material]
        stack = sum(float(self.system.LDE.GetSurfaceAt(i).Thickness) for i in range(glass[0], glass[-1] + 1))
        track = sum(float(self.system.LDE.GetSurfaceAt(i).Thickness) for i in range(1, self.system.LDE.NumberOfSurfaces - 1))
        for offset in (10.0, -1.0):
            result = optimization.zemax_setup_merit_function(
                max_barrel_length=stack + offset, max_totr=track + offset,
                target_efl=100, barrel_weight=20, totr_weight=1)
            self.assertEqual(result["status"], "success")
            mfe.CalculateMeritFunction()
            bound_rows = [i for i in range(1, mfe.NumberOfOperands + 1)
                          if str(mfe.GetOperandAt(i).Type) == "OPLT"]
            self.assertEqual(len(bound_rows), 2)
            types = set()
            for row in bound_rows:
                bound = mfe.GetOperandAt(row)
                measurement = mfe.GetOperandAt(bound.GetOperandCell(col.Param1).IntegerValue)
                types.add(str(measurement.Type))
                self.assertEqual(measurement.Weight, 0)
                self.assertAlmostEqual(bound.Value - bound.Target, max(0, -offset), places=7)
                expected = stack if str(measurement.Type) == "TTHI" else track
                self.assertAlmostEqual(measurement.Value, expected, places=7)
            self.assertEqual(types, {"TTHI", "TOTR"})

    def test_length_upper_bounds_and_references(self):
        self.check_length_bounds()

    def test_single_wavelength_spot(self):
        zemax_load_template("singlet_bk7")
        result = analysis.zemax_run_spot_diagram()
        self.assertEqual(result["status"], "success")
        field = result["fields"][0]
        self.assertEqual(len(field["wavelengths"]), 1)
        self.assertEqual(field["polychromatic_rms_spot_um"], field["wavelengths"][0]["rms_spot_um"])

    def test_single_glass_length_bound(self):
        zemax_load_template("singlet_bk7")
        self.check_length_bounds()

    def test_invalid_bound_does_not_change_merit_function(self):
        mfe = self.system.MFE
        def snapshot():
            # BLNK cells contain NaN; compare their stable text representation.
            return [(str(mfe.GetOperandAt(i).Type), str(mfe.GetOperandAt(i).Target), str(mfe.GetOperandAt(i).Weight))
                    for i in range(1, mfe.NumberOfOperands + 1)]
        before = snapshot()
        for limit in (0, -1, math.inf, math.nan):
            self.assertEqual(optimization.zemax_setup_merit_function(max_totr=limit)["status"], "error")
            self.assertEqual(snapshot(), before)

    def test_drawings_share_actual_metadata(self):
        result = cad.zemax_export_optical_drawing(output_dir=str(self.output / "drawings"))
        self.assertEqual(result["status"], "success")
        asm = result["assembly_drawing"]
        elements = cad._extract_lens_elements(self.system)
        first, rear = elements[0]["surface_start"], elements[-1]["surface_end"]
        expected_stack = sum(float(self.system.LDE.GetSurfaceAt(i).Thickness) for i in range(first, rear))
        expected_track = sum(float(self.system.LDE.GetSurfaceAt(i).Thickness) for i in range(1, self.system.LDE.NumberOfSurfaces - 1))
        self.assertAlmostEqual(asm["lens_stack_length_mm"], expected_stack, places=8)
        self.assertAlmostEqual(asm["total_track_length"], expected_track, places=8)
        self.assertTrue(asm["object_at_infinity"])
        self.assertIsNone(asm["object_working_distance_mm"])
        self.assertAlmostEqual(asm["rear_image_gap_mm"] + expected_stack, expected_track, places=8)
        all_text = []
        for path in (self.output / "drawings").iterdir():
            if path.suffix == ".md":
                all_text.append(path.read_text(encoding="utf-8"))
            elif path.suffix == ".dxf":
                doc = cad.ezdxf.readfile(path)
                all_text.append("\n".join(e.dxf.text for e in doc.modelspace().query("TEXT")))
            elif path.suffix == ".png":
                self.assertGreater(path.stat().st_size, 1000)
        for text in all_text:
            for forbidden in ("785", "810", "850", "780-860", "WATER IMMERSION", "SM1", "BBAR", ">90%", "99.0%", "2026/09/24"):
                self.assertNotIn(forbidden, text)
        for drawing in result["element_drawings"]:
            self.assertNotIn("dxf_error", drawing)
            self.assertIn("Not specified", drawing["coating"])
            for path_key in ("gbt_markdown_file", "iso_markdown_file"):
                self.assertIn(drawing["design_wavelengths"], Path(drawing[path_key]).read_text(encoding="utf-8"))
            self.assertIn(f"**图纸比例**: `{drawing['drawing_scale']}`", Path(drawing["gbt_markdown_file"]).read_text(encoding="utf-8"))
        assembly_text = Path(asm["assembly_markdown_file"]).read_text(encoding="utf-8")
        self.assertIn(f"{expected_track:.4f}", assembly_text)
        self.assertIn("Infinity", assembly_text)
        (self.output / "drawings.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_fallback_and_tolerance_grades(self):
        # Capture the text actually rendered by Matplotlib, including fallback notes.
        from matplotlib.axes import Axes
        original = Axes.text
        texts = []
        def capture(axis, x, y, text, *args, **kwargs):
            texts.append(str(text))
            return original(axis, x, y, text, *args, **kwargs)
        with patch.object(Axes, "text", capture):
            result = cad.zemax_export_optical_drawing(output_dir=str(self.output / "fallback"), export_dxf=False)
        joined = "\n".join(texts)
        self.assertNotIn("785", joined)
        self.assertIn(result["element_drawings"][0]["design_wavelengths"], joined)
        self.assertIn("∞", joined)
        self.assertIn(f"{result['assembly_drawing']['total_track_length']:.4f}", joined)
        for grade, scratch in (("Commercial", "60-40"), ("High-Precision", "20-10")):
            result = cad.zemax_export_optical_drawing(output_dir=str(self.output / grade), iso_tolerance_grade=grade, generate_2d_plot=False)
            doc = cad.ezdxf.readfile(result["element_drawings"][0]["dxf_drawing_file"])
            text = "\n".join(e.dxf.text for e in doc.modelspace().query("TEXT"))
            self.assertIn(scratch, text)
            self.assertNotIn("40-20", text)

    def test_finite_object_distance_and_coating(self):
        self.system.LDE.GetSurfaceAt(0).Thickness = 250
        element = cad._extract_lens_elements(self.system)[0]
        self.system.LDE.GetSurfaceAt(element["surface_start"]).Coating = "I.99"
        metadata = cad._system_drawing_metadata(self.system, self.zos, cad._extract_lens_elements(self.system))
        self.assertEqual(metadata["object_working_distance_mm"], 250)
        self.assertFalse(metadata["object_at_infinity"])
        self.assertIn("I.99", cad._element_coating(self.system, element))
        self.system.LDE.GetSurfaceAt(1).TiltDecenterData.BeforeSurfaceTiltX = 5
        self.assertIsNone(cad._system_drawing_metadata(self.system, self.zos, cad._extract_lens_elements(self.system))["lens_stack_length_mm"])

    def test_ring_clearance_and_fit(self):
        for template in (None, "cooke_triplet"):
            if template:
                zemax_load_template(template)
                # The unmodified triplet has unequal lens ODs; a simple straight
                # spacer cannot clear its larger aperture while fitting the small bore.
                rejected = cad.zemax_export_prescription_for_cad(output_filepath=str(self.output / "triplet_too_small.json"))
                self.assertEqual(rejected["status"], "error")
                self.assertIn("Spacer", rejected["message"])
                self.assertFalse((self.output / "triplet_too_small.json").exists())
            result = cad.zemax_export_prescription_for_cad(margin_mm=10 if template else 2,
                output_filepath=str(self.output / f"{template or 'case'}_prescription.json"))
            self.assertEqual(result["status"], "success", result)
            for ring in [result["retaining_ring"], *result["spacing_rings"]]:
                self.assertGreaterEqual(ring["inner_diameter_mm"], ring["aperture_envelope_mm"] + 0.2 - 1e-9)
                self.assertGreaterEqual((ring["outer_diameter_mm"] - ring["inner_diameter_mm"]) / 2, 0.5)
                self.assertEqual(ring["solidworks_mcp_command"]["arguments"]["inner_diameter"], ring["inner_diameter_mm"])

    def test_impossible_ring_is_rejected(self):
        with self.assertRaises(ValueError):
            cad._ring_dimensions(20, 15)
        result = cad.zemax_export_prescription_for_cad(margin_mm=0.01, output_filepath=str(self.output / "invalid.json"))
        self.assertEqual(result["status"], "error")
        self.assertFalse((self.output / "invalid.json").exists())
        for margin in (0, -1, math.inf, math.nan):
            self.assertEqual(cad.zemax_export_prescription_for_cad(margin_mm=margin)["status"], "error")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case")
    args, remaining = parser.parse_known_args()
    StageOneRegression.case = args.case
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
