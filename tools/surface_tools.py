"""
Zemax Surface and Lens Data Editor (LDE) Tools
Handles surface modification, insertion, deletion, and solve configuration.
"""

import math
from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession
from core.editor_cells import read_cell, write_cell
from core.operation_guard import model_operation
from domain.zemax_rules import OpticalRuleCheck
from tools.config_tools import resize_configs, write_mce_row
from tools.optical_setup_tools import (
    _apply_aperture,
    _apply_fields,
    _apply_ray_aiming,
    _apply_wavelengths,
)


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


def _write_surface_params(surface_index: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrapped parameter-column writer; also called from zemax_apply_prescription."""
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


def zemax_set_surface_params(surface_index: int, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Write LDE parameter columns. Keys are par1..par12, or aliases for the current surface type:
    CoordinateBreak: decenter_x, decenter_y, tilt_x, tilt_y, tilt_z (deg), order (0/1);
    EvenAspheric: a2, a4, ..., a16;
    DiffractionGrating: lines_per_um (Par1), order (Par2) -- set both, and the surface Material
    to MIRROR for a reflection grating. Grating efficiency is not modelled by OpticStudio.
    """
    return _write_surface_params(surface_index, params)


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


def _prescription_rule_check(surfaces: List[Dict[str, Any]], general: Dict[str, Any],
                             fields: Optional[List[Dict[str, Any]]],
                             wavelengths: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Run the shared OpticalRuleCheck over a prescription that has not been written yet.

    Builds the same system summary shape zemax_get_system_data returns, so the batch path
    and the post-hoc validation report can never disagree about a threshold.
    """
    summary_surfaces = [
        {
            "index": i,
            "comment": str(s.get("comment") or ""),
            "type": s["_type"],
            "is_stop": bool(s.get("is_stop")),
            "radius": s["_r"],
            "thickness": s["_t"],
            "material": s["_m"],
            "semi_diameter": s["_sd"],
            "conic": float(s.get("conic") or 0.0),
        }
        for i, s in enumerate(surfaces)
    ]
    return OpticalRuleCheck().validate_system({
        "surfaces": summary_surfaces,
        "general": general,
        "fields": fields or [],
        "wavelengths": wavelengths or [],
    })


def _preflight_prescription(surfaces: List[Dict[str, Any]], general: Dict[str, Any],
                            fields: Optional[List[Dict[str, Any]]],
                            wavelengths: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Refuse a whole prescription before a single cell is written.

    A design-rule violation caught here costs nothing: nothing has been touched, so there is
    no snapshot to roll back. Caught afterwards it costs a rollback, a recovery file, and
    another assistant turn to retype the prescription.
    """
    for i, s in enumerate(surfaces):
        if s["_r"] is None:
            continue  # inherits the radius already in the LDE
        if isinstance(s["_r"], str) and s["_r"].strip().lower() in ("flat", "inf", "infinite", "plane"):
            s["_r"] = 0.0  # optical flat; the LDE radius column takes 0.0, written as such below
        if s["_r"] is not None and not math.isfinite(s["_r"]):
            s["_r"] = 0.0

    audit = _prescription_rule_check(surfaces, general, fields, wavelengths)
    findings = audit["findings"]
    critical = [f for f in findings if f["level"] == "CRITICAL"]
    warnings = [f for f in findings if f["level"] == "WARNING"]

    # AGENTS.md red lines, applied as numeric rejects rather than prose. OpticalRuleCheck
    # already flags each of these; the batch path refuses rather than reports because the
    # prescription is not yet in the model.
    for i, s in enumerate(surfaces):
        if s["_m"] and s["_t"] is not None and s["_t"] < 1.0:
            critical.append({
                "level": "CRITICAL", "rule": "Glass Center Thickness", "surface": i,
                "message": f"Surface {i} ({s['_m']}) center thickness {s['_t']:.3f} mm < 1.0 mm "
                           f"(AGENTS.md DFM red line).",
            })
        # Internal element-to-element air gap. Exempted by Comment keyword the same way
        # OpticalRuleCheck does it, for deliberate relay / intermediate-image spaces.
        if not s["_m"] and s["_t"] is not None:
            comment = str(s.get("comment") or "").upper()
            is_relay = any(k in comment for k in ("RELAY", "INTERMEDIATE", "CONJUGATE", "4F",
                                                  "TUBE TO OBJ", "SCAN TO TUBE"))
            if s["_t"] > 12.0 and not is_relay and not _is_external_air(surfaces, i):
                critical.append({
                    "level": "CRITICAL", "rule": "Excessive Internal Air Space", "surface": i,
                    "message": f"Surface {i} internal air gap {s['_t']:.2f} mm > 12.0 mm "
                               f"(AGENTS.md optimizer-escape red line). Long gaps decenter badly.",
                })

    payload: Dict[str, Any] = {
        "status": "rejected" if critical else "ok",
        "audit_status": audit["status"],
        "critical_count": len(critical),
        "warning_count": len(warnings),
        "critical": critical,
        "warnings": warnings,
        "total_findings": len(findings),
    }
    if critical:
        payload["status"] = "rejected"
        payload["code"] = "PRESCRIPTION_RULE_VIOLATION"
        payload["message"] = (
            f"{len(critical)} critical design-rule violation(s); nothing was written. Fix the "
            f"flagged surfaces and resubmit, or pass allow_warnings=True to override the "
            f"warning-level findings only (critical findings cannot be overridden)."
        )
    return payload


def _is_external_air(surfaces: List[Dict[str, Any]], index: int) -> bool:
    """Air before the first glass or after the last glass is outside the lens stack."""
    glass = [i for i, s in enumerate(surfaces) if s["_m"]]
    if not glass:
        return True
    return index < glass[0] or index >= glass[-1]


def _coerce_radius(radius: Any, index: int) -> float:
    """Accept a number, or the words a designer actually writes for a flat."""
    if isinstance(radius, str):
        text = radius.strip().lower()
        if text in ("flat", "inf", "infinite", "plane", "plano"):
            return 0.0
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"surfaces[{index}].radius '{radius}' is not a number or 'flat'.")
    return float(radius)


def _resolve_surface_type(session, surface_type: str):
    """Map a ZOSAPI SurfaceType name to its enum member (only real enum members)."""
    types = session.ZOSAPI.Editors.LDE.SurfaceType
    wanted = str(surface_type).replace(" ", "").lower()
    match = next((n for n in dir(types)
                  if n.lower() == wanted and isinstance(getattr(types, n), types)), None)
    if match is None:
        raise ValueError(f"Surface type '{surface_type}' not found in ZOSAPI SurfaceType.")
    return getattr(types, match)


def zemax_apply_prescription(
    surfaces: List[Dict[str, Any]],
    fields: Optional[List[Dict[str, Any]]] = None,
    field_type: Optional[str] = None,
    wavelengths: Optional[List[Dict[str, Any]]] = None,
    primary_wavelength_index: int = 1,
    aperture_type: Optional[str] = None,
    aperture_value: Optional[float] = None,
    ray_aiming: Optional[str] = None,
    stop_surface: Optional[int] = None,
    allow_warnings: bool = False,
) -> Dict[str, Any]:
    """
    Write a complete prescription in ONE call: the whole surface table, plus fields,
    wavelengths, aperture, ray aiming and the stop, under a single snapshot and one gate check.

    Replaces the ~4-calls-per-surface modeling loop. The entire prescription is checked
    against the shared design rules first (OpticalRuleCheck: glass center/edge thickness,
    internal air gap, barrel aspect ratio, steepness, near-flat sag, ray aiming) and refused
    as a whole if any CRITICAL finding is present, so an infeasible design never lands.

    surfaces: Object to Image, in order. Each entry:
      radius (float, or null/omitted to keep the LDE's current value), thickness (float, mm),
      material (glass name; omit, null or 'AIR' for air), semi_diameter (float),
      conic (float), comment (str), type ('Standard', 'EvenAspheric', 'CoordinateBreak',
      'DiffractionGrating', ...), params (par1..par12 or the type's aliases), is_stop (bool).
      The first entry is the Object surface and the last is the Image surface.
    fields: [{"x": .., "y": .., "weight": ..}]; field_type: 'Angle', 'ObjectHeight',
      'ParaxialImageHeight', 'RealImageHeight'. Omit either to leave fields untouched.
    wavelengths: [{"wavelength_um": .., "weight": ..}]; primary_wavelength_index: 1-based.
    aperture_type / aperture_value: 'EPD', 'ImageSpaceFNum', 'ObjectSpaceNA', 'FloatByStopSize'.
    ray_aiming: 'Off', 'Paraxial', 'Real'. Required when a CoordinateBreak sits at or before
      the stop, or the field angle exceeds 20 degrees.
    stop_surface: 0-based index of the aperture stop; applied after the surfaces are written.
    allow_warnings: write despite WARNING-level findings (CRITICAL findings always refuse).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    lde = sys.LDE
    sd = sys.SystemData

    if not isinstance(surfaces, list) or len(surfaces) < 2:
        return {"status": "error", "code": "BAD_PRESCRIPTION",
                "message": "surfaces must list at least the Object and Image surfaces."}

    # Normalize the raw entries before any validation reads them.
    prepared: List[Dict[str, Any]] = []
    target = lde.NumberOfSurfaces
    for i, s in enumerate(surfaces):
        if not isinstance(s, dict):
            return {"status": "error", "code": "BAD_PRESCRIPTION",
                    "message": f"surfaces[{i}] must be an object, got {type(s).__name__}."}
        radius = s.get("radius")
        thickness = s.get("thickness")
        if i != 0 and thickness is None:
            return {"status": "error", "code": "MISSING_THICKNESS",
                    "message": f"surfaces[{i}] has no thickness. Every surface except the Object "
                               f"needs one; use 0.0 for a dummy plane."}
        material = str(s.get("material") or "").strip()
        if material.upper() == "AIR":
            material = ""
        data: Dict[str, Any] = {
            "radius": None if radius is None else _coerce_radius(radius, i),
            "thickness": None if thickness is None else float(thickness),
            "material": material,
            "semi_diameter": s.get("semi_diameter"),
            "conic": s.get("conic"),
            "comment": s.get("comment"),
            "type": s.get("type"),
            "params": s.get("params") or {},
            "is_stop": s.get("is_stop"),
        }
        # Mirror the normalized values into the keys the rule summary reads, keeping the
        # unset sentinels (-1 radius, 0.0 semi-diameter) that the LDE reports for a fresh row.
        surf_now = lde.GetSurfaceAt(min(i, target - 1))
        data["_r"] = data["radius"] if data["radius"] is not None else float(surf_now.Radius)
        data["_t"] = data["thickness"] if data["thickness"] is not None else 0.0
        data["_m"] = data["material"]
        data["_sd"] = (float(data["semi_diameter"]) if data["semi_diameter"] is not None
                       else float(surf_now.SemiDiameter))
        # Resolve the type to a real enum member now, not in the write loop: a typo found
        # mid-write would cost a snapshot rollback and a recovery file for a caller mistake.
        if data["type"] is not None:
            try:
                data["_resolved_type"] = _resolve_surface_type(session, data["type"])
            except ValueError as error:
                return {"status": "error", "code": "BAD_SURFACE_TYPE",
                        "message": f"surfaces[{i}]: {error}"}
        else:
            data["_resolved_type"] = None
        data["_type"] = str(data["type"] or str(surf_now.Type).split(".")[-1])
        prepared.append(data)

    # Field and wavelength types are only validated when the caller supplies them.
    field_writer_type = "angle"
    if field_type is not None:
        field_writer_type = str(field_type).lower().replace("_", "").strip()
        if field_writer_type not in ("angle", "objectheight", "height", "paraxialimageheight",
                                     "realimageheight"):
            return {"status": "error", "code": "BAD_FIELD_TYPE",
                    "message": f"Unsupported field_type '{field_type}'. Choose from 'Angle', "
                               f"'ObjectHeight', 'ParaxialImageHeight', 'RealImageHeight'."}
    if fields is not None and not fields:
        return {"status": "error", "code": "BAD_FIELDS",
                "message": "fields was supplied as an empty list; omit it to leave fields untouched."}

    general = {
        "field_type": str(field_writer_type),
        "ray_aiming": str(ray_aiming if ray_aiming is not None
                          else sd.RayAiming.RayAiming),
    }
    audit = _preflight_prescription(prepared, general, fields, wavelengths)

    if audit["status"] == "rejected":
        return audit
    if audit["warning_count"] and not allow_warnings:
        return {
            "status": "rejected",
            "code": "PRESCRIPTION_WARNINGS",
            "message": (f"{audit['warning_count']} design-rule warning(s); nothing was written. "
                        f"Review the findings and resubmit, or pass allow_warnings=True to "
                        f"write anyway."),
            "audit_status": audit["audit_status"],
            "critical_count": 0,
            "warning_count": audit["warning_count"],
            "critical": [],
            "warnings": audit["warnings"],
            "total_findings": audit["total_findings"],
        }

    # Grow or shrink the LDE to the prescription length. The Image surface stays last.
    while lde.NumberOfSurfaces < len(prepared):
        lde.InsertNewSurfaceAt(lde.NumberOfSurfaces - 1)
    while lde.NumberOfSurfaces > len(prepared):
        if lde.NumberOfSurfaces <= 2:
            break
        lde.RemoveSurfaceAt(lde.NumberOfSurfaces - 2)

    written = []
    for i, s in enumerate(prepared):
        surf = lde.GetSurfaceAt(i)
        if s["_resolved_type"] is not None:
            surf.ChangeType(surf.GetSurfaceTypeSettings(s["_resolved_type"]))
        if s["radius"] is not None:
            surf.Radius = s["radius"]
        if s["thickness"] is not None:
            surf.Thickness = s["thickness"]
        surf.Material = s["material"]
        if s["semi_diameter"] is not None:
            surf.SemiDiameter = float(s["semi_diameter"])
        if s["conic"] is not None:
            surf.Conic = float(s["conic"])
        if s["comment"] is not None:
            surf.Comment = str(s["comment"])
        if s["params"]:
            params_result = _write_surface_params(i, s["params"])
            if params_result.get("status") == "error":
                return params_result
        if s["is_stop"] is not None and i > 0:
            surf.IsStop = bool(s["is_stop"])
        written.append({
            "index": i,
            "type": str(surf.Type).split(".")[-1],
            "radius": float(surf.Radius),
            "thickness": float(surf.Thickness),
            "material": str(surf.Material).strip(),
            "semi_diameter": float(surf.SemiDiameter),
            "is_stop": bool(surf.IsStop),
        })

    system_updates: Dict[str, Any] = {}
    if aperture_type is not None:
        result = _apply_aperture(aperture_type, float(aperture_value or 0.0))
        if result.get("status") == "error":
            return result
        system_updates["aperture"] = {"type": result["aperture_type"],
                                      "value": result["aperture_value"]}
    if fields is not None:
        result = _apply_fields(field_type, fields)
        if result.get("status") == "error":
            return result
        system_updates["fields"] = {"type": result["field_type"],
                                    "count": result["number_of_fields"]}
    if wavelengths is not None:
        result = _apply_wavelengths(wavelengths, int(primary_wavelength_index))
        if result.get("status") == "error":
            return result
        system_updates["wavelengths"] = {"count": result["number_of_wavelengths"],
                                         "primary_index": result["primary_wavelength_index"]}
    if ray_aiming is not None:
        result = _apply_ray_aiming(ray_aiming)
        if result.get("status") == "error":
            return result
        system_updates["ray_aiming"] = result["ray_aiming"]
    if stop_surface is not None:
        stop_index = int(stop_surface)
        if not 1 <= stop_index <= lde.NumberOfSurfaces - 1:
            return {"status": "error", "code": "BAD_STOP",
                    "message": f"stop_surface {stop_index} must be between 1 and "
                               f"{lde.NumberOfSurfaces - 2}."}
        for i in range(lde.NumberOfSurfaces):
            lde.GetSurfaceAt(i).IsStop = (i == stop_index)
        system_updates["stop_surface"] = stop_index

    return {
        "status": "success",
        "message": f"Prescription written: {len(written)} surfaces.",
        "surface_count": lde.NumberOfSurfaces,
        "surfaces": written,
        "system_updates": system_updates,
        "validation": {
            "audit_status": audit["audit_status"],
            "critical_count": 0,
            "warning_count": audit["warning_count"],
            "warnings": audit["warnings"],
            "total_findings": audit["total_findings"],
            "overridden": bool(audit["warning_count"] and allow_warnings),
        },
    }


for _name in ("zemax_surface_operations", "zemax_insert_surface", "zemax_delete_surface", "zemax_set_solve",
              "zemax_set_surface_type", "zemax_set_surface_params", "zemax_add_fold_mirror",
              "zemax_add_scan_mirror", "zemax_apply_prescription"):
    globals()[_name] = model_operation(globals()[_name])
