"""Read-only, engine-neutral prescription snapshots and axial YZ geometry.

This module performs geometry only. It does not trace rays, run analyses, change
solves, or change a prescription. Unsupported systems never get guessed shapes.
"""
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def json_safe(value):
    """Keep optical infinity explicit while emitting strict JSON for browsers."""
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity" if value < 0 else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def sag(radius, conic, height):
    """Zemax sequential conic sag in the prescription's native length units."""
    radius, conic, height = float(radius), float(conic), float(height)
    if not math.isfinite(conic) or not math.isfinite(height) or math.isnan(radius):
        raise ValueError("曲面参数不是有效数值")
    if radius == 0 or math.isinf(radius):
        return 0.0
    curvature = 1 / radius
    discriminant = 1 - (1 + conic) * (curvature * height) ** 2
    if discriminant < -1e-12:
        raise ValueError("通光口径超出此圆锥曲面的实数定义域")
    return curvature * height ** 2 / (1 + math.sqrt(max(0, discriminant)))


def minimum_surface_gap(front, back, distance, half):
    """Minimum axial conic separation on the common circular aperture.

    For z'(y) = y / (R * sqrt(1 - (1+k)*y*y/(R*R))), an interior
    stationary point requires equally signed finite radii and
    y*y = (R_back*R_back - R_front*R_front) / (k_back - k_front).
    Endpoints plus this candidate exhaust the extrema; drawing samples are not
    used for collision detection. Scale the radii before squaring them.
    """
    ra, rb = float(front["radius"]), float(back["radius"])
    ka, kb = float(front["conic"]), float(back["conic"])
    heights = [0., half]
    if (ra != 0 and rb != 0 and math.isfinite(ra) and math.isfinite(rb)
            and (ra > 0) == (rb > 0) and ka != kb):
        scale = max(abs(ra), abs(rb))
        delta_k = kb - ka
        if not math.isfinite(delta_k):
            raise ValueError("圆锥系数差值超出可计算范围")
        squared = ((rb - ra) / scale) * (rb / scale + ra / scale) / delta_k
        if squared > 0 and math.isfinite(squared):
            height = scale * math.sqrt(squared)
            if 0 < height < half:
                heights.append(height)
    candidates = [(math.fsum((distance, sag(rb, kb, height), -sag(ra, ka, height))), height)
                  for height in heights]
    if not all(math.isfinite(gap) for gap, _ in candidates):
        raise ValueError("曲面间距超出可计算范围")
    return min(candidates)


def _is_air(surface):
    return surface["material"].strip().upper() in {"", "AIR"}


def _check_clearances(surfaces, contours):
    # A Standard row in air on both sides is a reference/stop plane, not a
    # glass boundary. Keep it in the drawing, but check across it so that a
    # zero-thickness dummy neither causes a false collision nor hides one.
    boundaries = [i for i in range(1, len(surfaces))
                  if i == len(surfaces) - 1 or not (_is_air(surfaces[i - 1]) and _is_air(surfaces[i]))]
    for start, end in zip(boundaries, boundaries[1:]):
        front, back = surfaces[start], surfaces[end]
        half = min(abs(contours[start - 1]["points"][-1][1]), abs(contours[end - 1]["points"][-1][1]))
        distance = math.fsum(row["thickness"] for row in surfaces[start:end])
        gap, _ = minimum_surface_gap(front, back, distance, half)
        # Relative length tolerance keeps the same verdict after a unit change.
        tolerance = 1e-10 * max(half, abs(distance))
        if _is_air(front):
            if gap < -tolerance:
                raise ValueError(f'S{front["index"]} 与 S{back["index"]} 的空气层轮廓相交')
        elif gap <= tolerance:
            raise ValueError(f'S{front["index"]} 与 S{back["index"]} 的镜片厚度不为正或轮廓相交')


def build_layout(model):
    """Build a single-scale axial section, including the native image position."""
    result = {"status": "unsupported", "plane": "YZ", "units": model["units"],
              "surfaces": [], "elements": [], "issues": list(model.get("layout_issues", [])), "rays": []}
    surfaces = model["surfaces"]
    if model["system_mode"] != "Sequential":
        result["issues"].append("当前二维轮廓仅支持序列系统")
        return result
    if len(surfaces) < 3:
        result["issues"].append("尚无可显示的镜头")
        return result
    for surface in surfaces[1:-1]:
        if surface["material"].upper() == "MIRROR":
            result["issues"].append(f'S{surface["index"]} 为反射面，尚不支持二维轮廓')
        elif surface["type"] != "Standard":
            result["issues"].append(f'S{surface["index"]} · {surface["type"]} 尚不支持二维轮廓')
        if not math.isfinite(float(surface["thickness"])) or surface["thickness"] < 0:
            result["issues"].append(f'S{surface["index"]} 后含虚像间隔或非有限间隔')
        if not math.isfinite(float(surface["semi_diameter"])) or surface["semi_diameter"] <= 0:
            result["issues"].append(f'S{surface["index"]} 缺少有效半口径')
    # Unsupported coordinate frames may affect every subsequent surface.
    if result["issues"]:
        return result
    for surface in surfaces[1:-1]:
        try:
            sag(surface["radius"], surface["conic"], surface["semi_diameter"])
        except (ValueError, TypeError, OverflowError) as error:
            result["issues"].append(
                f'S{surface["index"]}：{error}（半径 {surface["radius"]:.6g} {model["units"]}，'
                f'半口径 {surface["semi_diameter"]:.6g} {model["units"]}，圆锥系数 {surface["conic"]:.6g}）')
    if result["issues"]:
        return result
    maximum = max(surface["semi_diameter"] for surface in surfaces[1:-1])
    vertex = 0.0
    try:
        for surface in surfaces[1:]:
            index = surface["index"]
            image = index == surfaces[-1]["index"]
            if image and surface["type"] != "Standard":
                raise ValueError(f'S{index} 像面类型尚不支持二维轮廓')
            half = surface["semi_diameter"]
            if image and (not math.isfinite(half) or half <= 0):
                half = maximum * .6  # A plane marker when the native image has no aperture.
                if sag(surface["radius"], surface["conic"], half) != 0:
                    raise ValueError("曲像面缺少有效半口径")
            points = [[vertex + sag(surface["radius"], surface["conic"], y), y]
                      for y in (half * (2 * i / 96 - 1) for i in range(97))]
            result["surfaces"].append({"index": index, "vertex_z": vertex,
                                       "is_image": image, "is_stop": surface["is_stop"],
                                       "points": points})
            if not image:
                vertex += surface["thickness"]
        _check_clearances(surfaces, result["surfaces"])
        for i, surface in enumerate(surfaces[1:-1]):
            if _is_air(surface):
                continue
            front, back = result["surfaces"][i:i + 2]
            if back["is_image"]:
                raise ValueError(f'S{surface["index"]} 材料延伸到像面，无法构成封闭镜片')
            result["elements"].append({"front": front["index"], "back": back["index"],
                                       "material": surface["material"],
                                       "points": front["points"] + list(reversed(back["points"]))})
    except (ValueError, TypeError, OverflowError) as error:
        result.update(surfaces=[], elements=[], issues=[str(error)])
        return result
    coordinates = [point for surface in result["surfaces"] for point in surface["points"]]
    result["bounds"] = [min(p[0] for p in coordinates), min(p[1] for p in coordinates),
                        max(p[0] for p in coordinates), max(p[1] for p in coordinates)]
    result["status"] = "ready"
    return result


UNIT_LABELS = {"Millimeters": "mm", "Centimeters": "cm", "Meters": "m", "Inches": "in"}


def native_layout_issues(system):
    """Reject local transforms and apertures not represented by this section."""
    issues = []
    for index in range(1, system.LDE.NumberOfSurfaces):
        row = system.LDE.GetSurfaceAt(index)
        try:
            tilt = row.TiltDecenterData
            transformed = any(float(getattr(tilt, side + "Surface" + component)) != 0
                              for side in ("Before", "After")
                              for component in ("DecenterX", "DecenterY", "TiltX", "TiltY", "TiltZ"))
            if (transformed or str(tilt.AfterSurfaceMode).split(".")[-1] != "Explicit"
                    or str(tilt.CoordinateReturn).split(".")[-1] != "None"):
                issues.append(f"S{index} 含局部坐标变换，尚不支持二维轮廓")
            aperture_type = str(row.ApertureData.CurrentType).split(".")[-1]
            if aperture_type not in {"None", "FloatingAperture"}:
                issues.append(f"S{index} 含 {aperture_type} 自定义孔径，尚不支持二维轮廓")
        except Exception:
            issues.append(f"S{index} 无法确认坐标与孔径，未绘制二维轮廓")
    return issues


def capture_snapshot():
    """Read the same live session used by the existing tools, under its lock."""
    from core.zos_session import ZOSSession
    from tools.system_tools import zemax_get_system_data

    with ZOSSession.operation_lock:
        session = ZOSSession.get_instance()
        if session.last_recovery_error:
            raise RuntimeError("模型恢复失败，需重新打开模型后读取")
        data = zemax_get_system_data()
        if data.get("status") != "success":
            raise RuntimeError(data.get("message", "读取模型失败"))
        system = session.system
        units_name = str(system.SystemData.Units.LensUnits).split(".")[-1]
        filepath = session.current_filepath
        model = {"name": Path(filepath).stem if filepath else str(getattr(getattr(system.SystemData, "TitleNotes", None), "Title", "") or "未命名模型"),
                 "filepath": filepath, "project": session.model_project,
                 "units": UNIT_LABELS.get(units_name, units_name), "lens_units": units_name,
                 "system_mode": str(system.Mode).split(".")[-1],
                 "configuration": int(system.MCE.CurrentConfiguration),
                 "configuration_count": int(system.MCE.NumberOfConfigurations),
                 "surfaces": data["surfaces"], "fields": data["fields"],
                 "wavelengths": data["wavelengths"], "settings": data["general"],
                 "layout_issues": native_layout_issues(system)}
        layout = build_layout(model)
        from core.workbench_display import native_display_layout
        display = native_display_layout(system, model)
        display["validation_issues"] = layout["issues"]
        safe_model = json_safe(model)
        from core.workbench_trace import native_field_table
        fields, normalization = native_field_table(system)
        # Preserve the legacy field table; trace metadata is additive.
        safe_model["trace_fields"] = fields
        safe_model["settings"]["field_normalization"] = normalization
        revision = hashlib.sha256(json.dumps(safe_model, sort_keys=True, ensure_ascii=False,
                                            allow_nan=False).encode("utf-8")).hexdigest()[:16]
        return {"status": "success", "schema_version": 1, "revision": revision,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "connection": {"state": "connected", "engine": "zemax", "mode": session.mode},
                "model": safe_model, "layout": json_safe(layout), "display_layout": json_safe(display),
                "capabilities": {"ray_trace": True, "analysis": False, "editing": False}}
