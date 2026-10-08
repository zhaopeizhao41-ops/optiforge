"""
Mechanical structure generation and assembly tools for optical systems.
Includes barrel generation, optomechanical spacing calculation, and mounting interface design.

These tools encode the manufacturability rules the design specification mandates:
  * Flat land margin >= 1.0 mm beyond the clear aperture (the ground flat that a lens
    seat actually rests on).
  * Outer diameters snapped to the standard diamond-turning series (Phi 16 / 25.4 / 30 mm).
  * Edge thickness (ET) >= 1.2 mm to eliminate knife edges and chipping.
  * Barrel core stack length-to-diameter ratio L/D <= 2.5 for a rigid, mountable handpiece.
"""

from typing import Any, Dict, List, Optional, Tuple

from core.zos_session import ZOSSession
from core.operation_guard import serialized_operation
from core.operands import _operand

# Flat land ground outside the clear aperture (mm) before the seat diameter.
FLAT_LAND_MM = 1.0
# Standard outer diameter series (mm) used by diamond-turned lens barrels.
STANDARD_OD_SERIES_MM = (16.0, 25.4, 30.0)
# Minimum acceptable edge thickness (mm).
MIN_EDGE_THICKNESS_MM = 1.2
# Maximum acceptable barrel length / diameter ratio.
MAX_LD_RATIO = 2.5


def _surface_kind(surf) -> str:
    """Classify a surface as a lens front, a mirror, a coordinate break, or inert (air/blank)."""
    type_name = str(surf.TypeName).upper()
    if "COORDINATE" in type_name or "COORDBRK" in type_name:
        return "coordinate_break"
    if "MIRROR" in type_name or "MIRROR" in str(surf.Material).upper():
        return "mirror"
    if str(surf.Material).strip() == "":
        return "air"
    return "glass"


def _edge_thickness_mm(session, front: int, back: int) -> Optional[float]:
    """Edge thickness of the piece between `front` and `back`, evaluated at the smaller
    of the two semi-diameters: ET = CT + sag(back) - sag(front)."""
    lde = session.system.LDE
    s_front, s_back = lde.GetSurfaceAt(front), lde.GetSurfaceAt(back)
    ct = float(s_front.Thickness)
    if not (ct > 0):
        return None
    r = min(float(s_front.SemiDiameter), float(s_back.SemiDiameter))
    if not (r > 0):
        return None
    try:
        sag_front = _operand(session, "SSAG", front, 0.0, r, 0.0)
        sag_back = _operand(session, "SSAG", back, 0.0, r, 0.0)
    except Exception:
        return None
    return ct + sag_back - sag_front


def _standard_od(required_mm: float) -> float:
    """Smallest standard barrel OD at or above `required_mm`; rounded up when beyond the series."""
    for od in STANDARD_OD_SERIES_MM:
        if od >= required_mm - 1e-9:
            return od
    import math

    return math.ceil(required_mm)


def _collect_elements(session, first_surface: int, last_surface: int) -> List[Dict[str, Any]]:
    """Every refractive glass piece between the two surfaces, treated as front/back pair.

    `last_surface` is the last surface *before the image*, so a glass front at that index
    still pairs with its back surface at `last_surface + 1`; requiring `back <= last_surface`
    would drop the final element of the stack (and the only element of a singlet).
    """
    lde = session.system.LDE
    n_surf = int(lde.NumberOfSurfaces)
    elements: List[Dict[str, Any]] = []
    for i in range(first_surface, last_surface + 1):
        surf = lde.GetSurfaceAt(i)
        if _surface_kind(surf) != "glass":
            continue
        back = i + 1
        if back >= n_surf:
            continue
        ca = min(float(surf.SemiDiameter), float(lde.GetSurfaceAt(back).SemiDiameter)) * 2.0
        if not (ca > 0):
            continue
        elements.append({
            "front_surface": i,
            "back_surface": back,
            "ca_mm": ca,
            "center_thickness_mm": float(surf.Thickness),
            "edge_thickness_mm": _edge_thickness_mm(session, i, back),
            "material": str(surf.Material),
        })
    return elements


def zemax_generate_barrel_assembly(first_surface=1, last_surface=None, barrel_od_mm=None,
                                   wall_thickness_mm=3.0, flange_thickness_mm=5.0):
    """Generate a parametric barrel assembly for a lens stack.

    Each glass piece gets a seat whose inner diameter is its clear aperture plus the
    flat land margin; the barrel OD is the largest seat diameter plus two wall thicknesses,
    snapped up to the standard OD series (Phi 16 / 25.4 / 30 mm) unless the caller pins it.

    Args:
        first_surface: First surface of the stack (default 1).
        last_surface: Last surface of the stack (default: last surface before the image).
        barrel_od_mm: Barrel outer diameter. Defaults to the standard series value that
            fits the stack.
        wall_thickness_mm: Barrel wall thickness (default 3.0 mm).
        flange_thickness_mm: Flange thickness (default 5.0 mm).

    Returns:
        {"status": "success", "barrel_od_mm": ..., "barrel_length_mm": ..., "ld_ratio": ...,
         "elements": [{front_surface, back_surface, seat_bore_mm, seat_od_mm,
                       center_thickness_mm, edge_thickness_mm, spacer_mm, material}, ...],
         "warnings": [...]}
    """
    session = ZOSSession.get_instance()
    system = session.system

    if last_surface is None:
        last_surface = system.LDE.NumberOfSurfaces - 2

    pieces = _collect_elements(session, first_surface, last_surface)
    if not pieces:
        return {"status": "error",
                "message": f"No refractive glass elements between surfaces {first_surface} and {last_surface}."}

    warnings: List[str] = []
    elements: List[Dict[str, Any]] = []
    max_seat_od = 0.0
    bare_length = 0.0

    for piece in pieces:
        front, back = piece["front_surface"], piece["back_surface"]
        back_surf = system.LDE.GetSurfaceAt(back)
        # Air gap after this piece, up to the next surface (a spacer ring lives there).
        spacer_n = back + 1
        spacer = float(system.LDE.GetSurfaceAt(spacer_n).Thickness) if spacer_n <= last_surface else 0.0

        seat_bore = piece["ca_mm"] + 2.0 * FLAT_LAND_MM
        seat_od = seat_bore + 2.0 * wall_thickness_mm
        max_seat_od = max(max_seat_od, seat_od)
        bare_length += piece["center_thickness_mm"] + spacer

        et = piece["edge_thickness_mm"]
        if et is not None and et < MIN_EDGE_THICKNESS_MM:
            warnings.append(
                f"Surface {front}-{back}: edge thickness {et:.3f} mm is below the "
                f"{MIN_EDGE_THICKNESS_MM} mm floor (knife-edge / chipping risk)."
            )

        elements.append({
            "front_surface": front,
            "back_surface": back,
            "material": piece["material"],
            "seat_bore_mm": round(seat_bore, 3),
            "seat_od_mm": round(seat_od, 3),
            "center_thickness_mm": round(piece["center_thickness_mm"], 3),
            "edge_thickness_mm": round(et, 3) if et is not None else None,
            "spacer_mm": round(spacer, 3),
        })

    auto_od = _standard_od(max_seat_od)
    barrel_od = float(barrel_od_mm) if barrel_od_mm is not None else auto_od
    if barrel_od_mm is not None and barrel_od < max_seat_od - 1e-9:
        warnings.append(
            f"Requested barrel OD {barrel_od:.3f} mm is smaller than the required seat "
            f"diameter {max_seat_od:.3f} mm; the bore will interfere."
        )
    if barrel_od_mm is None and auto_od > STANDARD_OD_SERIES_MM[-1]:
        warnings.append(
            f"Required seat diameter {max_seat_od:.3f} mm exceeds the standard OD series "
            f"{STANDARD_OD_SERIES_MM}; a custom outer diameter was used."
        )

    bare_length += flange_thickness_mm
    ld_ratio = bare_length / barrel_od if barrel_od > 0 else 0.0
    if ld_ratio > MAX_LD_RATIO:
        warnings.append(
            f"Barrel length/diameter ratio {ld_ratio:.2f} exceeds the {MAX_LD_RATIO} "
            f"limit (the barrel becomes flexure-sensitive)."
        )

    return {
        "status": "success",
        "barrel_od_mm": round(barrel_od, 3),
        "barrel_od_source": "standard_series" if barrel_od_mm is None else "user",
        "barrel_length_mm": round(bare_length, 3),
        "ld_ratio": round(ld_ratio, 3),
        "wall_thickness_mm": wall_thickness_mm,
        "flange_thickness_mm": flange_thickness_mm,
        "flat_land_mm": FLAT_LAND_MM,
        "elements": elements,
        "num_elements": len(elements),
        "warnings": warnings,
    }


zemax_generate_barrel_assembly = serialized_operation(zemax_generate_barrel_assembly)


def zemax_compute_optomech_spacing(element_surfaces, target_clearance_mm=0.5):
    """Compute optomechanical spacing requirements for lens elements.

    Edge thickness is taken from the real surface sag at the seat diameter
    (ET = CT + sag_back - sag_front), not from the axial thickness, so the
    knife-edge verdict actually reflects the physical part.

    Args:
        element_surfaces: List of lens element FRONT surface indices.
        target_clearance_mm: Target radial clearance between the lens land and the
            bore (default 0.5 mm on diameter).

    Returns:
        {"status": "success", "elements": [{front_surface, back_surface, ca_mm,
         bore_mm, edge_thickness_mm, stress_risk}, ...], "warnings": [...]}
    """
    session = ZOSSession.get_instance()
    system = session.system
    lde = system.LDE
    last = int(lde.NumberOfSurfaces) - 1

    elements: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for surf_idx in element_surfaces:
        if not 0 < surf_idx < last:
            warnings.append(f"Surface {surf_idx} is outside the model; skipped.")
            continue
        surf = lde.GetSurfaceAt(surf_idx)
        if _surface_kind(surf) != "glass":
            warnings.append(f"Surface {surf_idx} carries no glass material; skipped.")
            continue
        back = surf_idx + 1
        ca_mm = min(float(surf.SemiDiameter), float(lde.GetSurfaceAt(back).SemiDiameter)) * 2.0
        et = _edge_thickness_mm(session, surf_idx, back)

        if et is None:
            stress_risk = "unknown"
            warnings.append(f"Surface {surf_idx}-{back}: edge thickness could not be solved.")
        elif et < MIN_EDGE_THICKNESS_MM:
            stress_risk = "high"
            warnings.append(
                f"Surface {surf_idx}-{back}: edge thickness {et:.3f} mm is below "
                f"{MIN_EDGE_THICKNESS_MM} mm."
            )
        elif et < 2.0:
            stress_risk = "medium"
        else:
            stress_risk = "low"

        elements.append({
            "front_surface": surf_idx,
            "back_surface": back,
            "material": str(surf.Material),
            "ca_mm": round(ca_mm, 3),
            "bore_mm": round(ca_mm + 2.0 * FLAT_LAND_MM, 3),
            "edge_thickness_mm": round(et, 3) if et is not None else None,
            "stress_risk": stress_risk,
        })

    return {
        "status": "success",
        "target_clearance_mm": target_clearance_mm,
        "flat_land_mm": FLAT_LAND_MM,
        "elements": elements,
        "warnings": warnings,
    }


zemax_compute_optomech_spacing = serialized_operation(zemax_compute_optomech_spacing)


def zemax_generate_mount_interface(mount_surface, interface_type="C-mount",
                                   back_focal_distance_mm=None):
    """Generate a standard mount interface specification (C-mount, SM1, etc.).

    Args:
        mount_surface: Surface where the mount attaches.
        interface_type: Mount standard ("C-mount", "SM1", "SM2", "RMS").
        back_focal_distance_mm: Optional back focal distance constraint.

    Returns:
        {"status": "success", "interface_type": ..., "thread_spec": ...,
         "flange_distance_mm": ..., "back_focal_distance_mm": ...}
    """
    system = ZOSSession.get_instance().system

    # Standard mount specifications
    mount_specs = {
        "C-mount": {
            "thread": "1 inch 32 TPI",
            "flange_distance_mm": 17.526,
            "od_mm": 25.4,
        },
        "SM1": {
            "thread": "1.035 inch-40 TPI",
            "flange_distance_mm": None,  # No flange
            "od_mm": 30.0,
        },
        "SM2": {
            "thread": "2.035 inch-40 TPI",
            "flange_distance_mm": None,
            "od_mm": 55.0,
        },
        "RMS": {
            "thread": "0.800 inch-36 TPI (20.32mm)",
            "flange_distance_mm": None,
            "od_mm": 20.32,
        },
    }

    if interface_type not in mount_specs:
        return {
            "status": "error",
            "message": f"Unknown interface type: {interface_type}. Available: {list(mount_specs.keys())}",
        }

    spec = mount_specs[interface_type]
    n_surf = int(system.LDE.NumberOfSurfaces)

    if not 0 <= mount_surface < n_surf:
        return {"status": "error",
                "message": f"mount_surface {mount_surface} is outside the model (0..{n_surf - 1})."}

    # Compute back focal distance if not provided
    if back_focal_distance_mm is None:
        # Distance from the mount surface to the image surface
        bfd = 0.0
        for i in range(mount_surface, n_surf - 1):
            bfd += float(system.LDE.GetSurfaceAt(i).Thickness)
        back_focal_distance_mm = bfd

    result = {
        "status": "success",
        "interface_type": interface_type,
        "thread_spec": spec["thread"],
        "flange_distance_mm": spec["flange_distance_mm"],
        "mount_od_mm": spec["od_mm"],
        "back_focal_distance_mm": round(float(back_focal_distance_mm), 3),
        "mount_surface": mount_surface,
    }
    flange = spec["flange_distance_mm"]
    if flange is not None and abs(float(back_focal_distance_mm) - flange) > 1e-3:
        result["note"] = (
            f"The computed back focal distance {float(back_focal_distance_mm):.3f} mm does not "
            f"match the {interface_type} flange distance {flange} mm; a spacing ring of "
            f"{abs(float(back_focal_distance_mm) - flange):.3f} mm is required."
        )
    return result


zemax_generate_mount_interface = serialized_operation(zemax_generate_mount_interface)
