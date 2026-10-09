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

INDUSTRY NORMS ENCODED HERE (see domain/optical_expert_manual.md §11):
  * Fold mirrors: after each MIRROR the beam reverses, so every following thickness
    (and radius) must be sign-flipped. Hand-built CB tilts without that flip produce a
    layout that looks folded but traces straight through - the Czerny-Turner failure.
  * Gratings must be a DiffractionGrating surface type carrying lines/um + order; a
    Standard surface with "grating" in its Comment is just a flat mirror, i.e. no
    dispersion at all (silently wrong spectrum, not a broken file).
  * Manufacturability (Edmund Optics knowledge center, Karow 2004):
      - edge thickness >= ~0.7 mm evaluated at the clear aperture + 1 mm diameter;
      - Karow Z = |D1/R1 + D2/R2| > 0.56 self-centers in a bell chuck, below it the
        optic is centered by hand (cost/schedule risk, not a defect);
      - concentric radii |R1 - R2 -/+ CT| > 2 mm for same-sign radii;
      - avoid hemispheric (|R| <= 0.7 * D) and near-flat (sag <= 100 um) surfaces;
      - D:CT above ~10:1 and past 15:1 drives polishing cost up.
"""
import math
from typing import Dict, Any, List, Callable, Tuple
from core.zos_session import ZOSSession
from core.editor_cells import read_cell


# ==============================================================================
# KNOWN FAILURE MODES (case studies that drove each gate)
# ==============================================================================

KNOWN_FAILURE_MODES = {
    "fold_mirror_without_sign_flip": {
        "date": "2026-10-08",
        "symptom": "Off-axis mirrors do not show a folded beam in the 3D layout; rays appear to "
                   "travel straight through the mirror stack and the image never forms",
        "root_cause": "The fold was built by hand as a CoordinateBreak tilt instead of the "
                     "LDE fold-mirror tool, so the thickness/radius sign flip that a reflection "
                     "requires was never applied. A CB tilt only rotates the local axis; it does "
                     "not reverse the propagation direction, so the downstream surfaces stay on "
                     "the far side of the mirror.",
        "correct_pattern": "zemax_add_fold_mirror -> CB(theta) / MIRROR / CB(-theta), with negative "
                          "thicknesses (and negated radii) for everything after the mirror. No "
                          "decenter is needed for a plane fold mirror.",
        "gate": "check_fold_sign_consistency",
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
    "grating_not_modeled": {
        "date": "2026-10-08",
        "symptom": "Spectrometer image forms but carries no dispersion: the 'grating' behaves as a "
                   "flat mirror, so the detector sees one spot instead of a spectrum. The .zmx is "
                   "valid and opens without error, so nothing warns the designer.",
        "root_cause": "The dispersive element was left as a Standard surface with 'grating' in its "
                     "Comment. A surface only disperses when its Type is DiffractionGrating, with "
                     "lines/um in Par1 and the order in Par2 (and MIRROR material when used in "
                     "reflection).",
        "gate": "check_grating_surface",
    },
    "image_thickness_inf_false_positive": {
        "date": "2026-10-08",
        "symptom": "Gate reported 'image surface should have thickness 0' on designs whose image "
                   "plane carries a solve (MarginalRayHeight / pickup); the raw cell reads inf.",
        "root_cause": "The image-surface thickness was compared without filtering non-finite values "
                     "or skipping solve-driven cells.",
        "gate": "check_image_surface_thickness",
    },
}

# Manufacturability thresholds (see module docstring for sources).
MIN_EDGE_THICKNESS_MM = 0.7          # at the clear aperture + 1 mm diameter
KAROW_SELF_CENTER_MIN = 0.56         # below this, bell-chuck centering fails
CONCENTRICITY_MIN_MM = 2.0           # same-sign radii only
HEMISPHERIC_RADIUS_RATIO = 0.7       # |R| <= 0.7 * D is hemispheric
NEAR_FLAT_SAG_MM = 0.1               # sag <= 100 um is effectively flat
DIA_CT_INFO = 10.0                   # D:CT above this starts costing money
DIA_CT_WARN = 15.0                   # ... and this is where it bites


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
    """Gate: Surfaces labeled 'mirror'/'reflect' must have Material='MIRROR'.

    Triggered by: Reflective spectrometer designs (2026-10-08)
    Gratings are handled separately by check_grating_surface, because a grating may
    legitimately be used in transmission (no MIRROR material).
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    issues = []

    for i in range(1, lde.NumberOfSurfaces - 1):
        surf = lde.GetSurfaceAt(i)
        comment_lower = str(surf.Comment).lower()
        material = str(surf.Material).strip().upper()

        if any(keyword in comment_lower for keyword in ["mirror", "reflect"]):
            if material not in ("MIRROR", ""):
                issues.append(
                    f"Surface {i} ({surf.Comment}) is labeled as reflective but "
                    f"Material={material} (not MIRROR). Ray will refract instead of reflect. "
                    f"FIX: zemax_surface_operations(surface_index={i}, material='MIRROR')"
                )

    return len(issues) == 0, issues, []


def check_fold_sign_consistency() -> Tuple[bool, List[str], List[str]]:
    """Gate: thickness/radius signs must flip after every reflecting surface.

    Triggered by: the Czerny-Turner layout that traced straight through the mirrors
    (2026-10-08). See KNOWN_FAILURE_MODES["fold_mirror_without_sign_flip"].

    In the LDE the thickness column is the distance to the next surface measured along
    the current propagation direction. A reflection reverses that direction, so every
    thickness after a MIRROR is negative (and every radius sign is flipped). This is
    exactly what RunTool_AddFoldMirror does for you; a hand-built CoordinateBreak tilt
    does not, and the layout silently stops folding.

    Reports warnings, not failures: a sign slip is a real error, but the check is
    geometric inference and must not be able to block an exotic-but-correct file.
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    warnings: List[str] = []

    reflected = False
    for i in range(1, lde.NumberOfSurfaces):
        surf = lde.GetSurfaceAt(i)
        surf_type = str(surf.Type).split(".")[-1]
        material = str(surf.Material).strip().upper()
        is_mirror = material == "MIRROR" or "Mirror" in surf_type

        # The mirror's own thickness is measured after the reflection.
        if is_mirror:
            reflected = not reflected

        try:
            thickness = float(surf.Thickness)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(thickness) or thickness == 0.0:
            continue

        if reflected and thickness > 0:
            warnings.append(
                f"Surface {i} ({surf.Comment or surf_type}) has thickness {thickness:+.3f} after a "
                f"reflection: the beam has reversed, so this must be negative or the following "
                f"surfaces stay on the wrong side of the mirror. "
                f"FIX: zemax_surface_operations(surface_index={i}, thickness={-thickness:.3f}) "
                f"— or rebuild the fold with zemax_add_fold_mirror, which flips the sign for you."
            )
        elif not reflected and thickness < 0:
            warnings.append(
                f"Surface {i} ({surf.Comment or surf_type}) has negative thickness {thickness:+.3f} "
                f"with no preceding mirror. Either a mirror is missing its MIRROR material, or the "
                f"minus sign is a leftover from an unfolding edit."
            )

    return True, [], warnings


def check_grating_surface() -> Tuple[bool, List[str], List[str]]:
    """Gate: a surface described as a grating must really be a DiffractionGrating.

    Triggered by: the spectrometer whose "grating" was a plain flat mirror (2026-10-08).
    See KNOWN_FAILURE_MODES["grating_not_modeled"].

    Checks, for every surface whose Comment mentions a grating:
      1. Type == DiffractionGrating   (otherwise there is no dispersion at all);
      2. Par1 = lines/um > 0          (the grating constant; 0 means an infinitely fine
                                       grating that Zemax cannot use);
      3. Par2 = order != 0            (order 0 is a plain mirror);
      4. Material MIRROR for a reflective grating (warning - transmission gratings exist).
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    columns = session.ZOSAPI.Editors.LDE.SurfaceColumn
    issues: List[str] = []
    warnings: List[str] = []

    for i in range(1, lde.NumberOfSurfaces - 1):
        surf = lde.GetSurfaceAt(i)
        if "grating" not in str(surf.Comment).lower():
            continue

        surf_type = str(surf.Type).split(".")[-1]
        if "Grating" not in surf_type:
            issues.append(
                f"Surface {i} ({surf.Comment}) is described as a grating but its Type is "
                f"{surf_type}, so it disperses nothing — the detector will see a single spot, not "
                f"a spectrum. FIX: zemax_set_surface_type(surface_index={i}, "
                f"surface_type='DiffractionGrating') then zemax_set_surface_params({i}, "
                f"{{'lines_per_um': <l/mm>, 'order': 1}})."
            )
            continue

        lines_per_um = float(read_cell(surf.GetSurfaceCell(columns.Par1)))
        order = float(read_cell(surf.GetSurfaceCell(columns.Par2)))
        if not lines_per_um > 0:
            issues.append(
                f"Surface {i} ({surf.Comment}) is a grating with Par1 (lines/um) = {lines_per_um:g}. "
                f"FIX: zemax_set_surface_params({i}, {{'lines_per_um': <l/mm>, 'order': 1}})."
            )
        if order == 0:
            issues.append(
                f"Surface {i} ({surf.Comment}) is a grating with Par2 (order) = 0: zeroth order is "
                f"undiffracted, i.e. a plain mirror. FIX: set 'order' to +1 (or -1) to match the "
                f"layout direction of the diffracted beam."
            )
        if str(surf.Material).strip().upper() != "MIRROR":
            warnings.append(
                f"Surface {i} ({surf.Comment}) is a grating with Material="
                f"{str(surf.Material).strip() or '<unset>'}. If this is a reflection grating the "
                f"material must be MIRROR; leave it blank only for a transmission grating. Note "
                f"that Zemax does not model grating efficiency at all."
            )

    return len(issues) == 0, issues, warnings


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
    """Gate: Image surface (last surface) should have a fixed thickness of 0.

    Triggered by: a false positive on solve-driven image planes (2026-10-08) — the raw
    cell of a MarginalRayHeight/pickup solve reads inf, which is legitimate. Only a
    finite, solve-free, non-zero thickness is an actual problem.
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    warnings = []

    img_index = lde.NumberOfSurfaces - 1
    img_surf = lde.GetSurfaceAt(img_index)
    try:
        thickness = float(img_surf.Thickness)
    except (TypeError, ValueError):
        return True, [], []

    # GetSolveData() can legitimately come back null on some surface/cell combinations;
    # an unreadable solve must not turn into a gate crash.
    try:
        solve_data = img_surf.ThicknessCell.GetSolveData()
        solve_type = str(solve_data.Type).split(".")[-1] if solve_data is not None else "Fixed"
    except Exception:
        solve_type = "Fixed"

    if math.isfinite(thickness) and abs(thickness) > 0.001 and solve_type == "Fixed":
        warnings.append(
            f"Image surface (#{img_index}) has non-zero thickness ({thickness:.4f}). Should be 0 "
            f"for a final image plane. "
            f"FIX: zemax_surface_operations(surface_index={img_index}, thickness=0.0)"
        )

    return True, [], warnings


def check_manufacturability() -> Tuple[bool, List[str], List[str]]:
    """Gate: shop-floor rules that decide whether the design can be built at all.

    Warnings only — these constrain cost and yield, they do not make the file invalid.

    Every rule is the industry default quoted by the Edmund Optics design-for-
    manufacturability notes (with Karow 2004 for the centering factor), evaluated on the
    element's clear-aperture diameter:
      * edge thickness >= 0.7 mm at (CA + 1 mm) diameter, since the blank is edged down
        to size and the rim must survive grinding and beveling;
      * Karow Z = |D1/R1 + D2/R2| > 0.56 so bell-chuck centering works automatically;
      * concentric radii (same-sign R1, R2) need |R1 - R2 -/+ CT| > 2 mm of material to
        remove during centering;
      * hemispheric (|R| <= 0.7 D) and near-flat (sag <= 100 um) surfaces are the two
        shapes polishing houses reject;
      * diameter:center-thickness above 10:1 (and badly above 15:1) means more polishing
        time, more wedge and more cost for the same wavefront.
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    warnings: List[str] = []

    def _f(value: Any) -> float:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return float("nan")
        return out if math.isfinite(out) else float("nan")

    def _sag(radius: float, y: float) -> float:
        """Signed sagitta, matching OpticalRuleCheck.compute_edge_thickness (positive for R > 0).

        A hyper-hemispheric surface (|y| >= |R|) has no finite sag; return 0 rather than
        raise, since the shape itself is reported by the hemisphere rule.
        """
        if radius == 0 or not math.isfinite(radius) or abs(y) >= abs(radius):
            return 0.0
        sign = 1.0 if radius > 0 else -1.0
        return radius - sign * math.sqrt(radius * radius - y * y)

    for i in range(1, lde.NumberOfSurfaces - 1):
        surf = lde.GetSurfaceAt(i)
        material = str(surf.Material).strip()
        if not material or material.upper() == "AIR":
            continue

        nxt = lde.GetSurfaceAt(i + 1)
        r1, r2 = _f(surf.Radius), _f(nxt.Radius)
        ct = _f(surf.Thickness)
        semi = max(_f(surf.SemiDiameter), _f(nxt.SemiDiameter))
        if not math.isfinite(semi) or semi <= 0 or not math.isfinite(ct) or not math.isfinite(r1):
            continue
        dia = 2.0 * semi

        # Edge thickness at the blanked diameter (CA + 1 mm), the value a shop quotes.
        semi_prime = semi + 0.5
        et = ct - _sag(r1, semi_prime) + (_sag(r2, semi_prime) if math.isfinite(r2) else 0.0)
        if et < MIN_EDGE_THICKNESS_MM:
            warnings.append(
                f"Surface {i} ({material}) edge thickness {et:.2f} mm at the {2 * semi_prime:.1f} mm "
                f"blank diameter is below the {MIN_EDGE_THICKNESS_MM} mm shop minimum: the rim will "
                f"chip during edging/beveling. FIX: increase center thickness or flatten the sag."
            )

        # Karow Z factor: can the optic center itself in a bell chuck?
        karow = 0.0
        for radius, chuck_dia in ((r1, dia), (r2, dia)):
            if math.isfinite(radius) and abs(radius) > 1e-9:
                karow += chuck_dia / radius
        karow = abs(karow)
        if karow < KAROW_SELF_CENTER_MIN:
            warnings.append(
                f"Surface {i} ({material}) Karow factor Z = |D1/R1 + D2/R2| = {karow:.2f} < "
                f"{KAROW_SELF_CENTER_MIN}. The element will not self-center in a bell chuck and "
                f"needs manual centering (cost and schedule risk). FIX: bend the element into a "
                f"meniscus or re-split the power so the two surfaces are not nearly concentric."
            )

        # Concentric radii: how much material must come off while centering?
        if math.isfinite(r2) and r1 != 0 and r2 != 0 and (r1 > 0) == (r2 > 0):
            margin = abs(r1 - r2 - ct) if r1 > 0 else abs(r1 - r2 + ct)
            if margin < CONCENTRICITY_MIN_MM:
                warnings.append(
                    f"Surface {i} ({material}) concentricity |R1 - R2 -/+ CT| = {margin:.2f} mm < "
                    f"{CONCENTRICITY_MIN_MM} mm: centering has to remove a lot of material to fix "
                    f"surface-to-surface decentering. FIX: make the radii less concentric."
                )

        # Shapes a polishing house will not quote: hemispheres and near-flats.
        for label, radius in (("front", r1), ("rear", r2)):
            if not math.isfinite(radius) or radius == 0:
                continue
            if abs(radius) <= HEMISPHERIC_RADIUS_RATIO * dia:
                warnings.append(
                    f"Surface {i} ({material}, {label} face) has |R| = {abs(radius):.2f} mm <= "
                    f"{HEMISPHERIC_RADIUS_RATIO} x diameter ({dia:.2f} mm): a hemispheric bowl is "
                    f"very hard to grind and polish. FIX: increase |R| or split the element."
                )
            elif 0 < abs(_sag(radius, semi)) <= NEAR_FLAT_SAG_MM:
                warnings.append(
                    f"Surface {i} ({material}, {label} face) has a sag of "
                    f"{abs(_sag(radius, semi)) * 1000:.1f} um: effectively a flat, which is as hard "
                    f"to make as a deep curve because the radius cannot be verified against a test "
                    f"plate. FIX: make it truly flat (radius = inf) or give it real curvature."
                )

        if math.isfinite(ct) and ct > 0 and (dia / ct) > DIA_CT_INFO:
            severity = "well past" if (dia / ct) > DIA_CT_WARN else "above"
            warnings.append(
                f"Surface {i} ({material}) has a diameter:center-thickness ratio of {dia / ct:.1f}:1, "
                f"{severity} the {DIA_CT_INFO:.0f}:1 where polishing cost climbs — thin blanks flex "
                f"under polishing pressure and come out with wedge. FIX: thicken the element or "
                f"reduce the diameter."
            )

    return True, [], warnings


# ==============================================================================
# GATE REGISTRY (add new gates here)
# ==============================================================================

QUALITY_GATES: List[Tuple[str, Callable]] = [
    ("aperture_blockage", check_aperture_blockage),
    ("mirror_material", check_mirror_material),
    ("fold_sign_consistency", check_fold_sign_consistency),
    ("grating_surface", check_grating_surface),
    ("system_aperture", check_system_aperture),
    ("wavelengths_defined", check_wavelengths_defined),
    ("image_surface_thickness", check_image_surface_thickness),
    ("manufacturability", check_manufacturability),
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
