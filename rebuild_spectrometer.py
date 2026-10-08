"""
Rebuild spectrometer with proper session and verification
"""

from core.zos_session import ZOSSession
from tools.surface_tools import zemax_set_surface_type, zemax_set_surface_params
import os

def rebuild_spectrometer():
    """Rebuild and verify the spectrometer design"""

    session = ZOSSession.get_instance()

    print("\n" + "="*80)
    print("REBUILDING CZERNY-TURNER SPECTROMETER")
    print("="*80)

    # Start fresh
    print("\n[1] Creating new system...")
    session.new_system(save_changes=False)

    # Direct LDE access
    lde = session.system.LDE
    print(f"    Initial surfaces: {lde.NumberOfSurfaces}")

    # Set wavelengths (400-700 nm, 3 wavelengths)
    print("\n[2] Setting wavelengths...")
    sys_data = session.system.SystemData
    wavelengths = sys_data.Wavelengths
    wavelengths.SelectWavelengthPreset(session.ZOSAPI.SystemData.WavelengthPreset.FdC_Visible)
    num_waves = wavelengths.NumberOfWavelengths
    print(f"    Wavelengths: {num_waves}")
    for i in range(1, num_waves + 1):
        w = wavelengths.GetWavelength(i)
        print(f"      {i}: {w.Wavelength:.4f} μm")

    # Set aperture and field
    print("\n[3] System aperture and field...")
    sys_data.Aperture.ApertureType = session.ZOSAPI.SystemData.ZemaxApertureType.FloatByStopSize
    sys_data.Aperture.ApertureValue = 12.5  # 25mm diameter entrance pupil
    sys_data.Fields.AddField(0, 0, 1.0)  # On-axis
    print(f"    Aperture: {sys_data.Aperture.ApertureValue} mm")
    print(f"    Fields: {sys_data.Fields.NumberOfFields}")

    # Build surfaces
    print("\n[4] Building optical system...")

    # Surface 0: OBJ (already exists)
    surf0 = lde.GetSurfaceAt(0)
    surf0.Comment = "Object (Entrance Slit)"
    surf0.Thickness = 50.0  # 50mm to collimator
    print(f"    0: OBJ (Entrance Slit) -> t=50mm")

    # Surface 1: STO (Stop at collimator)
    lde.InsertNewSurfaceAt(1)
    surf1 = lde.GetSurfaceAt(1)
    surf1.Comment = "Stop (Collimator)"
    surf1.Thickness = 3.5
    print(f"    1: STO (Collimator aperture)")

    # Surface 2-4: Collimator achromat (AC254-050-A)
    lde.InsertNewSurfaceAt(2)
    surf2 = lde.GetSurfaceAt(2)
    surf2.Comment = "Collimator Front"
    surf2.Radius = 32.6
    surf2.Thickness = 6.0
    surf2.Material = "N-BK7"
    print(f"    2: Collimator Front (R=32.6, t=6.0, N-BK7)")

    lde.InsertNewSurfaceAt(3)
    surf3 = lde.GetSurfaceAt(3)
    surf3.Comment = "Collimator Interface"
    surf3.Radius = -23.8
    surf3.Thickness = 2.5
    surf3.Material = "SF5"
    print(f"    3: Collimator Interface (R=-23.8, t=2.5, SF5)")

    lde.InsertNewSurfaceAt(4)
    surf4 = lde.GetSurfaceAt(4)
    surf4.Comment = "Collimator Back"
    surf4.Radius = -129.2
    surf4.Thickness = 75.0  # 75mm to grating
    print(f"    4: Collimator Back (R=-129.2, t=75mm)")

    # Surface 5: Coordinate Break (tilt toward grating)
    print(f"\n    Inserting Coordinate Break 1...")
    lde.InsertNewSurfaceAt(5)
    cb1_result = zemax_set_surface_type(5, 'CoordinateBreak')
    if cb1_result.get('status') == 'success':
        zemax_set_surface_params(5, {
            'tilt_x': 14.0,
            'decenter_x': 0.0,
            'decenter_y': 0.0,
            'tilt_y': 0.0,
            'tilt_z': 0.0,
            'order': 0
        })
        surf5 = lde.GetSurfaceAt(5)
        surf5.Comment = "CB1 (14° tilt to grating)"
        surf5.Thickness = 0.0
        print(f"    5: CoordinateBreak (tilt_x=14°) SUCCESS")
    else:
        print(f"    5: CoordinateBreak FAILED: {cb1_result.get('message')}")

    # Surface 6: Binary2 Grating
    print(f"\n    Inserting Binary2 Grating...")
    lde.InsertNewSurfaceAt(6)
    grating_result = zemax_set_surface_type(6, 'Binary2')
    if grating_result.get('status') == 'success':
        zemax_set_surface_params(6, {
            'par1': 1,      # Diffraction order +1
            'par2': 1.2     # Lines per micron (1200 gr/mm)
        })
        surf6 = lde.GetSurfaceAt(6)
        surf6.Comment = "Grating (1200 gr/mm)"
        surf6.Thickness = 75.0  # 75mm to camera
        print(f"    6: Binary2 Grating (order=1, 1200 gr/mm) SUCCESS")
    else:
        print(f"    6: Binary2 Grating FAILED: {grating_result.get('message')}")

    # Surface 7: Coordinate Break (return to lab frame)
    print(f"\n    Inserting Coordinate Break 2...")
    lde.InsertNewSurfaceAt(7)
    cb2_result = zemax_set_surface_type(7, 'CoordinateBreak')
    if cb2_result.get('status') == 'success':
        zemax_set_surface_params(7, {
            'tilt_x': 14.0,
            'decenter_x': 0.0,
            'decenter_y': 0.0,
            'tilt_y': 0.0,
            'tilt_z': 0.0,
            'order': 0
        })
        surf7 = lde.GetSurfaceAt(7)
        surf7.Comment = "CB2 (14° return to lab)"
        surf7.Thickness = 0.0
        print(f"    7: CoordinateBreak (tilt_x=14°) SUCCESS")
    else:
        print(f"    7: CoordinateBreak FAILED: {cb2_result.get('message')}")

    # Surface 8-10: Camera achromat (AC254-050-A)
    lde.InsertNewSurfaceAt(8)
    surf8 = lde.GetSurfaceAt(8)
    surf8.Comment = "Camera Front"
    surf8.Radius = 32.6
    surf8.Thickness = 6.0
    surf8.Material = "N-BK7"
    print(f"    8: Camera Front (R=32.6, t=6.0, N-BK7)")

    lde.InsertNewSurfaceAt(9)
    surf9 = lde.GetSurfaceAt(9)
    surf9.Comment = "Camera Interface"
    surf9.Radius = -23.8
    surf9.Thickness = 2.5
    surf9.Material = "SF5"
    print(f"    9: Camera Interface (R=-23.8, t=2.5, SF5)")

    lde.InsertNewSurfaceAt(10)
    surf10 = lde.GetSurfaceAt(10)
    surf10.Comment = "Camera Back"
    surf10.Radius = -129.2
    surf10.Thickness = 50.0  # 50mm to detector
    print(f"    10: Camera Back (R=-129.2, t=50mm)")

    # Surface 11: Image plane
    lde.InsertNewSurfaceAt(11)
    surf11 = lde.GetSurfaceAt(11)
    surf11.Comment = "Detector"
    surf11.Thickness = 0.0
    print(f"    11: Image (Detector)")

    # Verification
    print(f"\n[5] Verification:")
    total_surfaces = lde.NumberOfSurfaces
    print(f"    Total surfaces: {total_surfaces}")

    # Count surface types
    cb_count = 0
    grating_found = False
    for i in range(total_surfaces):
        surf = lde.GetSurfaceAt(i)
        type_name = surf.TypeName
        if 'Coordinate' in type_name or 'Break' in type_name:
            cb_count += 1
        if 'Binary' in type_name:
            grating_found = True

    print(f"    Coordinate Breaks: {cb_count}")
    print(f"    Binary2 Grating: {'YES' if grating_found else 'NO'}")

    # Save file
    output_dir = "D:\\mcp gemini zemax\\output\\compact_spectrometer"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "czerny_turner_VERIFIED.zmx")

    print(f"\n[6] Saving to: {output_file}")
    session.save_file(output_file)

    if os.path.exists(output_file):
        file_size = os.path.getsize(output_file)
        print(f"    File size: {file_size} bytes ({file_size/1024:.1f} KB)")

    print("\n" + "="*80)
    print("SPECTROMETER BUILD COMPLETE")
    print("="*80)
    print(f"\nDesign saved: {output_file}")
    print(f"  - {total_surfaces} surfaces")
    print(f"  - {cb_count} coordinate breaks")
    print(f"  - Binary2 grating: {grating_found}")
    print(f"  - {num_waves} wavelengths")

    # Don't close session - keep it alive for next operation
    return output_file

if __name__ == '__main__':
    rebuild_spectrometer()
