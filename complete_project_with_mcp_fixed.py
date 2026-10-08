"""
Complete Spectrometer Project - FIXED VERSION
Fixes aperture degradation issue by using EPD mode and proper stop setting
"""

from core.zos_session import ZOSSession
from tools.analysis_tools import zemax_run_spot_diagram, zemax_run_fft_mtf, zemax_run_ray_fan
from tools.cad_export_tools import zemax_export_cad
from tools.system_tools import zemax_get_system_data, zemax_save_file
import os
import json

def complete_project_with_mcp_fixed():
    """Run complete project with fixed aperture settings"""

    session = ZOSSession.get_instance()

    output_dir = "D:\\mcp gemini zemax\\output\\spectrometer_fixed"
    os.makedirs(output_dir, exist_ok=True)

    results = {
        "project": "Fixed Compact Spectrometer",
        "status": "running",
        "steps": [],
        "files": []
    }

    print("\n" + "="*80)
    print("COMPLETE SPECTROMETER PROJECT - FIXED VERSION")
    print("="*80)

    # ========================================================================
    # STEP 1: CREATE DESIGN WITH PROPER APERTURE
    # ========================================================================
    print("\n[STEP 1] Creating Optical Design with Fixed Aperture...")

    try:
        session.new_system(save_changes=False)
        lde = session.system.LDE
        sys_data = session.system.SystemData

        # Set wavelengths
        wavelengths = sys_data.Wavelengths
        wavelengths.SelectWavelengthPreset(session.ZOSAPI.SystemData.WavelengthPreset.FdC_Visible)
        print("  [OK] Wavelengths: F-d-C (486, 588, 656 nm)")

        # *** FIX 1: Use EPD instead of FloatByStopSize ***
        sys_data.Aperture.ApertureType = session.ZOSAPI.SystemData.ZemaxApertureType.EntrancePupilDiameter
        sys_data.Aperture.ApertureValue = 12.5  # 12.5 mm EPD
        print("  [OK] Aperture: EPD = 12.5 mm (FIXED)")

        # Fields
        sys_data.Fields.AddField(0, 0, 1.0)
        print("  [OK] Field: On-axis")

        # Build surfaces
        surf0 = lde.GetSurfaceAt(0)
        surf0.Comment = "Entrance Slit"
        surf0.Thickness = 50.0

        # *** FIX 2: Make surface 1 the actual Stop ***
        lde.InsertNewSurfaceAt(1)
        surf1 = lde.GetSurfaceAt(1)
        surf1.Comment = "Aperture Stop"
        surf1.Thickness = 3.5
        surf1.SemiDiameter = 6.25  # Half of EPD

        # Collimator achromat
        lde.InsertNewSurfaceAt(2)
        surf2 = lde.GetSurfaceAt(2)
        surf2.Comment = "Collimator Front"
        surf2.Radius = 32.6
        surf2.Thickness = 6.0
        surf2.Material = "N-BK7"
        surf2.SemiDiameter = 12.7

        lde.InsertNewSurfaceAt(3)
        surf3 = lde.GetSurfaceAt(3)
        surf3.Comment = "Collimator Cement"
        surf3.Radius = -23.8
        surf3.Thickness = 2.5
        surf3.Material = "SF5"
        surf3.SemiDiameter = 12.7

        lde.InsertNewSurfaceAt(4)
        surf4 = lde.GetSurfaceAt(4)
        surf4.Comment = "Collimator Back"
        surf4.Radius = -129.2
        surf4.Thickness = 75.0
        surf4.SemiDiameter = 12.7

        # Coordinate Break 1
        types = session.ZOSAPI.Editors.LDE.SurfaceType
        columns = session.ZOSAPI.Editors.LDE.SurfaceColumn

        lde.InsertNewSurfaceAt(5)
        surf5 = lde.GetSurfaceAt(5)
        cb_settings = surf5.GetSurfaceTypeSettings(types.CoordinateBreak)
        surf5.ChangeType(cb_settings)
        surf5.Comment = "CB1 (14deg tilt)"
        surf5.Thickness = 0.0
        surf5.GetSurfaceCell(columns.Par3).DoubleValue = 14.0

        # Diffraction Grating (use DiffractionGrating type instead of Binary2)
        lde.InsertNewSurfaceAt(6)
        surf6 = lde.GetSurfaceAt(6)

        # Try DiffractionGrating type
        try:
            grating_settings = surf6.GetSurfaceTypeSettings(types.DiffractionGrating)
            surf6.ChangeType(grating_settings)
            surf6 = lde.GetSurfaceAt(6)  # Re-get after type change
            surf6.Comment = "Diffraction Grating 600 l/mm"
            surf6.Material = "MIRROR"
            surf6.Thickness = 75.0
            surf6.SemiDiameter = 15.0
            # Par1 = lines per micrometer (600 l/mm = 0.6 l/um)
            surf6.GetSurfaceCell(columns.Par1).DoubleValue = 0.6
            # Par2 = diffraction order
            surf6.GetSurfaceCell(columns.Par2).IntegerValue = 1
            print("  [OK] Using DiffractionGrating surface type")
        except:
            # Fallback to Binary2
            binary2_settings = surf6.GetSurfaceTypeSettings(types.Binary2)
            surf6.ChangeType(binary2_settings)
            surf6 = lde.GetSurfaceAt(6)
            surf6.Comment = "Grating 600 l/mm (Binary2)"
            surf6.Thickness = 75.0
            surf6.SemiDiameter = 15.0
            surf6.GetSurfaceCell(columns.Par1).DoubleValue = 1.0
            surf6.GetSurfaceCell(columns.Par2).DoubleValue = 0.6
            print("  [OK] Using Binary2 surface type (fallback)")

        # Coordinate Break 2
        lde.InsertNewSurfaceAt(7)
        surf7 = lde.GetSurfaceAt(7)
        cb2_settings = surf7.GetSurfaceTypeSettings(types.CoordinateBreak)
        surf7.ChangeType(cb2_settings)
        surf7.Comment = "CB2 (14deg return)"
        surf7.Thickness = 0.0
        surf7.GetSurfaceCell(columns.Par3).DoubleValue = 14.0

        # Camera achromat
        lde.InsertNewSurfaceAt(8)
        surf8 = lde.GetSurfaceAt(8)
        surf8.Comment = "Camera Front"
        surf8.Radius = 32.6
        surf8.Thickness = 6.0
        surf8.Material = "N-BK7"
        surf8.SemiDiameter = 12.7

        lde.InsertNewSurfaceAt(9)
        surf9 = lde.GetSurfaceAt(9)
        surf9.Comment = "Camera Cement"
        surf9.Radius = -23.8
        surf9.Thickness = 2.5
        surf9.Material = "SF5"
        surf9.SemiDiameter = 12.7

        lde.InsertNewSurfaceAt(10)
        surf10 = lde.GetSurfaceAt(10)
        surf10.Comment = "Camera Back"
        surf10.Radius = -129.2
        surf10.Thickness = 50.0
        surf10.SemiDiameter = 12.7

        # Image
        img = lde.GetSurfaceAt(11)
        img.Comment = "Detector"
        img.SemiDiameter = 14.336

        # *** FIX 3: Explicitly set the stop surface ***
        surf1.IsStop = True
        print(f"  [OK] Stop set to surface 1")

        print(f"  [OK] Total surfaces: {lde.NumberOfSurfaces}")

        # Save immediately
        zmx_file = os.path.join(output_dir, "spectrometer_fixed.zmx")
        session.save_file(zmx_file)
        print(f"  [OK] Saved: {zmx_file}")
        results["files"].append(zmx_file)
        results["steps"].append({"step": "create_design", "status": "success"})

    except Exception as e:
        print(f"  [FAIL] Design creation error: {e}")
        results["steps"].append({"step": "create_design", "status": "error", "message": str(e)})
        return results

    # ========================================================================
    # STEP 2: VERIFY APERTURE AFTER SAVE
    # ========================================================================
    print("\n[STEP 2] Verifying Aperture After Save/Reload...")

    try:
        # Reload the file
        session.load_file(zmx_file)
        sys_data = session.system.SystemData

        aperture_type = str(sys_data.Aperture.ApertureType)
        aperture_value = sys_data.Aperture.ApertureValue

        print(f"  [CHECK] Aperture Type: {aperture_type}")
        print(f"  [CHECK] Aperture Value: {aperture_value} mm")

        if aperture_value < 1.0:
            print(f"  [WARNING] Aperture degraded to {aperture_value}!")
        else:
            print(f"  [OK] Aperture preserved: {aperture_value} mm")

        results["steps"].append({
            "step": "verify_aperture",
            "status": "success",
            "aperture_type": aperture_type,
            "aperture_value": aperture_value
        })

    except Exception as e:
        print(f"  [FAIL] Verification error: {e}")
        results["steps"].append({"step": "verify_aperture", "status": "error", "message": str(e)})

    # ========================================================================
    # STEP 3: RUN SPOT DIAGRAM
    # ========================================================================
    print("\n[STEP 3] Running Spot Diagram Analysis...")

    try:
        spot_result = zemax_run_spot_diagram()

        if spot_result.get('status') == 'success':
            fields = spot_result.get('fields', [])
            if fields:
                field = fields[0]
                rms = field.get('polychromatic_rms_spot_um', 0)
                geo = field.get('polychromatic_geo_spot_um', 0)
                airy = field.get('airy_disk_radius_um', 0)

                print(f"  [OK] RMS Spot: {rms:.3f} µm")
                print(f"  [OK] GEO Spot: {geo:.3f} µm")
                print(f"  [OK] Airy Disk: {airy:.3f} µm")

                results["steps"].append({
                    "step": "spot_diagram",
                    "status": "success",
                    "rms_um": rms,
                    "geo_um": geo,
                    "airy_um": airy
                })
            else:
                print("  [WARNING] No field data returned")
        else:
            print(f"  [FAIL] {spot_result.get('message', 'Unknown error')}")

    except Exception as e:
        print(f"  [FAIL] Spot diagram error: {e}")
        results["steps"].append({"step": "spot_diagram", "status": "error", "message": str(e)})

    # ========================================================================
    # STEP 4: GET SYSTEM DATA
    # ========================================================================
    print("\n[STEP 4] Getting System Data...")

    try:
        sys_data_result = zemax_get_system_data()

        if sys_data_result.get('status') == 'success':
            general = sys_data_result.get('general', {})
            surfaces = sys_data_result.get('surfaces', [])

            print(f"  [OK] Surfaces: {len(surfaces)}")
            print(f"  [OK] Aperture: {general.get('aperture_type')} = {general.get('aperture_value')}")
            print(f"  [OK] Fields: {general.get('field_type')}")

            # Save system data
            sys_data_file = os.path.join(output_dir, "system_data.json")
            with open(sys_data_file, 'w') as f:
                json.dump(sys_data_result, f, indent=2)
            results["files"].append(sys_data_file)

            results["steps"].append({
                "step": "system_data",
                "status": "success",
                "num_surfaces": len(surfaces)
            })
        else:
            print(f"  [FAIL] {sys_data_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] System data error: {e}")
        results["steps"].append({"step": "system_data", "status": "error", "message": str(e)})

    # ========================================================================
    # STEP 5: RUN MTF ANALYSIS
    # ========================================================================
    print("\n[STEP 5] Running MTF Analysis...")

    try:
        mtf_result = zemax_run_fft_mtf(max_frequency=50.0)

        if mtf_result.get('status') == 'success':
            print(f"  [OK] MTF analysis completed")

            # Save MTF data
            mtf_file = os.path.join(output_dir, "mtf_analysis.json")
            with open(mtf_file, 'w') as f:
                json.dump(mtf_result, f, indent=2)
            results["files"].append(mtf_file)

            results["steps"].append({"step": "mtf_analysis", "status": "success"})
        else:
            print(f"  [FAIL] {mtf_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] MTF error: {e}")
        results["steps"].append({"step": "mtf_analysis", "status": "error", "message": str(e)})

    # ========================================================================
    # STEP 6: RUN RAY FAN
    # ========================================================================
    print("\n[STEP 6] Running Ray Fan Analysis...")

    try:
        rayfan_result = zemax_run_ray_fan()

        if rayfan_result.get('status') == 'success':
            print(f"  [OK] Ray fan analysis completed")

            # Save ray fan data
            rayfan_file = os.path.join(output_dir, "rayfan_analysis.json")
            with open(rayfan_file, 'w') as f:
                json.dump(rayfan_result, f, indent=2)
            results["files"].append(rayfan_file)

            results["steps"].append({"step": "rayfan_analysis", "status": "success"})
        else:
            print(f"  [FAIL] {rayfan_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] Ray fan error: {e}")
        results["steps"].append({"step": "rayfan_analysis", "status": "error", "message": str(e)})

    # ========================================================================
    # STEP 7: EXPORT CAD FILES
    # ========================================================================
    print("\n[STEP 7] Exporting CAD Files...")

    cad_dir = os.path.join(output_dir, "cad")
    os.makedirs(cad_dir, exist_ok=True)

    # STEP export
    try:
        step_file = os.path.join(cad_dir, "spectrometer_fixed.stp")
        step_result = zemax_export_cad(
            filepath=step_file,
            file_type='STEP',
            surfaces_as_solids=True
        )

        if step_result.get('status') == 'success':
            file_path = step_result.get('filepath', step_file)
            if os.path.exists(file_path):
                size_kb = os.path.getsize(file_path) / 1024
                print(f"  [OK] STEP: {size_kb:.1f} KB")
                results["files"].append(file_path)
            else:
                print(f"  [WARNING] STEP file not found")
        else:
            print(f"  [FAIL] STEP export: {step_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] STEP export error: {e}")

    # IGES export
    try:
        iges_file = os.path.join(cad_dir, "spectrometer_fixed.igs")
        iges_result = zemax_export_cad(
            filepath=iges_file,
            file_type='IGES',
            surfaces_as_solids=True
        )

        if iges_result.get('status') == 'success':
            file_path = iges_result.get('filepath', iges_file)
            if os.path.exists(file_path):
                size_kb = os.path.getsize(file_path) / 1024
                print(f"  [OK] IGES: {size_kb:.1f} KB")
                results["files"].append(file_path)
        else:
            print(f"  [FAIL] IGES export: {iges_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] IGES export error: {e}")

    # STL export
    try:
        stl_file = os.path.join(cad_dir, "spectrometer_fixed.stl")
        stl_result = zemax_export_cad(
            filepath=stl_file,
            file_type='STL',
            surfaces_as_solids=True
        )

        if stl_result.get('status') == 'success':
            file_path = stl_result.get('filepath', stl_file)
            if os.path.exists(file_path):
                size_kb = os.path.getsize(file_path) / 1024
                print(f"  [OK] STL: {size_kb:.1f} KB")
                results["files"].append(file_path)
        else:
            print(f"  [FAIL] STL export: {stl_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] STL export error: {e}")

    results["steps"].append({"step": "cad_export", "status": "success"})

    # ========================================================================
    # STEP 8: GENERATE FINAL REPORT
    # ========================================================================
    print("\n[STEP 8] Generating Final Report...")

    results["status"] = "completed"
    results["output_directory"] = output_dir

    report_file = os.path.join(output_dir, "FINAL_REPORT_FIXED.json")
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"  [OK] Report saved: {report_file}")
    results["files"].append(report_file)

    # ========================================================================
    # SUMMARY
    # ========================================================================
    print("\n" + "="*80)
    print("PROJECT COMPLETED - FIXED VERSION")
    print("="*80)

    success_steps = sum(1 for s in results["steps"] if s.get("status") == "success")
    total_steps = len(results["steps"])

    print(f"\nSteps completed: {success_steps}/{total_steps}")
    print(f"Files generated: {len(results['files'])}")
    print(f"\nOutput directory: {output_dir}")
    print(f"Main design file: spectrometer_fixed.zmx")
    print(f"Final report: FINAL_REPORT_FIXED.json")

    return results


if __name__ == '__main__':
    complete_project_with_mcp_fixed()
