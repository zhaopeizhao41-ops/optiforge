"""
Zemax OpticStudio MCP Server
Exposes Zemax OpticStudio automation tools, optical design manual rules,
and system inspection resources to Antigravity and other MCP-compliant AI agents.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer
from tools import (
    zemax_system_info as _system_info,
    zemax_new_file as _new_file,
    zemax_load_file as _load_file,
    zemax_save_file as _save_file,
    zemax_get_system_data as _get_system_data,
    zemax_load_template as _load_template,
    zemax_set_aperture as _set_aperture,
    zemax_set_fields as _set_fields,
    zemax_set_wavelengths as _set_wavelengths,
    zemax_set_ray_aiming as _set_ray_aiming,
    zemax_surface_operations as _surface_operations,
    zemax_insert_surface as _insert_surface,
    zemax_delete_surface as _delete_surface,
    zemax_set_solve as _set_solve,
    zemax_setup_merit_function as _setup_merit_function,
    zemax_add_operand as _add_operand,
    zemax_quick_focus as _quick_focus,
    zemax_run_optimization as _run_optimization,
    zemax_run_hammer as _run_hammer,
    zemax_run_spot_diagram as _run_spot_diagram,
    zemax_run_fft_mtf as _run_fft_mtf,
    zemax_run_ray_fan as _run_ray_fan,
    zemax_run_wavefront_map as _run_wavefront_map,
    zemax_run_field_curvature_distortion as _run_field_curvature_distortion,
    zemax_validate_design_rules as _validate_design_rules,
    zemax_lookup_manual as _lookup_manual,
)
from domain.operand_kb import OPERAND_DATABASE
from domain.zemax_rules import OpticalRuleCheck

app = MCPServer("zemax-opticstudio")


# ==============================================================================
# MCP Tools - System Management
# ==============================================================================

@app.tool()
def zemax_system_info() -> str:
    """
    Get current Zemax OpticStudio status, active license, primary system parameters,
    and first-order optical properties (EFL, F/#, EPD, TOTR).
    """
    res = _system_info()
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_new_file(catalogs: Optional[List[str]] = None) -> str:
    """
    Create a clean, new sequential optical design in Zemax OpticStudio.
    Automatically enables standard optical glass catalogs (default: SCHOTT and CDGM).
    """
    res = _new_file(catalogs=catalogs)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_load_file(filepath: str) -> str:
    """
    Load an existing Zemax .zos or .zmx optical design file from disk.
    filepath: Absolute path to the .zos or .zmx file.
    """
    res = _load_file(filepath)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_save_file(filepath: Optional[str] = None) -> str:
    """
    Save the active optical design to disk in .zmx (classic ASCII) or .zos format.
    filepath: Destination path (e.g. 'd:/mcp gemini zemax/output/my_lens.zmx').
    If omitted, automatically saves as .zmx in 'd:/mcp gemini zemax/output/optical_design.zmx'.
    """
    res = _save_file(filepath)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_get_system_data() -> str:
    """
    Retrieve comprehensive data of the current optical system:
    All surface parameters (radius, thickness, material, semi-diameter, conic, comment, solves),
    fields list, wavelengths list, and aperture parameters.
    """
    res = _get_system_data()
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_load_template(template_id: str) -> str:
    """
    Instantiate a classic optical design template into Zemax.
    template_id: 'singlet_bk7', 'achromat_doublet', or 'cooke_triplet'.
    """
    res = _load_template(template_id)
    return json.dumps(res, ensure_ascii=False, indent=2)


# ==============================================================================
# MCP Tools - Optical Setup
# ==============================================================================

@app.tool()
def zemax_set_aperture(aperture_type: str, aperture_value: float) -> str:
    """
    Set system aperture type and value.
    aperture_type: 'EPD' (Entrance Pupil Diameter), 'ImageSpaceFNum' (F-Number), 'ObjectSpaceNA' (Numerical Aperture), 'FloatByStopSize'.
    aperture_value: Aperture value (e.g. 25.0 for 25mm EPD, or 4.0 for F/4).
    """
    res = _set_aperture(aperture_type, aperture_value)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_set_fields(field_type: str, fields: List[Dict[str, float]]) -> str:
    """
    Configure optical fields of view.
    field_type: 'Angle' (degrees), 'ObjectHeight' (mm), 'ParaxialImageHeight' (mm), 'RealImageHeight' (mm).
    fields: List of field dicts, e.g. [{"x": 0.0, "y": 0.0, "weight": 1.0}, {"x": 0.0, "y": 14.0, "weight": 1.0}]
    """
    res = _set_fields(field_type, fields)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_set_wavelengths(wavelengths: List[Dict[str, float]], primary_index: int = 1) -> str:
    """
    Configure wavelengths in micrometers.
    wavelengths: List of dicts, e.g. [{"wavelength_um": 0.58756, "weight": 1.0}, {"wavelength_um": 0.48613, "weight": 1.0}].
    primary_index: 1-based index of primary wavelength (default: 1).
    """
    res = _set_wavelengths(wavelengths, primary_index)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_set_ray_aiming(method: str = "Off") -> str:
    """
    Set Ray Aiming mode.
    method: 'Off', 'Paraxial', or 'Real'.
    Required by Zemax manual when field angle > 20 degrees or optical speed / pupil aberrations are large.
    """
    res = _set_ray_aiming(method)
    return json.dumps(res, ensure_ascii=False, indent=2)


# ==============================================================================
# MCP Tools - Surface & Lens Data Editor (LDE)
# ==============================================================================

@app.tool()
def zemax_surface_operations(
    surface_index: int,
    radius: Optional[float] = None,
    thickness: Optional[float] = None,
    material: Optional[str] = None,
    semi_diameter: Optional[float] = None,
    conic: Optional[float] = None,
    comment: Optional[str] = None,
    is_stop: Optional[bool] = None,
) -> str:
    """
    Modify parameters of an optical surface in the Lens Data Editor (LDE).
    surface_index: 0-based surface index (0 is Object, 1..N are lenses/mirrors, last is Image).
    radius: Curvature radius in mm (positive if center of curvature is towards image).
    thickness: Axial distance to next surface in mm.
    material: Glass name from catalog (e.g. 'N-BK7', 'N-SF11', 'H-K9L') or '' for air.
    semi_diameter: Clear aperture semi-diameter in mm.
    conic: Conic constant (0 for spherical, -1 for paraboloid).
    comment: Surface label/comment.
    is_stop: True to make this surface the optical Stop.
    """
    res = _surface_operations(
        surface_index=surface_index,
        radius=radius,
        thickness=thickness,
        material=material,
        semi_diameter=semi_diameter,
        conic=conic,
        comment=comment,
        is_stop=is_stop,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_insert_surface(surface_index: int) -> str:
    """
    Insert a new blank surface at the specified index in the LDE.
    surface_index: Target index (1 to N).
    """
    res = _insert_surface(surface_index)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_delete_surface(surface_index: int) -> str:
    """
    Remove an existing surface from the LDE.
    surface_index: Surface index to remove (cannot delete Object 0 or Image surface).
    """
    res = _delete_surface(surface_index)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_set_solve(
    surface_index: int,
    cell: str,
    solve_type: str,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Configure a solve on a surface cell.
    surface_index: Surface index.
    cell: 'radius', 'thickness', or 'semidiameter'.
    solve_type: 'variable' (for optimization), 'fixed', 'fnumber', 'pickup', 'marginal_ray_angle', 'marginal_ray_height', 'edgethickness'.
    params: Optional dict of solve parameters (e.g. {"f_number": 5.0} or {"source_surface": 1, "scale": 1.0}).
    """
    res = _set_solve(surface_index, cell, solve_type, params)
    return json.dumps(res, ensure_ascii=False, indent=2)


# ==============================================================================
# MCP Tools - Optimization & Merit Function
# ==============================================================================

@app.tool()
def zemax_setup_merit_function(
    criterion: str = "RMS_Spot",
    reference: str = "Centroid",
    rings: int = 4,
    arms: int = 6,
    min_air_center: float = 0.5,
    max_air_center: float = 100.0,
    min_air_edge: float = 0.5,
    min_glass_center: float = 2.0,
    max_glass_center: float = 25.0,
    min_glass_edge: float = 1.0,
    target_efl: Optional[float] = None,
    efl_weight: float = 10.0,
    max_totr: Optional[float] = None,
    totr_weight: float = 1.0,
) -> str:
    """
    Build a standard Merit Function according to the Zemax OpticStudio User Manual.
    Automatically configures:
    - Gaussian Quadrature pupil integration (rings, arms)
    - Fabrication boundary constraints (MNCA/MXCA/MNEA for air, MNCG/MXCG/MNEG for glass)
    - Optional first-order focal length (EFFL) and total track (TOTR) targets.
    criterion: 'RMS_Spot' (for geometric aberrations) or 'RMS_Wavefront' (near diffraction limit).
    reference: 'Centroid' or 'ChiefRay'.
    """
    res = _setup_merit_function(
        criterion=criterion,
        reference=reference,
        rings=rings,
        arms=arms,
        min_air_center=min_air_center,
        max_air_center=max_air_center,
        min_air_edge=min_air_edge,
        min_glass_center=min_glass_center,
        max_glass_center=max_glass_center,
        min_glass_edge=min_glass_edge,
        target_efl=target_efl,
        efl_weight=efl_weight,
        max_totr=max_totr,
        totr_weight=totr_weight,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_add_operand(
    type_code: str,
    target: float,
    weight: float,
    param1: int = 0,
    param2: int = 0,
    param3: int = 0,
    param4: int = 0,
    position: Optional[int] = None,
) -> str:
    """
    Insert a custom optimization operand into the Merit Function Editor (MFE).
    type_code: Zemax operand code (e.g. 'EFFL', 'TOTR', 'MNCA', 'MNCG', 'SPHA', 'COMA', 'ASTI', 'MTFT').
    target: Target numerical value.
    weight: Optimization penalty weight.
    """
    res = _add_operand(
        type_code=type_code,
        target=target,
        weight=weight,
        param1=param1,
        param2=param2,
        param3=param3,
        param4=param4,
        position=position,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_quick_focus(
    criterion: str = "SpotSizeRadial",
    use_centroid: bool = True,
) -> str:
    """
    Run Quick Focus tool on the current optical system to adjust the back focal distance (thickness of last lens/air surface).
    criterion: 'SpotSizeRadial' or 'RMSWavefront'.
    """
    res = _quick_focus(criterion, use_centroid)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_run_optimization(
    algorithm: str = "DLS",
    cycles: str = "Automatic",
    cores: int = 8,
) -> str:
    """
    Run Local Optimization on the current optical system.
    algorithm: 'DLS' (Damped Least Squares) or 'OD' (Orthogonal Descent).
    cycles: 'Automatic', '1', '5', '10', '50'.
    """
    res = _run_optimization(algorithm=algorithm, cycles=cycles, cores=cores)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_run_hammer(timeout_seconds: int = 10) -> str:
    """
    Run Hammer Optimization to search broader parameter space for alternative local minima.
    timeout_seconds: Duration before terminating Hammer search (default: 10s).
    """
    res = _run_hammer(timeout_seconds=timeout_seconds)
    return json.dumps(res, ensure_ascii=False, indent=2)


# ==============================================================================
# MCP Tools - Optical Performance Analysis
# ==============================================================================

@app.tool()
def zemax_run_spot_diagram(field_index: Optional[int] = None) -> str:
    """
    Run Standard Spot Diagram analysis across fields and wavelengths.
    Returns RMS spot radius, GEO maximum radius, Airy disk radius, and diffraction limit diagnosis.
    field_index: Optional 1-based field index (if omitted, reports all fields).
    """
    res = _run_spot_diagram(field_index=field_index)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_run_fft_mtf(
    max_frequency: float = 100.0,
    sample_size: str = "256x256",
) -> str:
    """
    Run Fast Fourier Transform Modulation Transfer Function (FFT MTF) analysis.
    Returns spatial frequency response and tangential/sagittal MTF values.
    max_frequency: Maximum spatial frequency in cycles/mm.
    sample_size: Pupil sampling density ('128x128', '256x256', '512x512').
    """
    res = _run_fft_mtf(max_frequency=max_frequency, sample_size=sample_size)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_run_ray_fan(field_index: int = 1) -> str:
    """
    Run Ray Fan analysis (transverse ray aberrations Ey vs Py and Ex vs Px).
    Examines spherical aberration, coma, and astigmatism signatures.
    """
    res = _run_ray_fan(field_index=field_index)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_run_wavefront_map(field_index: int = 1) -> str:
    """
    Run Wavefront Map analysis to evaluate optical path difference (OPD).
    Returns Peak-to-Valley (PV) error, RMS wavefront error (in waves), and estimated Strehl ratio.
    """
    res = _run_wavefront_map(field_index=field_index)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_run_field_curvature_distortion() -> str:
    """
    Run Field Curvature and Distortion analysis.
    Evaluates tangential/sagittal focal shifts and percentage distortion across the field.
    """
    res = _run_field_curvature_distortion()
    return json.dumps(res, ensure_ascii=False, indent=2)


# ==============================================================================
# MCP Tools - Design Rules Validation & Manual Knowledge
# ==============================================================================

@app.tool()
def zemax_validate_design_rules() -> str:
    """
    Perform a complete optical manufacturing and design integrity audit on the current system
    based on the criteria set forth in the Zemax OpticStudio User Manual.
    
    Checks include:
    - Knife-edge / negative edge thickness (lens geometry self-intersection)
    - Minimum glass center thickness (polishing fracture risk)
    - Minimum glass edge thickness (mounting & beveling feasibility)
    - Minimum center & edge air clearance (element collision risk)
    - Element aspect ratio (rigidity check)
    - High-field / large NA Ray Aiming requirement
    """
    res = _validate_design_rules()
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_lookup_manual(query: str) -> str:
    """
    Search the embedded Zemax OpticStudio Manual Knowledge Base for optimization operands,
    design rules, coordinate conventions, and aberration control principles.
    query: Keyword, operand code (e.g. 'EFFL', 'MNCA', 'SPHA', 'MTFT', 'Air', 'Glass').
    """
    res = _lookup_manual(query)
    return json.dumps(res, ensure_ascii=False, indent=2)


# ==============================================================================
# MCP Resources
# ==============================================================================

@app.resource("zemax://system/current")
def get_current_system_resource() -> str:
    """Live snapshot of the current Zemax optical design configuration."""
    return json.dumps(_get_system_data(), ensure_ascii=False, indent=2)


@app.resource("zemax://rules/summary")
def get_optical_rules_resource() -> str:
    """Zemax Application Manual optical manufacturing standards and rule thresholds."""
    rules = {
        "glass_center_thickness": ">= 1.0 mm (prevent distortion and cracking during polishing)",
        "glass_edge_thickness": ">= 1.0 mm (prevent knife-edge chipping and permit beveling)",
        "air_center_thickness": ">= 0.1 mm (thermal expansion clearance)",
        "air_edge_thickness": ">= 0.5 mm (prevent surface collision at rim)",
        "aspect_ratio": "CT / Diameter >= 0.08 ~ 0.10 for thin lenses",
        "ray_aiming": "Required if semi-field angle >= 20 deg or entrance pupil is strongly aberrated",
        "diffraction_limit_criterion": "If RMS spot radius <= Airy disk radius (1.22 * lambda * F/#), switch optimization to RMS Wavefront Error",
        "strehl_ratio": ">= 0.80 satisfies Marechal diffraction-limited criterion",
    }
    return json.dumps(rules, ensure_ascii=False, indent=2)


@app.resource("zemax://operands/catalog")
def get_operands_catalog_resource() -> str:
    """Complete catalog of supported Zemax optimization operands."""
    return json.dumps(OPERAND_DATABASE, ensure_ascii=False, indent=2)


# ==============================================================================
# Server Entry Point
# ==============================================================================

def main():
    import sys
    # Configure stdout for utf-8 stdio communication
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
