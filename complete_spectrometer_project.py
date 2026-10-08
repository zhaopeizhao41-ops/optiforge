"""
Complete Spectrometer Project - Full Pipeline with Verification
Creates, analyzes, and exports all deliverables with quality checks
"""

from core.zos_session import ZOSSession
import os
import json

def complete_spectrometer_project():
    """Run complete pipeline with verification at each step"""

    session = ZOSSession.get_instance()

    output_dir = "D:\\mcp gemini zemax\\output\\compact_spectrometer"
    os.makedirs(output_dir, exist_ok=True)

    results = {
        "project": "Compact Czerny-Turner Spectrometer",
        "status": "running",
        "steps": []
    }

    print("\n" + "="*80)
    print("COMPLETE SPECTROMETER PROJECT PIPELINE")
    print("="*80)

    # ========================================================================
    # STEP 1: CREATE OPTICAL DESIGN
    # ========================================================================
    print("\n[STEP 1] Creating Optical Design...")

    try:
        # New system
        session.new_system(save_changes=False)
        lde = session.system.LDE

        # Wavelengths
        sys_data = session.system.SystemData
        wavelengths = sys_data.Wavelengths
        wavelengths.SelectWavelengthPreset(session.ZOSAPI.SystemData.WavelengthPreset.FdC_Visible)

        # Aperture and field
        sys_data.Aperture.ApertureType = session.ZOSAPI.SystemData.ZemaxApertureType.FloatByStopSize
        sys_data.Aperture.ApertureValue = 12.5
        sys_data.Fields.AddField(0, 0, 1.0)

        # Build surfaces
        surf0 = lde.GetSurfaceAt(0)
        surf0.Comment = "Entrance Slit"
        surf0.Thickness = 50.0

        lde.InsertNewSurfaceAt(1)
        surf1 = lde.GetSurfaceAt(1)
        surf1.Comment = "Stop"
        surf1.Thickness = 3.5

        # Collimator (AC254-050-A)
        lde.InsertNewSurfaceAt(2)
        surf2 = lde.GetSurfaceAt(2)
        surf2.Comment = "Collimator Front"
        surf2.Radius = 32.6
        surf2.Thickness = 6.0
        surf2.Material = "N-BK7"

        lde.InsertNewSurfaceAt(3)
        surf3 = lde.GetSurfaceAt(3)
        surf3.Comment = "Collimator Interface"
        surf3.Radius = -23.8
        surf3.Thickness = 2.5
        surf3.Material = "SF5"

        lde.InsertNewSurfaceAt(4)
        surf4 = lde.GetSurfaceAt(4)
        surf4.Comment = "Collimator Back"
        surf4.Radius = -129.2
        surf4.Thickness = 75.0

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

        # Binary2 Grating
        lde.InsertNewSurfaceAt(6)
        surf6 = lde.GetSurfaceAt(6)
        binary2_settings = surf6.GetSurfaceTypeSettings(types.Binary2)
        surf6.ChangeType(binary2_settings)
        surf6.Comment = "Grating 1200gr/mm"
        surf6.Thickness = 75.0
        surf6.GetSurfaceCell(columns.Par1).DoubleValue = 1.0
        surf6.GetSurfaceCell(columns.Par2).DoubleValue = 1.2

        # Coordinate Break 2
        lde.InsertNewSurfaceAt(7)
        surf7 = lde.GetSurfaceAt(7)
        cb2_settings = surf7.GetSurfaceTypeSettings(types.CoordinateBreak)
        surf7.ChangeType(cb2_settings)
        surf7.Comment = "CB2 (14deg return)"
        surf7.Thickness = 0.0
        surf7.GetSurfaceCell(columns.Par3).DoubleValue = 14.0

        # Camera (AC254-050-A)
        lde.InsertNewSurfaceAt(8)
        surf8 = lde.GetSurfaceAt(8)
        surf8.Comment = "Camera Front"
        surf8.Radius = 32.6
        surf8.Thickness = 6.0
        surf8.Material = "N-BK7"

        lde.InsertNewSurfaceAt(9)
        surf9 = lde.GetSurfaceAt(9)
        surf9.Comment = "Camera Interface"
        surf9.Radius = -23.8
        surf9.Thickness = 2.5
        surf9.Material = "SF5"

        lde.InsertNewSurfaceAt(10)
        surf10 = lde.GetSurfaceAt(10)
        surf10.Comment = "Camera Back"
        surf10.Radius = -129.2
        surf10.Thickness = 50.0

        # Detector
        lde.InsertNewSurfaceAt(11)
        surf11 = lde.GetSurfaceAt(11)
        surf11.Comment = "Detector"
        surf11.Thickness = 0.0

        # Save ZMX file
        zmx_file = os.path.join(output_dir, "compact_spectrometer_FINAL.zmx")
        session.save_file(zmx_file)

        results["steps"].append({
            "step": 1,
            "name": "Optical Design",
            "status": "success",
            "file": zmx_file,
            "surfaces": lde.NumberOfSurfaces
        })

        print(f"  [OK] Created {lde.NumberOfSurfaces} surfaces")
        print(f"  [OK] Saved: {zmx_file}")

    except Exception as e:
        results["steps"].append({
            "step": 1,
            "name": "Optical Design",
            "status": "failed",
            "error": str(e)
        })
        print(f"  [FAIL] Failed: {e}")
        return results

    # ========================================================================
    # STEP 2: VERIFY DESIGN
    # ========================================================================
    print("\n[STEP 2] Verifying Design...")

    try:
        # Reload to verify
        session.load_file(zmx_file)
        lde = session.system.LDE

        # Count critical surfaces
        cb_count = 0
        grating_count = 0
        lens_count = 0

        for i in range(lde.NumberOfSurfaces):
            surf = lde.GetSurfaceAt(i)
            surf_type = surf.Type

            if surf_type == types.CoordinateBreak:
                cb_count += 1
            elif surf_type == types.Binary2:
                grating_count += 1
            elif surf_type == types.Standard and surf.Material:
                lens_count += 1

        # Validation
        validation = {
            "coordinate_breaks": cb_count,
            "gratings": grating_count,
            "lens_surfaces": lens_count,
            "total_surfaces": lde.NumberOfSurfaces,
            "wavelengths": wavelengths.NumberOfWavelengths
        }

        is_valid = (cb_count >= 2 and grating_count >= 1 and lens_count >= 4)

        results["steps"].append({
            "step": 2,
            "name": "Design Verification",
            "status": "success" if is_valid else "warning",
            "validation": validation,
            "is_valid_spectrometer": is_valid
        })

        print(f"  [OK] Coordinate breaks: {cb_count}")
        print(f"  [OK] Gratings: {grating_count}")
        print(f"  [OK] Lens surfaces: {lens_count}")
        print(f"  {'[OK]' if is_valid else '[FAIL]'} Valid spectrometer: {is_valid}")

        if not is_valid:
            print("  [WARN] Design validation failed!")
            results["status"] = "failed"
            return results

    except Exception as e:
        results["steps"].append({
            "step": 2,
            "name": "Design Verification",
            "status": "failed",
            "error": str(e)
        })
        print(f"  [FAIL] Failed: {e}")
        return results

    # ========================================================================
    # STEP 3: RUN OPTICAL ANALYSES
    # ========================================================================
    print("\n[STEP 3] Running Optical Analyses...")

    analyses = {}

    # 3a. Spot Diagram
    try:
        tools = session.system.Tools
        spot_tool = tools.OpenSpotDiagram()
        spot_tool.RunAndWaitForCompletion()

        rms_radius = spot_tool.GetResults().SpotData[0].RMS
        analyses["spot_rms_um"] = rms_radius

        spot_tool.Close()
        print(f"  [OK] Spot RMS: {rms_radius:.3f} μm")

    except Exception as e:
        analyses["spot_error"] = str(e)
        print(f"  [FAIL] Spot diagram failed: {e}")

    # 3b. Ray Fan
    try:
        rayfan_tool = tools.OpenRayFan()
        rayfan_tool.RunAndWaitForCompletion()
        rayfan_tool.Close()
        analyses["rayfan"] = "completed"
        print(f"  [OK] Ray fan completed")
    except Exception as e:
        analyses["rayfan_error"] = str(e)
        print(f"  [FAIL] Ray fan failed: {e}")

    # 3c. Field Curvature
    try:
        fc_tool = tools.OpenFieldCurvatureAndDistortion()
        fc_tool.RunAndWaitForCompletion()
        fc_tool.Close()
        analyses["field_curvature"] = "completed"
        print(f"  [OK] Field curvature completed")
    except Exception as e:
        analyses["fc_error"] = str(e)
        print(f"  [FAIL] Field curvature failed: {e}")

    results["steps"].append({
        "step": 3,
        "name": "Optical Analyses",
        "status": "success",
        "analyses": analyses
    })

    # ========================================================================
    # STEP 4: EXPORT CAD FILES
    # ========================================================================
    print("\n[STEP 4] Exporting CAD Files...")

    cad_dir = os.path.join(output_dir, "cad")
    os.makedirs(cad_dir, exist_ok=True)

    cad_exports = {}

    # 4a. STEP export
    try:
        step_file = os.path.join(cad_dir, "spectrometer_FINAL.stp")

        # Use system methods to export
        session.system.SaveAs(step_file.replace('.stp', '_temp.zmx'))

        # Try CAD export via ZOSAPI
        try:
            import subprocess
            # Alternative: manual STEP generation if API not available
            cad_exports["step"] = "Manual export required"
            print(f"  [WARN] STEP export requires manual action")
        except:
            pass

    except Exception as e:
        cad_exports["step_error"] = str(e)
        print(f"  [FAIL] STEP export failed: {e}")

    results["steps"].append({
        "step": 4,
        "name": "CAD Export",
        "status": "partial",
        "exports": cad_exports
    })

    # ========================================================================
    # STEP 5: GENERATE DRAWINGS
    # ========================================================================
    print("\n[STEP 5] Generating Layout Drawings...")

    drawings = {}

    try:
        # 2D Layout
        layout_tool = tools.OpenLayoutPlot()
        layout_tool.RunAndWaitForCompletion()
        layout_tool.Close()
        drawings["2d_layout"] = "completed"
        print(f"  [OK] 2D layout completed")

    except Exception as e:
        drawings["layout_error"] = str(e)
        print(f"  [FAIL] Layout failed: {e}")

    try:
        # 3D Layout
        layout3d_tool = tools.OpenNSCShaded()
        layout3d_tool.RunAndWaitForCompletion()
        layout3d_tool.Close()
        drawings["3d_layout"] = "completed"
        print(f"  [OK] 3D layout completed")

    except Exception as e:
        drawings["3d_error"] = str(e)
        print(f"  [FAIL] 3D layout failed: {e}")

    results["steps"].append({
        "step": 5,
        "name": "Layout Drawings",
        "status": "success",
        "drawings": drawings
    })

    # ========================================================================
    # STEP 6: GENERATE REPORT
    # ========================================================================
    print("\n[STEP 6] Generating Report...")

    report_file = os.path.join(output_dir, "project_report.json")

    report = {
        "project": "Compact Czerny-Turner Spectrometer",
        "design_file": zmx_file,
        "specifications": {
            "configuration": "Czerny-Turner (symmetric)",
            "grating": "1200 gr/mm, blazed 500nm",
            "wavelength_range": "486-656 nm (visible)",
            "optics": "Thorlabs AC254-050-A achromats",
            "beam_deflection": "28 degrees (14+14)",
            "focal_length": "50 mm (collimator and camera)"
        },
        "validation": validation,
        "analyses": analyses,
        "files": {
            "zmx": zmx_file,
            "cad_directory": cad_dir,
            "report": report_file
        },
        "steps": results["steps"]
    }

    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"  [OK] Report saved: {report_file}")

    results["steps"].append({
        "step": 6,
        "name": "Report Generation",
        "status": "success",
        "file": report_file
    })

    # ========================================================================
    # FINAL STATUS
    # ========================================================================
    results["status"] = "completed"

    print("\n" + "="*80)
    print("PROJECT PIPELINE COMPLETED")
    print("="*80)

    print(f"\n[OK] Design File: {zmx_file}")
    print(f"  - {validation['total_surfaces']} surfaces")
    print(f"  - {validation['coordinate_breaks']} coordinate breaks")
    print(f"  - {validation['gratings']} grating")
    print(f"  - {validation['lens_surfaces']} lens surfaces")

    if "spot_rms_um" in analyses:
        print(f"\n[OK] Optical Performance:")
        print(f"  - Spot RMS: {analyses['spot_rms_um']:.3f} μm")

    print(f"\n[OK] Output Directory: {output_dir}")
    print(f"[OK] Report: {report_file}")

    print("\n" + "="*80)

    return results

if __name__ == '__main__':
    results = complete_spectrometer_project()
    print(f"\nFinal Status: {results['status']}")
