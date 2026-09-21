"""
Zemax System Management Tools
Handles system initialization, file I/O, templates, and high-level system inspection.
"""

import os
from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession
from domain.design_templates import get_template, list_templates


def zemax_system_info() -> Dict[str, Any]:
    """
    Get current Zemax OpticStudio status, active license, primary system parameters,
    and first-order optical properties.
    """
    session = ZOSSession.get_instance()
    app = session.application
    sys = session.system
    zos = session.ZOSAPI

    if not app or not sys:
        return {"status": "error", "message": "ZOS-API session not initialized."}

    # License and mode
    license_status = str(app.LicenseStatus)
    mode = session.mode
    is_valid = bool(app.IsValidLicenseForAPI)

    # First-order paraxial evaluation via MFE
    try:
        efl = sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.EFFL, 1, 0, 0, 0, 0, 0, 0, 0)
        totr = sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.TOTR, 0, 0, 0, 0, 0, 0, 0, 0)
        wfno = sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.WFNO, 1, 0, 0, 0, 0, 0, 0, 0)
        enpz = sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.ENPZ, 0, 0, 0, 0, 0, 0, 0, 0)
        expp = sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.EXPP, 0, 0, 0, 0, 0, 0, 0, 0)
    except Exception:
        efl, totr, wfno, enpz, expp = 0.0, 0.0, 0.0, 0.0, 0.0

    sd = sys.SystemData
    aperture_type = str(sd.Aperture.ApertureType)
    aperture_val = float(sd.Aperture.ApertureValue)
    field_type = str(sd.Fields.GetFieldType())
    num_fields = int(sd.Fields.NumberOfFields)
    num_waves = int(sd.Wavelengths.NumberOfWavelengths)
    num_surfaces = int(sys.LDE.NumberOfSurfaces)

    return {
        "status": "success",
        "connection_mode": mode,
        "license_valid": is_valid,
        "license_status": license_status,
        "current_file": session.current_filepath or "Unsaved New File",
        "surfaces_count": num_surfaces,
        "aperture": {
            "type": aperture_type,
            "value": aperture_val,
        },
        "fields_count": num_fields,
        "field_type": field_type,
        "wavelengths_count": num_waves,
        "first_order_properties": {
            "effective_focal_length_mm": round(efl, 4) if abs(efl) < 1e9 else "Infinite",
            "total_track_mm": round(totr, 4),
            "working_f_number": round(wfno, 4) if abs(wfno) < 1e9 else "N/A",
            "entrance_pupil_position_mm": round(enpz, 4),
            "exit_pupil_position_mm": round(expp, 4),
        },
    }


def zemax_new_file(catalogs: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Create a clean, new sequential optical design.
    Automatically enables standard glass catalogs (default: SCHOTT and CDGM).
    """
    session = ZOSSession.get_instance()
    session.new_system(save_changes=False)
    sys = session.system

    # Load glass catalogs
    target_catalogs = catalogs or ["SCHOTT", "CDGM"]
    loaded = []
    for cat in target_catalogs:
        try:
            sys.SystemData.MaterialCatalogs.AddCatalog(cat)
            loaded.append(cat)
        except Exception:
            pass

    return {
        "status": "success",
        "message": "Initialized clean sequential optical system.",
        "loaded_glass_catalogs": loaded,
        "num_surfaces": sys.LDE.NumberOfSurfaces,
    }


def zemax_load_file(filepath: str) -> Dict[str, Any]:
    """Load a Zemax .zos or .zmx optical design file."""
    session = ZOSSession.get_instance()
    abs_path = os.path.abspath(filepath)
    if not os.path.exists(abs_path):
        return {"status": "error", "message": f"File does not exist: {abs_path}"}
    session.load_file(abs_path, save_changes=False)
    return {
        "status": "success",
        "message": f"Successfully loaded design: {abs_path}",
        "surfaces_count": session.system.LDE.NumberOfSurfaces,
    }


def zemax_save_file(filepath: Optional[str] = None) -> Dict[str, Any]:
    """
    Save the active optical design to disk.
    If filepath is omitted, automatically saves as .zmx in 'd:/mcp gemini zemax/output/optical_design.zmx'.
    Supports both .zmx (classic ASCII format) and .zos (OpticStudio modern format).
    """
    session = ZOSSession.get_instance()
    try:
        target_path = filepath
        if not target_path:
            if session.current_filepath:
                target_path = session.current_filepath
            else:
                default_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
                os.makedirs(default_dir, exist_ok=True)
                target_path = os.path.join(default_dir, "optical_design.zmx")

        # If extension omitted, default to .zmx
        _, ext = os.path.splitext(target_path)
        if not ext:
            target_path = target_path + ".zmx"

        session.save_file(target_path)
        return {
            "status": "success",
            "saved_to": session.current_filepath,
            "format": os.path.splitext(session.current_filepath)[1].lower(),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def zemax_get_system_data() -> Dict[str, Any]:
    """
    Retrieve comprehensive data of the current optical system:
    All surface parameters (radius, thickness, material, semi-diameter, conic, comment, solves),
    fields list, wavelengths list, and aperture parameters.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    lde = sys.LDE
    sd = sys.SystemData

    surfaces = []
    for i in range(lde.NumberOfSurfaces):
        surf = lde.GetSurfaceAt(i)
        
        # Determine variable status via GetSolveData()
        rad_solve_data = surf.RadiusCell.GetSolveData()
        thk_solve_data = surf.ThicknessCell.GetSolveData()
        rad_solve = str(rad_solve_data.Type)
        thk_solve = str(thk_solve_data.Type)

        surfaces.append({
            "index": i,
            "comment": str(surf.Comment),
            "is_stop": bool(surf.IsStop),
            "radius": float(surf.Radius),
            "thickness": float(surf.Thickness),
            "material": str(surf.Material).strip(),
            "semi_diameter": float(surf.SemiDiameter),
            "conic": float(surf.Conic),
            "radius_solve": rad_solve,
            "thickness_solve": thk_solve,
            "is_radius_variable": (rad_solve_data.Type == zos.Editors.SolveType.Variable),
            "is_thickness_variable": (thk_solve_data.Type == zos.Editors.SolveType.Variable),
        })

    # Fields
    fields = []
    for f_idx in range(1, sd.Fields.NumberOfFields + 1):
        f = sd.Fields.GetField(f_idx)
        fields.append({
            "index": f_idx,
            "x": float(f.X),
            "y": float(f.Y),
            "weight": float(f.Weight),
            "vdx": float(f.VDX),
            "vdy": float(f.VDY),
            "vcx": float(f.VCX),
            "vcy": float(f.VCY),
        })

    # Wavelengths
    wavelengths = []
    for w_idx in range(1, sd.Wavelengths.NumberOfWavelengths + 1):
        w = sd.Wavelengths.GetWavelength(w_idx)
        wavelengths.append({
            "index": w_idx,
            "wavelength_um": float(w.Wavelength),
            "weight": float(w.Weight),
            "is_primary": bool(w.IsPrimary),
        })

    return {
        "status": "success",
        "general": {
            "aperture_type": str(sd.Aperture.ApertureType),
            "aperture_value": float(sd.Aperture.ApertureValue),
            "field_type": str(sd.Fields.GetFieldType()),
            "ray_aiming": str(sd.RayAiming.RayAiming),
        },
        "surfaces": surfaces,
        "fields": fields,
        "wavelengths": wavelengths,
    }


def zemax_load_template(template_id: str) -> Dict[str, Any]:
    """
    Instantiate a classic optical design template into Zemax:
    Available templates: 'singlet_bk7', 'achromat_doublet', 'cooke_triplet'.
    """
    session = ZOSSession.get_instance()
    tmpl = get_template(template_id)
    session.new_system(save_changes=False)
    sys = session.system
    zos = session.ZOSAPI

    # Set Catalogs
    sys.SystemData.MaterialCatalogs.AddCatalog("SCHOTT")
    sys.SystemData.MaterialCatalogs.AddCatalog("CDGM")

    # Aperture
    sys.SystemData.Aperture.ApertureValue = tmpl["aperture"]["value"]

    # Wavelengths
    waves = tmpl["wavelengths"]
    for idx, w in enumerate(waves):
        if idx == 0:
            w1 = sys.SystemData.Wavelengths.GetWavelength(1)
            w1.Wavelength = w
            w1.Weight = 1.0
            w1.MakePrimary()
        else:
            sys.SystemData.Wavelengths.AddWavelength(w, 1.0)

    # Fields
    f_list = tmpl["fields"]
    for idx, f in enumerate(f_list):
        if idx == 0:
            f1 = sys.SystemData.Fields.GetField(1)
            f1.X = f["x"]
            f1.Y = f["y"]
            f1.Weight = f["weight"]
        else:
            sys.SystemData.Fields.AddField(f["x"], f["y"], f["weight"])

    # Surfaces
    lde = sys.LDE
    surfaces_data = tmpl["surfaces"]
    for idx, s in enumerate(surfaces_data, start=1):
        surf = lde.InsertNewSurfaceAt(idx)
        surf.Radius = s.get("radius", 0.0)
        surf.Thickness = s.get("thickness", 0.0)
        surf.Material = s.get("material", "")
        surf.Comment = s.get("comment", "")

    stop_idx = tmpl.get("stop_surface", 1)
    if stop_idx < lde.NumberOfSurfaces:
        lde.GetSurfaceAt(stop_idx).IsStop = True

    return {
        "status": "success",
        "message": f"Successfully loaded template: {tmpl['name']}",
        "template_id": template_id,
        "description": tmpl["description"],
        "num_surfaces": lde.NumberOfSurfaces,
    }
