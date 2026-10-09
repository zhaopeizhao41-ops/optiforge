"""
Zemax Surface and Lens Data Editor (LDE) Tools
Handles surface modification, insertion, deletion, and solve configuration.
"""

import math
from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession
from core.editor_cells import read_cell, write_cell
from core.operation_guard import model_operation
from tools.config_tools import resize_configs, write_mce_row


def zemax_surface_operations(
    surface_index: int,
    radius: Optional[float] = None,
    thickness: Optional[float] = None,
    material: Optional[str] = None,
    semi_diameter: Optional[float] = None,
    conic: Optional[float] = None,
    comment: Optional[str] = None,
    is_stop: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Modify parameters of an optical surface in the Lens Data Editor (LDE).
    surface_index: 0-based surface index (0 is Object, 1..N are lenses/mirrors, last is Image).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    lde = sys.LDE

    if surface_index < 0 or surface_index >= lde.NumberOfSurfaces:
        return {
            "status": "error",
            "message": f"Invalid surface index {surface_index}. Available surfaces: 0 to {lde.NumberOfSurfaces - 1}.",
        }

    surf = lde.GetSurfaceAt(surface_index)

    if radius is not None:
        surf.Radius = float(radius)
    if thickness is not None:
        surf.Thickness = float(thickness)
    if material is not None:
        surf.Material = str(material).strip()
    if semi_diameter is not None:
        surf.SemiDiameter = float(semi_diameter)
    if conic is not None:
        surf.Conic = float(conic)
    if comment is not None:
        surf.Comment = str(comment)
    if is_stop is not None and surface_index > 0:
        surf.IsStop = bool(is_stop)

    return {
        "status": "success",
        "surface": {
            "index": surface_index,
            "comment": str(surf.Comment),
            "is_stop": bool(surf.IsStop),
            "radius": float(surf.Radius),
            "thickness": float(surf.Thickness),
            "material": str(surf.Material),
            "semi_diameter": float(surf.SemiDiameter),
            "conic": float(surf.Conic),
            "radius_solve": str(surf.RadiusCell.GetSolveData().Type),
            "thickness_solve": str(surf.ThicknessCell.GetSolveData().Type),
        },
    }


def zemax_insert_surface(surface_index: int) -> Dict[str, Any]:
    """Insert a new surface at the specified index."""
    session = ZOSSession.get_instance()
    sys = session.system
    lde = sys.LDE

    if surface_index < 1 or surface_index > lde.NumberOfSurfaces:
        return {
            "status": "error",
            "message": f"Surface insertion index must be between 1 and {lde.NumberOfSurfaces}.",
        }

    new_surf = lde.InsertNewSurfaceAt(surface_index)
    return {
        "status": "success",
        "message": f"Inserted new surface at index {surface_index}.",
        "new_surface_count": lde.NumberOfSurfaces,
    }


def zemax_delete_surface(surface_index: int) -> Dict[str, Any]:
    """Remove a surface at the specified index."""
    session = ZOSSession.get_instance()
    sys = session.system
    lde = sys.LDE

    # Surface 0 (Object) and last surface (Image) cannot be removed
    if surface_index <= 0 or surface_index >= lde.NumberOfSurfaces - 1:
        return {
            "status": "error",
            "message": f"Cannot delete Object (0) or Image ({lde.NumberOfSurfaces - 1}) surface.",
        }

    lde.RemoveSurfaceAt(surface_index)
    return {
        "status": "success",
        "message": f"Deleted surface at index {surface_index}.",
        "new_surface_count": lde.NumberOfSurfaces,
    }


def zemax_set_solve(
    surface_index: int,
    cell: str,
    solve_type: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Configure a solve on a surface cell.
    surface_index: Surface index.
    cell: 'radius', 'thickness', 'semidiameter', 'conic', or 'par1'..'par12' (the LDE
      parameter columns; on an EvenAspheric surface par1..par8 are a2, a4, ... a16).
    solve_type: 'variable', 'fixed', 'fnumber', 'pickup', 'marginal_ray_angle', 'marginal_ray_height', 'edgethickness'.
    params: Optional dict of solve parameters (e.g. {"f_number": 5.0} or {"source_surface": 1, "scale": 1.0}).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    lde = sys.LDE

    if surface_index < 0 or surface_index >= lde.NumberOfSurfaces:
        return {"status": "error", "message": f"Invalid surface index {surface_index}."}

    surf = lde.GetSurfaceAt(surface_index)
    target_cell = None
    cell_clean = cell.lower().strip()
    if cell_clean == "radius":
        target_cell = surf.RadiusCell
    elif cell_clean == "thickness":
        target_cell = surf.ThicknessCell
    elif cell_clean in ["semidiameter", "semi_diameter"]:
        target_cell = surf.SemiDiameterCell
    elif cell_clean == "conic":
        # The conic constant is a solve-capable LDE column, exposed through SurfaceColumn
        # rather than a named cell property (radius/thickness are the only ones with one).
        target_cell = surf.GetSurfaceCell(zos.Editors.LDE.SurfaceColumn.Conic)
    elif cell_clean.startswith("par") and cell_clean[3:].isdigit() and 1 <= int(cell_clean[3:]) <= 12:
        # Aspheric coefficients live in the ParN columns. Releasing these is the only way
        # to give a spherical-aberration-limited design the extra freedom it needs.
        target_cell = surf.GetSurfaceCell(
            getattr(zos.Editors.LDE.SurfaceColumn, f"Par{int(cell_clean[3:])}"))
    else:
        return {"status": "error",
                "message": f"Unknown cell '{cell}'. Must be 'radius', 'thickness', 'semidiameter', "
                           f"'conic', or 'par1'..'par12'."}

    st_clean = solve_type.lower().replace("_", "").strip()
    p = params or {}

    if st_clean == "variable":
        target_cell.MakeSolveVariable()
    elif st_clean == "fixed":
        target_cell.MakeSolveFixed()
    elif st_clean == "fnumber":
        solve = target_cell.CreateSolveType(zos.Editors.SolveType.FNumber)
        solve._S_FNumber.FNumber = float(p.get("f_number", 10.0))
        target_cell.SetSolveData(solve)
    elif st_clean in ["pickup", "surfacepickup"]:
        solve = target_cell.CreateSolveType(zos.Editors.SolveType.SurfacePickup)
        solve._S_SurfacePickup.Surface = int(p.get("source_surface", 1))
        solve._S_SurfacePickup.ScaleFactor = float(p.get("scale", 1.0))
        solve._S_SurfacePickup.Offset = float(p.get("offset", 0.0))
        target_cell.SetSolveData(solve)
    elif st_clean == "marginalrayangle":
        solve = target_cell.CreateSolveType(zos.Editors.SolveType.MarginalRayAngle)
        solve._S_MarginalRayAngle.Angle = float(p.get("angle", 0.0))
        target_cell.SetSolveData(solve)
    elif st_clean == "marginalrayheight":
        solve = target_cell.CreateSolveType(zos.Editors.SolveType.MarginalRayHeight)
        solve._S_MarginalRayHeight.Height = float(p.get("height", 0.0))
        target_cell.SetSolveData(solve)
    elif st_clean == "edgethickness":
        solve = target_cell.CreateSolveType(zos.Editors.SolveType.EdgeThickness)
        solve._S_EdgeThickness.Thickness = float(p.get("thickness", 1.0))
        solve._S_EdgeThickness.RadialHeight = float(p.get("radial_height", 0.0))
        target_cell.SetSolveData(solve)
    else:
        return {
            "status": "error",
            "message": f"Unsupported solve type '{solve_type}'. Supported: 'variable', 'fixed', 'fnumber', 'pickup', 'marginal_ray_angle', 'marginal_ray_height', 'edgethickness'.",
        }

    return {
        "status": "success",
        "surface": surface_index,
        "cell": cell,
        "solve_type": str(target_cell.GetSolveData().Type),
    }


# Friendly names for LDE parameter columns (ParN), keyed by surface type.
_PARAM_ALIASES = {
    "CoordinateBreak": {"decenter_x": 1, "decenter_y": 2, "tilt_x": 3, "tilt_y": 4, "tilt_z": 5, "order": 6},
    "EvenAspheric": {f"a{2 * k}": k for k in range(1, 9)},
    # A grating only disperses when its Type is DiffractionGrating and Par1 (lines/um) is
    # non-zero; a Standard surface with "grating" in the Comment is a plain flat.
    "DiffractionGrating": {"lines_per_um": 1, "order": 2},
}


def zemax_set_surface_type(surface_index: int, surface_type: str) -> Dict[str, Any]:
    """Change a surface type by ZOSAPI SurfaceType name (Standard, EvenAspheric, CoordinateBreak, ...)."""
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    if surface_index < 0 or surface_index >= lde.NumberOfSurfaces:
        return {"status": "error", "message": f"Invalid surface index {surface_index}."}
    types = session.ZOSAPI.Editors.LDE.SurfaceType
    wanted = str(surface_type).replace(" ", "").lower()
    # Only real enum members: dir() also lists methods (ToString, ...), and passing one to .NET crashes pythonnet.
    match = next((n for n in dir(types) if n.lower() == wanted and isinstance(getattr(types, n), types)), None)
    if match is None:
        return {"status": "error", "message": f"Surface type '{surface_type}' not found in ZOSAPI SurfaceType."}
    surf = lde.GetSurfaceAt(surface_index)
    surf.ChangeType(surf.GetSurfaceTypeSettings(getattr(types, match)))
    return {"status": "success", "surface_index": surface_index, "type": str(surf.Type)}


def zemax_set_surface_params(surface_index: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Write LDE parameter columns. Keys are par1..par12, or aliases for the current surface type:
    CoordinateBreak: decenter_x, decenter_y, tilt_x, tilt_y, tilt_z (deg), order (0/1);
    EvenAspheric: a2, a4, ..., a16;
    DiffractionGrating: lines_per_um (Par1), order (Par2) -- set both, and the surface Material
    to MIRROR for a reflection grating. Grating efficiency is not modelled by OpticStudio.
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    if surface_index < 0 or surface_index >= lde.NumberOfSurfaces:
        return {"status": "error", "message": f"Invalid surface index {surface_index}."}
    surf = lde.GetSurfaceAt(surface_index)
    kind = str(surf.Type)
    aliases = _PARAM_ALIASES.get(kind, {})
    columns = session.ZOSAPI.Editors.LDE.SurfaceColumn
    written = {}
    for key, value in params.items():
        name = str(key).lower()
        slot = aliases.get(name) or (int(name[3:]) if name.startswith("par") and name[3:].isdigit() else None)
        column = getattr(columns, f"Par{slot}", None) if slot else None
        if column is None:
            return {"status": "error", "message": f"'{key}' is not a parameter of a {kind} surface; "
                                                  f"use par1..par12 or {sorted(aliases) or 'no aliases'}."}
        written[name] = write_cell(surf.GetSurfaceCell(column), value, f"surface {surface_index} {key}")
    return {"status": "success", "surface_index": surface_index, "type": kind, "written": written}


def _fold_target_error(lde, surface_index: int) -> Optional[str]:
    """A fold/scan mirror replaces an existing flat, air-spaced, standard dummy surface."""
    if surface_index <= 0 or surface_index >= lde.NumberOfSurfaces - 1:
        return f"Surface {surface_index} must lie between the object and image surfaces."
    surf = lde.GetSurfaceAt(surface_index)
    if surf.IsStop:
        return f"Surface {surface_index} is the stop; insert a dummy surface for the mirror instead."
    if str(surf.Type) != "Standard" or math.isfinite(float(surf.Radius)) or str(surf.Material).strip():
        return f"Surface {surface_index} must be a flat Standard surface in air (a dummy placeholder)."
    return None


def _fold(session, surface_index: int, reflect_angle_deg: float, axis: str) -> Dict[str, int]:
    """Run the OpticStudio fold-mirror tool: surface k becomes CB, MIRROR, CB (2 surfaces added)."""
    lde = session.system.LDE
    before = int(lde.NumberOfSurfaces)
    tilt = session.ZOSAPI.Editors.LDE.TiltType
    lde.RunTool_AddFoldMirror(surface_index, tilt.XTilt if axis == "x" else tilt.YTilt, float(reflect_angle_deg))
    if int(lde.NumberOfSurfaces) != before + 2:
        raise RuntimeError(f"Fold-mirror tool added {int(lde.NumberOfSurfaces) - before} surfaces, expected 2.")
    return {"cb_before": surface_index, "mirror_surface": surface_index + 1, "cb_after": surface_index + 2,
            "n_surfaces": int(lde.NumberOfSurfaces)}


def zemax_add_fold_mirror(surface_index: int, reflect_angle_deg: float = 90.0, axis: str = "x") -> Dict[str, Any]:
    """
    Turn a flat dummy surface into a fold mirror deflecting the beam by reflect_angle_deg about the
    local x or y axis. Thicknesses after the mirror are negated automatically.
    """
    session = ZOSSession.get_instance()
    error = _fold_target_error(session.system.LDE, surface_index)
    if error:
        return {"status": "error", "message": error}
    return {"status": "success", "axis": axis, "reflect_angle_deg": float(reflect_angle_deg),
            **_fold(session, surface_index, reflect_angle_deg, axis)}


def _pickup(session, cell, source_surface: int, column, scale: float, offset: float) -> None:
    solve = cell.CreateSolveType(session.ZOSAPI.Editors.SolveType.SurfacePickup)
    solve._S_SurfacePickup.Surface = int(source_surface)
    solve._S_SurfacePickup.Column = column
    solve._S_SurfacePickup.ScaleFactor = float(scale)
    solve._S_SurfacePickup.Offset = float(offset)
    cell.SetSolveData(solve)


def zemax_add_scan_mirror(
    surface_index: int,
    scan_angles_deg: List[Any],
    reflect_angle_deg: float = 90.0,
    axis: str = "x",
) -> Dict[str, Any]:
    """
    Fold mirror whose mechanical tilt varies per configuration (galvo / MEMS scanner).
    scan_angles_deg: one mechanical angle per configuration about the fold axis, or [fold, cross]
    pairs for a 2-axis mirror. The beam deviates by ~2x the fold-axis angle. CB1 carries
    theta0 + alpha through MCE PRAM rows; CB2 picks it up with scale -1 and offset 2*theta0, so
    everything after the mirror keeps the nominal fold axis. A single-configuration system is
    expanded to one configuration per angle; otherwise the counts must already match.
    """
    session = ZOSSession.get_instance()
    lde, mce = session.system.LDE, session.system.MCE
    pairs = [tuple(float(v) for v in a) if isinstance(a, (list, tuple)) else (float(a), 0.0) for a in scan_angles_deg]
    if not pairs or any(len(p) != 2 for p in pairs):
        return {"status": "error", "message": "scan_angles_deg needs numbers or [fold, cross] pairs."}
    n_cfg = int(mce.NumberOfConfigurations)
    if n_cfg not in (1, len(pairs)):
        return {"status": "error", "message": f"System has {n_cfg} configurations but {len(pairs)} scan angles."}
    error = _fold_target_error(lde, surface_index)
    if error:
        return {"status": "error", "message": error}

    result = _fold(session, surface_index, reflect_angle_deg, axis)
    cb1, cb2 = lde.GetSurfaceAt(result["cb_before"]), lde.GetSurfaceAt(result["cb_after"])
    columns = session.ZOSAPI.Editors.LDE.SurfaceColumn
    fold_par, cross_par = (3, 4) if axis == "x" else (4, 3)
    theta0 = read_cell(cb1.GetSurfaceCell(getattr(columns, f"Par{fold_par}")))
    two_axis = any(p[1] for p in pairs)
    for par, offset in ((fold_par, 2.0 * theta0), (cross_par, 0.0))[: 2 if two_axis else 1]:
        column = getattr(columns, f"Par{par}")
        _pickup(session, cb2.GetSurfaceCell(column), result["cb_before"], column, -1.0, offset)
    # Order: the fold-axis tilt is the outer gimbal axis on both CBs, so CB2 undoes CB1 exactly.
    cb1.GetSurfaceCell(columns.Par6).IntegerValue = 0 if axis == "x" else 1
    cb2.GetSurfaceCell(columns.Par6).IntegerValue = 1 if axis == "x" else 0

    resize_configs(mce, len(pairs))
    zos = session.ZOSAPI
    rows = [write_mce_row(zos, mce, "PRAM", [theta0 + p[0] for p in pairs], result["cb_before"], fold_par)[0]]
    if two_axis:
        rows.append(write_mce_row(zos, mce, "PRAM", [p[1] for p in pairs], result["cb_before"], cross_par)[0])
    mce.SetCurrentConfiguration(1)
    return {"status": "success", "axis": axis, "theta0_deg": theta0, "n_configs": int(mce.NumberOfConfigurations),
            "mce_rows": rows, "scan_angles_deg": [list(p) for p in pairs], **result}


for _name in ("zemax_surface_operations", "zemax_insert_surface", "zemax_delete_surface", "zemax_set_solve",
              "zemax_set_surface_type", "zemax_set_surface_params", "zemax_add_fold_mirror",
              "zemax_add_scan_mirror"):
    globals()[_name] = model_operation(globals()[_name])
