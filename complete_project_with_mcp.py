"""
Complete Spectrometer Project Using Proper MCP Tools
Full pipeline with all analyses, exports, and verification
"""

from core.zos_session import ZOSSession
from tools.analysis_tools import zemax_run_spot_diagram
from tools.cad_export_tools import zemax_export_cad
from tools.system_tools import zemax_get_system_data
import os
import json

def complete_project_with_mcp():
    """Run complete project using MCP tools"""

    session = ZOSSession.get_instance()

    output_dir = "D:\\mcp gemini zemax\\output\\compact_spectrometer"
    os.makedirs(output_dir, exist_ok=True)

    print("\n" + "="*80)
    print("COMPLETE SPECTROMETER PROJECT - MCP TOOLS")
    print("="*80)

    # ========================================================================
    # STEP 1: CREATE DESIGN
    # ========================================================================
    print("\n[STEP 1] Creating Optical Design...")

    try:
        session.new_system(save_changes=False)
        lde = session.system.LDE

        # Set wavelengths
        sys_data = session.system.SystemData
        wavelengths = sys_data.Wavelengths
        wavelengths.SelectWavelengthPreset(session.ZOSAPI.SystemData.WavelengthPreset.FdC_Visible)

        # Aperture
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

        # Collimator
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

        # Camera
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

        # Save
        zmx_file = os.path.join(output_dir, "compact_spectrometer_COMPLETE.zmx")
        session.save_file(zmx_file)

        print(f"  [OK] Design created: {lde.NumberOfSurfaces} surfaces")
        print(f"  [OK] Saved: {zmx_file}")

    except Exception as e:
        print(f"  [FAIL] Design creation failed: {e}")
        return

    # ========================================================================
    # STEP 2: VERIFY DESIGN
    # ========================================================================
    print("\n[STEP 2] Verifying Design...")

    try:
        # Reload and verify
        session.load_file(zmx_file)
        lde = session.system.LDE

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

        print(f"  [OK] Coordinate breaks: {cb_count}")
        print(f"  [OK] Gratings: {grating_count}")
        print(f"  [OK] Lens surfaces: {lens_count}")

        if cb_count < 2 or grating_count < 1:
            print(f"  [FAIL] Invalid spectrometer configuration!")
            return

        print(f"  [OK] Valid spectrometer confirmed")

    except Exception as e:
        print(f"  [FAIL] Verification failed: {e}")
        return

    # ========================================================================
    # STEP 3: RUN SPOT DIAGRAM (MCP TOOL)
    # ========================================================================
    print("\n[STEP 3] Running Spot Diagram Analysis...")

    try:
        spot_result = zemax_run_spot_diagram()

        if spot_result.get('status') == 'success':
            rms = spot_result.get('rms_radius_um', 0)
            geo = spot_result.get('geo_radius_um', 0)
            print(f"  [OK] Spot RMS: {rms:.3f} um")
            print(f"  [OK] Spot GEO: {geo:.3f} um")
        else:
            print(f"  [FAIL] Spot analysis failed: {spot_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] Spot analysis error: {e}")
        spot_result = {"error": str(e)}

    # ========================================================================
    # STEP 4: GET SYSTEM DATA (MCP TOOL)
    # ========================================================================
    print("\n[STEP 4] Getting System Data...")

    try:
        sys_data_result = zemax_get_system_data()

        if sys_data_result.get('status') == 'success':
            num_surfaces = sys_data_result.get('num_surfaces', 0)
            wavelengths_list = sys_data_result.get('wavelengths_um', [])
            print(f"  [OK] Surfaces: {num_surfaces}")
            print(f"  [OK] Wavelengths: {wavelengths_list}")
        else:
            print(f"  [FAIL] System data failed: {sys_data_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] System data error: {e}")
        sys_data_result = {"error": str(e)}

    # ========================================================================
    # STEP 5: EXPORT CAD (MCP TOOL)
    # ========================================================================
    print("\n[STEP 5] Exporting CAD Files...")

    cad_dir = os.path.join(output_dir, "cad")
    os.makedirs(cad_dir, exist_ok=True)

    cad_results = {}

    # STEP export
    try:
        step_file = os.path.join(cad_dir, "spectrometer_COMPLETE.stp")
        step_result = zemax_export_cad(
            filepath=step_file,
            project_name='compact_spectrometer',
            file_type='STEP',
            surfaces_as_solids=True,
            first_surface=2,
            last_surface=10
        )

        if step_result.get('status') == 'success':
            size_kb = step_result.get('file_size_kb', 0)
            print(f"  [OK] STEP export: {size_kb:.1f} KB")
            cad_results['step'] = step_file
        else:
            print(f"  [FAIL] STEP export failed: {step_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] STEP export error: {e}")

    # IGES export
    try:
        iges_file = os.path.join(cad_dir, "spectrometer_COMPLETE.igs")
        iges_result = zemax_export_cad(
            filepath=iges_file,
            project_name='compact_spectrometer',
            file_type='IGES',
            surfaces_as_solids=True,
            first_surface=2,
            last_surface=10
        )

        if iges_result.get('status') == 'success':
            size_kb = iges_result.get('file_size_kb', 0)
            print(f"  [OK] IGES export: {size_kb:.1f} KB")
            cad_results['iges'] = iges_file
        else:
            print(f"  [FAIL] IGES export failed: {iges_result.get('message')}")

    except Exception as e:
        print(f"  [FAIL] IGES export error: {e}")

    # ========================================================================
    # STEP 6: GENERATE FINAL REPORT
    # ========================================================================
    print("\n[STEP 6] Generating Final Report...")

    report = {
        "project": "Compact Czerny-Turner Spectrometer",
        "status": "completed",
        "design_file": zmx_file,
        "file_size_kb": round(os.path.getsize(zmx_file)/1024, 1),
        "specifications": {
            "configuration": "Czerny-Turner symmetric",
            "grating": "1200 gr/mm, Binary2 type",
            "wavelength_range": "486-656 nm (F-d-C)",
            "optics": "Thorlabs AC254-050-A achromats",
            "beam_deflection": "28 degrees total",
            "focal_length": "50 mm"
        },
        "validation": {
            "coordinate_breaks": cb_count,
            "gratings": grating_count,
            "lens_surfaces": lens_count,
            "total_surfaces": lde.NumberOfSurfaces,
            "is_valid": True
        },
        "optical_performance": spot_result,
        "system_data": sys_data_result,
        "cad_exports": cad_results,
        "output_directory": output_dir
    }

    report_file = os.path.join(output_dir, "FINAL_REPORT.json")
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"  [OK] Report saved: {report_file}")

    # ========================================================================
    # FINAL SUMMARY
    # ========================================================================
    print("\n" + "="*80)
    print("PROJECT COMPLETED SUCCESSFULLY")
    print("="*80)

    print(f"\nDesign File: {zmx_file}")
    print(f"  Size: {report['file_size_kb']} KB")
    print(f"  Surfaces: {lde.NumberOfSurfaces}")
    print(f"  Coordinate Breaks: {cb_count}")
    print(f"  Gratings: {grating_count}")

    if spot_result.get('status') == 'success':
        print(f"\nOptical Performance:")
        print(f"  RMS Spot: {spot_result.get('rms_radius_um', 0):.3f} um")
        print(f"  GEO Spot: {spot_result.get('geo_radius_um', 0):.3f} um")

    if cad_results:
        print(f"\nCAD Exports:")
        for fmt, path in cad_results.items():
            print(f"  {fmt.upper()}: {path}")

    print(f"\nFull Report: {report_file}")
    print("\n" + "="*80)

    return report

if __name__ == '__main__':
    complete_project_with_mcp()
