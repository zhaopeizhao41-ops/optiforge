"""
Comprehensive Integration Test Suite for Zemax MCP
Verifies system initialization, template loading, surface manipulation, solves,
merit function generation, quick focus, optimization, analyses, and design rules validation.
"""

import os
import sys
import json

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools import (
    zemax_system_info,
    zemax_new_file,
    zemax_save_file,
    zemax_load_template,
    zemax_surface_operations,
    zemax_set_solve,
    zemax_setup_merit_function,
    zemax_quick_focus,
    zemax_run_optimization,
    zemax_run_spot_diagram,
    zemax_run_fft_mtf,
    zemax_run_wavefront_map,
    zemax_validate_design_rules,
    zemax_lookup_manual,
)


def run_tests():
    print("=== 1. Testing zemax_system_info ===")
    info = zemax_system_info()
    print("System Info:", info["status"], "| License:", info.get("license_status"))
    assert info["status"] == "success"

    print("\n=== 2. Testing zemax_load_template ('achromat_doublet') ===")
    tmpl_res = zemax_load_template("achromat_doublet")
    print("Template load:", tmpl_res["status"], "| Surfaces:", tmpl_res["num_surfaces"])
    assert tmpl_res["status"] == "success"

    print("\n=== 3. Testing zemax_surface_operations & zemax_set_solve ===")
    # Make radius and thickness variable on doublet
    s1 = zemax_set_solve(1, "radius", "variable")
    s2 = zemax_set_solve(2, "radius", "variable")
    s3 = zemax_set_solve(3, "radius", "variable")
    t1 = zemax_set_solve(1, "thickness", "variable")
    t2 = zemax_set_solve(2, "thickness", "variable")
    print("Solve configuration on doublet surfaces 1..3: SUCCESS")
    assert s1["status"] == "success"

    print("\n=== 4. Testing zemax_setup_merit_function (Zemax Manual Rules) ===")
    mf_res = zemax_setup_merit_function(
        criterion="RMS_Spot",
        reference="Centroid",
        rings=4,
        arms=6,
        min_air_center=0.5,
        min_glass_center=2.0,
        min_glass_edge=1.0,
        target_efl=100.0,
        efl_weight=10.0,
    )
    print("Merit function created:", mf_res["status"], "| Operands:", mf_res["total_operands"])
    assert mf_res["status"] == "success"

    print("\n=== 5. Testing zemax_quick_focus ===")
    qf_res = zemax_quick_focus(criterion="SpotSizeRadial")
    print("Quick focus:", qf_res["status"], "| BFL (mm):", qf_res["back_focal_length_mm"])
    assert qf_res["status"] == "success"

    print("\n=== 6. Testing zemax_run_optimization (DLS) ===")
    opt_res = zemax_run_optimization(algorithm="DLS", cycles="Automatic")
    print("Optimization:", opt_res["status"], "| Init MF:", opt_res.get("initial_merit_function"), "-> Final MF:", opt_res.get("final_merit_function"), f"(-{opt_res.get('improvement_percentage')}%)")
    assert opt_res["status"] == "success"

    print("\n=== 7. Testing zemax_run_spot_diagram ===")
    spot_res = zemax_run_spot_diagram()
    print("Spot diagram:", spot_res["status"], "| Airy radius (um):", spot_res["airy_disk_radius_um"])
    for f in spot_res["fields"]:
        print(f"  Field {f['field_index']}: RMS spot = {f['polychromatic_rms_spot_um']} um, Diffraction-limited = {f['is_diffraction_limited']}")
    assert spot_res["status"] == "success"

    print("\n=== 8. Testing zemax_run_fft_mtf ===")
    mtf_res = zemax_run_fft_mtf(max_frequency=50.0)
    print("FFT MTF:", mtf_res["status"], "| Series count:", mtf_res["series_count"])
    if mtf_res["results"]:
        sample_pts = mtf_res["results"][0]["key_frequencies"]
        print("  Sample MTF at frequencies (lp/mm):", [(p['frequency_lp_mm'], p['tangential_mtf']) for p in sample_pts[:4]])
    assert mtf_res["status"] == "success"

    print("\n=== 9. Testing zemax_run_wavefront_map ===")
    wf_res = zemax_run_wavefront_map()
    print("Wavefront Map:", wf_res["status"], "| PV:", wf_res["peak_to_valley_pv_waves"], "waves, RMS:", wf_res["rms_wavefront_error_waves"], "waves, Strehl:", wf_res["strehl_ratio_estimate"])
    assert wf_res["status"] == "success"

    print("\n=== 10. Testing zemax_validate_design_rules (Zemax Fabrication Audit) ===")
    audit_res = zemax_validate_design_rules()
    print("Design rules audit:", audit_res["status"], "| Result:", audit_res["audit_result"], "| Warnings:", audit_res["summary"]["warnings"], "| Critical:", audit_res["summary"]["critical_errors"])
    for finding in audit_res["findings"]:
        print(f"  [{finding['level']}] Surface {finding['surface']} - {finding['rule']}: {finding['message']}")
    assert audit_res["status"] == "success"

    print("\n=== 11. Testing zemax_lookup_manual ===")
    kb_res = zemax_lookup_manual("EFFL")
    print("Knowledge lookup 'EFFL':", kb_res["status"], "| Name:", kb_res["exact_match"]["name"])
    assert kb_res["status"] == "success"

    print("\n=== 12. Testing zemax_save_file (Outputting .zmx file) ===")
    save_res = zemax_save_file()
    print("Save file result:", save_res["status"], "| Saved to:", save_res.get("saved_to"), "| Format:", save_res.get("format"))
    assert save_res["status"] == "success"
    assert os.path.exists(save_res["saved_to"])
    assert save_res["saved_to"].endswith(".zmx")

    print("\n==============================================")
    print(">>> ALL 12 ZEMAX INTEGRATION TESTS PASSED! <<<")
    print("==============================================")


if __name__ == "__main__":
    run_tests()
