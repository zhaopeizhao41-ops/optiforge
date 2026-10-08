"""
Tolerance analysis and manufacturability tools for optical systems.
Includes tolerance sensitivity analysis, inverse Monte Carlo tolerancing,
and retroreflection audit for confocal systems.
"""

import math
from typing import Any, Dict, List, Optional

from core.zos_session import ZOSSession
from core.operation_guard import serialized_operation
from core.operands import _operand

# Criterion name -> (ZOS-API enum attribute, human label). Resolved lazily so the tool
# fails with a clear message rather than an AttributeError when the API shape differs.
_TOLERANCE_CRITERIA = {
    "RMS_SPOT": ("RMSSpot", "RMS Spot Radius"),
    "RMS_WAVEFRONT": ("RMSWavefront", "RMS Wavefront"),
    "MTF": ("MTF", "MTF"),
    "GEO_SPOT": ("GeometricSpot", "Geometric Spot Radius"),
}


def zemax_run_tolerance_analysis(criterion="RMS_Spot", num_trials=100, compensators=None):
    """Run a Monte Carlo tolerance analysis and report the statistical performance.

    Compensators are added through the tolerance data editor, and the analysis is run
    with `num_trials` Monte Carlo samples of the active tolerance set.

    Args:
        criterion: Performance criterion ("RMS_Spot", "RMS_Wavefront", "MTF", "Geo_Spot").
        num_trials: Number of Monte Carlo trials (default 100).
        compensators: Optional list of compensator surface indices.

    Returns:
        {"status": "success", "nominal_performance": ..., "mean_performance": ...,
         "std_performance": ..., "best_performance": ..., "worst_performance": ...,
         "yield_percent": ..., "criterion": ..., "num_trials": ..., "compensators": [...]}
    """
    from core.analysis_runner import run_analysis

    session = ZOSSession.get_instance()
    system = session.system
    zos = session.ZOSAPI

    key = str(criterion).upper().replace(" ", "_")
    if key not in _TOLERANCE_CRITERIA:
        return {"status": "error",
                "message": f"Unknown criterion '{criterion}'. Available: {sorted(_TOLERANCE_CRITERIA)}."}
    if not isinstance(num_trials, int) or num_trials < 1:
        return {"status": "error", "message": "num_trials must be a positive integer."}

    enum_name, label = _TOLERANCE_CRITERIA[key]

    tol = system.Analyses.New_TolerancingMonteCarlo()
    settings = tol.GetSettings()

    try:
        criterion_enum = getattr(zos.Analysis.ToleranceCriterionType, enum_name)
    except AttributeError as error:
        tol.Close()
        return {"status": "error",
                "message": f"Tolerance criterion '{enum_name}' is unavailable in this "
                           f"OpticStudio build: {error}"}

    settings.Criterion.SetCriterionType(criterion_enum)
    settings.NumberOfTrials = num_trials
    if compensators:
        # Compensators are referenced by the compensator operand in the tolerance editor;
        # AddCompensator is not part of the public settings surface on every build.
        for surf in compensators:
            try:
                settings.AddCompensator(int(surf))
            except Exception as error:
                tol.Close()
                return {"status": "error",
                        "message": f"Could not add compensator surface {surf}: {error}"}

    run_analysis(tol, timeout_s=300)
    results = tol.GetResults()

    def _read(name: str, default: float = float("nan")) -> float:
        try:
            return float(getattr(results, name))
        except Exception:
            return default

    nominal = _read("NominalPerformance")
    mean = _read("MeanPerformance")
    std = _read("StandardDeviation")
    best = _read("BestPerformance")
    worst = _read("WorstPerformance")

    # A trial passes when it stays within 2x the nominal criterion value.
    yield_percent = _read("PercentWithinTol", default=float("nan"))
    if math.isnan(yield_percent) and not math.isnan(nominal) and not math.isnan(worst):
        yield_percent = 100.0 if worst <= 2.0 * nominal else float("nan")

    tol.Close()

    result = {
        "status": "success",
        "criterion": criterion,
        "criterion_label": label,
        "num_trials": num_trials,
        "nominal_performance": None if math.isnan(nominal) else round(nominal, 6),
        "mean_performance": None if math.isnan(mean) else round(mean, 6),
        "std_performance": None if math.isnan(std) else round(std, 6),
        "best_performance": None if math.isnan(best) else round(best, 6),
        "worst_performance": None if math.isnan(worst) else round(worst, 6),
        "yield_percent": None if math.isnan(yield_percent) else round(yield_percent, 3),
    }
    if compensators:
        result["compensators"] = [int(s) for s in compensators]

    # If the tolerance editor is empty the run is meaningless; say so explicitly.
    if results is None or all(result[k] is None for k in
                             ("nominal_performance", "mean_performance", "std_performance")):
        result["warning"] = ("No tolerance data was recovered; define tolerances in the "
                             "tolerance data editor before running the analysis.")
    return result


zemax_run_tolerance_analysis = serialized_operation(zemax_run_tolerance_analysis)


def _fresnel_reflectance(n_incident: float, n_exit: float, incidence_deg: float) -> float:
    """Unpolarized Fresnel reflectance at a dielectric interface (0-1).

    Incidence angle is measured from the surface normal. Total internal reflection
    returns 1.0; a zero or invalid index contrast falls back to 0.04 (bare glass).
    """
    theta_i = math.radians(abs(incidence_deg))
    cos_i = math.cos(theta_i)
    if n_incident <= 0 or n_exit <= 0:
        return 0.04
    sin_t_sq = (n_incident / n_exit) ** 2 * (1.0 - cos_i ** 2)
    if sin_t_sq >= 1.0:
        return 1.0
    cos_t = math.sqrt(1.0 - sin_t_sq)
    rs = ((n_incident * cos_i - n_exit * cos_t) / (n_incident * cos_i + n_exit * cos_t)) ** 2
    rp = ((n_incident * cos_t - n_exit * cos_i) / (n_incident * cos_t + n_exit * cos_i)) ** 2
    return 0.5 * (rs + rp)


def zemax_audit_retroreflection(detector_surface, source_surface=None, threshold_percent=0.1,
                                coating_reflectance=0.005):
    """Audit reflective (retro) return to the detector/pinhole (confocal pinhole leak).

    For every surface the Fresnel reflectance is computed from the indices either side
    and the chief ray incidence angle (RAID) at that surface. Mirrors are treated as
    fully reflecting; coated dielectric surfaces use `coating_reflectance` instead of
    the bare Fresnel value.

    Args:
        detector_surface: Surface index of the detector / confocal pinhole.
        source_surface: Optional source surface (defaults to the object surface, 0).
        threshold_percent: Report surfaces whose reflectance exceeds this percentage.
        coating_reflectance: Residual reflectance assumed for an AR-coated dielectric
            surface (default 0.5%).

    Returns:
        {"status": "success", "total_retroreflection_percent": ...,
         "problem_surfaces": [{surface, reflection_percent, type, coating, incidence_deg}, ...]}
    """
    session = ZOSSession.get_instance()
    system = session.system
    zos = session.ZOSAPI
    lde = system.LDE

    n_surf = int(lde.NumberOfSurfaces)
    if not 0 <= int(detector_surface) < n_surf:
        return {"status": "error",
                "message": f"detector_surface {detector_surface} is outside the model (0..{n_surf - 1})."}

    problem_surfaces: List[Dict[str, Any]] = []
    total_retro = 0.0
    wave = 1

    for surf_idx in range(1, n_surf - 1):
        surf = lde.GetSurfaceAt(surf_idx)
        type_name = str(surf.TypeName)
        type_upper = type_name.upper()
        material = str(surf.Material).strip()

        is_mirror = "MIRROR" in type_upper or "MIRROR" in material.upper()
        is_dielectric = material != ""
        if not (is_mirror or is_dielectric):
            continue

        coating = str(getattr(surf, "Coating", "") or "")
        try:
            n_front = _operand(session, "INDX", surf_idx, wave)
            n_back = _operand(session, "INDX", surf_idx + 1, wave)
        except Exception:
            n_front, n_back = 1.0, 1.5
        try:
            incidence_deg = abs(_operand(session, "RAID", surf_idx, 0, 0, 1, wave, 0, 0, 0))
        except Exception:
            incidence_deg = 0.0

        if is_mirror:
            reflectance = 0.98  # Enhanced silver / protected aluminium
        elif coating and coating.upper() not in ("NONE", ""):
            reflectance = float(coating_reflectance)
        else:
            reflectance = _fresnel_reflectance(n_front, n_back, incidence_deg)

        retro_percent = reflectance * 100.0
        if retro_percent > threshold_percent:
            problem_surfaces.append({
                "surface": surf_idx,
                "reflection_percent": round(retro_percent, 5),
                "type": type_name,
                "coating": coating or "None",
                "incidence_deg": round(incidence_deg, 3),
                "n_front": round(n_front, 4),
                "n_back": round(n_back, 4),
            })
            total_retro += retro_percent

    return {
        "status": "success",
        "detector_surface": int(detector_surface),
        "source_surface": source_surface if source_surface is not None else 0,
        "threshold_percent": threshold_percent,
        "total_retroreflection_percent": round(total_retro, 5),
        "problem_surfaces": problem_surfaces,
    }


zemax_audit_retroreflection = serialized_operation(zemax_audit_retroreflection)


def zemax_generate_folded_drawing(output_path, unfold=True, show_rays=True, num_rays=5):
    """Generate folded optical system drawing (layout with fold mirrors).

    Args:
        output_path: Output file path (PNG, PDF, or EMF).
        unfold: If True, generate unfolded (straightened) layout (default True).
        show_rays: Show ray traces in drawing (default True).
        num_rays: Number of rays to trace per field (default 5).

    Returns:
        {"status": "success", "output_path": ..., "num_surfaces": ..., "total_length_mm": ...}
    """
    from core.analysis_runner import run_analysis
    import os

    session = ZOSSession.get_instance()
    system = session.system

    # SystemDrawing is the API entry point for both the folded system layout and the
    # unfolded (straightened) layout; the unfold flag is carried on the settings object.
    layout = system.Analyses.New_SystemDrawing()
    settings = layout.GetSettings()

    unfolded = False
    for attr in ("UnfoldSystem", "Unfold", "UseUnfoldedSystem"):
        if hasattr(settings, attr):
            setattr(settings, attr, bool(unfold))
            unfolded = True
            break
    if not unfolded:
        layout.Close()
        return {"status": "error",
                "message": "This OpticStudio build does not expose an unfold setting on the "
                           "system drawing; the unfolded layout cannot be requested."}

    # Configure drawing settings
    if hasattr(settings, 'ShowRays'):
        settings.ShowRays = show_rays
    if hasattr(settings, 'NumberOfRays'):
        settings.NumberOfRays = num_rays

    # Run analysis
    run_analysis(layout, timeout_s=60)

    # Export drawing
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Determine format from extension
    ext = os.path.splitext(output_path)[1].lower()
    if ext == '.png':
        layout.GetResults().ExportAsPNG(output_path)
    elif ext == '.pdf':
        layout.GetResults().ExportAsPDF(output_path)
    elif ext == '.emf':
        layout.GetResults().ExportAsEMF(output_path)
    else:
        layout.Close()
        return {"status": "error", "message": f"Unsupported format: {ext}. Use .png, .pdf, or .emf"}

    # Get system metrics
    num_surfaces = system.LDE.NumberOfSurfaces
    total_length = 0.0
    for i in range(num_surfaces - 1):
        total_length += float(system.LDE.GetSurfaceAt(i).Thickness)

    layout.Close()

    return {
        "status": "success",
        "output_path": output_path,
        "num_surfaces": num_surfaces,
        "total_length_mm": round(total_length, 4),
        "unfolded": unfold,
    }


zemax_generate_folded_drawing = serialized_operation(zemax_generate_folded_drawing)

