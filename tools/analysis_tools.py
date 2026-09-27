"""
Zemax Optical Analysis Tools
Executes optical performance evaluations: Spot Diagram, FFT MTF, Ray Fan,
Wavefront Map, and Field Curvature / Distortion.
"""

import math
import re
from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession
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


def zemax_run_spot_diagram(field_index: Optional[int] = None) -> Dict[str, Any]:
    """
    Run Standard Spot Diagram analysis across fields and wavelengths.
    Returns RMS spot radius, GEO maximum radius, Airy disk radius, and diffraction limit diagnosis.
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
        spot.ApplyAndWaitForCompletion()
        res = spot.GetResults()

        spot_data = res.SpotData
        if not spot_data:
            return {"status": "error", "message": "Failed to acquire SpotData."}

        num_fields = int(spot_data.NumberOfFields)
        num_waves = int(spot_data.NumberOfWavelengths)

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
    finally:
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

        mtf.ApplyAndWaitForCompletion()
        res = mtf.GetResults()

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


def zemax_run_ray_fan(field_index: Optional[int] = None) -> Dict[str, Any]:
    """
    Run Ray Fan analysis (transverse ray aberrations Ey vs Py and Ex vs Px).
    Examines spherical aberration, coma, and astigmatism signatures.
    field_index: 1-based field to evaluate; None evaluates all fields.
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

        fan.ApplyAndWaitForCompletion()
        res = fan.GetResults()

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


def zemax_run_wavefront_map(field_index: int = 1) -> Dict[str, Any]:
    """
    Run Wavefront Map analysis to evaluate optical path difference (OPD).
    Returns Peak-to-Valley (PV) error, RMS wavefront error (waves), and estimated Strehl ratio.
    field_index: 1-based field to evaluate.
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

        wf.ApplyAndWaitForCompletion()
        res = wf.GetResults()

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


def zemax_run_field_curvature_distortion() -> Dict[str, Any]:
    """
    Run Field Curvature and Distortion analysis.
    Evaluates tangential/sagittal focal shifts and percentage distortion across the field.
    """
    session = ZOSSession.get_instance()
    sys = session.system

    fcd = sys.Analyses.New_FieldCurvatureAndDistortion()
    try:
        fcd.ApplyAndWaitForCompletion()
        res = fcd.GetResults()

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
) -> Dict[str, Any]:
    """
    Trace a hexapolar pupil grid for every field and wavelength (Batch Ray Trace) and render
    a Zemax-style spot diagram PNG with the Airy disk circle, referenced to the primary
    wavelength chief ray. Saved into the active project's reports/ folder.
    rings: Number of hexapolar pupil rings (ring k holds 6k rays).
    filename: Output PNG file name (or path relative to reports/).
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
