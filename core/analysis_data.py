"""Image-quality contracts and verdicts shared by MCP and the workbench.

No native imports: validation/budget checks happen before opening any analysis.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

KINDS = ("mtf", "wavefront", "spot", "ray_fan", "field_curvature")
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024
RMS_LIMIT = 1 / 14


class AnalysisError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(message, code="ANALYSIS_DATA_UNVERIFIED"):
    raise AnalysisError(code, message)


def finite(value):
    if isinstance(value, bool):
        fail("数值不能是布尔值")
    value = float(value)
    if not math.isfinite(value):
        fail("原生数据包含非有限数值")
    return value


def default_fields(model, maximum=5):
    fields = sorted((f for f in model.get("trace_fields", model["fields"])
                     if not f.get("ignore") and f.get("is_active", True)),
                    key=lambda f: (math.hypot(finite(f["x"]), finite(f["y"])), f["index"]))
    count = min(maximum, len(fields))
    # Match Math.round in the UI (indices are nonnegative); Python round uses
    # ties-to-even and otherwise selects a different set at half steps.
    return [fields[math.floor(i * (len(fields)-1) / max(1, count-1) + .5)]["index"] for i in range(count)]


def validate_settings(settings=None):
    """Validate even when no native model is connected (also used by MCP)."""
    s = deepcopy({} if settings is None else settings)
    allowed = {"analyses", "fields", "sample_size", "max_frequency", "wavefront_size", "spot_rings",
               "mtf_min_at_30lp_mm", "distortion_max_pct", "item_timeout_s", "suite_timeout_s"}
    if not isinstance(s, dict) or set(s) - allowed:
        fail("分析设置含未知项目", "ANALYSIS_SETTINGS_INVALID")
    defaults = {"analyses": list(KINDS), "sample_size": "256x256", "max_frequency": 100.,
                "wavefront_size": 128, "spot_rings": 6, "mtf_min_at_30lp_mm": None,
                "distortion_max_pct": None, "item_timeout_s": 120., "suite_timeout_s": 600.}
    s = {**defaults, **s}
    kinds = s["analyses"]
    aliases = {"opd":"wavefront", "wavefront_map":"wavefront", "fft_mtf":"mtf", "spot_diagram":"spot",
               "rayfan":"ray_fan", "distortion":"field_curvature", "field_curvature_distortion":"field_curvature"}
    if isinstance(kinds, list):
        kinds = [aliases.get(k.strip().lower().replace(" ","_"), k.strip().lower().replace(" ","_")) if isinstance(k, str) else k for k in kinds]
        s["analyses"] = kinds
    if (not isinstance(kinds, list) or not 1 <= len(kinds) <= 5 or
            any(not isinstance(k, str) or k not in KINDS for k in kinds) or len(set(kinds)) != len(kinds)):
        fail("请选择不重复的分析项目", "ANALYSIS_SETTINGS_INVALID")
    s["analyses"] = [k for k in KINDS if k in kinds]
    if s["sample_size"] not in ("128x128", "256x256", "512x512"):
        fail("MTF 采样仅支持 128/256/512", "ANALYSIS_SETTINGS_INVALID")
    for key, choices in (("wavefront_size", (128, 256)), ("spot_rings", (3, 6, 9, 12))):
        if type(s[key]) is not int or s[key] not in choices:
            fail(f"{key} 超出支持范围", "ANALYSIS_SETTINGS_INVALID")
    for key, low, high in (("max_frequency", .001, 100000.), ("item_timeout_s", .1, 120.),
                           ("suite_timeout_s", .1, 600.)):
        s[key] = finite(s[key])
        if not low <= s[key] <= high:
            fail(f"{key} 超出预算", "ANALYSIS_SETTINGS_INVALID")
    for key, high in (("mtf_min_at_30lp_mm", 1.), ("distortion_max_pct", 10000.)):
        if s[key] is not None:
            s[key] = finite(s[key])
            if not 0 <= s[key] <= high:
                fail(f"{key} 超出支持范围", "ANALYSIS_SETTINGS_INVALID")
    if "fields" in s:
        fields = s["fields"]
        if (not isinstance(fields, list) or not 1 <= len(fields) <= 5 or
                any(type(i) is not int or i < 1 for i in fields) or len(set(fields)) != len(fields)):
            fail("最多选择 5 个实际视场", "ANALYSIS_SETTINGS_INVALID")
        s["fields"] = sorted(fields)
    return s


def settings_for(model, settings=None):
    s = validate_settings(settings)
    s.setdefault("fields", sorted(default_fields(model)))
    available = {f["index"] for f in model.get("trace_fields", model["fields"])
                 if not f.get("ignore") and f.get("is_active", True)}
    if not s["fields"] or set(s["fields"]) - available:
        fail("所选视场已不存在或已停用", "ANALYSIS_SETTINGS_INVALID")
    waves = model["wavelengths"]
    if not 1 <= len(waves) <= 12 or len(model["fields"]) > 100 or len(model["surfaces"]) > 200:
        fail("分析预算最多 12 个波长、100 个视场、200 面", "ANALYSIS_BUDGET_EXCEEDED")
    primary_wave(model)
    for w in waves:
        if finite(w["wavelength_um"]) <= 0 or finite(w.get("weight", 1)) < 0:
            fail("波长或权重无效")
    # Conservative allocation estimate; serialized byte size is checked again.
    grids = len(s["fields"]) * s["wavefront_size"]**2 if "wavefront" in s["analyses"] else 0
    spots = len(s["fields"]) * len(waves) * (1 + 3*s["spot_rings"]*(s["spot_rings"]+1))
    if spots > 30000 or grids * 30 + spots * 90 + 400000 > MAX_RESULT_BYTES:
        fail("所选采样超出单任务 8 MiB 预算，请减少视场或采样", "ANALYSIS_BUDGET_EXCEEDED")
    return s


def primary_wave(model):
    waves = [w for w in model["wavelengths"] if w.get("is_primary")]
    if len(waves) != 1 or finite(waves[0]["wavelength_um"]) <= 0:
        fail("没有唯一可信的主波长；不使用默认波长")
    return waves[0]


def length_to_um(unit):
    units = {"mm": 1000., "cm": 10000., "m": 1e6, "in": 25400.,
             "Millimeters": 1000., "Centimeters": 10000., "Meters": 1e6, "Inches": 25400.}
    if unit not in units:
        fail(f"尚未核实长度单位 {unit}")
    return units[unit]


def result_key(state, settings):
    payload = {k: state[k] for k in ("service_instance_id", "model_id", "model_revision", "active_project", "model_project")}
    payload.update(configuration=state["snapshot"]["model"]["configuration"], settings=settings)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def interpolate(x, y, target):
    """Only exact native values or bracketed linear interpolation; no nearest point."""
    if len(x) != len(y) or not x or any(not math.isfinite(float(v)) for v in x):
        return {"value": None, "method": "unavailable"}
    if any(x[i] >= x[i+1] for i in range(len(x)-1)):
        return {"value": None, "method": "unavailable"}
    for i, value in enumerate(x):
        if value == target and y[i] is not None and math.isfinite(y[i]):
            return {"value": y[i], "method": "native_exact", "bracket": [value, value]}
        if i and x[i-1] < target < value:
            a, b = y[i-1], y[i]
            if a is not None and b is not None and math.isfinite(a) and math.isfinite(b):
                return {"value": a + (b-a)*(target-x[i-1])/(value-x[i-1]),
                        "method": "linear_interpolation", "bracket": [x[i-1], value]}
    return {"value": None, "method": "out_of_coverage"}


def wavefront_grid(rows, *, explicit_mask=None):
    """Installed API emits NaN outside the pupil. Zero is always valid.

    A finite-only legacy square has no trustworthy outside-pupil sentinel;
    don't guess a circular mask from coordinates or from zero values.
    """
    if not rows or not rows[0] or len(rows) > 256 or any(len(r) != len(rows[0]) for r in rows) or len(rows[0]) > 256:
        fail("波前网格尺寸无效")
    nan_mask = any(math.isnan(float(v)) for row in rows for v in row)
    if explicit_mask is None and not nan_mask:
        fail("该 API 网格没有可核实的有效孔径掩码，不能用零值推断孔径")
    if explicit_mask is not None and (len(explicit_mask) != len(rows) or any(len(a) != len(b) for a,b in zip(explicit_mask, rows))):
        fail("波前掩码尺寸不匹配")
    values, mask, valid = [], [], []
    for i, row in enumerate(rows):
        vr, mr = [], []
        for j, v in enumerate(row):
            v = float(v)
            if math.isinf(v):
                fail("波前包含无穷值，不能当作孔径外 NaN")
            usable = bool(explicit_mask[i][j]) if explicit_mask is not None else math.isfinite(v)
            if usable:
                v = finite(v); valid.append(v)
            vr.append(v if usable else None); mr.append(usable)
        values.append(vr); mask.append(mr)
    if not valid:
        fail("波前没有有效孔径数据", "ANALYSIS_NO_DATA")
    mean = math.fsum(valid)/len(valid)
    rms = math.sqrt(math.fsum((v-mean)**2 for v in valid)/len(valid))
    pv = max(valid)-min(valid)
    strehl = math.exp(-(2*math.pi*rms)**2)
    return {"values": values, "valid_mask": mask, "valid_count": len(valid), "pv_waves": pv, "rms_waves": rms,
            "strehl_estimate": strehl, "strehl_method": "Marechal exp(-(2*pi*RMS)^2), estimated",
            "strehl_small_aberration": rms <= .1, "mask_source": "explicit" if explicit_mask is not None else "native_nan",
            "statistics_source": "native grid; uniform valid samples; piston removed; tilt retained"}


def axis_identity(label):
    text = str(label).lower().strip()
    if text in {"t", "tan", "tangential", "子午", "tan位移", "tangential shift"}:
        return "tangential"
    if text in {"s", "sag", "sagittal", "弧矢", "矢高位移", "sagittal shift"}:
        return "sagittal"
    if text in {"distortion", "畸变"}:
        return "distortion"
    return None


def native_wavefront_statistics(lines):
    """Localized native headers carry rounded numbers, never full precision."""
    number = r"([-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eEdD][-+]?\d+)?)"
    for line in lines:
        line = str(line).replace("＝", "=")
        if not re.search(r"(?:peak.?to.?valley|\bPV\b|峰.*谷)",line,re.I):
            continue
        match = re.search(r"=\s*"+number+r".*?RMS\s*=\s*"+number,line,re.I)
        if not match:
            continue
        tokens = [v.replace(",",".").replace("D","e").replace("d","e") for v in match.groups()]
        values = [finite(v) for v in tokens]
        if any(v < 0 for v in values):
            fail("原生 PV/RMS 头部为负")
        def quantum(token):
            mantissa, _, exponent = token.lower().partition("e")
            decimals = len(mantissa.partition(".")[2])
            return 10.**(int(exponent or 0)-decimals)
        return {"pv_waves":values[0],"rms_waves":values[1],
                "pv_rounding_step":quantum(tokens[0]),"rms_rounding_step":quantum(tokens[1]),
                "source":"native HeaderData; rounded", "unit":"waves"}
    return None


def compare_wavefront_statistics(grid, native):
    if native is None:
        return {"verified":False,"reason":"缺少可解析的原生 PV/RMS 对照"}
    differences = {key:abs(grid[key]-native[key]) for key in ("pv_waves","rms_waves")}
    matches = all(differences[key] <= native[key.replace("_waves","_rounding_step")]/2 + 1e-12 for key in differences)
    return {"verified":matches,"method":"same native result and sampling; grid piston removed, tilt retained vs rounded native header",
            "differences":differences,"reason":"与原生头部舍入精度一致" if matches else "网格与原生统计差异未解释，判定未验证"}


def suite_verdict(items, settings, model):
    rows = []
    complete = set(settings["fields"]) == {f["index"] for f in model.get("trace_fields",model["fields"])
                                          if not f.get("ignore") and f.get("is_active",True)}
    for name in settings["analyses"]:
        item = items[name]
        row = {"analysis": name, "verdict": "INFO", "criteria": "诊断数据"}
        if item["status"] != "success":
            row.update(verdict="ERROR", evidence=item.get("message", "无有效数据"))
        elif name == "wavefront":
            fields = item["fields"]
            worst = max(fields, key=lambda f: f["grid"]["rms_waves"])
            g = worst["grid"]
            passed = g["rms_waves"] <= RMS_LIMIT and g["strehl_estimate"] >= .8
            verified = all(f.get("statistics_comparison",{}).get("verified") for f in fields)
            row.update(verdict=(("PASS" if complete else "WARN") if passed else "FAIL") if verified else "WARN",
                       criteria="RMS ≤ 1/14 waves 且估算 Strehl ≥ 0.8；仅覆盖视场",
                       evidence={"worst_covered_field": worst["field_index"], "rms_waves": g["rms_waves"],
                                 "strehl_estimate": g["strehl_estimate"], "full_field_table": complete})
            row["evidence"]["native_statistics_verified"] = verified
        elif name == "mtf":
            samples = [interpolate(c["x"],c["y"],30) for c in item["curves"]]
            values = [p["value"] for p in samples if p["value"] is not None]
            covered = len(values) == len(samples) and bool(samples)
            threshold = settings["mtf_min_at_30lp_mm"]
            row.update(criteria="30 lp/mm T/S；精确点或包围线性插值", evidence={"minimum": min(values) if values else None,
                      "samples": samples, "threshold": threshold})
            if threshold is not None:
                row["verdict"] = "ERROR" if not covered else "PASS" if min(values) >= threshold else "FAIL"
        elif name == "field_curvature":
            curves = [c for c in item["curves"] if c["kind"] == "distortion"]
            values = [abs(y) for c in curves for y in c["y"] if y is not None and math.isfinite(y)]
            covered = bool(curves) and all(c["y"] and len(c["x"]) == len(c["y"]) and
                          all(y is not None and math.isfinite(y) for y in c["y"]) for c in curves)
            threshold = settings["distortion_max_pct"]
            row.update(criteria="|畸变| %；原生 +Y 扫描", evidence={"maximum": max(values) if values else None, "threshold": threshold})
            if threshold is not None:
                row["verdict"] = "ERROR" if not covered else "PASS" if max(values) <= threshold else "FAIL"
        if row["verdict"] == "PASS" and not complete:
            row["verdict"] = "WARN"
            row["coverage_warning"] = "仅覆盖所选视场，不能判定全视场通过"
        rows.append(row)
    levels = {r["verdict"] for r in rows}
    overall = next((level for level in ("ERROR", "FAIL", "WARN", "PASS") if level in levels), "INFO")
    return {"overall": overall, "verdicts": rows, "full_field_table":complete}


def compact_suite(result, include_details=False):
    """Preserve MCP summary fields without a second native calculation."""
    data = {k:v for k,v in result.items() if include_details or k != "items"}
    meta = result.get("model_metadata", {})
    items = result.get("items", {})
    headline = {}
    wave = items.get("wavefront", {})
    if wave.get("status") == "success" and wave.get("fields"):
        worst = max(wave["fields"], key=lambda f:f["grid"]["rms_waves"])
        headline.update(worst_wavefront_field=worst["field_index"], rms_wavefront_waves=worst["grid"]["rms_waves"],
                        strehl_ratio=worst["grid"]["strehl_estimate"], pv_waves=worst["grid"]["pv_waves"])
    spot = items.get("spot", {})
    if spot.get("status") == "success" and spot.get("fields"):
        worst = max(spot["fields"], key=lambda f:f["rms_radius_um"])
        headline.update(worst_rms_spot_um=worst["rms_radius_um"], worst_spot_field=worst["field_index"])
    for row in result.get("verdicts", []):
        evidence = row.get("evidence", {})
        if not isinstance(evidence, dict):continue
        if row["analysis"] == "mtf":headline["min_mtf_at_30lp_mm"] = evidence.get("minimum")
        if row["analysis"] == "field_curvature":headline["max_distortion_pct"] = evidence.get("maximum")
    fan = [abs(v) for c in items.get("ray_fan",{}).get("curves",[]) for v in c["y"] if v is not None]
    if fan:headline["max_transverse_error_um"] = max(fan)
    curvature = [abs(v) for c in items.get("field_curvature",{}).get("curves",[]) if c["kind"] != "distortion" for v in c["y"] if v is not None]
    if curvature:headline["max_field_curvature_mm"] = max(curvature)
    waves = meta.get("wavelengths", [])
    primary = [w["wavelength_um"] for w in waves if w.get("is_primary")]
    return {**data, "analysis":"Analysis Suite", "config":meta.get("configuration"),
            "fields_in_model":len(meta.get("fields", [])), "wavelengths_in_model":len(waves),
            "primary_wavelength_um":primary[0] if len(primary)==1 else None,
            "working_f_number":meta.get("working_f_number"), "diffraction_limit_psf_um":meta.get("airy_radius_um"),
            "headline":headline, "criteria_note":"RMS ≤ 1/14 waves and estimated Strehl ≥ 0.8; selected fields only. Spot is diagnostic INFO. Missing references remain null."}
