"""Axial display geometry read from ZOS, separate from prescription validation.

GetSag supplies the primary sag (including the native out-of-domain display
behavior). Mechanical lands are display edges, never ray-trace apertures.
No analysis tool, solve, prescription setter or Save is used here.
"""
import math
import time


MAX_DISPLAY_SURFACES = 200
DISPLAY_SECONDS = 5.


def display_layout(model, read_surface, read_sag):
    result = {"status": "unsupported", "plane": "YZ", "units": model["units"],
              "source": "zemax-primary-sag", "surfaces": [], "elements": [],
              "issues": list(model.get("layout_issues", [])), "rays": []}
    surfaces = model["surfaces"]
    if model["system_mode"] != "Sequential" or len(surfaces) < 3:
        result["issues"].append("当前模型没有可显示的序列截面")
    if len(surfaces) - 1 > MAX_DISPLAY_SURFACES:
        result["issues"].append("截面显示超过 200 面采样上限")
    for row in surfaces[1:]:
        if row["type"] != "Standard" or row["material"].upper() == "MIRROR":
            result["issues"].append(f'S{row["index"]} 面型尚不支持原生截面采样')
    if result["issues"]:
        return result
    vertex = 0.
    started = time.monotonic()
    try:
        for position, row in enumerate(surfaces[1:], 1):
            index = row["index"]
            image = position == len(surfaces) - 1
            drawing = read_surface(index)
            if drawing["hidden"] or drawing["edges"] != "Squared" or drawing["no_edges"]:
                raise ValueError(f'S{index} 含尚未支持的隐藏或边缘绘图设置')
            half, mechanical = float(row["semi_diameter"]), float(drawing["mechanical"])
            if not all(math.isfinite(v) and v >= 0 for v in (half, mechanical)) or mechanical < half:
                raise ValueError(f'S{index} 的原生绘图口径不可用')
            if half == 0:
                raise ValueError(f'S{index} 缺少截面绘图半口径')
            heights = {half * (2 * i / 96 - 1) for i in range(97)}
            # Include the exact conic domain boundary, so native saturation has
            # a real corner instead of being smoothed across two samples.
            radius, conic = float(row["radius"]), float(row["conic"])
            if radius != 0 and math.isfinite(radius) and conic > -1:
                limit = abs(radius) / math.sqrt(1 + conic)
                if limit < half:
                    heights.update((-limit, limit))
            points = []
            for height in sorted(heights):
                if time.monotonic() - started > DISPLAY_SECONDS:
                    raise ValueError("原生截面采样超过 5 秒预算")
                raw = read_sag(index, height)
                if len(raw) != 3 or not raw[0] or not math.isfinite(float(raw[1])):
                    raise ValueError(f'S{index} 原生 GetSag 未返回有效主矢高')
                points.append([vertex + float(raw[1]), height])
            if mechanical > half:
                points = [[points[0][0], -mechanical], *points, [points[-1][0], mechanical]]
            result["surfaces"].append({"index": index, "vertex_z": vertex, "is_image": image,
                                       "is_stop": row["is_stop"], "points": points,
                                       "clear_semi_diameter": half, "mechanical_semi_diameter": mechanical})
            if not image:
                distance = float(row["thickness"])
                if not math.isfinite(distance) or distance < 0:
                    raise ValueError(f'S{index} 含尚不支持的反向或非有限间隔')
                vertex += distance
        for row, front, back in zip(surfaces[1:-1], result["surfaces"], result["surfaces"][1:]):
            if row["material"].strip().upper() not in {"", "AIR"}:
                result["elements"].append({"front": front["index"], "back": back["index"],
                                           "material": row["material"], "ends_at_image": back["is_image"],
                                           "points": front["points"] + list(reversed(back["points"]))})
        coordinates = [p for surface in result["surfaces"] for p in surface["points"]]
        if not all(math.isfinite(v) for p in coordinates for v in p):
            raise ValueError("原生截面坐标不是有限数值")
        result["bounds"] = [min(p[0] for p in coordinates), min(p[1] for p in coordinates),
                            max(p[0] for p in coordinates), max(p[1] for p in coordinates)]
        result["status"] = "ready"
    except Exception as error:
        # Display-only native reads can fail independently of the prescription.
        # Never retain a partial contour or discard an otherwise valid snapshot.
        result.update(surfaces=[], elements=[], issues=[f"原生截面不可用：{error}"])
    return result


def native_display_layout(system, model):
    from System import Double
    def drawing(index):
        row = system.LDE.GetSurfaceAt(index)
        return {"mechanical": float(row.MechanicalSemiDiameter), "hidden": bool(row.DrawData.DoNotDrawThisSurface),
                "edges": str(row.DrawData.DrawEdgesAs).split(".")[-1], "no_edges": bool(row.DrawData.DoNotDrawEdgesFromThisSurface)}
    return display_layout(model, drawing, lambda index, height: system.LDE.GetSag(index, 0., height, Double(0), Double(0)))
