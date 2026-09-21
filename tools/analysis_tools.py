"""
Zemax Optical Analysis Tools
Executes optical performance evaluations: Spot Diagram, FFT MTF, Ray Fan,
Wavefront Map, and Field Curvature / Distortion.
"""

import os
import re
import tempfile
from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession
from domain.zemax_rules import OpticalRuleCheck


def zemax_run_spot_diagram(field_index: Optional[int] = None) -> Dict[str, Any]:
    """
    Run Standard Spot Diagram analysis across fields and wavelengths.
    Returns RMS spot radius, GEO maximum radius, Airy disk radius, and diffraction limit diagnosis.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    rule_checker = OpticalRuleCheck()

    spot = sys.Analyses.New_StandardSpot()
    spot.ApplyAndWaitForCompletion()
    res = spot.GetResults()

    spot_data = res.SpotData
    if not spot_data:
        spot.Close()
        return {"status": "error", "message": "Failed to acquire SpotData."}

    num_fields = int(spot_data.NumberOfFields)
    num_waves = int(spot_data.NumberOfWavelengths)

    # Calculate Airy disk radius for comparison
    primary_wave_um = 0.55
    sd_w = sys.SystemData.Wavelengths
    for w_i in range(1, sd_w.NumberOfWavelengths + 1):
        wave_item = sd_w.GetWavelength(w_i)
        if wave_item.IsPrimary:
            primary_wave_um = float(wave_item.Wavelength)
            break
    try:
        wfno = float(sys.MFE.GetOperandValue(zos.Editors.MFE.MeritOperandType.WFNO, 1, 0, 0, 0, 0, 0, 0, 0))
    except Exception:
        wfno = 0.0

    airy_disk_radius_um = rule_checker.calculate_airy_disk_radius_um(primary_wave_um, wfno)

    field_results = []
    target_fields = [field_index] if field_index is not None else range(1, num_fields + 1)

    for f_idx in target_fields:
        if 1 <= f_idx <= num_fields:
            rms_poly = float(spot_data.GetRMSSpotSizeFor(f_idx, 0))  # 0 is polychromatic
            geo_poly = float(spot_data.GetGeoSpotSizeFor(f_idx, 0))
            
            wave_breakdown = []
            for w_idx in range(1, num_waves + 1):
                wave_breakdown.append({
                    "wave_index": w_idx,
                    "rms_spot_um": round(float(spot_data.GetRMSSpotSizeFor(f_idx, w_idx)), 4),
                    "geo_spot_um": round(float(spot_data.GetGeoSpotSizeFor(f_idx, w_idx)), 4),
                })

            is_diffraction_limited = (rms_poly <= airy_disk_radius_um) if airy_disk_radius_um > 0 else False

            field_results.append({
                "field_index": f_idx,
                "polychromatic_rms_spot_um": round(rms_poly, 4),
                "polychromatic_geo_spot_um": round(geo_poly, 4),
                "airy_disk_radius_um": round(airy_disk_radius_um, 4),
                "is_diffraction_limited": is_diffraction_limited,
                "wavelengths": wave_breakdown,
            })

    spot.Close()

    return {
        "status": "success",
        "analysis": "Standard Spot Diagram",
        "working_f_number": round(wfno, 4) if wfno > 0 else "N/A",
        "primary_wavelength_um": primary_wave_um,
        "airy_disk_radius_um": round(airy_disk_radius_um, 4),
        "fields": field_results,
    }


def zemax_run_fft_mtf(
    max_frequency: float = 100.0,
    sample_size: str = "256x256",
) -> Dict[str, Any]:
    """
    Run Fast Fourier Transform Modulation Transfer Function (FFT MTF) analysis.
    max_frequency: Maximum spatial frequency in cycles/mm.
    sample_size: Pupil sampling density ('128x128', '256x256', '512x512').
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    mtf = sys.Analyses.New_FftMtf()
    settings = mtf.GetSettings()
    settings.MaximumFrequency = float(max_frequency)

    sample_map = {
        "128x128": zos.Analysis.SampleSizes.S_128x128,
        "256x256": zos.Analysis.SampleSizes.S_256x256,
        "512x512": zos.Analysis.SampleSizes.S_512x512,
    }
    if sample_size in sample_map:
        settings.SampleSize = sample_map[sample_size]

    mtf.ApplyAndWaitForCompletion()
    res = mtf.GetResults()

    series_data = []
    num_series = int(res.NumberOfDataSeries)

    for s_idx in range(num_series):
        series = res.GetDataSeries(s_idx)
        freqs = list(series.XData.Data)
        y_raw = series.YData.Data
        n_pts = y_raw.GetLength(0)

        # Extract MTF at key spatial frequencies (0, 10, 30, 50, and max)
        key_freq_points = [0.0, 10.0, 20.0, 30.0, 50.0, max_frequency]
        sampled_metrics = []

        for target_freq in key_freq_points:
            if target_freq <= max_frequency and freqs:
                # Find closest index
                closest_idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - target_freq))
                actual_f = freqs[closest_idx]
                t_val = float(y_raw[closest_idx, 0])
                s_val = float(y_raw[closest_idx, 1])
                sampled_metrics.append({
                    "frequency_lp_mm": round(actual_f, 1),
                    "tangential_mtf": round(t_val, 4),
                    "sagittal_mtf": round(s_val, 4),
                })

        series_data.append({
            "series_index": s_idx,
            "description": str(series.Description),
            "key_frequencies": sampled_metrics,
        })

    mtf.Close()

    return {
        "status": "success",
        "analysis": "FFT MTF",
        "maximum_frequency_lp_mm": max_frequency,
        "series_count": num_series,
        "results": series_data,
    }


def zemax_run_ray_fan(field_index: int = 1) -> Dict[str, Any]:
    """
    Run Ray Fan analysis (transverse ray aberrations Ey vs Py and Ex vs Px).
    Examines spherical aberration, coma, and astigmatism signatures.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    fan = sys.Analyses.New_RayFan()
    fan.ApplyAndWaitForCompletion()
    res = fan.GetResults()

    num_series = int(res.NumberOfDataSeries)
    series_info = []

    for s_idx in range(min(4, num_series)):
        s = res.GetDataSeries(s_idx)
        x_data = list(s.XData.Data)
        y_data = list(s.YData.Data)
        max_aberration = max(abs(y) for y in y_data) if y_data else 0.0
        
        series_info.append({
            "series_index": s_idx,
            "description": str(s.Description),
            "pupil_points_count": len(x_data),
            "max_transverse_error_um": round(float(max_aberration), 4),
        })

    fan.Close()

    return {
        "status": "success",
        "analysis": "Ray Aberration Fan",
        "field_index": field_index,
        "total_curves": num_series,
        "curves_summary": series_info,
    }


def zemax_run_wavefront_map(field_index: int = 1) -> Dict[str, Any]:
    """
    Run Wavefront Map analysis to evaluate optical path difference (OPD).
    Returns Peak-to-Valley (PV) error, RMS wavefront error (waves), and estimated Strehl ratio.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    rule_checker = OpticalRuleCheck()

    wf = sys.Analyses.New_WavefrontMap()
    wf.ApplyAndWaitForCompletion()
    res = wf.GetResults()

    # Parse Header for PV and RMS
    pv_waves = 0.0
    rms_waves = 0.0

    lines = [str(line) for line in res.HeaderData.Lines]
    for line in lines:
        # Match lines like "峰到谷 = 1.2345 波, RMS = 0.1234波" or English equivalents
        m = re.search(r"=\s*([0-9.]+).*RMS\s*=\s*([0-9.]+)", line)
        if m:
            pv_waves = float(m.group(1))
            rms_waves = float(m.group(2))
            break

    wf.Close()

    # Estimate Strehl ratio
    strehl_ratio = rule_checker.estimate_strehl_ratio(rms_waves)
    is_diffraction_limited = (rms_waves <= 0.072) or (strehl_ratio >= 0.80)

    return {
        "status": "success",
        "analysis": "Wavefront Map",
        "field_index": field_index,
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


def zemax_run_field_curvature_distortion() -> Dict[str, Any]:
    """
    Run Field Curvature and Distortion analysis.
    Evaluates tangential/sagittal focal shifts and percentage distortion across the field.
    """
    session = ZOSSession.get_instance()
    sys = session.system

    fcd = sys.Analyses.New_FieldCurvatureAndDistortion()
    fcd.ApplyAndWaitForCompletion()
    res = fcd.GetResults()

    series_data = []
    num_series = int(res.NumberOfDataSeries)
    for s_idx in range(num_series):
        s = res.GetDataSeries(s_idx)
        series_data.append({
            "series_index": s_idx,
            "description": str(s.Description),
            "points_count": len(list(s.XData.Data)),
        })

    fcd.Close()

    return {
        "status": "success",
        "analysis": "Field Curvature and Distortion",
        "series_count": num_series,
        "series": series_data,
    }
