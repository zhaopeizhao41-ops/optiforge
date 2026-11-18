"""
Zemax CAD Export & Optical Drawing Tools
Enables export of native 3D CAD files (STEP, IGES, SAT, STL) and ISO 10110 optical manufacturing drawings,
providing direct optomechanical linkage with SolidWorks MCP.
"""

import copy
import json
import math
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple, Union
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

# Configure Chinese typography for GB/T 13323-2009 national standard drawings
plt.rcParams["font.sans-serif"] = ["STSong", "Microsoft YaHei", "SimHei", "SimSun", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

from core.zos_session import ZOSSession
from core.operation_guard import serialized_operation
from core.analysis_runner import close_tool, run_tool_bounded
from tools.project_manager import (
    get_project_dir,
    resolve_project_directory_path,
    resolve_project_file_path,
    get_active_project_name,
    set_active_project,
)

try:
    import ezdxf
    from ezdxf import units
    from ezdxf.enums import TextEntityAlignment
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import (
        Configuration,
        BackgroundPolicy,
        ColorPolicy,
        LineweightPolicy,
    )
    EZDXF_AVAILABLE = True
except ImportError:
    EZDXF_AVAILABLE = False


# Large assemblies with traced rays can take minutes; a stuck export must not hold the lock.
_CAD_EXPORT_TIMEOUT_S = 600.0


def _render_dxf_to_png(dxf_filepath: str, png_filepath: str, dpi: int = 450) -> bool:
    """
    Render a high-contrast monochrome CAD drawing preview from DXF modelspace
    using ezdxf's matplotlib backend, ensuring 1:1 fidelity with native DXF.
    """
    if not EZDXF_AVAILABLE:
        return False
    try:
        doc = ezdxf.readfile(dxf_filepath)
        msp = doc.modelspace()
        fig = plt.figure(figsize=(11.69, 8.27), dpi=dpi)
        ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
        ctx = RenderContext(doc)
        cfg = Configuration(
            background_policy=BackgroundPolicy.WHITE,
            color_policy=ColorPolicy.BLACK,
            lineweight_policy=LineweightPolicy.ABSOLUTE,
            min_lineweight=0.14,
            lineweight_scaling=1.85,
        )
        out = MatplotlibBackend(ax, adjust_figure=False)
        frontend = Frontend(ctx, out, config=cfg)
        frontend.draw_layout(msp, finalize=True)
        ax.set_xlim(-1.5, 298.5)
        ax.set_ylim(-1.5, 211.5)
        ax.set_aspect("equal")
        fig.savefig(png_filepath, dpi=dpi, facecolor="white", bbox_inches="tight", pad_inches=0.04)
        plt.close(fig)
        return True
    except Exception as e:
        print(f"Warning: Failed to render DXF to PNG ({e}), falling back to native matplotlib renderer.")
        return False



def zemax_export_cad(
    filepath: Optional[str] = None,
    project_name: Optional[str] = None,
    file_type: str = "STEP",
    surfaces_as_solids: bool = True,
    first_surface: Optional[int] = None,
    last_surface: Optional[int] = None,
    export_dummy_surfaces: bool = False,
    dummy_thickness: float = 0.0,
    export_rays: bool = False,
    num_rays: int = 1,
    wavelength_index: int = 0,
    field_index: int = 0,
    spline_segments: int = 32,
    tolerance: float = 0.001,
) -> Dict[str, Any]:
    """
    Export current Zemax optical design to a standard 3D CAD file (STEP, IGES, SAT, STL).
    Specially optimized for SolidWorks MCP linkage: exports solid bodies that can be directly
    opened and assembled in SolidWorks.

    Args:
        filepath: Destination file path. If None, saves to 'output/<project_name>/cad/<system_name>.<ext>'.
        project_name: Optional target project name to associate with this export.
        file_type: CAD format ('STEP', 'IGES', 'SAT', 'STL'). Default is 'STEP' (ISO 10303).
        surfaces_as_solids: If True, exports closed volumes as solid bodies (required for SolidWorks assembly).
        first_surface: First surface to export (1-based). Default is the first glass surface.
        last_surface: Last surface to export. Default is the image surface, or the surface where
            an immersion medium (water/oil) starts, so the liquid is not exported as a solid.
        export_dummy_surfaces: Whether to export zero-thickness dummy surfaces / stops.
        dummy_thickness: Virtual thickness assigned to dummy surfaces if exported.
        export_rays: If True, traces and exports marginal/chief ray geometry into the CAD file.
        num_rays: Number of rays across the pupil when export_rays is True.
        wavelength_index: Wavelength to trace (0 = all).
        field_index: Field to trace (0 = all).
        spline_segments: Number of spline segments for surface curve interpolation (default 32).
        tolerance: Chordal tolerance for NURBS / spline tessellation (mm).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    num_surfs = sys.LDE.NumberOfSurfaces
    if num_surfs < 4:
        return {
            "status": "error",
            "message": "Optical system too simple for export (need at least one lens). Load a valid design with zemax_load_file.",
        }

    if project_name:
        set_active_project(project_name)
    active_proj = get_active_project_name()
    default_first, default_last = _default_cad_surface_range(sys)
    f_surf = int(first_surface) if first_surface is not None else default_first
    l_surf = int(last_surface) if last_surface is not None else default_last

    f_surf = max(1, min(f_surf, num_surfs))
    l_surf = max(f_surf, min(l_surf, num_surfs))

    ft_lower = file_type.strip().lower()
    ext_map = {
        "step": ".step",
        "stp": ".step",
        "iges": ".igs",
        "igs": ".igs",
        "sat": ".sat",
        "stl": ".stl",
    }
    target_ext = ext_map.get(ft_lower, ".step")

    ft_enum_map = {
        "step": zos.Tools.General.CADFileType.STEP,
        "stp": zos.Tools.General.CADFileType.STEP,
        "iges": zos.Tools.General.CADFileType.IGES,
        "igs": zos.Tools.General.CADFileType.IGES,
        "sat": zos.Tools.General.CADFileType.SAT,
        "stl": zos.Tools.General.CADFileType.STL,
    }
    cad_enum = ft_enum_map.get(ft_lower, zos.Tools.General.CADFileType.STEP)

    base_name = active_proj
    if session.current_filepath:
        base_name = os.path.splitext(os.path.basename(session.current_filepath))[0]
    target_path = resolve_project_file_path(
        filepath,
        default_filename=f"{base_name}{target_ext}",
        subfolder="cad",
        project_name=active_proj,
    )

    cad_tool = session.open_tool("OpenExportCAD")
    try:
        cad_tool.FileType = cad_enum
        cad_tool.SurfacesAsSolids = bool(surfaces_as_solids)
        cad_tool.FirstSurface = f_surf
        cad_tool.LastSurface = l_surf
        cad_tool.ExportDummySurfaces = bool(export_dummy_surfaces)
        if dummy_thickness > 0:
            cad_tool.DummyThickness = float(dummy_thickness)

        # Map SplineSegments enum safely
        if spline_segments <= 16:
            cad_tool.SplineSegments = zos.Tools.General.SplineSegmentsType.N_016
        elif spline_segments <= 32:
            cad_tool.SplineSegments = zos.Tools.General.SplineSegmentsType.N_032
        elif spline_segments <= 64:
            cad_tool.SplineSegments = zos.Tools.General.SplineSegmentsType.N_064
        elif spline_segments <= 128:
            cad_tool.SplineSegments = zos.Tools.General.SplineSegmentsType.N_128
        elif spline_segments <= 256:
            cad_tool.SplineSegments = zos.Tools.General.SplineSegmentsType.N_256
        else:
            cad_tool.SplineSegments = zos.Tools.General.SplineSegmentsType.N_512

        # Map CADToleranceType enum safely
        if tolerance >= 1e-4:
            cad_tool.Tolerance = zos.Tools.General.CADToleranceType.N_TenEMinus4
        elif tolerance >= 1e-5:
            cad_tool.Tolerance = zos.Tools.General.CADToleranceType.N_TenEMinus5
        elif tolerance >= 1e-6:
            cad_tool.Tolerance = zos.Tools.General.CADToleranceType.N_TenEMinus6
        else:
            cad_tool.Tolerance = zos.Tools.General.CADToleranceType.N_TenEMinus7

        if export_rays:
            cad_tool.NumberOfRays = int(num_rays)
            if wavelength_index > 0:
                cad_tool.Wavelength = int(wavelength_index)
            else:
                cad_tool.SetWavelengthAll()
            if field_index > 0:
                cad_tool.Field = int(field_index)
            else:
                cad_tool.SetFieldAll()
        else:
            cad_tool.NumberOfRays = 0

        cad_tool.OutputFileName = target_path
        if run_tool_bounded(cad_tool, _CAD_EXPORT_TIMEOUT_S):
            succeeded, error_msg = False, f"CAD 导出超过 {_CAD_EXPORT_TIMEOUT_S:g} s 未完成，已取消"
        else:
            succeeded = bool(cad_tool.Succeeded)
            error_msg = str(cad_tool.ErrorMessage) if cad_tool.ErrorMessage else None
    finally:
        close_tool(cad_tool)

    if not succeeded or not os.path.exists(target_path):
        return {
            "status": "error",
            "message": f"CAD Export failed: {error_msg or 'File was not generated.'}",
            "target_path": target_path,
        }

    cad_bodies = None
    if target_ext == ".step":
        cad_bodies = _postprocess_step(target_path, _cad_part_names(sys), base_name)
        geometry_count = cad_bodies["solids"] if surfaces_as_solids else cad_bodies["faces"]
        if geometry_count == 0:
            os.remove(target_path)
            return {
                "status": "error",
                "message": (
                    f"CAD export produced no {'solid bodies' if surfaces_as_solids else 'faces'} "
                    f"for surfaces {f_surf}-{l_surf}; the empty file was removed. "
                    "Check that the design is loaded and the surface range covers lens elements."
                ),
                "target_path": target_path,
            }

    file_size = os.path.getsize(target_path)

    # Prepare SolidWorks MCP linkage advice
    solidworks_command = {
        "action": "open_solidworks_document",
        "arguments": {
            "file_path": target_path,
            "doc_type": "assembly",
        },
    }

    return {
        "status": "success",
        "file_path": target_path,
        "format": file_type.upper(),
        "file_size_bytes": file_size,
        "surfaces_exported": {"first_surface": f_surf, "last_surface": l_surf},
        "surfaces_as_solids": surfaces_as_solids,
        "rays_included": export_rays,
        "cad_bodies": cad_bodies,
        "solidworks_mcp_linkage": {
            "recommended_tool": "open_solidworks_document",
            "example_call": solidworks_command,
            "notes": "File is ready to be opened in SolidWorks for lens barrel enclosure, retaining ring, and spacer design.",
        },
    }


NON_GLASS_MEDIA = {
    "", "air", "water", "h2o", "pure_water", "oil", "immersion_oil",
    "glycerol", "glycerin", "vacuum", "none"
}
# A reflective surface is not a refractive medium: it never starts or joins a lens element.
MIRROR_MEDIA = {"mirror"}


def _is_lens_medium(material: str) -> bool:
    key = material.strip().lower()
    return key not in NON_GLASS_MEDIA and key not in MIRROR_MEDIA


def _mechanical_semi_diameter(surface) -> float:
    """Lens-edge semi-diameter of a surface: the mechanical semi-diameter when the model
    defines one, otherwise the clear semi-diameter (older files, mocks)."""
    clear = float(surface.SemiDiameter)
    try:
        mech = float(surface.MechanicalSemiDiameter)
    except (AttributeError, TypeError, ValueError):
        return clear
    return mech if math.isfinite(mech) and mech > clear else clear


def _extract_lens_elements(sys) -> List[Dict[str, Any]]:
    """Helper to parse sequential LDE surfaces into discrete optical elements."""
    num_surfs = sys.LDE.NumberOfSurfaces
    elements = []
    i = 1
    elem_idx = 1

    while i < num_surfs:
        surf = sys.LDE.GetSurfaceAt(i)
        mat = str(surf.Material).strip()

        # If current surface has glass material, it is the front of an optical element
        if _is_lens_medium(mat):
            s_start = i
            # Look ahead for cemented interfaces or rear surface
            components = []
            cur_s = i
            while cur_s < num_surfs:
                s_obj = sys.LDE.GetSurfaceAt(cur_s)
                m = str(s_obj.Material).strip()
                r_front = float(s_obj.Radius)
                thick = float(s_obj.Thickness)
                sd_front = float(s_obj.SemiDiameter)
                msd_front = _mechanical_semi_diameter(s_obj)
                conic = float(s_obj.Conic)
                stype = str(s_obj.Type)

                next_s = cur_s + 1
                if next_s <= num_surfs:
                    s_rear_obj = sys.LDE.GetSurfaceAt(next_s)
                    r_rear = float(s_rear_obj.Radius)
                    sd_rear = float(s_rear_obj.SemiDiameter)
                    msd_rear = _mechanical_semi_diameter(s_rear_obj)
                    k_rear = float(s_rear_obj.Conic)
                    m_next = str(s_rear_obj.Material).strip()
                else:
                    r_rear = 0.0
                    sd_rear = sd_front
                    msd_rear = msd_front
                    k_rear = 0.0
                    m_next = ""

                components.append({
                    "surface_front": cur_s,
                    "surface_rear": next_s,
                    "material": m,
                    "thickness": thick,
                    "radius_front": r_front,
                    "radius_rear": r_rear,
                    "semi_diameter_front": sd_front,
                    "semi_diameter_rear": sd_rear,
                    "mech_semi_diameter_front": msd_front,
                    "mech_semi_diameter_rear": msd_rear,
                    "conic_front": conic,
                    "conic_rear": k_rear,
                    "surface_type": stype,
                })

                # If next surface has another glass, it's a cemented doublet/triplet
                if _is_lens_medium(m_next) and next_s < num_surfs:
                    cur_s = next_s
                else:
                    s_end = next_s
                    break

            elem_type = "single_lens"
            if len(components) == 2:
                elem_type = "cemented_doublet"
            elif len(components) > 2:
                elem_type = "cemented_multiplet"
            elif components[0]["radius_front"] == 0 and components[0]["radius_rear"] == 0:
                elem_type = "optical_window"

            # Compute element-level envelope
            max_sd = max(
                max(c["semi_diameter_front"], c["semi_diameter_rear"]) for c in components
            )
            total_ct = sum(c["thickness"] for c in components)

            elements.append({
                "element_index": elem_idx,
                "element_type": elem_type,
                "surface_start": s_start,
                "surface_end": s_end,
                "components": components,
                "max_semi_diameter": max_sd,
                "total_center_thickness": total_ct,
            })
            elem_idx += 1
            i = s_end
        else:
            i += 1

    return elements


IMMERSION_MEDIA = NON_GLASS_MEDIA - {"", "air", "vacuum", "none"}


def _default_cad_surface_range(sys) -> Tuple[int, int]:
    """First glass surface up to the start of an immersion medium (else the image).

    A water/oil gap exported as a solid clutters the CAD assembly and hides the
    working distance, so it is excluded unless last_surface is given explicitly.
    """
    num_surfs = int(sys.LDE.NumberOfSurfaces)
    materials = [str(sys.LDE.GetSurfaceAt(i).Material).strip().lower() for i in range(num_surfs)]
    first = next((i for i in range(1, num_surfs - 1) if materials[i] not in NON_GLASS_MEDIA), 1)
    last = next((i for i in range(first + 1, num_surfs - 1) if materials[i] in IMMERSION_MEDIA),
                num_surfs - 1)
    return first, last


def _step_name(text: str) -> str:
    """ASCII name without quotes, safe inside a STEP string and as a CAD file name."""
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", text).strip("_") or "part"


def _cad_part_names(sys) -> Dict[Tuple[int, int], str]:
    """Map the ZOS-API 'surfaces a,b' CAD bodies to lens-based part names."""
    names = {}
    for elem in _extract_lens_elements(sys):
        comps = elem["components"]
        tag = f"L{elem['element_index']:02d}"
        start, end = elem["surface_start"], elem["surface_end"]
        names[(start, end)] = f"{tag}_{'_'.join(c['material'] for c in comps)}_S{start}-{end}"
        if len(comps) > 1:
            for n, c in enumerate(comps):
                front, rear = c["surface_front"], c["surface_rear"]
                names[(front, rear)] = f"{tag}{chr(ord('a') + n)}_{c['material']}_S{front}-{rear}"
    for i in range(1, int(sys.LDE.NumberOfSurfaces) - 1):
        mat = str(sys.LDE.GetSurfaceAt(i).Material).strip()
        if mat.lower() in IMMERSION_MEDIA:
            names[(i, i + 1)] = f"MEDIUM_{mat.upper()}_S{i}-{i + 1}"
    return {key: _step_name(name) for key, name in names.items()}


_STEP_ENTITY = re.compile(r"^#(\d+)\s*=\s*([A-Z0-9_]+)\s*\((.*)\)\s*;\s*$")
_STEP_REF = re.compile(r"#(\d+)")
_STEP_SURFACES = re.compile(r"surfaces\s+(\d+)\s*,\s*(\d+)", re.IGNORECASE)
_STEP_PRODUCT_NAMES = re.compile(r"^(#\d+\s*=\s*PRODUCT\s*\()'(?:[^']|'')*'\s*,\s*'(?:[^']|'')*'")


def _postprocess_step(path: str, part_names: Dict[Tuple[int, int], str], base_name: str) -> Dict[str, Any]:
    """Count the exported geometry and name the anonymous ZOS-API products.

    ZOS-API writes every PRODUCT as '0', which SolidWorks shows as components
    0-1, 0-2, ... Each body is found via NAUO -> PRODUCT_DEFINITION ->
    formation -> PRODUCT and renamed after its lens element.
    """
    with open(path, "r", encoding="latin-1", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)

    text = "".join(lines)
    solids = len(re.findall(r"\b(?:MANIFOLD_SOLID_BREP|BREP_WITH_VOIDS)\s*\(", text))
    faces = len(re.findall(r"\b(?:ADVANCED_FACE|FACE_SURFACE)\s*\(", text))

    entities = {}  # id -> (line index, entity type, argument text)
    for idx, line in enumerate(lines):
        m = _STEP_ENTITY.match(line)
        if m:
            entities[m.group(1)] = (idx, m.group(2), m.group(3))

    def product_of(pd_id):
        for _ in range(2):  # PRODUCT_DEFINITION -> formation -> PRODUCT
            ent = entities.get(pd_id)
            ref = _STEP_REF.search(ent[2]) if ent else None
            if not ref:
                return None
            pd_id = ref.group(1)
        return pd_id if entities.get(pd_id, (0, ""))[1] == "PRODUCT" else None

    renames, parts = {}, []
    relating, related = set(), set()
    for _, etype, args in entities.values():
        refs = _STEP_REF.findall(args)
        if etype != "NEXT_ASSEMBLY_USAGE_OCCURRENCE" or len(refs) < 2:
            continue
        relating.add(refs[0])
        related.add(refs[1])
        surfaces = _STEP_SURFACES.search(args)
        product = product_of(refs[1])
        if surfaces and product and product not in renames:
            a, b = int(surfaces.group(1)), int(surfaces.group(2))
            renames[product] = part_names.get((a, b), f"S{a}-{b}")
            parts.append(renames[product])
    root_name = _step_name(base_name)
    for pd_id in relating - related:
        product = product_of(pd_id)
        if product and product not in renames:
            renames[product] = root_name
    # ZOS inserts a sub-assembly named after the .zmx file between root and lenses
    for pd_id in relating & related:
        product = product_of(pd_id)
        if product and product not in renames:
            renames[product] = f"{root_name}_lenses"

    for product, name in renames.items():
        idx = entities[product][0]
        lines[idx] = _STEP_PRODUCT_NAMES.sub(
            lambda m: f"{m.group(1)}'{name}','{name}'", lines[idx], count=1)
    if renames:
        with open(path, "w", encoding="latin-1", newline="") as fh:
            fh.writelines(lines)
    return {"solids": solids, "faces": faces, "part_names": parts}


def _system_drawing_metadata(sys, zos, elements, image_surface: Optional[int] = None) -> Dict[str, Any]:
    """Read shared optical facts once; distances are not interchangeable.

    image_surface ends the drawn range early (a group in front of an intermediate image);
    the rear gap is then measured to that surface instead of the final image."""
    waves = sys.SystemData.Wavelengths
    wavelengths_nm = [float(waves.GetWavelength(i).Wavelength) * 1000.0
                      for i in range(1, int(waves.NumberOfWavelengths) + 1)]
    first = elements[0]["surface_start"]
    rear = elements[-1]["surface_end"]
    image = int(sys.LDE.NumberOfSurfaces) - 1
    if image_surface is not None:
        image = max(rear, min(image, int(image_surface)))

    def thickness_sum(start, end):
        values = [float(sys.LDE.GetSurfaceAt(i).Thickness) for i in range(start, end)]
        return sum(values) if all(math.isfinite(v) and abs(v) < 1e10 for v in values) else None

    track = float(sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.TOTR, 0, 0, 0, 0, 0, 0, 0, 0))
    track = track if math.isfinite(track) else None

    # A thickness sum is a physical axial separation only in a centered,
    # forward-going system. Do not label folds/decenters as mechanical distances.
    centered = True
    for i in range(image):
        surface = sys.LDE.GetSurfaceAt(i)
        td = surface.TiltDecenterData
        transforms = [float(getattr(td, f"{side}Surface{kind}{axis}"))
                      for side in ("Before", "After")
                      for kind, axes in (("Decenter", "XY"), ("Tilt", "XYZ"))
                      for axis in axes]
        thickness = float(surface.Thickness)
        if (str(surface.Type) not in {"Standard", "EvenAspheric", "OddAspheric", "Paraxial"}
                or str(surface.Material).upper() == "MIRROR"
                or any(v != 0 for v in transforms) or thickness < 0
                or (i > 0 and not math.isfinite(thickness))):
            centered = False

    if image_surface is not None and centered:
        # a group drawn on its own: length from its first vertex to the end of the range
        group_track = thickness_sum(first, image)
        track = group_track if group_track is not None else track
    stack = thickness_sum(first, rear) if centered else None
    rear_gap = thickness_sum(rear, image) if centered else None
    object_t = float(sys.LDE.GetSurfaceAt(0).Thickness)
    infinite_object = object_t > 1e10
    wd = thickness_sum(0, first) if centered and not infinite_object else None
    wd_str = "Infinity (object at infinity)" if infinite_object else f"{wd:.4f}" if wd is not None else "Not determined"
    return {
        "drawing_date": date.today().strftime("%Y/%m/%d"),
        "wavelengths_nm": wavelengths_nm,
        "design_wavelengths": ", ".join(f"{w:g}" for w in wavelengths_nm) + " nm",
        "spectral_range": f"{min(wavelengths_nm):g} ~ {max(wavelengths_nm):g} nm" if wavelengths_nm else "Not specified",
        "total_track_length_mm": track,
        "lens_stack_length_mm": stack,
        "rear_image_gap_mm": rear_gap,
        "object_working_distance_mm": wd,
        "object_at_infinity": infinite_object,
        "distance_basis": "Centered sequential axial distances" if centered else "Mechanical distances not determined for this geometry",
        "totr_str": f"{track:.4f}" if track is not None else "Not determined",
        "stack_str": f"{stack:.4f}" if stack is not None else "Not determined",
        "rear_gap_str": f"{rear_gap:.4f}" if rear_gap is not None else "Not determined",
        "wd_str": wd_str,
    }


def _element_coating(sys, element) -> str:
    surfaces = range(element["surface_start"], element["surface_end"] + 1)
    return "; ".join(f"S{i}: {str(sys.LDE.GetSurfaceAt(i).Coating).strip() or 'Not specified'}"
                     for i in surfaces)


_COMMON_OPTICAL_GLASS_ND: Dict[str, float] = {
    # CDGM
    "H-K9L": 1.51680, "H-K9": 1.51680, "K9": 1.51630,
    "H-FK61": 1.49700, "H-FK71": 1.45650,
    "H-ZK9B": 1.62041, "H-BAK7": 1.56883,
    "H-LAK2": 1.67790, "H-LAK7A": 1.65160, "H-LAK53A": 1.73800,
    "H-LAF2": 1.74400, "H-LAF3B": 1.71700, "H-LAF50B": 1.77250,
    "H-ZLAF50D": 1.80400, "H-ZLAF68C": 1.81600,
    "H-ZBAF52": 1.65844,
    "H-F1": 1.60562, "H-F4": 1.61659,
    "H-ZF1": 1.64769, "H-ZF2": 1.67270, "H-ZF4A": 1.72825,
    "H-ZF6": 1.75520, "H-ZF7L": 1.80610, "H-ZF13": 1.78472,
    "H-ZF52": 1.84666, "H-ZF52A": 1.84666, "H-ZF62": 1.92286,
    "H-ZF73": 1.94595,
    # SCHOTT
    "N-BK7": 1.51680, "BK7": 1.51680, "N-PK52A": 1.49700,
    "N-BAK4": 1.56883, "N-SK16": 1.62041, "SK16": 1.62041,
    "N-BAF10": 1.67003, "BAF10": 1.67003,
    "N-LAK9": 1.69100, "N-LAF21": 1.78800, "N-LASF44": 1.80420,
    "N-F2": 1.62004, "F2": 1.62004,
    "N-SF2": 1.64769, "SF2": 1.64769,
    "N-SF5": 1.67270, "SF5": 1.67270,
    "N-SF6": 1.80518, "SF6": 1.80518,
    "N-SF10": 1.72825, "SF10": 1.72825,
    "N-SF11": 1.78472, "SF11": 1.78472,
    "N-SF57": 1.84666, "N-SF66": 1.92286,
    # OHARA
    "S-BSL7": 1.51633, "S-FPL51": 1.49700, "S-FPL53": 1.43875,
    "S-BAL35": 1.58913, "S-LAL14": 1.69680, "S-LAH64": 1.78800,
    "S-TIM2": 1.62004, "S-TIH6": 1.80518, "S-TIH53": 1.84666,
    # UV / IR / Crystals / Polymers
    "F_SILICA": 1.45846, "SILICA": 1.45846, "FSILICA": 1.45846,
    "JGS1": 1.45846, "JGS2": 1.45846,
    "CAF2": 1.43384, "MGFL2": 1.37770, "MGF2": 1.37770,
    "SAPPHIRE": 1.76820,
    "ZNSE": 2.67080, "ZNS": 2.29230, "GE": 4.00320, "SI": 3.42230,
    "PMMA": 1.49176, "POLYCARB": 1.58547, "COP": 1.53116, "ZEONEX": 1.53116,
}


def _lookup_material_nd(sys, zos, surf_idx: int, mat_name: str, primary_wave: int) -> Optional[float]:
    """Resolve refractive index n_d via ZOS-API INDX operand when available, else built-in table."""
    if not mat_name or not _is_lens_medium(mat_name):
        return 1.0
    try:
        val = float(
            sys.MFE.GetOperandValue(
                zos.Editors.MFE.MeritOperandType.INDX, int(surf_idx), int(primary_wave), 0, 0, 0, 0, 0, 0
            )
        )
        if 1.05 < val < 5.0 and math.isfinite(val):
            return val
    except Exception:
        pass
    norm = mat_name.strip().upper()
    norm_clean = re.sub(r"[^A-Z0-9\-]", "", norm)
    for candidate in (norm, norm_clean, norm_clean.lstrip("H-"), f"N-{norm_clean}"):
        if candidate in _COMMON_OPTICAL_GLASS_ND:
            return _COMMON_OPTICAL_GLASS_ND[candidate]
    return None


def _compute_element_paraxial_efl_bfl(
    sys, zos, element: Dict[str, Any], primary_wave: int
) -> Tuple[Optional[float], Optional[float]]:
    """Compute paraxial EFL (f) and rear vertex BFL (Sf) in air for a singlet or cemented group."""
    comps = element.get("components") or []
    if not comps:
        return None, None
    y = 1.0
    nu = 0.0
    n_prev = 1.0
    for comp in comps:
        n_glass = _lookup_material_nd(sys, zos, comp["surface_front"], comp["material"], primary_wave)
        if n_glass is None:
            return None, None
        r_f = float(comp["radius_front"])
        phi_f = (n_glass - n_prev) / r_f if (0.0 < abs(r_f) < 1e8) else 0.0
        nu -= y * phi_f
        y += float(comp["thickness"]) * (nu / n_glass)
        n_prev = n_glass
    r_r = float(comps[-1]["radius_rear"])
    phi_r = (1.0 - n_prev) / r_r if (0.0 < abs(r_r) < 1e8) else 0.0
    nu -= y * phi_r
    power = -nu
    if abs(power) < 1e-8 or not math.isfinite(power):
        return None, None
    efl = 1.0 / power
    bfl = y / power
    if abs(efl) > 1e6 or abs(bfl) > 1e6:
        return None, None
    return round(efl, 2), round(bfl, 2)


def _format_chinese_coating_note(sys, element: Dict[str, Any], spectral_range: str) -> str:
    """Format pure Chinese optical coating specification from outer surfaces without 'Not specified'."""
    s_start = int(element["surface_start"])
    s_end = int(element["surface_end"])
    spec_txt = spectral_range.replace(" ", "") if spectral_range and "Not" not in spectral_range else "工作波段"
    outer_coatings: List[str] = []
    for s_idx in (s_start, s_end):
        try:
            c = str(sys.LDE.GetSurfaceAt(s_idx).Coating).strip()
            if c and c.upper() != "NONE":
                outer_coatings.append(c)
        except Exception:
            pass
    unique_c = list(dict.fromkeys(outer_coatings))
    if not unique_c:
        return f"通光面镀宽带增透膜，透过率≥99%@{spec_txt}"
    joined = "/".join(unique_c)
    if any(k in joined.upper() for k in ("HR", "REFL", "MIRROR")):
        return f"通光面镀高反膜（{joined}）@{spec_txt}"
    if any(c.upper().startswith("I.") for c in unique_c):
        return f"通光面镀膜（{joined}），透过率满足设计要求@{spec_txt}"
    return f"通光面镀增透膜（{joined}），透过率≥99%@{spec_txt}"


def _ring_dimensions(clear_aperture: float, outer_diameter: float) -> Dict[str, float]:
    """Conservative aperture envelope, with 0.1 radial clearance and 0.5 wall.

    This only checks aperture/fit geometry, not the traced beam through a finite
    thickness ring. Never enlarge the OD beyond its mating lens/bore to force fit.
    """
    if not all(math.isfinite(v) and v > 0 for v in (clear_aperture, outer_diameter)):
        raise ValueError("Ring aperture and OD must be finite and positive.")
    inner = math.ceil((clear_aperture + 0.2) * 100.0 - 1e-9) / 100.0
    wall = (outer_diameter - inner) / 2.0
    if wall < 0.5:
        raise ValueError(f"No supported ring fits: aperture={clear_aperture:g}, OD={outer_diameter:g}, required ID={inner:g} mm and radial wall >= 0.5 mm.")
    return {"inner_diameter_mm": inner, "outer_diameter_mm": outer_diameter,
            "aperture_envelope_mm": clear_aperture, "radial_wall_mm": round(wall, 4)}


def _calculate_sag(radius: float, conic: float, y: float) -> float:
    """Calculate exact surface sag z(y)."""
    if radius == 0.0 or abs(radius) > 1e10:
        return 0.0
    c = 1.0 / radius
    arg = 1.0 - (1.0 + conic) * (c**2) * (y**2)
    if arg < 0:
        return math.copysign(abs(radius), radius)
    return (c * y**2) / (1.0 + math.sqrt(arg))


def _mpl_draw_roughness_symbol(ax: Any, tip: Tuple[float, float], value_text: str, scale: float = 1.0):
    """Draw GB/T 131 surface roughness V symbol on a matplotlib Axes in mm coordinates."""
    x0, y0 = tip
    h_tri = 2.8 * scale
    dx_tri = h_tri / math.sqrt(3.0)
    h_tail = 5.8 * scale
    dx_tail = h_tail / math.sqrt(3.0)
    ax.plot([x0 - dx_tri, x0, x0 + dx_tail], [y0 + h_tri, y0, y0 + h_tail], color="#000000", linewidth=0.85)
    ax.plot([x0 - dx_tri, x0 + dx_tri], [y0 + h_tri, y0 + h_tri], color="#000000", linewidth=0.85)
    if value_text:
        ax.text(x0 - 0.1 * scale, y0 + h_tri + 0.5 * scale, value_text, fontsize=6.5 * scale, ha="center", va="bottom")


def _mpl_draw_arrow_head(ax: Any, tip: Tuple[float, float], direction: Tuple[float, float], size: float = 2.1):
    """Draw solid black CAD arrowhead in matplotlib mm coordinates."""
    dx, dy = direction
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    w = size * 0.35
    p_base = (tip[0] - ux * size, tip[1] - uy * size)
    tri = patches.Polygon(
        [tip, (p_base[0] + px * w, p_base[1] + py * w), (p_base[0] - px * w, p_base[1] - py * w)],
        closed=True, facecolor="#000000", edgecolor="#000000", linewidth=0.3
    )
    ax.add_patch(tri)


def _mpl_draw_gbt13323_sheet_and_title(ax: Any, drawing_data: Dict[str, Any], is_assembly: bool = False):
    """
    Draw shared A4 (297x210mm) sheet border, left filing strip, centering marks,
    general roughness '其余 ▽ 0.05', and 180mm GB/T 10609.1 title block with empty '单位' content.
    """
    ax.add_patch(patches.Rectangle((0.0, 0.0), 297.0, 210.0, fill=False, edgecolor="#333333", linewidth=0.55))
    ax.add_patch(patches.Rectangle((25.0, 5.0), 267.0, 200.0, fill=False, edgecolor="#000000", linewidth=1.45))

    # Centering marks
    ax.plot([148.5, 148.5], [210.0, 201.5], color="#000000", linewidth=1.2)
    ax.plot([148.5, 148.5], [0.0, 8.5], color="#000000", linewidth=1.2)
    ax.plot([21.0, 28.5], [105.0, 105.0], color="#000000", linewidth=1.2)
    ax.plot([297.0, 288.5], [105.0, 105.0], color="#000000", linewidth=1.2)

    # Left filing margin strip (X: 0..21)
    y_bands = [0.0, 7.0, 18.0, 24.0, 35.0, 41.0, 52.0, 58.0]
    ax.add_patch(patches.Rectangle((0.0, 0.0), 21.0, 58.0, fill=False, edgecolor="#000000", linewidth=0.7))
    for yb in y_bands[1:-1]:
        ax.plot([0.0, 21.0], [yb, yb], color="#000000", linewidth=0.65)
    ax.plot([10.5, 10.5], [0.0, 7.0], color="#000000", linewidth=0.65)
    ax.text(5.25, 3.5, "日期", fontsize=7.5, ha="center", va="center")
    ax.text(15.75, 3.5, "签字", fontsize=7.5, ha="center", va="center")
    for lbl, (ya, yb) in [("底图总号", (18.0, 24.0)), ("日底图总号", (35.0, 41.0)), ("介质编号", (52.0, 58.0))]:
        ax.text(10.5, 0.5 * (ya + yb), lbl, fontsize=7.2, ha="center", va="center")

    # Top-right general roughness
    ax.text(275.5, 193.5, "其余", fontsize=9.8, ha="right", va="center")
    _mpl_draw_roughness_symbol(ax, (279.5, 191.2), "0.05", scale=1.25)

    # 180mm GB/T 10609.1 Title block (X: 112..292, Y: 5..54)
    tb_x0, tb_x1, tb_y0, tb_y1 = 112.0, 292.0, 5.0, 54.0
    x_mid, x_right = 204.0, 242.0
    ax.add_patch(patches.Rectangle((tb_x0, tb_y0), tb_x1 - tb_x0, tb_y1 - tb_y0, fill=False, edgecolor="#000000", linewidth=0.9))
    ax.plot([x_mid, x_mid], [tb_y0, tb_y1], color="#000000", linewidth=0.8)
    ax.plot([x_right, x_right], [tb_y0, tb_y1], color="#000000", linewidth=0.8)

    for r in range(1, 7):
        y_l = tb_y0 + r * 7.0
        ax.plot([tb_x0, x_mid], [y_l, y_l], color="#000000", linewidth=0.65)
    for xv in [122.0, 132.0, 142.0, 168.0, 186.0]:
        ax.plot([xv, xv], [33.0, tb_y1], color="#000000", linewidth=0.65)
    up_cols = [112.0, 122.0, 132.0, 142.0, 168.0, 186.0, 204.0]
    for i, hdr in enumerate(["标记", "处数", "分区", "更改文件号", "签名", "年/月/日"]):
        ax.text(0.5 * (up_cols[i] + up_cols[i + 1]), 36.5, hdr, fontsize=7.0, ha="center", va="center")

    lo_cols = [112.0, 124.0, 140.0, 156.0, 170.0, 187.0, 204.0]
    for xv in lo_cols[1:-1]:
        ax.plot([xv, xv], [tb_y0, 33.0], color="#000000", linewidth=0.65)
    sign_row = [drawing_data.get("role_design", "设计"), drawing_data.get("drawn_by", "(签名)"),
                drawing_data.get("drawing_date", "(年月日)"), "标准化",
                drawing_data.get("std_by", "(签名)"), drawing_data.get("std_date", "(年月日)")]
    for i, txt in enumerate(sign_row):
        if txt:
            ax.text(0.5 * (lo_cols[i] + lo_cols[i + 1]), 29.5, txt, fontsize=7.0, ha="center", va="center")
    ax.text(0.5 * (lo_cols[0] + lo_cols[1]), 15.5, "审核", fontsize=7.0, ha="center", va="center")
    ax.text(0.5 * (lo_cols[0] + lo_cols[1]), 8.5, "工艺", fontsize=7.0, ha="center", va="center")
    ax.text(0.5 * (lo_cols[3] + lo_cols[4]), 8.5, "批准", fontsize=7.0, ha="center", va="center")

    # Middle block (X: 204..242)
    for yl in (12.0, 19.0, 26.0):
        ax.plot([x_mid, x_right], [yl, yl], color="#000000", linewidth=0.65)
    ax.text(223.0, 47.0, "系统类别" if is_assembly else "光学材料", fontsize=8.0, ha="center", va="center")
    if is_assembly:
        ax.text(223.0, 36.0, drawing_data.get("assembly_category", "光学系统总装"), fontsize=8.0, ha="center", va="center")
    else:
        raw_m = drawing_data.get("title_materials")
        mats = [m.strip() for m in re.split(r"[/\n,]+", str(drawing_data.get("material", "H-K9L"))) if m.strip()] if raw_m is None else list(raw_m)
        if len(mats) == 1:
            ax.text(223.0, 36.0, mats[0], fontsize=8.0, ha="center", va="center")
        else:
            for i, ml in enumerate(mats[:4]):
                ax.text(207.5, 40.0 - i * 5.2, str(ml), fontsize=7.5, ha="left", va="center")

    for xv in (222.0, 232.0):
        ax.plot([xv, xv], [12.0, 26.0], color="#000000", linewidth=0.65)
    for xv in (210.0, 216.0):
        ax.plot([xv, xv], [12.0, 19.0], color="#000000", linewidth=0.65)
    ax.text(213.0, 22.5, "阶段标记", fontsize=6.8, ha="center", va="center")
    ax.text(227.0, 22.5, "重量", fontsize=6.8, ha="center", va="center")
    ax.text(237.0, 22.5, "比例", fontsize=6.8, ha="center", va="center")
    sc_val = str(drawing_data.get("title_scale_str", "") or "").strip()
    if sc_val:
        ax.text(237.0, 15.5, sc_val, fontsize=6.5, ha="center", va="center")
    ax.text(223.0, 8.5, drawing_data.get("sheet_str", "共    张    第    张"), fontsize=7.0, ha="center", va="center")

    # Right block (X: 242..292): "单位" header strictly empty content
    ax.plot([x_right, tb_x1], [20.0, 20.0], color="#000000", linewidth=0.65)
    ax.plot([x_right, tb_x1], [37.0, 37.0], color="#000000", linewidth=0.65)
    ax.text(267.0, 49.5, "单位", fontsize=8.0, ha="center", va="center")
    ax.text(267.0, 33.2, "图样名称", fontsize=7.8, ha="center", va="center")

    od = float(drawing_data.get("od", 25.4))
    if drawing_data.get("title_name"):
        name_txt = str(drawing_data["title_name"])
    elif is_assembly:
        name_txt = f"{drawing_data.get('drawing_name', '光学系统')}总装图"
    else:
        name_txt = drawing_data.get("drawing_name", f"\u03a6{od:.1f} 光学零件").replace("\n", " ")
    ax.text(267.0, 25.5, name_txt, fontsize=9.8 if len(name_txt) <= 14 else 7.6, ha="center", va="center")

    dwg_code = str(drawing_data.get("title_code", drawing_data.get("drawing_code", "(图样代号)")) or "(图样代号)")
    if dwg_code in ("OPT-00", "OPT-ASM"):
        dwg_code = "(图样代号)"
    ax.text(267.0, 12.5, dwg_code, fontsize=8.6, ha="center", va="center")


def _mpl_draw_glass_hatch(ax: Any, poly_pts: List[Tuple[float, float]], angle_deg: float, axis_y: float, keepout_pt: Optional[Tuple[float, float]] = None):
    """Draw sparse GB/T 13323 double-stroke hatching inside lens polygon on matplotlib Axes."""
    ys = [p[1] for p in poly_pts]
    half_span = 0.5 * (max(ys) - min(ys))
    if half_span < 2.0:
        return
    rad = math.radians(angle_deg)
    dx, dy = math.cos(rad), math.sin(rad)
    nx, ny = -dy, dx
    for frac in (-0.72, -0.25, 0.25, 0.72):
        y_probe = axis_y + frac * half_span
        spans = _clip_line_to_polygon((0.0, y_probe), (1.0, 0.0), poly_pts)
        if not spans:
            continue
        t0, t1 = max(spans, key=lambda iv: iv[1] - iv[0])
        if t1 - t0 < 1.2:
            continue
        cx, cy = 0.5 * (t0 + t1), y_probe
        if keepout_pt is not None and math.hypot(cx - keepout_pt[0], cy - keepout_pt[1]) < 3.5:
            continue
        for shift in (-0.4, 0.4):
            base = (cx + nx * shift, cy + ny * shift)
            for ct0, ct1 in _clip_line_to_polygon(base, (dx, dy), poly_pts):
                if ct0 <= 0.0 <= ct1 and (ct1 - ct0) >= 1.0:
                    hd = min(1.85, 0.34 * (ct1 - ct0))
                    ax.plot([base[0] - hd * dx, base[0] + hd * dx], [base[1] - hd * dy, base[1] + hd * dy], color="#222222", linewidth=0.55)
                    break


def _render_gbt13323_element_drawing(
    drawing_data: Dict[str, Any],
    output_png: str,
):
    """
    Matplotlib fallback renderer matching the absorbed Chinese GB/T 13323-2009 template.
    Preserves blank '单位' content, 180-deg header code, and unsigned linear dimensions.
    """
    fig = plt.figure(figsize=(16.0, 11.31), dpi=320, facecolor="white")
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(-1.5, 298.5)
    ax.set_ylim(-1.5, 211.5)
    ax.set_aspect("equal")
    ax.axis("off")

    _mpl_draw_gbt13323_sheet_and_title(ax, drawing_data, is_assembly=False)

    # Top-left 17-row Optical Requirements Table (X: 25..94, Y: 205 down)
    x0, col1_w, col2_w = 25.0, 43.0, 26.0
    tbl_w = col1_w + col2_w
    row_hs = [7.5] + [5.4] * 7 + [5.5] + [5.4] * 8
    y_edges = [205.0]
    for rh in row_hs:
        y_edges.append(y_edges[-1] - rh)
    ax.add_patch(patches.Rectangle((x0, y_edges[-1]), tbl_w, 205.0 - y_edges[-1], fill=False, edgecolor="#000000", linewidth=0.75))
    for ye in y_edges[1:-1]:
        ax.plot([x0, x0 + tbl_w], [ye, ye], color="#000000", linewidth=0.6)
    ax.plot([x0 + col1_w, x0 + col1_w], [y_edges[8], y_edges[1]], color="#000000", linewidth=0.6)
    ax.plot([x0 + col1_w, x0 + col1_w], [y_edges[-1], y_edges[9]], color="#000000", linewidth=0.6)

    ax.text(x0 + 0.5 * tbl_w, 0.5 * (y_edges[0] + y_edges[1]), "(图样代号)(存储代号)", fontsize=8.0, ha="center", va="center", rotation=180)
    efl_v = str(drawing_data.get("efl_str", "") or "").strip()
    bfl_v = str(drawing_data.get("bfl_str", "") or "").strip()
    d0_v = drawing_data.get("d0_str") or (f"{float(drawing_data.get('ca', 0.0)):g}mm" if float(drawing_data.get("ca", 0.0)) > 0 else "")
    mat_rows = [
        (r"$\Delta n_d$", drawing_data.get("delta_nd", "2C")),
        (r"$\Delta(n_F-n_C)$", drawing_data.get("delta_nf_nc", "2C")),
        ("光学均匀性", drawing_data.get("homogeneity", "")),
        ("应力双折射", drawing_data.get("stress_biref", "")),
        ("光学吸收系数", drawing_data.get("absorption", "")),
        ("条纹度", drawing_data.get("striae", "")),
        ("气泡度", drawing_data.get("bubbles", "")),
    ]
    for idx, (lbl, val) in enumerate(mat_rows):
        ym = 0.5 * (y_edges[1 + idx] + y_edges[2 + idx])
        ax.text(x0 + 0.5 * col1_w, ym, lbl, fontsize=7.5, ha="center", va="center")
        if val:
            ax.text(x0 + col1_w + 0.5 * col2_w, ym, str(val), fontsize=7.5, ha="center", va="center")

    ax.text(x0 + 0.5 * tbl_w, 0.5 * (y_edges[8] + y_edges[9]), "对零件的要求", fontsize=7.8, ha="center", va="center")
    part_rows = [
        ("N", drawing_data.get("N", "3")),
        (r"$\Delta N$", drawing_data.get("delta_N", "0.3")),
        (r"$\Delta R$", drawing_data.get("delta_R", "A")),
        ("B", drawing_data.get("B", "IV")),
        (r"$\chi$", drawing_data.get("wedge_chi", "3'")),
        ("f", f"{efl_v}mm" if efl_v not in ("", "-") and not efl_v.endswith("mm") else efl_v),
        (r"$S_f$", f"{bfl_v}mm" if bfl_v not in ("", "-") and not bfl_v.endswith("mm") else bfl_v),
        (r"$D_0$", str(d0_v)),
    ]
    for idx, (lbl, val) in enumerate(part_rows):
        ym = 0.5 * (y_edges[9 + idx] + y_edges[10 + idx])
        ax.text(x0 + 0.5 * col1_w, ym, lbl, fontsize=7.5, ha="center", va="center")
        if val:
            ax.text(x0 + col1_w + 0.5 * col2_w, ym, str(val), fontsize=7.5, ha="center", va="center")

    # Technical Notes (bottom-left)
    notes = drawing_data.get("custom_notes") or drawing_data.get("technical_notes") or [
        "1、材料采用指定牌号优质光学玻璃；",
        "2、所有倒角 0.3×45°，棱边不得崩边；",
        f"3、设计波长：{drawing_data.get('design_wavelengths', '532nm')}。",
    ]
    cur_ny = 95.0
    for n in notes:
        for wl in _wrap_cad_note_line(n, max_units=27.0):
            ax.text(28.5, cur_ny, wl, fontsize=7.8, ha="left", va="top")
            cur_ny -= 4.4
        cur_ny -= 1.2

    # Lens Geometry & Dimensions
    elements = drawing_data.get("elements") or [{"index": 1, "r1": 50.0, "r2": -50.0, "t": 5.0, "k1": 0, "k2": 0, "material": "H-K9L"}]
    od = float(drawing_data.get("od", 25.4))
    od_tol = drawing_data.get("od_tolerance", "-0.05")
    half_od = od / 2.0
    total_axial = sum(float(e.get("t", 5.0)) + float(e.get("air_after", 0.0)) for e in elements)

    axis_y = float(drawing_data.get("axis_y", 132.0))
    scale = float(drawing_data.get("scale_override") or max(1.0, min(6.0, round(min(105.0 / max(total_axial, 8.0), 82.0 / max(od, 8.0)) * 4.0) / 4.0)))
    x_origin = float(drawing_data.get("lens_center_x", 182.0)) - 0.5 * total_axial * scale
    ax.plot([max(100.0, x_origin - 28.0), min(286.0, x_origin + total_axial * scale + 38.0)], [axis_y, axis_y],
            color="#c62828", linestyle="-.", linewidth=0.75)

    cur_z = x_origin
    slices_m = []
    for idx, elem in enumerate(elements):
        r1 = float(elem.get("r1", 0.0))
        r2 = float(elem.get("r2", 0.0))
        r1d = float(elem.get("r1_draw", r1))
        r2d = float(elem.get("r2_draw", r2))
        ct = float(elem.get("t", 5.0))
        air_after = float(elem.get("air_after", 0.0))
        prev_air = (idx == 0) or (float(elements[idx - 1].get("air_after", 0.0)) > 1e-4)
        next_air = (idx == len(elements) - 1) or (air_after > 1e-4)

        poly, f_pts, r_pts, z_fn, z_rn = _build_lens_slice_polygon(
            cur_z, cur_z + ct * scale, r1d, r2d, float(elem.get("k1", 0.0)), float(elem.get("k2", 0.0)),
            half_od, scale, axis_y, chamfer_front=0.3 if prev_air else 0.0, chamfer_rear=0.3 if next_air else 0.0
        )
        ax.add_patch(patches.Polygon(poly, closed=True, facecolor="none", edgecolor="#000000", linewidth=1.05))
        xm = 0.5 * (cur_z + _calculate_sag(r1d, 0.0, 0.55 * half_od) * scale + cur_z + ct * scale + _calculate_sag(r2d, 0.0, 0.55 * half_od) * scale)
        ym = axis_y + 0.55 * half_od * scale
        _mpl_draw_glass_hatch(ax, poly, float(elem.get("hatch_angle", 45.0 if idx % 2 == 0 else 135.0)), axis_y, (xm, ym) if len(elements) > 1 else None)
        if len(elements) > 1:
            ax.text(xm, ym, str(elem.get("index", idx + 1)), fontsize=8.0, ha="center", va="center")

        y_d = axis_y - half_od * scale - (7.5 + (idx % 3) * 6.0)
        ax.plot([cur_z, cur_z], [axis_y, y_d - 2.0], color="#333333", linewidth=0.55)
        ax.plot([cur_z + ct * scale, cur_z + ct * scale], [axis_y, y_d - 2.0], color="#333333", linewidth=0.55)
        ax.plot([cur_z, cur_z + ct * scale], [y_d, y_d], color="#333333", linewidth=0.6)
        _mpl_draw_arrow_head(ax, (cur_z, y_d), (1.0, 0.0), 1.8)
        _mpl_draw_arrow_head(ax, (cur_z + ct * scale, y_d), (-1.0, 0.0), 1.8)
        ax.text(0.5 * (2 * cur_z + ct * scale), y_d + 1.0, f"{ct:g}{elem.get('t_tolerance', '±0.02')}", fontsize=7.0, ha="center", va="bottom")

        slices_m.append((cur_z, cur_z + ct * scale, f_pts, r_pts, r1, r2, z_fn, z_rn))
        cur_z += (ct + air_after) * scale

    _mpl_draw_roughness_symbol(ax, (0.5 * (slices_m[0][6] + slices_m[-1][7]), axis_y + half_od * scale), "1.6", 0.95)
    for si, (zf, zr, fp, rp, r1, r2, zfn, zrn) in enumerate(slices_m):
        pf = fp[int(len(fp) * 0.72)]
        ax.annotate(_format_gbt_radius(r1), xy=pf, xytext=(pf[0] - 18.0, pf[1] + 8.0),
                    arrowprops=dict(arrowstyle="->", color="black", lw=0.7), fontsize=7.2, ha="right")
        if si == len(slices_m) - 1:
            pr = rp[int(len(rp) * 0.28)]
            ax.annotate(_format_gbt_radius(r2), xy=pr, xytext=(pr[0] + 18.0, pr[1] - 8.0),
                        arrowprops=dict(arrowstyle="->", color="black", lw=0.7), fontsize=7.2, ha="left")

    x_od = max(s[7] for s in slices_m) + 14.0
    ax.plot([slices_m[-1][7], x_od + 2.0], [axis_y + half_od * scale, axis_y + half_od * scale], color="#333333", linewidth=0.55)
    ax.plot([slices_m[-1][7], x_od + 2.0], [axis_y - half_od * scale, axis_y - half_od * scale], color="#333333", linewidth=0.55)
    ax.plot([x_od, x_od], [axis_y - half_od * scale, axis_y + half_od * scale], color="#333333", linewidth=0.6)
    _mpl_draw_arrow_head(ax, (x_od, axis_y - half_od * scale), (0.0, -1.0), 2.0)
    _mpl_draw_arrow_head(ax, (x_od, axis_y + half_od * scale), (0.0, 1.0), 2.0)
    ax.text(x_od - 1.5, axis_y, f"{od:g}{od_tol}", fontsize=7.5, ha="right", va="center", rotation=90)

    fig.savefig(output_png, bbox_inches="tight", pad_inches=0.04, dpi=320)
    plt.close(fig)


def _render_gbt13323_assembly_drawing(
    drawing_data: Dict[str, Any],
    output_png: str,
):
    """Matplotlib fallback renderer for Chinese GB/T 13323-2009 system assembly drawing."""
    fig = plt.figure(figsize=(16.0, 11.31), dpi=320, facecolor="white")
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(-1.5, 298.5)
    ax.set_ylim(-1.5, 211.5)
    ax.set_aspect("equal")
    ax.axis("off")

    _mpl_draw_gbt13323_sheet_and_title(ax, drawing_data, is_assembly=True)

    assembly_elements = drawing_data.get("assembly_elements", [])
    x0, tbl_w = 25.0, 69.0
    x_mid = x0 + 36.0
    sys_rows = [
        ("工作波段", drawing_data.get("spectral_range", "-")),
        ("有效焦距 f'", f"{drawing_data.get('efl_str', '-')}mm"),
        ("总轨长 TOTR", f"{drawing_data.get('totr_str', '-')}mm"),
        ("后截距 BFL", f"{drawing_data.get('bfl_str', '-')}mm"),
        ("物方工作距", "\u221e" if drawing_data.get("object_at_infinity") else drawing_data.get("wd_str", "-")),
        ("视场 FOV", drawing_data.get("fov_str", "-")),
    ]
    h_hdr, h_row = 8.5, 5.6
    y_bot = 205.0 - h_hdr - 6.0 - len(sys_rows) * h_row
    ax.add_patch(patches.Rectangle((x0, y_bot), tbl_w, 205.0 - y_bot, fill=False, edgecolor="#000000", linewidth=0.75))
    ax.plot([x0, x0 + tbl_w], [205.0 - h_hdr, 205.0 - h_hdr], color="#000000", linewidth=0.6)
    ax.text(x0 + 0.5 * tbl_w, 205.0 - 0.5 * h_hdr, "(图样代号)(存储代号)", fontsize=8.0, ha="center", va="center", rotation=180)
    y_sub = 205.0 - h_hdr - 6.0
    ax.plot([x0, x0 + tbl_w], [y_sub, y_sub], color="#000000", linewidth=0.6)
    ax.text(x0 + 0.5 * tbl_w, 0.5 * (205.0 - h_hdr + y_sub), "系统主要参数", fontsize=7.8, ha="center", va="center")
    ax.plot([x_mid, x_mid], [y_bot, y_sub], color="#000000", linewidth=0.6)
    for i, (lbl, val) in enumerate(sys_rows):
        yr = y_sub - (i + 1) * h_row
        if i < len(sys_rows) - 1:
            ax.plot([x0, x0 + tbl_w], [yr, yr], color="#000000", linewidth=0.6)
        ax.text(0.5 * (x0 + x_mid), yr + 0.5 * h_row, lbl, fontsize=7.2, ha="center", va="center")
        ax.text(0.5 * (x_mid + x0 + tbl_w), yr + 0.5 * h_row, str(val), fontsize=7.0, ha="center", va="center")

    notes = drawing_data.get("technical_notes") or [
        f"1、工作波段：{drawing_data.get('design_wavelengths', 'Not specified')}；",
        f"2、总轨长 TOTR={drawing_data.get('totr_str', '-')}，镜组叠层={drawing_data.get('stack_str', '-')}；",
        "3、各透镜同轴度 ≤ 0.005，隔圈平行度 ≤ 0.003。",
    ]
    cur_ny = 92.0
    for n in notes:
        for wl in _wrap_cad_note_line(n, max_units=27.0):
            ax.text(28.5, cur_ny, wl, fontsize=7.6, ha="left", va="top")
            cur_ny -= 4.4
        cur_ny -= 1.0

    if assembly_elements:
        max_od = max(float(e.get("od", 20.0)) for e in assembly_elements)
        total_axial = sum(float(e.get("ct", 5.0)) + float(e.get("air_after", 0.0)) for e in assembly_elements)
        axis_y = 132.0
        scale = max(1.0, min(4.0, round(min(145.0 / max(total_axial, 20.0), 82.0 / max(max_od, 10.0)) * 4.0) / 4.0))
        x_origin = 188.0 - 0.5 * total_axial * scale
        ax.plot([max(100.0, x_origin - 15.0), min(286.0, x_origin + total_axial * scale + 20.0)],
                [axis_y, axis_y], color="#c62828", linestyle="-.", linewidth=0.75)

        cur_z = x_origin
        comp_num = 1
        for elem_i, elem in enumerate(assembly_elements):
            half_od = float(elem.get("od", max_od)) / 2.0
            comps = elem.get("components", [])
            comp_z = cur_z
            for c_idx, comp in enumerate(comps):
                ct = float(comp.get("ct", 5.0))
                poly, _, _, _, _ = _build_lens_slice_polygon(
                    comp_z, comp_z + ct * scale, float(comp["r1"]), float(comp["r2"]),
                    float(comp.get("k1", 0.0)), float(comp.get("k2", 0.0)), half_od, scale, axis_y,
                    chamfer_front=0.25 if c_idx == 0 else 0.0, chamfer_rear=0.25 if c_idx == len(comps) - 1 else 0.0, n_pts=30
                )
                ax.add_patch(patches.Polygon(poly, closed=True, facecolor="none", edgecolor="#000000", linewidth=0.95))
                _mpl_draw_glass_hatch(ax, poly, 45.0 if comp_num % 2 == 1 else 135.0, axis_y)
                comp_num += 1
                comp_z += ct * scale
            cur_z = comp_z + float(elem.get("air_after", 0.0)) * scale

    fig.savefig(output_png, bbox_inches="tight", pad_inches=0.04, dpi=320)
    plt.close(fig)


def _write_gbt13323_element_markdown(dwg_data: Dict[str, Any], filepath: str):
    """Write standardized GB/T 13323-2009 manufacturing specification sheet."""
    elements = dwg_data.get("elements", [])
    notes = dwg_data.get("technical_notes", [])
    notes_txt = "\n".join(f"- {n}" for n in notes)

    elems_table = "\n".join(
        f"| 透镜 {e['index']} | `{e['material']}` | `R1 = {e['r1']:.4f}` | `R2 = {e['r2']:.4f}` | `{e['t']:.4f}` ({e['t_tolerance']}) |"
        for e in elements
    )

    md = f"""# GB/T 13323-2009 光学制图零件规范书
## 图样名称: {dwg_data['drawing_name']} ({dwg_data['drawing_code']})

- **标准规范**: GB/T 13323-2009《光学制图》、GB/T 903-2019《无色光学玻璃》、GB/T 2831-2009
- **光学材料**: `{dwg_data['material']}`
- **机械外径 (OD)**: `Ø {dwg_data['od']:.2f} {dwg_data['od_tolerance']}`
- **有效通光孔径 (CA)**: `Ø {dwg_data['ca']:.2f}`
- **图纸比例**: `{dwg_data['drawing_scale']}`
- **设计波长**: `{dwg_data.get('design_wavelengths', '-')}`
- **镀膜**: `{dwg_data.get('coating', 'Not specified')}`

---

### 一、光学特性与对零件的要求表 (GB/T 13323 表头栏目)

| 项目代号 | 检验指标名称 | 设计公差等级 / 规定值 | 标准依据 |
| :--- | :--- | :--- | :--- |
| **Δnd** | 折射率允差 | `{dwg_data.get('delta_nd', '2C')}` | GB/T 903-2019 |
| **Δ(nF - nC)** | 色散系数允差 | `{dwg_data.get('delta_nf_nc', '2C')}` | GB/T 903-2019 |
| **光学均匀性** | 均匀性级别 | `{dwg_data.get('homogeneity', '2')}` 级 | GB/T 903-2019 |
| **应力双折射** | 光程差 | `{dwg_data.get('stress_biref', '1')}` 级 | GB/T 903-2019 |
| **条纹度** | 无条纹级别 | `{dwg_data.get('striae', '1')}` 级 | GB/T 903-2019 |
| **气泡度** | 气泡度类别 | `{dwg_data.get('bubbles', '1')}` 级 | GB/T 903-2019 |
| **N** | 光圈数 (球面度偏差) | `{dwg_data.get('N', '3')}` | GB/T 2831-2009 |
| **ΔN** | 局部光圈 (散光度) | `{dwg_data.get('delta_N', '0.3')}` | GB/T 2831-2009 |
| **ΔR** | 样板拟合允差 | `{dwg_data.get('delta_R', 'A')}` | GB/T 2831-2009 |
| **B** | 表面疵病级数 | `{dwg_data.get('B', 'IV')}` 级 | GB/T 1185-2006 |
| **χ** | 偏角差 (定心偏心) | `{dwg_data.get('wedge_chi', "1'")}` | GB/T 13323-2009 |
| **D0** | 有效孔径 | `Ø {dwg_data.get('ca_str', '-')}` | GB/T 13323-2009 |

---

### 二、元件几何构成参数

| 元件分片 | 材料牌号 | 前表面曲率 R1 | 后表面曲率 R2 | 中心厚度 CT |
| :--- | :--- | :--- | :--- | :--- |
{elems_table}

---

### 三、技术要求 (Technical Notes)
{notes_txt}
"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md)


def _write_assembly_markdown_drawing(dwg_data: Dict[str, Any], filepath: str):
    """Write standardized GB/T 13323-2009 assembly specification sheet."""
    assembly_elements = dwg_data.get("assembly_elements", [])
    notes = dwg_data.get("technical_notes", [])
    notes_txt = "\n".join(f"- {n}" for n in notes)

    elems_table = "\n".join(
        f"| 元件 {e['elem_index']} | {len(e['components'])} 片组 | `Ø {e['od']:.2f}` | `{e['ct']:.4f}` | `({e['air_after']:.4f})` |"
        for e in assembly_elements
    )

    md = f"""# GB/T 13323-2009 光学系统总装配合图规范书
## 图样名称: {dwg_data['drawing_name']} ({dwg_data['drawing_code']})

- **标准规范**: GB/T 13323-2009《光学制图》系统总图
- **有效焦距**: `{dwg_data.get('efl_str', '-')}` mm
- **数值孔径 NA**: `{dwg_data.get('na_str', '-')}`
- **工作谱段**: `{dwg_data.get('spectral_range', '-')}`
- **设计波长**: `{dwg_data.get('design_wavelengths', '-')}`
- **工作距离 WD**: `{dwg_data.get('wd_str', '-')}` mm
- **光学总长 TOTR (第一面至像面)**: `{dwg_data.get('totr_str', '-')}` mm
- **镜组轴向长度 (第一片前面至末片后面)**: `{dwg_data.get('stack_str', '-')}` mm
- **末片后表面至像面**: `{dwg_data.get('rear_gap_str', '-')}` mm
- **距离定义**: `{dwg_data.get('distance_basis', '-')}`

---

### 一、光学系统主要指标

| 参数项目 | 指标要求 |
| :--- | :--- |
| **波像差 RMS** | `{dwg_data.get('rms_wavefront', '< 0.05 λ')}` |
| **轴向色差** | `{dwg_data.get('axial_color', '< 2.5 μm')}` |
| **装配同轴度** | `{dwg_data.get('concentricity', '< 0.003 mm')}` |
| **外圆配合公差** | `{dwg_data.get('barrel_fit', 'g6/H7')}` |
| **出射光瞳** | `{dwg_data.get('pupil_str', '-')}` |

---

### 二、各透镜组机械与装配参数

| 组别 | 结构形式 | 机械外径 OD | 纯玻璃总厚度 CT | 组后空气间隔 |
| :--- | :--- | :--- | :--- | :--- |
{elems_table}

---

### 三、装配技术要求
{notes_txt}
"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md)


def _write_element_markdown_drawing(spec: Dict[str, Any], filepath: str):
    """Write standardized ISO 10110 manufacturing specification sheet."""
    s1 = spec["surface_1"]
    s2 = spec["surface_2"]
    mat_tol = spec["material_tolerances"]

    md_content = f"""# ISO 10110 光学零件制造图纸规格书 (Optical Element Drawing)
## 元件编号: Element {spec['element_index']} ({spec['element_type']})

- **设计图号 / 表面范围**: `{spec['surfaces']}`
- **玻璃材质牌号**: `{spec['material']}`
- **图纸公差等级**: `{spec['diameter_tolerance']}`
- **设计波长**: `{spec.get('design_wavelengths', '-')}`

---

### 一、机械外形尺寸与安装配合 (Mechanical Geometry)

| 尺寸项目 (Item) | 设计公称值 (Nominal) | 制造公差 (Tolerance) | 工艺说明 (Notes) |
| :--- | :--- | :--- | :--- |
| **机械外径 (OD)** | `Ø {spec['mechanical_diameter_od_mm']:.3f} mm` | `{spec['diameter_tolerance']}` | 配合镜筒公差 H7/g6 单向装配 |
| **中心厚度 (CT)** | `{spec['center_thickness_ct_mm']:.4f} mm` | `{spec['center_thickness_tolerance']}` | 光轴中心厚度测量 |
| **边缘厚度 (ET)** | `{spec['edge_thickness_et_mm']:.4f} mm` | 参考测量值 (Ref) | 杜绝刀口尖角 |
| **平直安装台阶 (Flat Land)**| `宽 {spec['flat_land_width_mm']:.3f} mm` | 参考设计值 | 预留隔圈与压圈定位端面 |
| **棱边保护倒角 (Chamfer)** | `{spec['protective_chamfer_mm']}` | ±0.1 mm | 防止装配碰撞崩边 |

---

### 二、光学表面技术要求 (Optical Surface Specifications - ISO 10110)

| 表面编号 (Surface) | 曲率半径 R (Radius) | 有效孔径 (CA) | 面形公差 3/ (Form PV/dN) | 偏心倾斜 4/ (Tilt) | 表面瑕疵 5/ (Scratch-Dig) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **第一面 (S1 - 前表面)** | `{s1['radius_r1_mm']}` | `Ø {s1['clear_aperture_ca1_mm']:.3f} mm` | `{s1['surface_form_iso3']}` | `{s1['centering_iso4']}` | `{s1['scratch_dig_iso5']}` |
| **第二面 (S2 - 后表面)** | `{s2['radius_r2_mm']}` | `Ø {s2['clear_aperture_ca2_mm']:.3f} mm` | `{s2['surface_form_iso3']}` | `{s2['centering_iso4']}` | `{s2['scratch_dig_iso5']}` |

---

### 三、光学材料物理特性与内质公差 (Material Specifications)

- **0/ 应力双折射 (Stress Birefringence)**: `{mat_tol['stress_birefringence_iso0']}`
- **1/ 气泡与杂质 (Bubbles & Inclusions)**: `{mat_tol['bubbles_iso1']}`
- **2/ 条纹与均匀性 (Inhomogeneity & Striae)**: `{mat_tol['inhomogeneity_iso2']}`
- **表面镀膜规格 (Coating)**: `{spec['coating']}`
"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md_content)


def _pick_cad_font(candidates: List[str], default: str = "simhei.ttf") -> str:
    """Return the first installed Windows font filename from candidates, else default."""
    win_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(win_fonts):
        existing = {f.lower(): f for f in os.listdir(win_fonts)}
        for cand in candidates:
            if cand.lower() in existing:
                return existing[cand.lower()]
    return default


def _draw_dxf_roughness_symbol(
    msp: Any,
    tip: Tuple[float, float],
    value_text: str = "1.6",
    scale: float = 1.0,
    style: str = "GB_NUM",
    layer: str = "5_DIMENSION",
    text_layer: str = "6_TEXT",
):
    """
    Draw GB/T 131 basic surface roughness symbol (equilateral triangle V with extended
    right leg and roughness Ra value above the top bar, left of the tail).
    """
    x0, y0 = tip
    h_tri = 2.8 * scale
    dx_tri = h_tri / math.sqrt(3.0)
    h_tail = 5.8 * scale
    dx_tail = h_tail / math.sqrt(3.0)

    p_top_l = (x0 - dx_tri, y0 + h_tri)
    p_top_r = (x0 + dx_tri, y0 + h_tri)
    p_tail = (x0 + dx_tail, y0 + h_tail)

    msp.add_line((x0, y0), p_top_l, dxfattribs={"layer": layer, "color": 3})
    msp.add_line((x0, y0), p_tail, dxfattribs={"layer": layer, "color": 3})
    msp.add_line(p_top_l, p_top_r, dxfattribs={"layer": layer, "color": 3})

    if value_text:
        y_txt = y0 + h_tri + 0.70 * scale
        x_leg = x0 + (h_tri + 0.70 * scale) / math.sqrt(3.0)
        t = msp.add_text(
            value_text,
            dxfattribs={"height": 2.15 * scale, "style": style, "layer": text_layer},
        )
        t.set_placement((x_leg - 0.42 * scale, y_txt), align=TextEntityAlignment.BOTTOM_RIGHT)


def _draw_dxf_optical_surface_mark(
    msp: Any,
    center: Tuple[float, float],
    radius: float = 1.25,
    layer: str = "6_TEXT",
):
    """Draw optical polished surface symbol (circle with inscribed cross '+')."""
    cx, cy = center
    msp.add_circle(center=(cx, cy), radius=radius, dxfattribs={"layer": layer, "color": 7})
    msp.add_line((cx - radius, cy), (cx + radius, cy), dxfattribs={"layer": layer, "color": 7})
    msp.add_line((cx, cy - radius), (cx, cy + radius), dxfattribs={"layer": layer, "color": 7})


def _measure_dxf_text_width(text: str, height: float, style: str = "GB_NUM") -> float:
    """Measure rendered DXF text advance width using ezdxf font metrics with safe fallback."""
    if not text:
        return 0.0
    if EZDXF_AVAILABLE:
        try:
            from ezdxf.fonts import fonts as _ez_fonts

            font_name = "STSONG.TTF" if style in ("GB_SONG", "HZ_STYLE") else "msyhl.ttc"
            return float(_ez_fonts.make_font(font_name, float(height)).text_width(text))
        except Exception:
            pass
    w = 0.0
    for ch in text:
        if ord(ch) > 127 and ch not in "°±×≥′":
            w += height * 1.12
        elif ch == "△":
            w += height * (1.95 if style in ("GB_SONG", "HZ_STYLE") else 1.25)
        elif ch in "()-.′ ":
            w += height * 0.58
        elif "A" <= ch <= "Z":
            w += height * (0.62 if ch == "I" else 0.96)
        else:
            w += height * 0.78
    return w


def _draw_dxf_subscript_text(
    msp: Any,
    segments: List[Tuple[str, float, float]],
    anchor_x: float,
    anchor_y: float,
    base_height: float = 2.6,
    style: str = "GB_SONG",
    layer: str = "6_TEXT",
    center_block: bool = True,
    italic_chars: bool = False,
):
    """
    Render multi-segment text containing base, subscript, or superscript spans.
    Each segment is (text, rel_scale, dy_frac) relative to base_height.
    """
    widths: List[float] = []
    pads_l: List[float] = []
    for txt, rel_s, _ in segments:
        w = _measure_dxf_text_width(txt, base_height * rel_s, style)
        pad_l = base_height * 0.08 if rel_s < 0.85 else 0.0
        pad_r = base_height * 0.08 if rel_s < 0.85 else 0.0
        widths.append(w + pad_l + pad_r)
        pads_l.append(pad_l)

    total_w = sum(widths)
    cur_x = anchor_x - total_w / 2.0 if center_block else anchor_x
    for (txt, rel_s, dy_frac), seg_w, pad_l in zip(segments, widths, pads_l):
        if txt:
            attrs: Dict[str, Any] = {
                "height": base_height * rel_s,
                "style": style,
                "layer": layer,
            }
            if italic_chars and rel_s < 0.85:
                attrs["oblique"] = 12.0
            t = msp.add_text(txt, dxfattribs=attrs)
            t.set_placement(
                (cur_x + pad_l, anchor_y + base_height * dy_frac),
                align=TextEntityAlignment.MIDDLE_LEFT,
            )
        cur_x += seg_w


def _draw_dxf_center_cross(msp: Any, center: Tuple[float, float], half_size: float = 1.8):
    """Draw a tiny crosshair '+' at a radius center of curvature."""
    cx, cy = center
    msp.add_line((cx - half_size, cy), (cx + half_size, cy), dxfattribs={"layer": "5_DIMENSION", "color": 3})
    msp.add_line((cx, cy - half_size), (cx, cy + half_size), dxfattribs={"layer": "5_DIMENSION", "color": 3})


def _clip_line_to_polygon(
    p0: Tuple[float, float],
    direction: Tuple[float, float],
    poly_pts: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """Return sorted parameter intervals along line p(t) = p0 + t*direction inside poly_pts."""
    dx, dy = direction
    t_hits: List[float] = []
    n = len(poly_pts)
    for i in range(n):
        ax, ay = poly_pts[i]
        bx, by = poly_pts[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        denom = dx * ey - dy * ex
        if abs(denom) < 1e-9:
            continue
        rx, ry = ax - p0[0], ay - p0[1]
        s = (rx * (-dy) + ry * dx) / denom
        if 0.0 <= s < 1.0:
            t = (rx * ey - ry * ex) / denom
            t_hits.append(t)
    t_hits.sort()
    return [(t_hits[k], t_hits[k + 1]) for k in range(0, len(t_hits) - 1, 2)]


def _draw_dxf_glass_hatch(
    msp: Any,
    poly_pts: List[Tuple[float, float]],
    angle_deg: float = 45.0,
    y_axis_paper: float = 132.0,
    keepout_pt: Optional[Tuple[float, float]] = None,
    keepout_r: float = 3.5,
):
    """
    Draw sparse GB/T 13323-2009 optical glass section pairs (// or \\ short double strokes)
    strictly clipped inside the glass polygon.
    """
    if len(poly_pts) < 3:
        return
    ys = [p[1] for p in poly_pts]
    y_min, y_max = min(ys), max(ys)
    half_span = (y_max - y_min) / 2.0
    if half_span < 2.0:
        return

    rad = math.radians(angle_deg)
    dx, dy = math.cos(rad), math.sin(rad)
    nx, ny = -dy, dx

    # Sample clean glass double-strokes across lower, axis, and upper zones (avoiding exact axis vertex y=0)
    sample_fracs = [-0.56, 0.02, 0.44]
    pair_gap = 0.65
    for frac in sample_fracs:
        y_probe = y_axis_paper + frac * half_span
        spans = _clip_line_to_polygon((0.0, y_probe), (1.0, 0.0), poly_pts)
        if not spans:
            continue
        t0, t1 = max(spans, key=lambda iv: iv[1] - iv[0])
        if t1 - t0 < 1.2:
            continue
        cx, cy = 0.5 * (t0 + t1), y_probe
        if keepout_pt is not None and math.hypot(cx - keepout_pt[0], cy - keepout_pt[1]) < keepout_r:
            continue

        for shift in (-pair_gap * 0.5, pair_gap * 0.5):
            base = (cx + nx * shift, cy + ny * shift)
            chord_spans = _clip_line_to_polygon(base, (dx, dy), poly_pts)
            for ct0, ct1 in chord_spans:
                if ct0 <= 0.0 <= ct1:
                    chord_len = ct1 - ct0
                    if chord_len < 1.0:
                        break
                    half_draw = min(1.50, 0.30 * chord_len)
                    p_a = (base[0] - half_draw * dx, base[1] - half_draw * dy)
                    p_b = (base[0] + half_draw * dx, base[1] + half_draw * dy)
                    msp.add_line(p_a, p_b, dxfattribs={"layer": "4_HATCH", "color": 4})
                    break


def _setup_gbt13323_dxf_document() -> Tuple[Any, Any]:
    """
    Create an ISO A4 Landscape (297x210mm) DXF document formatted to the
    standard Chinese GB/T 13323 / GB/T 14689 optical engineering drawing layout.
    """
    doc = ezdxf.new("R2010")
    doc.units = units.MM

    font_song = _pick_cad_font(["STSONG.TTF", "stsong.ttf", "simhei.ttf"])
    font_num = _pick_cad_font(["msyhl.ttc", "MSYHL.TTC", "simhei.ttf"])
    doc.styles.add("GB_SONG", font=font_song)
    doc.styles.add("GB_NUM", font=font_num)
    doc.styles.add("HZ_STYLE", font=font_song)

    if "CENTER" not in doc.linetypes:
        doc.linetypes.add("CENTER", pattern=[15.0, 11.5, -1.6, 0.5, -1.6], description="Center ____ . ____ .")

    doc.layers.add("0_FRAME", color=7, lineweight=50)
    doc.layers.add("1_TABLE", color=7, lineweight=25)
    doc.layers.add("2_CONTOUR", color=7, lineweight=40)
    doc.layers.add("3_AXIS", color=1, linetype="CENTER", lineweight=20)
    doc.layers.add("4_HATCH", color=4, lineweight=20)
    doc.layers.add("5_DIMENSION", color=3, lineweight=15)
    doc.layers.add("6_TEXT", color=7, lineweight=20)

    msp = doc.modelspace()

    # 1. Outer Sheet Border (297x210mm, thin border line)
    msp.add_lwpolyline(
        [(0.0, 0.0), (297.0, 0.0), (297.0, 210.0), (0.0, 210.0)],
        close=True,
        dxfattribs={"layer": "0_FRAME", "lineweight": 18},
    )

    # 2. Inner Drawing Frame (Left 25mm binding margin, Top/Right/Bottom 5mm)
    x_min, y_min = 25.0, 5.0
    x_max, y_max = 292.0, 205.0
    msp.add_lwpolyline(
        [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)],
        close=True,
        dxfattribs={"layer": "0_FRAME", "lineweight": 50},
    )

    # 3. Four Centering Marks crossing inner frame
    msp.add_line((148.5, 210.0), (148.5, 201.5), dxfattribs={"layer": "0_FRAME", "lineweight": 40})
    msp.add_line((148.5, 0.0), (148.5, 8.5), dxfattribs={"layer": "0_FRAME", "lineweight": 40})
    msp.add_line((21.0, 105.0), (28.5, 105.0), dxfattribs={"layer": "0_FRAME", "lineweight": 40})
    msp.add_line((297.0, 105.0), (288.5, 105.0), dxfattribs={"layer": "0_FRAME", "lineweight": 40})

    # 4. Left Filing Margin Boxes (X: 0 to 21mm, upright text bottom-up)
    fx0, fx1 = 0.0, 21.0
    y_bands = [0.0, 7.0, 18.0, 24.0, 35.0, 41.0, 52.0, 58.0]
    msp.add_lwpolyline([(fx0, y_bands[0]), (fx1, y_bands[0]), (fx1, y_bands[-1]), (fx0, y_bands[-1])],
                       close=True, dxfattribs={"layer": "1_TABLE"})
    for yb in y_bands[1:-1]:
        msp.add_line((fx0, yb), (fx1, yb), dxfattribs={"layer": "1_TABLE"})
    msp.add_line((10.5, 0.0), (10.5, 7.0), dxfattribs={"layer": "1_TABLE"})

    msp.add_text("日期", dxfattribs={"height": 2.6, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
        (5.25, 3.5), align=TextEntityAlignment.MIDDLE_CENTER
    )
    msp.add_text("签字", dxfattribs={"height": 2.6, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
        (15.75, 3.5), align=TextEntityAlignment.MIDDLE_CENTER
    )
    for label, (y_a, y_b) in [("底图总号", (18.0, 24.0)), ("日底图总号", (35.0, 41.0)), ("介质编号", (52.0, 58.0))]:
        msp.add_text(label, dxfattribs={"height": 2.5, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
            (10.5, 0.5 * (y_a + y_b)), align=TextEntityAlignment.MIDDLE_CENTER
        )

    # 5. Top-Right General Surface Roughness ("其余 ▽ 0.05")
    txt_qy = msp.add_text("其余", dxfattribs={"height": 3.4, "style": "GB_SONG", "layer": "6_TEXT"})
    txt_qy.set_placement((275.5, 193.5), align=TextEntityAlignment.MIDDLE_RIGHT)
    _draw_dxf_roughness_symbol(msp, tip=(279.5, 191.2), value_text="0.05", scale=1.25, style="GB_NUM")

    return doc, msp


def _draw_dxf_title_block(msp: Any, data: Dict[str, Any], is_assembly: bool = False):
    """
    Draw standard Chinese GB/T 10609.1 (180mm x 49mm) Title Block matching the
    primary project optical template (X: 112.0 to 292.0, Y: 5.0 to 54.0).
    CRITICAL POLICY: The '单位' (Organization/Unit) header is preserved for standard
    structure, while its value area MUST ALWAYS remain completely empty.
    """
    tb_x0 = 112.0
    tb_x1 = 292.0
    tb_y0 = 5.0
    tb_y1 = 54.0
    row_h = 7.0  # 7 rows * 7mm = 49mm

    x_mid_blk = 204.0   # Left block (92mm): 112..204
    x_right_blk = 242.0  # Middle block (38mm): 204..242; Right block (50mm): 242..292

    # Outer border
    msp.add_lwpolyline(
        [(tb_x0, tb_y0), (tb_x1, tb_y0), (tb_x1, tb_y1), (tb_x0, tb_y1)],
        close=True,
        dxfattribs={"layer": "1_TABLE"},
    )

    # Major vertical block dividers
    msp.add_line((x_mid_blk, tb_y0), (x_mid_blk, tb_y1), dxfattribs={"layer": "1_TABLE"})
    msp.add_line((x_right_blk, tb_y0), (x_right_blk, tb_y1), dxfattribs={"layer": "1_TABLE"})

    # === LEFT BLOCK (X: 112.0 to 204.0, width 92mm, 7 rows x 7mm) ===
    for r in range(1, 7):
        y_line = tb_y0 + r * row_h
        msp.add_line((tb_x0, y_line), (x_mid_blk, y_line), dxfattribs={"layer": "1_TABLE"})

    # Upper change-log band (Y: 33.0 to 54.0): widths [10, 10, 10, 26, 18, 18]
    up_cols = [112.0, 122.0, 132.0, 142.0, 168.0, 186.0, 204.0]
    for xv in up_cols[1:-1]:
        msp.add_line((xv, 33.0), (xv, tb_y1), dxfattribs={"layer": "1_TABLE"})

    up_headers = ["标记", "处数", "分区", "更改文件号", "签名", "年/月/日"]
    for idx, hdr in enumerate(up_headers):
        cx = 0.5 * (up_cols[idx] + up_cols[idx + 1])
        msp.add_text(
            hdr,
            dxfattribs={"height": 2.4, "style": "GB_SONG", "layer": "6_TEXT"},
        ).set_placement((cx, 36.5), align=TextEntityAlignment.MIDDLE_CENTER)

    # Lower sign-off band (Y: 5.0 to 33.0): widths [12, 16, 16, 14, 17, 17]
    lo_cols = [112.0, 124.0, 140.0, 156.0, 170.0, 187.0, 204.0]
    for xv in lo_cols[1:-1]:
        msp.add_line((xv, tb_y0), (xv, 33.0), dxfattribs={"layer": "1_TABLE"})

    sign_row4 = [
        data.get("role_design", "设计"),
        data.get("drawn_by", "(签名)"),
        data.get("drawing_date", "(年月日)"),
        "标准化",
        data.get("std_by", "(签名)"),
        data.get("std_date", "(年月日)"),
    ]
    def _fit_title_h(s: str, max_w_mm: float, default_h: float, min_h: float = 1.65) -> float:
        units = sum(1.12 if ord(ch) > 127 else 0.68 for ch in s)
        if units <= 1e-6:
            return default_h
        return max(min_h, min(default_h, max_w_mm / units))

    for idx, txt in enumerate(sign_row4):
        cx = 0.5 * (lo_cols[idx] + lo_cols[idx + 1])
        if txt:
            col_w = lo_cols[idx + 1] - lo_cols[idx]
            h_cell = _fit_title_h(str(txt), col_w - 2.0, 2.4, min_h=1.7)
            msp.add_text(
                txt,
                dxfattribs={"height": h_cell, "style": "GB_SONG", "layer": "6_TEXT"},
            ).set_placement((cx, 29.5), align=TextEntityAlignment.MIDDLE_CENTER)

    # Row 6 (Y: 12..19): 审核
    msp.add_text(
        "审核",
        dxfattribs={"height": 2.4, "style": "GB_SONG", "layer": "6_TEXT"},
    ).set_placement((0.5 * (lo_cols[0] + lo_cols[1]), 15.5), align=TextEntityAlignment.MIDDLE_CENTER)

    # Row 7 (Y: 5..12): 工艺 & 批准
    msp.add_text(
        "工艺",
        dxfattribs={"height": 2.4, "style": "GB_SONG", "layer": "6_TEXT"},
    ).set_placement((0.5 * (lo_cols[0] + lo_cols[1]), 8.5), align=TextEntityAlignment.MIDDLE_CENTER)
    msp.add_text(
        "批准",
        dxfattribs={"height": 2.4, "style": "GB_SONG", "layer": "6_TEXT"},
    ).set_placement((0.5 * (lo_cols[3] + lo_cols[4]), 8.5), align=TextEntityAlignment.MIDDLE_CENTER)

    # === MIDDLE BLOCK (X: 204.0 to 242.0, width 38mm) ===
    # Horizontal lines at Y = 12, 19, 26 (top Y=26..54 is open merged material box)
    for y_line in (12.0, 19.0, 26.0):
        msp.add_line((x_mid_blk, y_line), (x_right_blk, y_line), dxfattribs={"layer": "1_TABLE"})

    # Top material block (Y: 26..54)
    mat_hdr = "系统类别" if is_assembly else "光学材料"
    msp.add_text(
        mat_hdr,
        dxfattribs={"height": 2.8, "style": "GB_SONG", "layer": "6_TEXT"},
    ).set_placement((0.5 * (x_mid_blk + x_right_blk), 47.0), align=TextEntityAlignment.MIDDLE_CENTER)

    if is_assembly:
        mat_lines = [data.get("assembly_category", "光学系统总装")]
    else:
        raw_mats = data.get("title_materials")
        if raw_mats is None:
            mat_str = str(data.get("material", "H-K9L"))
            mat_lines = [m.strip() for m in re.split(r"[/\n,]+", mat_str) if m.strip()]
        else:
            mat_lines = [str(m).strip() for m in raw_mats if str(m).strip()]

    n_mats = len(mat_lines)
    if n_mats == 1:
        msp.add_text(
            mat_lines[0],
            dxfattribs={"height": 2.8, "style": "GB_SONG", "layer": "6_TEXT"},
        ).set_placement((0.5 * (x_mid_blk + x_right_blk), 36.0), align=TextEntityAlignment.MIDDLE_CENTER)
    else:
        y_start_m = 40.0 if n_mats <= 3 else 41.5
        dy_m = 5.2 if n_mats <= 3 else 4.2
        h_m = 2.6 if n_mats <= 3 else 2.1
        for idx, ml in enumerate(mat_lines[:4]):
            msp.add_text(
                ml,
                dxfattribs={"height": h_m, "style": "GB_SONG", "layer": "6_TEXT"},
            ).set_placement((x_mid_blk + 3.5, y_start_m - idx * dy_m), align=TextEntityAlignment.MIDDLE_LEFT)

    # Row 5 & Row 6 (Y: 12..26): 阶段标记 (18mm) | 重量 (10mm) | 比例 (10mm)
    x_wt = x_mid_blk + 18.0   # 222.0
    x_sc = x_wt + 10.0        # 232.0
    msp.add_line((x_wt, 12.0), (x_wt, 26.0), dxfattribs={"layer": "1_TABLE"})
    msp.add_line((x_sc, 12.0), (x_sc, 26.0), dxfattribs={"layer": "1_TABLE"})
    # 3 sub-cells under 阶段标记 (Y: 12..19)
    msp.add_line((x_mid_blk + 6.0, 12.0), (x_mid_blk + 6.0, 19.0), dxfattribs={"layer": "1_TABLE"})
    msp.add_line((x_mid_blk + 12.0, 12.0), (x_mid_blk + 12.0, 19.0), dxfattribs={"layer": "1_TABLE"})

    msp.add_text("阶段标记", dxfattribs={"height": 2.3, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
        (0.5 * (x_mid_blk + x_wt), 22.5), align=TextEntityAlignment.MIDDLE_CENTER
    )
    msp.add_text("重量", dxfattribs={"height": 2.3, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
        (0.5 * (x_wt + x_sc), 22.5), align=TextEntityAlignment.MIDDLE_CENTER
    )
    msp.add_text("比例", dxfattribs={"height": 2.3, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
        (0.5 * (x_sc + x_right_blk), 22.5), align=TextEntityAlignment.MIDDLE_CENTER
    )

    wt_val = str(data.get("weight_str", "") or "").strip()
    if wt_val and wt_val != "TBD":
        msp.add_text(wt_val, dxfattribs={"height": 2.0, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
            (0.5 * (x_wt + x_sc), 15.5), align=TextEntityAlignment.MIDDLE_CENTER
        )
    sc_val = str(data.get("title_scale_str", "") or "").strip()
    if sc_val:
        msp.add_text(sc_val, dxfattribs={"height": 2.1, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
            (0.5 * (x_sc + x_right_blk), 15.5), align=TextEntityAlignment.MIDDLE_CENTER
        )

    # Row 7 (Y: 5..12): 共   张   第   张
    sheet_txt = data.get("sheet_str", "共    张    第    张")
    msp.add_text(
        sheet_txt,
        dxfattribs={"height": 2.4, "style": "GB_SONG", "layer": "6_TEXT"},
    ).set_placement((0.5 * (x_mid_blk + x_right_blk), 8.5), align=TextEntityAlignment.MIDDLE_CENTER)

    # === RIGHT BLOCK (X: 242.0 to 292.0, width 50mm, 3 tiers) ===
    msp.add_line((x_right_blk, 20.0), (tb_x1, 20.0), dxfattribs={"layer": "1_TABLE"})
    msp.add_line((x_right_blk, 37.0), (tb_x1, 37.0), dxfattribs={"layer": "1_TABLE"})
    cx_r = 0.5 * (x_right_blk + tb_x1)

    # Top cell (Y: 37..54): "单位" header preserved, VALUE STRICTLY EMPTY
    msp.add_text("单位", dxfattribs={"height": 2.7, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
        (cx_r, 49.5), align=TextEntityAlignment.MIDDLE_CENTER
    )

    # Middle cell (Y: 20..37): 图样名称 + Component/System title
    msp.add_text("图样名称", dxfattribs={"height": 2.6, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
        (cx_r, 33.2), align=TextEntityAlignment.MIDDLE_CENTER
    )
    od = float(data.get("od", 25.4))
    if "title_name" in data and data["title_name"]:
        name_txt = str(data["title_name"])
    elif is_assembly:
        raw_name = str(data.get("drawing_name", "光学系统总装图") or "光学系统总装图").strip()
        name_txt = raw_name if "总装" in raw_name else f"{raw_name}总装图"
    else:
        name_txt = str(data.get("drawing_name", f"\u03a6{od:.1f} 光学零件") or f"\u03a6{od:.1f} 光学零件")

    name_lines = [ln.strip() for ln in name_txt.splitlines() if ln.strip()][:2] or [name_txt.strip()]
    if len(name_lines) == 1:
        h_name = _fit_title_h(name_lines[0], 45.5, 3.6, min_h=1.9)
        msp.add_text(name_lines[0], dxfattribs={"height": h_name, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
            (cx_r, 25.5), align=TextEntityAlignment.MIDDLE_CENTER
        )
    else:
        y_offsets = (28.0, 23.0)
        for ln_txt, y_ln in zip(name_lines, y_offsets):
            h_ln = _fit_title_h(ln_txt, 45.5, 2.75, min_h=1.7)
            msp.add_text(ln_txt, dxfattribs={"height": h_ln, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
                (cx_r, y_ln), align=TextEntityAlignment.MIDDLE_CENTER
            )

    # Bottom cell (Y: 5..20): (图样代号)
    dwg_code = str(data.get("title_code", data.get("drawing_code", "(图样代号)")) or "(图样代号)")
    if dwg_code in ("OPT-00", "OPT-ASM"):
        dwg_code = "(图样代号)"
    h_code = _fit_title_h(dwg_code, 45.5, 3.0, min_h=1.8)
    msp.add_text(dwg_code, dxfattribs={"height": h_code, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
        (cx_r, 12.5), align=TextEntityAlignment.MIDDLE_CENTER
    )


def _wrap_cad_note_line(note: str, max_units: float = 28.5) -> List[str]:
    """Wrap a Chinese/ASCII mixed note cleanly without breaking short numbers or trailing punctuation."""
    no_line_start = set("，。；：、,.!?;:）)％%")
    tokens = re.findall(r"[A-Za-z0-9.\-_+/~≤≥±°℃Φ#]+|.", note)
    lines: List[str] = []
    cur = ""
    cur_u = 0.0
    for tok in tokens:
        w = sum(1.0 if ord(ch) > 127 else 0.54 for ch in tok)
        if cur and (cur_u + w > max_units) and not (len(tok) == 1 and tok in no_line_start):
            lines.append(cur)
            cur = tok.lstrip() if tok == " " else tok
            cur_u = sum(1.0 if ord(ch) > 127 else 0.54 for ch in cur)
        else:
            cur += tok
            cur_u += w
    if cur:
        lines.append(cur)
    return lines or [""]


def _layout_technical_notes(notes: List[str], x: float = 28.5, y_top: float = 95.0) -> Tuple[float, List[List[str]]]:
    """Text height and wrapped lines of the notes block.

    A short block keeps the template layout (2.75 mm text, wide lines above the title block).
    A block that would run down beside the title block (its top edge is Y=54, left edge X=112)
    is re-wrapped to stay left of X=109 and shrunk until it ends above the frame."""
    def wrap(height: float, max_units: float) -> List[List[str]]:
        return [[ln for seg in note.split("\n") for ln in _wrap_cad_note_line(seg, max_units=max_units)] for note in notes]

    def block_height(height: float, wrapped: List[List[str]]) -> float:
        return sum(len(lines) * 1.6 * height + (1.2 / 2.75) * height for lines in wrapped)

    wide = wrap(2.75, 28.5)
    if y_top - block_height(2.75, wide) >= 57.0:
        return 2.75, wide
    em_per_height = 1.47                      # paper width of one full-width character per mm of text height
    for height in (2.75, 2.5, 2.3, 2.1, 1.9, 1.75):
        narrow = wrap(height, (109.0 - x) / (em_per_height * height))
        if y_top - block_height(height, narrow) >= 8.0:
            return height, narrow
    return 1.75, narrow


def _draw_dxf_technical_notes(msp: Any, notes: List[str], x: float = 28.5, y_top: float = 95.0):
    """Draw Chinese GB/T 13323 numbered technical notes in the bottom-left zone."""
    height, wrapped = _layout_technical_notes(notes, x=x, y_top=y_top)
    cur_y = y_top
    for lines in wrapped:
        for line in lines:
            msp.add_text(
                line,
                dxfattribs={"height": height, "style": "GB_SONG", "layer": "6_TEXT"},
            ).set_placement((x, cur_y), align=TextEntityAlignment.TOP_LEFT)
            cur_y -= 1.6 * height
        cur_y -= (1.2 / 2.75) * height



def _draw_dxf_arrow(
    msp: Any,
    tip: Tuple[float, float],
    direction: Tuple[float, float],
    size: float = 3.2,
    filled: bool = False,
) -> Tuple[float, float]:
    """Draw slender CAD closed-blank arrowhead at tip; hollow style keeps center spine to tip."""
    dx, dy = direction
    length = math.hypot(dx, dy)
    if length == 0:
        return tip
    ux, uy = dx / length, dy / length
    px, py = -uy, ux

    w = size * 0.21 if not filled else size * 0.34
    p_base = (tip[0] - ux * size, tip[1] - uy * size)
    p_left = (p_base[0] + px * w, p_base[1] + py * w)
    p_right = (p_base[0] - px * w, p_base[1] - py * w)

    if filled:
        msp.add_solid([tip, p_left, p_right], dxfattribs={"layer": "5_DIMENSION", "color": 3})
        return p_base
    msp.add_lwpolyline([tip, p_left, p_right], close=True, dxfattribs={"layer": "5_DIMENSION", "color": 3})
    return tip


def _draw_dxf_leader(
    msp: Any,
    tip: Tuple[float, float],
    angle_deg: float,
    length: float,
    shoulder_len: float,
    text_upper: str,
    text_lower: Optional[str] = None,
    arrow_size: float = 2.4,
):
    """Draw standard optical radius/chamfer leader with hollow CAD arrowhead and shoulder text."""
    rad = math.radians(angle_deg)
    p2 = (tip[0] + length * math.cos(rad), tip[1] + length * math.sin(rad))
    shoulder_sign = 1.0 if math.cos(rad) >= 0 else -1.0
    p3 = (p2[0] + shoulder_sign * shoulder_len, p2[1])

    p_base = _draw_dxf_arrow(msp, tip, (-math.cos(rad), -math.sin(rad)), size=arrow_size)
    msp.add_line(p_base, p2, dxfattribs={"layer": "5_DIMENSION", "color": 3})
    msp.add_line(p2, p3, dxfattribs={"layer": "5_DIMENSION", "color": 3})

    align_u = TextEntityAlignment.BOTTOM_LEFT if shoulder_sign > 0 else TextEntityAlignment.BOTTOM_RIGHT
    t_u = msp.add_text(text_upper, dxfattribs={"height": 2.4, "style": "HZ_STYLE", "layer": "6_TEXT"})
    tx = p2[0] + (1.0 if shoulder_sign > 0 else -1.0)
    t_u.set_placement((tx, p2[1] + 0.6), align=align_u)

    if text_lower:
        align_l = TextEntityAlignment.TOP_LEFT if shoulder_sign > 0 else TextEntityAlignment.TOP_RIGHT
        t_l = msp.add_text(text_lower, dxfattribs={"height": 2.0, "style": "HZ_STYLE", "layer": "6_TEXT"})
        t_l.set_placement((tx, p2[1] - 0.6), align=align_l)


def _draw_dxf_balloon(msp: Any, center: Tuple[float, float], number: int, start_pt: Tuple[float, float]):
    """Draw component balloon circle with number and leader line."""
    r = 3.2
    msp.add_circle(center=center, radius=r, dxfattribs={"layer": "5_DIMENSION", "color": 3})
    t = msp.add_text(str(number), dxfattribs={"height": 2.8, "style": "HZ_STYLE", "layer": "6_TEXT"})
    t.set_placement(center, align=TextEntityAlignment.MIDDLE_CENTER)

    dx = center[0] - start_pt[0]
    dy = center[1] - start_pt[1]
    dist = math.hypot(dx, dy)
    if dist > r:
        ux, uy = dx / dist, dy / dist
        p_circ = (center[0] - ux * r, center[1] - uy * r)
        msp.add_line(start_pt, p_circ, dxfattribs={"layer": "5_DIMENSION", "color": 3})
        msp.add_circle(center=start_pt, radius=0.6, dxfattribs={"layer": "5_DIMENSION", "color": 3})


def _draw_dxf_linear_dimension(
    msp: Any,
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    dim_offset: float,
    text: str,
    orientation: str = "horizontal",
):
    """
    Draw a clean, 100% CAD-compatible dimension line with extension lines, solid arrows, and text.
    orientation: 'horizontal' or 'vertical'.
    """
    if orientation == "horizontal":
        y_dim = p1[1] + dim_offset
        x_min = min(p1[0], p2[0])
        x_max = max(p1[0], p2[0])

        ext_overshoot = 2.0 if dim_offset > 0 else -2.0
        msp.add_line((x_min, p1[1]), (x_min, y_dim + ext_overshoot), dxfattribs={"layer": "5_DIMENSION", "color": 3})
        msp.add_line((x_max, p2[1]), (x_max, y_dim + ext_overshoot), dxfattribs={"layer": "5_DIMENSION", "color": 3})
        msp.add_line((x_min, y_dim), (x_max, y_dim), dxfattribs={"layer": "5_DIMENSION", "color": 3})

        span = x_max - x_min
        if span >= 8.0:
            _draw_dxf_arrow(msp, (x_min, y_dim), (-1.0, 0.0), size=2.2)
            _draw_dxf_arrow(msp, (x_max, y_dim), (1.0, 0.0), size=2.2)
            t = msp.add_text(text, dxfattribs={"height": 2.4, "style": "HZ_STYLE", "layer": "6_TEXT"})
            t.set_placement(((x_min + x_max) / 2.0, y_dim + 0.8), align=TextEntityAlignment.BOTTOM_CENTER)
        else:
            _draw_dxf_arrow(msp, (x_min, y_dim), (1.0, 0.0), size=2.0)
            _draw_dxf_arrow(msp, (x_max, y_dim), (-1.0, 0.0), size=2.0)
            msp.add_line((x_min - 3.5, y_dim), (x_min, y_dim), dxfattribs={"layer": "5_DIMENSION", "color": 3})
            msp.add_line((x_max, y_dim), (x_max + 3.5, y_dim), dxfattribs={"layer": "5_DIMENSION", "color": 3})
            t = msp.add_text(text, dxfattribs={"height": 2.1, "style": "HZ_STYLE", "layer": "6_TEXT"})
            t.set_placement(((x_min + x_max) / 2.0, y_dim + 0.8), align=TextEntityAlignment.BOTTOM_CENTER)

    else:  # vertical
        x_dim = p1[0] + dim_offset
        y_min = min(p1[1], p2[1])
        y_max = max(p1[1], p2[1])

        ext_overshoot = 2.0 if dim_offset > 0 else -2.0
        msp.add_line((p1[0], y_min), (x_dim + ext_overshoot, y_min), dxfattribs={"layer": "5_DIMENSION", "color": 3})
        msp.add_line((p2[0], y_max), (x_dim + ext_overshoot, y_max), dxfattribs={"layer": "5_DIMENSION", "color": 3})
        msp.add_line((x_dim, y_min), (x_dim, y_max), dxfattribs={"layer": "5_DIMENSION", "color": 3})

        v_span = y_max - y_min
        if v_span >= 8.0:
            _draw_dxf_arrow(msp, (x_dim, y_min), (0.0, -1.0), size=2.2)
            _draw_dxf_arrow(msp, (x_dim, y_max), (0.0, 1.0), size=2.2)
        else:
            _draw_dxf_arrow(msp, (x_dim, y_min), (0.0, 1.0), size=2.0)
            _draw_dxf_arrow(msp, (x_dim, y_max), (0.0, -1.0), size=2.0)
            msp.add_line((x_dim, y_min - 3.5), (x_dim, y_min), dxfattribs={"layer": "5_DIMENSION", "color": 3})
            msp.add_line((x_dim, y_max), (x_dim, y_max + 3.5), dxfattribs={"layer": "5_DIMENSION", "color": 3})

        t = msp.add_text(text, dxfattribs={"height": 2.5, "style": "HZ_STYLE", "layer": "6_TEXT", "rotation": 90.0})
        t.set_placement((x_dim - 1.2, (y_min + y_max) / 2.0), align=TextEntityAlignment.BOTTOM_CENTER)


def _draw_dxf_optical_table(msp: Any, data: Dict[str, Any], x_start: float = 25.0, y_top: float = 205.0):
    """
    Draw the Chinese GB/T 13323 top-left 17-row Optical Requirements & Tolerances table
    anchored flush to the inner top-left corner (X: 25.0 to 94.0).
    """
    col1_w = 43.0
    col2_w = 26.0
    tbl_w = col1_w + col2_w  # 69.0mm
    x_mid = x_start + col1_w
    x_end = x_start + tbl_w

    h_top_hdr = 7.5
    h_row = 5.4
    h_part_hdr = 5.5

    row_heights = [h_top_hdr] + [h_row] * 7 + [h_part_hdr] + [h_row] * 8
    y_edges = [y_top]
    for rh in row_heights:
        y_edges.append(y_edges[-1] - rh)
    y_bottom = y_edges[-1]

    # Outer table outline
    msp.add_lwpolyline(
        [(x_start, y_bottom), (x_end, y_bottom), (x_end, y_top), (x_start, y_top)],
        close=True,
        dxfattribs={"layer": "1_TABLE"},
    )
    for yb in y_edges[1:-1]:
        msp.add_line((x_start, yb), (x_end, yb), dxfattribs={"layer": "1_TABLE"})

    # Vertical column divider splits material rows (1..7) and part rows (9..16), skipping rows 0 and 8
    msp.add_line((x_mid, y_edges[8]), (x_mid, y_edges[1]), dxfattribs={"layer": "1_TABLE"})
    msp.add_line((x_mid, y_bottom), (x_mid, y_edges[9]), dxfattribs={"layer": "1_TABLE"})

    cx_all = x_start + 0.5 * tbl_w
    cx_c1 = x_start + 0.5 * col1_w
    cx_c2 = x_mid + 0.5 * col2_w

    # Row 0: 180-deg rotated "(图样代号)(存储代号)"
    top_code = str(data.get("table_header_code", "(图样代号)(存储代号)"))
    msp.add_text(
        top_code,
        dxfattribs={"height": 2.9, "style": "GB_SONG", "layer": "6_TEXT", "rotation": 180.0},
    ).set_placement((cx_all, 0.5 * (y_edges[0] + y_edges[1])), align=TextEntityAlignment.MIDDLE_CENTER)

    # Rows 1..7: Material Requirements
    efl_str = str(data.get("efl_str", "") or "").strip()
    if efl_str not in ("", "-") and not efl_str.endswith("mm"):
        efl_str = f"{efl_str}mm"
    bfl_str = str(data.get("bfl_str", "") or "").strip()
    if bfl_str not in ("", "-") and not bfl_str.endswith("mm"):
        bfl_str = f"{bfl_str}mm"
    if "d0_str" in data and data["d0_str"] is not None:
        d0_str = str(data["d0_str"]).strip()
    else:
        ca_val = float(data.get("ca", 0.0))
        d0_str = f"{ca_val:g}mm" if ca_val > 0 else ""

    mat_rows: List[Tuple[Any, str]] = [
        ([("△ ", 1.0, 0.0), ("n", 1.0, 0.0), ("d", 0.72, -0.18)], data.get("delta_nd", "2C")),
        ([("△(", 1.0, 0.0), ("n", 1.0, 0.0), ("F", 0.68, -0.18), ("-n", 1.0, 0.0), ("C", 0.68, -0.18), (")", 1.0, 0.0)], data.get("delta_nf_nc", "2C")),
        ("光学均匀性", data.get("homogeneity", "")),
        ("应力双折射", data.get("stress_biref", "")),
        ("光学吸收系数", data.get("absorption", "")),
        ("条纹度", data.get("striae", "")),
        ("气泡度", data.get("bubbles", "")),
    ]
    for idx, (lbl, val) in enumerate(mat_rows):
        ym = 0.5 * (y_edges[1 + idx] + y_edges[2 + idx])
        if isinstance(lbl, list):
            _draw_dxf_subscript_text(msp, lbl, cx_c1, ym, base_height=2.55, style="GB_SONG", italic_chars=True)
        else:
            msp.add_text(lbl, dxfattribs={"height": 2.55, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
                (cx_c1, ym), align=TextEntityAlignment.MIDDLE_CENTER
            )
        if val:
            msp.add_text(str(val), dxfattribs={"height": 2.55, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
                (cx_c2, ym), align=TextEntityAlignment.MIDDLE_CENTER
            )

    # Row 8: Subheader "对零件的要求"
    msp.add_text(
        "对零件的要求",
        dxfattribs={"height": 2.7, "style": "GB_SONG", "layer": "6_TEXT"},
    ).set_placement((cx_all, 0.5 * (y_edges[8] + y_edges[9])), align=TextEntityAlignment.MIDDLE_CENTER)

    # Rows 9..16: Part Tolerances & Paraxial Params
    part_rows: List[Tuple[Any, str, bool]] = [
        ("N", data.get("N", "3"), True),
        ("△ N", data.get("delta_N", "0.3"), True),
        ("△ R", data.get("delta_R", "A"), True),
        ("B", data.get("B", "IV"), True),
        ("χ", data.get("wedge_chi", "3'"), True),
        ("f", efl_str, True),
        ([("S", 1.0, 0.0), ("f", 0.72, -0.18)], bfl_str, False),
        ([("D", 1.0, 0.0), ("0", 0.72, -0.18)], d0_str, False),
    ]
    for idx, (lbl, val, is_oblique) in enumerate(part_rows):
        ym = 0.5 * (y_edges[9 + idx] + y_edges[10 + idx])
        if isinstance(lbl, list):
            _draw_dxf_subscript_text(msp, lbl, cx_c1, ym, base_height=2.55, style="GB_SONG", italic_chars=True)
        else:
            attrs: Dict[str, Any] = {"height": 2.55, "style": "GB_SONG", "layer": "6_TEXT"}
            if is_oblique:
                attrs["oblique"] = 12.0
            msp.add_text(lbl, dxfattribs=attrs).set_placement((cx_c1, ym), align=TextEntityAlignment.MIDDLE_CENTER)
        if val:
            v_txt = str(val)
            v_units = sum(1.12 if ord(ch) > 127 else 0.62 for ch in v_txt)
            v_h = max(1.5, min(2.6, (col2_w - 2.0) / max(v_units, 1e-6)))
            msp.add_text(v_txt, dxfattribs={"height": v_h, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
                (cx_c2, ym), align=TextEntityAlignment.MIDDLE_CENTER
            )


def _draw_dxf_assembly_table(msp: Any, data: Dict[str, Any], x_start: float = 25.0, y_top: float = 205.0):
    """
    Draw Chinese GB/T 13323 top-left System & Optical BOM table for Optical System Assembly
    anchored flush to X=25.0..94.0 (matching the primary template visual grammar).
    """
    tbl_w = 69.0
    x_end = x_start + tbl_w
    c1 = x_start + 8.0
    c2 = x_start + 34.0
    c3 = x_start + 61.0

    assembly_elements = data.get("assembly_elements", [])
    rows_bom: List[Tuple[str, str, str, str]] = []
    for elem in assembly_elements:
        e_idx = elem.get("elem_index", 1)
        comps = elem.get("components", [])
        mat_str = "/".join(c.get("material", "") for c in comps)
        if len(comps) == 1:
            desc = f"单透镜 L{e_idx}"
        elif len(comps) == 2:
            desc = f"双胶合组 G{e_idx}"
        else:
            desc = f"胶合组 G{e_idx}"
        rows_bom.append((str(e_idx), desc, mat_str, "1"))

    # the barrel row is a placeholder of the template; drawings of bare optics switch it off
    if data.get("bom_show_barrel", True):
        rows_bom.append((str(len(rows_bom) + 1), "镜筒及隔圈", "6061-T6", "1"))

    h_top_hdr = 8.5
    h_sub_hdr = 6.0
    h_sys_row = 5.5
    h_bom_row = 5.5

    def _fmt_mm(v: Any) -> str:
        s = str(v or "-").strip()
        if s in ("", "-", "Not determined"):
            return "-" if s in ("", "-") else s
        return s if s.endswith("mm") else f"{s}mm"

    sys_params = [
        ("有效焦距 f'", _fmt_mm(data.get("efl_str", "-"))),
        ("总轨长 TOTR", _fmt_mm(data.get("totr_str", "-"))),
        ("后截距 BFL", _fmt_mm(data.get("bfl_str", "-"))),
        ("数值孔径 / F/#", str(data.get("f_num_str", "-"))),
        ("工作波段", str(data.get("spectral_range", "-"))),
    ]

    y_cur = y_top
    y_e_tophdr = y_cur - h_top_hdr
    y_e_syshdr = y_e_tophdr - h_sub_hdr
    y_e_sys = y_e_syshdr - len(sys_params) * h_sys_row
    y_e_bomhdr = y_e_sys - h_sub_hdr
    y_e_bom = y_e_bomhdr - len(rows_bom) * h_bom_row

    # Outer table outline
    msp.add_lwpolyline(
        [(x_start, y_e_bom), (x_end, y_e_bom), (x_end, y_top), (x_start, y_top)],
        close=True,
        dxfattribs={"layer": "1_TABLE"},
    )
    cx_all = x_start + 0.5 * tbl_w

    # Row 0: 180-deg rotated code header
    msp.add_line((x_start, y_e_tophdr), (x_end, y_e_tophdr), dxfattribs={"layer": "1_TABLE"})
    msp.add_text(
        str(data.get("table_header_code", "(图样代号)(存储代号)")),
        dxfattribs={"height": 2.9, "style": "GB_SONG", "layer": "6_TEXT", "rotation": 180.0},
    ).set_placement((cx_all, 0.5 * (y_top + y_e_tophdr)), align=TextEntityAlignment.MIDDLE_CENTER)

    # Row 1: Subheader "系统主要参数"
    msp.add_line((x_start, y_e_syshdr), (x_end, y_e_syshdr), dxfattribs={"layer": "1_TABLE"})
    msp.add_text("系统主要参数", dxfattribs={"height": 2.6, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
        (cx_all, 0.5 * (y_e_tophdr + y_e_syshdr)), align=TextEntityAlignment.MIDDLE_CENTER
    )

    # System parameter rows
    x_sys_mid = x_start + 36.0
    msp.add_line((x_sys_mid, y_e_sys), (x_sys_mid, y_e_syshdr), dxfattribs={"layer": "1_TABLE"})
    for idx, (p_lbl, p_val) in enumerate(sys_params):
        yr_top = y_e_syshdr - idx * h_sys_row
        yr_bot = yr_top - h_sys_row
        msp.add_line((x_start, yr_bot), (x_end, yr_bot), dxfattribs={"layer": "1_TABLE"})
        ym = 0.5 * (yr_top + yr_bot)
        msp.add_text(p_lbl, dxfattribs={"height": 2.3, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
            (0.5 * (x_start + x_sys_mid), ym), align=TextEntityAlignment.MIDDLE_CENTER
        )
        h_v = 2.3 if len(p_val) <= 14 else 1.85
        msp.add_text(p_val, dxfattribs={"height": h_v, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
            (0.5 * (x_sys_mid + x_end), ym), align=TextEntityAlignment.MIDDLE_CENTER
        )

    # BOM Subheader
    msp.add_line((x_start, y_e_bomhdr), (x_end, y_e_bomhdr), dxfattribs={"layer": "1_TABLE"})
    msp.add_line((c1, y_e_bom), (c1, y_e_sys), dxfattribs={"layer": "1_TABLE"})
    msp.add_line((c2, y_e_bom), (c2, y_e_sys), dxfattribs={"layer": "1_TABLE"})
    msp.add_line((c3, y_e_bom), (c3, y_e_sys), dxfattribs={"layer": "1_TABLE"})
    ym_bh = 0.5 * (y_e_sys + y_e_bomhdr)
    for title, (xa, xb) in [("序号", (x_start, c1)), ("名称", (c1, c2)), ("材料", (c2, c3)), ("数量", (c3, x_end))]:
        msp.add_text(title, dxfattribs={"height": 2.3, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
            (0.5 * (xa + xb), ym_bh), align=TextEntityAlignment.MIDDLE_CENTER
        )

    # BOM rows
    for idx, (it, desc, mat, qty) in enumerate(rows_bom):
        yr_top = y_e_bomhdr - idx * h_bom_row
        yr_bot = yr_top - h_bom_row
        if idx < len(rows_bom) - 1:
            msp.add_line((x_start, yr_bot), (x_end, yr_bot), dxfattribs={"layer": "1_TABLE"})
        ym = 0.5 * (yr_top + yr_bot)
        msp.add_text(it, dxfattribs={"height": 2.2, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
            (0.5 * (x_start + c1), ym), align=TextEntityAlignment.MIDDLE_CENTER
        )
        msp.add_text(desc, dxfattribs={"height": 2.1, "style": "GB_SONG", "layer": "6_TEXT"}).set_placement(
            (0.5 * (c1 + c2), ym), align=TextEntityAlignment.MIDDLE_CENTER
        )
        h_mat = 2.1 if len(mat) <= 13 else 1.65
        msp.add_text(mat, dxfattribs={"height": h_mat, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
            (0.5 * (c2 + c3), ym), align=TextEntityAlignment.MIDDLE_CENTER
        )
        msp.add_text(qty, dxfattribs={"height": 2.2, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
            (0.5 * (c3 + x_end), ym), align=TextEntityAlignment.MIDDLE_CENTER
        )


def _draw_dxf_radial_dim_gbt(
    msp: Any,
    p_surf: Tuple[float, float],
    center_paper: Tuple[float, float],
    label: str,
    ext_past_surf: float = 0.0,
    center_dist_cap: float = 28.0,
    show_center_cross: bool = True,
    show_outer_cross: bool = False,
    arrow_from_center_side: bool = True,
    text_offset_t: float = 0.5,
    jog_spec: Optional[Dict[str, Any]] = None,
):
    """
    Draw GB/T 13323 radial dimension with straight or Z-jogged (foreshortened) ray
    aligned to curvature center, hollow CAD arrowhead stopping at base, '+' center mark,
    and upright unsigned R text.
    """
    vx = p_surf[0] - center_paper[0]
    vy = p_surf[1] - center_paper[1]
    dist = math.hypot(vx, vy)
    if dist < 1e-6:
        return
    ux, uy = vx / dist, vy / dist
    dx_c, dy_c = -ux, -uy  # points from surface toward true curvature center

    arrow_sz = 3.2
    txt_h = float(jog_spec.get("text_height", 2.65)) if jog_spec else 2.65
    if jog_spec:
        out_arm = float(jog_spec.get("out_arm", 0.0))
        arm1 = float(jog_spec.get("arm1", 26.0))
        zig_back = float(jog_spec.get("zig_back", -6.0))
        zig_perp = float(jog_spec.get("zig_perp", -3.2))
        arm2 = float(jog_spec.get("arm2", 14.0))
        nx, ny = -dy_c, dx_c

        if out_arm > 0.0:
            est_w = _measure_dxf_text_width(label, txt_h, "GB_NUM") + 4.5
            out_arm = max(out_arm, est_w)
            p_base = _draw_dxf_arrow(msp, p_surf, (dx_c, dy_c), size=arrow_sz)
            p_out = (p_surf[0] + ux * out_arm, p_surf[1] + uy * out_arm)
            msp.add_line(p_out, p_base, dxfattribs={"layer": "5_DIMENSION", "color": 3})
            start_in = p_surf
            p_txt_a, p_txt_b = p_out, p_base
            t_txt = float(jog_spec.get("text_t", 0.45))
        else:
            p_base = _draw_dxf_arrow(msp, p_surf, (ux, uy), size=arrow_sz)
            start_in = p_base
            p_txt_a = None
            p_txt_b = None
            t_txt = float(jog_spec.get("text_t", 0.65))

        p_j0 = (p_surf[0] + dx_c * arm1, p_surf[1] + dy_c * arm1)
        p_j1 = (p_j0[0] + dx_c * zig_back + nx * zig_perp, p_j0[1] + dy_c * zig_back + ny * zig_perp)
        p_cross = (p_j1[0] + dx_c * arm2, p_j1[1] + dy_c * arm2)

        msp.add_line(start_in, p_j0, dxfattribs={"layer": "5_DIMENSION", "color": 3})
        msp.add_line(p_j0, p_j1, dxfattribs={"layer": "5_DIMENSION", "color": 3})
        msp.add_line(p_j1, p_cross, dxfattribs={"layer": "5_DIMENSION", "color": 3})
        if show_center_cross:
            _draw_dxf_center_cross(msp, p_cross, half_size=1.8)

        if p_txt_a is None:
            on_seg = str(jog_spec.get("text_on_seg", "arm1"))
            if on_seg == "arm2":
                p_txt_a, p_txt_b = p_j1, p_cross
            else:
                p_txt_a, p_txt_b = p_surf, p_j0
        p_txt_base = (
            p_txt_a[0] + (p_txt_b[0] - p_txt_a[0]) * t_txt,
            p_txt_a[1] + (p_txt_b[1] - p_txt_a[1]) * t_txt,
        )
    else:
        use_dist = min(dist, center_dist_cap)
        p_cross = (p_surf[0] - ux * use_dist, p_surf[1] - uy * use_dist)
        p_outer = (p_surf[0] + ux * ext_past_surf, p_surf[1] + uy * ext_past_surf)

        arrow_dir = (ux, uy) if arrow_from_center_side else (-ux, -uy)
        p_base = _draw_dxf_arrow(msp, p_surf, arrow_dir, size=arrow_sz)

        if ext_past_surf > 0.5 and use_dist > 0.5:
            if arrow_from_center_side:
                msp.add_line(p_cross, p_base, dxfattribs={"layer": "5_DIMENSION", "color": 3})
                msp.add_line(p_surf, p_outer, dxfattribs={"layer": "5_DIMENSION", "color": 3})
            else:
                msp.add_line(p_cross, p_surf, dxfattribs={"layer": "5_DIMENSION", "color": 3})
                msp.add_line(p_base, p_outer, dxfattribs={"layer": "5_DIMENSION", "color": 3})
        elif ext_past_surf > 0.5:
            msp.add_line(p_base, p_outer, dxfattribs={"layer": "5_DIMENSION", "color": 3})
        else:
            msp.add_line(p_cross, p_base, dxfattribs={"layer": "5_DIMENSION", "color": 3})

        if show_center_cross and use_dist > 2.0:
            _draw_dxf_center_cross(msp, p_cross, half_size=1.8)
        if show_outer_cross and ext_past_surf > 2.0:
            _draw_dxf_center_cross(msp, p_outer, half_size=1.8)

        if ext_past_surf >= 10.0:
            p_txt_base = (p_surf[0] + ux * (ext_past_surf * text_offset_t), p_surf[1] + uy * (ext_past_surf * text_offset_t))
        else:
            t_pos = max(0.18, min(0.82, text_offset_t))
            p_txt_base = (
                p_cross[0] + (p_surf[0] - p_cross[0]) * t_pos,
                p_cross[1] + (p_surf[1] - p_cross[1]) * t_pos,
            )

    ang_deg = math.degrees(math.atan2(uy, ux))
    if ang_deg > 90.0:
        ang_deg -= 180.0
    elif ang_deg <= -90.0:
        ang_deg += 180.0

    ang_r = math.radians(ang_deg)
    tx_n, ty_n = -math.sin(ang_r), math.cos(ang_r)
    p_txt = (p_txt_base[0] + tx_n * 0.9, p_txt_base[1] + ty_n * 0.9)

    t = msp.add_text(
        label,
        dxfattribs={"height": txt_h, "style": "GB_NUM", "layer": "6_TEXT", "rotation": ang_deg},
    )
    t.set_placement(p_txt, align=TextEntityAlignment.BOTTOM_CENTER)


def _draw_dxf_chamfer_callout(
    msp: Any,
    tip: Tuple[float, float],
    angle_deg: float,
    stem_len: float,
    shoulder_len: float,
    prefix: str,
    tol_upper: str = "+0.2",
    tol_lower: str = "0",
    suffix: str = "\u00d745\u00b0",
    show_arrow: bool = False,
):
    """Draw chamfer leader with inline upper/lower tolerance stack (e.g. 8--0.3 +0.2/0 x45 deg)."""
    h_main = 2.65
    h_tol = 1.55

    w_pre = _measure_dxf_text_width(prefix, h_main, "GB_NUM")
    w_top = _measure_dxf_text_width(tol_upper, h_tol, "GB_NUM")
    w_bot = _measure_dxf_text_width(tol_lower, h_tol, "GB_NUM")
    w_tol = max(w_top, w_bot)
    w_suf = _measure_dxf_text_width(suffix, h_main, "GB_NUM")
    has_tol = bool(tol_upper or tol_lower)
    gap_pre = 0.12 if has_tol else 0.0
    gap_post = 0.15 if has_tol else 0.0
    tot_w = w_pre + gap_pre + w_tol + gap_post + w_suf

    rad = math.radians(angle_deg)
    p2 = (tip[0] + stem_len * math.cos(rad), tip[1] + stem_len * math.sin(rad))
    dir_sign = 1.0 if math.cos(rad) >= 0 else -1.0
    sh_use = max(shoulder_len, tot_w + 3.5)
    p3 = (p2[0] + dir_sign * sh_use, p2[1])

    start_pt = _draw_dxf_arrow(msp, tip, (-math.cos(rad), -math.sin(rad)), size=2.4) if show_arrow else tip
    msp.add_line(start_pt, p2, dxfattribs={"layer": "6_TEXT", "color": 7})
    msp.add_line(p2, p3, dxfattribs={"layer": "6_TEXT", "color": 7})

    cx = 0.5 * (p2[0] + p3[0])
    y_line = p2[1] + 0.75
    x_cur = cx - 0.5 * tot_w

    msp.add_text(prefix, dxfattribs={"height": h_main, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
        (x_cur, y_line), align=TextEntityAlignment.BOTTOM_LEFT
    )
    x_cur += w_pre + gap_pre

    if has_tol:
        cx_tol = x_cur + 0.5 * w_tol
        if tol_upper:
            msp.add_text(tol_upper, dxfattribs={"height": h_tol, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
                (cx_tol, y_line + 2.00), align=TextEntityAlignment.BOTTOM_CENTER
            )
        if tol_lower:
            x_low = cx_tol + (0.32 if (tol_upper.startswith("+") and len(tol_upper) > len(tol_lower)) else 0.0)
            msp.add_text(tol_lower, dxfattribs={"height": h_tol, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
                (x_low, y_line - 0.10), align=TextEntityAlignment.BOTTOM_CENTER
            )
        x_cur += w_tol + gap_post

    msp.add_text(suffix, dxfattribs={"height": h_main, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
        (x_cur, y_line), align=TextEntityAlignment.BOTTOM_LEFT
    )


def _draw_dxf_linear_dim_gbt(
    msp: Any,
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    y_dim: float,
    text: str,
    shoulder_side: Optional[str] = None,
    shoulder_len: float = 13.0,
    shoulder_anchor_x: Optional[float] = None,
    internal_arrows: Optional[bool] = None,
    draw_ext1: bool = True,
    draw_ext2: bool = True,
    draw_span_line: bool = True,
):
    """
    Draw horizontal linear dimension cleanly with extension lines from anchor points
    to y_dim, inside/outside hollow CAD arrows, optional shoulder extension, and zero 'mm' suffix.
    """
    if p1[0] <= p2[0]:
        xa, ya = p1
        xb, yb = p2
    else:
        xa, ya = p2
        xb, yb = p1

    overshoot = 2.2 if y_dim >= ya else -2.2
    if draw_ext1:
        msp.add_line((xa, ya), (xa, y_dim + overshoot), dxfattribs={"layer": "5_DIMENSION", "color": 3})
    if draw_ext2:
        msp.add_line((xb, yb), (xb, y_dim + overshoot), dxfattribs={"layer": "5_DIMENSION", "color": 3})

    span = xb - xa
    if internal_arrows is None:
        internal_arrows = span >= 12.5

    if internal_arrows:
        pa = _draw_dxf_arrow(msp, (xa, y_dim), (-1.0, 0.0), size=3.2)
        pb = _draw_dxf_arrow(msp, (xb, y_dim), (1.0, 0.0), size=3.2)
        if draw_span_line and pb[0] > pa[0]:
            msp.add_line(pa, pb, dxfattribs={"layer": "5_DIMENSION", "color": 3})
    else:
        if draw_span_line and span > 0.1:
            msp.add_line((xa, y_dim), (xb, y_dim), dxfattribs={"layer": "5_DIMENSION", "color": 3})
        pa = _draw_dxf_arrow(msp, (xa, y_dim), (1.0, 0.0), size=3.2)
        pb = _draw_dxf_arrow(msp, (xb, y_dim), (-1.0, 0.0), size=3.2)
        msp.add_line((xa - 4.8, y_dim), pa, dxfattribs={"layer": "5_DIMENSION", "color": 3})
        msp.add_line(pb, (xb + 4.8, y_dim), dxfattribs={"layer": "5_DIMENSION", "color": 3})

    txt_h = 2.70
    est_text_w = _measure_dxf_text_width(text, txt_h, "GB_NUM")
    if shoulder_side == "left":
        anchor_l = xa if shoulder_anchor_x is None else min(xa, shoulder_anchor_x)
        sh_eff = max(shoulder_len, est_text_w + 3.5)
        x_end = anchor_l - sh_eff
        start_sh = (xa - 4.5) if not internal_arrows else xa
        if x_end < start_sh:
            msp.add_line((x_end, y_dim), (start_sh, y_dim), dxfattribs={"layer": "5_DIMENSION", "color": 3})
        cx_txt = min(anchor_l - 0.5 * est_text_w - 1.5, 0.5 * (x_end + anchor_l))
        msp.add_text(text, dxfattribs={"height": txt_h, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
            (cx_txt, y_dim + 0.65), align=TextEntityAlignment.BOTTOM_CENTER
        )
    elif shoulder_side == "right":
        anchor_r = xb if shoulder_anchor_x is None else max(xb, shoulder_anchor_x)
        sh_eff = max(shoulder_len, est_text_w + 3.5)
        x_end = anchor_r + sh_eff
        start_sh = (xb + 4.5) if not internal_arrows else xb
        if x_end > start_sh:
            msp.add_line((start_sh, y_dim), (x_end, y_dim), dxfattribs={"layer": "5_DIMENSION", "color": 3})
        cx_txt = max(anchor_r + 0.5 * est_text_w + 1.5, 0.5 * (anchor_r + x_end))
        msp.add_text(text, dxfattribs={"height": txt_h, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
            (cx_txt, y_dim + 0.65), align=TextEntityAlignment.BOTTOM_CENTER
        )
    else:
        msp.add_text(text, dxfattribs={"height": txt_h, "style": "GB_NUM", "layer": "6_TEXT"}).set_placement(
            (0.5 * (xa + xb), y_dim + 0.65), align=TextEntityAlignment.BOTTOM_CENTER
        )


def _draw_dxf_vertical_od_dim(
    msp: Any,
    x_top: float,
    x_bot: float,
    y_bot: float,
    y_top: float,
    x_dim: float,
    od_main: str,
    od_tol: str = "-0.05",
):
    """Draw vertical OD dimension (rotated 90 deg, main number + deviation flanking axis cleanly)."""
    ext_over = 2.4 if x_dim >= max(x_top, x_bot) else -2.4
    msp.add_line((x_bot, y_bot), (x_dim + ext_over, y_bot), dxfattribs={"layer": "5_DIMENSION", "color": 3})
    msp.add_line((x_top, y_top), (x_dim + ext_over, y_top), dxfattribs={"layer": "5_DIMENSION", "color": 3})
    pb = _draw_dxf_arrow(msp, (x_dim, y_bot), (0.0, -1.0), size=3.4)
    pt = _draw_dxf_arrow(msp, (x_dim, y_top), (0.0, 1.0), size=3.4)
    msp.add_line(pb, pt, dxfattribs={"layer": "5_DIMENSION", "color": 3})

    y_mid = 0.5 * (y_bot + y_top)
    if od_tol:
        h_main = 2.85
        h_tol = 1.85
        msp.add_text(
            od_main,
            dxfattribs={"height": h_main, "style": "GB_NUM", "layer": "6_TEXT", "rotation": 90.0},
        ).set_placement((x_dim - 0.9, y_mid - 1.0), align=TextEntityAlignment.BOTTOM_RIGHT)
        # "upper/lower" (e.g. "0/-0.02") is drawn as two stacked limit deviations
        devs = [d.strip() for d in str(od_tol).split("/")] if "/" in str(od_tol) else [str(od_tol)]
        for d_i, dev in enumerate(devs[:2]):
            x_dev = x_dim - 0.65 - (2.35 * (len(devs[:2]) - 1 - d_i))
            msp.add_text(
                dev,
                dxfattribs={"height": h_tol, "style": "GB_NUM", "layer": "6_TEXT", "rotation": 90.0},
            ).set_placement((x_dev, y_mid + 1.0), align=TextEntityAlignment.BOTTOM_LEFT)
    else:
        msp.add_text(
            od_main,
            dxfattribs={"height": 2.6, "style": "GB_NUM", "layer": "6_TEXT", "rotation": 90.0},
        ).set_placement((x_dim - 0.8, y_mid), align=TextEntityAlignment.BOTTOM_CENTER)


def _build_lens_slice_polygon(
    z_front: float,
    z_rear: float,
    r1_draw: float,
    r2_draw: float,
    k1: float,
    k2: float,
    half_od: float,
    scale: float,
    axis_y: float,
    chamfer_front: float = 0.3,
    chamfer_rear: float = 0.3,
    n_pts: int = 44,
    polish_half_front: Optional[float] = None,
    polish_half_rear: Optional[float] = None,
) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[Tuple[float, float]], float, float]:
    """
    Build closed lens slice polygon from -half_od to +half_od with safe protective
    rim chamfers (suppressing chamfer bevel on concave overhangs to avoid rim spikes).
    A surface polished only to a diameter (polish_half_*) continues as a flat land,
    perpendicular to the axis, from that diameter out to the rim.
    Returns (poly, front_pts, rear_pts, z_f_nom_rim, z_r_nom_rim).
    """
    def _land(polish_half: Optional[float]) -> Optional[float]:
        if polish_half is None or not (0.0 < polish_half < half_od - 1e-6):
            return None
        return float(polish_half)

    land_f = _land(polish_half_front)
    land_r = _land(polish_half_rear)

    def _sag_front(y: float) -> float:
        return _calculate_sag(r1_draw, k1, min(abs(y), land_f) if land_f else y)

    def _sag_rear(y: float) -> float:
        return _calculate_sag(r2_draw, k2, min(abs(y), land_r) if land_r else y)

    def _heights(y_max: float, land: Optional[float]) -> List[float]:
        ys = [-y_max + (2.0 * y_max * i) / n_pts for i in range(n_pts + 1)]
        if land and land < y_max:
            ys = sorted(set(ys) | {-land, land})
        return ys

    sag_f_rim = _sag_front(half_od)
    sag_r_rim = _sag_rear(half_od)
    z_f_nom_rim = z_front + sag_f_rim * scale
    z_r_nom_rim = z_rear + sag_r_rim * scale

    cf = min(chamfer_front, half_od * 0.08) if chamfer_front > 0 else 0.0
    cr = min(chamfer_rear, half_od * 0.08) if chamfer_rear > 0 else 0.0
    # Concave rim overhangs (r1<0 left overhang or r2>0 right overhang) keep crisp corner intersection;
    # a flat land ends in a normal rim corner and keeps its protective chamfer.
    if r1_draw < -1e-6 and not land_f:
        cf = 0.0
    if r2_draw > 1e-6 and not land_r:
        cr = 0.0
    if z_r_nom_rim - z_f_nom_rim < (cf + cr + 0.15) * scale:
        cf = 0.0
        cr = 0.0

    y_f_max = half_od - cf
    y_r_max = half_od - cr

    front_pts: List[Tuple[float, float]] = []
    for y in _heights(y_f_max, land_f):
        front_pts.append((z_front + _sag_front(y) * scale, axis_y + y * scale))

    rear_pts: List[Tuple[float, float]] = []
    for y in reversed(_heights(y_r_max, land_r)):
        rear_pts.append((z_rear + _sag_rear(y) * scale, axis_y + y * scale))

    z_f_top_rim = max(z_f_nom_rim, front_pts[-1][0] + cf * scale) if cf > 0.0 else z_f_nom_rim
    z_r_top_rim = min(z_r_nom_rim, rear_pts[0][0] - cr * scale) if cr > 0.0 else z_r_nom_rim
    if z_r_top_rim <= z_f_top_rim + 0.05 * scale:
        z_f_top_rim = z_f_nom_rim
        z_r_top_rim = z_r_nom_rim

    poly: List[Tuple[float, float]] = []
    poly.extend(front_pts)
    poly.append((z_f_top_rim, axis_y + half_od * scale))
    poly.append((z_r_top_rim, axis_y + half_od * scale))
    poly.extend(rear_pts)
    poly.append((z_r_top_rim, axis_y - half_od * scale))
    poly.append((z_f_top_rim, axis_y - half_od * scale))

    return poly, front_pts, rear_pts, z_f_nom_rim, z_r_nom_rim


GBT14690_SCALES = (10.0, 5.0, 4.0, 2.5, 2.0, 1.0, 0.5, 0.4, 0.25, 0.2, 0.1)


def _pick_standard_scale(axial_span: float, od: float, max_w: float = 105.0, max_h: float = 82.0) -> float:
    """Largest GB/T 14690 scale at which the section fits the drawing viewport."""
    for s in GBT14690_SCALES:
        if axial_span * s <= max_w and od * s <= max_h:
            return s
    return GBT14690_SCALES[-1]


def _format_scale_ratio(scale: float) -> str:
    """'5:1', '2.5:1', '1:2' ... for a paper/real scale factor."""
    if scale >= 1.0:
        return f"{scale:g}:1"
    return f"1:{1.0 / scale:g}"


def _format_gbt_radius(r_val: float, custom_label: Optional[str] = None) -> str:
    """Format unsigned curvature radius label matching GB/T 13323 (no negative signs)."""
    if custom_label:
        return str(custom_label)
    if abs(r_val) < 1e-6 or abs(r_val) > 1e8:
        return "R\u221e"
    return f"R{abs(r_val):.3f}"


def _assign_auto_radial_configs(
    unique_surfs: List[Dict[str, Any]],
    axis_y: float,
    scale: float,
) -> None:
    """Populate eyepiece-grade parallel Z-jog radial specs for surfaces lacking manual jog_spec."""
    if not unique_surfs:
        return
    all_pts = [p for rec in unique_surfs for p in (rec.get("pts") or [])]
    if not all_pts:
        return
    x_lens_left = min(p[0] for p in all_pts)
    x_lens_right = max(p[0] for p in all_pts)

    # First pass: estimate signed curvature radius on paper and horizontal chord clearance
    enriched = []
    for idx, rec in enumerate(unique_surfs):
        cfg = rec.get("pos_cfg") or {}
        if cfg.get("jog_spec"):
            continue
        pts = rec.get("pts") or []
        if len(pts) < 3:
            continue
        p_edge = pts[-1]
        dx = p_edge[0] - float(rec["z_v"])
        dy = p_edge[1] - axis_y
        if abs(dx) < 1e-4:
            continue
        r_paper = (dx * dx + dy * dy) / (2.0 * dx)
        if abs(r_paper) > 1e6:
            continue
        # Center side: R < 0 -> center LEFT (-1); R > 0 -> center RIGHT (+1)
        center_on_left = r_paper < 0.0
        # Front convex surfaces (r_paper > 0 near front left) use eyepiece R89.540 style (out_arm left, jog right/inside)
        is_front_convex = (not center_on_left) and (float(rec["z_v"]) < 0.5 * (x_lens_left + x_lens_right)) and (idx < len(unique_surfs) - 1)
        side_key = "left" if (center_on_left or is_front_convex) else "right"
        enriched.append({
            "idx": idx,
            "rec": rec,
            "r_paper": r_paper,
            "abs_r": abs(r_paper),
            "center_on_left": center_on_left,
            "is_front_convex": is_front_convex,
            "side_key": side_key,
        })

    if not enriched:
        return

    # Assign vertical fracs per quadrant: larger |R| (flatter ray) sits further from axis so same-side rays diverge cleanly
    left_center = sorted([e for e in enriched if e["side_key"] == "left" and not e["is_front_convex"]], key=lambda e: -e["abs_r"])
    left_out = sorted([e for e in enriched if e["is_front_convex"]], key=lambda e: -e["abs_r"])
    right_center = sorted([e for e in enriched if e["side_key"] == "right"], key=lambda e: -e["abs_r"])

    # Assign upper-left slots [0.78, 0.64, ...] and lower-left slots [0.25, 0.36, ...]
    upper_slots = [0.78, 0.63, 0.52]
    lower_slots = [0.24, 0.36, 0.44]

    # Put front convex rays on lower-left by default when upper-left has center-left rays (mirrors eyepiece R89.540)
    if left_center:
        for k, e in enumerate(left_center):
            if k < 2:
                e["pts_frac"] = upper_slots[k]
            else:
                e["pts_frac"] = lower_slots[min(k - 2 + len(left_out), len(lower_slots) - 1)]
        for k, e in enumerate(left_out):
            e["pts_frac"] = lower_slots[min(k, len(lower_slots) - 1)]
    else:
        for k, e in enumerate(left_out):
            e["pts_frac"] = upper_slots[k] if k % 2 == 0 and k // 2 < len(upper_slots) else lower_slots[min(k // 2, len(lower_slots) - 1)]

    if len(right_center) >= 3:
        for k, e in enumerate(right_center):
            e["pts_frac"] = upper_slots[k] if k < 2 else lower_slots[min(k - 2, len(lower_slots) - 1)]
    elif len(right_center) == 2:
        right_center[0]["pts_frac"] = 0.77
        right_center[1]["pts_frac"] = 0.63
    elif len(right_center) == 1:
        right_center[0]["pts_frac"] = 0.76

    x_left_limit = 104.5
    x_right_limit = 281.0

    for e in enriched:
        rec = e["rec"]
        pts = rec["pts"]
        frac = float(e["pts_frac"])
        p_hit = pts[max(0, min(len(pts) - 1, int(round(frac * (len(pts) - 1)))))]
        c_x = float(rec["z_v"]) + e["r_paper"]
        vx = p_hit[0] - c_x
        vy = p_hit[1] - axis_y
        d_c = math.hypot(vx, vy)
        ux, uy = vx / max(d_c, 1e-6), vy / max(d_c, 1e-6)
        r_label = str(rec.get("label") or "")
        txt_w = _measure_dxf_text_width(r_label, 2.55, "GB_NUM") + 3.5

        if e["is_front_convex"]:
            # Arrow sits on front curve pointing inward; text on open left out_arm; compact Z-jog + '+' toward right
            avail_left = max(10.0, p_hit[0] - x_left_limit)
            out_arm = min(max(txt_w + 3.5, 16.0), max(11.0, avail_left - 2.0))
            # Check horizontal room to next surface at similar y
            next_rec = unique_surfs[e["idx"] + 1] if e["idx"] + 1 < len(unique_surfs) else None
            glass_dx = 0.0
            if next_rec and next_rec.get("pts"):
                n_pts = next_rec["pts"]
                n_pt = min(n_pts, key=lambda p: abs(p[1] - p_hit[1]))
                glass_dx = n_pt[0] - p_hit[0]
            if glass_dx >= 17.5:
                arm1, zig_back, zig_perp, arm2 = 5.2, 3.0, 2.0, 4.8
            else:
                # Cross cleanly through thin glass into open right margin
                u_hx = max(abs(-ux), 0.35)
                d_right = max(8.0, (x_lens_right + 15.0 - p_hit[0]) / u_hx)
                max_right = max(18.0, (x_right_limit - p_hit[0]) / u_hx)
                arm1 = min(d_right, max(12.0, max_right - 10.0))
                zig_back = min(5.5, 0.45 * arm1)
                zig_perp = 2.4
                arm2 = max(6.5, min(14.0, max_right - arm1 + zig_back - 2.5))
            jog = {
                "out_arm": round(out_arm, 2),
                "arm1": round(arm1, 2),
                "zig_back": round(zig_back, 2),
                "zig_perp": round(zig_perp, 2),
                "arm2": round(arm2, 2),
                "text_t": 0.54,
                "text_on_seg": "out",
                "text_height": 2.55,
                "show_center_cross": True,
            }
        elif e["center_on_left"]:
            # Center-side (-ux, -uy) points LEFT into open space [104.5, x_lens_left]
            u_hx = max(abs(-ux), 0.35)
            d_clear = max(1.5, (p_hit[0] - (x_lens_left - 2.5)) / u_hx)
            max_reach = max(18.0, (p_hit[0] - x_left_limit) / u_hx)
            arm1 = min(d_clear + max(txt_w, 15.0), max(d_clear + 11.0, max_reach - 11.0))
            zig_back = min(8.5, max(4.2, 0.52 * min(arm1, 16.5)))
            arm2 = max(6.5, min(17.0, max_reach - arm1 + zig_back - 2.5))
            t_mid = (d_clear + arm1) * 0.5 / max(arm1, 1e-4)
            jog = {
                "out_arm": 0.0,
                "arm1": round(arm1, 2),
                "zig_back": round(zig_back, 2),
                "zig_perp": 2.7,
                "arm2": round(arm2, 2),
                "text_t": round(max(0.32, min(0.82, t_mid)), 3),
                "text_on_seg": "arm1",
                "text_height": 2.55,
                "show_center_cross": True,
            }
        else:
            # Center-side (-ux, -uy) points RIGHT past lens & vertical OD dim line into open space
            u_hx = max(abs(-ux), 0.35)
            d_clear = max(2.5, (x_lens_right + 14.5 - p_hit[0]) / u_hx)
            max_reach = max(22.0, (x_right_limit - p_hit[0]) / u_hx)
            arm1 = min(d_clear + max(txt_w, 15.0), max(d_clear + 11.0, max_reach - 11.0))
            zig_back = min(8.5, max(4.2, 0.52 * min(arm1, 16.5)))
            arm2 = max(6.5, min(17.0, max_reach - arm1 + zig_back - 2.5))
            t_mid = (d_clear + arm1) * 0.5 / max(arm1, 1e-4)
            jog = {
                "out_arm": 0.0,
                "arm1": round(arm1, 2),
                "zig_back": round(zig_back, 2),
                "zig_perp": 2.7,
                "arm2": round(arm2, 2),
                "text_t": round(max(0.32, min(0.82, t_mid)), 3),
                "text_on_seg": "arm1",
                "text_height": 2.55,
                "show_center_cross": True,
            }

        new_cfg = dict(rec.get("pos_cfg") or {})
        new_cfg["pts_frac"] = frac
        new_cfg["jog_spec"] = jog
        rec["pos_cfg"] = new_cfg


def _export_gbt13323_element_dxf(drawing_data: Dict[str, Any], filepath: str):
    """
    Export single optical element, cemented doublet/triplet, or multi-doublet component
    drawing in the exact Chinese GB/T 13323-2009 layout absorbed from the project template.
    """
    doc, msp = _setup_gbt13323_dxf_document()

    elements = drawing_data.get("elements", [])
    if not elements:
        elements = [{"index": 1, "r1": 50.0, "r2": -50.0, "t": 5.0, "k1": 0, "k2": 0, "material": "H-K9L"}]

    od = float(drawing_data.get("od", 25.4))
    od_tol = drawing_data.get("od_tolerance", "-0.05")
    half_od = od / 2.0
    total_axial_span = sum(float(e.get("t", 5.0)) + float(e.get("air_after", 0.0)) for e in elements)

    # Fit lens graphic generously into right/upper viewport (X: ~115..258, Y: ~64..195)
    axis_y = float(drawing_data.get("axis_y", 132.0))
    scale_override = drawing_data.get("scale_override")
    if scale_override is not None:
        scale = float(scale_override)
    elif str(drawing_data.get("scale_policy", "standard")).lower() == "fit":
        scale = min(105.0 / max(total_axial_span, 8.0), 82.0 / max(od, 8.0))
        scale = max(1.0, min(6.0, round(scale * 4.0) / 4.0))
    else:
        scale = _pick_standard_scale(total_axial_span, od)
    # The sheet states the scale that was really used (callers may blank it with title_scale_str="").
    drawing_data["drawing_scale"] = _format_scale_ratio(scale)
    if "title_scale_str" not in drawing_data:
        drawing_data["title_scale_str"] = drawing_data["drawing_scale"]

    # Draw Top-Left 17-row Optical Requirements Table
    _draw_dxf_optical_table(msp, drawing_data, x_start=25.0, y_top=205.0)

    # Draw Bottom-Right GB/T 10609.1 Title Block ('单位' strictly blank)
    _draw_dxf_title_block(msp, drawing_data, is_assembly=False)

    # Draw Bottom-Left Numbered Chinese Technical Notes
    notes_list = drawing_data.get("custom_notes") or drawing_data.get("technical_notes")
    if not notes_list:
        mats_unique = []
        for el in elements:
            m_name = str(el.get("material", "H-K9L")).strip()
            if m_name and m_name not in mats_unique:
                mats_unique.append(m_name)
        mat_desc = "、".join(mats_unique) if mats_unique else "光学玻璃"
        scratch_dig = str(drawing_data.get("scratch_dig", "") or "").strip()
        coat_raw = str(drawing_data.get("coating", "增透膜") or "增透膜").strip()
        spec_wave = str(drawing_data.get("design_wavelengths", "532nm") or "532nm").strip()

        notes_list = [f"1、透镜采用{mat_desc}光学玻璃；"]
        has_cement = any(
            idx < len(elements) - 1 and float(elements[idx].get("air_after", 0.0)) <= 1e-4
            for idx in range(len(elements))
        )
        if has_cement:
            notes_list.append("2、胶合采用环氧树脂或者光敏胶；")
        else:
            notes_list.append("2、所有倒角均为0.3×45°，无崩边裂纹；")

        coat_line = f"3、镀膜：{coat_raw}"
        if scratch_dig:
            coat_line += f"，表面光洁度 {scratch_dig}；"
        else:
            coat_line += "；"
        notes_list.append(coat_line)
        notes_list.append(f"4、焦距及后截距对应波长：{spec_wave}")

    _draw_dxf_technical_notes(msp, notes_list, x=28.5, y_top=95.0)

    # Place optical graphic horizontally centered inside open right area (X: 112..255)
    center_x = float(drawing_data.get("lens_center_x", 182.0))
    x_origin = center_x - 0.5 * total_axial_span * scale
    axis_x0 = max(100.0, x_origin - 28.0)
    axis_x1 = min(286.0, x_origin + total_axial_span * scale + 38.0)
    msp.add_line((axis_x0, axis_y), (axis_x1, axis_y), dxfattribs={"layer": "3_AXIS", "color": 1})

    # Pre-build slices and cemented groups
    slices: List[Dict[str, Any]] = []
    cur_z = x_origin
    n_elems = len(elements)
    for idx, elem in enumerate(elements):
        r1 = float(elem.get("r1", 0.0))
        r2 = float(elem.get("r2", 0.0))
        r1_draw = float(elem.get("r1_draw", r1))
        r2_draw = float(elem.get("r2_draw", r2))
        k1 = float(elem.get("k1", 0.0))
        k2 = float(elem.get("k2", 0.0))
        ct = float(elem.get("t", 5.0))
        air_after = float(elem.get("air_after", 0.0))
        elem_od = float(elem.get("od", od))
        elem_half_od = elem_od / 2.0

        prev_air = (idx == 0) or (float(elements[idx - 1].get("air_after", 0.0)) > 1e-4)
        next_air = (idx == n_elems - 1) or (air_after > 1e-4)

        z_front = cur_z
        z_rear = z_front + ct * scale
        cf_val = float(elem["chamfer_front"]) if "chamfer_front" in elem else (0.3 if prev_air else 0.0)
        cr_val = float(elem["chamfer_rear"]) if "chamfer_rear" in elem else (0.3 if next_air else 0.0)
        poly, f_pts, r_pts, z_f_nom, z_r_nom = _build_lens_slice_polygon(
            z_front=z_front,
            z_rear=z_rear,
            r1_draw=r1_draw,
            r2_draw=r2_draw,
            k1=k1,
            k2=k2,
            half_od=elem_half_od,
            scale=scale,
            axis_y=axis_y,
            chamfer_front=cf_val,
            chamfer_rear=cr_val,
            polish_half_front=(float(elem["polish_dia_front"]) / 2.0 if elem.get("polish_dia_front") else None),
            polish_half_rear=(float(elem["polish_dia_rear"]) / 2.0 if elem.get("polish_dia_rear") else None),
        )

        # Element number position near upper flat rim clear of hatches and radial rays
        num_y_frac = float(elem.get("num_y_frac", 0.78))
        x_mid_upper = 0.5 * (z_front + _calculate_sag(r1_draw, k1, num_y_frac * elem_half_od) * scale
                             + z_rear + _calculate_sag(r2_draw, k2, num_y_frac * elem_half_od) * scale)
        y_num = axis_y + num_y_frac * elem_half_od * scale

        msp.add_lwpolyline(poly, close=True, dxfattribs={"layer": "2_CONTOUR"})
        hatch_ang = float(elem.get("hatch_angle", 45.0 if idx % 2 == 0 else 135.0))
        _draw_dxf_glass_hatch(
            msp,
            poly,
            angle_deg=hatch_ang,
            y_axis_paper=axis_y,
            keepout_pt=(x_mid_upper, y_num) if n_elems > 1 else None,
        )

        if n_elems > 1:
            msp.add_text(
                str(elem.get("index", idx + 1)),
                dxfattribs={"height": 2.9, "style": "GB_NUM", "layer": "6_TEXT"},
            ).set_placement((x_mid_upper, y_num), align=TextEntityAlignment.MIDDLE_CENTER)

        slices.append({
            "idx": idx,
            "elem": elem,
            "z_front": z_front,
            "z_rear": z_rear,
            "z_f_nom": z_f_nom,
            "z_r_nom": z_r_nom,
            "front_pts": f_pts,
            "rear_pts": r_pts,
            "r1": r1,
            "r2": r2,
            "r1_draw": r1_draw,
            "r2_draw": r2_draw,
            "ct": ct,
            "air_after": air_after,
            "half_od": elem_half_od,
            "prev_air": prev_air,
            "next_air": next_air,
            "chamfer_front": cf_val,
            "chamfer_rear": cr_val,
        })
        cur_z = z_rear + air_after * scale

    y_rim_top = axis_y + half_od * scale
    y_rim_bot = axis_y - half_od * scale

    # Air-surface polished marks (circle-cross) + rim roughness + top (ET) dimensions
    groups: List[List[Dict[str, Any]]] = []
    cur_grp: List[Dict[str, Any]] = []
    for s in slices:
        cur_grp.append(s)
        if s["next_air"]:
            groups.append(cur_grp)
            cur_grp = []

    for g_idx, grp in enumerate(groups):
        g_front = grp[0]
        g_rear = grp[-1]

        # '⊕' marks hugging outer upper air-glass edges cleanly
        f_pts_list = g_front["front_pts"]
        idx_f = int(len(f_pts_list) * float(g_front["elem"].get("mark_front_frac", 0.90)))
        p_f = f_pts_list[max(1, min(len(f_pts_list) - 2, idx_f))]
        if g_front["elem"].get("mark_front", True):
            _draw_dxf_optical_surface_mark(msp, (p_f[0] - 2.1, p_f[1] - 0.4), radius=1.22, layer="6_TEXT")

        r_pts_list = g_rear["rear_pts"]
        idx_r = int(len(r_pts_list) * float(g_rear["elem"].get("mark_rear_frac", 0.10)))
        p_r = r_pts_list[max(1, min(len(r_pts_list) - 2, idx_r))]
        if g_rear["elem"].get("mark_rear", True):
            _draw_dxf_optical_surface_mark(msp, (p_r[0] + 2.1, p_r[1] - 0.4), radius=1.22, layer="6_TEXT")

        # Rim roughness '▽ 1.6' seated centered on the widest flat top rim in the group
        rim_best = max(grp, key=lambda x: abs(x["z_r_nom"] - x["z_f_nom"]))
        z_rim_mid = 0.5 * (rim_best["z_f_nom"] + rim_best["z_r_nom"])
        _draw_dxf_roughness_symbol(
            msp, tip=(z_rim_mid, y_rim_top), value_text="1.6", scale=1.12, layer="6_TEXT", text_layer="6_TEXT"
        )

        # Reference edge thickness dimensions (ET) above each element in the group
        if len(grp) == 1:
            s = grp[0]
            et_cfg = s["elem"].get("et_dim_cfg") or {}
            et_val = s["elem"].get("display_et")
            if et_val is None:
                et_val = (s["z_r_nom"] - s["z_f_nom"]) / scale
            shoulder = et_cfg.get("shoulder_side", "left" if (g_idx == 0 and len(groups) > 1) else "right")
            if abs(s["z_r_nom"] - s["z_f_nom"]) >= 12.0 and "shoulder_side" not in et_cfg:
                shoulder = None
            _draw_dxf_linear_dim_gbt(
                msp,
                (s["z_f_nom"], y_rim_top),
                (s["z_r_nom"], y_rim_top),
                y_dim=y_rim_top + 7.2,
                text=f"({float(et_val):.3f})",
                shoulder_side=shoulder,
                shoulder_len=float(et_cfg.get("shoulder_len", 12.0)),
                internal_arrows=et_cfg.get("internal_arrows"),
                draw_span_line=bool(et_cfg.get("draw_span_line", True)),
            )
        else:
            # Multi-element cemented group: stagger ET reference dimensions outside group rims
            out_side = "left" if (g_idx == 0 and len(groups) > 1) else "right"
            for e_pos, s in enumerate(grp):
                et_cfg = s["elem"].get("et_dim_cfg") or {}
                et_val = s["elem"].get("display_et")
                if et_val is None:
                    et_val = (s["z_r_nom"] - s["z_f_nom"]) / scale
                default_tier = 1 if (e_pos == 0 if out_side == "left" else e_pos == len(grp) - 1) else 2
                tier = int(et_cfg.get("tier", default_tier))
                y_tier = y_rim_top + (7.2 if tier == 1 else 13.0)
                sh_side = et_cfg.get("shoulder_side", out_side)
                anchor_x = g_front["z_f_nom"] if sh_side == "left" else g_rear["z_r_nom"]
                _draw_dxf_linear_dim_gbt(
                    msp,
                    (s["z_f_nom"], y_rim_top),
                    (s["z_r_nom"], y_rim_top),
                    y_dim=y_tier,
                    text=f"({float(et_val):.3f})",
                    shoulder_side=sh_side,
                    shoulder_len=float(et_cfg.get("shoulder_len", 16.5)),
                    shoulder_anchor_x=anchor_x,
                    internal_arrows=et_cfg.get("internal_arrows"),
                    draw_span_line=bool(et_cfg.get("draw_span_line", True)),
                )

        # Protective chamfer callout placed cleanly above the staggered (ET) tiers
        chamfer_spec = next(
            (el_s["elem"].get("chamfer_callout") for el_s in grp if el_s["elem"].get("chamfer_callout")),
            None,
        )
        if chamfer_spec:
            tip_on = str(chamfer_spec.get("tip_on", "front"))
            tip_x = g_rear["z_r_nom"] if tip_on == "rear" else g_front["z_f_nom"]
            c_ang = float(chamfer_spec.get("angle", 52.0 if tip_on == "rear" else 115.0))
            _draw_dxf_chamfer_callout(
                msp,
                tip=(tip_x, y_rim_top),
                angle_deg=c_ang,
                stem_len=float(chamfer_spec.get("stem", 19.0)),
                shoulder_len=float(chamfer_spec.get("shoulder", 18.0)),
                prefix=str(chamfer_spec.get("prefix", "0.3")),
                tol_upper=str(chamfer_spec.get("tol_upper", "+0.2")),
                tol_lower=str(chamfer_spec.get("tol_lower", "0")),
                suffix=str(chamfer_spec.get("suffix", "\u00d745\u00b0")),
                show_arrow=bool(chamfer_spec.get("show_arrow", False)),
            )
        elif g_idx == len(groups) - 1:
            # count the outer rims with a chamfer: a cemented face (chamfer 0) has none
            rims = [(g["z_f_nom"], g_["chamfer_front"]) for g, g_ in ((gr[0], gr[0]) for gr in groups)]
            rims += [(g["z_r_nom"], g["chamfer_rear"]) for g in (gr[-1] for gr in groups)]
            sizes = [c for _, c in rims if c > 1e-9]
            if not sizes:
                continue
            bevel_count = len(sizes)
            bevel_txt = f"{max(sizes):g}"
            rear_has = g_rear["chamfer_rear"] > 1e-9
            _draw_dxf_chamfer_callout(
                msp,
                tip=((g_rear["z_r_nom"] if rear_has else g_front["z_f_nom"]), y_rim_top),
                angle_deg=82.0 if rear_has else 112.0,
                stem_len=21.0,
                shoulder_len=16.5,
                prefix=f"{bevel_count}--{bevel_txt}" if bevel_count > 1 else bevel_txt,
                tol_upper="+0.2",
                tol_lower="0",
                suffix="\u00d745\u00b0",
                show_arrow=False,
            )

    # Bottom Center Thickness & Air-Gap Dimensions (staggered rows from axis vertices, ZERO 'mm')
    dim_specs: List[Tuple[float, float, str, int, Dict[str, Any]]] = []
    for s in slices:
        ct = s["ct"]
        ct_tol = s["elem"].get("t_tolerance", "±0.02")
        ct_fmt = f"{ct:.2f}" if (s["elem"].get("t_decimals", 2) == 2 and ct % 1 != 0) else f"{ct:g}"
        dim_specs.append((
            s["z_front"],
            s["z_rear"],
            f"{ct_fmt}{ct_tol}",
            s["idx"],
            dict(s["elem"].get("ct_dim_cfg") or {}),
        ))
        if s["air_after"] > 1e-4 and s["idx"] < len(slices) - 1:
            air = s["air_after"]
            air_tol = s["elem"].get("air_tolerance", "±0.02")
            air_fmt = f"{air:g}"
            dim_specs.append((
                s["z_rear"],
                s["z_rear"] + air * scale,
                f"{air_fmt}{air_tol}",
                99,
                dict(s["elem"].get("air_dim_cfg") or {}),
            ))

    row_offsets = [7.5, 13.5, 19.5]
    for d_i, (za, zb, d_txt, s_idx, cfg_d) in enumerate(dim_specs):
        if s_idx == 99:
            lvl = int(cfg_d.get("tier", 2))
            y_d = y_rim_bot - row_offsets[max(0, min(2, lvl))]
            sh = cfg_d.get("shoulder_side", "right")
            sh_l = float(cfg_d.get("shoulder_len", 12.0))
        else:
            lvl = int(cfg_d.get("tier", d_i % 3))
            y_d = y_rim_bot - row_offsets[max(0, min(2, lvl))]
            default_sh = "left" if s_idx < 2 else "right"
            if "shoulder_side" in cfg_d:
                sh = cfg_d["shoulder_side"]
            else:
                sh = default_sh if abs(zb - za) < 18.0 else None
            sh_l = float(cfg_d.get("shoulder_len", 13.0))
        sh_anchor = slices[0]["z_front"] if cfg_d.get("anchor_first") else None
        _draw_dxf_linear_dim_gbt(
            msp,
            (za, axis_y),
            (zb, axis_y),
            y_dim=y_d,
            text=d_txt,
            shoulder_side=sh,
            shoulder_len=sh_l,
            shoulder_anchor_x=sh_anchor,
            internal_arrows=cfg_d.get("internal_arrows"),
        )

    # Right Vertical Outer Diameter Dimension (without 'mm')
    max_z_nom = max(s["z_r_nom"] for s in slices)
    max_z_all = max(pt[0] for s in slices for pt in s["rear_pts"])
    min_z_all = min(pt[0] for s in slices for pt in s["front_pts"])
    x_od_dim = max(max_z_nom, max_z_all) + 12.0

    # Polished diameter of a surface that ends in a flat land (dimensioned between the land corners)
    for s in slices:
        for side, key, z_v, r_d, k_d in (("front", "polish_dia_front", s["z_front"], s["r1_draw"], float(s["elem"].get("k1", 0.0))),
                                         ("rear", "polish_dia_rear", s["z_rear"], s["r2_draw"], float(s["elem"].get("k2", 0.0)))):
            p_dia = s["elem"].get(key)
            if not p_dia or float(p_dia) >= 2.0 * s["half_od"] - 1e-6:
                continue
            p_half = float(p_dia) / 2.0
            z_land = z_v + _calculate_sag(r_d, k_d, p_half) * scale
            if side == "rear":
                x_p = z_land + 5.5
                x_od_dim = max(x_od_dim, x_p + 9.5)
            else:
                x_p = z_land - 5.5
            _draw_dxf_vertical_od_dim(
                msp,
                x_top=z_land,
                x_bot=z_land,
                y_bot=axis_y - p_half * scale,
                y_top=axis_y + p_half * scale,
                x_dim=x_p,
                od_main="\u03a6" + f"{float(p_dia):g}",
                od_tol=str(s["elem"].get(key + "_tolerance", "")),
            )

    od_main_str = f"{drawing_data.get('od_prefix', '')}{od:g}"
    _draw_dxf_vertical_od_dim(
        msp,
        x_top=slices[-1]["z_r_nom"],
        x_bot=slices[-1]["z_r_nom"],
        y_bot=y_rim_bot,
        y_top=y_rim_top,
        x_dim=x_od_dim,
        od_main=od_main_str,
        od_tol=od_tol,
    )

    # Radial Curvature Leaders (all unsigned R values, '+' center marks along radial ray)
    surf_records: List[Dict[str, Any]] = []
    for s in slices:
        surf_records.append({
            "z_v": s["z_front"],
            "r_opt": s["r1"],
            "r_draw": s["r1_draw"],
            "pts": s["front_pts"],
            "label": _format_gbt_radius(s["r1"], s["elem"].get("r1_label")),
            "pos_cfg": s["elem"].get("r1_dim_cfg"),
        })
        if s["next_air"] or len(slices) > 1:
            surf_records.append({
                "z_v": s["z_rear"],
                "r_opt": s["r2"],
                "r_draw": s["r2_draw"],
                "pts": list(reversed(s["rear_pts"])),
                "label": _format_gbt_radius(s["r2"], s["elem"].get("r2_label")),
                "pos_cfg": s["elem"].get("r2_dim_cfg"),
            })

    # Deduplicate cemented interfaces while preserving every surface once
    unique_surfs: List[Dict[str, Any]] = []
    for rec in surf_records:
        if not unique_surfs or abs(rec["z_v"] - unique_surfs[-1]["z_v"]) > 1e-3:
            unique_surfs.append(rec)

    _assign_auto_radial_configs(unique_surfs, axis_y, scale)
    default_fracs = [0.76, 0.64, 0.24, 0.72, 0.34, 0.70, 0.28]
    for sr_idx, rec in enumerate(unique_surfs):
        cfg = rec["pos_cfg"] or {}
        pts = rec["pts"]
        frac = float(cfg.get("pts_frac", default_fracs[sr_idx % len(default_fracs)]))
        idx_hit = max(1, min(len(pts) - 2, int(round(frac * (len(pts) - 1)))))
        p_hit = pts[idx_hit]

        r_d = rec["r_draw"]
        if abs(r_d) < 1e-6 or abs(r_d) > 1e8:
            # Plano surface note
            ang = 140.0 if sr_idx == 0 else 40.0
            _draw_dxf_leader(msp, p_hit, ang, 10.0, 9.0, "R\u221e", None)
            continue

        c_paper = (rec["z_v"] + r_d * scale, axis_y)
        vx = p_hit[0] - c_paper[0]
        vy = p_hit[1] - c_paper[1]
        d_c = math.hypot(vx, vy)
        ux, uy = vx / max(d_c, 1e-6), vy / max(d_c, 1e-6)

        if cfg.get("jog_spec"):
            _draw_dxf_radial_dim_gbt(
                msp,
                p_hit,
                c_paper,
                rec["label"],
                show_center_cross=bool(cfg.get("show_center_cross", True)),
                jog_spec=cfg["jog_spec"],
            )
            continue

        # Direct radial ray outward into open whitespace cleanly without back-crossing glass
        dir_side = cfg.get("dir_side")
        if dir_side is None:
            on_left = sr_idx < len(unique_surfs) / 2.0
            dir_side = "left" if on_left else "right"

        want_left = (dir_side == "left")
        plus_u_goes_to_preferred = (want_left and ux < 0) or ((not want_left) and ux > 0)
        if plus_u_goes_to_preferred:
            if "ext_past_surf" in cfg:
                out_tail = float(cfg["ext_past_surf"])
                txt_t = float(cfg.get("text_offset_t", 0.56))
            elif (not want_left) and ux > 0.2:
                dx_past = max(24.0, x_od_dim - p_hit[0] + 18.0)
                out_tail = max(28.0, dx_past / ux)
                txt_t = float(cfg.get("text_offset_t", max(0.56, min(0.80, (x_od_dim - p_hit[0] + 10.5) / (out_tail * ux)))))
            elif want_left and (-ux) > 0.2:
                out_tail = max(28.0, (p_hit[0] - min_z_all + 22.0) / (-ux))
                txt_t = float(cfg.get("text_offset_t", 0.58))
            else:
                out_tail = 28.0
                txt_t = float(cfg.get("text_offset_t", 0.56))
            _draw_dxf_radial_dim_gbt(
                msp,
                p_hit,
                c_paper,
                rec["label"],
                ext_past_surf=out_tail,
                center_dist_cap=float(cfg.get("center_dist_cap", 0.0)),
                show_center_cross=bool(cfg.get("show_center_cross", False)),
                show_outer_cross=bool(cfg.get("show_outer_cross", False)),
                arrow_from_center_side=False,
                text_offset_t=txt_t,
            )
            continue

        if "center_dist_cap" in cfg:
            cap_dist = float(cfg["center_dist_cap"])
            txt_t = float(cfg.get("text_offset_t", 0.50))
        elif want_left and ux > 0.2:
            cap_dist = max(26.0, (p_hit[0] - min_z_all + 22.0) / ux)
            txt_t = float(cfg.get("text_offset_t", 0.28))
        elif (not want_left) and (-ux) > 0.2:
            cap_dist = max(26.0, (x_od_dim - p_hit[0] + 20.0) / (-ux))
            txt_t = float(cfg.get("text_offset_t", 0.26))
        else:
            cap_dist = 26.0
            txt_t = float(cfg.get("text_offset_t", 0.50))

        _draw_dxf_radial_dim_gbt(
            msp,
            p_hit,
            c_paper,
            rec["label"],
            ext_past_surf=float(cfg.get("ext_past_surf", 0.0)),
            center_dist_cap=cap_dist,
            show_center_cross=bool(cfg.get("show_center_cross", True)),
            show_outer_cross=bool(cfg.get("show_outer_cross", False)),
            arrow_from_center_side=bool(cfg.get("arrow_from_center_side", True)),
            text_offset_t=txt_t,
        )

    doc.saveas(filepath)


def _export_gbt13323_assembly_dxf(drawing_data: Dict[str, Any], filepath: str):
    """
    Export optical system assembly drawing in the primary Chinese GB/T 13323 template
    ('单位' blank, A4 landscape with filing margin, 180mm GB/T 10609.1 title block,
    unsigned dimensions, and sparse optical glass cross-section hatches).
    """
    doc, msp = _setup_gbt13323_dxf_document()

    assembly_elements = drawing_data.get("assembly_elements", [])
    if not assembly_elements:
        doc.saveas(filepath)
        return

    max_od = max(float(e.get("od", 20.0)) for e in assembly_elements)
    total_track = sum(float(e.get("ct", 5.0)) + float(e.get("air_after", 0.0)) for e in assembly_elements)

    axis_y = 132.0
    if drawing_data.get("scale_override") is not None:
        scale = float(drawing_data["scale_override"])
    elif str(drawing_data.get("scale_policy", "standard")).lower() == "fit":
        scale = min(145.0 / max(total_track, 20.0), 82.0 / max(max_od, 10.0))
        scale = max(1.0, min(4.0, round(scale * 4.0) / 4.0))
    else:
        scale = _pick_standard_scale(total_track, max_od, max_w=145.0, max_h=82.0)
    drawing_data["drawing_scale"] = _format_scale_ratio(scale)
    if "title_scale_str" not in drawing_data:
        drawing_data["title_scale_str"] = drawing_data["drawing_scale"]

    # Draw Top-Left Chinese System Parameters + BOM Table
    _draw_dxf_assembly_table(msp, drawing_data, x_start=25.0, y_top=205.0)

    # Draw Bottom-Right GB/T 10609.1 Title Block ('单位' blank)
    _draw_dxf_title_block(msp, drawing_data, is_assembly=True)

    # Draw Bottom-Left Chinese Notes positioned safely above title block (Y >= 58)
    spec_fallback = str(drawing_data.get("design_wavelengths") or "工作波段")
    notes_list = drawing_data.get("custom_notes") or drawing_data.get("technical_notes") or [
        f"1、工作波段：{spec_fallback}；",
        f"2、有效焦距 f'={drawing_data.get('efl_str', '-')}mm，后截距 BFL={drawing_data.get('bfl_str', '-')}mm；",
        f"3、总轨长 TOTR={drawing_data.get('totr_str', '-')}mm，镜组叠层 {drawing_data.get('stack_str', '-')}mm；",
        f"4、各镜片光轴同轴度 ≤ {drawing_data.get('coax_tol', '0.005')}，隔圈端面平行度 ≤ {drawing_data.get('para_tol', '0.003')}；",
        "5、镜筒内壁消光发黑处理，工作环境温度：20℃±2℃。",
    ]
    n_wrapped = sum(
        len(_wrap_cad_note_line(seg, max_units=28.5))
        for note in notes_list
        for seg in note.split("\n")
    )
    h_notes = n_wrapped * 4.4 + len(notes_list) * 1.2
    y_notes_top = max(92.0, min(116.0, 61.0 + h_notes))
    _draw_dxf_technical_notes(msp, notes_list, x=28.5, y_top=y_notes_top)

    # Place centered assembly layout inside X: 105..282, Y: 72..195
    center_x = 188.0
    x_origin = center_x - 0.5 * total_track * scale
    axis_x0 = max(100.0, x_origin - 15.0)
    axis_x1 = min(286.0, x_origin + total_track * scale + 20.0)
    msp.add_line((axis_x0, axis_y), (axis_x1, axis_y), dxfattribs={"layer": "3_AXIS", "color": 1})

    cur_z = x_origin
    comp_num = 1
    for elem_idx, elem in enumerate(assembly_elements):
        comps = elem.get("components", [])
        elem_od = float(elem.get("od", max_od))
        half_od = elem_od / 2.0
        air_after = float(elem.get("air_after", 0.0))

        comp_cur_z = cur_z
        elem_start_z = cur_z
        for c_idx, comp in enumerate(comps):
            r1 = float(comp.get("r1", 0.0))
            r2 = float(comp.get("r2", 0.0))
            k1 = float(comp.get("k1", 0.0))
            k2 = float(comp.get("k2", 0.0))
            ct = float(comp.get("ct", 5.0))

            z_front = comp_cur_z
            z_rear = z_front + ct * scale
            poly, _, _, _, _ = _build_lens_slice_polygon(
                z_front=z_front,
                z_rear=z_rear,
                r1_draw=r1,
                r2_draw=r2,
                k1=k1,
                k2=k2,
                half_od=half_od,
                scale=scale,
                axis_y=axis_y,
                chamfer_front=0.25 if c_idx == 0 else 0.0,
                chamfer_rear=0.25 if c_idx == len(comps) - 1 else 0.0,
                n_pts=32,
                polish_half_front=(float(comp["polish_dia_front"]) / 2.0 if comp.get("polish_dia_front") else None),
                polish_half_rear=(float(comp["polish_dia_rear"]) / 2.0 if comp.get("polish_dia_rear") else None),
            )

            x_lbl = 0.5 * (z_front + z_rear)
            y_lbl = axis_y + 0.55 * half_od * scale
            msp.add_lwpolyline(poly, close=True, dxfattribs={"layer": "2_CONTOUR"})
            _draw_dxf_glass_hatch(
                msp,
                poly,
                angle_deg=45.0 if comp_num % 2 == 1 else 135.0,
                y_axis_paper=axis_y,
                keepout_pt=(x_lbl, y_lbl),
            )
            msp.add_text(
                str(comp_num),
                dxfattribs={"height": 2.6, "style": "GB_NUM", "layer": "6_TEXT"},
            ).set_placement((x_lbl, y_lbl), align=TextEntityAlignment.MIDDLE_CENTER)

            comp_num += 1
            comp_cur_z = z_rear

        elem_end_z = comp_cur_z
        balloon_y = axis_y + max_od * scale * 0.5 + 10.0 + (elem_idx % 2) * 7.5
        _draw_dxf_balloon(
            msp,
            (0.5 * (elem_start_z + elem_end_z), balloon_y),
            elem.get("elem_index", elem_idx + 1),
            (0.5 * (elem_start_z + elem_end_z), axis_y + half_od * scale),
        )

        if air_after > 0.05 and elem_idx < len(assembly_elements) - 1:
            next_z = elem_end_z + air_after * scale
            gap_y = axis_y - max_od * scale * 0.5 - (8.0 + (elem_idx % 2) * 6.5)
            gap_tol = str(drawing_data.get("air_gap_tol", "\u00b10.02") or "")
            gap_txt = f"{air_after:.2f}{gap_tol}"
            txt_need_w = _measure_dxf_text_width(gap_txt, 2.55, "GB_NUM") + 5.5
            _draw_dxf_linear_dim_gbt(
                msp,
                (elem_end_z, axis_y),
                (next_z, axis_y),
                y_dim=gap_y,
                text=gap_txt,
                shoulder_side="right" if air_after * scale < txt_need_w else None,
            )

        cur_z = elem_end_z + air_after * scale

    tot_z0 = x_origin
    tot_z1 = cur_z
    tot_y = axis_y - max_od * scale * 0.5 - 22.0
    _draw_dxf_linear_dim_gbt(
        msp,
        (tot_z0, axis_y),
        (tot_z1, axis_y),
        y_dim=tot_y,
        text=f"{total_track:.2f}",
    )

    # Vertical outer-diameter dimension(s) matching eyepiece standard (reusing unified helper)
    if assembly_elements:
        od_tol = str(drawing_data.get("od_tolerance", "-0.05") or "")
        last_el = assembly_elements[-1]
        last_od = float(last_el.get("od", max_od))
        last_comp = last_el["components"][-1]
        od_pre = str(drawing_data.get("od_prefix", ""))
        last_sag = _rim_sag(float(last_comp["r2"]), float(last_comp.get("k2", 0.0)), 0.5 * last_od, last_comp.get("polish_dia_rear"))
        z_r_rim = tot_z1 + last_sag * scale
        x_od_r = min(281.0, max(tot_z1, z_r_rim) + 12.0)
        _draw_dxf_vertical_od_dim(
            msp, z_r_rim, z_r_rim,
            axis_y - 0.5 * last_od * scale, axis_y + 0.5 * last_od * scale,
            x_od_r, f"{od_pre}{last_od:g}", od_tol,
        )

        first_el = assembly_elements[0]
        first_od = float(first_el.get("od", max_od))
        if abs(first_od - last_od) > 0.2:
            first_comp = first_el["components"][0]
            first_sag = _rim_sag(float(first_comp["r1"]), float(first_comp.get("k1", 0.0)), 0.5 * first_od, first_comp.get("polish_dia_front"))
            z_f_rim = x_origin + first_sag * scale
            x_od_l = max(104.5, min(x_origin, z_f_rim) - 12.0)
            _draw_dxf_vertical_od_dim(
                msp, z_f_rim, z_f_rim,
                axis_y - 0.5 * first_od * scale, axis_y + 0.5 * first_od * scale,
                x_od_l, f"{od_pre}{first_od:g}", od_tol,
            )

    doc.saveas(filepath)



def _element_outer_diameter(components: List[Dict[str, Any]], od_source: str = "standard") -> Tuple[float, float]:
    """(lens outer diameter, largest clear diameter) of an element.

    'standard': legacy rule, clear aperture + 2 mm rounded up to a stock diameter.
    'model'   : the mechanical semi-diameters of the lens file are the lens edge."""
    max_ca = max(max(2 * c["semi_diameter_front"], 2 * c["semi_diameter_rear"]) for c in components)
    if str(od_source).lower() == "model":
        mech = max(
            max(2 * c.get("mech_semi_diameter_front", c["semi_diameter_front"]),
                2 * c.get("mech_semi_diameter_rear", c["semi_diameter_rear"]))
            for c in components
        )
        return round(mech, 4), max_ca
    raw_od = max_ca + 2.0
    for std in (10.0, 12.0, 12.7, 15.0, 16.0, 20.0, 25.0, 25.4, 30.0, 38.1, 50.0, 50.8):
        if std >= raw_od:
            return std, max_ca
    return float(math.ceil(raw_od)), max_ca


def _flat_land_polish_diameters(comp: Dict[str, Any]) -> Dict[str, float]:
    """Polished diameters of concave faces whose clear semi-diameter is smaller than the
    lens edge: the sphere stops there and a flat land runs out to the rim."""
    out = {}
    for side, r_key, concave in (("front", "radius_front", lambda r: r < -1e-9), ("rear", "radius_rear", lambda r: r > 1e-9)):
        clear = float(comp[f"semi_diameter_{side}"])
        mech = float(comp.get(f"mech_semi_diameter_{side}", clear))
        if concave(float(comp[r_key])) and clear < mech - 0.05:
            out[f"polish_dia_{side}"] = round(2.0 * clear, 4)
    return out


def _rim_sag(radius: float, conic: float, half_od: float, polish_dia: Optional[float]) -> float:
    """Sag of a face at the lens rim; a flat land keeps the sag of its polished diameter."""
    y = min(half_od, float(polish_dia) / 2.0) if polish_dia else half_od
    return _calculate_sag(radius, conic, y)


def _resolve_drawing_overrides(overrides: Optional[Union[str, Dict[str, Any]]]) -> Dict[str, Any]:
    """Overrides come as a dict or as the path of a UTF-8 JSON file."""
    if overrides is None:
        return {}
    if isinstance(overrides, str):
        with open(overrides, "r", encoding="utf-8") as fh:
            overrides = json.load(fh)
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be a dict or the path of a JSON file holding one.")
    return overrides


def _apply_drawing_overrides(dwg: Dict[str, Any], overrides: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Merge 'common' and then the entry of this drawing into its data.

    overrides = {"common": {...}, "elements": {"1": {...}, "3a": {...}}, "assembly": {...}}
    A list under "elements" inside an entry updates the lens slices one by one; every
    other key replaces the generated value (tolerances, notes, names, layout hints ...)."""
    entry = overrides.get("assembly") if key == "assembly" else (overrides.get("elements") or {}).get(str(key))
    for src in (overrides.get("common"), entry):
        if not src:
            continue
        for k, v in src.items():
            if k == "elements" and isinstance(v, list):
                for sl, upd in zip(dwg.get("elements", []), v):
                    if upd:
                        sl.update(upd)
            else:
                dwg[k] = copy.deepcopy(v)
    return dwg


def zemax_export_optical_drawing(
    element_index: Optional[Union[int, str]] = None,
    output_dir: Optional[str] = None,
    project_name: Optional[str] = None,
    drawing_standard: str = "GB/T 13323",
    iso_tolerance_grade: str = "Precision",
    export_assembly: bool = True,
    generate_2d_plot: bool = True,
    export_dxf: bool = True,
    od_source: str = "standard",
    first_surface: Optional[int] = None,
    last_surface: Optional[int] = None,
    split_cemented: bool = False,
    overrides: Optional[Union[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Generate manufacturing-compliant optical engineering drawings according to Chinese National Standards
    (GB/T 13323-2009, GB/T 903-2019, GB/T 2831-2009, GB/T 1185-2006) and ISO 10110.

    Strictly complies with the mandate:
    1. Standard Chinese GB/T 13323 / GB/T 10609.1 title block with the '单位' label preserved and its value area left completely blank.
    2. Linear dimensions without 'mm' suffix (unsigned R values and reference (ET) at rim).
    3. Multi-element cemented components with sparse GB/T optical glass cross-hatching and element numbers.
    4. Optical characteristics table with Δnd, Δ(nF-nC), N, ΔN, ΔR, B, χ, f, Sf, D0.
    5. Radial curvature leaders with center marks '+' and chamfer callouts.
    6. System assembly layout drawing with overall track and air gaps.

    Args:
        element_index: Specific element index (1-based), or 'assembly' / -1 to export assembly drawing only,
                       or None to export both all individual elements and the full assembly.
        output_dir: Target directory (defaults to 'output/<project_name>/drawings').
        project_name: Optional target project name.
        drawing_standard: 'GB/T 13323' (default, Chinese National Standard) or 'ISO 10110'.
        iso_tolerance_grade: 'Commercial', 'Precision' (default), or 'High-Precision'.
        export_assembly: If True, also generates the system assembly drawing (drawing_assembly.png).
        generate_2d_plot: If True, renders dimensioned engineering drawings via matplotlib.
        od_source: 'standard' (clear aperture + 2 mm, rounded up to a stock diameter) or 'model'
                   (lens outer diameter = mechanical semi-diameters of the lens file; a concave face whose
                   clear semi-diameter is smaller is drawn as sphere + flat land with its polished diameter).
        first_surface / last_surface: draw only the lens group inside this surface range (e.g. a scan lens
                   in front of an intermediate image); last_surface also ends the rear gap of the assembly.
        split_cemented: also write one part drawing per lens of a cemented group (drawing_element_3a ...).
        overrides: dict or JSON file path {"common": {...}, "elements": {"1": {...}, "3a": {...}},
                   "assembly": {...}} replacing generated values (real tolerances, per-surface clear
                   apertures, notes, names, layout hints) of the drawings.
    """
    if str(od_source).lower() not in ("standard", "model"):
        return {"status": "error", "message": "od_source must be 'standard' or 'model'."}
    try:
        overrides = _resolve_drawing_overrides(overrides)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"Could not read overrides: {exc}"}

    session = ZOSSession.get_instance()
    sys = session.system

    num_surfs = sys.LDE.NumberOfSurfaces
    if num_surfs < 4:
        return {
            "status": "error",
            "message": "Optical system too simple for export (need at least one lens). Load a valid design with zemax_load_file.",
        }

    if project_name:
        set_active_project(project_name)
    active_proj = get_active_project_name()

    output_dir = resolve_project_directory_path(
        output_dir, subfolder="drawings", project_name=active_proj
    )

    elements = _extract_lens_elements(sys)
    if first_surface is not None or last_surface is not None:
        lo = int(first_surface) if first_surface is not None else 0
        hi = int(last_surface) if last_surface is not None else int(num_surfs)
        elements = [e for e in elements if e["surface_start"] >= lo and e["surface_end"] <= hi]
    if not elements:
        return {"status": "error", "message": "No optical lens elements found in the current system (or in the given surface range)."}

    metadata = _system_drawing_metadata(sys, session.ZOSAPI, elements, image_surface=last_surface)
    if metadata["lens_stack_length_mm"] is None:
        return {"status": "error", "message": "Axial drawings require a centered, forward-going sequential system; this geometry needs a separate mechanical layout."}

    tolerance_presets = {
        "commercial": {
            "iso_0": "0/ 10 nm/cm (Commercial)",
            "iso_1": "1/ 5x0.25 (Bubbles & Inclusions)",
            "iso_2": "2/ 2; 3 (Inhomogeneity & Striae)",
            "iso_3": "3/ 5(2) RMS <= 0.10 um (Surface Form N=5, dN=2)",
            "iso_4": "4/ 3' (Centering Wedge Angle <= 3 arcmin)",
            "iso_5": "5/ 5x0.4; L 1x0.04 (Scratch-Dig 60-40)",
            "scratch_dig": "60-40",
            "ct_tol": "±0.10",
            "dia_tol": "-0.10",
            "radius_tol": "±0.2%",
            "delta_nd": "3C", "delta_nf_nc": "3C", "homogeneity": "3", "stress_biref": "2",
            "striae": "2", "bubbles": "2", "N": "5", "delta_N": "1.0", "delta_R": "B",
            "B": "V", "wedge_chi": "3'"
        },
        "precision": {
            "iso_0": "0/ 5 nm/cm (Precision Low Stress)",
            "iso_1": "1/ 3x0.16 (Bubbles & Inclusions)",
            "iso_2": "2/ 1; 2 (Inhomogeneity & Striae)",
            "iso_3": "3/ 3/1(0.5) RMS <= 0.05 um (Surface Form N=3, dN=1)",
            "iso_4": "4/ 1' (Centering Wedge Angle <= 1 arcmin)",
            "iso_5": "5/ 3x0.16; L 1x0.01 (Scratch-Dig 40-20)",
            "scratch_dig": "40-20",
            "ct_tol": "±0.02",
            "dia_tol": "-0.05",
            "radius_tol": "±0.1%",
            "delta_nd": "2C", "delta_nf_nc": "2C", "homogeneity": "2", "stress_biref": "1",
            "striae": "1", "bubbles": "1", "N": "3", "delta_N": "0.3", "delta_R": "A",
            "B": "IV", "wedge_chi": "1'"
        },
        "high-precision": {
            "iso_0": "0/ 2 nm/cm (Laser Grade)",
            "iso_1": "1/ 1x0.10 (Bubbles & Inclusions)",
            "iso_2": "2/ 1; 1 (Inhomogeneity & Striae)",
            "iso_3": "3/ 1/0.5(0.2) RMS <= 0.02 um (Lambda/20)",
            "iso_4": "4/ 30\" (Centering Wedge Angle <= 30 arcsec)",
            "iso_5": "5/ 1x0.10; L 1x0.005 (Scratch-Dig 20-10 / 10-5)",
            "scratch_dig": "20-10",
            "ct_tol": "±0.01",
            "dia_tol": "-0.02",
            "radius_tol": "±0.05%",
            "delta_nd": "1A", "delta_nf_nc": "1A", "homogeneity": "1", "stress_biref": "1",
            "striae": "1", "bubbles": "1", "N": "2", "delta_N": "0.2", "delta_R": "A",
            "B": "III", "wedge_chi": "30\""
        },
    }
    tols = tolerance_presets.get(iso_tolerance_grade.lower(), tolerance_presets["precision"])

    # Determine targets
    is_assembly_only = str(element_index).lower() in ["assembly", "-1"]
    target_elements = [] if is_assembly_only else elements
    if element_index is not None and not is_assembly_only:
        filtered = [e for e in elements if e["element_index"] == int(element_index)]
        if not filtered:
            return {
                "status": "error",
                "message": f"Element {element_index} not found. System has {len(elements)} elements.",
            }
        target_elements = filtered

    generated_drawings = []

    def _emit_element_drawing(dwg: Dict[str, Any], tag: str):
        """Write DXF / PNG / GB/T markdown of one element (or one lens of a cemented group)."""
        png_path = os.path.join(output_dir, f"drawing_element_{tag}.png")
        dxf_path = os.path.join(output_dir, f"drawing_element_{tag}.dxf")
        md_path = os.path.join(output_dir, f"gbt13323_drawing_element_{tag}.md")
        png_rendered = False
        if export_dxf and EZDXF_AVAILABLE:
            try:
                _export_gbt13323_element_dxf(dwg, dxf_path)
                dwg["dxf_drawing_file"] = dxf_path
                if generate_2d_plot:
                    png_rendered = _render_dxf_to_png(dxf_path, png_path)
            except Exception as e:
                dwg["dxf_error"] = str(e)
        if generate_2d_plot and not png_rendered:
            _render_gbt13323_element_drawing(dwg, png_path)
        # DXF export sets the actual drawing scale; keep the specification in sync.
        _write_gbt13323_element_markdown(dwg, md_path)
        dwg["gbt_markdown_file"] = md_path
        dwg["drawing_image_file"] = png_path
        generated_drawings.append(dwg)

    # 1. Process Individual Elements
    for elem in target_elements:
        e_idx = elem["element_index"]
        e_type = elem["element_type"]
        components = elem["components"]

        mech_od, max_ca = _element_outer_diameter(components, od_source)
        model_od = str(od_source).lower() == "model"

        elem_slices = []
        for c_i, comp in enumerate(components):
            elem_slices.append({
                "index": c_i + 1,
                "r1": round(comp["radius_front"], 4),
                "r2": round(comp["radius_rear"], 4),
                "k1": comp["conic_front"],
                "k2": comp["conic_rear"],
                "t": round(comp["thickness"], 4),
                "t_tolerance": tols["ct_tol"],
                "material": comp["material"],
                **(_flat_land_polish_diameters(comp) if model_od else {}),
            })

        mat_str = "\n".join(c["material"] for c in components)
        type_names = {
            "single_lens": "单透镜",
            "cemented_doublet": "双胶合透镜组",
            "cemented_multiplet": "三胶合透镜组" if len(components) == 3 else f"{len(components)}胶合透镜组",
            "optical_window": "平晶/盖玻片",
        }
        cn_type = type_names.get(e_type, "透镜元件")

        primary_wave_idx = 1
        primary_wave_nm = ""
        try:
            zos_api = session.ZOSAPI
            waves_obj = sys.SystemData.Wavelengths
            for wi in range(1, int(waves_obj.NumberOfWavelengths) + 1):
                if waves_obj.GetWavelength(wi).IsPrimary:
                    primary_wave_idx = wi
                    primary_wave_nm = f"{float(waves_obj.GetWavelength(wi).Wavelength) * 1000.0:g}"
                    break
            if not primary_wave_nm and int(waves_obj.NumberOfWavelengths) >= 1:
                primary_wave_nm = f"{float(waves_obj.GetWavelength(1).Wavelength) * 1000.0:g}"
        except Exception:
            zos_api = None

        elem_efl, elem_bfl = _compute_element_paraxial_efl_bfl(sys, zos_api, elem, primary_wave_idx)
        efl_str_val = f"{elem_efl:.2f}" if elem_efl is not None else "-"
        bfl_str_val = f"{elem_bfl:.2f}" if elem_bfl is not None else "-"

        mat_note = (
            "、".join(f"L{ci + 1}:{c['material']}" for ci, c in enumerate(components))
            if len(components) > 1
            else components[0]["material"]
        )
        wave_note = (
            f"5、焦距及后截距对应波长：{primary_wave_nm}nm（设计波段：{metadata['design_wavelengths']}）。"
            if primary_wave_nm and primary_wave_nm in metadata["design_wavelengths"]
            else f"5、焦距及后截距对应设计波长：{metadata['design_wavelengths']}。"
        )
        # Keep wavelength list visible for multi-wavelength systems
        if metadata.get("wavelengths_nm") and len(metadata["wavelengths_nm"]) > 1:
            wave_note = f"5、焦距及后截距对应波长：{primary_wave_nm or metadata['design_wavelengths']}nm（全波段：{metadata['design_wavelengths']}）。"

        dwg_data = {
            **metadata,
            "drawing_date": "",
            "element_index": e_idx,
            "drawing_name": f"光学元件\n{cn_type} {e_idx}",
            "drawing_code": f"OPT-{active_proj.upper()[:6]}-E{e_idx:02d}",
            "material": mat_str,
            "od": mech_od,
            "ca": round(max_ca, 2),
            "od_tolerance": tols["dia_tol"],
            "drawing_scale": "2:1",
            "delta_nd": tols["delta_nd"],
            "delta_nf_nc": tols["delta_nf_nc"],
            "homogeneity": tols["homogeneity"],
            "stress_biref": tols["stress_biref"],
            "striae": tols["striae"],
            "bubbles": tols["bubbles"],
            "N": tols["N"],
            "delta_N": tols["delta_N"],
            "delta_R": tols["delta_R"],
            "B": tols["B"],
            "wedge_chi": tols["wedge_chi"],
            "scratch_dig": tols["scratch_dig"],
            "surface_quality": tols["iso_5"],
            "coating": _element_coating(sys, elem),
            "efl_str": efl_str_val,
            "bfl_str": bfl_str_val,
            "ca_str": f"{max_ca:.1f}",
            "technical_notes": [
                f"1、材料采用指定牌号优质光学玻璃（{mat_note}）；",
                "2、未注倒角按图注执行，棱边不得崩边；",
                "3、胶合采用环氧树脂或光敏胶，胶层厚度 0.01~0.02mm；" if len(components) > 1 else "3、机械外圆磨砂加工，表面粗糙度 1.6；",
                f"4、{_format_chinese_coating_note(sys, elem, metadata['spectral_range'])}，表面光洁度 {tols['B']}（{tols['scratch_dig']}）；",
                wave_note,
            ],
            "elements": elem_slices,
        }

        base_dwg_data = copy.deepcopy(dwg_data)          # before overrides: source of the split part drawings
        _apply_drawing_overrides(dwg_data, overrides, str(e_idx))
        iso_md_filepath = os.path.join(output_dir, f"iso10110_drawing_element_{e_idx}.md")

        # Also write ISO 10110 spec for compatibility
        c0 = components[0]
        c_last = components[-1]
        iso_spec = {
            "design_wavelengths": metadata["design_wavelengths"],
            "element_index": e_idx,
            "element_type": e_type,
            "surfaces": f"S{elem['surface_start']} - S{elem['surface_end']}",
            "material": "/".join(c["material"] for c in components),
            "mechanical_diameter_od_mm": mech_od,
            "diameter_tolerance": tols["dia_tol"],
            "center_thickness_ct_mm": round(elem["total_center_thickness"], 4),
            "center_thickness_tolerance": tols["ct_tol"],
            "edge_thickness_et_mm": round(elem["total_center_thickness"]
                                          - _rim_sag(c0["radius_front"], c0["conic_front"], mech_od / 2.0, elem_slices[0].get("polish_dia_front"))
                                          + _rim_sag(c_last["radius_rear"], c_last["conic_rear"], mech_od / 2.0, elem_slices[-1].get("polish_dia_rear")), 4),
            "flat_land_width_mm": round((mech_od - max_ca) / 2.0, 3),
            "protective_chamfer_mm": "0.3 x 45°",
            "surface_1": {
                "radius_r1_mm": round(c0["radius_front"], 4) if c0["radius_front"] != 0 else "Plano (Infinity)",
                "conic_k1": c0["conic_front"],
                "clear_aperture_ca1_mm": round(2 * c0["semi_diameter_front"], 3),
                "radius_tolerance": tols["radius_tol"],
                "surface_form_iso3": tols["iso_3"],
                "centering_iso4": tols["iso_4"],
                "scratch_dig_iso5": tols["iso_5"],
            },
            "surface_2": {
                "radius_r2_mm": round(c_last["radius_rear"], 4) if c_last["radius_rear"] != 0 else "Plano (Infinity)",
                "conic_k2": c_last["conic_rear"],
                "clear_aperture_ca2_mm": round(2 * c_last["semi_diameter_rear"], 3),
                "radius_tolerance": tols["radius_tol"],
                "surface_form_iso3": tols["iso_3"],
                "centering_iso4": tols["iso_4"],
                "scratch_dig_iso5": tols["iso_5"],
            },
            "material_tolerances": {
                "stress_birefringence_iso0": tols["iso_0"],
                "bubbles_iso1": tols["iso_1"],
                "inhomogeneity_iso2": tols["iso_2"],
            },
            "coating": dwg_data["coating"],
        }
        _write_element_markdown_drawing(iso_spec, iso_md_filepath)
        dwg_data["iso_markdown_file"] = iso_md_filepath
        _emit_element_drawing(dwg_data, str(e_idx))

        # One part drawing per lens of a cemented group (each lens is polished on its own)
        if split_cemented and len(components) > 1:
            for c_i, comp in enumerate(components):
                tag = f"{e_idx}{chr(ord('a') + c_i)}"
                part = copy.deepcopy(base_dwg_data)
                sl = dict(part["elements"][c_i])
                sl.update({"index": 1, "mark_front": c_i == 0, "mark_rear": c_i == len(components) - 1})
                if c_i > 0:
                    sl["chamfer_front"] = 0.0                     # cemented faces are not chamfered
                if c_i < len(components) - 1:
                    sl["chamfer_rear"] = 0.0
                part_elem = {"components": [comp]}
                p_efl, p_bfl = _compute_element_paraxial_efl_bfl(sys, zos_api, part_elem, primary_wave_idx)
                part_ca = max(2 * comp["semi_diameter_front"], 2 * comp["semi_diameter_rear"])
                part_notes = list(part["technical_notes"])
                part_notes[0] = f"1、材料采用指定牌号优质光学玻璃（{comp['material']}）；"
                part_notes[2] = f"3、本件为胶合件 {e_idx} 的第 {c_i + 1} 片，无 ⊕ 标记的面为胶合面，不镀膜；"
                part.update({
                    "element_index": tag,
                    "drawing_name": f"光学元件\n单透镜 {tag}",
                    "drawing_code": f"OPT-{active_proj.upper()[:6]}-E{e_idx:02d}{chr(ord('A') + c_i)}",
                    "material": comp["material"],
                    "ca": round(part_ca, 2),
                    "ca_str": f"{part_ca:.1f}",
                    "efl_str": f"{p_efl:.2f}" if p_efl is not None else "-",
                    "bfl_str": f"{p_bfl:.2f}" if p_bfl is not None else "-",
                    "technical_notes": part_notes,
                    "elements": [sl],
                })
                _apply_drawing_overrides(part, overrides, tag)
                _emit_element_drawing(part, tag)

    # 2. Process System Assembly Drawing
    assembly_drawing_info = None
    if export_assembly or is_assembly_only:
        num_surfs = sys.LDE.NumberOfSurfaces
        assembly_elements = []
        for e_i, elem in enumerate(elements):
            s_end = elem["surface_end"]
            air_after = 0.0
            if e_i < len(elements) - 1 and s_end < num_surfs:
                for gap_index in range(s_end, elements[e_i + 1]["surface_start"]):
                    t_val = float(sys.LDE.GetSurfaceAt(gap_index).Thickness)
                    if math.isfinite(t_val) and abs(t_val) < 1e4:
                        air_after += t_val

            comps = []
            for comp in elem["components"]:
                comps.append({
                    "r1": round(comp["radius_front"], 4),
                    "r2": round(comp["radius_rear"], 4),
                    "k1": comp["conic_front"],
                    "k2": comp["conic_rear"],
                    "ct": round(comp["thickness"], 4),
                    "ca": round(max(2 * comp["semi_diameter_front"], 2 * comp["semi_diameter_rear"]), 2),
                    "material": comp["material"],
                    **(_flat_land_polish_diameters(comp) if str(od_source).lower() == "model" else {}),
                })

            mech_od = _element_outer_diameter(elem["components"], od_source)[0]

            assembly_elements.append({
                "elem_index": elem["element_index"],
                "components": comps,
                "od": mech_od,
                "ct": round(elem["total_center_thickness"], 4),
                "air_after": round(air_after, 4)
            })

        # Extract first-order optical data for assembly drawing
        efl_str = "-"
        na_str = "-"
        fov_str = "-"
        pupil_str = "-"
        immersion_str = "空气 (n=1.000)"
        axial_color_str = "-"
        f_num_computed = "-"
        try:
            zos = session.ZOSAPI
            waves = sys.SystemData.Wavelengths
            primary_wave = next((i for i in range(1, int(waves.NumberOfWavelengths) + 1)
                                 if waves.GetWavelength(i).IsPrimary), 1)
            if first_surface is not None or last_surface is not None:
                # focal length of the drawn group, not of the whole system
                efl_num = sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.EFLY,
                                                  elements[0]["surface_start"], elements[-1]["surface_end"], 0, 0, 0, 0, 0, 0)
            else:
                efl_num = sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.EFFL, 0, primary_wave, 0, 0, 0, 0, 0, 0)
            if abs(efl_num) > 0.001 and abs(efl_num) < 1e5:
                efl_str = f"{efl_num:.2f}"
        except Exception:
            efl_num = 0.0

        try:
            sd = sys.SystemData
            ap_type = str(sd.Aperture.ApertureType)
            ap_val = float(sd.Aperture.ApertureValue)
            if ((first_surface is not None or last_surface is not None) and "EntrancePupil" in ap_type
                    and ap_val > 0 and abs(efl_num) > 0.001):
                f_num_computed = f"F/{abs(efl_num) / ap_val:.2f}"       # the group alone, same entrance beam
            elif ap_type == "ObjectSpaceNA":
                na_str = f"{ap_val:.2f}"
            elif ap_type == "ImageSpaceFNum" and ap_val > 0:
                f_num_computed = f"F/{ap_val:.2f}"
        except Exception:
            pass

        try:
            zos = session.ZOSAPI
            expd = sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.EXPD, 0, 0, 0, 0, 0, 0, 0, 0)
            if 0.001 < abs(expd) < 1e5:
                pupil_str = f"Φ {abs(expd):.2f} mm"
                if f_num_computed == "-" and na_str == "-" and abs(efl_num) > 0.001:
                    f_num_computed = f"F/{abs(efl_num / expd):.2f}"
            axcl = sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.AXCL, 0, 0, 0, 0, 0, 0, 0, 0)
            if abs(axcl) < 1e3:
                axial_color_str = f"{abs(axcl) * 1000.0:.1f} μm"
        except Exception:
            pass

        try:
            flds = sys.SystemData.Fields
            max_f = max(
                math.hypot(float(flds.GetField(i).X), float(flds.GetField(i).Y))
                for i in range(1, int(flds.NumberOfFields) + 1)
            )
            if "Angle" in str(flds.GetFieldType()):
                fov_str = f"2ω = {2.0 * max_f:.2f}°"
            elif max_f > 0:
                fov_str = f"Φ {2.0 * max_f:.2f} mm"
        except Exception:
            pass

        try:
            obj_mat = str(sys.LDE.GetSurfaceAt(0).Material).strip()
            if obj_mat:
                immersion_str = obj_mat
        except Exception:
            pass

        proj_clean = active_proj.replace('_', ' ').title()
        dwg_name_title = f"{proj_clean}\n光学系统总装图"
        bfl_val = float(metadata.get("rear_image_gap_mm") or 0.0)
        bfl_str = f"{bfl_val:.2f}" if bfl_val > 0.001 else "-"
        grade_l = str(iso_tolerance_grade or "precision").lower()
        gap_tol_str = "\u00b10.01" if "high" in grade_l else ("\u00b10.05" if "comm" in grade_l else "\u00b10.02")
        coax_tol_str = "0.002" if "high" in grade_l else ("0.010" if "comm" in grade_l else "0.005")
        para_tol_str = "0.001" if "high" in grade_l else ("0.008" if "comm" in grade_l else "0.003")

        asm_dwg_data = {
            **metadata,
            "drawing_date": "",
            "drawing_name": dwg_name_title,
            "drawing_code": f"ASM-{active_proj.upper()[:8]}-00",
            "drawing_scale": "2:1",
            "od": max(e["od"] for e in assembly_elements),
            "od_tolerance": tols["dia_tol"],
            "air_gap_tol": gap_tol_str,
            "coax_tol": coax_tol_str,
            "para_tol": para_tol_str,
            "na_str": na_str,
            "f_num_str": f"NA {na_str}" if na_str != "-" else f_num_computed,
            "efl_str": efl_str,
            "bfl_str": bfl_str,
            "fov_str": fov_str,
            "pupil_str": pupil_str,
            "immersion_str": immersion_str,
            "coverglass_str": "-",
            "rms_wavefront": "-",
            "axial_color": axial_color_str,
            "concentricity": f"< {coax_tol_str}",
            "barrel_fit": "g6/H7",
            "assembly_elements": assembly_elements,
            "technical_notes": [
                f"1、波段：{metadata['spectral_range']}，{f_num_computed}，{fov_str}；",
                f"2、焦距 f'={efl_str}mm，后截距 BFL={bfl_str}mm；",
                f"3、各镜片光轴同轴度 ≤ {coax_tol_str}mm，隔圈平行度 ≤ {para_tol_str}mm；",
                f"4、胶合件须定心对中，偏角差 χ ≤ {tols['wedge_chi']}；",
                "5、镜筒内壁消光发黑处理，工作温度：20℃±2℃。"
            ]
        }

        _apply_drawing_overrides(asm_dwg_data, overrides, "assembly")
        asm_png_path = os.path.join(output_dir, "drawing_assembly.png")
        asm_md_path = os.path.join(output_dir, "drawing_assembly.md")

        _write_assembly_markdown_drawing(asm_dwg_data, asm_md_path)
        asm_dxf_path = os.path.join(output_dir, "drawing_assembly.dxf")
        asm_png_rendered = False

        if export_dxf and EZDXF_AVAILABLE:
            try:
                _export_gbt13323_assembly_dxf(asm_dwg_data, asm_dxf_path)
                if generate_2d_plot:
                    asm_png_rendered = _render_dxf_to_png(asm_dxf_path, asm_png_path)
            except Exception as e:
                asm_dxf_path = f"error: {str(e)}"

        if generate_2d_plot and not asm_png_rendered:
            _render_gbt13323_assembly_drawing(asm_dwg_data, asm_png_path)


        assembly_drawing_info = {
            "assembly_markdown_file": asm_md_path,
            "assembly_image_file": asm_png_path,
            "assembly_dxf_file": asm_dxf_path,
            "total_track_length": metadata["total_track_length_mm"],
            "lens_stack_length_mm": metadata["lens_stack_length_mm"],
            "rear_image_gap_mm": metadata["rear_image_gap_mm"],
            "object_working_distance_mm": metadata["object_working_distance_mm"],
            "object_at_infinity": metadata["object_at_infinity"],
            "distance_basis": metadata["distance_basis"],
            "elements_count": len(assembly_elements),
        }

    return {
        "status": "success",
        "output_directory": output_dir,
        "standard": "GB/T 13323-2009 & ISO 10110",
        "tolerance_grade": iso_tolerance_grade,
        "title_block_specification": "Complies strictly with GB/T 13323-2009 / GB/T 10609.1 standard; '单位' cell header preserved with value left completely empty.",
        "drawings_count": len(generated_drawings),
        "element_drawings": generated_drawings,
        "assembly_drawing": assembly_drawing_info,
        "solidworks_mcp_guidance": "These national standard drawings and data bridge provide exact lens OD, flat lands, and thicknesses needed for SolidWorks lens barrel and spacer modeling.",
    }


def zemax_export_prescription_for_cad(
    margin_mm: float = 2.0,
    output_filepath: Optional[str] = None,
    project_name: Optional[str] = None,
    barrel_radial_clearance_mm: float = 0.05,
) -> Dict[str, Any]:
    """
    Opto-Mechanical Bridge Tool: Extracts the complete optical prescription and translates it
    into structured payloads directly compatible with SolidWorks MCP tools:
    - 'build_system_from_prescription': Surfaces array ready to construct all 3D solid lens parts.
    - 'create_3d_lens_spacer': Pre-calculated spacing rings for every air gap.
    - 'create_3d_retaining_ring': Lock ring threads and diameters.
    - 'create_3d_lens_barrel': Stepped bore barrel dimensions.

    Args:
        margin_mm: Mechanical rim margin added to 2 * SemiDiameter (default 2.0 mm).
        output_filepath: JSON export path (default 'output/<project_name>/optomech/<system_name>_prescription.json').
        project_name: Optional target project name.
        barrel_radial_clearance_mm: Radial tolerance clearance between lens OD and barrel bore (default 0.05 mm).
    """
    if isinstance(margin_mm, str) and output_filepath is None:
        output_filepath = margin_mm
        margin_mm = 2.0
    else:
        try:
            margin_mm = float(margin_mm)
        except (TypeError, ValueError):
            return {"status": "error", "message": "margin_mm must be finite and positive."}

    if not math.isfinite(margin_mm) or margin_mm <= 0:
        return {"status": "error", "message": "margin_mm must be finite and positive."}
    if not math.isfinite(barrel_radial_clearance_mm) or barrel_radial_clearance_mm < 0:
        return {"status": "error", "message": "barrel_radial_clearance_mm must be finite and nonnegative."}

    session = ZOSSession.get_instance()
    sys = session.system

    num_surfs = sys.LDE.NumberOfSurfaces
    if num_surfs < 4:
        return {
            "status": "error",
            "message": "Optical system too simple for export (need at least one lens). Load a valid design with zemax_load_file.",
        }

    if project_name:
        set_active_project(project_name)
    active_proj = get_active_project_name()

    surfaces_data = []

    # 1. Extract raw surfaces compatible with build_system_from_prescription
    for s_idx in range(1, num_surfs):
        s_obj = sys.LDE.GetSurfaceAt(s_idx)
        r = float(s_obj.Radius)
        t = float(s_obj.Thickness)
        m = str(s_obj.Material).strip()
        sd = float(s_obj.SemiDiameter)

        surfaces_data.append({
            "surface": s_idx,
            "radius": 0.0 if (math.isinf(r) or abs(r) > 1e9) else round(r, 4),
            "thickness": 0.0 if (math.isinf(t) or abs(t) > 1e9) else round(t, 4),
            "material": m if m else "",
            "semi_diameter": round(sd, 4) if not math.isinf(sd) else 0.0,
            "is_stop": bool(s_obj.IsStop),
        })

    # 2. Extract discrete elements & compute air spacers
    elements = _extract_lens_elements(sys)
    if not elements:
        return {"status": "error", "message": "No optical lens elements found in the current system."}
    geometry = _system_drawing_metadata(sys, session.ZOSAPI, elements)
    if geometry["lens_stack_length_mm"] is None:
        return {"status": "error", "message": "Ring recommendations require a centered, forward-going sequential system."}

    spacers = []
    lens_parts = []

    for e in elements:
        s_start = e["surface_start"]
        s_end = e["surface_end"]

        # Compute max CA and recommended OD
        max_ca = 2.0 * e["max_semi_diameter"]
        mech_od = math.ceil(max_ca + margin_mm)

        lens_parts.append({
            "element_index": e["element_index"],
            "element_type": e["element_type"],
            "surface_start": s_start,
            "surface_end": s_end,
            "material": "/".join(c["material"] for c in e["components"]),
            "center_thickness": round(e["total_center_thickness"], 4),
            "clear_aperture_mm": round(max_ca, 3),
            "recommended_od_mm": mech_od,
            "recommended_barrel_bore_mm": round(mech_od + 2.0 * barrel_radial_clearance_mm, 3),
        })

    # Find air gaps between elements for spacer rings
    for idx in range(len(elements) - 1):
        e1 = elements[idx]
        e2 = elements[idx + 1]

        gap_surf = e1["surface_end"]
        # Sum thickness between e1 end and e2 start
        air_gap_thickness = 0.0
        for s in range(gap_surf, e2["surface_start"]):
            t_val = float(sys.LDE.GetSurfaceAt(s).Thickness)
            if not math.isinf(t_val) and abs(t_val) < 1e9:
                air_gap_thickness += t_val

        od1 = lens_parts[idx]["recommended_od_mm"]
        od2 = lens_parts[idx + 1]["recommended_od_mm"]
        # Use the larger aperture and all intervening surfaces. The smaller
        # mating OD limits support/fit; enlarging it would cause interference.
        aperture_envelope = max(
            2.0 * e1["max_semi_diameter"], 2.0 * e2["max_semi_diameter"],
            *(2.0 * float(sys.LDE.GetSurfaceAt(s).SemiDiameter)
              for s in range(gap_surf, e2["surface_start"] + 1)))
        try:
            dimensions = _ring_dimensions(aperture_envelope, min(od1, od2))
        except ValueError as exc:
            return {"status": "error", "message": f"Spacer {idx + 1}: {exc}"}
        spacer_id = dimensions["inner_diameter_mm"]
        spacer_od = dimensions["outer_diameter_mm"]

        spacer_len = round(air_gap_thickness, 3)
        if spacer_len <= 0:
            return {"status": "error", "message": f"Spacer {idx + 1} has no positive axial space."}

        spacers.append({
            **dimensions,
            "spacer_index": idx + 1,
            "between_elements": f"Element {idx+1} -> Element {idx+2}",
            "inner_diameter_mm": spacer_id,
            "outer_diameter_mm": spacer_od,
            "length_mm": spacer_len,
            "solidworks_mcp_command": {
                "tool": "create_3d_lens_spacer",
                "arguments": {
                    "inner_diameter": spacer_id,
                    "outer_diameter": spacer_od,
                    "length": spacer_len,
                },
            },
        })

    # Recommended Retaining Ring
    front_od = lens_parts[0]["recommended_od_mm"]
    try:
        front_dimensions = _ring_dimensions(2.0 * elements[0]["max_semi_diameter"], front_od)
    except ValueError as exc:
        return {"status": "error", "message": f"Retaining ring: {exc}"}
    front_id = front_dimensions["inner_diameter_mm"]
    retaining_ring = {
        **front_dimensions,
        "outer_diameter_mm": front_od,
        "inner_diameter_mm": front_id,
        "thickness_mm": 2.5,
        "solidworks_mcp_command": {
            "tool": "create_3d_retaining_ring",
            "arguments": {
                "outer_diameter": front_od,
                "inner_diameter": front_id,
                "thickness": 2.5,
            },
        },
    }

    # Recommended Lens Barrel Stepped Bore
    max_barrel_od = max(lp["recommended_barrel_bore_mm"] for lp in lens_parts) + 4.0 if lens_parts else 30.0
    barrel_steps = [
        {
            "step_index": lp["element_index"],
            "bore_diameter_mm": lp["recommended_barrel_bore_mm"],
            "depth_mm": round(lp["center_thickness"] + 1.0, 2),
        }
        for lp in lens_parts
    ]

    payload = {
        "status": "success",
        "margin_mm": margin_mm,
        "total_track_mm": round(geometry["total_track_length_mm"], 4) if geometry["total_track_length_mm"] is not None else None,
        "lens_stack_length_mm": geometry["lens_stack_length_mm"],
        "rear_image_gap_mm": geometry["rear_image_gap_mm"],
        "object_working_distance_mm": geometry["object_working_distance_mm"],
        "object_at_infinity": geometry["object_at_infinity"],
        "elements_count": len(lens_parts),
        "spacers_count": len(spacers),
        "ring_validation": {
            "basis": "Surface aperture envelope; centered coaxial placement",
            "radial_aperture_clearance_mm": 0.1,
            "minimum_radial_wall_mm": 0.5,
            "status": "aperture_and_fit_checked",
            "requires_verification": "Trace all fields/wavelengths through the final ring positions and thicknesses; confirm contact, threads and tolerances before manufacture.",
        },
        "solidworks_build_system_payload": {
            "surfaces": surfaces_data,
            "margin_mm": margin_mm,
        },
        "lens_elements": lens_parts,
        "spacing_rings": spacers,
        "retaining_ring": retaining_ring,
        "barrel_specification": {
            "outer_diameter_mm": round(max_barrel_od, 2),
            "stepped_bores": barrel_steps,
        },
    }

    base_name = active_proj
    if session.current_filepath:
        base_name = os.path.splitext(os.path.basename(session.current_filepath))[0]

    target_path = resolve_project_file_path(
        output_filepath,
        default_filename=f"{base_name}_prescription.json",
        subfolder="optomech",
        project_name=active_proj,
    )

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    payload["saved_json_filepath"] = target_path
    return payload


for _name in ("zemax_export_cad", "zemax_export_optical_drawing", "zemax_export_prescription_for_cad"):
    globals()[_name] = serialized_operation(globals()[_name])
