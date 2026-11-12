"""Display-only semi-diameter derivation; never a prescription write.

A freshly written prescription reports SemiDiameter = 0 until apertures are set,
which used to leave the workbench section blank until the end of the run. The
fallback draws a labelled picture from derived values and must not touch the
model, the validation layout, or any native state.
"""
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.workbench_snapshot import (build_layout, derive_display_apertures,
                                     derived_display_layout)


def model(halfs=(0., 0., 0., 0.), aperture=("EPD", 10.)):
    rows = []
    for index, half in enumerate(halfs):
        radius = math.inf if index in (0, len(halfs) - 1) else (20. if index % 2 else -20.)
        thickness = math.inf if index == 0 else (30. if index == len(halfs) - 1 else 4.)
        material = "N-BK7" if index == 1 else ""
        rows.append({"index": index, "radius": radius, "thickness": thickness,
                     "material": material, "semi_diameter": half, "conic": 0.,
                     "type": "Standard", "is_stop": index == 1})
    return {"units": "mm", "system_mode": "Sequential", "surfaces": rows,
            "settings": {"aperture_type": aperture[0], "aperture_value": aperture[1]}}


class DerivedApertureTests(unittest.TestCase):
    def test_zero_apertures_still_reject_the_validation_layout(self):
        data = model()
        self.assertEqual(build_layout(data)["status"], "unsupported")

    def test_derived_layout_uses_epd_half_and_is_labelled(self):
        data = model()
        before = deepcopy(data)
        layout, derived = derived_display_layout(data, build_layout(data)["issues"])
        self.assertEqual(data, before)                       # nothing written back
        self.assertIsNotNone(layout)
        self.assertEqual(layout["status"], "ready")
        self.assertEqual(layout["source"], "derived-display-aperture")
        self.assertEqual({item["semi_diameter"] for item in derived}, {5.0})
        self.assertTrue(all("显示用半口径推导" in issue for issue in layout["issues"]))
        self.assertTrue(layout.get("validation_issues"))

    def test_existing_apertures_are_not_derived(self):
        data = model(halfs=(math.inf, 4., 4., 2.))
        self.assertEqual(build_layout(data)["status"], "ready")
        self.assertEqual(derive_display_apertures(data), (None, []))

    def test_radius_fallback_when_no_aperture_or_epd(self):
        data = model(aperture=("Float by stop size", 0.))
        layout, derived = derived_display_layout(data, [])
        self.assertIsNotNone(layout)
        # Interior radii are 20 mm → 25 % of the largest radius.
        self.assertEqual({item["semi_diameter"] for item in derived}, {5.0})

    def test_no_basis_returns_no_picture(self):
        data = model(aperture=("Float by stop size", 0.))
        for surface in data["surfaces"][1:-1]:
            surface["radius"] = math.inf
        layout, derived = derived_display_layout(data, [])
        self.assertIsNone(layout)
        self.assertEqual(derived, [])


if __name__ == "__main__":
    unittest.main()
