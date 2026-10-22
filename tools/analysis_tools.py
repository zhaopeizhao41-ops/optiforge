"""
Zemax Optical Analysis Tools
Executes optical performance evaluations: Spot Diagram, FFT MTF, Ray Fan,
Wavefront Map, and Field Curvature / Distortion.
"""

import math
import re
from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession
from core.operation_guard import serialized_operation
from core.analysis_runner import run_analysis, with_configuration
from domain.zemax_rules import OpticalRuleCheck

# Header line such as "Peak to valley = 0.1234 waves, RMS = 0.0321 waves."
# or the localized "峰到谷 = 170.3507 波, RMS = 48.8159波."
_PV_RMS_RE = re.compile(r"=\s*([-+]?[0-9.]+(?:[eE][-+]?\d+)?).*RMS\s*=\s*([-+]?[0-9.]+(?:[eE][-+]?\d+)?)")


def _settings(analysis):
    """
    GetSettings() returns the base IAS_ interface under pythonnet 3; the concrete
    settings members (Field, MaximumFrequency, ...) live on __implementation__.
    """
    settings = analysis.GetSettings()
    return getattr(settings, "__implementation__", settings)


def _apply_field(settings, field_index: Optional[int], num_fields: int) -> Optional[int]:
    """Restrict an analysis to one field (1-based). Returns the applied field or None for all fields."""
    if field_index is None:
        return None
    if not 1 <= int(field_index) <= num_fields:
        raise ValueError(f"Invalid field_index {field_index}. Available fields: 1 to {num_fields}.")
    settings.Field.SetFieldNumber(int(field_index))
    return int(field_index)


def _matrix_columns(data) -> List[List[float]]:
    """Convert a .NET double[,] (points x columns) or double[] into a list of columns."""
    if int(data.Rank) == 1:
        return [[float(data[i]) for i in range(data.GetLength(0))]]
    n_pts, n_cols = data.GetLength(0), data.GetLength(1)
    return [[float(data[i, c]) for i in range(n_pts)] for c in range(n_cols)]


def _max_abs(values: List[float]) -> float:
    finite = [abs(v) for v in values if math.isfinite(v)]
    return max(finite) if finite else 0.0


def _primary_wavelength_um(sys) -> float:
    sd_w = sys.SystemData.Wavelengths
    for w_i in range(1, sd_w.NumberOfWavelengths + 1):
        wave_item = sd_w.GetWavelength(w_i)
        if wave_item.IsPrimary:
            return float(wave_item.Wavelength)
    return 0.55


def _tone(verdict: str) -> str:
    """Consistent uppercase tone so the model can pattern-match the row at a glance."""
    return {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "info": "INFO", "error": "ERROR"}[verdict]


def _worst_verdict(verdicts: List[str]) -> str:
    """Most severe verdict present. INFO is the lowest rung, not above PASS: a suite whose only
    gated rows passed is a PASS, and the informational rows must not downgrade it."""
    for level in ("fail", "error", "warn", "pass"):
        if level in verdicts:
            return level
    return "info"


def zemax_run_spot_diagram(
    field_index: Optional[int] = None,
    include_wavelength_detail: bool = False,
    config: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Run Standard Spot Diagram analysis across fields and wavelengths.
    Returns RMS spot radius, GEO maximum radius, Airy disk radius, and diffraction limit diagnosis.
    include_wavelength_detail: also break the spot down per wavelength. This costs one extra
      full Apply() cycle PER WAVELENGTH on top of the all-wavelength pass, so it is off by
      default; the polychromatic result already covers the usual pass/fail question. Turn it on
      only when you need to see which wavelength is dragging a field down.
    config: Optional 1-based MCE configuration to evaluate (restored afterwards).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    rule_checker = OpticalRuleCheck()

    num_sys_fields = int(sys.SystemData.Fields.NumberOfFields)
    if field_index is not None and not 1 <= int(field_index) <= num_sys_fields:
        return {"status": "error", "message": f"Invalid field_index {field_index}. Available fields: 1 to {num_sys_fields}."}

    spot = sys.Analyses.New_StandardSpot()
    try:
        settings = _settings(spot)
        settings.Field.UseAllFields()
        settings.Wavelength.UseAllWavelengths()
        res = run_analysis(spot)

        spot_data = res.SpotData
        if not spot_data:
            return {"status": "error", "message": "Failed to acquire SpotData."}

        num_fields = int(spot_data.NumberOfFields)
        num_waves = int(sys.SystemData.Wavelengths.NumberOfWavelengths)

        # Calculate Airy disk radius for comparison
        primary_wave_um = _primary_wavelength_um(sys)
        try:
            wfno = float(sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.WFNO, 0, 0, 0, 0, 0, 0, 0, 0))
        except Exception:
            wfno = 0.0

        airy_disk_radius_um = rule_checker.calculate_airy_disk_radius_um(primary_wave_um, wfno)

        field_results = []
        target_fields = [int(field_index)] if field_index is not None else range(1, num_fields + 1)

        for f_idx in target_fields:
            rms_poly = float(spot_data.GetRMSSpotSizeFor(f_idx, 0))  # 0 is polychromatic
            geo_poly = float(spot_data.GetGeoSpotSizeFor(f_idx, 0))

            is_diffraction_limited = (rms_poly <= airy_disk_radius_um) if airy_disk_radius_um > 0 else False

            field_results.append({
                "field_index": f_idx,
                "polychromatic_rms_spot_um": round(rms_poly, 4),
                "polychromatic_geo_spot_um": round(geo_poly, 4),
                "airy_disk_radius_um": round(airy_disk_radius_um, 4),
                "is_diffraction_limited": is_diffraction_limited,
                "wavelengths": [],
            })

        # In an all-wavelength analysis some API versions return aggregate data
        # even for a nonzero waveN. Recompute each selected wavelength, copying
        # native values before the next Apply invalidates the previous results.
        # Opt-in: this is num_waves extra full Apply() cycles, the single most
        # expensive thing this tool can do.
        if include_wavelength_detail:
            for w_idx in range(1, num_waves + 1):
                settings.Wavelength.SetWavelengthNumber(w_idx)
                mono_data = run_analysis(spot).SpotData
                if not mono_data:
                    return {"status": "error", "message": f"Failed to acquire SpotData for wavelength {w_idx}."}
                for field in field_results:
                    f_idx = field["field_index"]
                    field["wavelengths"].append({
                        "wave_index": w_idx,
                        "rms_spot_um": round(float(mono_data.GetRMSSpotSizeFor(f_idx, 1)), 4),
                        "geo_spot_um": round(float(mono_data.GetGeoSpotSizeFor(f_idx, 1)), 4),
                    })
    finally:
        spot.Close()

    return {
        "status": "success",
        "analysis": "Standard Spot Diagram",
        "working_f_number": round(wfno, 4) if wfno > 0 else "N/A",
        "primary_wavelength_um": primary_wave_um,
        "airy_disk_radius_um": round(airy_disk_radius_um, 4),
        "wavelength_detail_included": include_wavelength_detail,
        "fields": field_results,
    }


def zemax_run_fft_mtf(
    max_frequency: float = 100.0,
    sample_size: str = "256x256",
    config: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Run Fast Fourier Transform Modulation Transfer Function (FFT MTF) analysis.
    max_frequency: Maximum spatial frequency in cycles/mm.
    sample_size: Pupil sampling density ('128x128', '256x256', '512x512').
    config: Optional 1-based MCE configuration to evaluate (restored afterwards).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    sample_map = {
        "128x128": zos.Analysis.SampleSizes.S_128x128,
        "256x256": zos.Analysis.SampleSizes.S_256x256,
        "512x512": zos.Analysis.SampleSizes.S_512x512,
    }

    mtf = sys.Analyses.New_FftMtf()
    try:
        settings = _settings(mtf)
        settings.MaximumFrequency = float(max_frequency)
        if sample_size in sample_map:
            settings.SampleSize = sample_map[sample_size]

        res = run_analysis(mtf)

        series_data = []
        num_series = int(res.NumberOfDataSeries)
        # Extract MTF at key spatial frequencies (0, 10, 20, 30, 50, and max)
        key_freq_points = sorted({f for f in (0.0, 10.0, 20.0, 30.0, 50.0, float(max_frequency)) if f <= max_frequency})

        for s_idx in range(num_series):
            series = res.GetDataSeries(s_idx)
            freqs = list(series.XData.Data)
            columns = _matrix_columns(series.YData.Data)
            tan_col = columns[0]
            sag_col = columns[1] if len(columns) > 1 else columns[0]

            sampled_metrics = []
            for target_freq in key_freq_points:
                if not freqs:
                    break
                closest_idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - target_freq))
                sampled_metrics.append({
                    "frequency_lp_mm": round(float(freqs[closest_idx]), 1),
                    "tangential_mtf": round(tan_col[closest_idx], 4),
                    "sagittal_mtf": round(sag_col[closest_idx], 4),
                })

            series_data.append({
                "series_index": s_idx,
                "description": str(series.Description),
                "key_frequencies": sampled_metrics,
            })
    finally:
        mtf.Close()

    return {
        "status": "success",
        "analysis": "FFT MTF",
        "maximum_frequency_lp_mm": max_frequency,
        "series_count": num_series,
        "results": series_data,
    }


def zemax_run_ray_fan(field_index: Optional[int] = None, config: Optional[int] = None) -> Dict[str, Any]:
    """
    Run Ray Fan analysis (transverse ray aberrations Ey vs Py and Ex vs Px).
    Examines spherical aberration, coma, and astigmatism signatures.
    field_index: 1-based field to evaluate; None evaluates all fields.
    config: Optional 1-based MCE configuration to evaluate (restored afterwards).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    num_fields = int(sys.SystemData.Fields.NumberOfFields)
    sd_w = sys.SystemData.Wavelengths
    wave_labels = [float(sd_w.GetWavelength(i).Wavelength) for i in range(1, sd_w.NumberOfWavelengths + 1)]

    fan = sys.Analyses.New_RayFan()
    try:
        try:
            applied_field = _apply_field(_settings(fan), field_index, num_fields)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        res = run_analysis(fan)

        num_series = int(res.NumberOfDataSeries)
        series_info = []
        for s_idx in range(num_series):
            s = res.GetDataSeries(s_idx)
            columns = _matrix_columns(s.YData.Data)
            per_wave = [
                {
                    "wavelength_um": wave_labels[c] if c < len(wave_labels) else None,
                    "max_transverse_error_um": round(_max_abs(col), 4),
                }
                for c, col in enumerate(columns)
            ]
            series_info.append({
                "series_index": s_idx,
                "fan": "tangential" if s_idx % 2 == 0 else "sagittal",
                "description": str(s.Description),
                "pupil_points_count": len(columns[0]) if columns else 0,
                "max_transverse_error_um": round(max((w["max_transverse_error_um"] for w in per_wave), default=0.0), 4),
                "wavelengths": per_wave,
            })
    finally:
        fan.Close()

    return {
        "status": "success",
        "analysis": "Ray Aberration Fan",
        "field_index": applied_field if applied_field is not None else "all",
        "total_curves": num_series,
        "curves_summary": series_info,
    }


def zemax_run_wavefront_map(field_index: int = 1, config: Optional[int] = None) -> Dict[str, Any]:
    """
    Run Wavefront Map analysis to evaluate optical path difference (OPD).
    Returns Peak-to-Valley (PV) error, RMS wavefront error (waves), and estimated Strehl ratio.
    field_index: 1-based field to evaluate.
    config: Optional 1-based MCE configuration to evaluate (restored afterwards).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    rule_checker = OpticalRuleCheck()
    num_fields = int(sys.SystemData.Fields.NumberOfFields)

    wf = sys.Analyses.New_WavefrontMap()
    try:
        try:
            _apply_field(_settings(wf), field_index, num_fields)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        res = run_analysis(wf)

        pv_waves = None
        rms_waves = None
        source = "header"
        for line in (str(l) for l in res.HeaderData.Lines):
            m = _PV_RMS_RE.search(line)
            if m:
                pv_waves = float(m.group(1))
                rms_waves = float(m.group(2))
                break

        if pv_waves is None and int(res.NumberOfDataGrids) > 0:
            # Fallback: statistics over the OPD grid (points outside the pupil are 0)
            source = "data_grid"
            grid = res.GetDataGrid(0).Values
            vals = [
                float(grid[i, j])
                for i in range(grid.GetLength(0))
                for j in range(grid.GetLength(1))
                if math.isfinite(float(grid[i, j])) and float(grid[i, j]) != 0.0
            ]
            if vals:
                mean = sum(vals) / len(vals)
                pv_waves = max(vals) - min(vals)
                rms_waves = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    finally:
        wf.Close()

    if pv_waves is None:
        return {"status": "error", "message": "Failed to extract PV/RMS from Wavefront Map results."}

    # Estimate Strehl ratio
    strehl_ratio = rule_checker.estimate_strehl_ratio(rms_waves)
    is_diffraction_limited = (rms_waves <= 0.072) or (strehl_ratio >= 0.80)

    return {
        "status": "success",
        "analysis": "Wavefront Map",
        "field_index": field_index,
        "value_source": source,
        "peak_to_valley_pv_waves": round(pv_waves, 4),
        "rms_wavefront_error_waves": round(rms_waves, 4),
        "strehl_ratio_estimate": round(strehl_ratio, 4),
        "is_diffraction_limited": is_diffraction_limited,
        "quality_assessment": (
            "Diffraction Limited (Marechal Criterion Satisfied, Strehl >= 0.8)"
            if is_diffraction_limited
            else "Aberration Limited (Strehl < 0.8, further optimization recommended)"
        ),
    }


def zemax_run_field_curvature_distortion(config: Optional[int] = None) -> Dict[str, Any]:
    """
    Run Field Curvature and Distortion analysis.
    Evaluates tangential/sagittal focal shifts and percentage distortion across the field.
    config: Optional 1-based MCE configuration to evaluate (restored afterwards).
    """
    session = ZOSSession.get_instance()
    sys = session.system

    fcd = sys.Analyses.New_FieldCurvatureAndDistortion()
    try:
        res = run_analysis(fcd)

        series_data = []
        num_series = int(res.NumberOfDataSeries)
        for s_idx in range(num_series):
            s = res.GetDataSeries(s_idx)
            field_axis = [float(v) for v in s.XData.Data]
            # Columns: tangential shift, sagittal shift, real height, reference height, distortion (%)
            columns = _matrix_columns(s.YData.Data)
            entry: Dict[str, Any] = {
                "series_index": s_idx,
                "description": str(s.Description),
                "points_count": len(field_axis),
                "max_field": round(max(field_axis), 4) if field_axis else 0.0,
            }
            if len(columns) >= 2:
                entry["max_tangential_field_curvature_mm"] = round(_max_abs(columns[0]), 6)
                entry["max_sagittal_field_curvature_mm"] = round(_max_abs(columns[1]), 6)
                entry["astigmatism_at_max_field_mm"] = round(columns[0][-1] - columns[1][-1], 6)
            if len(columns) >= 5:
                entry["max_distortion_pct"] = round(_max_abs(columns[4]), 4)
                entry["distortion_at_max_field_pct"] = round(columns[4][-1], 4)
            series_data.append(entry)
    finally:
        fcd.Close()

    return {
        "status": "success",
        "analysis": "Field Curvature and Distortion",
        "series_count": num_series,
        "series": series_data,
    }


def zemax_run_analysis_suite(
    analyses: Optional[List[str]] = None,
    max_frequency: float = 100.0,
    sample_size: str = "256x256",
    max_wavefront_fields: int = 5,
    mtf_min_at_30lp_mm: Optional[float] = None,
    distortion_max_pct: Optional[float] = None,
    config: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Run the whole image-quality battery in ONE call and return a compact PASS/WARN/FAIL verdict
    table instead of five raw analysis dumps. Use this after every optimization round; drop to the
    individual analyses only when a row FAILs and you need the detail behind it.

    analyses: any of 'wavefront', 'spot', 'mtf', 'ray_fan', 'field_curvature'. Default: the first
      three. Order given is the order run.
    max_frequency / sample_size: passed to the FFT MTF run.
    max_wavefront_fields: run the wavefront map on at most this many fields (the first N), since the
      verdict is judged on the worst field. Keeps the suite bounded on a many-field system.
    mtf_min_at_30lp_mm: if given, the MTF row is judged against this tangential/sagittal floor at
      30 lp/mm; omit it and the MTF row is reported as INFO (raw numbers, no verdict).
    distortion_max_pct: if given, the field-curvature row FAILs above this |distortion|.
    config: Optional 1-based MCE configuration for the whole suite (restored afterwards).

    Verdicts are judged against the acceptance criteria in the project manual, not against the
    Airy disk: Rayleigh lambda/4 P-V (reference), Marechal lambda/14 RMS wavefront, Strehl >= 0.8
    for diffraction limited. Spot radius vs Airy radius is reported as INFO only -- geometric spot
    size is not an acceptance criterion.
    """
    session = ZOSSession.get_instance()
    sys = session.system

    aliases = {
        "wavefront": "wavefront", "wavefront_map": "wavefront", "opd": "wavefront",
        "spot": "spot", "spot_diagram": "spot",
        "mtf": "mtf", "fft_mtf": "mtf",
        "ray_fan": "ray_fan", "rayfan": "ray_fan",
        "field_curvature": "field_curvature", "field_curvature_distortion": "field_curvature",
        "distortion": "field_curvature",
    }
    if analyses is None:
        requested = ["wavefront", "spot", "mtf"]
    elif not isinstance(analyses, list) or not analyses:
        return {"status": "error", "code": "BAD_ANALYSES",
                "message": "analyses must be a non-empty list of analysis names."}
    else:
        requested = []
        for name in analyses:
            key = aliases.get(str(name).strip().lower().replace(" ", "_"))
            if key is None:
                return {"status": "error", "code": "BAD_ANALYSES",
                        "message": f"Unknown analysis '{name}'. Choose from: wavefront, spot, mtf, "
                                   f"ray_fan, field_curvature."}
            if key not in requested:
                requested.append(key)

    num_fields = int(sys.SystemData.Fields.NumberOfFields)
    num_waves = int(sys.SystemData.Wavelengths.NumberOfWavelengths)
    wave_um = _primary_wavelength_um(sys)
    rule_checker = OpticalRuleCheck()

    # Working F/# drives the diffraction-limit reference. Read it defensively: a system that
    # cannot report WFNO (no merit function yet, or an unsolvable pupil) still gets a suite run,
    # just without the PSF reference.
    try:
        wfno = float(sys.MFE.GetOperandValue(
            session.ZOSAPI.Editors.MFE.MeritOperandType.WFNO, 0, 0, 0, 0, 0, 0, 0, 0))
    except Exception:
        wfno = 0.0

    rows: List[Dict[str, Any]] = []
    # Key numbers the caller actually needs to see, kept out of the raw dumps.
    headline: Dict[str, Any] = {}
    verdicts: List[str] = []
    gated: List[str] = []

    def add_row(analysis: str, verdict: str, criteria: str, evidence: str, detail: Any = None) -> None:
        row = {"analysis": analysis, "verdict": _tone(verdict), "criteria": criteria, "evidence": evidence}
        if detail is not None:
            row["detail"] = detail
        rows.append(row)
        verdicts.append(verdict)
        if verdict != "info":
            gated.append(verdict)

    # ---- Wavefront (the primary acceptance criterion) -------------------------
    if "wavefront" in requested:
        n = max(1, min(int(max_wavefront_fields), num_fields))
        worst = None
        per_field = []
        for f_idx in range(1, n + 1):
            res = _run_wavefront_map(field_index=f_idx)
            if res.get("status") != "success":
                add_row("wavefront", "error", "Marechal lambda/14 RMS, Strehl >= 0.8",
                        str(res.get("message", "wavefront map failed")))
                worst = None
                break
            entry = {
                "field_index": f_idx,
                "pv_waves": res["peak_to_valley_pv_waves"],
                "rms_waves": res["rms_wavefront_error_waves"],
                "strehl": res["strehl_ratio_estimate"],
            }
            per_field.append(entry)
            if worst is None or entry["rms_waves"] > worst["rms_waves"]:
                worst = entry
        if worst is not None:
            rms = worst["rms_waves"]
            strehl = worst["strehl"]
            if rms <= 0.072 and strehl >= 0.8:
                verdict = "pass"
            elif rms <= 0.072 or strehl >= 0.8:
                verdict = "warn"
            else:
                verdict = "fail"
            headline.update({
                "worst_wavefront_field": worst["field_index"],
                "rms_wavefront_waves": rms,
                "strehl_ratio": strehl,
                "pv_waves": worst["pv_waves"],
            })
            add_row("wavefront", verdict,
                    "RMS <= lambda/14 (0.0714), Strehl >= 0.8",
                    f"worst field {worst['field_index']}: RMS {rms:.4f} waves, Strehl {strehl:.3f}, PV {worst['pv_waves']:.4f} waves",
                    per_field if n > 1 else None)

    # ---- Spot (diagnostic; geometric spot is not an acceptance criterion) -----
    if "spot" in requested:
        res = _run_spot_diagram()
        if res.get("status") != "success":
            add_row("spot", "error", "informational (spot vs Airy)", str(res.get("message", "spot failed")))
        else:
            airy = res["airy_disk_radius_um"]
            fields = res["fields"]
            worst = max(fields, key=lambda f: f["polychromatic_rms_spot_um"]) if fields else None
            inside = sum(1 for f in fields if f["is_diffraction_limited"])
            headline.update({
                "airy_radius_um": airy,
                "worst_rms_spot_um": worst["polychromatic_rms_spot_um"] if worst else None,
                "worst_spot_field": worst["field_index"] if worst else None,
                "fields_inside_airy": f"{inside}/{len(fields)}",
            })
            add_row("spot", "info", "informational: RMS spot vs Airy radius (not an acceptance gate)",
                    f"Airy {airy:.3f} um; worst RMS spot {worst['polychromatic_rms_spot_um']:.3f} um at field "
                    f"{worst['field_index']}; {inside}/{len(fields)} fields inside Airy"
                    if worst else f"Airy {airy:.3f} um",
                    None)

    # ---- FFT MTF (judged only if the caller supplies a floor) ----------------
    if "mtf" in requested:
        res = _run_fft_mtf(max_frequency=max_frequency, sample_size=sample_size)
        if res.get("status") != "success":
            add_row("mtf", "error", f"tangential/sagittal >= {mtf_min_at_30lp_mm}" if mtf_min_at_30lp_mm else "informational",
                    str(res.get("message", "MTF failed")))
        else:
            at_30 = []
            for series in res["results"]:
                for point in series["key_frequencies"]:
                    if abs(point["frequency_lp_mm"] - 30.0) <= 5.0:
                        at_30.append(min(point["tangential_mtf"], point["sagittal_mtf"]))
            worst_mtf = min(at_30) if at_30 else None
            headline["min_mtf_at_30lp_mm"] = round(worst_mtf, 4) if worst_mtf is not None else None
            if mtf_min_at_30lp_mm is None or worst_mtf is None:
                add_row("mtf", "info", "informational: MTF at 30 lp/mm (pass mtf_min_at_30lp_mm to gate it)",
                        f"min T/S MTF near 30 lp/mm: {worst_mtf:.4f}" if worst_mtf is not None else "no 30 lp/mm sample in range")
            else:
                verdict = "pass" if worst_mtf >= float(mtf_min_at_30lp_mm) else "fail"
                add_row("mtf", verdict, f"min T/S MTF at 30 lp/mm >= {mtf_min_at_30lp_mm}",
                        f"min {worst_mtf:.4f} across {res['series_count']} series")

    # ---- Ray fan (diagnostic) ------------------------------------------------
    if "ray_fan" in requested:
        res = _run_ray_fan()
        if res.get("status") != "success":
            add_row("ray_fan", "error", "informational (transverse aberration)", str(res.get("message", "ray fan failed")))
        else:
            worst = max((c["max_transverse_error_um"] for c in res["curves_summary"]), default=0.0)
            headline["max_transverse_error_um"] = round(worst, 4)
            add_row("ray_fan", "info", "informational: transverse ray aberration (diagnostic only)",
                    f"max transverse error {worst:.4f} um over {res['total_curves']} curves")

    # ---- Field curvature / distortion ---------------------------------------
    if "field_curvature" in requested:
        res = _run_field_curvature_distortion()
        if res.get("status") != "success":
            add_row("field_curvature", "error", "distortion", str(res.get("message", "field curvature failed")))
        else:
            curves = [s for s in res["series"] if "max_tangential_field_curvature_mm" in s]
            dist = [s.get("max_distortion_pct") for s in res["series"] if s.get("max_distortion_pct") is not None]
            worst_curv = max((s["max_tangential_field_curvature_mm"] for s in curves), default=None)
            worst_dist = max(dist) if dist else None
            headline["max_field_curvature_mm"] = worst_curv
            headline["max_distortion_pct"] = worst_dist
            curvature_txt = f"max |tangential| {worst_curv:.4f} mm" if worst_curv is not None else "no curvature data"
            dist_txt = f"max |distortion| {worst_dist:.4f}%" if worst_dist is not None else "no distortion data"
            if distortion_max_pct is None or worst_dist is None:
                add_row("field_curvature", "info",
                        "informational: field curvature / distortion (pass distortion_max_pct to gate distortion)",
                        f"{curvature_txt}; {dist_txt}")
            else:
                verdict = "pass" if worst_dist <= float(distortion_max_pct) else "fail"
                add_row("field_curvature", verdict, f"|distortion| <= {distortion_max_pct}%",
                        f"{dist_txt}; {curvature_txt}")

    overall = _worst_verdict(verdicts) if verdicts else "info"
    return {
        "status": "success",
        "analysis": "Analysis Suite",
        "overall": _tone(overall),
        "config": config,
        "fields_in_model": num_fields,
        "wavelengths_in_model": num_waves,
        "primary_wavelength_um": wave_um,
        "working_f_number": round(wfno, 4) if wfno > 0 else "N/A",
        "diffraction_limit_psf_um": round(rule_checker.calculate_airy_disk_radius_um(wave_um, wfno), 4),
        "headline": headline,
        "verdicts": rows,
        "criteria_note": (
            "PASS/FAIL rows use the project acceptance criteria (RMS wavefront lambda/14, Strehl >= 0.8). "
            "INFO rows carry numbers with no verdict. Geometric spot radius vs the Airy radius is NOT "
            "an acceptance criterion in this project -- judge the design on wavefront/MTF/Strehl."
        ),
    }


def _wavelength_color(wl_um: float) -> str:
    """Approximate visible color of a wavelength (for spot diagram ray markers)."""
    nm = wl_um * 1000.0
    if nm < 440:
        r, g, b = (440 - nm) / 60, 0.0, 1.0
    elif nm < 490:
        r, g, b = 0.0, (nm - 440) / 50, 1.0
    elif nm < 510:
        r, g, b = 0.0, 1.0, (510 - nm) / 20
    elif nm < 580:
        r, g, b = (nm - 510) / 70, 1.0, 0.0
    elif nm < 645:
        r, g, b = 1.0, (645 - nm) / 65, 0.0
    else:
        r, g, b = 1.0, 0.0, 0.0
    # Darken slightly so pure yellow/green markers stay readable on white
    r, g, b = (max(0.0, min(1.0, c)) * 0.85 for c in (r, g, b))
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def _hexapolar_pupil(rings: int) -> List[tuple]:
    """Zemax-style hexapolar pupil sampling: ring k holds 6k points."""
    pts = [(0.0, 0.0)]
    for k in range(1, rings + 1):
        rho = k / rings
        for j in range(6 * k):
            theta = 2.0 * math.pi * j / (6 * k)
            pts.append((rho * math.cos(theta), rho * math.sin(theta)))
    return pts


def zemax_export_spot_diagram_plot(
    rings: int = 12,
    filename: str = "spot_diagram.png",
    config: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Trace a hexapolar pupil grid for every field and wavelength (Batch Ray Trace) and render
    a Zemax-style spot diagram PNG with the Airy disk circle, referenced to the primary
    wavelength chief ray. Saved into the active project's reports/ folder.
    rings: Number of hexapolar pupil rings (ring k holds 6k rays).
    filename: Output PNG file name (or path relative to reports/).
    config: Optional 1-based MCE configuration to trace (restored afterwards).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from tools.project_manager import resolve_project_file_path

    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    rule_checker = OpticalRuleCheck()

    sd = sys.SystemData
    num_fields = int(sd.Fields.NumberOfFields)
    num_waves = int(sd.Wavelengths.NumberOfWavelengths)
    fields = [sd.Fields.GetField(f) for f in range(1, num_fields + 1)]
    max_field = max(max(abs(float(f.X)), abs(float(f.Y))) for f in fields) or 1.0
    waves = [float(sd.Wavelengths.GetWavelength(w).Wavelength) for w in range(1, num_waves + 1)]
    primary_idx = next(
        (w for w in range(1, num_waves + 1) if sd.Wavelengths.GetWavelength(w).IsPrimary), 1
    )
    primary_wave_um = waves[primary_idx - 1]
    wfno = float(sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.WFNO, 0, 0, 0, 0, 0, 0, 0, 0))
    airy_um = rule_checker.calculate_airy_disk_radius_um(primary_wave_um, wfno)

    pupil = _hexapolar_pupil(max(1, int(rings)))
    image_surf = sys.LDE.NumberOfSurfaces - 1
    opd_none = getattr(zos.Tools.RayTrace.OPDMode, "None")

    # Ray order per field: chief ray (primary wave), then every wave x pupil point
    rays_per_field = 1 + num_waves * len(pupil)
    rt = session.open_tool("OpenBatchRayTrace")
    try:
        norm = rt.CreateNormUnpol(num_fields * rays_per_field, zos.Tools.RayTrace.RaysType.Real, image_surf)
        for f in fields:
            hx, hy = float(f.X) / max_field, float(f.Y) / max_field
            norm.AddRay(primary_idx, hx, hy, 0.0, 0.0, opd_none)
            for w in range(1, num_waves + 1):
                for px, py in pupil:
                    norm.AddRay(w, hx, hy, px, py, opd_none)
        rt.RunAndWaitForCompletion()
        norm.StartReadingResults()
        raw = []
        for _ in range(num_fields * rays_per_field):
            r = norm.ReadNextResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            ok, err, vig, x, y = r[0], r[2], r[3], r[4], r[5]
            raw.append((x, y) if ok and err == 0 and vig == 0 else None)
    finally:
        rt.Close()

    field_results = []
    plot_data = []
    for f_i, f in enumerate(fields):
        block = raw[f_i * rays_per_field:(f_i + 1) * rays_per_field]
        chief = block[0]
        if chief is None:
            return {"status": "error", "message": f"Chief ray of field {f_i + 1} failed to trace."}
        per_wave = []
        all_pts = []
        for w_i in range(num_waves):
            seg = block[1 + w_i * len(pupil):1 + (w_i + 1) * len(pupil)]
            pts = [((p[0] - chief[0]) * 1000.0, (p[1] - chief[1]) * 1000.0) for p in seg if p is not None]
            per_wave.append(pts)
            all_pts.extend(pts)
        if not all_pts:
            return {"status": "error", "message": f"All rays of field {f_i + 1} were vignetted or failed."}
        n = len(all_pts)
        cx = sum(p[0] for p in all_pts) / n
        cy = sum(p[1] for p in all_pts) / n
        rms_chief = math.sqrt(sum(p[0] ** 2 + p[1] ** 2 for p in all_pts) / n)
        rms_centroid = math.sqrt(sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2 for p in all_pts) / n)
        geo = max(math.hypot(p[0], p[1]) for p in all_pts)
        field_results.append({
            "field_index": f_i + 1,
            "field": [float(f.X), float(f.Y)],
            "rays_traced": n,
            "rays_vignetted": num_waves * len(pupil) - n,
            "rms_radius_chief_um": round(rms_chief, 4),
            "rms_radius_centroid_um": round(rms_centroid, 4),
            "geo_radius_um": round(geo, 4),
            "all_rays_inside_airy": geo <= airy_um,
        })
        plot_data.append((f, chief, per_wave))

    # Common scale for every field, large enough to show the full Airy circle
    half = 1.25 * max([airy_um] + [fr["geo_radius_um"] for fr in field_results])
    half = float(f"{half:.2g}")
    cols = min(num_fields, 3)
    rows = int(math.ceil(num_fields / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 4.6 * rows + 0.9), squeeze=False)
    for ax in axes.flat[num_fields:]:
        ax.axis("off")

    field_unit = "°" if "angle" in str(sd.Fields.GetFieldType()).lower() else " mm"
    for ax, (f, chief, per_wave), fr in zip(axes.flat, plot_data, field_results):
        for w_i, pts in enumerate(per_wave):
            ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=6, marker="+", linewidths=0.6,
                       color=_wavelength_color(waves[w_i]), label=f"{waves[w_i]:.4f} µm")
        ax.add_patch(plt.Circle((0, 0), airy_um, fill=False, color="black", lw=0.9))
        ax.set_xlim(-half, half)
        ax.set_ylim(-half, half)
        ax.set_aspect("equal")
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.25, lw=0.5)
        ax.set_title(f"Field {fr['field_index']}: {float(f.Y):.3g}{field_unit}", fontsize=10)
        ax.set_xlabel(
            f"RMS {fr['rms_radius_chief_um']:.3f} µm | GEO {fr['geo_radius_um']:.3f} µm\n"
            f"Image ({chief[0]:.4f}, {chief[1]:.4f}) mm",
            fontsize=8,
        )
    axes.flat[0].legend(fontsize=7, loc="upper right", markerscale=2)
    fig.suptitle(
        f"Spot Diagram  |  F/{wfno:.2f}  |  Airy radius {airy_um:.3f} µm (circle)  |  "
        f"box ±{half:g} µm  |  ref: chief ray",
        fontsize=10,
    )
    fig.tight_layout()

    out_path = resolve_project_file_path(filename, "spot_diagram.png", subfolder="reports")
    fig.savefig(out_path, dpi=200, facecolor="white")
    plt.close(fig)

    return {
        "status": "success",
        "analysis": "Spot Diagram Plot (Batch Ray Trace)",
        "image_file": out_path,
        "working_f_number": round(wfno, 4),
        "primary_wavelength_um": primary_wave_um,
        "airy_disk_radius_um": round(airy_um, 4),
        "pupil_rays_per_wavelength": len(pupil),
        "plot_half_width_um": half,
        "all_fields_inside_airy": all(fr["all_rays_inside_airy"] for fr in field_results),
        "fields": field_results,
    }


# Unwrapped implementations, captured before the decorators go on. zemax_run_analysis_suite
# drives the whole battery under ONE configuration switch and ONE lock acquisition, so it must
# call these bodies rather than the public tools (which would re-enter with_configuration and
# re-open the analysis -- the exact per-call overhead the suite exists to remove).
_run_spot_diagram = zemax_run_spot_diagram
_run_fft_mtf = zemax_run_fft_mtf
_run_ray_fan = zemax_run_ray_fan
_run_wavefront_map = zemax_run_wavefront_map
_run_field_curvature_distortion = zemax_run_field_curvature_distortion

for _name in (
    "zemax_run_spot_diagram", "zemax_run_fft_mtf", "zemax_run_ray_fan",
    "zemax_run_wavefront_map", "zemax_run_field_curvature_distortion",
    "zemax_export_spot_diagram_plot", "zemax_run_analysis_suite",
):
    globals()[_name] = serialized_operation(with_configuration(globals()[_name]))
