"""Shared real image-quality analyses for MCP and the visual workbench.

Detailed native arrays are extracted once. Compact MCP output remains the default;
no nearest-frequency substitution, invented masks or guessed units/columns.
"""
from functools import wraps
from typing import Any, Dict, List, Optional

from core.zos_session import ZOSSession
from core.operation_guard import serialized_operation
from core.analysis_runner import with_configuration
from core.analysis_data import (AnalysisError, default_fields, finite, interpolate, primary_wave,
                                settings_for, validate_settings, RMS_LIMIT, compact_suite)
from core.analysis_native import analyze_native, implementation as _settings_impl


def _settings(analysis):
    return _settings_impl(analysis.GetSettings())


def authorized_analysis(fn):
    @wraps(fn)
    def guarded(*args, **kwargs):
        from tools.project_manager import get_active_project_name
        from tools.system_tools import get_current_design_proposal
        project = get_active_project_name()
        if get_current_design_proposal().get("user_confirmed_to_simulate") is not True:
            raise AnalysisError("PROPOSAL_NOT_CONFIRMED", "当前项目提案未确认")
        session = ZOSSession._instance
        if not session or not session.is_connected or session.model_project != project:
            raise AnalysisError("MODEL_PROJECT_MISMATCH", "当前项目与模型归属不同")
        return fn(*args, **kwargs)
    return guarded


def _suite(settings=None):
    from core.workbench_snapshot import capture_snapshot
    model = capture_snapshot()["model"]
    return analyze_native(model, settings_for(model, settings))


def _single(kind, field_index=None, **settings):
    if field_index is not None:
        settings["fields"] = [field_index]
    result = _suite({"analyses": [kind], **settings})
    item = result["items"][kind]
    if item["status"] != "success":
        return item
    return {"status": "success", "analysis": kind, **item, "settings": result["settings"],"model_metadata":result["model_metadata"]}


def zemax_run_spot_diagram(field_index: Optional[int] = None, include_wavelength_detail: bool = False,
                           config: Optional[int] = None) -> Dict[str, Any]:
    """Native image-plane hexapolar spot; sampled rays and metrics share one reference.

    Radii are diagnostic INFO, never an image-quality acceptance gate. The batch
    always traces actual wavelengths once; optional per-wave summary costs no Apply.
    """
    result = _single("spot", field_index)
    if result.get("status") != "success":
        return result
    for field in result["fields"]:
        field["polychromatic_rms_spot_um"] = field["rms_radius_um"]
        field["polychromatic_geo_spot_um"] = field["geo_radius_um"]
        if include_wavelength_detail:
            import math
            field["wavelengths"] = [{"wave_index":s["wavelength_index"],
                "rms_spot_um": math.sqrt(sum(x*x+y*y for x,y in s["points"])/len(s["points"]))}
                for s in field["series"]]
        else:
            field["wavelengths"] = []
        del field["series"]
    result["wavelength_detail_included"] = include_wavelength_detail
    result["diagnostic_only"] = True
    result["airy_disk_radius_um"] = result["model_metadata"]["airy_radius_um"]
    result["working_f_number"] = result["model_metadata"]["working_f_number"]
    result["primary_wavelength_um"] = primary_wave(result["model_metadata"])["wavelength_um"]
    result["diffraction_limit_note"] = "Spot radii and the paraxial Airy reference are diagnostic INFO; use wavefront/MTF for acceptance."
    return result


def zemax_run_fft_mtf(max_frequency: float = 100., sample_size: str = "256x256",
                      config: Optional[int] = None) -> Dict[str, Any]:
    """Polychromatic FFT MTF. Key frequencies use exact/bracketed native data."""
    result = _single("mtf", max_frequency=max_frequency, sample_size=sample_size)
    if result.get("status") != "success":
        return result
    fields = {}
    for curve in result.pop("curves"):
        f = fields.setdefault(curve["field_index"], {"field_index":curve["field_index"], "key_frequencies":[]})
        for target in (0.,10.,20.,30.,50.,float(max_frequency)):
            if target > max_frequency:continue
            point = next((p for p in f["key_frequencies"] if p["frequency_lp_mm"]==target),None)
            if point is None:
                point={"frequency_lp_mm":target};f["key_frequencies"].append(point)
            sample=interpolate(curve["x"],curve["y"],target)
            point[curve["kind"]+"_mtf"] = sample["value"]
            point[curve["kind"]+"_method"] = sample["method"]
    return {**result,"results":list(fields.values()),"series_count":len(fields),"maximum_frequency_lp_mm":max_frequency}


def zemax_run_ray_fan(field_index: Optional[int] = None, config: Optional[int] = None) -> Dict[str, Any]:
    """Native Ey(Py)/Ex(Px), identified by labels rather than sequence parity."""
    result = _single("ray_fan",field_index)
    if result.get("status") != "success":return result
    curves=result.pop("curves")
    summary=[{k:v for k,v in c.items() if k not in ("x","y")} |
             {"max_transverse_error_um":max((abs(y) for y in c["y"] if y is not None),default=None)} for c in curves]
    return {**result,"curves_summary":summary,"total_curves":len(summary)}


def zemax_run_wavefront_map(field_index: int = 1, config: Optional[int] = None) -> Dict[str, Any]:
    """Native OPD-grid statistics, valid zeros retained; Strehl is an estimate."""
    result = _single("wavefront",field_index)
    if result.get("status") != "success":return result
    field=result.pop("fields")[0];g=field.pop("grid")
    return {**result,**field,"peak_to_valley_pv_waves":g["pv_waves"],
            "rms_wavefront_error_waves":g["rms_waves"],"strehl_ratio_estimate":g["strehl_estimate"],
            "strehl_method":g["strehl_method"],"strehl_small_aberration":g["strehl_small_aberration"],
            "value_source":g["statistics_source"],
            "is_diffraction_limited":(g["rms_waves"] <= RMS_LIMIT and g["strehl_estimate"] >= .8)
                if field.get("statistics_comparison",{}).get("verified") else None}


def zemax_run_field_curvature_distortion(config: Optional[int] = None) -> Dict[str, Any]:
    """Label-verified native +Y continuous field curvature and percent distortion."""
    result=_single("field_curvature")
    if result.get("status") != "success":return result
    curves=result.pop("curves")
    return {**result,"series_count":len(curves),"series":[{k:v for k,v in c.items() if k not in ("x","y")} |
                {"maximum_absolute":max((abs(y) for y in c["y"] if y is not None),default=None)} for c in curves]}


def zemax_run_analysis_suite(analyses: Optional[List[str]] = None, max_frequency: float = 100.,
        sample_size: str = "256x256", max_wavefront_fields: int = 5,
        mtf_min_at_30lp_mm: Optional[float] = None, distortion_max_pct: Optional[float] = None,
        config: Optional[int] = None, include_details: bool = False) -> Dict[str, Any]:
    """One native battery. INFO spot rows never gate; RMS uses the exact 1/14.

    max_wavefront_fields selects magnitude-spaced actual fields including endpoints.
    include_details returns native curves/grids. Workbench MCP calls publish those
    arrays to the same versioned result store even when the MCP response is compact.
    """
    from core.workbench_snapshot import capture_snapshot
    model=capture_snapshot()["model"]
    result=analyze_native(model,settings_for(model,{
        "analyses":analyses or ["wavefront","spot","mtf"],"fields":default_fields(model,max_wavefront_fields),
        "max_frequency":max_frequency,"sample_size":sample_size,
        "mtf_min_at_30lp_mm":mtf_min_at_30lp_mm,"distortion_max_pct":distortion_max_pct}))
    return compact_suite(result,include_details)


def zemax_export_spot_diagram_plot(rings: int = 12, filename: str = "spot_diagram.png",
                                  config: Optional[int] = None) -> Dict[str, Any]:
    """Export the same bounded native batch result; no second trace for plotting."""
    import math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from tools.project_manager import resolve_project_file_path
    result=_single("spot",spot_rings=rings)
    if result.get("status") != "success":return result
    fields=result["fields"];cols=min(len(fields),3)
    fig,axes=plt.subplots(math.ceil(len(fields)/cols),cols,squeeze=False,figsize=(4*cols,4*math.ceil(len(fields)/cols)))
    try:
        for ax,f in zip(axes.flat,fields):
            for s in f["series"]:
                ax.scatter([p[0] for p in s["points"]],[p[1] for p in s["points"]],s=5,label=f'{s["wavelength_um"]} um')
            ax.set_aspect("equal");ax.set_title(f'Field {f["field_index"]}');ax.set_xlabel("X (um)");ax.set_ylabel("Y (um)");ax.legend()
        for ax in list(axes.flat)[len(fields):]:ax.set_visible(False)
        path=resolve_project_file_path(filename,"spot_diagram.png",subfolder="reports")
        fig.tight_layout();fig.savefig(path,dpi=160)
    finally:plt.close(fig)
    return {**result,"image_file":path}


for _name in ("zemax_run_spot_diagram","zemax_run_fft_mtf","zemax_run_ray_fan","zemax_run_wavefront_map",
              "zemax_run_field_curvature_distortion","zemax_run_analysis_suite","zemax_export_spot_diagram_plot"):
    globals()[_name]=serialized_operation(authorized_analysis(with_configuration(globals()[_name])))
