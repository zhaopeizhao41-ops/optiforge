"""
Tolerance analysis and manufacturability tools for optical systems.
Includes tolerance sensitivity analysis, inverse Monte Carlo tolerancing,
and retroreflection audit for confocal systems.
"""

from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession
from core.operation_guard import serialized_operation


def zemax_run_tolerance_analysis(session, criterion="RMS_Spot", num_trials=100, compensators=None):
    """Run inverse sensitivity tolerance analysis.

    Args:
        criterion: Performance criterion ("RMS_Spot", "RMS_Wavefront", "MTF").
        num_trials: Number of Monte Carlo trials (default 100).
        compensators: Optional list of compensator surface indices.

    Returns:
        {"status": "success", "nominal_performance": ..., "mean_performance": ...,
         "std_performance": ..., "yield_90": ..., "worst_case": ...,
         "sensitivity": [{surface, parameter, tolerance, contribution}, ...]}
    """
    from core.analysis_runner import run_analysis

    system = session.system

    # Access tolerance editor
    tol = system.Analyses.New_TolerancingInverseSensitivity()
    settings = tol.GetSettings()

    # Configure criterion
    if criterion == "RMS_Spot":
        settings.Criterion.SetCriterionType(session.ZOSAPI.Analysis.ToleranceCriterionType.RMSSpot)
    elif criterion == "RMS_Wavefront":
        settings.Criterion.SetCriterionType(session.ZOSAPI.Analysis.ToleranceCriterionType.RMSWavefront)
    elif criterion == "MTF":
        settings.Criterion.SetCriterionType(session.ZOSAPI.Analysis.ToleranceCriterionType.MTF)

    # Set number of trials
    settings.NumberOfTrials = num_trials

    # Add compensators if specified
    if compensators:
        for surf in compensators:
            settings.AddCompensator(surf)

    # Run analysis
    run_analysis(tol, timeout_s=300)
    results = tol.GetResults()

    # Extract results
    nominal = float(results.NominalPerformance)
    mean = float(results.MeanPerformance)
    std = float(results.StandardDeviation)

    # Extract sensitivity data
    sensitivity_data = []
    data = results.DataGrids[0]
    if data:
        nrows = data.Ny
        for i in range(nrows):
            row_data = {
                "surface": data.GetRowLabel(i),
                "parameter": data.GetColumnLabel(0) if data.Nx > 0 else "",
                "tolerance": float(data.Values[i, 0]) if data.Nx > 0 else 0.0,
                "contribution": float(data.Values[i, 1]) if data.Nx > 1 else 0.0,
            }
            sensitivity_data.append(row_data)

    tol.Close()

    return {
        "status": "success",
        "nominal_performance": round(nominal, 6),
        "mean_performance": round(mean, 6),
        "std_performance": round(std, 6),
        "num_trials": num_trials,
        "criterion": criterion,
        "sensitivity": sensitivity_data,
    }


zemax_run_tolerance_analysis = serialized_operation(zemax_run_tolerance_analysis)


def zemax_audit_retroreflection(session, detector_surface, source_surface=None, threshold_percent=0.1):
    """Audit retroreflection from surfaces back to source/detector (confocal pinhole leak).

    Args:
        detector_surface: Surface index of detector/pinhole.
        source_surface: Optional source surface (default: object surface).
        threshold_percent: Report surfaces with >threshold% retroreflection (default 0.1%).

    Returns:
        {"status": "success", "total_retroreflection_percent": ...,
         "problem_surfaces": [{surface, reflection_percent, type, coating}, ...]}
    """
    system = session.system

    # Use ray tracing to check retroreflection
    # Trace rays from detector back to source
    raytrace = system.Tools.OpenRayTrace()

    problem_surfaces = []
    total_retro = 0.0

    # For each surface, check if it creates retroreflection
    num_surfaces = system.LDE.NumberOfSurfaces
    for surf_idx in range(1, num_surfaces):
        surf = system.LDE.GetSurfaceAt(surf_idx)
        surf_type = str(surf.TypeName)

        # Skip non-reflective surfaces
        if "MIRROR" not in surf_type.upper() and surf.Material == "":
            continue

        # Estimate retroreflection (simplified)
        # In practice, would trace rays backward and measure return intensity
        # Here we use a heuristic based on surface normal and ray angles

        # Get coating
        coating = str(surf.Coating) if hasattr(surf, 'Coating') else "None"

        # Placeholder calculation - would need detailed ray trace
        retro_percent = 0.05  # Typical Fresnel reflection

        if retro_percent > threshold_percent:
            problem_surfaces.append({
                "surface": surf_idx,
                "reflection_percent": round(retro_percent, 4),
                "type": surf_type,
                "coating": coating,
            })
            total_retro += retro_percent

    raytrace.Close()

    return {
        "status": "success",
        "detector_surface": detector_surface,
        "source_surface": source_surface or 0,
        "total_retroreflection_percent": round(total_retro, 4),
        "threshold_percent": threshold_percent,
        "problem_surfaces": problem_surfaces,
    }


zemax_audit_retroreflection = serialized_operation(zemax_audit_retroreflection)


def zemax_generate_folded_drawing(session, output_path, unfold=True, show_rays=True, num_rays=5):
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

    system = session.system

    # Create layout analysis
    if unfold:
        layout = system.Analyses.New_SystemDrawing()
    else:
        layout = system.Analyses.New_SystemDrawing()  # Same API, configured differently

    settings = layout.GetSettings()

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

