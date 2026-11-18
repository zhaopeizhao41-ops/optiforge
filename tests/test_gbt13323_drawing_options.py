"""
Tests for the fabrication-drawing options of the GB/T 13323 exporter: lens outer diameter from the
model, flat lands, sub-ranges with ideal (paraxial) surfaces, split cemented groups, overrides,
standard scales and the notes layout.  Offline: the OpticStudio session is mocked.
The lens data are made-up round numbers, not a real design.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import ezdxf
import matplotlib

matplotlib.use("Agg")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.test_gbt13323_drawing_template import _make_mock_session
from tools.cad_export_tools import (
    _apply_drawing_overrides,
    _build_lens_slice_polygon,
    _calculate_sag,
    _format_scale_ratio,
    _layout_technical_notes,
    _pick_standard_scale,
    _wrap_cad_note_line,
    zemax_export_optical_drawing,
)

# cemented pair of the mock relay: rear face is a dimple, clear semi-diameter 5.5, lens edge 10
PAIR_R1, PAIR_R2, PAIR_R3 = 18.0, -14.0, 13.0
PAIR_T1, PAIR_T2 = 8.0, 7.5
PAIR_HALF_OD, PAIR_POLISH_HALF = 10.0, 5.5


def _relay_session():
    """Stop, singlet, cemented pair whose last face is a dimple with a flat land, an intermediate
    image, and an ideal lens behind it (as in a scan relay with an ideal objective)."""
    surfaces = [
        (0.0, float("inf"), "", 0.0, 0.0, "", False),
        (0.0, 20.0, "", 2.0, 0.0, "", True),                        # 1 stop
        (25.0, 5.0, "H-K9L", 7.0, 0.0, "AR", False),                # 2 singlet
        (12.0, 5.0, "", 7.0, 0.0, "AR", False),                     # 3
        (PAIR_R1, PAIR_T1, "H-K9L", PAIR_HALF_OD, 0.0, "AR", False),   # 4 cemented pair
        (PAIR_R2, PAIR_T2, "H-ZF1", PAIR_HALF_OD, 0.0, "", False),     # 5
        (PAIR_R3, 6.0, "", PAIR_POLISH_HALF, 0.0, "AR", False),        # 6 dimple
        (0.0, 30.0, "", 3.0, 0.0, "", False),                       # 7 intermediate image
        (0.0, 12.5, "", 10.0, 0.0, "", False),                      # 8 ideal lens
        (0.0, 0.0, "", 0.4, 0.0, "", False),                        # 9 image
    ]
    sess = _make_mock_session(surfaces, efl=-2.5, expd=4.0)
    lde = sess.system.LDE
    lde.GetSurfaceAt(6).MechanicalSemiDiameter = PAIR_HALF_OD
    lde.GetSurfaceAt(8).Type = "Paraxial"
    return sess


def _texts(path):
    return [e.dxf.text for e in ezdxf.readfile(path).modelspace().query("TEXT")]


class TestDrawingOptions(unittest.TestCase):

    def test_standard_scale_series(self):
        self.assertEqual(_pick_standard_scale(5.0, 14.0), 5.0)
        self.assertEqual(_pick_standard_scale(10.0, 20.0), 4.0)
        self.assertEqual(_pick_standard_scale(25.0, 32.0), 2.5)
        self.assertEqual(_pick_standard_scale(300.0, 120.0), 0.25)
        self.assertEqual(_format_scale_ratio(2.5), "2.5:1")
        self.assertEqual(_format_scale_ratio(0.5), "1:2")

    def test_flat_land_polygon(self):
        poly, front, rear, z_f_rim, z_r_rim = _build_lens_slice_polygon(
            z_front=0.0, z_rear=PAIR_T2, r1_draw=PAIR_R2, r2_draw=PAIR_R3, k1=0.0, k2=0.0, half_od=PAIR_HALF_OD,
            scale=1.0, axis_y=0.0, chamfer_front=0.0, chamfer_rear=0.3, polish_half_rear=PAIR_POLISH_HALF)
        land_z = PAIR_T2 + _calculate_sag(PAIR_R3, 0.0, PAIR_POLISH_HALF)
        self.assertAlmostEqual(z_r_rim, land_z, places=9)                 # rim sits on the land, not on the sphere
        self.assertLess(z_r_rim, PAIR_T2 + _calculate_sag(PAIR_R3, 0.0, PAIR_HALF_OD) - 2.0)
        on_land = [p for p in rear if abs(p[1]) >= PAIR_POLISH_HALF - 1e-9]
        self.assertTrue(all(abs(p[0] - land_z) < 1e-9 for p in on_land))  # everything outside the polish is flat
        self.assertTrue(any(abs(abs(p[1]) - PAIR_POLISH_HALF) < 1e-9 for p in rear))  # exact corner points exist
        self.assertAlmostEqual(min(p[0] for p in rear), PAIR_T2, places=9)  # vertex unchanged

    def test_notes_stay_left_of_the_title_block(self):
        short = ["1、材料采用指定牌号优质光学玻璃；", "2、未注倒角 0.3×45°；"]
        h, wrapped = _layout_technical_notes(short)
        self.assertEqual(h, 2.75)
        self.assertEqual([len(w) for w in wrapped], [1, 1])
        long_notes = [f"{k}、通光口径：前表面 Φ12.0，后表面 Φ8.0；表面疵病：前表面 40-20，后表面 20-10（距中间像面约 6mm）；" for k in range(1, 9)]
        h, wrapped = _layout_technical_notes(long_notes)
        self.assertLessEqual(h, 2.75)
        limit = (109.0 - 28.5) / (1.47 * h)
        for lines in wrapped:
            for line in lines:
                units = sum(1.0 if ord(ch) > 127 else 0.54 for ch in line)
                self.assertLessEqual(units, limit + 1.0)
        total = sum(len(w) * 1.6 * h + (1.2 / 2.75) * h for w in wrapped)
        self.assertGreaterEqual(95.0 - total, 8.0)
        self.assertIn("Φ16.5", " ".join(_wrap_cad_note_line("通光口径：前表面 Φ14.5，后表面 Φ16.5；通光口径内表面疵病", max_units=14.0)))

    def test_overrides_merge(self):
        dwg = {"N": "3", "elements": [{"index": 1, "t": 5.0}, {"index": 2, "t": 3.0}], "technical_notes": ["a"]}
        ov = {"common": {"N": "2"}, "elements": {"3": {"elements": [None, {"chamfer_rear": 0.2}], "technical_notes": ["x", "y"]}},
              "assembly": {"bom_show_barrel": False}}
        _apply_drawing_overrides(dwg, ov, "3")
        self.assertEqual(dwg["N"], "2")
        self.assertEqual(dwg["elements"][0], {"index": 1, "t": 5.0})
        self.assertEqual(dwg["elements"][1]["chamfer_rear"], 0.2)
        self.assertEqual(dwg["technical_notes"], ["x", "y"])
        asm = _apply_drawing_overrides({}, ov, "assembly")
        self.assertEqual(asm, {"N": "2", "bom_show_barrel": False})

    def test_group_export_with_model_od_split_and_overrides(self):
        overrides = {
            "common": {"od_prefix": "Φ", "od_tolerance": "0/-0.02", "N": "2", "delta_N": "0.5"},
            "elements": {"2b": {"title_name": "LENS-B", "d0_str": "Φ12.0/Φ8.0"}},
            "assembly": {"bom_show_barrel": False, "technical_notes": ["1、只表示光学件的相对位置；"]},
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "tools.cad_export_tools.ZOSSession.get_instance", return_value=_relay_session()
        ):
            res = zemax_export_optical_drawing(
                output_dir=tmp, project_name="opt_test", od_source="model", first_surface=2, last_surface=7,
                split_cemented=True, overrides=overrides, generate_2d_plot=False)
            self.assertEqual(res["status"], "success", res.get("message"))
            tags = [str(d["element_index"]) for d in res["element_drawings"]]
            self.assertEqual(tags, ["1", "2", "2a", "2b"])
            by_tag = {str(d["element_index"]): d for d in res["element_drawings"]}
            self.assertEqual(by_tag["1"]["od"], 14.0)                    # 2 x mechanical semi-diameter, not CA + 2 -> 16
            self.assertEqual(by_tag["2"]["od"], 20.0)
            self.assertEqual(by_tag["1"]["drawing_scale"], "5:1")
            self.assertEqual(by_tag["2"]["elements"][1]["polish_dia_rear"], 2 * PAIR_POLISH_HALF)
            self.assertNotIn("polish_dia_front", by_tag["2"]["elements"][0])

            edge_to_land = (PAIR_T2 - _calculate_sag(PAIR_R2, 0.0, PAIR_HALF_OD)
                            + _calculate_sag(PAIR_R3, 0.0, PAIR_POLISH_HALF))
            t_group = _texts(by_tag["2"]["dxf_drawing_file"])
            self.assertIn("Φ11", t_group)                                # polished diameter of the dimple
            self.assertIn("Φ20", t_group)
            self.assertIn("-0.02", t_group)                              # stacked limit deviations 0 / -0.02
            self.assertTrue(any(t.startswith("2--0.3") for t in t_group))
            self.assertIn("4:1", t_group)                                # scale in the title block
            self.assertIn(f"({edge_to_land:.3f})", t_group)              # edge thickness measured to the land

            part_b = by_tag["2b"]
            self.assertFalse(part_b["elements"][0]["mark_front"])        # cemented face: no coating mark
            self.assertTrue(part_b["elements"][0]["mark_rear"])
            self.assertEqual(part_b["elements"][0]["chamfer_front"], 0.0)
            t_b = _texts(part_b["dxf_drawing_file"])
            self.assertIn("LENS-B", t_b)
            self.assertIn("Φ12.0/Φ8.0", t_b)
            self.assertTrue(any(t == "0.3" for t in t_b))                # one chamfered rim only
            self.assertFalse(any(t.startswith("2--") for t in t_b))
            self.assertEqual(sum(1 for e in ezdxf.readfile(part_b["dxf_drawing_file"]).modelspace().query("CIRCLE")
                                 if abs(e.dxf.radius - 1.22) < 1e-6), 1)  # exactly one coating mark

            asm = res["assembly_drawing"]
            stack = 5.0 + 5.0 + PAIR_T1 + PAIR_T2
            self.assertAlmostEqual(asm["lens_stack_length_mm"], stack, places=6)
            self.assertAlmostEqual(asm["rear_image_gap_mm"], 6.0, places=6)          # to the intermediate image
            self.assertAlmostEqual(asm["total_track_length"], stack + 6.0, places=6)
            t_asm = _texts(asm["assembly_dxf_file"])
            self.assertNotIn("镜筒及隔圈", t_asm)
            self.assertIn("1、只表示光学件的相对位置；", t_asm)

    def test_defaults_are_unchanged_and_bad_options_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "tools.cad_export_tools.ZOSSession.get_instance", return_value=_relay_session()
        ):
            res = zemax_export_optical_drawing(output_dir=tmp, project_name="opt_test", generate_2d_plot=False)
            self.assertEqual(res["status"], "success", res.get("message"))   # an ideal lens no longer blocks the export
            self.assertEqual([d["od"] for d in res["element_drawings"]], [16.0, 25.0])   # legacy stock diameters
            self.assertNotIn("polish_dia_rear", res["element_drawings"][1]["elements"][1])
            self.assertTrue(any("镜筒及隔圈" in t for t in _texts(res["assembly_drawing"]["assembly_dxf_file"])))
            bad = zemax_export_optical_drawing(output_dir=tmp, project_name="opt_test", od_source="guess")
            self.assertEqual(bad["status"], "error")
            missing = zemax_export_optical_drawing(output_dir=tmp, project_name="opt_test",
                                                   overrides=os.path.join(tmp, "no_such_file.json"))
            self.assertEqual(missing["status"], "error")


if __name__ == "__main__":
    unittest.main()
