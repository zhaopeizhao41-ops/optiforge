"""
Zemax System Management Tools
Handles system initialization, file I/O, templates, and high-level system inspection.
"""

import os
from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession
from domain.design_templates import get_template, list_templates
from tools.project_manager import (
    get_output_base_dir,
    get_active_project_name,
    get_active_project_info,
    set_active_project,
    get_project_dir,
    resolve_project_file_path,
    list_projects,
    sanitize_project_name,
)


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


def zemax_set_project(
    project_name: str,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create or switch to a dedicated project workspace directory under 'output/<project_name>/'.
    All subsequent design saves, CAD models, ISO 10110 drawings, and optomechanical exports
    will be neatly isolated inside this project folder.
    """
    return set_active_project(project_name, description=description)


def zemax_get_project() -> Dict[str, Any]:
    """
    Get the currently active project workspace information, root path, and subdirectories.
    """
    return get_active_project_info()


def zemax_list_projects() -> Dict[str, Any]:
    """
    List all optical design projects currently stored under the 'output/' directory.
    """
    projs = list_projects()
    return {
        "status": "success",
        "total_projects": len(projs),
        "active_project": get_active_project_name(),
        "projects": projs,
    }


def zemax_load_file(filepath: str) -> Dict[str, Any]:
    """Load a Zemax .zos or .zmx optical design file."""
    session = ZOSSession.get_instance()
    abs_path = os.path.abspath(filepath)
    if not os.path.exists(abs_path):
        return {"status": "error", "message": f"File does not exist: {abs_path}"}
    session.load_file(abs_path, save_changes=False)

    # Automatically synchronize active project context
    norm_path = os.path.normpath(abs_path)
    output_dir = os.path.normpath(get_output_base_dir())
    if norm_path.startswith(output_dir):
        rel = os.path.relpath(norm_path, output_dir)
        parts = rel.split(os.sep)
        if len(parts) >= 2 and not parts[0].startswith("."):
            set_active_project(parts[0])
        else:
            stem = os.path.splitext(os.path.basename(abs_path))[0]
            set_active_project(stem)
    else:
        stem = os.path.splitext(os.path.basename(abs_path))[0]
        set_active_project(stem)

    return {
        "status": "success",
        "message": f"Successfully loaded design: {abs_path}",
        "surfaces_count": session.system.LDE.NumberOfSurfaces,
        "active_project": get_active_project_name(),
    }


def zemax_save_file(
    filepath: Optional[str] = None,
    project_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Save the active optical design to disk.
    If project_name is provided, ensures the project directory is initialized and active.
    If filepath is omitted, automatically saves as .zmx in 'output/<project_name>/<project_name>.zmx'.
    Supports both .zmx (classic ASCII format) and .zos (OpticStudio modern format).
    """
    session = ZOSSession.get_instance()
    try:
        if project_name:
            set_active_project(project_name)
        active_proj = get_active_project_name()

        target_path = filepath
        if not target_path:
            if (
                session.current_filepath
                and os.path.isabs(session.current_filepath)
                and active_proj in session.current_filepath
            ):
                target_path = session.current_filepath
            else:
                target_path = resolve_project_file_path(
                    None, default_filename=f"{active_proj}.zmx", project_name=active_proj
                )
        elif not os.path.isabs(target_path):
            target_path = resolve_project_file_path(
                target_path, default_filename=f"{active_proj}.zmx", project_name=active_proj
            )

        # If extension omitted, default to .zmx
        _, ext = os.path.splitext(target_path)
        if not ext:
            target_path = target_path + ".zmx"

        session.save_file(target_path)
        return {
            "status": "success",
            "project_name": active_proj,
            "project_directory": get_project_dir(active_proj),
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


# ==============================================================================
# Optical Design Proposal & Confirmation Gate
# ==============================================================================

CURRENT_DESIGN_PROPOSAL: Dict[str, Any] = {}


def zemax_register_design_proposal(
    project_name: str,
    target_specs: Dict[str, Any],
    initial_structure_source: str,
    optical_theory_analysis: str,
    glass_selection_rationale: str,
    merit_function_strategy: str,
    mechanical_constraints: Optional[str] = None,
    internal_air_spacing_budget: Optional[str] = None,
    user_confirmed_to_simulate: bool = False,
) -> Dict[str, Any]:
    """
    Register and format a comprehensive optical design proposal before Zemax simulation.
    Enforces the mandatory SOP:
    1. Web/patent search of initial structures
    2. Deep optical thinking & Seidel aberration budget
    3. Structured proposal formulation (including strict internal air spacing & compactness budget)
    4. Explicit user decision gate before simulation
    """
    global CURRENT_DESIGN_PROPOSAL

    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    proposal_doc = f"""# 《光学设计与仿真提案报告》: {project_name}
**登记时间**: {now_str}
**仿真授权状态**: {"✅ 已获用户授权确认，可启动仿真" if user_confirmed_to_simulate else "⏳ 待用户审阅确认 (GATE ACTIVE)"}

---

### 一、 核心设计指标规格
"""
    for k, v in target_specs.items():
        proposal_doc += f"- **{k}**: `{v}`\n"

    proposal_doc += f"""
---

### 二、 专利与文献初始结构选型依据 (Web Search Benchmark)
{initial_structure_source}

---

### 三、 深度光学推演思考与像差平衡 (Deep Optical Theory & Aberration Budget)
{optical_theory_analysis}

---

### 四、 玻璃选型与色差校正论证 (Glass Selection & Achromatization)
{glass_selection_rationale}

---

### 五、 评价函数控制与优化策略 (Merit Function & Optimization Strategy)
{merit_function_strategy}

---

### 六、 镜片空气间隔与紧凑装配预算 (Internal Air Spacing & Compactness Budget)
{internal_air_spacing_budget or "内部空气间隙严格限定：MXCA <= 12.0 mm (杜绝跑偏拉长)；镜组核心叠层长度 TTHI <= 0.35 * EFL；镜筒长径比 L/D <= 2.5。"}
"""

    if mechanical_constraints:
        proposal_doc += f"""
---

### 七、 机械外形与加工边界约束 (Mechanical & Fabrication Constraints)
{mechanical_constraints}
"""

    # Automatically initialize dedicated project workspace
    proj_info = set_active_project(
        project_name,
        description=f"Optical design proposal for {project_name}",
        target_specs=target_specs,
    )
    p_dir = proj_info.get("project_directory", get_project_dir(project_name))
    proposal_report_path = os.path.join(p_dir, "reports", "design_proposal.md")
    try:
        with open(proposal_report_path, "w", encoding="utf-8") as f:
            f.write(proposal_doc)
    except Exception:
        pass

    CURRENT_DESIGN_PROPOSAL = {
        "project_name": project_name,
        "timestamp": now_str,
        "project_directory": p_dir,
        "proposal_report_file": proposal_report_path,
        "target_specs": target_specs,
        "initial_structure_source": initial_structure_source,
        "optical_theory_analysis": optical_theory_analysis,
        "glass_selection_rationale": glass_selection_rationale,
        "merit_function_strategy": merit_function_strategy,
        "internal_air_spacing_budget": internal_air_spacing_budget or "",
        "mechanical_constraints": mechanical_constraints or "",
        "user_confirmed_to_simulate": user_confirmed_to_simulate,
        "formatted_proposal": proposal_doc,
    }

    if not user_confirmed_to_simulate:
        next_step = (
            "【门禁拦截】提案已成功登记。请先将上述方案呈报给用户审阅，"
            "并明确询问：'以上为根据文献检索与深度光学推演制定的初始方案，请审阅是否同意启动 Zemax 仿真与自动优化？'。"
            "在收到用户明确确认（'同意' / '开始仿真' / 'proceed'）前，严禁调用仿真建模工具。"
        )
    else:
        next_step = (
            "【门禁放行】用户已确认授权仿真。可以调用 zemax_new_file、zemax_surface_operations、"
            "zemax_setup_merit_function 及 zemax_run_optimization 开始实际建模与优化。"
        )

    return {
        "status": "success",
        "project_name": project_name,
        "project_directory": p_dir,
        "proposal_report_file": proposal_report_path,
        "simulation_authorized": user_confirmed_to_simulate,
        "next_action": next_step,
        "formatted_proposal": proposal_doc,
    }


def get_current_design_proposal() -> Dict[str, Any]:
    """Retrieve the currently registered design proposal in this session."""
    return CURRENT_DESIGN_PROPOSAL or {"status": "none", "message": "No design proposal registered yet."}


# ==============================================================================
# Optical Requirements Specification (ORS) Audit & Clarification Engine
# ==============================================================================

SYSTEM_SPEC_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "imaging_lens": {
        "name": "成像镜头 (摄影/机器视觉/安防定焦镜头)",
        "mandatory": [
            {
                "key": "efl_mm",
                "aliases": ["efl", "focal_length", "f", "焦距", "有效焦距"],
                "name": "系统有效焦距 (EFL)",
                "unit": "mm",
                "desc": "确定成像系统的基础视场角与放大比例",
                "default": "50.0 mm (工业标准镜头基准)",
                "question": "请问期望的目标有效焦距（EFL）是多少毫米？（如标准 25mm, 35mm, 50mm, 75mm）",
            },
            {
                "key": "f_number",
                "aliases": ["fno", "f_num", "f/#", "f_number", "光圈", "孔径", "f数", "相对孔径"],
                "name": "工作光圈数 (F/#) 或 通光口径 (EPD)",
                "unit": "无量纲",
                "desc": "决定集光能量、景深与衍射极限艾里斑尺寸",
                "default": "2.8 (通用工业与摄影基准)",
                "question": "请问设计的光圈大小（F-number）是多少？（如高速大光圈 F/1.4~F/2.0，或常规工业 F/2.8~F/4.0）",
            },
            {
                "key": "fov_or_sensor",
                "aliases": ["fov", "field_of_view", "2w", "image_circle", "sensor", "sensor_format", "视场", "像面", "传感器靶面"],
                "name": "最大视场角 (FOV 2ω) 或 传感器像面直径",
                "unit": "度 / mm",
                "desc": "决定物方覆盖范围或像方对角线像面尺寸",
                "default": "像面直径 11.0 mm (对应 2/3\" 工业传感器靶面，2w ≈ 25°)",
                "question": "请问搭配的图像传感器靶面大小（如 1/2\", 2/3\", 1\", 全画幅）或目标全视场角（FOV 2ω）是多少？",
            },
            {
                "key": "wavelength_range",
                "aliases": ["wave", "wavelength", "spectrum", "波段", "波长", "光谱"],
                "name": "工作光谱范围",
                "unit": "nm",
                "desc": "决定消色差谱段（可见光消初级色差，复消色差消二级光谱）",
                "default": "486.1 nm ~ 656.3 nm (可见光 F-d-C 谱段，主波长 587.6 nm)",
                "question": "请问工作光谱范围是什么？（如标准可见光 400~700nm，近红外 850nm，还是双波段？）",
            },
        ],
        "recommended": [
            {
                "key": "pixel_pitch_um",
                "aliases": ["pixel", "pixel_pitch", "pixel_size", "像元大小", "像元尺寸"],
                "name": "传感器像元大小 (Pixel Pitch)",
                "unit": "μm",
                "desc": "用于精确计算 Nyquist 截止空间频率 f_N = 1000/(2*p) lp/mm 及 MTF 考核指标",
                "default": "3.45 μm (典型工业 CMOS，对应截止频率 145 lp/mm，要求 MTF@145 > 0.2)",
                "question": "传感器的像元尺寸是多少微米（μm）？（若已知像元尺寸，可精准设定截止频率与 MTF 考核指标）",
            },
            {
                "key": "max_total_track_mm",
                "aliases": ["totr", "total_track", "lens_length", "总长", "光学总长"],
                "name": "最大光学总长限制 (TOTR)",
                "unit": "mm",
                "desc": "第一面玻璃顶点至图像传感器像平面的最大距离",
                "default": "<= 1.5 ~ 2.0 * EFL",
                "question": "是否有镜头最大光学总长（TOTR）或机械外径限制？",
            },
            {
                "key": "min_back_focal_length_mm",
                "aliases": ["bfl", "back_focus", "back_focal_length", "后截距", "后工作距"],
                "name": "最小后截距 (BFL)",
                "unit": "mm",
                "desc": "最后一面玻璃至传感器的空气净距，预留机械法兰及滤光片空间",
                "default": ">= 12.0 mm (C 接口法兰距为 17.526 mm)",
                "question": "最小后工作距离（BFL）或机械接口类型是什么？（如 C-Mount / CS-Mount 等）",
            },
            {
                "key": "operating_temperature_c",
                "aliases": ["temp", "temperature", "athermal", "温度", "工作温度", "无热化"],
                "name": "工作温度范围",
                "unit": "°C",
                "desc": "判断是否需要执行宽温区消热差无热化设计",
                "default": "常温 20°C ± 5°C (无需特殊无热化配对)",
                "question": "该系统是在室内常温工作（20°C），还是需要宽温区无热化（如车载/户外 -40°C ~ +85°C）？",
            },
        ],
    },
    "scan_lens": {
        "name": "激光扫描透镜 / F-Theta 透镜",
        "mandatory": [
            {
                "key": "efl_mm",
                "aliases": ["efl", "focal_length", "f", "焦距"],
                "name": "扫描有效焦距 (EFL)",
                "unit": "mm",
                "desc": "决定扫描平面扫描幅面 2y = 2*f*theta",
                "default": "90.0 mm ~ 100.0 mm",
                "question": "请问期望的扫描透镜焦距（EFL）或扫描平面幅面尺寸是多少？（如 50x50 mm, 100x100 mm）",
            },
            {
                "key": "scan_angle_deg",
                "aliases": ["scan_angle", "theta", "angle", "扫描角", "摆角", "偏转角"],
                "name": "振镜最大光学扫描角 (θ)",
                "unit": "度",
                "desc": "振镜最大出射光束光学半角或全角",
                "default": "± 15.0° (光学半角 15°，对应机械摆角 ±7.5°)",
                "question": "请问激光振镜的最大光学扫描角度是多少？（如常用 ±10°, ±15°, ±20°）",
            },
            {
                "key": "beam_diameter_mm",
                "aliases": ["beam_dia", "aperture", "epd", "waist", "光束直径", "入瞳直径", "振镜通光孔径"],
                "name": "入射激光束直径 / 振镜通光口径 (EPD)",
                "unit": "mm",
                "desc": "入射高斯光束 1/e^2 直径或振镜光阑孔径",
                "default": "3.60 mm ~ 5.0 mm",
                "question": "请问入射激光光束直径（1/e^2 直径）或振镜反射镜有效通光孔径是多少？",
            },
            {
                "key": "wavelength_nm",
                "aliases": ["wave", "wavelength", "波长", "激光波长"],
                "name": "激光工作波长",
                "unit": "nm",
                "desc": "激光中心波长",
                "default": "1064 nm (光纤激光) / 532 nm (绿光) / 830 nm (近红外)",
                "question": "请问激光器的工作波长是多少？（如 1064nm 光纤激光、532nm 绿光、355nm 紫外、830nm 近红外）",
            },
        ],
        "recommended": [
            {
                "key": "telecentricity_required",
                "aliases": ["telecentric", "cra", "远心", "远心度"],
                "name": "像方远心度要求 (Telecentricity)",
                "unit": "度",
                "desc": "边缘光束在工作平面上的主光线垂直度，深雕/打标需严格远心",
                "default": "远心设计 (主光线入射角 CRA <= 1.0°)",
                "question": "该 F-Theta 扫描透镜是否需要像方远心？（即要求主光线垂直入射加工平面 CRA ≤ 1.0°？）",
            },
            {
                "key": "working_distance_mm",
                "aliases": ["wd", "working_distance", "工作距离", "物距"],
                "name": "工作距离 (WD)",
                "unit": "mm",
                "desc": "最后一面玻璃至扫描工作平面的净空间隔",
                "default": ">= 60.0 mm",
                "question": "工作距离（从最后一面透镜到加工平面的净距 WD）是否有明确要求？",
            },
            {
                "key": "galvo_distance_mm",
                "aliases": ["galvo_dist", "stop_dist", "振镜间距", "入瞳距离"],
                "name": "振镜反射面至镜头第一面距离 (Galvo-to-Lens Distance)",
                "unit": "mm",
                "desc": "振镜转轴中点到扫描镜头前表面的机械安装净距",
                "default": "20.0 ~ 30.0 mm",
                "question": "振镜反射镜到扫描透镜前端面的机械预留安装间距是多少？（通常为 20~35 mm）",
            },
        ],
    },
    "microscope_objective": {
        "name": "显微物镜 (有限远 / 无限远共轭)",
        "mandatory": [
            {
                "key": "magnification",
                "aliases": ["mag", "magnification", "放大倍率", "倍率", "倍数"],
                "name": "标称放大倍率 (Magnification)",
                "unit": "X",
                "desc": "显微系统的基本放大倍数",
                "default": "20X",
                "question": "请问显微物镜的目标放大倍率是多少？（如 4X, 10X, 20X, 40X, 60X, 100X）",
            },
            {
                "key": "numerical_aperture_na",
                "aliases": ["na", "aperture", "数值孔径"],
                "name": "物方数值孔径 (NA)",
                "unit": "无量纲",
                "desc": "决定空间分辨率 delta = 0.61*lambda/NA 与景深",
                "default": "0.50 (对于 20X)",
                "question": "请问目标数值孔径（NA）是多少？（如 10X NA=0.25~0.30, 20X NA=0.45~0.50, 40X NA=0.65~0.75）",
            },
            {
                "key": "immersion_medium",
                "aliases": ["medium", "immersion", "water", "oil", "介质", "浸没", "水浸", "油浸"],
                "name": "物方工作浸没介质",
                "unit": "折射率 n",
                "desc": "空气(1.0)、水(1.333)、硅油(1.40)或香柏油(1.515)",
                "default": "空气 Dry (n=1.0)",
                "question": "物方工作介质是空气（Dry，n=1.0）、水浸（Water，n=1.333）还是油浸（Oil，n=1.515）？",
            },
            {
                "key": "wavelength_range",
                "aliases": ["wave", "wavelength", "波长", "光谱"],
                "name": "工作光谱范围",
                "unit": "nm",
                "desc": "荧光/共聚焦波长或明场宽带",
                "default": "400 nm ~ 700 nm (标准可见光消色差)",
                "question": "工作波段范围是什么？（如标准可见光 405~650nm，双光子近红外 800~1064nm？）",
            },
        ],
        "recommended": [
            {
                "key": "working_distance_mm",
                "aliases": ["wd", "working_distance", "工作距离"],
                "name": "物方工作距离 (WD)",
                "unit": "mm",
                "desc": "物镜前表面到样品焦平面的自由净距",
                "default": "长工作距离 >= 2.0 ~ 5.0 mm",
                "question": "物方工作距离（WD）期望是多少？（如超长工作距 SLWD >= 10mm，常规 WD 1~3mm）",
            },
            {
                "key": "coverslip_thickness_mm",
                "aliases": ["coverslip", "cover_glass", "盖玻片", "盖片"],
                "name": "盖玻片厚度与材料",
                "unit": "mm",
                "desc": "标准 0.17 mm 盖玻片（D263M 玻璃），NA>0.4 时必须纳入系统校正球差",
                "default": "0.17 mm (标准 #1.5 盖玻片)",
                "question": "是否有盖玻片校正要求？（如标准 0.17mm 盖玻片，或无盖玻片 0.0mm 培养皿直接观测？）",
            },
            {
                "key": "tube_lens_focal_length_mm",
                "aliases": ["tube_lens", "tube_efl", "筒镜焦距"],
                "name": "搭配的配套筒镜焦距 (Tube Lens EFL)",
                "unit": "mm",
                "desc": "无限远校正物镜必须搭配指定筒镜（Olympus/Nikon 200mm, Zeiss 165mm, Leica 200mm）",
                "default": "200.0 mm (Olympus / Nikon / Mitutoyo 国际标准)",
                "question": "配套的显微筒镜焦距是多少？（标准通常为 200 mm）",
            },
        ],
    },
    "tube_lens": {
        "name": "显微筒镜 / 4f 中继成像镜",
        "mandatory": [
            {
                "key": "efl_mm",
                "aliases": ["efl", "focal_length", "焦距"],
                "name": "筒镜有效焦距 (EFL)",
                "unit": "mm",
                "desc": "确定显微物镜的横向放大率 M = f_tube / f_obj",
                "default": "200.0 mm",
                "question": "筒镜的有效焦距是多少？（行业标准通常为 200mm，蔡司标准为 165mm）",
            },
            {
                "key": "clear_aperture_mm",
                "aliases": ["aperture", "epd", "dia", "口径", "通光口径"],
                "name": "有效通光口径 (CA)",
                "unit": "mm",
                "desc": "需匹配物镜出瞳在远距传播后的光束口径，防止边缘切光渐晕",
                "default": ">= 25.0 ~ 30.0 mm",
                "question": "筒镜所需的有效通光口径是多少？（通常要求 25~35 mm 以匹配物镜出瞳）",
            },
            {
                "key": "field_number_fn_mm",
                "aliases": ["field_number", "fn", "image_circle", "视场数", "像面大小"],
                "name": "视场数 / 传感器成像圈直径 (Field Number FN)",
                "unit": "mm",
                "desc": "像方平场校正直径（FN22 对应 22mm 直径）",
                "default": "22.0 mm (FN 22 标准)",
                "question": "期望的成像视场数（FN）或传感器对角线尺寸是多少？（如通用 FN22 或宽视场 FN25）",
            },
        ],
        "recommended": [
            {
                "key": "pupil_distance_mm",
                "aliases": ["pupil_dist", "obj_dist", "入瞳距离", "物镜距离"],
                "name": "物镜出瞳至筒镜前端面距离",
                "unit": "mm",
                "desc": "无限远光路中插入滤色块、分束镜的自由空间距离",
                "default": "50.0 ~ 120.0 mm",
                "question": "物镜安装面（出瞳）到筒镜的安装距离预留多少？（通常为 50~150 mm）",
            },
        ],
    },
}


def zemax_audit_requirements(
    system_type: str = "imaging_lens",
    specs: Optional[Dict[str, Any]] = None,
    user_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Step 0: Specification Completeness Audit & Interactive Clarification Engine.
    Examines input optical requirements against standard industry Optical Requirements
    Specifications (ORS) matrices, detects missing core/recommended parameters, and
    generates tailored clarification questions with sensible default recommendations.

    Args:
        system_type: Type of optical system ('imaging_lens', 'scan_lens', 'microscope_objective', 'tube_lens').
        specs: Dictionary of currently extracted/known parameters from the user.
        user_prompt: Optional raw user prompt string to aid keyword recognition.

    Returns:
        Structured audit report detailing recognized specs, missing mandatory specs,
        missing recommended specs, interactive clarification questions, and readiness status.
    """
    # Normalize system type
    type_key = system_type.lower().strip()
    if type_key not in SYSTEM_SPEC_TEMPLATES:
        # Fallback to closest or default imaging lens
        if "scan" in type_key or "f-theta" in type_key or "ftheta" in type_key:
            type_key = "scan_lens"
        elif "objective" in type_key or "micro" in type_key or "物镜" in type_key:
            type_key = "microscope_objective"
        elif "tube" in type_key or "筒镜" in type_key or "relay" in type_key:
            type_key = "tube_lens"
        else:
            type_key = "imaging_lens"

    tmpl = SYSTEM_SPEC_TEMPLATES[type_key]
    user_specs = dict(specs or {})

    # If raw user_prompt is provided, do a basic keyword scan to extract potential specs
    if user_prompt:
        import re
        prompt_lower = user_prompt.lower()
        # Scan for EFL e.g. "50mm", "f=50"
        efl_m = re.search(r'(?:f\s*=\s*|efl\s*[:=]?\s*|焦距[:=]?\s*)(\d+(?:\.\d+)?)\s*mm', prompt_lower)
        if efl_m and "efl_mm" not in user_specs and "efl" not in user_specs:
            user_specs["efl_mm"] = float(efl_m.group(1))

        # Scan for F/# e.g. "f/2.8", "f2.8", "f/1.4"
        fno_m = re.search(r'f\s*/\s*(\d+(?:\.\d+)?)', prompt_lower)
        if fno_m and "f_number" not in user_specs and "fno" not in user_specs:
            user_specs["f_number"] = float(fno_m.group(1))

        # Scan for wavelength e.g. "830nm", "1064nm", "532nm"
        wave_m = re.search(r'(\d{3,4})\s*nm', prompt_lower)
        if wave_m and "wavelength" not in user_specs and "wavelength_range" not in user_specs:
            user_specs["wavelength_range"] = f"{wave_m.group(1)} nm"

    recognized_specs: Dict[str, Any] = {}
    missing_mandatory: List[Dict[str, Any]] = []
    missing_recommended: List[Dict[str, Any]] = []
    interactive_questions: List[str] = []

    # 1. Audit mandatory parameters
    for item in tmpl["mandatory"]:
        val = None
        for alias in item["aliases"] + [item["key"]]:
            if alias in user_specs:
                val = user_specs[alias]
                break
        if val is not None and str(val).strip() != "":
            recognized_specs[item["key"]] = val
        else:
            missing_mandatory.append({
                "key": item["key"],
                "name": item["name"],
                "unit": item["unit"],
                "description": item["desc"],
                "recommended_default": item["default"],
                "question": item["question"],
            })
            interactive_questions.append(
                f"- **{item['name']}**: {item['question']}\n  *(行业推荐默认值: `{item['default']}`)*"
            )

    # 2. Audit recommended parameters
    for item in tmpl["recommended"]:
        val = None
        for alias in item["aliases"] + [item["key"]]:
            if alias in user_specs:
                val = user_specs[alias]
                break
        if val is not None and str(val).strip() != "":
            recognized_specs[item["key"]] = val
        else:
            missing_recommended.append({
                "key": item["key"],
                "name": item["name"],
                "unit": item["unit"],
                "description": item["desc"],
                "recommended_default": item["default"],
                "question": item["question"],
            })

    is_complete = (len(missing_mandatory) == 0)
    status = "READY_FOR_DESIGN" if is_complete else "NEEDS_CLARIFICATION"

    # Build Markdown Report
    report = f"### 《光学需求规格书 (ORS) 完备性审查报告》: {tmpl['name']}\n\n"
    report += f"- **系统类别**: `{tmpl['name']}`\n"
    report += f"- **审查判定状态**: `{'✅ 核心参数齐备 (READY_FOR_DESIGN)' if is_complete else '⚠️ 存在核心关键参数缺失 (NEEDS_CLARIFICATION)'}`\n\n"

    report += "#### 1. 当前已识别参数:\n"
    if recognized_specs:
        for k, v in recognized_specs.items():
            report += f"- **{k}**: `{v}`\n"
    else:
        report += "- *(暂未识别到确定的光学参数)*\n"

    if missing_mandatory:
        report += "\n#### 2. 必须向用户追问确认的核心缺失参数 (Mandatory):\n"
        for q in interactive_questions:
            report += f"{q}\n"

    if missing_recommended:
        report += "\n#### 3. 建议进一步明确的工程/探测器参数 (Recommended):\n"
        for item in missing_recommended:
            report += f"- **{item['name']}**: {item['question']} *(若未指定，设计将默认采用 `{item['recommended_default']}`)*\n"

    if not is_complete:
        next_action = (
            "【前置门禁拦截】核心参数缺失！智能体必须停下来，将上述'必须向用户追问确认的核心缺失参数'以清晰、专业的格式向用户提问，"
            "并提供推荐选项。在用户补充或确认参数前，严禁盲目进入 Step 1 联网检索或 Step 2 理论推演！"
        )
    else:
        next_action = (
            "【参数齐备放行】核心一阶与光谱参数已满足设计输入要求！可以启动 Step 1 联网检索成熟初始结构与 Step 2 深度光学推演。"
        )

    return {
        "status": status,
        "is_complete": is_complete,
        "system_type": type_key,
        "system_name": tmpl["name"],
        "recognized_specs": recognized_specs,
        "missing_mandatory": missing_mandatory,
        "missing_recommended": missing_recommended,
        "interactive_questions": interactive_questions,
        "next_action": next_action,
        "audit_report": report,
    }

