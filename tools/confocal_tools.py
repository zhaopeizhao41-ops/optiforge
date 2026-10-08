"""
Reflectance-confocal (RCM) model tools: a tissue stack imaged at several depths, and the mechanical
envelope of the optical train for handpiece design.
Tissue refractive indices are always user-supplied; a common reference for human skin is
Ding et al. 2006, Phys. Med. Biol. 51(6):1479-1489, doi:10.1088/0031-9155/51/6/008.
"""

import math
from typing import Any, Callable, Dict, List, Optional
from core.zos_session import ZOSSession
from core.operation_guard import model_operation, serialized_operation
from tools.config_tools import resize_configs, write_mce_row


def _secant(f: Callable[[float], float], x0: float, x1: float, tol: float = 1e-7, max_iter: int = 30) -> float:
    """Root of f by the secant method (these optics are near-linear, so 1-3 steps usually suffice)."""
    f0, f1 = f(x0), f(x1)
    for _ in range(max_iter):
        if abs(f1) <= tol:
            return x1
        if f1 == f0:
            break
        x0, f0, x1 = x1, f1, x1 - f1 * (x1 - x0) / (f1 - f0)
        f1 = f(x1)
    if abs(f1) <= tol:
        return x1
    raise RuntimeError(f"Secant solve did not converge (residual {f1:.3g}).")


def _operand(session, code: str, *params) -> float:
    """Evaluate one merit-function operand without adding it to the MFE."""
    op_type = getattr(session.ZOSAPI.Editors.MFE.MeritOperandType, code)
    args = list(params) + [0] * (8 - len(params))
    return float(session.system.MFE.GetOperandValue(op_type, *args))


def _primary_wave(system) -> int:
    waves = system.SystemData.Wavelengths
    return next((i for i in range(1, waves.NumberOfWavelengths + 1) if waves.GetWavelength(i).IsPrimary), 1)


def _layer_error(layer: Any, label: str, needs_thickness: bool) -> Optional[str]:
    if not isinstance(layer, dict) or ("n" in layer) == ("material" in layer):
        return f"{label} needs exactly one of 'n' (index at the primary wavelength) or 'material' (catalog name)."
    if (needs_thickness or "thickness_um" in layer) and not float(layer.get("thickness_um", -1)) >= 0:
        return f"{label} needs thickness_um >= 0."
    return None


def _apply_material(session, index: int, layer: Dict[str, Any], wave: int) -> Dict[str, Any]:
    """Catalog glass by name, or a model glass whose nd is solved so INDX hits layer['n'] at `wave`."""
    surf = session.system.LDE.GetSurfaceAt(index)
    if "material" in layer:
        surf.Material = str(layer["material"]).strip()
        if str(surf.Material).upper() != str(layer["material"]).strip().upper():
            raise ValueError(f"Material '{layer['material']}' was not accepted on surface {index}.")
        return {"surface": index, "material": str(surf.Material),
                "n": round(_operand(session, "INDX", index, wave), 6)}
    target, vd = float(layer["n"]), float(layer.get("vd", 55.0))
    solve = surf.MaterialCell.CreateSolveType(session.ZOSAPI.Editors.SolveType.MaterialModel)
    model = solve._S_MaterialModel
    model.AbbeVd = vd

    def residual(nd: float) -> float:
        model.IndexNd = nd
        surf.MaterialCell.SetSolveData(solve)
        return _operand(session, "INDX", index, wave) - target

    nd = _secant(residual, target, target + 0.01, tol=1e-7)
    residual(nd)
    return {"surface": index, "model_nd": round(nd, 6), "vd": vd,
            "n": round(_operand(session, "INDX", index, wave), 6)}


def _tissue_split(thicknesses: List[Optional[float]], depth: float) -> List[float]:
    """Per-layer thickness (mm) so the focus sits `depth` below the tissue surface; the last layer
    takes the remainder (it is also capped by its own thickness when one is given)."""
    out, left = [], depth
    for t in thicknesses[:-1]:
        out.append(min(left, t))
        left -= out[-1]
    if thicknesses[-1] is not None and left > thicknesses[-1] + 1e-12:
        raise ValueError(f"Depth {depth * 1000:g} um is deeper than the tissue stack.")
    return out + [left]


def zemax_setup_tissue_stack(
    gap_surface: int,
    tissue_layers: List[Dict[str, Any]],
    depths_um: List[float],
    cover_layers: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Insert the sample stack after the immersion gap and make one configuration per imaging depth.
    gap_surface: immersion medium directly before the image (e.g. 3 mm WATER); its thickness is
      re-solved per depth so the paraxial marginal ray focuses on the image plane.
    cover_layers: fixed layers in order (window, gel): {"n": 1.51 | "material": "N-BK7",
      "thickness_um": 170, "vd": 55}.
    tissue_layers: stratum corneum, epidermis, dermis ... with the same keys; the last layer may
      omit thickness_um and absorbs the remaining depth.
    depths_um: focus depths below the tissue surface.
    n is the index at the primary wavelength (skin values depend on wavelength and site; see the
    Ding 2006 reference in the module docstring).
    """
    session = ZOSSession.get_instance()
    system = session.system
    lde, mce = system.LDE, system.MCE
    if str(system.SystemData.Units.LensUnits) != "Millimeters":
        return {"status": "error", "message": "Lens units must be millimeters."}
    if gap_surface != int(lde.NumberOfSurfaces) - 2:
        return {"status": "error", "message": f"gap_surface must be the surface just before the image "
                                              f"({int(lde.NumberOfSurfaces) - 2})."}
    if str(lde.GetSurfaceAt(gap_surface).ThicknessCell.GetSolveData().Type) not in ("Fixed", "Variable"):
        return {"status": "error", "message": f"Remove the thickness solve on surface {gap_surface} first."}
    cover = list(cover_layers or [])
    checks = [(l, f"cover_layers[{i}]", True) for i, l in enumerate(cover)]
    checks += [(l, f"tissue_layers[{i}]", i < len(tissue_layers) - 1) for i, l in enumerate(tissue_layers)]
    errors = [e for e in (_layer_error(*c) for c in checks) if e]
    if not tissue_layers or errors:
        return {"status": "error", "message": errors[0] if errors else "tissue_layers must not be empty."}
    n_cfg = int(mce.NumberOfConfigurations)
    if n_cfg not in (1, len(depths_um)):
        return {"status": "error", "message": f"System has {n_cfg} configurations but {len(depths_um)} depths."}
    tissue_t = [float(l["thickness_um"]) / 1000.0 if "thickness_um" in l else None for l in tissue_layers]
    try:
        splits = [_tissue_split(tissue_t, float(d) / 1000.0) for d in depths_um]
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    wave = _primary_wave(system)
    gap = lde.GetSurfaceAt(gap_surface)
    gap0 = float(gap.Thickness)
    layers = cover + list(tissue_layers)
    for _ in layers:
        lde.InsertNewSurfaceAt(gap_surface + 1)
    applied = [_apply_material(session, gap_surface + 1 + i, l, wave) for i, l in enumerate(layers)]
    for i, layer in enumerate(cover):
        lde.GetSurfaceAt(gap_surface + 1 + i).Thickness = float(layer["thickness_um"]) / 1000.0
    tissue = [gap_surface + 1 + len(cover) + i for i in range(len(tissue_layers))]
    image = int(lde.NumberOfSurfaces) - 1

    def residual(t: float) -> float:
        gap.Thickness = t
        return _operand(session, "PARY", image, wave, 0, 0, 0, 1)

    gaps = []
    for depth, split in zip(depths_um, splits):
        for s, t in zip(tissue, split):
            lde.GetSurfaceAt(s).Thickness = t
        g = _secant(residual, gap0, gap0 + 0.01, tol=1e-8)
        if g < 0:
            return {"status": "error", "message": f"Depth {depth} um needs a negative immersion gap "
                                                  f"({g:.4f} mm): working distance too short for this stack."}
        residual(g)
        gaps.append(g)

    resize_configs(mce, len(depths_um))
    zos = session.ZOSAPI
    rows = {"gap": write_mce_row(zos, mce, "THIC", gaps, gap_surface)[0]}
    for k, s in enumerate(tissue):
        rows[f"tissue_{k}"] = write_mce_row(zos, mce, "THIC", [sp[k] for sp in splits], s)[0]
    mce.SetCurrentConfiguration(1)
    configs = [{"config": c + 1, "depth_um": float(d), "gap_mm": round(g, 6),
                "gap_change_um": round((g - gap0) * 1000.0, 3)} for c, (d, g) in enumerate(zip(depths_um, gaps))]
    return {"status": "success", "gap_surface": gap_surface, "layers": applied, "tissue_surfaces": tissue,
            "image_surface": image, "n_configs": int(mce.NumberOfConfigurations), "mce_rows": rows,
            "configs": configs}


def _global_frame(lde, index: int):
    """(R, t): local -> global is R @ p + t (R row-major 3x3)."""
    g = list(lde.GetGlobalMatrix(index, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))[1:]
    return [g[0:3], g[3:6], g[6:9]], g[9:12]


def zemax_get_envelope(first_surface: Optional[int] = None, last_surface: Optional[int] = None,
                       frame_surface: Optional[int] = None, rim_points: int = 36) -> Dict[str, Any]:
    """
    Bounding box of the optical train for handpiece design: surface vertices plus rim points
    (semi-diameter, with sag) of every non-coordinate-break surface from first to last
    (default 1 .. image-1). Coordinates are global, or in the local frame of frame_surface.
    """
    session = ZOSSession.get_instance()
    lde = session.system.LDE
    n = int(lde.NumberOfSurfaces)
    first = 1 if first_surface is None else first_surface
    last = n - 2 if last_surface is None else last_surface
    if not 0 <= first <= last < n or (frame_surface is not None and not 0 <= frame_surface < n):
        return {"status": "error", "message": f"Surface range must satisfy 0 <= first <= last < {n}."}
    fr, ft = _global_frame(lde, frame_surface) if frame_surface is not None else ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0])

    def to_frame(R, t, p):
        g = [sum(R[i][j] * p[j] for j in range(3)) + t[i] - ft[i] for i in range(3)]
        return [sum(fr[j][i] * g[j] for j in range(3)) for i in range(3)]

    points, vertices, path, widest = [], [], 0.0, (0.0, None)
    for s in range(first, last + 1):
        surf = lde.GetSurfaceAt(s)
        if s < last and math.isfinite(float(surf.Thickness)):
            path += abs(float(surf.Thickness))
        if str(surf.Type) == "CoordinateBreak":
            continue
        R, t = _global_frame(lde, s)
        vertex = to_frame(R, t, [0.0, 0.0, 0.0])
        axis = [a - v for a, v in zip(to_frame(R, t, [0.0, 0.0, 1.0]), vertex)]
        sd = float(surf.SemiDiameter)
        vertices.append({"surface": s, "xyz": [round(v, 4) for v in vertex], "axis": [round(a, 5) for a in axis],
                         "semi_diameter": round(sd, 4) if math.isfinite(sd) else None})
        points.append(vertex)
        if not (math.isfinite(sd) and sd > 0):
            continue
        widest = max(widest, (sd, s), key=lambda w: w[0])
        for k in range(rim_points):
            a = 2.0 * math.pi * k / rim_points
            x, y = sd * math.cos(a), sd * math.sin(a)
            points.append(to_frame(R, t, [x, y, _operand(session, "SSAG", s, 0, x, y)]))
    if not points:
        return {"status": "error", "message": "No physical surfaces in the requested range."}
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    return {"status": "success", "frame": "global" if frame_surface is None else f"surface {frame_surface}",
            "surfaces": [first, last], "bbox_min": [round(v, 4) for v in lo], "bbox_max": [round(v, 4) for v in hi],
            "size": [round(h - l, 4) for h, l in zip(hi, lo)], "max_clear_diameter": round(2.0 * widest[0], 4),
            "widest_surface": widest[1], "path_length": round(path, 4), "vertices": vertices}


zemax_setup_tissue_stack = model_operation(zemax_setup_tissue_stack)
zemax_get_envelope = serialized_operation(zemax_get_envelope)


def zemax_run_huygens_psf(session, field=1, wavelength=1, config=None):
    """Run Huygens PSF analysis and compute lateral FWHM and Strehl ratio.

    Args:
        field: 1-based field number (default 1, on-axis).
        wavelength: 1-based wavelength number (default 1).
        config: Optional 1-based configuration number.

    Returns:
        {"status": "success", "fwhm_um": <lateral FWHM>, "strehl": <Strehl ratio>,
         "airy_fwhm_um": <diffraction limit 0.51*lambda/NA>, "field": <field>,
         "wavelength_nm": <wavelength>, "na": <numerical aperture>}
    """
    from core.analysis_runner import run_analysis, configuration

    system = session.system
    with configuration(system, config):
        # Get wavelength
        wave_um = system.SystemData.Wavelengths.GetWavelength(wavelength).Wavelength

        # Get NA from system aperture (if ImageSpaceNA type)
        sysa = system.SystemData.Aperture
        img_space_na = 0.0
        try:
            if sysa.ApertureType.ToString() == "ImageSpaceNA":
                img_space_na = float(sysa.ApertureValue)
        except:
            pass

        # Compute Airy FWHM if NA available
        airy_fwhm_um = 0.51 * wave_um / img_space_na if img_space_na > 0 else 0

        hps = system.Analyses.New_HuygensPsf()
        run_analysis(hps, timeout_s=120)
        results = hps.GetResults()
        dg = results.DataGrids[0]
        vals = dg.Values
        nx, ny = vals.GetLength(0), vals.GetLength(1)
        dx_um, dy_um = dg.Dx, dg.Dy

        # Find peak
        peak_val = 0.0
        peak_i, peak_j = nx // 2, ny // 2
        for i in range(nx):
            for j in range(ny):
                v = vals[i, j]
                if v > peak_val:
                    peak_val, peak_i, peak_j = v, i, j

        # FWHM: find half-max crossings along x and y
        half = peak_val / 2.0
        fwhm_x = fwhm_y = 0.0

        # X direction (j varies)
        left = right = peak_j
        for j in range(peak_j, -1, -1):
            if vals[peak_i, j] < half:
                left = j
                break
        for j in range(peak_j, ny):
            if vals[peak_i, j] < half:
                right = j
                break
        fwhm_x = abs(right - left) * dx_um

        # Y direction (i varies)
        bottom = top = peak_i
        for i in range(peak_i, -1, -1):
            if vals[i, peak_j] < half:
                bottom = i
                break
        for i in range(peak_i, nx):
            if vals[i, peak_j] < half:
                top = i
                break
        fwhm_y = abs(top - bottom) * dy_um

        fwhm_um = (fwhm_x + fwhm_y) / 2.0
        strehl = peak_val  # Peak is normalized Strehl

        hps.Close()
        result = {"status": "success", "fwhm_um": round(fwhm_um, 4), "strehl": round(strehl, 4),
                  "airy_fwhm_um": round(airy_fwhm_um, 4), "field": field,
                  "wavelength_nm": round(wave_um * 1000, 1), "na": round(img_space_na, 3)}
        if config is not None:
            result["config"] = config
        return result


zemax_run_huygens_psf = serialized_operation(zemax_run_huygens_psf)

