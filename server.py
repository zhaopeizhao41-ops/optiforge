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
    zemax_register_design_proposal as _register_design_proposal,
    get_current_design_proposal as _get_current_design_proposal,
    zemax_audit_requirements as _audit_requirements,
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

MANDATORY_WORKFLOW_INSTRUCTIONS = """
=== ZEMAX OPTICSTUDIO AUTONOMOUS OPTICAL DESIGN STANDARD OPERATING PROCEDURE (SOP) ===
⚠️ MANDATORY 5-STAGE CLOSED-LOOP PROTOCOL FOR ANY OPTICAL DESIGN OR MODELING TASK:

0. [STEP 0: SPECIFICATION COMPLETENESS AUDIT & INTERACTIVE REFINEMENT]
   - When a user proposes an initial design requirement, NEVER jump straight into searching or designing if specifications are vague or missing!
   - Call `zemax_audit_requirements` to inspect the completeness of input specifications (EFL, F/#, FOV/Sensor, Wavelengths, Pixel Pitch, TOTR, WD, Temperature).
   - If status is 'NEEDS_CLARIFICATION' (missing mandatory parameters), HALT IMMEDIATELY. Present the structured question list with recommended default values to the user.
   - Proactively dialogue with the user to refine and finalize all key parameters before proceeding.

1. [STEP 1: WEB SEARCH INITIAL STRUCTURE]
   - Once requirements are complete, use web search tools (patents, papers, optical handbooks like Smith, Kingslake, Fischer) to locate proven initial lens architectures matching specifications (EFL, F/#, FOV, wavelengths).
   - NEVER start modeling from arbitrary numbers or guesses.

2. [STEP 2: DEEP OPTICAL THINKING & SENIOR ABERRATION ANALYSIS]
   - Perform Gaussian first-order power distribution calculations (EPD = EFL / F#, optical invariant H = n*u*y, Phi = sum(phi_i)).
   - Analyze third-order Seidel aberration budget (spherical, coma, astigmatism, Petzval field curvature, distortion).
   - Formulate chromatic aberration correction pairs (crown-flint Abbe number relations sum(phi/V)=0, secondary spectrum).
   - Enforce physical feasibility rules (CT >= 1.0mm, ET >= 1.0mm, air clearance MNCA/MNEA >= 0.2mm).

2.1. [CRITICAL EXPERT RULE: STRICT INTERNAL AIR GAP & COMPACTNESS BUDGET]
   - NEVER allow internal air spaces between lens elements to blow up into empty voids (> 12~15mm)!
   - Understand the aberration cheat: optimizers expand air gaps to bypass Petzval field curvature and higher-order spherical aberration, creating unmountable, vibration-sensitive barrels with huge decenter sensitivity (delta = d * theta_tilt).
   - Constrain all internal air spaces with MXCA / CTLT <= 8.0 ~ 12.0 mm and bound barrel core stack length with TTHI <= 0.35 ~ 0.6 * EFL.
   - If image quality struggles under compact spacing, substitute higher-index glass (e.g. H-ZLaF50D / N-LASF44, n > 1.78) or split elements; DO NOT surrender to runaway air gaps!

2.2. [PREFERRED INDUSTRIAL GLASS CATALOG & PRODUCIBILITY]
   - Always choose glasses from CDGM and Schott Preferred Lists (H-K9L/N-BK7, H-ZF4A/N-SF11, H-LaK53A/N-LAK9, H-ZLaF50D/N-LAF21, H-FK61/N-PK52A).
   - Check acid resistance (SR), weathering resistance (CR), and thermal shock sensitivity. Never place soft or low-acid-resistance glasses on exposed external surfaces.

2.3. [SECONDARY SPECTRUM & ABNORMAL DISPERSION PLACEMENT]
   - When correcting secondary spectrum (APO), abnormal dispersion ED glass (H-FK61, CaF2) MUST be placed on the positive element with maximum axial marginal ray height (y) to maximize correction leverage without steep curvatures.

2.4. [DFM, TESTABILITY & ANGLE OF INCIDENCE (AOI) DESENSITIZATION]
   - Test plate fit rule: Surface radius must satisfy |R| >= 1.2 ~ 1.5 * Semi-Diameter to prevent untestable hyper-hemispherical bowls.
   - Desensitize tolerances by constraining max ray angle of incidence (RAID <= 30°~45°). If ray bending delta = I - I' is too large, split the element into two gentle elements.
   - Lens edge thickness must satisfy ET >= 1.2 ~ 1.5mm to eliminate knife edges and chipping. Flat land margin >= 1.0mm.

2.5. [ASPHERIC SURFACE DISCIPLINE]
   - Placement: Near stop/pupil to correct spherical/coma; far from stop/near image to correct field curvature/distortion.
   - Order Release: Start with conic constant k, then 4th order, then 6th order. Strictly FREEZE 8th+ order coefficients to prevent unmanufacturable mid-spatial frequency ripples and inflection points.

2.6. [PROGRESSIVE 4-STAGE OPTIMIZATION PIPELINE]
   - Stage 1: Freeze thicknesses, solve basic topology with RMS Spot.
   - Stage 2: Pre-embed hard boundaries (MNCA, MXCA, MNEA, TTHI, MNEG), release thicknesses under DLS.
   - Stage 3: Switch to RMS Wavefront (Centroid) as spot approaches 1.5x Airy disk; substitute catalog glasses.
   - Stage 4: Hammer optimization with doubled boundary weights + RAID desensitization.

3. [STEP 3: FORMULATE & REGISTER DESIGN PROPOSAL]
   - Call `zemax_register_design_proposal` to record and structure your optical design proposal.
   - Present the formatted proposal to the user for formal review.

4. [STEP 4: USER CONFIRMATION GATE - HALT & ASK]
   - HALT and explicitly ask the user whether they approve starting simulation in Zemax.
   - STRICT PROHIBITION: DO NOT invoke `zemax_new_file`, `zemax_load_template`, `zemax_surface_operations`, `zemax_setup_merit_function`, `zemax_run_optimization`, or `zemax_run_hammer` until the user explicitly gives authorization.
======================================================================================
"""

app = MCPServer(
    name="zemax-opticstudio",
    instructions=MANDATORY_WORKFLOW_INSTRUCTIONS,
)



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
def zemax_audit_requirements(
    system_type: str = "imaging_lens",
    specs: Optional[Dict[str, Any]] = None,
    user_prompt: Optional[str] = None,
) -> str:
    """
    Step 0: Optical Requirements Specification (ORS) Audit & Clarification Tool.
    Examines input optical requirements against standard industry Optical Requirements
    Specifications (ORS) matrices, detects missing core/recommended parameters, and
    generates tailored clarification questions with sensible default recommendations.

    Call this tool FIRST whenever a user proposes an optical design or improvement task.
    If the status is 'NEEDS_CLARIFICATION', ask the user the returned clarification questions
    before searching initial structures or simulating.
    """
    res = _audit_requirements(system_type=system_type, specs=specs, user_prompt=user_prompt)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
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
) -> str:
    """
    Register, format, and audit a comprehensive optical design proposal before Zemax simulation.
    MANDATORY GATE:
    1. Search web for initial patent/literature baseline structures.
    2. Perform deep optical thinking (first-order power distribution, Seidel aberration budget, glass pairing).
    3. Output this structured proposal to the user for formal review.
    4. Explicitly obtain user confirmation before setting user_confirmed_to_simulate=True and launching simulation.
    """
    res = _register_design_proposal(
        project_name=project_name,
        target_specs=target_specs,
        initial_structure_source=initial_structure_source,
        optical_theory_analysis=optical_theory_analysis,
        glass_selection_rationale=glass_selection_rationale,
        merit_function_strategy=merit_function_strategy,
        mechanical_constraints=mechanical_constraints,
        internal_air_spacing_budget=internal_air_spacing_budget,
        user_confirmed_to_simulate=user_confirmed_to_simulate,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_new_file(catalogs: Optional[List[str]] = None) -> str:
    """
    Create a clean, new sequential optical design in Zemax OpticStudio.
    Automatically enables standard optical glass catalogs (default: SCHOTT and CDGM).

    [SOP PREREQUISITE GATE]: Before creating a new design file for a design task,
    the agent MUST perform web search for initial structures, conduct deep optical thinking,
    present a structured design proposal for review, and receive explicit user authorization.
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

    [SOP PREREQUISITE GATE]: Review proposal with user and receive confirmation before loading templates for a new design.
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

    [SOP PREREQUISITE GATE]: If constructing or altering a new design, prior web search,
    deep optical thinking, proposal review, and explicit user confirmation are required.
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
    max_air_center: float = 12.0,
    min_air_edge: float = 0.5,
    min_glass_center: float = 2.0,
    max_glass_center: float = 25.0,
    min_glass_edge: float = 1.0,
    target_efl: Optional[float] = None,
    efl_weight: float = 10.0,
    max_totr: Optional[float] = None,
    totr_weight: float = 1.0,
    max_internal_air: float = 12.0,
    max_barrel_length: Optional[float] = None,
    barrel_weight: float = 20.0,
) -> str:
    """
    Build a standard Merit Function with strict manufacturing and optomechanical compactness controls.
    Automatically configures:
    - Gaussian Quadrature pupil integration (rings, arms)
    - Fabrication boundary constraints (MNCA/MXCA/MNEA for air, MNCG/MXCG/MNEG for glass)
    - Strict internal element-to-element air gap constraint (MXCA <= 12.0 mm) preventing runaway air spaces
    - Optional lens barrel core stack length constraint (TTHI) preventing oversized housings
    - Optional first-order focal length (EFFL) and total track (TOTR) targets.
    criterion: 'RMS_Spot' (for geometric aberrations) or 'RMS_Wavefront' (near diffraction limit).
    reference: 'Centroid' or 'ChiefRay'.

    [SOP PREREQUISITE GATE]: Requires user approval of the design proposal before setting up optimization.
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
        max_internal_air=max_internal_air,
        max_barrel_length=max_barrel_length,
        barrel_weight=barrel_weight,
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

    [SOP PREREQUISITE GATE]: Optimization runs MUST be preceded by web search of initial structures,
    deep optical thinking, design proposal review, and explicit user approval.
    """
    res = _run_optimization(algorithm=algorithm, cycles=cycles, cores=cores)
    return json.dumps(res, ensure_ascii=False, indent=2)


@app.tool()
def zemax_run_hammer(timeout_seconds: int = 10) -> str:
    """
    Run Hammer Optimization to search broader parameter space for alternative local minima.
    timeout_seconds: Duration before terminating Hammer search (default: 10s).

    [SOP PREREQUISITE GATE]: Requires explicit user authorization before running global/hammer optimization.
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


@app.resource("zemax://workflow/sop")
def get_workflow_sop_resource() -> str:
    """Mandatory 4-Stage Autonomous Optical Design SOP documentation."""
    return MANDATORY_WORKFLOW_INSTRUCTIONS.strip()


@app.resource("zemax://proposal/current")
def get_current_proposal_resource() -> str:
    """Currently registered optical design proposal in this session."""
    return json.dumps(_get_current_design_proposal(), ensure_ascii=False, indent=2)


@app.resource("zemax://manual/expert_compactness_guide")
def get_expert_compactness_guide_resource() -> str:
    """Optical Design Expert Manual: Element Air Spacing, Optomechanical Assembly & Compactness Guidelines."""
    manual_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "domain", "optical_expert_manual.md")
    if os.path.exists(manual_path):
        with open(manual_path, "r", encoding="utf-8") as f:
            return f.read()
    return "Expert manual file not found."


@app.resource("zemax://manual/expert_design_guide")
def get_expert_design_guide_resource() -> str:
    """Optical Design Expert Manual: Full Lifecycle System Design, Aberration Balancing, Glass Selection & DFM Standards."""
    manual_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "domain", "optical_expert_manual.md")
    if os.path.exists(manual_path):
        with open(manual_path, "r", encoding="utf-8") as f:
            return f.read()
    return "Expert manual file not found."


# ==============================================================================
# MCP Prompts
# ==============================================================================

@app.prompt("optical_design_workflow")
def optical_design_workflow_prompt(target_optical_task: str) -> str:
    """
    Standard Operating Procedure prompt guiding the AI agent through the mandatory
    5-stage optical design closed-loop protocol.
    """
    return (
        f"You are tasked with the following optical engineering design: {target_optical_task}\n\n"
        "MANDATORY 5-STAGE WORKFLOW:\n"
        "0. Specification Completeness Audit: Call `zemax_audit_requirements` to check if key parameters "
        "(EFL, F/#, FOV/Sensor, Wavelength, Pixel Pitch, TOTR, WD) are provided. If incomplete, HALT and "
        "interactively ask the user to clarify missing parameters with recommended defaults!\n"
        "1. Web Search Initial Structure: Search patent databases, literature, or optical handbooks (Smith, Kingslake) "
        "to find proven baseline configurations matching the target specs.\n"
        "2. Deep Optical Thinking: Calculate first-order Gaussian parameters (EPD, optical invariant), Seidel aberration "
        "budget, and crown-flint chromatic aberration balance under strict internal air gap (<= 12mm) constraints.\n"
        "3. Design Proposal Review: Call `zemax_register_design_proposal` and present the complete proposal to the user.\n"
        "4. User Confirmation Gate: HALT and ask the user for confirmation. DO NOT call Zemax simulation/optimization "
        "tools until explicit approval is granted."
    )


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
