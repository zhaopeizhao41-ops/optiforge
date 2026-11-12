"""SIMULATED optics for the workbench demo/acceptance fixture. Never opens Zemax.

A small vectorized sequential real-ray tracer for spherical surfaces, plus
image-quality items shaped like core.analysis_native output. Results are
software-test data only: no ray aiming, no polarization, wavefront and MTF are
estimates (ray-aberration integration; geometric x diffraction MTF).

Glass dispersion: SCHOTT Sellmeier coefficients from the SCHOTT Zemax catalog
2017-01-20b as published on refractiveindex.info (read 2026-11-11).
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
import time

import numpy as np

ENGINE = "SIMULATED Python 实光线追迹（演示，非 Zemax）"
SELLMEIER = {
    "N-SK16": ((1.34317774, 0.241144399, 0.994317969), (0.00704687339, 0.0229005, 92.7508526)),
    "N-F2": ((1.39757037, 0.159201403, 1.2686543), (0.00995906143, 0.0546931752, 119.248346)),
}


def index(material, wavelength_um):
    if material.strip().upper() in {"", "AIR"}:
        return 1.0
    b, c = SELLMEIER[material]
    w2 = wavelength_um ** 2
    return math.sqrt(1 + sum(bi * w2 / (w2 - ci) for bi, ci in zip(b, c)))


def utc():
    return datetime.now(timezone.utc).isoformat()


def cooke_model(project):
    """Repository template domain/design_templates.py cooke_triplet (EFL ~50, F/4.5, 20 deg)."""
    rows = [(22.0, 3.2, "N-SK16", "L1 前"), (-435.0, 5.5, "", "L1 后"), (-22.2, 1.1, "N-F2", "L2 前"),
            (20.3, 4.7, "", "L2 后 · 光阑"), (79.7, 2.8, "N-SK16", "L3 前"), (-18.4, 42.0, "", "L3 后")]
    surfaces = [{"index": 0, "radius": math.inf, "thickness": math.inf, "material": "", "comment": "物面"}]
    for i, (radius, thickness, material, comment) in enumerate(rows, 1):
        surfaces.append({"index": i, "radius": radius, "thickness": thickness, "material": material, "comment": comment})
    surfaces.append({"index": 7, "radius": math.inf, "thickness": 0.0, "material": "", "comment": "像面"})
    for s in surfaces:
        s.update(semi_diameter=s.get("semi_diameter", 0.0), conic=0.0, type="Standard", is_stop=s["index"] == 4,
                 radius_solve="Variable" if 1 <= s["index"] <= 6 else "Fixed", thickness_solve="Fixed")
    model = {"units": "mm", "lens_units": "Millimeters", "system_mode": "Sequential", "name": "Cooke 三片式 · SIMULATED",
             "filepath": "simulated-cooke-triplet.zmx", "project": project, "configuration": 1, "configuration_count": 1,
             "settings": {"aperture_type": "EPD", "aperture_value": 11.1, "field_type": "Angle",
                          "field_normalization": "Radial", "ray_aiming": "Off"},
             "fields": [{"index": i + 1, "x": 0.0, "y": y, "weight": 1.0} for i, y in enumerate((0.0, 14.0, 20.0))],
             "wavelengths": [{"index": 1, "wavelength_um": 0.58756, "weight": 1.0, "is_primary": True},
                             {"index": 2, "wavelength_um": 0.48613, "weight": 1.0, "is_primary": False},
                             {"index": 3, "wavelength_um": 0.65627, "weight": 1.0, "is_primary": False}],
             "surfaces": surfaces}
    model["trace_fields"] = [{**f, "is_active": True, "ignore": False} for f in model["fields"]]
    update_semi_diameters(model)
    return model


class System:
    """Precomputed optical data for one model and wavelength."""

    def __init__(self, model, wavelength_um):
        self.model = model
        rows = model["surfaces"]
        self.radius = np.array([float(s["radius"]) for s in rows[1:]])
        self.curv = np.where(np.isinf(self.radius), 0.0, 1 / np.where(np.isinf(self.radius), 1, self.radius))
        self.thick = np.array([float(s["thickness"]) for s in rows[1:-1]])
        self.vertex = np.concatenate([[0.0], np.cumsum(self.thick)])
        self.n = np.array([index(s["material"], wavelength_um) for s in rows[1:]])  # after each surface
        self.n_before = np.concatenate([[1.0], self.n[:-1]])
        self.stop = next(i for i, s in enumerate(rows[1:]) if s["is_stop"])

    def paraxial(self):
        """Return (efl, entrance pupil z relative to S1, image-space marginal slope for unit height)."""
        # Full system (all refracting surfaces before the image).
        y, u = 1.0, 0.0
        for k in range(len(self.thick)):
            u = (self.n_before[k] * u - y * self.curv[k] * (self.n[k] - self.n_before[k])) / self.n[k]
            if k < len(self.thick) - 1:
                y = y + self.thick[k] * u
        efl = -1.0 / u
        # Entrance pupil: object-space ray (y1, u0) reaching the stop at height 0.
        def stop_height(y1, u0):
            y, u = y1, u0
            for k in range(self.stop):
                u = (self.n_before[k] * u - y * self.curv[k] * (self.n[k] - self.n_before[k])) / self.n[k]
                y = y + self.thick[k] * u
            return y
        a = stop_height(1.0, 0.0)
        b = stop_height(0.0, 1.0)
        z_ep = b / a if a else 0.0  # chief ray y1 = -b/a * u0 crosses the axis at z = -y1/u0
        return efl, z_ep

    def trace(self, origin, direction):
        """Trace rays (N,3) from object space. Returns per-surface points, final directions, error codes."""
        p, d = origin.astype(float).copy(), direction.astype(float).copy()
        count = len(p)
        error = np.zeros(count, dtype=int)
        points = []
        for k in range(len(self.radius)):
            z0 = self.vertex[k]
            local = p - np.array([0, 0, z0])
            c = self.curv[k]
            if c == 0:
                t = -local[:, 2] / d[:, 2]
            else:
                r = 1 / c
                center = np.array([0, 0, r])
                oc = local - center
                b = np.sum(d * oc, axis=1)
                cc = np.sum(oc * oc, axis=1) - r * r
                disc = b * b - cc
                bad = disc < 0
                error[(error == 0) & bad] = k + 1
                root = np.sqrt(np.where(bad, 0, disc))
                t = -b - np.sign(r) * root
            q = local + t[:, None] * d
            points.append(q.copy())
            if k == len(self.radius) - 1:
                break
            normal = np.tile([0.0, 0.0, 1.0], (count, 1)) if c == 0 else (np.array([0, 0, 1 / c]) - q) * c
            normal /= np.linalg.norm(normal, axis=1)[:, None]
            mu = self.n_before[k] / self.n[k]
            cos_i = np.sum(d * normal, axis=1)
            k2 = 1 - mu * mu * (1 - cos_i * cos_i)
            error[(error == 0) & (k2 < 0)] = k + 1
            d = mu * d + (np.sqrt(np.clip(k2, 0, None)) - mu * cos_i)[:, None] * normal
            p = q + np.array([0, 0, z0])
        return points, d, error


def _rays(model, system, field, pupil):
    """Paraxial pupil aiming: collimated rays through the paraxial entrance pupil."""
    _, z_ep = system.paraxial()
    radius = model["settings"]["aperture_value"] / 2
    ax, ay = math.radians(field["x"]), math.radians(field["y"])
    direction = np.array([math.sin(ax), math.sin(ay), 0.0])
    direction[2] = math.sqrt(max(0.0, 1 - direction[0] ** 2 - direction[1] ** 2))
    pupil = np.atleast_2d(pupil)
    at_pupil = np.column_stack([pupil[:, 0] * radius, pupil[:, 1] * radius, np.full(len(pupil), z_ep)])
    start_z = min(z_ep, 0.0) - 10.0
    t = (at_pupil[:, 2] - start_z) / direction[2]
    origin = at_pupil - t[:, None] * direction
    return origin, np.tile(direction, (len(pupil), 1))


def trace_field(model, field, wavelength_um, pupil):
    system = System(model, wavelength_um)
    origin, direction = _rays(model, system, field, pupil)
    points, final, error = system.trace(origin, direction)
    return system, points, final, error


def update_semi_diameters(model):
    """Automatic semi-diameters (Zemax 'Automatic' analogue) with 5% margin; never vignettes."""
    ring = np.array([[math.cos(a), math.sin(a)] for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)])
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    heights = np.zeros(len(model["surfaces"]) - 1)
    for field in model["fields"]:
        _, points, _, error = trace_field(model, field, primary["wavelength_um"], ring)
        for k, q in enumerate(points):
            ok = error == 0
            if ok.any():
                heights[k] = max(heights[k], float(np.max(np.hypot(q[ok, 0], q[ok, 1]))))
    for k, surface in enumerate(model["surfaces"][1:]):
        surface["semi_diameter"] = round(float(heights[k]) * 1.05, 4) if heights[k] else 1.0
    return model


# ---------------------------------------------------------------- trace fixture

def trace_adapter(model, settings):
    """Real-ray paths shaped for core.workbench_trace.assemble (no native API)."""
    from core.workbench_trace import assemble
    cache = {}

    def batch(surface, rays):
        if "rows" not in cache:
            rows = []
            for number, ray in enumerate(rays, 1):
                system, points, _, error = trace_field(model, ray["field"], ray["wavelength_um"], [[ray["px"], ray["py"]]])
                rows.append((points, int(error[0])))
            cache["rows"] = rows
        output = []
        for number, (points, error) in enumerate(cache["rows"], 1):
            q = points[surface - 1][0]
            failed = error and surface >= error
            output.append((True, number, error if failed else 0, 0, float(q[0]), float(q[1]), float(q[2]),
                           0., 0., 1., 0., 0., 1., 0., 1.))
        return output

    vertex = System(model, 0.58756).vertex

    def frame(surface):
        return [1, 0, 0, 0, 1, 0, 0, 0, 1], [0, 0, float(vertex[surface - 1])]
    start = time.monotonic()
    result = assemble(model, settings, batch, frame)
    result["elapsed_ms"] = (time.monotonic() - start) * 1000
    return result


# ---------------------------------------------------------------- analyses

def _hexapolar(rings):
    pupil = [(0.0, 0.0)]
    for ring in range(1, rings + 1):
        for i in range(6 * ring):
            angle = 2 * math.pi * i / (6 * ring)
            pupil.append((ring / rings * math.cos(angle), ring / rings * math.sin(angle)))
    return np.array(pupil)


def _image_xy(model, field, wavelength_um, pupil):
    _, points, final, error = trace_field(model, field, wavelength_um, pupil)
    return points[-1][:, :2], final, error


def _working_f_number(model):
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    _, _, final, _ = trace_field(model, model["fields"][0], primary["wavelength_um"], [[0.0, 1.0]])
    sin_u = abs(float(final[0, 1]))
    return 1 / (2 * sin_u) if sin_u else None


def spot_item(model, settings):
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    pupil = _hexapolar(settings["spot_rings"])
    fields = []
    for field in model["fields"]:
        if field["index"] not in settings["fields"]:
            continue
        chief, _, _ = _image_xy(model, field, primary["wavelength_um"], [[0.0, 0.0]])
        series, counts = [], {"valid": 0, "vignetted": 0, "failed": 0, "total": 0}
        for wave in model["wavelengths"]:
            xy, _, error = _image_xy(model, field, wave["wavelength_um"], pupil)
            ok = error == 0
            counts["total"] += len(xy)
            counts["failed"] += int((~ok).sum())
            counts["valid"] += int(ok.sum())
            series.append({"wavelength_index": wave["index"], "wavelength_um": wave["wavelength_um"], "weight": wave["weight"],
                           "points": ((xy[ok] - chief[0]) * 1000).round(5).tolist()})
        weighted = [(p, s["weight"]) for s in series for p in s["points"]]
        total = math.fsum(w for _, w in weighted)
        fields.append({"field_index": field["index"], "reference_xy_native": chief[0].tolist(), "series": series, "counts": counts,
                       "rms_radius_um": float(math.sqrt(math.fsum(w * (p[0] ** 2 + p[1] ** 2) for p, w in weighted) / total)),
                       "geo_radius_um": float(max(math.hypot(*p) for p, _ in weighted))})
    return {"fields": fields, "unit": "µm", "effective_settings": {
        "reference": "primary wavelength chief ray", "sampling": "hexapolar", "rings": settings["spot_rings"],
        "rays_per_wavelength": len(pupil), "surface": "image", "delta_focus": 0,
        "statistics": "wavelength weighted, valid rays only; SIMULATED diagnostic INFO", "native_length_unit": "mm"},
        "coverage": {"fields": settings["fields"], "wavelengths": "all"}}


def ray_fan_item(model, settings):
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    samples = np.linspace(-1, 1, 21)
    curves = []
    for field in model["fields"]:
        if field["index"] not in settings["fields"]:
            continue
        chief, _, _ = _image_xy(model, field, primary["wavelength_um"], [[0.0, 0.0]])
        for kind, column, axis in (("tangential", 1, "Py"), ("sagittal", 0, "Px")):
            pupil = np.column_stack([samples, np.zeros_like(samples)]) if column == 0 else np.column_stack([np.zeros_like(samples), samples])
            for wave in model["wavelengths"]:
                xy, _, error = _image_xy(model, field, wave["wavelength_um"], pupil)
                y = [round(float(v), 6) if e == 0 else None for v, e in zip((xy[:, column] - chief[0, column]) * 1000, error)]
                curves.append({"field_index": field["index"], "wavelength_index": wave["index"], "wavelength_um": wave["wavelength_um"],
                               "kind": kind, "x": samples.round(4).tolist(), "y": y, "x_unit": "normalized pupil", "y_unit": "µm",
                               "coordinates": "Ey(Py)" if kind == "tangential" else "Ex(Px)",
                               "description": f"SIMULATED {axis} fan"})
    return {"curves": curves, "effective_settings": {"number_of_rays": 21, "check_apertures": True, "vignetted_pupil": True,
            "reference": "SIMULATED primary chief ray; image local coordinates"},
            "coverage": {"fields": settings["fields"], "wavelengths": "all"}}


def _focus_shift(model, field, wavelength_um, column, delta=0.02):
    """Axial distance from the image plane to where a thin T or S fan focuses."""
    pupil = [[0.0, delta], [0.0, -delta]] if column == 1 else [[delta, 0.0], [-delta, 0.0]]
    xy, final, error = _image_xy(model, field, wavelength_um, pupil)
    if error.any():
        return None
    dy = final[0, column] / final[0, 2] - final[1, column] / final[1, 2]
    return None if dy == 0 else float(-(xy[0, column] - xy[1, column]) / dy)


def field_curvature_item(model, settings):
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    maximum = max(math.hypot(f["x"], f["y"]) for f in model["fields"])
    angles = np.linspace(0, maximum, 21)
    efl, _ = System(model, primary["wavelength_um"]).paraxial()
    curves = []
    for wave in model["wavelengths"]:
        tangential, sagittal, distortion = [], [], []
        for angle in angles:
            field = {"x": 0.0, "y": float(angle)}
            tangential.append(_focus_shift(model, field, wave["wavelength_um"], 1))
            sagittal.append(_focus_shift(model, field, wave["wavelength_um"], 0))
            chief, _, error = _image_xy(model, field, wave["wavelength_um"], [[0.0, 0.0]])
            ideal = efl * math.tan(math.radians(angle))
            distortion.append(None if error.any() else 0.0 if angle == 0 else float((chief[0, 1] - ideal) / ideal * 100))
        description = f"SIMULATED {wave['wavelength_um']:.5f} µm"
        for kind, label, y in (("tangential", "T", tangential), ("sagittal", "S", sagittal), ("distortion", "畸变", distortion)):
            curves.append({"kind": kind, "label": label, "wavelength_um": wave["wavelength_um"], "x": angles.round(4).tolist(),
                           "y": [None if v is None else round(v, 7) for v in y], "x_unit": "deg",
                           "y_unit": "%" if kind == "distortion" else "mm", "description": description})
    return {"curves": curves, "effective_settings": {"scan": "+Y continuous SIMULATED scan", "distortion": "F-Tan(theta)",
            "reference_field": 1, "ignore_vignette": False, "native_length_unit": "mm"},
            "coverage": {"fields": "continuous +Y to maximum field", "wavelengths": "all"}}


def wavefront_item(model, settings):
    """OPD estimate by integrating transverse ray aberrations over the pupil (SIMULATED)."""
    from core.analysis_data import wavefront_grid
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    size = settings["wavefront_size"]
    fnumber = _working_f_number(model) or 4.5
    na = 1 / (2 * fnumber)
    lam_mm = primary["wavelength_um"] / 1000
    axis = np.linspace(-1, 1, size)
    px, py = np.meshgrid(axis, axis)
    inside = px ** 2 + py ** 2 <= 1
    fields = []
    for field in model["fields"]:
        if field["index"] not in settings["fields"]:
            continue
        pupil = np.column_stack([px[inside], py[inside]])
        xy, _, error = _image_xy(model, field, primary["wavelength_um"], np.vstack([[0.0, 0.0], pupil]))
        chief, xy = xy[0], xy[1:]
        ex, ey = np.zeros_like(px), np.zeros_like(py)
        ex[inside], ey[inside] = xy[:, 0] - chief[0], xy[:, 1] - chief[1]
        step = axis[1] - axis[0]
        # dW/dp = -NA * eps (mm per unit pupil); integrate along rows and columns, then average.
        wy = -na * np.cumsum(np.where(inside, ey, 0), axis=0) * step
        wx = -na * np.cumsum(np.where(inside, ex, 0), axis=1) * step
        w = (wx + wy) / lam_mm
        rows = np.where(inside, w, np.nan)
        rows = rows - np.nanmean(rows)
        grid = wavefront_grid(rows.tolist())
        grid.update(nx=size, ny=size, min_x=-1.0, min_y=-1.0, dx=float(step), dy=float(step), unit="waves",
                    coordinates="SIMULATED normalized pupil grid", orientation="row index increases with +Py")
        fields.append({"field_index": field["index"], "wavelength_index": primary["index"], "wavelength_um": primary["wavelength_um"],
                       "grid": grid, "native_header": [], "native_statistics": None,
                       "statistics_comparison": {"verified": False, "reason": "SIMULATED：无原生 PV/RMS 对照"},
                       "effective_settings": {"sampling": f"{size}x{size}", "remove_tilt": False, "reference_to_primary": False,
                                              "use_exit_pupil": True, "polarization": "None", "surface": "image"}})
    return {"fields": fields, "coverage": {"fields": settings["fields"], "wavelengths": [primary["index"]]}}


def mtf_item(model, settings):
    """Geometric spot MTF x diffraction-limited circular-aperture MTF (SIMULATED estimate)."""
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    fnumber = _working_f_number(model) or 4.5
    cutoff = 1 / (primary["wavelength_um"] / 1000 * fnumber)
    frequencies = np.linspace(0, settings["max_frequency"], 41)
    nu = np.clip(frequencies / cutoff, 0, 1)
    diffraction = 2 / math.pi * (np.arccos(nu) - nu * np.sqrt(1 - nu * nu))
    pupil = _hexapolar(8)
    curves, effective = [], []
    for field in model["fields"]:
        if field["index"] not in settings["fields"]:
            continue
        points, weights = [], []
        for wave in model["wavelengths"]:
            xy, _, error = _image_xy(model, field, wave["wavelength_um"], pupil)
            points.append(xy[error == 0])
            weights.append(np.full(int((error == 0).sum()), wave["weight"]))
        points, weights = np.vstack(points), np.concatenate(weights)
        points -= np.average(points, axis=0, weights=weights)
        for kind, column, label in (("tangential", 1, "T"), ("sagittal", 0, "S")):
            phase = np.exp(-2j * math.pi * np.outer(frequencies, points[:, column]))
            geometric = np.abs(phase @ weights) / weights.sum()
            curves.append({"field_index": field["index"], "kind": kind, "label": label, "x": frequencies.round(4).tolist(),
                           "y": np.clip(geometric * diffraction, 0, 1).round(6).tolist(), "x_unit": "lp/mm",
                           "y_unit": "normalized", "description": "SIMULATED geometric x diffraction estimate"})
        effective.append({"field_index": field["index"], "sample_size": settings["sample_size"],
                          "max_frequency": float(settings["max_frequency"]), "wavelengths": "all system wavelengths and weights",
                          "polarization": False, "type": "SIMULATED estimate", "surface": "image"})
    return {"curves": curves, "effective_settings": effective, "coverage": {"fields": settings["fields"], "wavelengths": "all"}}


ITEMS = {"mtf": mtf_item, "wavefront": wavefront_item, "spot": spot_item, "ray_fan": ray_fan_item,
         "field_curvature": field_curvature_item}


def analysis_adapter(model, settings, progress=None):
    """Same result envelope as core.analysis_native.NativeSuite.execute."""
    from core.analysis_data import settings_for, suite_verdict
    settings = settings_for(model, settings)
    started, clock, items = utc(), time.monotonic(), {}
    for name in settings["analyses"]:
        if progress:
            progress(name, None)
        try:
            items[name] = {**ITEMS[name](model, settings), "status": "success"}
        except Exception as error:  # keep failures visible as ERROR rows, like the native suite
            items[name] = {"status": "error", "code": "SIMULATED_FAILED", "message": str(error)}
    fnumber = _working_f_number(model)
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    return {"analysis_schema_version": 1, "status": "success" if all(i["status"] == "success" for i in items.values()) else "partial",
            "engine": ENGINE, "started_at": started, "finished_at": utc(), "elapsed_ms": (time.monotonic() - clock) * 1000,
            "settings": settings, "items": items,
            "model_metadata": {"name": model.get("name"), "units": model["units"], "configuration": model["configuration"],
                               "fields": model["fields"], "wavelengths": model["wavelengths"], "working_f_number": fnumber,
                               "airy_radius_um": 1.22 * primary["wavelength_um"] * fnumber if fnumber else None,
                               "airy_method": "SIMULATED 1.22 * lambda * working F/#; diagnostic only"},
            **suite_verdict(items, settings, model)}


# ---------------------------------------------------------------- optimizer

def merit(model, efl_target=50.0):
    """Residuals: transverse errors (mm) over fields/wavelengths/pupil + EFL target."""
    pupil = _hexapolar(3)
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    residuals = []
    for field in model["fields"]:
        chief, _, _ = _image_xy(model, field, primary["wavelength_um"], [[0.0, 0.0]])
        for wave in model["wavelengths"]:
            xy, _, error = _image_xy(model, field, wave["wavelength_um"], pupil)
            delta = np.where(error[:, None] == 0, xy - chief[0], 1.0)
            residuals.extend(delta.ravel())
    efl, _ = System(model, primary["wavelength_um"]).paraxial()
    residuals.append((efl - efl_target) * 0.02 * len(residuals) ** .5)
    residuals.extend(edge_penalties(model))
    return np.array(residuals)


def edge_thickness(model):
    """Edge thickness of each gap at the larger adjacent semi-diameter (MNEG/MNEA analogue)."""
    from core.workbench_snapshot import sag
    rows, edges = model["surfaces"], []
    for front, back in zip(rows[1:-2], rows[2:-1]):
        half = max(front["semi_diameter"], back["semi_diameter"])
        try:
            edge = front["thickness"] - sag(front["radius"], 0, half) + sag(back["radius"], 0, half)
        except ValueError:
            edge = -1.0
        edges.append((front["index"], bool(front["material"]), edge))
    return edges


def edge_penalties(model, glass_min=1.2, air_min=0.8):
    """Hard-boundary residuals, zero when satisfied (weighted like MNEG/MNEA rows)."""
    return [10.0 * max(0.0, (glass_min if glass else air_min) - edge) for _, glass, edge in edge_thickness(model)]


def optimize(model, variables="curvatures", iterations=12, efl_target=50.0):
    """Bounded damped least squares (scipy) on the SIMULATED merit; mutates model in place."""
    from scipy.optimize import least_squares
    rows = model["surfaces"]
    keys = [(i, "radius") for i in range(1, 7)] + [(6, "thickness")]
    if variables in {"thickness", "all"}:
        keys += [(i, "thickness") for i in (1, 2, 3, 4, 5)]

    def encode():
        return np.array([1 / rows[i]["radius"] if key == "radius" else rows[i]["thickness"] for i, key in keys])

    def decode(x):
        for (i, key), value in zip(keys, x):
            if key == "radius":
                rows[i]["radius"] = 1 / value if abs(value) > 1e-6 else 1e6
            else:
                rows[i]["thickness"] = float(value)

    lower = [(-0.2 if key == "radius" else (1.5 if rows[i]["material"] else 0.5)) for i, key in keys]  # CT >= 1.5 mm glass, 0.5 mm air
    upper = [(0.2 if key == "radius" else (6.0 if rows[i]["material"] else 12.0)) for i, key in keys]
    lower[6], upper[6] = 30.0, 60.0  # back focal distance
    start = np.clip(encode(), lower, upper)

    def residual(x):
        decode(x)
        try:
            update_semi_diameters(model)
            value = merit(model, efl_target)
            return np.where(np.isfinite(value), value, 1.0)
        except Exception:
            return np.ones(len(merit_shape))
    update_semi_diameters(model)
    merit_shape = merit(model, efl_target)
    before = float(np.sqrt(np.mean(merit_shape ** 2)))
    result = least_squares(residual, start, bounds=(lower, upper), max_nfev=iterations, x_scale="jac")
    decode(result.x)
    for i, key in keys:
        rows[i][key] = round(float(rows[i][key]), 4)
    update_semi_diameters(model)
    after = float(np.sqrt(np.mean(merit(model, efl_target) ** 2)))
    return {"status": "success", "message": f"SIMULATED 阻尼最小二乘：评价函数 {before:.5f} → {after:.5f}（{variables}）",
            "merit_before": before, "merit_after": after, "variables": variables}


def describe(model):
    primary = next(w for w in model["wavelengths"] if w["is_primary"])
    efl, z_ep = System(model, primary["wavelength_um"]).paraxial()
    fnumber = _working_f_number(model)
    return {"efl_mm": float(efl), "entrance_pupil_z_mm": float(z_ep), "working_f_number": None if fnumber is None else float(fnumber)}


def snapshot(model):
    """A complete workbench snapshot for SIMULATED data (core.workbench_snapshot layout)."""
    from core.workbench_snapshot import build_layout, json_safe
    copy = deepcopy(model)
    return {"status": "success", "schema_version": 1, "revision": str(hash(str(copy["surfaces"]))),
            "observed_at": utc(), "connection": {"state": "connected", "engine": "SIMULATED", "mode": "演示 · 非 Zemax"},
            "model": json_safe(copy), "layout": build_layout(copy),
            "capabilities": {"ray_trace": True, "analysis": True, "editing": False}}
