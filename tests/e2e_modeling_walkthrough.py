"""End-to-end walkthrough of the mandatory 5-stage workflow against a live OpticStudio.

Drives the tool layer exactly as server.py does (same functions, same JSON shape) so a
signature or gate regression shows up as a failure here instead of silently.

Run: python -B tests/e2e_modeling_walkthrough.py
"""
import json
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
# The audit payload carries emoji and Chinese; the default Windows console codec (GBK)
# cannot encode them and would abort the walkthrough mid-stage.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import (  # noqa: E402
    zemax_audit_requirements,
    zemax_register_design_proposal,
    zemax_confirm_design_proposal,
    get_current_design_proposal,
    zemax_new_file,
    zemax_set_aperture,
    zemax_set_fields,
    zemax_set_wavelengths,
    zemax_surface_operations,
    zemax_insert_surface,
    zemax_set_solve,
    zemax_set_surface_params,
    zemax_setup_merit_function,
    zemax_run_optimization,
    zemax_run_spot_diagram,
    zemax_run_fft_mtf,
    zemax_validate_design_rules,
    zemax_get_system_data,
    zemax_save_file,
    zemax_get_envelope,
    zemax_fiber_coupling,
    zemax_scan_pupil_check,
    zemax_audit_retroreflection,
    zemax_generate_barrel_assembly,
    zemax_compute_optomech_spacing,
    zemax_generate_mount_interface,
    zemax_run_tolerance_analysis,
)

PROJECT = "e2e_633nm_singlet"
failures = []


def stage(label, result):
    """Print one stage's verdict and record hard errors."""
    ok = isinstance(result, dict) and result.get("status") != "error"
    mark = "OK  " if ok else "FAIL"
    print(f"\n=== [{mark}] {label} ===")
    print(json.dumps(result, indent=2, ensure_ascii=False)[:1400])
    if not ok:
        failures.append(f"{label}: {result.get('message') or result.get('code')}")
    return result


def main():
    # ---------------------------------------------------------------- Step 0
    stage("Step 0 audit (incomplete spec)", zemax_audit_requirements(
        system_type="imaging_lens",
        specs={"efl_mm": 25.0, "f_number": 4.0},
        user_prompt="design a 25mm lens",
    ))

    stage("Step 0 audit (complete spec)", zemax_audit_requirements(
        system_type="imaging_lens",
        specs={"efl_mm": 25.0, "f_number": 4.0, "fov_or_sensor": "24 deg full field",
               "wavelength_range": "633 nm", "pixel_pitch_um": 3.45,
               "max_total_track_mm": 40.0, "working_distance_mm": 15.0},
        user_prompt="633nm confocal probe objective, 25mm EFL, F/4, 24deg FOV",
    ))

    # ---------------------------------------------------------------- Step 1
    # (web/patent search is an agent action, not a tool call; recorded in the proposal)
    initial_structure_source = (
        "Cooke-style singlet baseline from Smith, Modern Lens Design (Ch.2 singlet "
        "achromatization case study), scaled to EFL 25 mm / F/4 at 633 nm."
    )

    # ---------------------------------------------------------------- Step 2/3
    stage("Step 3 register proposal (gate stays closed)",
          zemax_register_design_proposal(
              project_name=PROJECT,
              target_specs={"efl_mm": 25.0, "f_number": 4.0, "fov_deg": 24.0,
                            "wavelength_um": 0.633, "totr_mm": 40.0},
              initial_structure_source=initial_structure_source,
              optical_theory_analysis=(
                  "EPD = 25/4 = 6.25 mm. For a single 633 nm element the dominant term is "
                  "spherochromatism; the design targets a plano-convex with the convex side "
                  "toward the object so the marginal ray is bent at one surface only, then "
                  "residual defocus is taken out by the image-space solve."
              ),
              glass_selection_rationale=(
                  "H-K9L (CDGM preferred list, N-BK7 equivalent): n=1.5151 at 633 nm, "
                  "Vd=64.2, good acid/weathering resistance for an exposed first surface."
              ),
              merit_function_strategy=(
                  "Stage 1 RMS Spot with thicknesses frozen; Stage 2 release air gap with "
                  "MXCA/MNEG barriers; Stage 3 switch to RMS Wavefront."
              ),
              mechanical_constraints="Flat land 1.0 mm, standard OD series, ET >= 1.2 mm.",
              internal_air_spacing_budget="BFL only, <= 12 mm; barrel L/D <= 2.5.",
              user_confirmed_to_simulate=False,
          ))

    # Gate must still be closed here.
    proposal = get_current_design_proposal()
    print(f"\n[check] gate after register: user_confirmed_to_simulate="
          f"{proposal.get('user_confirmed_to_simulate')}")
    if proposal.get("user_confirmed_to_simulate") is True:
        failures.append("register leaked an open gate")

    # Prove the gate actually blocks a model write before Step 4.
    blocked = zemax_set_aperture("EntrancePupilDiameter", 6.25)
    print(f"[check] model write before confirm -> status={blocked.get('status')} "
          f"code={blocked.get('code')}")
    if blocked.get("status") != "error" or blocked.get("code") != "PROPOSAL_NOT_CONFIRMED":
        failures.append("model write was NOT blocked before confirmation")

    # ---------------------------------------------------------------- Step 4
    stage("Step 4 confirm proposal", zemax_confirm_design_proposal(
        user_confirmed_to_simulate=True))

    # The model must exist before any edit: the active project's model is bound by
    # zemax_new_file / zemax_load_file, not by the confirmation call.
    stage("New file", zemax_new_file(catalogs=["CDGM"]))
    stage("Gate now open: set aperture", zemax_set_aperture("EntrancePupilDiameter", 6.25))
    stage("Set wavelengths (633 nm)", zemax_set_wavelengths(
        [{"wavelength_um": 0.633, "weight": 1.0}], primary_index=1))
    # Fields must fit the format the element can actually cover. At f=25 mm the image
    # surface carries a 4 mm semi-diameter (~8 mm image circle), i.e. +-9 deg. A 24 deg
    # field would put the paraxial image height at 11.1 mm, outside the format: the spot
    # terms for it then dominate the merit function and buy their own improvement by
    # dragging the image plane to a defocused compromise. Stay inside the format.
    stage("Set fields", zemax_set_fields("Angle", [
        {"x": 0.0, "y": 0.0}, {"x": 0.0, "y": 5.0}, {"x": 0.0, "y": 9.0},
    ]))

    # A fresh file is OBJ / IMA only. Insert one surface so the singlet has a real rear
    # glass surface: 0 = OBJ, 1 = front, 2 = rear, 3 = IMA. Editing surface 2 without
    # inserting would have written to the image surface.
    stage("Insert rear glass surface", zemax_insert_surface(surface_index=2))
    stage("Surface 1 (front, convex, plano-convex toward object)", zemax_surface_operations(
        surface_index=1, radius=13.2, thickness=3.0, material="H-K9L",
        semi_diameter=4.2, comment="633nm H-K9L front"))
    stage("Surface 2 (rear, plane)", zemax_surface_operations(
        surface_index=2, radius=0.0, thickness=20.0, semi_diameter=4.0,
        comment="rear plane, BFL"))

    # ---------------------------------------------------------------- Optimize
    # Optimization only moves cells that are marked variable in the LDE/MFE. A fresh
    # surface carries fixed solves, so leaving them alone is why zemax_run_optimization
    # previously reported `variables: 0` and returned without touching the model.
    #
    # Released variables, and the job each one does:
    #   * surface 1 radius  -> element power and bending (the main design lever).
    #   * surface 2 thickness -> back focal distance, i.e. defocus. Without this the
    #     optimizer cannot null the focus term and the spot stays defocus-dominated.
    # Deliberately NOT released:
    #   * surface 2 radius -> the rear face stays plano. It is the flat the mechanical
    #     stack seats against, so it must not drift during optimization.
    stage("Release surface 1 radius (variable)",
          zemax_set_solve(surface_index=1, cell="radius", solve_type="variable"))
    stage("Release surface 2 thickness (variable)",
          zemax_set_solve(surface_index=2, cell="thickness", solve_type="variable"))

    # Weight the fields towards the axis. All three now sit inside the format, but the
    # singlet's residual is dominated by on-axis spherical aberration, and the off-axis
    # fields are the ones that would otherwise buy their own correction by pulling the
    # image plane off the axis. Down-weighting them keeps focus where it belongs; the
    # optimizer's focus_diagnostic reports the result.
    stage("Merit function with barriers", zemax_setup_merit_function(
        criterion="RMS_Spot", rings=4, arms=6,
        min_air_center=0.5, max_air_center=12.0, min_air_edge=0.8,
        min_glass_center=1.0, min_glass_edge=1.2,
        target_efl=25.0, efl_weight=100.0, max_totr=40.0,
        field_weights=[1.0, 0.5, 0.25],
    ))
    stage("Quick focus / optimize", zemax_run_optimization(
        algorithm="DLS", cycles="Automatic", max_rounds=2))

    # ---------------------------------------------------------------- Analyse
    stage("System data", zemax_get_system_data())
    stage("Spot diagram", zemax_run_spot_diagram())
    stage("FFT MTF", zemax_run_fft_mtf())
    stage("Design rule check", zemax_validate_design_rules())

    # ---------------------------------------------------------------- Phase 2-4 tools
    # These are the ones that were broken; exercise each through its real signature.
    stage("PSF envelope", zemax_get_envelope(first_surface=1, last_surface=2))
    stage("Fiber coupling (10um core, NA 0.14)", zemax_fiber_coupling(
        fiber_diameter_um=10.0, fiber_na=0.14, field=1, wavelength=1))
    stage("Retroreflection audit", zemax_audit_retroreflection(
        detector_surface=2, source_surface=0))

    # ---------------------------------------------------------------- Mechanical
    stage("Barrel assembly", zemax_generate_barrel_assembly(
        first_surface=1, wall_thickness_mm=3.0, flange_thickness_mm=5.0))
    stage("Optomech spacing", zemax_compute_optomech_spacing(
        element_surfaces=[1], target_clearance_mm=0.5))
    stage("Mount interface (C-mount)", zemax_generate_mount_interface(
        mount_surface=2, interface_type="C-mount"))

    # ---------------------------------------------------------------- Save
    stage("Save file", zemax_save_file())

    print("\n" + "=" * 62)
    if failures:
        print(f"FAILED ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
    else:
        print("ALL STAGES PASSED")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
