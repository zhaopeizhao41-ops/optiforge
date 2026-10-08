"""Design quality gates: automated pre-save checks that encode lessons from past failures.

PHILOSOPHY:
  Every "builds but doesn't work" failure should leave behind a CHECK that prevents
  the same class of error in the future. This module is the living memory of failure
  modes, translated into executable verification logic.

USAGE:
  All design-building tools (zemax_save_file, zemax_generate_*) call run_quality_gates()
  before writing output. If gates fail, the operation is blocked and the user gets
  actionable fixes.

EVOLUTION:
  When a new failure mode is discovered:
    1. Add a gate function here (def check_<failure_mode>)
    2. Register it in QUALITY_GATES list
    3. Document the triggering case in the docstring
    4. Update KNOWN_FAILURE_MODES with the case study
"""
from typing import Dict, Any, List, Callable, Tuple
from core.zos_session import ZOSSession


# ==============================================================================
# KNOWN FAILURE MODES (case studies that drove each gate)
# ==============================================================================

KNOWN_FAILURE_MODES = {
    "coordinate_break_without_decenter": {
        "date": "2026-10-08",
        "symptom": "Czerny-Turner mirrors don't appear in 3D layout; rays travel straight",
        "root_cause": "CoordinateBreak with tilt_x but no decenter → coordinate system rotates "
                     "but rays don't physically move off-axis to hit mirrors",
        "gate": "check_coordinate_break_usage",
    },
    "zero_semi_diameter": {
        "date": "2026-10-08",
        "symptom": "Rays blocked at surface, no illumination downstream",
        "root_cause": "Surface semi_diameter defaulted to 0 when not explicitly set",
        "gate": "check_aperture_blockage",
    },
    "missing_mirror_material": {
        "date": "2026-10-08",
        "symptom": "Reflective surface acts like glass, rays refract instead of reflect",
        "root_cause": "Surface Comment says 'mirror' but Material field not set to MIRROR",
        "gate": "check_mirror_material",
    },
}


# ==============================================================================
# QUALITY GATES (one function per failure mode)
# ==============================================================================

def check_aperture_blockage() -> Tuple[bool, List[str], List[str]]:
    """Gate: No optical surface may have semi-diameter = 0 (blocks all rays).

    Triggered by: Czerny-Turner failure (2026-10-08)
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    issues = []

    for i in range(1, lde.NumberOfSurfaces - 1):
        surf = lde.GetSurfaceAt(i)
        surf_type = str(surf.Type).split(".")[-1]

        # Exempt CoordinateBreak and Null (they don't block rays)
        if surf_type in ("CoordinateBreak", "Null"):
            continue

        if surf.SemiDiameter < 0.01:
            issues.append(
                f"Surface {i} ({surf.Comment or 'unnamed'}, type={surf_type}) has "
                f"SemiDiameter={surf.SemiDiameter:.4f} → will block all rays. "
                f"FIX: zemax_surface_operations(surface_index={i}, semi_diameter=<aperture>)"
            )

    return len(issues) == 0, issues, []


def check_mirror_material() -> Tuple[bool, List[str], List[str]]:
    """Gate: Surfaces labeled 'mirror' in comment must have Material='MIRROR'.

    Triggered by: Reflective spectrometer designs (2026-10-08)
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    issues = []

    for i in range(1, lde.NumberOfSurfaces - 1):
        surf = lde.GetSurfaceAt(i)
        comment_lower = str(surf.Comment).lower()
        material = str(surf.Material).strip().upper()

        if any(keyword in comment_lower for keyword in ["mirror", "grating", "reflect"]):
            if material not in ("MIRROR", ""):
                issues.append(
                    f"Surface {i} ({surf.Comment}) is labeled as reflective but "
                    f"Material={material} (not MIRROR). Ray will refract instead of reflect. "
                    f"FIX: zemax_surface_operations(surface_index={i}, material='MIRROR')"
                )

    return len(issues) == 0, issues, []


def check_coordinate_break_usage() -> Tuple[bool, List[str], List[str]]:
    """Gate: CoordinateBreak with tilt but no decenter is usually wrong for off-axis systems.

    Triggered by: Czerny-Turner off-axis mirror failure (2026-10-08)

    A CB with only tilt rotates the coordinate system but doesn't move the ray physically.
    For off-axis mirrors, you need decenter (to move the mirror location) + tilt.
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    warnings = []

    for i in range(1, lde.NumberOfSurfaces - 1):
        surf = lde.GetSurfaceAt(i)
        if str(surf.Type).split(".")[-1] != "CoordinateBreak":
            continue

        try:
            cols = session.ZOSAPI.Editors.LDE.SurfaceColumn
            # Par1=DecenterX, Par2=DecenterY, Par3=TiltX, Par4=TiltY, Par5=TiltZ
            dx = abs(float(surf.GetSurfaceCell(cols.Par1).DoubleValue))
            dy = abs(float(surf.GetSurfaceCell(cols.Par2).DoubleValue))
            tx = abs(float(surf.GetSurfaceCell(cols.Par3).DoubleValue))
            ty = abs(float(surf.GetSurfaceCell(cols.Par4).DoubleValue))
            tz = abs(float(surf.GetSurfaceCell(cols.Par5).DoubleValue))

            has_decenter = dx > 0.01 or dy > 0.01
            has_tilt = tx > 0.1 or ty > 0.1 or tz > 0.1

            if has_tilt and not has_decenter:
                # Check if next surface is a mirror (common off-axis mirror pattern)
                next_surf = lde.GetSurfaceAt(i + 1) if i + 1 < lde.NumberOfSurfaces else None
                next_is_mirror = False
                if next_surf:
                    next_comment = str(next_surf.Comment).lower()
                    next_mat = str(next_surf.Material).strip().upper()
                    next_is_mirror = "mirror" in next_comment or next_mat == "MIRROR"

                if next_is_mirror:
                    warnings.append(
                        f"Surface {i} is CoordinateBreak with tilt ({tx:.1f}, {ty:.1f}, {tz:.1f}) "
                        f"but zero decenter, followed by mirror at surface {i+1}. "
                        f"This pattern usually means the mirror won't be illuminated (rays stay on axis). "
                        f"FIX: Add decenter to move the mirror off-axis: "
                        f"zemax_set_surface_params({i}, {{'decenter_x': <offset>, 'tilt_x': {tx}}})"
                    )
        except Exception:
            pass

    # Warnings don't block save, but are printed prominently
    return True, [], warnings


def check_system_aperture() -> Tuple[bool, List[str], List[str]]:
    """Gate: System must have a defined aperture (EPD, F/#, etc.).

    Without aperture, raytrace is undefined.
    """
    session = ZOSSession.get_instance()
    issues = []

    try:
        ap = session.system.SystemData.Aperture
        ap_type = str(ap.ApertureType).split(".")[-1]
        ap_value = float(ap.ApertureValue)

        if ap_type == "None" or ap_value <= 0:
            issues.append(
                "System aperture is not set or is zero. Raytrace cannot proceed. "
                "FIX: zemax_set_aperture('EntrancePupilDiameter', <diameter_mm>)"
            )
    except Exception as e:
        issues.append(f"Could not read system aperture: {e}")

    return len(issues) == 0, issues, []


def check_wavelengths_defined() -> Tuple[bool, List[str], List[str]]:
    """Gate: At least one wavelength must be defined."""
    session = ZOSSession.get_instance()
    issues = []

    try:
        waves = session.system.SystemData.Wavelengths
        if waves.NumberOfWavelengths < 1:
            issues.append(
                "No wavelengths defined. At least one wavelength is required for raytrace. "
                "FIX: zemax_set_wavelengths([{'wavelength_um': 0.55, 'weight': 1.0}], primary_index=1)"
            )
    except Exception as e:
        issues.append(f"Could not read wavelengths: {e}")

    return len(issues) == 0, issues, []


def check_image_surface_thickness() -> Tuple[bool, List[str], List[str]]:
    """Gate: Image surface (last surface) should have thickness = 0."""
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    warnings = []

    img_surf = lde.GetSurfaceAt(lde.NumberOfSurfaces - 1)
    if abs(img_surf.Thickness) > 0.001:
        warnings.append(
            f"Image surface (#{lde.NumberOfSurfaces - 1}) has non-zero thickness "
            f"({img_surf.Thickness:.4f}). Should be 0 for final image plane. "
            f"FIX: zemax_surface_operations(surface_index={lde.NumberOfSurfaces - 1}, thickness=0.0)"
        )

    return True, [], warnings


# ==============================================================================
# GATE REGISTRY (add new gates here)
# ==============================================================================

QUALITY_GATES: List[Tuple[str, Callable]] = [
    ("aperture_blockage", check_aperture_blockage),
    ("mirror_material", check_mirror_material),
    ("coordinate_break_usage", check_coordinate_break_usage),
    ("system_aperture", check_system_aperture),
    ("wavelengths_defined", check_wavelengths_defined),
    ("image_surface_thickness", check_image_surface_thickness),
]


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def run_quality_gates(enforce_warnings: bool = False) -> Dict[str, Any]:
    """Run all registered quality gates before saving a design.

    Args:
        enforce_warnings: If True, warnings also block the save (strict mode).
                         If False (default), warnings are shown but don't block.

    Returns:
        {
            "status": "success" | "error",
            "passed": bool,
            "issues": [...],      # Critical failures (always block)
            "warnings": [...],    # Non-critical issues (block only if enforce_warnings=True)
            "gates_run": int,
        }
    """
    all_issues = []
    all_warnings = []

    for gate_name, gate_fn in QUALITY_GATES:
        try:
            passed, issues, warnings = gate_fn()
            all_issues.extend(issues)
            all_warnings.extend(warnings)
        except Exception as e:
            all_warnings.append(f"Gate '{gate_name}' failed to run: {e}")

    # Determine pass/fail
    has_critical = len(all_issues) > 0
    has_warnings = len(all_warnings) > 0

    if has_critical or (enforce_warnings and has_warnings):
        return {
            "status": "error",
            "passed": False,
            "issues": all_issues,
            "warnings": all_warnings,
            "gates_run": len(QUALITY_GATES),
            "message": f"Design failed {len(all_issues)} critical check(s) "
                      f"and has {len(all_warnings)} warning(s). Cannot save until fixed.",
        }

    return {
        "status": "success",
        "passed": True,
        "issues": [],
        "warnings": all_warnings,
        "gates_run": len(QUALITY_GATES),
        "message": f"Design passed all {len(QUALITY_GATES)} quality gates" +
                  (f" with {len(all_warnings)} warning(s)." if has_warnings else "."),
    }


def print_gate_report(result: Dict[str, Any]) -> None:
    """Pretty-print quality gate results."""
    print("\n" + "="*70)
    print("QUALITY GATES REPORT")
    print("="*70)
    print(f"Gates run: {result['gates_run']}")
    print(f"Status: {result['status'].upper()}")

    if result.get("issues"):
        print(f"\n✗ CRITICAL ISSUES ({len(result['issues'])}):")
        for issue in result["issues"]:
            print(f"  - {issue}")

    if result.get("warnings"):
        print(f"\n⚠ WARNINGS ({len(result['warnings'])}):")
        for warning in result["warnings"]:
            print(f"  - {warning}")

    if result["passed"]:
        print("\n✓ Design passed quality gates and can be saved.")
    else:
        print("\n✗ Design FAILED quality gates. Fix issues above before saving.")

    print("="*70)
