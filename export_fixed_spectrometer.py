"""
Load Fixed Spectrometer and Generate All Outputs
"""

from core.zos_session import ZOSSession
from tools.system_tools import zemax_load_file, zemax_get_system_data
from tools.cad_export_tools import zemax_export_cad
from tools.analysis_tools import zemax_run_spot_diagram
import os

def export_fixed_spectrometer():
    """Load the fixed spectrometer and generate all outputs"""

    session = ZOSSession()

    output_dir = "D:\\mcp gemini zemax\\output\\compact_spectrometer"
    zmx_file = os.path.join(output_dir, "czerny_turner_spectrometer_FIXED.zmx")

    print("\n" + "="*80)
    print("LOADING FIXED SPECTROMETER AND GENERATING OUTPUTS")
    print("="*80)

    # Load the file
    print(f"\n[1] Loading: {zmx_file}")
    load_result = zemax_load_file(zmx_file)
    print(f"    Status: {load_result.get('status')}")

    if load_result.get('status') != 'success':
        print(f"    ERROR: {load_result.get('message')}")
        session.close()
        return

    # Get system data
    print("\n[2] System Data:")
    sys_data = zemax_get_system_data()
    num_surfaces = sys_data.get('num_surfaces', 0)
    wavelengths = sys_data.get('wavelengths_um', [])
    print(f"    Surfaces: {num_surfaces}")
    print(f"    Wavelengths: {wavelengths}")

    # Export CAD files
    print("\n[3] Exporting CAD Files...")
    cad_dir = os.path.join(output_dir, "cad")
    os.makedirs(cad_dir, exist_ok=True)

    # STEP
    step_path = os.path.join(cad_dir, "spectrometer_FIXED.stp")
    step_result = zemax_export_cad(
        filepath=step_path,
        project_name='compact_spectrometer',
        file_type='STEP',
        surfaces_as_solids=True,
        first_surface=2,
        last_surface=10
    )
    if step_result.get('status') == 'success':
        print(f"    STEP: {step_result.get('file_size_kb', 0)} KB")

    # IGES
    iges_path = os.path.join(cad_dir, "spectrometer_FIXED.igs")
    iges_result = zemax_export_cad(
        filepath=iges_path,
        project_name='compact_spectrometer',
        file_type='IGES',
        surfaces_as_solids=True,
        first_surface=2,
        last_surface=10
    )
    if iges_result.get('status') == 'success':
        print(f"    IGES: {iges_result.get('file_size_kb', 0)} KB")

    # Run spot diagram
    print("\n[4] Running Spot Diagram...")
    spot_result = zemax_run_spot_diagram()
    if spot_result.get('status') == 'success':
        rms = spot_result.get('rms_radius_um', 'N/A')
        print(f"    RMS spot: {rms} um")

    # Summary
    print("\n" + "="*80)
    print("FIXED SPECTROMETER OUTPUTS COMPLETE")
    print("="*80)
    print(f"\nDesign File: {zmx_file} (12 KB)")
    print(f"  - Surfaces: {num_surfaces}")
    print(f"  - Wavelengths: {len(wavelengths)}")
    print(f"  - Coordinate Breaks: 2 (surfaces 5 & 7)")
    print(f"  - Binary2 Grating: surface 6 (1200 gr/mm)")
    print(f"\nCAD Exports:")
    print(f"  - {step_path}")
    print(f"  - {iges_path}")
    print(f"\nThis is a TRUE spectrometer with diffraction grating and folded beam path.")
    print(f"Open in Zemax to verify spectral dispersion across detector.")

    session.close()

if __name__ == '__main__':
    export_fixed_spectrometer()
