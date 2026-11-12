"""Read-only pictures of the workbench for any MCP host (text summary + PNG).

Reads only the service's published state, cached trace/analysis results and
immutable stage archives through the loopback control client. It never queues a
trace, analysis or native operation, and never changes authorization. Archived
or cached results are shown with their own verdicts; nothing is re-judged.
"""
import hashlib
import io
import json
import logging
import math

VIEWS = ("layout", "stages", "analysis")
SOURCE_KEYS = ("service_instance_id", "model_id", "model_revision", "active_project", "model_project")
# Validated categorical slots (light surface): blue, orange, aqua, then muted extras.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#8a63d2", "#b0841c", "#6b7680")
STAGE_RAMP = ("#86b6ef", "#5598e7", "#256abf", "#0d366b")
INK, MUTED, GRID, GLASS = "#202429", "#626970", "#dfe3e6", "#d6dce0"
DISCLAIMER = "只读视图：显示服务端已发布的状态、缓存与归档；不运行追迹/分析，不代表像质验收或仿真授权。"


class ViewError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _figure(width, height):
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    from matplotlib.figure import Figure
    from matplotlib import rcParams
    rcParams["font.family"] = ["Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "SimHei", "DejaVu Sans"]
    rcParams["axes.unicode_minus"] = False
    figure = Figure(figsize=(width, height), dpi=110, facecolor="white")
    return figure


def _png(figure):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    FigureCanvasAgg(figure)
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white", bbox_inches="tight", pad_inches=0.15)
    return buffer.getvalue()


def _style(axis):
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(GRID)
    axis.tick_params(colors=MUTED, labelsize=8)
    axis.grid(True, color=GRID, linewidth=0.8)
    axis.set_axisbelow(True)


def _fmt(value, digits=4):
    return "—" if value is None else f"{value:.{digits}g}"


def _current_source(state):
    snapshot = state.get("snapshot") or {}
    model = snapshot.get("model") or {}
    source = {key: state.get(key) for key in SOURCE_KEYS}
    source["configuration"] = model.get("configuration")
    return source


def _matches(source, state):
    current = _current_source(state)
    return isinstance(source, dict) and all(source.get(key) == current[key] for key in current)


# ------------------------------------------------------------------ layout

def _draw_layout(axis, layout, rays=None, color="#97a2a9", fill=GLASS, label=None, linewidth=1.2):
    for element in layout.get("elements", []):
        z = [point[0] for point in element["points"]] + [element["points"][0][0]]
        y = [point[1] for point in element["points"]] + [element["points"][0][1]]
        if fill:
            axis.fill(z, y, color=fill, alpha=0.75, linewidth=0)
        axis.plot(z, y, color=color, linewidth=linewidth, label=label)
        label = None
    for surface in layout.get("surfaces", []):
        z = [point[0] for point in surface["points"]]
        y = [point[1] for point in surface["points"]]
        if surface.get("is_image"):
            axis.plot(z, y, color=MUTED, linewidth=1.4)
        elif surface.get("is_stop"):
            top, bottom = max(y), min(y)
            axis.plot([surface["vertex_z"]] * 2, [top, top * 1.12], color=INK, linewidth=2)
            axis.plot([surface["vertex_z"]] * 2, [bottom, bottom * 1.12], color=INK, linewidth=2)
    for ray in rays or []:
        points = ray["points"]
        if len(points) < 2:
            continue
        axis.plot([p["xyz"][2] for p in points], [p["xyz"][1] for p in points], color=ray["color"],
                  linewidth=0.8, alpha=0.85)


def _trace_for(control, state):
    """The newest succeeded trace that belongs to the current model, if cached."""
    for summary in reversed(state.get("trace_summary") or []):
        if summary.get("status") == "succeeded" and summary.get("result_id") and _matches(summary.get("source"), state):
            try:
                return control.read_json(f"/api/traces/{summary['result_id']}")
            except Exception:
                return None
    return None


def layout_view(control, state, url):
    snapshot = state.get("snapshot")
    summary = {"view": "layout", "workbench_url": url, "service_instance_id": state.get("service_instance_id"),
               "active_project": state.get("active_project"), "validity": state.get("validity"), "note": DISCLAIMER}
    if not snapshot:
        summary["message"] = "当前没有已发布的模型快照（未载入模型或观察失败）。"
        return summary, None
    model = snapshot["model"]
    layout = snapshot.get("display_layout") or snapshot.get("layout") or {}
    summary.update(model={"name": model.get("name"), "revision": state.get("model_revision"),
                          "configuration": model.get("configuration"), "units": model.get("units"),
                          "surfaces": len(model.get("surfaces", []))},
                   engine=(snapshot.get("connection") or {}).get("mode") or (snapshot.get("connection") or {}).get("engine"),
                   surfaces=[{"index": s.get("index"), "comment": s.get("comment"), "radius": s.get("radius"),
                              "thickness": s.get("thickness"), "material": s.get("material") or "AIR"}
                             for s in model.get("surfaces", [])])
    if layout.get("status") != "ready":
        summary["message"] = "二维轮廓不可用：" + "；".join(layout.get("issues") or ["未知原因"])
        return summary, None
    trace = _trace_for(control, state)
    rays, fields = [], []
    if trace:
        fields = sorted({ray["field"]["index"] for ray in trace["rays"]})
        for ray in trace["rays"]:
            rays.append({"points": ray["points"], "color": SERIES[fields.index(ray["field"]["index"]) % len(SERIES)]})
        summary["trace"] = {"result_id": trace.get("result_id"), "counts": trace.get("counts"), "fields": fields}
    else:
        summary["trace"] = None
    figure = _figure(9, 4.2)
    axis = figure.add_subplot()
    _draw_layout(axis, layout, rays)
    axis.axhline(0, color=GRID, linewidth=0.8, linestyle=(0, (3, 4)))
    axis.set_aspect("equal", adjustable="datalim")
    axis.set_xlabel(f"Z / {model.get('units')}", color=MUTED, fontsize=9)
    for side in ("top", "right", "left"):
        axis.spines[side].set_visible(False)
    axis.tick_params(colors=MUTED, labelsize=8, left=False, labelleft=False)
    title = f"{model.get('name') or '当前模型'} · 修订 {state.get('model_revision')} · 配置 {model.get('configuration')}"
    axis.set_title(title, loc="left", fontsize=11, color=INK)
    if fields:
        from matplotlib.lines import Line2D
        axis.legend([Line2D([], [], color=SERIES[i % len(SERIES)]) for i in range(len(fields))],
                    [f"F{field}" for field in fields], loc="upper right", frameon=False, fontsize=8)
    else:
        axis.text(0.99, 0.02, "未显示光线：无当前模型的缓存追迹", transform=axis.transAxes, ha="right", fontsize=8, color=MUTED)
    return summary, _png(figure)


# ------------------------------------------------------------------ metrics

def quality_metrics(result):
    """Display numbers from a stored result; missing data stays None (mirrors the UI)."""
    from core.analysis_data import interpolate
    items, fields = result.get("items") or {}, (result.get("settings") or {}).get("fields") or []
    per = {field: {"wavefront_rms": None, "strehl": None, "spot_rms_um": None, "mtf30": None} for field in fields}
    if (items.get("wavefront") or {}).get("status") == "success":
        for row in items["wavefront"].get("fields", []):
            if row.get("field_index") in per:
                per[row["field_index"]]["wavefront_rms"] = row["grid"].get("rms_waves")
                per[row["field_index"]]["strehl"] = row["grid"].get("strehl_estimate")
    if (items.get("spot") or {}).get("status") == "success":
        for row in items["spot"].get("fields", []):
            if row.get("field_index") in per:
                per[row["field_index"]]["spot_rms_um"] = row.get("rms_radius_um")
    if (items.get("mtf") or {}).get("status") == "success":
        for field in fields:
            values = [interpolate(curve["x"], curve["y"], 30)["value"] for curve in items["mtf"].get("curves", [])
                      if curve.get("field_index") == field]
            if values and all(value is not None for value in values):
                per[field]["mtf30"] = min(values)

    def worst(key, pick=max):
        values = [row[key] for row in per.values() if row[key] is not None]
        return pick(values) if values else None
    mtf_values = [row["mtf30"] for row in per.values()]
    return {"overall": result.get("overall"), "fields": fields, "per_field": per,
            "worst_wavefront_rms": worst("wavefront_rms"), "worst_spot_rms_um": worst("spot_rms_um"),
            "min_mtf30": min(mtf_values) if mtf_values and all(v is not None for v in mtf_values) else None,
            "mtf_threshold": (result.get("settings") or {}).get("mtf_min_at_30lp_mm"),
            "airy_radius_um": (result.get("model_metadata") or {}).get("airy_radius_um"),
            "engine": result.get("engine")}


def _verdicts(result):
    return [{"analysis": row.get("analysis"), "verdict": row.get("verdict"), "criteria": row.get("criteria")}
            for row in result.get("verdicts", [])]


# ------------------------------------------------------------------ analysis

def analysis_view(control, state, url):
    summary = {"view": "analysis", "workbench_url": url, "active_project": state.get("active_project"), "note": DISCLAIMER}
    candidates = [s for s in (state.get("analysis_summary") or [])
                  if s.get("status") in {"succeeded", "partial"} and s.get("result_id")]
    if not candidates:
        summary["message"] = "没有可读取的缓存分析结果；请在工作台“分析”页或经授权的 Agent 运行分析。"
        return summary, None
    latest = candidates[-1]
    try:
        result = control.read_json(f"/api/analyses/{latest['result_id']}")
    except Exception:
        summary["message"] = "最近的分析结果已淘汰或不可读，请重新运行分析。"
        return summary, None
    metrics = quality_metrics(result)
    current = _matches(result.get("source"), state)
    summary.update(result_id=result.get("result_id"), current=current, finished_at=result.get("finished_at"),
                   status=result.get("status"), overall=result.get("overall"), verdicts=_verdicts(result),
                   metrics={key: metrics[key] for key in ("worst_wavefront_rms", "min_mtf30", "worst_spot_rms_um",
                                                          "mtf_threshold", "airy_radius_um", "engine")},
                   per_field=metrics["per_field"])
    if not current:
        summary["message"] = "该结果属于旧模型修订、配置或服务，仅供历史核对。"
    items = result.get("items") or {}
    mtf, spot = items.get("mtf") or {}, items.get("spot") or {}
    if mtf.get("status") != "success" and spot.get("status") != "success":
        return summary, None
    figure = _figure(10, 4.2)
    left, right = figure.subplots(1, 2, gridspec_kw={"width_ratios": [1.4, 1]})
    fields = metrics["fields"]
    if mtf.get("status") == "success":
        _style(left)
        for curve in mtf.get("curves", []):
            index = fields.index(curve["field_index"]) if curve.get("field_index") in fields else 0
            left.plot(curve["x"], [math.nan if v is None else v for v in curve["y"]], color=SERIES[index % len(SERIES)],
                      linewidth=1.6, linestyle="--" if curve.get("kind") == "sagittal" else "-",
                      label=f"F{curve.get('field_index')} {'S' if curve.get('kind') == 'sagittal' else 'T'}")
        threshold = metrics["mtf_threshold"]
        if threshold is not None:
            left.axhline(threshold, color=MUTED, linewidth=1, linestyle=(0, (4, 4)))
            left.text(left.get_xlim()[1], threshold, f" 门禁 {threshold:g}", va="bottom", ha="right", fontsize=8, color=MUTED)
        left.axvline(30, color=GRID, linewidth=1)
        left.set_ylim(0, 1.02)
        left.set_xlabel("空间频率 / lp/mm", color=MUTED, fontsize=9)
        left.set_ylabel("MTF", color=MUTED, fontsize=9)
        left.set_title("MTF（实线 T，虚线 S）", loc="left", fontsize=10, color=INK)
        left.legend(frameon=False, fontsize=7, ncol=2)
    else:
        left.axis("off")
        left.text(0.5, 0.5, "MTF 未完成", ha="center", color=MUTED)
    if spot.get("status") == "success" and spot.get("fields"):
        _style(right)
        worst = max(spot["fields"], key=lambda row: row.get("rms_radius_um") or 0)
        for index, series in enumerate(worst.get("series", [])):
            points = series.get("points") or []
            right.scatter([p[0] for p in points], [p[1] for p in points], s=4, color=SERIES[index % len(SERIES)],
                          label=f"{series.get('wavelength_um')} μm", linewidths=0)
        airy = metrics["airy_radius_um"]
        if airy:
            from matplotlib.patches import Circle
            right.add_patch(Circle((0, 0), airy, fill=False, edgecolor=MUTED, linestyle=(0, (4, 3)), linewidth=1))
        extent = max([abs(v) for series in worst.get("series", []) for p in series.get("points") or [] for v in p]
                     + [airy or 0]) * 1.12 or 1
        right.set_xlim(-extent, extent)
        right.set_ylim(-extent, extent)
        right.set_aspect("equal", adjustable="box")
        right.set_title(f"点列 F{worst['field_index']} · RMS {_fmt(worst.get('rms_radius_um'))} μm（诊断 INFO）",
                        loc="left", fontsize=10, color=INK)
        right.set_xlabel("X / μm", color=MUTED, fontsize=9)
        right.legend(frameon=False, fontsize=7, markerscale=2)
    else:
        right.axis("off")
    figure.suptitle(f"{'当前' if current else '历史'}分析 · {result.get('overall')} · {result.get('engine')}",
                    x=0.01, ha="left", fontsize=11, color=INK)
    return summary, _png(figure)


# ------------------------------------------------------------------ stages

def _archive(control, project, scope, action, reference):
    envelope = control.read_tool(project, action, {"source": scope, "artifact_id": reference["id"]})
    result = envelope.get("result") or {}
    if result.get("status") != "success":
        raise ViewError(result.get("code") or "ARCHIVE_UNAVAILABLE", result.get("message") or "归档不可读")
    raw = result["payload_json"].encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != reference["sha256"] or len(raw) != reference["bytes"]:
        raise ViewError("ARCHIVE_HASH_MISMATCH", "归档正文与引用校验不一致")
    return json.loads(raw)


def stages_view(control, state, url, project):
    summary = {"view": "stages", "workbench_url": url, "active_project": project, "note": DISCLAIMER}
    envelope = control.read_tool(project, "view")
    view = envelope.get("result") or {}
    if view.get("status") != "success":
        raise ViewError(view.get("code") or "WORKFLOW_READ_FAILED", view.get("message") or "无法读取工程流程")
    scope = {"service_instance_id": control.instance, "project_name": project}
    rows = []
    for number, stage in enumerate((view.get("optimization_milestones") or {}).get("stages", []), 1):
        attempt = next((a for a in stage.get("attempts", []) if a.get("declared_status") == "needs_review"), None)
        row = {"stage": stage["id"], "number": number, "label": stage["label"], "status": stage.get("status"),
               "attempt_id": attempt and attempt["id"], "note": attempt and attempt.get("note"),
               "stale_reasons": attempt and attempt.get("stale_reasons"), "metrics": None, "errors": []}
        if attempt and attempt.get("quality_artifact"):
            try:
                body = _archive(control, project, scope, "milestone_quality_artifact", attempt["quality_artifact"])
                row["metrics"] = quality_metrics(body)
                row["verdicts"] = _verdicts(body)
            except Exception as error:
                row["errors"].append(f"像质：{getattr(error, 'message', '读取失败')}")
        if attempt and attempt.get("geometry_artifact"):
            try:
                body = _archive(control, project, scope, "milestone_artifact", attempt["geometry_artifact"])
                snapshot = body["snapshot"]
                row["_layout"] = snapshot.get("display_layout") or snapshot.get("layout")
            except Exception as error:
                row["errors"].append(f"几何：{getattr(error, 'message', '读取失败')}")
        rows.append(row)
    summary["stages"] = [{key: value for key, value in row.items() if not key.startswith("_")} for row in rows]
    reported = [row for row in rows if row["metrics"] or row.get("_layout")]
    if not reported:
        summary["message"] = "尚无带归档的阶段记录（阶段报告需显式 archive_geometry / quality_result_id）。"
        return summary, None
    figure = _figure(11, 6.4)
    grid = figure.add_gridspec(2, 3, height_ratios=[1.15, 1], hspace=0.35, wspace=0.3)
    lens = figure.add_subplot(grid[0, :])
    glass = [p for row in reported if row.get("_layout") and row["_layout"].get("status") == "ready"
             for element in row["_layout"]["elements"] for p in element["points"]]
    from matplotlib.lines import Line2D
    handles = []
    for row in reported:
        layout = row.get("_layout")
        if layout and layout.get("status") == "ready":
            color = STAGE_RAMP[(row["number"] - 1) % len(STAGE_RAMP)]
            _draw_layout(lens, {"elements": layout["elements"], "surfaces": []}, color=color, fill=None,
                         linewidth=2.2 if row is reported[-1] else 1.4)
            handles.append((Line2D([], [], color=color, linewidth=2), f"阶段 {row['number']} · {row['label']}"))
    if glass:
        zs, ys = [p[0] for p in glass], [abs(p[1]) for p in glass]
        pad = (max(zs) - min(zs)) * 0.05
        lens.set_xlim(min(zs) - pad, max(zs) + pad)
        lens.set_ylim(-max(ys) * 1.1, max(ys) * 1.1)
    lens.set_aspect("equal", adjustable="box")
    lens.axis("off")
    lens.set_title("镜片截面演变（等比例，颜色由浅到深为阶段先后）", loc="left", fontsize=10, color=INK)
    if handles:
        lens.legend(*zip(*handles), frameon=False, fontsize=8, loc="center left", bbox_to_anchor=(1.02, 0.5))
    numbers = [row["number"] for row in rows if row["metrics"]]
    fields = sorted({field for row in rows if row["metrics"] for field in row["metrics"]["fields"]})[:3]
    charts = (("wavefront_rms", "波前 RMS / λ", 1 / 14, "门禁 λ/14"),
              ("mtf30", "MTF@30 lp/mm（T/S 较小值）", None, None),
              ("spot_rms_um", "点列 RMS / μm（诊断 INFO）", None, None))
    for column, (key, title, reference, reference_label) in enumerate(charts):
        axis = figure.add_subplot(grid[1, column])
        _style(axis)
        if key == "mtf30":
            threshold = next((row["metrics"]["mtf_threshold"] for row in reversed(rows) if row["metrics"]), None)
            reference, reference_label = (threshold, f"门禁 {threshold:g}") if threshold is not None else (None, None)
        for index, field in enumerate(fields):
            values = [next(r for r in rows if r["number"] == n)["metrics"]["per_field"].get(field, {}).get(key) for n in numbers]
            axis.plot(numbers, [math.nan if v is None else v for v in values], color=SERIES[index], linewidth=2,
                      marker="osD"[index], markersize=5, label=f"F{field}")
            last = next((v for v in reversed(values) if v is not None), None)
            if last is not None:
                axis.annotate(f"F{field}", (numbers[-1], last), xytext=(4, 0), textcoords="offset points",
                              fontsize=7, color=INK, va="center")
        if reference is not None:
            axis.axhline(reference, color=MUTED, linewidth=1, linestyle=(0, (4, 4)))
            axis.text(numbers[0] if numbers else 1, reference, reference_label, fontsize=7, color=MUTED, va="bottom")
        axis.set_xticks(numbers)
        axis.set_xticklabels([f"阶段 {n}" for n in numbers])
        axis.set_ylim(bottom=0)
        axis.set_title(title, loc="left", fontsize=9, color=INK)
    return summary, _png(figure)


# ------------------------------------------------------------------ entry

def render(control, service_instance_id, view="layout", project_name=None):
    if view not in VIEWS:
        raise ViewError("UNKNOWN_VIEW", "view 必须是 layout / stages / analysis")
    if service_instance_id != control.instance:
        raise ViewError("WORKBENCH_INSTANCE_MISMATCH", "service_instance_id 与当前凭据实例不一致")
    state = control.read_json("/api/state")
    status = control.status()
    url = control.url + "/"
    pages = [{"ui_session_id": f.get("ui_session_id"), "page": f.get("page"), "online": f.get("online")}
             for f in status.get("frontends", [])]
    project = state.get("active_project")
    if project_name is not None and project_name != project:
        raise ViewError("WORKFLOW_PROJECT_MISMATCH", "只能查看当前活动工程；请先在工作台或经确认的工具切换工程")
    if view == "layout":
        summary, image = layout_view(control, state, url)
    elif view == "analysis":
        summary, image = analysis_view(control, state, url)
    else:
        if not project:
            raise ViewError("WORKFLOW_PROJECT_INVALID", "当前没有活动工程")
        summary, image = stages_view(control, state, url, project)
    summary["frontends"] = pages
    summary["image"] = "attached" if image else None
    return summary, image
