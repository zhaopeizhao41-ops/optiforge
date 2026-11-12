"""
Unit tests and reference template generator for the primary Chinese GB/T 13323-2009 /
GB/T 10609.1 optical engineering drawing exporter ('单位' header preserved, value empty).
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ezdxf
import matplotlib

matplotlib.use("Agg")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.cad_export_tools import (
    _export_gbt13323_element_dxf,
    _render_dxf_to_png,
    _render_gbt13323_element_drawing,
    zemax_export_optical_drawing,
)


def build_eyepiece_35mm_reference_data() -> dict:
    """Construct exact specification dictionary reproducing '目镜-35mm' with blank '单位'."""
    return {
        "title_name": "目镜-35mm",
        "title_code": "(图样代号)",
        "table_header_code": "(图样代号)(存储代号)",
        "title_materials": ["H-ZLAF68C", "H-ZF73"],
        "material": "H-ZLAF68C\nH-ZF73",
        "sheet_str": "共    张    第    张",
        "title_scale_str": "",
        "weight_str": "",
        "delta_nd": "2C",
        "delta_nf_nc": "2C",
        "homogeneity": "",
        "stress_biref": "",
        "absorption": "",
        "striae": "",
        "bubbles": "",
        "N": "3",
        "delta_N": "0.3",
        "delta_R": "A",
        "B": "IV",
        "wedge_chi": "3'",
        "efl_str": "35",
        "bfl_str": "20.56",
        "d0_str": "38mm",
        "od": 40.0,
        "ca": 38.0,
        "od_tolerance": "-0.05",
        "axis_y": 134.0,
        "lens_center_x": 184.0,
        "scale_override": 2.15,
        "design_wavelengths": "532nm",
        "custom_notes": [
            "1、正透镜1和4采用成都光明\nH-ZLAF68C玻璃；",
            "2、负透镜2和3采用成都光明\nH-ZF73玻璃；",
            "3、胶合采用环氧树脂或者光敏胶；",
            "4、镀膜，透过率≥99%@450nm~650nm;",
            "5、焦距及后截距对应波长：532nm",
        ],
        "elements": [
            {
                "index": 1,
                "r1": 89.540,
                "r2": -47.860,
                "t": 7.50,
                "t_decimals": 2,
                "t_tolerance": "±0.02",
                "material": "H-ZLAF68C",
                "chamfer_front": 0.35,
                "chamfer_rear": 0.0,
                "num_y_frac": 0.80,
                "mark_front_frac": 0.86,
                "r1_label": "R89.540",
                "r2_label": "R47.860",
                "display_et": 0.859,
                "hatch_angle": 45.0,
                "et_dim_cfg": {
                    "tier": 1,
                    "shoulder_side": "left",
                    "shoulder_len": 25.0,
                    "internal_arrows": False,
                    "draw_span_line": True,
                },
                "ct_dim_cfg": {
                    "tier": 0,
                    "shoulder_side": "left",
                    "shoulder_len": 14.0,
                    "internal_arrows": True,
                },
                "chamfer_callout": {
                    "tip_on": "front",
                    "angle": 112.0,
                    "stem": 19.8,
                    "shoulder": 18.5,
                    "prefix": "8--0.3",
                    "tol_upper": "+0.2",
                    "tol_lower": "0",
                    "suffix": "x45°",
                    "show_arrow": False,
                },
                "r1_dim_cfg": {
                    "pts_frac": 0.24,
                    "show_center_cross": True,
                    "jog_spec": {
                        "out_arm": 28.0,
                        "arm1": 8.5,
                        "zig_back": -3.8,
                        "zig_perp": 3.2,
                        "arm2": 5.5,
                        "text_t": 0.48,
                    },
                },
                "r2_dim_cfg": {
                    "pts_frac": 0.622,
                    "show_center_cross": True,
                    "jog_spec": {
                        "arm1": 42.0,
                        "zig_back": -10.5,
                        "zig_perp": 4.2,
                        "arm2": 16.0,
                        "text_t": 0.74,
                    },
                },
            },
            {
                "index": 2,
                "r1": -47.860,
                "r2": -127.060,
                "t": 3.0,
                "t_decimals": 0,
                "t_tolerance": "±0.02",
                "material": "H-ZF73",
                "air_after": 1.0,
                "air_tolerance": "±0.02",
                "chamfer_front": 0.0,
                "chamfer_rear": 0.0,
                "num_y_frac": 0.80,
                "mark_rear_frac": 0.14,
                "r2_label": "R127.060",
                "display_et": 5.795,
                "hatch_angle": 45.0,
                "et_dim_cfg": {
                    "tier": 2,
                    "shoulder_side": "left",
                    "shoulder_len": 25.0,
                    "internal_arrows": False,
                    "draw_span_line": False,
                },
                "ct_dim_cfg": {
                    "tier": 1,
                    "shoulder_side": "left",
                    "shoulder_len": 14.0,
                    "anchor_first": True,
                    "internal_arrows": False,
                },
                "air_dim_cfg": {
                    "tier": 2,
                    "shoulder_side": "right",
                    "shoulder_len": 13.0,
                    "internal_arrows": False,
                },
                "r2_dim_cfg": {
                    "pts_frac": 0.822,
                    "show_center_cross": True,
                    "jog_spec": {
                        "arm1": 42.0,
                        "zig_back": -10.5,
                        "zig_perp": 4.2,
                        "arm2": 16.0,
                        "text_t": 0.74,
                    },
                },
            },
            {
                "index": 3,
                "r1": 32.960,
                "r2": 22.08,
                "t": 6.0,
                "t_decimals": 0,
                "t_tolerance": "±0.02",
                "material": "H-ZF73",
                "chamfer_front": 0.0,
                "chamfer_rear": 0.0,
                "num_y_frac": 0.80,
                "mark_front_frac": 0.88,
                "r1_label": "R32.960",
                "r2_label": "R22.08",
                "display_et": 11.963,
                "hatch_angle": 45.0,
                "et_dim_cfg": {
                    "tier": 2,
                    "shoulder_side": "right",
                    "shoulder_len": 46.0,
                    "internal_arrows": True,
                    "draw_span_line": True,
                },
                "ct_dim_cfg": {
                    "tier": 0,
                    "shoulder_side": "right",
                    "shoulder_len": 13.0,
                    "internal_arrows": False,
                },
                "r1_dim_cfg": {
                    "pts_frac": 0.76,
                    "show_center_cross": True,
                    "jog_spec": {
                        "arm1": 46.0,
                        "zig_back": -7.5,
                        "zig_perp": 4.0,
                        "arm2": 24.0,
                        "text_t": 0.14,
                        "text_height": 2.25,
                    },
                },
                "r2_dim_cfg": {
                    "pts_frac": 0.34,
                    "show_center_cross": True,
                    "jog_spec": {
                        "arm1": 17.0,
                        "zig_back": -5.0,
                        "zig_perp": -3.0,
                        "arm2": 17.5,
                        "text_t": 0.44,
                        "text_height": 2.35,
                    },
                },
            },
            {
                "index": 4,
                "r1": 22.08,
                "r2": 46.130,
                "t": 10.0,
                "t_decimals": 0,
                "t_tolerance": "±0.02",
                "material": "H-ZLAF68C",
                "chamfer_front": 0.0,
                "chamfer_rear": 0.0,
                "num_y_frac": 0.80,
                "mark_rear_frac": 0.19,
                "r2_label": "R46.130",
                "display_et": 1.837,
                "hatch_angle": 45.0,
                "et_dim_cfg": {
                    "tier": 1,
                    "shoulder_side": "right",
                    "shoulder_len": 46.0,
                    "internal_arrows": False,
                    "draw_span_line": True,
                },
                "ct_dim_cfg": {
                    "tier": 1,
                    "shoulder_side": "right",
                    "shoulder_len": 15.0,
                    "internal_arrows": True,
                },
                "chamfer_callout": {
                    "tip_on": "rear",
                    "angle": 66.0,
                    "stem": 19.8,
                    "shoulder": 18.0,
                    "prefix": "8--0.2",
                    "tol_upper": "+0.2",
                    "tol_lower": "0",
                    "suffix": "x45°",
                    "show_arrow": False,
                },
                "r2_dim_cfg": {
                    "pts_frac": 0.855,
                    "show_center_cross": True,
                    "jog_spec": {
                        "arm1": 41.0,
                        "zig_back": -8.0,
                        "zig_perp": -4.0,
                        "arm2": 16.5,
                        "text_t": 0.61,
                    },
                },
            },
        ],
    }


def _make_mock_surface(
    surf_num: int,
    radius: float,
    thickness: float,
    material: str,
    semi_diameter: float,
    conic: float = 0.0,
    coating: str = "AR532",
    is_stop: bool = False,
):
    td = SimpleNamespace(
        BeforeSurfaceDecenterX=0.0,
        BeforeSurfaceDecenterY=0.0,
        BeforeSurfaceTiltX=0.0,
        BeforeSurfaceTiltY=0.0,
        BeforeSurfaceTiltZ=0.0,
        AfterSurfaceDecenterX=0.0,
        AfterSurfaceDecenterY=0.0,
        AfterSurfaceTiltX=0.0,
        AfterSurfaceTiltY=0.0,
        AfterSurfaceTiltZ=0.0,
    )
    return SimpleNamespace(
        SurfaceNumber=surf_num,
        Radius=radius,
        Thickness=thickness,
        Material=material,
        SemiDiameter=semi_diameter,
        Conic=conic,
        Coating=coating,
        Comment="",
        IsStop=is_stop,
        Type="Standard",
        TypeName="Standard",
        TiltDecenterData=td,
    )


def _make_mock_session(surfaces_specs, efl=35.0, expd=10.0, axcl=0.01):
    surfs = [_make_mock_surface(i, *spec) for i, spec in enumerate(surfaces_specs)]

    class FakeLDE:
        NumberOfSurfaces = len(surfs)

        @staticmethod
        def GetSurfaceAt(idx):
            return surfs[idx]

    class FakeWave:
        def __init__(self, idx, wl, is_primary=False):
            self.Wavelength = wl
            self.IsPrimary = is_primary

    wlist = [FakeWave(1, 0.4861, False), FakeWave(2, 0.5876, True), FakeWave(3, 0.6563, False)]

    class FakeWavelengths:
        NumberOfWavelengths = len(wlist)

        @staticmethod
        def GetWavelength(idx):
            return wlist[idx - 1]

    class FakeField:
        X = 0.0
        Y = 10.0

    class FakeFields:
        NumberOfFields = 1

        @staticmethod
        def GetField(_idx):
            return FakeField()

        @staticmethod
        def GetFieldType():
            return "Angle"

    class FakeMFE:
        @staticmethod
        def GetOperandValue(op_type, *_args):
            name = str(op_type)
            if "TOTR" in name:
                return sum(float(s.Thickness) for s in surfs[1:-1])
            if "EFFL" in name:
                return efl
            if "EXPD" in name:
                return expd
            if "AXCL" in name:
                return axcl
            return 0.0

    class FakeSysData:
        Wavelengths = FakeWavelengths()
        Fields = FakeFields()
        Aperture = SimpleNamespace(ApertureType="EntrancePupilDiameter", ApertureValue=expd)

    fake_zosapi = SimpleNamespace(
        Editors=SimpleNamespace(
            MFE=SimpleNamespace(
                MeritOperandType=SimpleNamespace(EFFL="EFFL", EXPD="EXPD", AXCL="AXCL", TOTR="TOTR")
            )
        )
    )
    fake_system = SimpleNamespace(LDE=FakeLDE(), MFE=FakeMFE(), SystemData=FakeSysData())
    return SimpleNamespace(system=fake_system, ZOSAPI=fake_zosapi)


class TestGBT13323Template(unittest.TestCase):
    def test_exact_eyepiece_35mm_reproduction_with_empty_unit_field(self):
        ref_dir = os.path.join(REPO_ROOT, "output", "gbt13323_template_reference")
        os.makedirs(ref_dir, exist_ok=True)
        dxf_path = os.path.join(ref_dir, "eyepiece_35mm.dxf")
        png_path = os.path.join(ref_dir, "eyepiece_35mm.png")

        data = build_eyepiece_35mm_reference_data()
        _export_gbt13323_element_dxf(data, dxf_path)
        rendered = _render_dxf_to_png(dxf_path, png_path)

        self.assertTrue(os.path.exists(dxf_path))
        self.assertTrue(rendered)
        self.assertTrue(os.path.exists(png_path))

        doc = ezdxf.readfile(dxf_path)
        texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
        joined = "\n".join(texts)

        for expected in [
            "单位",
            "图样名称",
            "目镜-35mm",
            "(图样代号)(存储代号)",
            "对零件的要求",
            "H-ZLAF68C",
            "H-ZF73",
            "R127.060",
            "R47.860",
            "R89.540",
            "R32.960",
            "R22.08",
            "R46.130",
            "(0.859)",
            "(5.795)",
            "(11.963)",
            "(1.837)",
            "7.50±0.02",
            "3±0.02",
            "1±0.02",
            "6±0.02",
            "10±0.02",
            "35mm",
            "20.56mm",
            "38mm",
        ]:
            self.assertIn(expected, texts, f"Missing expected template text: {expected}")

        # "单位" header must exist while value cell inside X:242..292, Y:37..47 must have NO entities
        self.assertNotIn("科诺医疗", joined)
        unit_value_texts = [
            e.dxf.text
            for e in doc.modelspace().query("TEXT")
            if 242.0 <= e.dxf.insert[0] <= 292.0 and 37.0 <= e.dxf.insert[1] <= 47.0
        ]
        self.assertEqual(unit_value_texts, [], f"'单位' cell content must be blank, found: {unit_value_texts}")

        # No negative curvature labels
        self.assertFalse(any(t.startswith("R-") for t in texts), "Radius labels must be unsigned.")

    def test_zemax_export_element_and_assembly_tolerance_grades(self):
        surfaces = [
            (0.0, float("inf"), "", 0.0, 0.0, "", False),
            (60.0, 6.0, "H-K9L", 11.0, 0.0, "AR", True),
            (-45.0, 4.0, "H-ZF1", 11.0, 0.0, "", False),
            (-120.0, 3.0, "", 11.0, 0.0, "AR", False),
            (38.0, 5.0, "H-LAF2", 10.0, 0.0, "AR", False),
            (-80.0, 28.0, "", 10.0, 0.0, "AR", False),
            (0.0, 0.0, "", 5.0, 0.0, "", False),
        ]
        mock_sess = _make_mock_session(surfaces, efl=42.5, expd=16.0)

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "tools.cad_export_tools.ZOSSession.get_instance", return_value=mock_sess
        ):
            res_comm = zemax_export_optical_drawing(
                output_dir=os.path.join(tmpdir, "comm"),
                project_name="test_proj",
                iso_tolerance_grade="Commercial",
                export_assembly=True,
                export_dxf=True,
            )
            self.assertEqual(res_comm["status"], "success")
            self.assertGreaterEqual(res_comm["drawings_count"], 2)

            dxf_e1 = res_comm["element_drawings"][0]["dxf_drawing_file"]
            doc_comm = ezdxf.readfile(dxf_e1)
            comm_texts = [e.dxf.text for e in doc_comm.modelspace().query("TEXT")]
            self.assertTrue(any("60-40" in t for t in comm_texts))
            self.assertFalse(any("40-20" in t for t in comm_texts))
            self.assertIn("单位", comm_texts)
            unit_vals = [
                e.dxf.text
                for e in doc_comm.modelspace().query("TEXT")
                if 242.0 <= e.dxf.insert[0] <= 292.0 and 37.0 <= e.dxf.insert[1] <= 47.0
            ]
            self.assertEqual(unit_vals, [])

            res_hp = zemax_export_optical_drawing(
                output_dir=os.path.join(tmpdir, "hp"),
                project_name="test_proj",
                iso_tolerance_grade="High-Precision",
                export_assembly=True,
                export_dxf=True,
            )
            doc_hp = ezdxf.readfile(res_hp["element_drawings"][0]["dxf_drawing_file"])
            hp_texts = [e.dxf.text for e in doc_hp.modelspace().query("TEXT")]
            self.assertTrue(any("20-10" in t for t in hp_texts))
            self.assertFalse(any("40-20" in t for t in hp_texts))

            # Verify assembly DXF rendered & produced PNG, and save reference previews
            asm_dxf = res_hp["assembly_drawing"]["assembly_dxf_file"]
            asm_png = res_hp["assembly_drawing"]["assembly_image_file"]
            self.assertTrue(os.path.exists(asm_dxf))
            self.assertTrue(os.path.exists(asm_png))

            import shutil
            ref_dir = os.path.join(REPO_ROOT, "output", "gbt13323_template_reference")
            os.makedirs(ref_dir, exist_ok=True)
            shutil.copyfile(res_comm["element_drawings"][0]["drawing_image_file"], os.path.join(ref_dir, "sample_doublet_zemax.png"))
            shutil.copyfile(asm_png, os.path.join(ref_dir, "sample_assembly_zemax.png"))

    def test_matplotlib_fallback_preserves_axes_text_contracts(self):
        surfaces = [
            (0.0, float("inf"), "", 0.0, 0.0, "", False),
            (55.0, 5.5, "N-BK7", 11.5, 0.0, "AR", True),
            (-55.0, 42.0, "", 11.5, 0.0, "AR", False),
            (0.0, 0.0, "", 4.0, 0.0, "", False),
        ]
        mock_sess = _make_mock_session(surfaces, efl=50.0, expd=18.0)

        captured_texts = []
        orig_text = matplotlib.axes.Axes.text

        def spy_text(ax_self, x, y, s, *args, **kwargs):
            captured_texts.append(str(s))
            return orig_text(ax_self, x, y, s, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "tools.cad_export_tools.ZOSSession.get_instance", return_value=mock_sess
        ), patch.object(matplotlib.axes.Axes, "text", spy_text):
            res = zemax_export_optical_drawing(
                output_dir=tmpdir,
                project_name="mpl_fallback",
                iso_tolerance_grade="Precision",
                export_assembly=True,
                export_dxf=False,
            )
            self.assertEqual(res["status"], "success")
            joined = "\n".join(captured_texts)
            self.assertIn("486.1", joined)
            self.assertIn("\u221e", joined)
            self.assertIn("47.5000", joined)
            self.assertIn("单位", captured_texts)
            self.assertNotIn("科诺医疗", joined)


if __name__ == "__main__":
    unittest.main()
