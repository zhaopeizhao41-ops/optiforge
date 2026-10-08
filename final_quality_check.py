"""
Final Quality Check - Verify All Outputs and Generate Summary
"""

from core.zos_session import ZOSSession
import os
import json

def final_quality_check():
    """Comprehensive quality check of all project deliverables"""

    session = ZOSSession.get_instance()

    output_dir = "D:\\mcp gemini zemax\\output\\compact_spectrometer"
    main_zmx = os.path.join(output_dir, "compact_spectrometer_COMPLETE.zmx")

    print("\n" + "="*80)
    print("FINAL QUALITY CHECK")
    print("="*80)

    issues = []
    warnings = []

    # ========================================================================
    # CHECK 1: VERIFY MAIN DESIGN FILE
    # ========================================================================
    print("\n[CHECK 1] Main Design File...")

    try:
        if not os.path.exists(main_zmx):
            issues.append("Main ZMX file does not exist")
            print(f"  [FAIL] File not found: {main_zmx}")
        else:
            file_size = os.path.getsize(main_zmx)
            print(f"  [OK] File exists: {file_size} bytes")

            # Load and verify
            session.load_file(main_zmx)
            lde = session.system.LDE
            num_surfaces = lde.NumberOfSurfaces

            if num_surfaces != 14:
                issues.append(f"Expected 14 surfaces, got {num_surfaces}")
                print(f"  [FAIL] Wrong surface count: {num_surfaces}")
            else:
                print(f"  [OK] Surface count: {num_surfaces}")

            # Verify critical surface types
            types = session.ZOSAPI.Editors.LDE.SurfaceType
            cb_surfaces = []
            grating_surfaces = []

            for i in range(num_surfaces):
                surf = lde.GetSurfaceAt(i)
                if surf.Type == types.CoordinateBreak:
                    cb_surfaces.append(i)
                elif surf.Type == types.Binary2:
                    grating_surfaces.append(i)

            if len(cb_surfaces) != 2:
                issues.append(f"Expected 2 coordinate breaks, got {len(cb_surfaces)}")
                print(f"  [FAIL] Coordinate breaks: {len(cb_surfaces)}")
            else:
                print(f"  [OK] Coordinate breaks: {cb_surfaces}")

            if len(grating_surfaces) != 1:
                issues.append(f"Expected 1 grating, got {len(grating_surfaces)}")
                print(f"  [FAIL] Gratings: {len(grating_surfaces)}")
            else:
                # Verify grating parameters
                columns = session.ZOSAPI.Editors.LDE.SurfaceColumn
                grating_surf = lde.GetSurfaceAt(grating_surfaces[0])
                order = grating_surf.GetSurfaceCell(columns.Par1).DoubleValue
                density = grating_surf.GetSurfaceCell(columns.Par2).DoubleValue

                print(f"  [OK] Grating at surface {grating_surfaces[0]}")
                print(f"       Order: {order}, Density: {density} lines/um ({density*1000:.0f} gr/mm)")

                if abs(density - 1.2) > 0.01:
                    warnings.append(f"Grating density {density} != 1.2 lines/um")

            # Check wavelengths
            sys_data = session.system.SystemData
            wavelengths = sys_data.Wavelengths
            num_waves = wavelengths.NumberOfWavelengths

            if num_waves != 3:
                warnings.append(f"Expected 3 wavelengths, got {num_waves}")
                print(f"  [WARN] Wavelengths: {num_waves}")
            else:
                print(f"  [OK] Wavelengths: {num_waves}")
                for i in range(1, num_waves + 1):
                    w = wavelengths.GetWavelength(i)
                    print(f"       Wave {i}: {w.Wavelength:.4f} um")

    except Exception as e:
        issues.append(f"Design verification error: {e}")
        print(f"  [FAIL] Error: {e}")

    # ========================================================================
    # CHECK 2: VERIFY CAD EXPORTS
    # ========================================================================
    print("\n[CHECK 2] CAD Exports...")

    cad_dir = os.path.join(output_dir, "cad")
    cad_files = {
        "STEP": os.path.join(cad_dir, "spectrometer_COMPLETE.stp"),
        "IGES": os.path.join(cad_dir, "spectrometer_COMPLETE.igs")
    }

    for fmt, path in cad_files.items():
        if not os.path.exists(path):
            warnings.append(f"{fmt} file missing: {path}")
            print(f"  [WARN] {fmt} not found")
        else:
            size_kb = os.path.getsize(path) / 1024
            if size_kb < 1:
                warnings.append(f"{fmt} file too small: {size_kb:.1f} KB")
                print(f"  [WARN] {fmt} suspiciously small: {size_kb:.1f} KB")
            else:
                print(f"  [OK] {fmt}: {size_kb:.1f} KB")

    # ========================================================================
    # CHECK 3: VERIFY REPORT FILES
    # ========================================================================
    print("\n[CHECK 3] Report Files...")

    report_file = os.path.join(output_dir, "FINAL_REPORT.json")

    if not os.path.exists(report_file):
        issues.append("Final report missing")
        print(f"  [FAIL] Report not found")
    else:
        try:
            with open(report_file, 'r', encoding='utf-8') as f:
                report = json.load(f)

            print(f"  [OK] Report exists and is valid JSON")

            # Check report completeness
            required_keys = ['project', 'design_file', 'validation', 'optical_performance']
            missing_keys = [k for k in required_keys if k not in report]

            if missing_keys:
                warnings.append(f"Report missing keys: {missing_keys}")
                print(f"  [WARN] Missing keys: {missing_keys}")
            else:
                print(f"  [OK] Report contains all required sections")

            # Check validation status
            validation = report.get('validation', {})
            if not validation.get('is_valid', False):
                issues.append("Design validation failed in report")
                print(f"  [FAIL] is_valid = False")
            else:
                print(f"  [OK] Design marked as valid")

        except Exception as e:
            issues.append(f"Report read error: {e}")
            print(f"  [FAIL] Error reading report: {e}")

    # ========================================================================
    # CHECK 4: SPOT DIAGRAM RESULTS
    # ========================================================================
    print("\n[CHECK 4] Optical Performance...")

    try:
        with open(report_file, 'r', encoding='utf-8') as f:
            report = json.load(f)

        perf = report.get('optical_performance', {})

        if perf.get('status') != 'success':
            warnings.append("Spot diagram analysis failed")
            print(f"  [WARN] Analysis status: {perf.get('status')}")
        else:
            print(f"  [OK] Spot diagram completed")

            # Check if results are realistic
            rms = perf.get('rms_radius_um', 0)
            geo = perf.get('geo_radius_um', 0)

            if rms == 0 and geo == 0:
                warnings.append("Spot sizes are exactly zero (suspicious)")
                print(f"  [WARN] RMS and GEO both zero")
            else:
                print(f"  [OK] RMS spot: {rms:.3f} um")
                print(f"  [OK] GEO spot: {geo:.3f} um")

    except Exception as e:
        warnings.append(f"Could not check optical performance: {e}")
        print(f"  [WARN] Error: {e}")

    # ========================================================================
    # CHECK 5: FILE ORGANIZATION
    # ========================================================================
    print("\n[CHECK 5] File Organization...")

    expected_files = [
        "compact_spectrometer_COMPLETE.zmx",
        "FINAL_REPORT.json"
    ]

    found_count = 0
    for fname in expected_files:
        fpath = os.path.join(output_dir, fname)
        if os.path.exists(fpath):
            found_count += 1

    print(f"  [OK] Found {found_count}/{len(expected_files)} expected files")

    # Count total ZMX files
    zmx_files = [f for f in os.listdir(output_dir) if f.endswith('.zmx')]
    print(f"  [INFO] Total ZMX files: {len(zmx_files)}")

    if len(zmx_files) > 5:
        warnings.append(f"Too many ZMX files ({len(zmx_files)}) - consider cleanup")
        print(f"  [WARN] Many intermediate files present")

    # ========================================================================
    # FINAL SUMMARY
    # ========================================================================
    print("\n" + "="*80)
    print("QUALITY CHECK SUMMARY")
    print("="*80)

    if not issues:
        print("\n[OK] NO CRITICAL ISSUES FOUND")
    else:
        print(f"\n[FAIL] {len(issues)} CRITICAL ISSUES:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")

    if warnings:
        print(f"\n[WARN] {len(warnings)} WARNINGS:")
        for i, warn in enumerate(warnings, 1):
            print(f"  {i}. {warn}")
    else:
        print("\n[OK] NO WARNINGS")

    # Overall status
    if not issues and len(warnings) <= 2:
        status = "EXCELLENT"
    elif not issues:
        status = "GOOD"
    elif len(issues) <= 2:
        status = "ACCEPTABLE"
    else:
        status = "NEEDS WORK"

    print(f"\nOVERALL STATUS: {status}")

    # Save quality check results
    qc_results = {
        "status": status,
        "critical_issues": issues,
        "warnings": warnings,
        "checks_performed": 5,
        "main_design_file": main_zmx,
        "output_directory": output_dir
    }

    qc_file = os.path.join(output_dir, "QUALITY_CHECK.json")
    with open(qc_file, 'w', encoding='utf-8') as f:
        json.dump(qc_results, f, indent=2, ensure_ascii=False)

    print(f"\nQuality check report: {qc_file}")
    print("\n" + "="*80)

    return qc_results

if __name__ == '__main__':
    final_quality_check()
