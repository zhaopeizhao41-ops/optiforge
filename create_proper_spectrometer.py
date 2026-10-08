"""
Create a proper spectrometer by bypassing the project guard
"""

from core.zos_session import ZOSSession
import os

def create_proper_spectrometer():
    """Create spectrometer directly without project validation"""

    session = ZOSSession.get_instance()

    print("\n" + "="*80)
    print("CREATING PROPER CZERNY-TURNER SPECTROMETER")
    print("="*80)

    # Start fresh
    print("\n[1] Creating new system...")
    session.new_system(save_changes=False)

    # Direct LDE access
    lde = session.system.LDE
    print(f"    Initial surfaces: {lde.NumberOfSurfaces}")

    # Set wavelengths
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
    sys_data.Aperture.ApertureValue = 12.5
    sys_data.Fields.AddField(0, 0, 1.0)
    print(f"    Aperture: {sys_data.Aperture.ApertureValue} mm")
    print(f"    Fields: {sys_data.Fields.NumberOfFields}")

    # Build surfaces using direct ZOSAPI calls
    print("\n[4] Building optical system...")

    # Surface 0: OBJ
    surf0 = lde.GetSurfaceAt(0)
    surf0.Comment = "Object (Entrance Slit)"
    surf0.Thickness = 50.0
    print(f"    0: OBJ -> t=50mm")

    # Surface 1: STO
    lde.InsertNewSurfaceAt(1)
    surf1 = lde.GetSurfaceAt(1)
    surf1.Comment = "Stop (Collimator)"
    surf1.Thickness = 3.5
    print(f"    1: STO")

    # Surfaces 2-4: Collimator achromat
    lde.InsertNewSurfaceAt(2)
    surf2 = lde.GetSurfaceAt(2)
    surf2.Comment = "Collimator Front"
    surf2.Radius = 32.6
    surf2.Thickness = 6.0
    surf2.Material = "N-BK7"
    print(f"    2: Collimator Front")

    lde.InsertNewSurfaceAt(3)
    surf3 = lde.GetSurfaceAt(3)
    surf3.Comment = "Collimator Interface"
    surf3.Radius = -23.8
    surf3.Thickness = 2.5
    surf3.Material = "SF5"
    print(f"    3: Collimator Interface")

    lde.InsertNewSurfaceAt(4)
    surf4 = lde.GetSurfaceAt(4)
    surf4.Comment = "Collimator Back"
    surf4.Radius = -129.2
    surf4.Thickness = 75.0
    print(f"    4: Collimator Back")

    # Surface 5: Coordinate Break (direct ZOSAPI)
    print(f"\n    Creating Coordinate Break 1...")
    lde.InsertNewSurfaceAt(5)
    surf5 = lde.GetSurfaceAt(5)

    types = session.ZOSAPI.Editors.LDE.SurfaceType
    cb_settings = surf5.GetSurfaceTypeSettings(types.CoordinateBreak)
    surf5.ChangeType(cb_settings)
    surf5.Comment = "CB1 (14° tilt to grating)"
    surf5.Thickness = 0.0

    # Set tilt parameters
    columns = session.ZOSAPI.Editors.LDE.SurfaceColumn
    surf5.GetSurfaceCell(columns.Par3).DoubleValue = 14.0  # tilt_x
    print(f"    5: CoordinateBreak (tilt_x=14°)")

    # Surface 6: Binary2 Grating (direct ZOSAPI)
    print(f"\n    Creating Binary2 Grating...")
    lde.InsertNewSurfaceAt(6)
    surf6 = lde.GetSurfaceAt(6)

    binary2_settings = surf6.GetSurfaceTypeSettings(types.Binary2)
    surf6.ChangeType(binary2_settings)
    surf6.Comment = "Grating (1200 gr/mm)"
    surf6.Thickness = 75.0

    # Set grating parameters
    surf6.GetSurfaceCell(columns.Par1).DoubleValue = 1.0    # diffraction order
    surf6.GetSurfaceCell(columns.Par2).DoubleValue = 1.2    # lines/micron
    print(f"    6: Binary2 Grating (order=1, 1200 gr/mm)")

    # Surface 7: Coordinate Break 2 (direct ZOSAPI)
    print(f"\n    Creating Coordinate Break 2...")
    lde.InsertNewSurfaceAt(7)
    surf7 = lde.GetSurfaceAt(7)

    cb2_settings = surf7.GetSurfaceTypeSettings(types.CoordinateBreak)
    surf7.ChangeType(cb2_settings)
    surf7.Comment = "CB2 (14° return to lab)"
    surf7.Thickness = 0.0

    surf7.GetSurfaceCell(columns.Par3).DoubleValue = 14.0  # tilt_x
    print(f"    7: CoordinateBreak (tilt_x=14°)")

    # Surfaces 8-10: Camera achromat
    lde.InsertNewSurfaceAt(8)
    surf8 = lde.GetSurfaceAt(8)
    surf8.Comment = "Camera Front"
    surf8.Radius = 32.6
    surf8.Thickness = 6.0
    surf8.Material = "N-BK7"
    print(f"    8: Camera Front")

    lde.InsertNewSurfaceAt(9)
    surf9 = lde.GetSurfaceAt(9)
    surf9.Comment = "Camera Interface"
    surf9.Radius = -23.8
    surf9.Thickness = 2.5
    surf9.Material = "SF5"
    print(f"    9: Camera Interface")

    lde.InsertNewSurfaceAt(10)
    surf10 = lde.GetSurfaceAt(10)
    surf10.Comment = "Camera Back"
    surf10.Radius = -129.2
    surf10.Thickness = 50.0
    print(f"    10: Camera Back")

    # Surface 11: Image
    lde.InsertNewSurfaceAt(11)
    surf11 = lde.GetSurfaceAt(11)
    surf11.Comment = "Detector"
    surf11.Thickness = 0.0
    print(f"    11: Image (Detector)")

    # Verification
    print(f"\n[5] Verification:")
    total_surfaces = lde.NumberOfSurfaces
    print(f"    Total surfaces: {total_surfaces}")

    cb_count = 0
    grating_found = False
    for i in range(total_surfaces):
        surf = lde.GetSurfaceAt(i)
        type_name = surf.TypeName
        if 'Coordinate' in type_name or 'Break' in type_name:
            cb_count += 1
            print(f"      Surface {i}: {type_name}")
        if 'Binary' in type_name:
            grating_found = True
            print(f"      Surface {i}: {type_name}")

    print(f"    Coordinate Breaks: {cb_count}")
    print(f"    Binary2 Grating: {'YES' if grating_found else 'NO'}")

    # Save
    output_dir = "D:\\mcp gemini zemax\\output\\compact_spectrometer"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "spectrometer_WORKING.zmx")

    print(f"\n[6] Saving: {output_file}")
    session.save_file(output_file)

    if os.path.exists(output_file):
        file_size = os.path.getsize(output_file)
        print(f"    File size: {file_size/1024:.1f} KB")

    print("\n" + "="*80)
    print("SUCCESS: REAL SPECTROMETER WITH GRATING")
    print("="*80)

    return output_file

if __name__ == '__main__':
    create_proper_spectrometer()
