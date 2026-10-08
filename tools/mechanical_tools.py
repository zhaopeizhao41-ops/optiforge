"""
Mechanical structure generation and assembly tools for optical systems.
Includes barrel generation, optomechanical spacing calculation, and mounting interface design.
"""

from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession
from core.operation_guard import serialized_operation


def zemax_generate_barrel_assembly(session, first_surface=1, last_surface=None, barrel_od_mm=None,
                                   wall_thickness_mm=3.0, flange_thickness_mm=5.0):
    """Generate parametric barrel assembly for lens stack.

    Args:
        first_surface: First lens surface (default 1).
        last_surface: Last lens surface (default: image surface - 1).
        barrel_od_mm: Barrel outer diameter (default: auto from max clear aperture + 2*wall).
        wall_thickness_mm: Barrel wall thickness (default 3.0 mm).
        flange_thickness_mm: Flange/spacer thickness (default 5.0 mm).

    Returns:
        {"status": "success", "barrel_od_mm": ..., "barrel_length_mm": ...,
         "elements": [{surface, od_mm, thickness_mm, spacer_mm, type}, ...]}
    """
    system = session.system

    if last_surface is None:
        last_surface = system.LDE.NumberOfSurfaces - 1

    # Collect lens elements and compute barrel dimensions
    elements = []
    max_ca = 0.0
    total_length = 0.0

    for i in range(first_surface, last_surface + 1):
        surf = system.LDE.GetSurfaceAt(i)
        ca = float(surf.SemiDiameter) * 2  # Clear aperture diameter
        thickness = float(surf.Thickness)
        surf_type = str(surf.TypeName)

        if ca > max_ca:
            max_ca = ca

        # Check if this is a lens element (has material)
        material = str(surf.Material)
        is_lens = material != "" and "MIRROR" not in surf_type.upper()

        if is_lens:
            # Next surface should be the exit surface
            next_surf = system.LDE.GetSurfaceAt(i + 1) if i + 1 <= last_surface else None
            if next_surf:
                element_thickness = float(surf.Thickness)
                spacer = float(next_surf.Thickness) if i + 2 <= last_surface else 0.0

                elements.append({
                    "surface": i,
                    "od_mm": round(ca + 2 * wall_thickness_mm, 3),
                    "thickness_mm": round(element_thickness, 3),
                    "spacer_mm": round(spacer, 3),
                    "type": "lens",
                    "material": material,
                })

                total_length += element_thickness + spacer

    # Compute barrel OD if not specified
    if barrel_od_mm is None:
        barrel_od_mm = max_ca + 2 * wall_thickness_mm

    return {
        "status": "success",
        "barrel_od_mm": round(barrel_od_mm, 3),
        "barrel_length_mm": round(total_length, 3),
        "wall_thickness_mm": wall_thickness_mm,
        "flange_thickness_mm": flange_thickness_mm,
        "elements": elements,
        "num_elements": len(elements),
    }


zemax_generate_barrel_assembly = serialized_operation(zemax_generate_barrel_assembly)


def zemax_compute_optomech_spacing(session, element_surfaces, target_clearance_mm=0.5):
    """Compute optomechanical spacing requirements for lens elements.

    Args:
        element_surfaces: List of lens element surface indices.
        target_clearance_mm: Target clearance between lens edge and barrel (default 0.5 mm).

    Returns:
        {"status": "success", "elements": [{surface, ca_mm, edge_thickness_mm,
         mounting_clearance_mm, stress_risk}, ...]}
    """
    system = session.system

    elements = []

    for surf_idx in element_surfaces:
        surf = system.LDE.GetSurfaceAt(surf_idx)
        ca_mm = float(surf.SemiDiameter) * 2
        thickness = float(surf.Thickness)

        # Compute edge thickness
        # This is simplified - would need ray trace to exact edge point
        edge_thickness_mm = thickness  # Placeholder

        # Check mounting clearance
        mounting_clearance = ca_mm / 2 + target_clearance_mm

        # Assess stress risk based on edge thickness
        stress_risk = "low"
        if edge_thickness_mm < 1.5:
            stress_risk = "high"
        elif edge_thickness_mm < 3.0:
            stress_risk = "medium"

        elements.append({
            "surface": surf_idx,
            "ca_mm": round(ca_mm, 3),
            "edge_thickness_mm": round(edge_thickness_mm, 3),
            "mounting_clearance_mm": round(mounting_clearance, 3),
            "stress_risk": stress_risk,
        })

    return {
        "status": "success",
        "target_clearance_mm": target_clearance_mm,
        "elements": elements,
    }


zemax_compute_optomech_spacing = serialized_operation(zemax_compute_optomech_spacing)


def zemax_generate_mount_interface(session, mount_surface, interface_type="C-mount",
                                   back_focal_distance_mm=None):
    """Generate standard mount interface specification (C-mount, SM1, etc.).

    Args:
        mount_surface: Surface where mount attaches.
        interface_type: Mount standard ("C-mount", "SM1", "SM2", "RMS").
        back_focal_distance_mm: Optional back focal distance constraint.

    Returns:
        {"status": "success", "interface_type": ..., "thread_spec": ...,
         "flange_distance_mm": ..., "back_focal_distance_mm": ...}
    """
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
            "message": f"Unknown interface type: {interface_type}. Available: {list(mount_specs.keys())}"
        }

    spec = mount_specs[interface_type]

    # Compute back focal distance if not provided
    system = session.system
    if back_focal_distance_mm is None:
        # Distance from mount surface to image
        bfd = 0.0
        img_surf = system.LDE.NumberOfSurfaces - 1
        for i in range(mount_surface, img_surf):
            bfd += float(system.LDE.GetSurfaceAt(i).Thickness)
        back_focal_distance_mm = bfd

    return {
        "status": "success",
        "interface_type": interface_type,
        "thread_spec": spec["thread"],
        "flange_distance_mm": spec["flange_distance_mm"],
        "mount_od_mm": spec["od_mm"],
        "back_focal_distance_mm": round(back_focal_distance_mm, 3),
        "mount_surface": mount_surface,
    }


zemax_generate_mount_interface = serialized_operation(zemax_generate_mount_interface)
