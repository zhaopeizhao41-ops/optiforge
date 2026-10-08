"""
Direct LDE inspection to diagnose the issue
"""

from core.zos_session import ZOSSession
from tools.system_tools import zemax_load_file
import os

def inspect_loaded_file():
    """Directly inspect the LDE after loading"""

    session = ZOSSession()

    output_dir = "D:\\mcp gemini zemax\\output\\compact_spectrometer"
    zmx_file = os.path.join(output_dir, "czerny_turner_spectrometer_FIXED.zmx")

    print("\n" + "="*80)
    print("DIRECT LDE INSPECTION")
    print("="*80)

    # Check file exists
    if os.path.exists(zmx_file):
        file_size = os.path.getsize(zmx_file)
        print(f"\n[1] File exists: {zmx_file}")
        print(f"    Size: {file_size} bytes ({file_size/1024:.1f} KB)")
    else:
        print(f"\n[1] ERROR: File not found: {zmx_file}")
        session.close()
        return

    # Load file
    print(f"\n[2] Loading file...")
    load_result = zemax_load_file(zmx_file)
    print(f"    Status: {load_result.get('status')}")
    print(f"    Message: {load_result.get('message', 'N/A')}")

    # Direct LDE access
    print(f"\n[3] Direct LDE Access:")
    try:
        the_system = session.ZOSAPI.TheSystem
        lde = the_system.LDE

        num_surfaces = lde.NumberOfSurfaces
        print(f"    LDE.NumberOfSurfaces: {num_surfaces}")

        # Check system wavelengths
        system_data = the_system.SystemData
        wavelengths = system_data.Wavelengths
        num_waves = wavelengths.NumberOfWavelengths
        print(f"    Number of wavelengths: {num_waves}")

        if num_waves > 0:
            for i in range(1, num_waves + 1):
                wave = wavelengths.GetWavelength(i)
                print(f"      Wave {i}: {wave.Wavelength} μm")

        # List all surfaces
        print(f"\n[4] Surface List:")
        for i in range(num_surfaces):
            surf = lde.GetSurfaceAt(i)
            surf_type = surf.TypeName
            comment = surf.Comment
            thickness = surf.Thickness
            print(f"    Surface {i}: {surf_type:20s} t={thickness:8.3f}  {comment}")

    except Exception as e:
        print(f"    ERROR: {str(e)}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*80)

    session.close()

if __name__ == '__main__':
    inspect_loaded_file()
