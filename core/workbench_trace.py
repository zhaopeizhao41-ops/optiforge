"""Bounded, read-only sequential real rays. No local refraction approximation."""
from copy import deepcopy
import hashlib
import json
import math
import time

MAX_BYTES = 32 * 1024 * 1024


class TraceError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise TraceError("TRACE_INVALID_DATA", "原生追迹返回非有限数值")
    return value


def normalize_fields(fields, normalization):
    mode = normalization.split(".")[-1]
    if mode not in {"Radial", "Rectangular"} or not fields:
        raise TraceError("TRACE_FIELDS_UNSUPPORTED", "无法确认视场归一化方式")
    # The complete native table defines the scales, never the selected subset.
    radial = max(math.hypot(finite(f["x"]), finite(f["y"])) for f in fields)
    sx = max(abs(finite(f["x"])) for f in fields) if mode == "Rectangular" else radial
    sy = max(abs(finite(f["y"])) for f in fields) if mode == "Rectangular" else radial
    return [{**f, "hx": f["x"] / sx if sx else 0., "hy": f["y"] / sy if sy else 0.} for f in fields]


def settings_for(model, settings=None):
    settings = {} if settings is None else settings
    if not isinstance(settings, dict) or set(settings) - {"fields", "wavelengths", "pupil_count", "pupil_axis"}:
        raise TraceError("TRACE_SETTINGS_INVALID", "追迹设置包含未知项目")
    fields = [f for f in model["fields"] if not f.get("ignore", False) and f.get("is_active", True)]
    ordered = sorted(fields, key=lambda f: (math.hypot(f["x"], f["y"]), f["index"]))
    default = list(dict.fromkeys(ordered[i]["index"] for i in (0, len(ordered)//2, len(ordered)-1))) if ordered else []
    waves = model["wavelengths"]
    primary = [w["index"] for w in waves if w["is_primary"]]
    def indices(name, defaults, available, maximum):
        values = settings.get(name, defaults)
        if (not isinstance(values, list) or not 1 <= len(values) <= maximum
                or any(type(v) is not int for v in values) or len(set(values)) != len(values)
                or set(values) - set(available)):
            raise TraceError("TRACE_SETTINGS_INVALID", f"{name} 必须选择实际存在的索引，最多 {maximum} 个")
        return sorted(values)
    count = settings.get("pupil_count", 5)
    if type(count) is not int or count not in (3, 5, 9):
        raise TraceError("TRACE_SETTINGS_INVALID", "光瞳采样仅支持 3 / 5 / 9 条")
    axis = settings.get("pupil_axis", "Y")
    if axis not in ("X", "Y"):
        raise TraceError("TRACE_SETTINGS_INVALID", "光瞳采样方向仅支持 X / Y")
    normalized = {"fields": indices("fields", default, [f["index"] for f in fields], 9),
                  "wavelengths": indices("wavelengths", primary[:1], [w["index"] for w in waves], 3),
                  "pupil_count": count, "pupil_axis": axis}
    targets = len(model["surfaces"]) - 1
    paths = len(normalized["fields"]) * len(normalized["wavelengths"]) * count
    if not 1 <= targets <= 200 or paths > 243 or targets * paths > 48600:
        raise TraceError("TRACE_BUDGET_EXCEEDED", "追迹预算上限为 200 面、243 条路径、48600 个交点")
    return normalized


def settings_hash(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def trace_key(state, settings):
    source = [state["service_instance_id"], state["model_id"], state["model_revision"],
              state["snapshot"]["model"]["configuration"], settings_hash(settings)]
    return hashlib.sha256(json.dumps(source, separators=(",", ":")).encode()).hexdigest()


def eligibility(state):
    if not state.get("context_valid") or state.get("validity") != "valid" or not state.get("model_id") or not state.get("snapshot"):
        return {"status": "error", "code": "WORKBENCH_STATE_UNCERTAIN", "message": "当前模型身份不可信，请先读取或重新载入模型"}
    if not state.get("active_project") or state["active_project"] != state.get("model_project"):
        return {"status": "error", "code": "MODEL_PROJECT_MISMATCH", "message": "当前项目与模型归属不同"}
    if state.get("proposal_confirmed") is not True:
        return {"status": "error", "code": "PROPOSAL_NOT_CONFIRMED", "message": "当前项目提案尚未确认，请在 Agent 中审阅并确认"}
    return None


def native_field_table(system):
    table = system.SystemData.Fields
    result = []
    for index in range(1, int(table.NumberOfFields) + 1):
        field = table.GetField(index)
        result.append({"index": index, "x": float(field.X), "y": float(field.Y), "weight": float(field.Weight),
                       "is_active": bool(field.IsActive), "ignore": bool(field.Ignore),
                       "vignetting": {key: float(getattr(field, key)) for key in ("VDX", "VDY", "VCX", "VCY", "VAN", "TAN")}})
    return result, str(table.Normalization).split(".")[-1]


def ray_inputs(model, settings):
    fields = normalize_fields(model["fields"], model["settings"]["field_normalization"])
    if any(f.get("ignore") or not f.get("is_active", True) for f in fields):
        # The installed API does not document whether ignored extrema affect H.
        # Fail explicitly until a native comparison covers that ambiguity.
        raise TraceError("TRACE_IGNORED_FIELDS_UNVERIFIED", "包含忽略或非活动视场，其归一化尚未原生验证；本轮不追迹该模型")
    waves = {w["index"]: w for w in model["wavelengths"]}
    rays = []
    for field in fields:
        if field["index"] not in settings["fields"]:
            continue
        for wave in settings["wavelengths"]:
            for sample in range(settings["pupil_count"]):
                pupil = 2. * sample / (settings["pupil_count"] - 1) - 1.
                px, py = (pupil, 0.) if settings.get("pupil_axis", "Y") == "X" else (0., pupil)
                rays.append({"id": f'f{field["index"]}:w{wave}:p{sample}', "field": deepcopy(field),
                             "wave_index": wave, "wavelength_um": waves[wave]["wavelength_um"],
                             "px": px, "py": py, "points": [], "termination": None})
    return rays


def matrix_from_native(raw):
    if len(raw) != 13 or not raw[0]:
        raise TraceError("TRACE_FRAME_INVALID", "GetGlobalMatrix 未返回可信坐标矩阵")
    return [finite(x) for x in raw[1:10]], [finite(x) for x in raw[10:13]]


def display_point(point, frame, reference):
    rotation, origin = frame
    r1, t1 = reference
    global_xyz = [sum(rotation[3*i+j] * point[j] for j in range(3)) + origin[i] for i in range(3)]
    return [finite(sum(r1[3*j+i] * (global_xyz[j] - t1[j]) for j in range(3))) for i in range(3)]


def records(raw_records, count):
    output = {}
    for raw in raw_records:
        if len(raw) != 15 or not raw[0] or type(raw[1]) is not int or not 1 <= raw[1] <= count or raw[1] in output:
            raise TraceError("TRACE_RECORD_INVALID", "原生返回记录重复、越界或排列不符")
        output[raw[1]] = raw
    if len(output) != count:
        raise TraceError("TRACE_RECORD_MISSING", "原生返回记录缺失")
    return output


def assemble(model, settings, batch, get_frame):
    """Injected native operations allow testing path trust independently of ZOS."""
    rays = ray_inputs(model, settings)
    reference = get_frame(1)
    for surface in range(1, len(model["surfaces"])):
        frame = get_frame(surface)
        rows = records(batch(surface, rays), len(rays))
        for index, ray in enumerate(rays, 1):
            if ray["termination"] is not None:
                continue
            raw = rows[index]
            error, vignette = int(raw[2]), int(raw[3])
            if error:
                ray["termination"] = {"kind": "error", "first_observed_surface": surface,
                                      "error_code": error, "vignette_code": vignette}
                continue
            native_values = [finite(v) for v in raw[4:]]
            xyz = display_point(native_values[:3], frame, reference)
            ray["points"].append({"surface": surface, "xyz": xyz})
            if vignette:
                # First nonzero observed while tracing targets in order. No abs(code)
                # inference. The endpoint is shown, but no segment beyond it.
                ray["termination"] = {"kind": "vignetted", "first_observed_surface": surface,
                                      "error_code": error, "vignette_code": vignette}
    for ray in rays:
        if ray["termination"] is None:
            ray["termination"] = {"kind": "complete"}
    counts = {"complete": sum(r["termination"]["kind"] == "complete" for r in rays),
              "vignetted": sum(r["termination"].get("vignette_code", 0) != 0 for r in rays),
              "error": sum(r["termination"].get("error_code", 0) != 0 for r in rays),
              "both": sum(bool(r["termination"].get("error_code")) and bool(r["termination"].get("vignette_code")) for r in rays)}
    counts["drawable"] = sum(len(r["points"]) >= 2 for r in rays)
    counts["total"] = len(rays)
    points = [p["xyz"] for r in rays for p in r["points"]]
    return {"trace_schema_version": 1, "status": "success", "settings": settings,
            "settings_hash": settings_hash(settings), "units": model["units"],
            "frame": {"origin": "S1", "axes": "S1 local xyz", "range": [1, len(model["surfaces"])-1]},
            "native_inputs": {"field_type": model["settings"]["field_type"],
                              "normalization": model["settings"]["field_normalization"],
                              "ray_aiming": model["settings"]["ray_aiming"],
                              "pupil": "Py=0; Px=-1..1" if settings.get("pupil_axis", "Y") == "X" else "Px=0; Py=-1..1"},
            "rays": rays, "counts": counts,
            "bounds_xyz": [[min(p[i] for p in points), max(p[i] for p in points)] for i in range(3)] if points else None}


def trace_native(model, settings, *, budget_seconds=30):
    """Must run on the engine owner thread; direct use enforces proposal too."""
    from core.zos_session import ZOSSession
    from tools.project_manager import get_active_project_name
    from tools.system_tools import get_current_design_proposal
    with ZOSSession.operation_lock:
        project = get_active_project_name()
        if get_current_design_proposal().get("user_confirmed_to_simulate") is not True:
            raise TraceError("PROPOSAL_NOT_CONFIRMED", "当前项目提案未确认")
        session = ZOSSession._instance
        if not session or not session.is_connected or session.model_project != project:
            raise TraceError("MODEL_PROJECT_MISMATCH", "没有属于当前项目的已连接模型")
        if session.last_recovery_error:
            raise TraceError("WORKBENCH_STATE_UNCERTAIN", "模型恢复状态不确定")
        from core.workbench_snapshot import capture_snapshot
        observed = capture_snapshot()["model"]
        for name in ("surfaces", "settings", "configuration", "units", "filepath"):
            if observed[name] != model[name]:
                raise TraceError("WORKBENCH_CONTEXT_STALE", "原生模型与请求处方不一致")
        return trace_session(session, model, settings_for(observed, settings), budget_seconds=budget_seconds)


def trace_session(session, model, settings, *, budget_seconds=30):
    """Low-level adapter. Caller owns project authorization and operation lock."""
    from System import Double, Int32
    import ZOSAPI
    system = session.system
    if str(system.Mode).split(".")[-1] != "Sequential":
        raise TraceError("TRACE_MODE_UNSUPPORTED", "本轮仅支持序列几何实光线")
    for surface in model["surfaces"]:
        kind = surface["type"].lower()
        if any(word in kind for word in ("gradient", "grin", "birefringent", "nonsequential", "userdefined", "blackbox")):
            raise TraceError("TRACE_MEDIUM_UNSUPPORTED", f'S{surface["index"]} 的 {surface["type"]} 可能含介质内曲线或多路径，本轮不绘制')
    start = time.monotonic()
    deadline = start + budget_seconds
    def frame(index):
        return matrix_from_native(system.LDE.GetGlobalMatrix(index, *([Double(0)] * 12)))
    def batch(surface, rays):
        if time.monotonic() >= deadline:
            raise TraceError("TRACE_TIMEOUT", "追迹已超出计算预算")
        tool = session.open_tool("OpenBatchRayTrace")
        try:
            data = tool.CreateNormUnpol(len(rays), ZOSAPI.Tools.RayTrace.RaysType.Real, surface)
            for ray in rays:
                data.AddRay(ray["wave_index"], ray["field"]["hx"], ray["field"]["hy"], ray["px"], ray["py"],
                            getattr(ZOSAPI.Tools.RayTrace.OPDMode, "None"))
            tool.RunAndWaitWithTimeout(max(.001, deadline - time.monotonic()))
            timed_out = bool(tool.IsRunning) or time.monotonic() > deadline
            if timed_out:
                raise TraceError("TRACE_TIMEOUT", "原生工具超时；等待其安全停止后结束任务")
            if not tool.Succeeded:
                raise TraceError("TRACE_NATIVE_FAILED", str(tool.ErrorMessage))
            data.StartReadingResults()
            output = []
            for _ in range(len(rays) + 1):
                raw = data.ReadNextResult(*([Int32(0)] * 3 + [Double(0)] * 11))
                if not raw[0]:
                    return output
                output.append(tuple(raw))
            raise TraceError("TRACE_RECORD_INVALID", "原生结果数量超出请求")
        finally:
            try:
                if tool.IsRunning and tool.CanCancel:
                    tool.Cancel()
                # Timeout is NOT completion. Keep the owner queue occupied until
                # stopped, including noncancellable native tools.
                while tool.IsRunning:
                    tool.WaitWithTimeout(.5)
                if not tool.Close():
                    raise RuntimeError("Close 返回 false")
            except Exception as error:
                raise TraceError("TRACE_CLEANUP_FAILED", f"原生工具未安全释放，暂停后续计算：{error}") from error
    result = assemble(model, settings, batch, frame)
    result["elapsed_ms"] = (time.monotonic() - start) * 1000
    return result
