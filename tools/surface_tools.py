"""
Zemax Surface and Lens Data Editor (LDE) Tools
Handles surface modification, insertion, deletion, and solve configuration.
"""

from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession


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
    cell: 'radius', 'thickness', or 'semidiameter'.
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
    else:
        return {"status": "error", "message": f"Unknown cell '{cell}'. Must be 'radius', 'thickness', or 'semidiameter'."}

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
        solve._S_SurfacePickup.Scale = float(p.get("scale", 1.0))
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
