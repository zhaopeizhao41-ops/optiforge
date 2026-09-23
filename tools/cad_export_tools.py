"""
Zemax CAD Export & Optical Drawing Tools
Enables export of native 3D CAD files (STEP, IGES, SAT, STL) and ISO 10110 optical manufacturing drawings,
providing direct optomechanical linkage with SolidWorks MCP.
"""

import json
import math
import os
from typing import Any, Dict, List, Optional
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

from core.zos_session import ZOSSession


def zemax_export_cad(
    filepath: Optional[str] = None,
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
        filepath: Destination file path. If None, saves to 'output/<system_name>.<ext>'.
        file_type: CAD format ('STEP', 'IGES', 'SAT', 'STL'). Default is 'STEP' (ISO 10303).
        surfaces_as_solids: If True, exports closed volumes as solid bodies (required for SolidWorks assembly).
        first_surface: First surface to export (1-based). Default is 1.
        last_surface: Last surface to export. Default is the image surface.
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
    f_surf = int(first_surface) if first_surface is not None else 1
    l_surf = int(last_surface) if last_surface is not None else (num_surfs - 1)

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

    if not filepath:
        default_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
        os.makedirs(default_dir, exist_ok=True)
        base_name = "optical_assembly"
        if session.current_filepath:
            base_name = os.path.splitext(os.path.basename(session.current_filepath))[0]
        target_path = os.path.join(default_dir, f"{base_name}{target_ext}")
    else:
        target_path = os.path.abspath(filepath)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

    cad_tool = sys.Tools.OpenExportCAD()
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
        cad_tool.RunAndWaitForCompletion()

        succeeded = bool(cad_tool.Succeeded)
        error_msg = str(cad_tool.ErrorMessage) if cad_tool.ErrorMessage else None
    finally:
        cad_tool.Close()

    if not succeeded or not os.path.exists(target_path):
        return {
            "status": "error",
            "message": f"CAD Export failed: {error_msg or 'File was not generated.'}",
            "target_path": target_path,
        }

    file_size = os.path.getsize(target_path)

    # Prepare SolidWorks MCP linkage advice
    solidworks_command = {
        "action": "open_solidworks_document",
        "arguments": {
            "file_path": target_path,
            "doc_type": "part" if surfaces_as_solids else "assembly",
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
        "solidworks_mcp_linkage": {
            "recommended_tool": "open_solidworks_document",
            "example_call": solidworks_command,
            "notes": "File is ready to be opened in SolidWorks for lens barrel enclosure, retaining ring, and spacer design.",
        },
    }


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
        if mat and mat.lower() not in ["", "air"]:
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
                conic = float(s_obj.Conic)
                stype = str(s_obj.Type)

                next_s = cur_s + 1
                if next_s <= num_surfs:
                    s_rear_obj = sys.LDE.GetSurfaceAt(next_s)
                    r_rear = float(s_rear_obj.Radius)
                    sd_rear = float(s_rear_obj.SemiDiameter)
                    k_rear = float(s_rear_obj.Conic)
                    m_next = str(s_rear_obj.Material).strip()
                else:
                    r_rear = 0.0
                    sd_rear = sd_front
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
                    "conic_front": conic,
                    "conic_rear": k_rear,
                    "surface_type": stype,
                })

                # If next surface has another glass, it's a cemented doublet/triplet
                if m_next and m_next.lower() not in ["", "air"] and next_s < num_surfs:
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


def _calculate_sag(radius: float, conic: float, y: float) -> float:
    """Calculate exact surface sag z(y)."""
    if radius == 0.0 or abs(radius) > 1e10:
        return 0.0
    c = 1.0 / radius
    arg = 1.0 - (1.0 + conic) * (c**2) * (y**2)
    if arg < 0:
        return math.copysign(abs(radius), radius)
    return (c * y**2) / (1.0 + math.sqrt(arg))


def zemax_export_optical_drawing(
    element_index: Optional[int] = None,
    output_dir: Optional[str] = None,
    iso_tolerance_grade: str = "Precision",
    generate_2d_plot: bool = True,
) -> Dict[str, Any]:
    """
    Generate an ISO 10110 compliant optical manufacturing drawing (specification report & 2D drawing).
    Extracts radii, thicknesses, clear apertures, mechanical rims with flat mounting lands,
    chamfers, glass materials, and standard ISO 10110 tolerance indications.

    Args:
        element_index: Specific element index (1-based). If None, exports drawings for all elements.
        output_dir: Folder to save generated reports and .png drawings (default 'output/drawings').
        iso_tolerance_grade: 'Commercial', 'Precision' (default), or 'High-Precision'.
        generate_2d_plot: If True, renders dimensioned 2D cross-section engineering drawing via matplotlib.
    """
    session = ZOSSession.get_instance()
    sys = session.system

    if not output_dir:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "drawings")
    os.makedirs(output_dir, exist_ok=True)

    elements = _extract_lens_elements(sys)
    if not elements:
        return {"status": "error", "message": "No optical lens elements found in the current system."}

    target_elements = elements
    if element_index is not None:
        filtered = [e for e in elements if e["element_index"] == int(element_index)]
        if not filtered:
            return {
                "status": "error",
                "message": f"Element {element_index} not found. System has {len(elements)} elements.",
            }
        target_elements = filtered

    tolerance_presets = {
        "commercial": {
            "iso_0": "0/ 10 nm/cm (Commercial)",
            "iso_1": "1/ 5x0.25 (Bubbles & Inclusions)",
            "iso_2": "2/ 2; 3 (Inhomogeneity & Striae)",
            "iso_3": "3/ 5(2) RMS <= 0.10 um (Surface Form N=5, dN=2)",
            "iso_4": "4/ 3' (Centering Wedge Angle <= 3 arcmin)",
            "iso_5": "5/ 5x0.4; L 1x0.04 (Scratch-Dig 60-40)",
            "ct_tol": "±0.10 mm",
            "dia_tol": "+0.0 / -0.10 mm",
            "radius_tol": "±0.2%",
        },
        "precision": {
            "iso_0": "0/ 5 nm/cm (Precision Low Stress)",
            "iso_1": "1/ 3x0.16 (Bubbles & Inclusions)",
            "iso_2": "2/ 1; 2 (Inhomogeneity & Striae)",
            "iso_3": "3/ 3/1(0.5) RMS <= 0.05 um (Surface Form N=3, dN=1)",
            "iso_4": "4/ 1' (Centering Wedge Angle <= 1 arcmin)",
            "iso_5": "5/ 3x0.16; L 1x0.01 (Scratch-Dig 40-20)",
            "ct_tol": "±0.05 mm",
            "dia_tol": "+0.0 / -0.05 mm",
            "radius_tol": "±0.1%",
        },
        "high-precision": {
            "iso_0": "0/ 2 nm/cm (Laser Grade)",
            "iso_1": "1/ 1x0.10 (Bubbles & Inclusions)",
            "iso_2": "2/ 1; 1 (Inhomogeneity & Striae)",
            "iso_3": "3/ 1/0.5(0.2) RMS <= 0.02 um (Lambda/20)",
            "iso_4": "4/ 30\" (Centering Wedge Angle <= 30 arcsec)",
            "iso_5": "5/ 1x0.10; L 1x0.005 (Scratch-Dig 20-10 / 10-5)",
            "ct_tol": "±0.02 mm",
            "dia_tol": "+0.0 / -0.02 mm",
            "radius_tol": "±0.05%",
        },
    }
    tols = tolerance_presets.get(iso_tolerance_grade.lower(), tolerance_presets["precision"])

    generated_drawings = []

    for elem in target_elements:
        e_idx = elem["element_index"]
        c0 = elem["components"][0]
        c_last = elem["components"][-1]

        r1 = c0["radius_front"]
        r2 = c_last["radius_rear"]
        k1 = c0["conic_front"]
        k2 = c_last["conic_rear"]

        ca1 = round(2.0 * c0["semi_diameter_front"], 3)
        ca2 = round(2.0 * c_last["semi_diameter_rear"], 3)
        max_ca = max(ca1, ca2)

        # Standard mechanical outer diameter: Add margin and round up to clean standard
        raw_od = max_ca + 2.0
        # Round to standard millimeter or standard 12.7 / 25.4 / 30.0 / 50.8
        std_ods = [12.7, 16.0, 20.0, 25.0, 25.4, 30.0, 38.1, 50.0, 50.8]
        mech_od = raw_od
        for std in std_ods:
            if std >= raw_od:
                mech_od = std
                break
        if mech_od == raw_od:
            mech_od = math.ceil(raw_od)

        flat_land_w = round((mech_od - max_ca) / 2.0, 3)
        ct = round(elem["total_center_thickness"], 4)

        # Calculate exact edge thickness at mechanical rim
        y_rim = mech_od / 2.0
        sag1 = _calculate_sag(r1, k1, y_rim)
        sag2 = _calculate_sag(r2, k2, y_rim)
        et = round(ct - sag1 + sag2, 4)

        materials_str = "/".join(c["material"] for c in elem["components"])

        drawing_spec = {
            "element_index": e_idx,
            "element_type": elem["element_type"],
            "surfaces": f"S{elem['surface_start']} - S{elem['surface_end']}",
            "material": materials_str,
            "mechanical_diameter_od_mm": mech_od,
            "diameter_tolerance": tols["dia_tol"],
            "center_thickness_ct_mm": ct,
            "center_thickness_tolerance": tols["ct_tol"],
            "edge_thickness_et_mm": et,
            "flat_land_width_mm": flat_land_w,
            "protective_chamfer_mm": "0.3 x 45°",
            "surface_1": {
                "radius_r1_mm": round(r1, 4) if r1 != 0 else "Plano (Infinity)",
                "conic_k1": k1,
                "clear_aperture_ca1_mm": ca1,
                "radius_tolerance": tols["radius_tol"],
                "surface_form_iso3": tols["iso_3"],
                "centering_iso4": tols["iso_4"],
                "scratch_dig_iso5": tols["iso_5"],
            },
            "surface_2": {
                "radius_r2_mm": round(r2, 4) if r2 != 0 else "Plano (Infinity)",
                "conic_k2": k2,
                "clear_aperture_ca2_mm": ca2,
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
            "coating": "BBAR (400 - 700 nm, R_avg < 0.5%)",
        }

        # Save Markdown technical specifications
        md_filename = f"iso10110_drawing_element_{e_idx}.md"
        md_filepath = os.path.join(output_dir, md_filename)
        _write_element_markdown_drawing(drawing_spec, md_filepath)
        drawing_spec["spec_markdown_file"] = md_filepath

        # Generate 2D dimensioned plot if requested
        if generate_2d_plot:
            png_filename = f"drawing_element_{e_idx}.png"
            png_filepath = os.path.join(output_dir, png_filename)
            _render_2d_lens_drawing(drawing_spec, r1, r2, k1, k2, ct, mech_od, ca1, ca2, png_filepath)
            drawing_spec["drawing_image_file"] = png_filepath

        generated_drawings.append(drawing_spec)

    return {
        "status": "success",
        "output_directory": output_dir,
        "tolerance_grade": iso_tolerance_grade,
        "drawings_count": len(generated_drawings),
        "drawings": generated_drawings,
        "solidworks_mcp_guidance": "These ISO 10110 specifications provide the exact lens OD, flat lands, and thicknesses needed for SolidWorks lens barrel and spacer modeling.",
    }


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


def _render_2d_lens_drawing(
    spec: Dict[str, Any],
    r1: float,
    r2: float,
    k1: float,
    k2: float,
    ct: float,
    od: float,
    ca1: float,
    ca2: float,
    filepath: str,
):
    """Render a dimensioned 2D engineering cross-section of the lens element."""
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    ax.set_aspect("equal")

    y_half_od = od / 2.0
    y_half_ca1 = ca1 / 2.0
    y_half_ca2 = ca2 / 2.0

    # Profile generation
    y_pts = np.linspace(-y_half_od, y_half_od, 200)

    # Front profile z1(y)
    z1_pts = []
    for y in y_pts:
        if abs(y) <= y_half_ca1:
            z1_pts.append(_calculate_sag(r1, k1, y))
        else:
            # Flat land
            z1_pts.append(_calculate_sag(r1, k1, y_half_ca1))

    # Rear profile z2(y)
    z2_pts = []
    for y in y_pts:
        if abs(y) <= y_half_ca2:
            z2_pts.append(ct + _calculate_sag(r2, k2, y))
        else:
            # Flat land
            z2_pts.append(ct + _calculate_sag(r2, k2, y_half_ca2))

    z1_arr = np.array(z1_pts)
    z2_arr = np.array(z2_pts)

    # Fill lens cross section
    ax.fill_betweenx(y_pts, z1_arr, z2_arr, color="#D0E1F9", edgecolor="#1E3F66", linewidth=1.8, label="Optical Glass")

    # Centerline (Optical Axis)
    z_min = min(np.min(z1_arr), 0) - 5
    z_max = max(np.max(z2_arr), ct) + 5
    ax.plot([z_min, z_max], [0, 0], color="red", linestyle="-.", linewidth=1.0, label="Optical Axis")

    # Clear Aperture lines
    ax.plot([z1_arr[np.argmin(np.abs(y_pts - y_half_ca1))], z1_arr[np.argmin(np.abs(y_pts - y_half_ca1))]],
            [y_half_ca1, y_half_ca1 + 1], color="gray", linestyle=":")
    ax.plot([z1_arr[np.argmin(np.abs(y_pts + y_half_ca1))], z1_arr[np.argmin(np.abs(y_pts + y_half_ca1))]],
            [-y_half_ca1, -y_half_ca1 - 1], color="gray", linestyle=":")

    # Title & Dimension Labels
    e_idx = spec["element_index"]
    elem_type = spec["element_type"]
    mat = spec["material"]
    r1_str = f"R1 = {r1:.2f} mm" if r1 != 0 else "R1 = Plano"
    r2_str = f"R2 = {r2:.2f} mm" if r2 != 0 else "R2 = Plano"

    ax.set_title(f"ISO 10110 Optical Element Drawing: Element {e_idx} ({mat} {elem_type.upper()})\n"
                 f"OD = Ø{od:.2f} mm | CT = {ct:.3f} mm | CA1 = Ø{ca1:.2f} mm | CA2 = Ø{ca2:.2f} mm",
                 fontsize=12, fontweight="bold", pad=15)

    ax.text(z_min + 1, y_half_od * 0.75, f"Surface 1:\n{r1_str}\nCA = Ø{ca1:.2f}\n{spec['surface_1']['surface_form_iso3']}",
            fontsize=9, bbox=dict(boxstyle="round,pad=0.3", fc="#f0f0f0", ec="black", lw=0.8))

    ax.text(z_max - 8, y_half_od * 0.75, f"Surface 2:\n{r2_str}\nCA = Ø{ca2:.2f}\n{spec['surface_2']['surface_form_iso3']}",
            fontsize=9, bbox=dict(boxstyle="round,pad=0.3", fc="#f0f0f0", ec="black", lw=0.8))

    ax.text(ct / 2.0, -y_half_od * 0.85, f"Material: {mat}\nISO 4/: {spec['surface_1']['centering_iso4']}\nChamfer: {spec['protective_chamfer_mm']}",
            fontsize=9, ha="center", bbox=dict(boxstyle="round,pad=0.3", fc="#fff9e6", ec="#d4a017", lw=0.8))

    ax.set_xlabel("Z (Optical Axis / Thickness mm)", fontsize=10)
    ax.set_ylabel("Y (Aperture / Height mm)", fontsize=10)
    ax.grid(True, linestyle="--", alpha=0.5)

    plt.tight_layout()
    plt.savefig(filepath)
    plt.close(fig)


def zemax_export_prescription_for_cad(
    margin_mm: float = 2.0,
    output_filepath: Optional[str] = None,
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
        output_filepath: JSON export path (default 'output/solidworks_prescription.json').
        barrel_radial_clearance_mm: Radial tolerance clearance between lens OD and barrel bore (default 0.05 mm).
    """
    session = ZOSSession.get_instance()
    sys = session.system

    num_surfs = sys.LDE.NumberOfSurfaces
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
        ca1 = lens_parts[idx]["clear_aperture_mm"]
        ca2 = lens_parts[idx + 1]["clear_aperture_mm"]

        # Ensure spacer has clear aperture and positive wall thickness
        beam_clearance = min(ca1, ca2)
        spacer_id = round(beam_clearance * 0.95, 2)
        spacer_od = round(min(od1, od2), 2)
        if spacer_od < spacer_id + 1.5:
            spacer_od = round(max(od1, od2), 2)
        if spacer_od < spacer_id + 1.5:
            spacer_od = round(spacer_id + 2.0, 2)

        spacer_len = round(air_gap_thickness, 3)

        spacers.append({
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
    front_od = lens_parts[0]["recommended_od_mm"] if lens_parts else 25.4
    retaining_ring = {
        "outer_diameter_mm": front_od,
        "inner_diameter_mm": round(max(front_od - 4.0, 1.0), 2),
        "thickness_mm": 2.5,
        "solidworks_mcp_command": {
            "tool": "create_3d_retaining_ring",
            "arguments": {
                "outer_diameter": front_od,
                "inner_diameter": round(max(front_od - 4.0, 1.0), 2),
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

    total_track = 0.0
    for s_idx in range(1, num_surfs):
        th = float(sys.LDE.GetSurfaceAt(s_idx).Thickness)
        if not math.isinf(th) and abs(th) < 1e9:
            total_track += th

    payload = {
        "status": "success",
        "margin_mm": margin_mm,
        "total_track_mm": round(total_track, 4),
        "elements_count": len(lens_parts),
        "spacers_count": len(spacers),
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

    if not output_filepath:
        default_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
        os.makedirs(default_dir, exist_ok=True)
        output_filepath = os.path.join(default_dir, "solidworks_prescription.json")

    with open(output_filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    payload["saved_json_filepath"] = output_filepath
    return payload
