"""
Zemax Optimization Tools
Handles Merit Function construction, custom operand management, Quick Focus,
Local Optimization (DLS/OD), and Hammer global search.
"""

import math
import time
from typing import Any, Dict, List, Optional

from core.zos_session import ZOSSession
from core.operands import _operand
from core.operation_guard import model_operation
from core.editor_cells import write_cell
from core.analysis_runner import close_tool, quarantine, run_analysis, run_tool_bounded, stop_tool
from core.optimization_progress import report as report_progress
from domain.zemax_rules import OpticalRuleCheck

# Spot operands the optimization wizard emits for each field. The wizard stamps each one with
# the *normalized* field coordinate it scores (Hx/Hy), not a field index -- so a spot operand
# can only be attributed to a field by comparing those coordinates against the Field Editor.
_SPOT_OPERAND_CODES = ("TRAC", "RSCH", "RSCE", "TRCY", "TRCX", "RWCE", "RWCH")

# Above this multiple of the diffraction limit, the on-axis RMS spot is large enough to
# be worth diagnosing: is the design genuinely aberration-limited, or is the merit
# function simply settling on a defocused compromise for the off-axis fields?
_DIFFRACTION_LIMIT_FACTOR = 5.0


def zemax_setup_merit_function(
    criterion: str = "RMS_Spot",
    reference: str = "Centroid",
    rings: int = 4,
    arms: int = 6,
    min_air_center: float = 0.5,
    max_air_center: float = 12.0,
    min_air_edge: float = 0.5,
    min_glass_center: float = 2.0,
    max_glass_center: float = 25.0,
    min_glass_edge: float = 1.0,
    target_efl: Optional[float] = None,
    efl_weight: float = 10.0,
    max_totr: Optional[float] = None,
    totr_weight: float = 1.0,
    max_internal_air: float = 12.0,
    max_barrel_length: Optional[float] = None,
    barrel_weight: float = 20.0,
    field_weights: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Build standard Merit Function in accordance with Zemax OpticStudio User Manual guidelines.
    Automatically configures:
    - RMS Spot or Wavefront criterion with Gaussian Quadrature pupil integration
    - Mechanical fabrication boundary constraints (MNCA/MXCA/MNEA for air, MNCG/MXCG/MNEG for glass)
    - Strict internal element-to-element air gap control (MXCA <= 12.0 mm) to prevent runaway spacing
    - Optional upper bounds on lens barrel core stack (TTHI) and total track (TOTR).
      Lengths below these maxima incur no penalty.
    - Optional first-order focal length equality target (EFFL).

    field_weights scales the per-field spot operands the wizard emitted, one weight per
    field in field order (index 0 = the first field in the Field Editor). Omit for the
    wizard's uniform weighting. This is the main defence against a wide, uncorrectable
    off-axis field dominating the merit function and dragging the image plane to a
    defocused compromise: give the fields the design must actually resolve more weight.
    """
    for name, limit, weight in (("max_totr", max_totr, totr_weight),
                                ("max_barrel_length", max_barrel_length, barrel_weight)):
        if limit is not None and (
            not math.isfinite(float(limit)) or float(limit) <= 0
            or not math.isfinite(float(weight)) or float(weight) <= 0
        ):
            return {"status": "error", "message": f"{name} and its weight must be finite and positive."}
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    mfe = sys.MFE
    lde = sys.LDE

    wiz = mfe.SEQOptimizationWizard
    wiz.Initialize()

    # Criterion: 0 = RMS, 1 = PTV
    wiz.Type = 0

    # Data: 0 = Wavefront, 1 = Spot Size Radial
    crit_clean = criterion.lower().replace("_", "").strip()
    if "wavefront" in crit_clean:
        wiz.Data = 0
    else:
        wiz.Data = 1  # Spot Size Radial

    # Reference: 0 = Centroid, 1 = Chief Ray
    ref_clean = reference.lower().strip()
    if "chief" in ref_clean:
        wiz.Reference = 1
    else:
        wiz.Reference = 0  # Centroid

    # Rings & Arms
    ring_idx = max(0, min(19, rings - 1))
    wiz.Ring = ring_idx
    arms_map = {6: 0, 8: 1, 10: 2, 12: 3}
    wiz.Arm = arms_map.get(arms, 0)

    # Air Boundaries (Default to safe 12.0 mm to avoid optimizer cheating)
    wiz.IsAirUsed = True
    wiz.AirMin = float(min_air_center)
    wiz.AirMax = float(max_air_center)
    wiz.AirEdge = float(min_air_edge)

    # Glass Boundaries
    wiz.IsGlassUsed = True
    wiz.GlassMin = float(min_glass_center)
    wiz.GlassMax = float(max_glass_center)
    wiz.GlassEdge = float(min_glass_edge)

    # Apply Wizard
    wiz.Apply()

    added_operands = []

    # Re-weight the per-field spot operands when the caller asked for it. The wizard
    # weights every field equally, which is exactly wrong when one wide field is far
    # worse than the rest: its terms then dominate the sum and pull the optimum off the
    # axis. Match each operand's Param1 against the Field Editor index it refers to.
    if field_weights is not None:
        weights = [float(w) for w in field_weights]
        if not weights or any(not math.isfinite(w) or w < 0 for w in weights):
            return {"status": "error", "message": "field_weights must be finite and non-negative."}
        n_fields = int(sys.SystemData.Fields.NumberOfFields)
        if len(weights) != n_fields:
            return {"status": "error",
                    "message": f"field_weights has {len(weights)} entries but the system has {n_fields} fields."}
        if not any(w > 0 for w in weights):
            return {"status": "error", "message": "field_weights must leave at least one field weighted."}

        # The wizard emits one block of spot operands per field and stamps each with the
        # *normalized field coordinate* (Hx/Hy) it scores -- e.g. the 5 deg field of a
        # 0/5/9 set carries Hy = 5/9. The field index itself lives only in the Field
        # Editor, so normalise the fields the same way and match on the coordinates.
        fields = sys.SystemData.Fields
        ys = [float(fields.GetField(i).Y) for i in range(1, n_fields + 1)]
        xs = [float(fields.GetField(i).X) for i in range(1, n_fields + 1)]
        scale = max([abs(v) for v in ys + xs] + [1.0])
        normalised = [(xs[i] / scale, ys[i] / scale) for i in range(n_fields)]

        def _field_cells(op):
            """Column indices of the Hx and Hy cells, located by header (they are Latin in
            every locale, unlike Param1's 'Surf')."""
            found = {}
            for col in range(2, 10):
                header = str(op.GetCellAt(col).Header).strip().lower()
                if header in ("hx", "hy"):
                    found[header] = col
            return found.get("hx"), found.get("hy")

        tuned = 0
        unmatched = 0
        for row in range(1, mfe.NumberOfOperands + 1):
            op = mfe.GetOperandAt(row)
            if str(op.Type).split(".")[-1] not in _SPOT_OPERAND_CODES:
                continue
            hx_col, hy_col = _field_cells(op)
            try:
                ox = float(op.GetCellAt(hx_col).DoubleValue) if hx_col else 0.0
                oy = float(op.GetCellAt(hy_col).DoubleValue) if hy_col else 0.0
            except Exception:
                ox = oy = 0.0
            f_idx, best = None, 1e9
            for i, (nx, ny) in enumerate(normalised):
                d = abs(ox - nx) + abs(oy - ny)
                if d < best:
                    best, f_idx = d, i + 1
            if f_idx is None or best > 1e-3:
                unmatched += 1
                continue
            op.Weight = weights[f_idx - 1]
            tuned += 1

        if tuned == 0:
            return {"status": "error",
                    "message": "field_weights was given but no per-field spot operands were found to re-weight; "
                               "the wizard did not emit field-referenced spot terms for this criterion."}
        if unmatched:
            return {"status": "error",
                    "message": f"{unmatched} spot operands carry field coordinates no editor field matches; "
                               "refusing to re-weight an ambiguous merit function."}
        added_operands.append(
            f"per-field spot weights set for {n_fields} fields: {weights} ({tuned} operands)"
        )

    # The wizard's air bounds cover every air space, including the image-space gap
    # (back focal distance). An MXCA there (e.g. <= 12 mm on a 95 mm BFD) dominates the
    # merit function and fights any EFL target, collapsing the design; release it.
    merit_col = zos.Editors.MFE.MeritColumn
    image_gap_idx = lde.NumberOfSurfaces - 2
    for row in range(1, mfe.NumberOfOperands + 1):
        op = mfe.GetOperandAt(row)
        if op.Type == zos.Editors.MFE.MeritOperandType.MXCA:
            if op.GetOperandCell(merit_col.Param1).IntegerValue == image_gap_idx:
                op.Weight = 0.0
                added_operands.append(f"MXCA (Surface {image_gap_idx}, image-space gap) released: weight=0")

    # Detect glass surface indices to classify internal vs external spaces
    glass_surfs = []
    for s_idx in range(1, lde.NumberOfSurfaces - 1):
        mat = lde.GetSurfaceAt(s_idx).Material.strip()
        if mat and mat.upper() not in ["AIR", ""]:
            glass_surfs.append(s_idx)

    # 1. Enforce strict MXCA on all internal air spaces (between first glass and last glass)
    if glass_surfs and len(glass_surfs) >= 2:
        first_g = glass_surfs[0]
        last_g = glass_surfs[-1]

        for s_idx in range(first_g, last_g):
            surf = lde.GetSurfaceAt(s_idx)
            comm = (surf.Comment or "").upper()
            if any(kw in comm for kw in ["RELAY", "INTERMEDIATE", "CONJUGATE", "4F"]):
                continue
            mat = surf.Material.strip()
            # If this surface is an air space inside the lens group
            if not mat or mat.upper() == "AIR":
                op_air = mfe.InsertNewOperandAt(1)
                op_air.ChangeType(zos.Editors.MFE.MeritOperandType.MXCA)
                op_air.GetCellAt(2).IntegerValue = s_idx
                op_air.GetCellAt(3).IntegerValue = s_idx
                op_air.Target = float(max_internal_air)
                op_air.Weight = 25.0
                added_operands.append(f"MXCA (Surface {s_idx}) target={max_internal_air}mm, weight=25.0")

    # 2. First-order equality target (EFFL)
    if target_efl is not None:
        op_efl = mfe.InsertNewOperandAt(1)
        op_efl.ChangeType(zos.Editors.MFE.MeritOperandType.EFFL)
        op_efl.Target = float(target_efl)
        op_efl.Weight = float(efl_weight)
        waves = sys.SystemData.Wavelengths
        primary_wave = next(
            (w for w in range(1, waves.NumberOfWavelengths + 1) if waves.GetWavelength(w).IsPrimary), 1
        )
        op_efl.GetOperandCell(merit_col.Param2).IntegerValue = primary_wave  # EFFL: Param2 = Wave
        added_operands.append(f"EFFL target={target_efl}, weight={efl_weight}")

    # Append after all row insertions so the OPLT references remain correct.
    # Measurement operands have zero weight; OPLT equals its target below the
    # bound and the measured value above it (verified with the native API).
    def add_upper_bound(code, limit, weight, surface_range=None):
        measurement = mfe.AddOperand()
        measurement.ChangeType(getattr(zos.Editors.MFE.MeritOperandType, code))
        measurement.Weight = 0.0
        if surface_range:
            measurement.GetOperandCell(merit_col.Param1).IntegerValue = surface_range[0]
            measurement.GetOperandCell(merit_col.Param2).IntegerValue = surface_range[1]
        measurement_row = int(mfe.NumberOfOperands)
        bound = mfe.AddOperand()
        bound.ChangeType(zos.Editors.MFE.MeritOperandType.OPLT)
        bound.GetOperandCell(merit_col.Param1).IntegerValue = measurement_row
        bound.Target = float(limit)
        bound.Weight = float(weight)
        added_operands.append(f"{code} <= {limit}mm via OPLT (row {measurement_row}), weight={weight}")

    if max_barrel_length is not None and glass_surfs:
        add_upper_bound("TTHI", max_barrel_length, barrel_weight, (glass_surfs[0], glass_surfs[-1]))
    if max_totr is not None:
        add_upper_bound("TOTR", max_totr, totr_weight)

    return {
        "status": "success",
        "message": "Merit Function created with strict internal air spacing and compactness controls.",
        "criterion": criterion,
        "reference": reference,
        "max_internal_air_mm": max_internal_air,
        "max_barrel_length_mm": max_barrel_length,
        "total_operands": mfe.NumberOfOperands,
        "added_constraints": added_operands,
    }


# MFE cells 2..9 hold Param1..Param8; their headers are localized (e.g. Hx, Hy, Px, Py, or 面/波).
_PARAM_CELLS = range(2, 10)


def _operand_param_cells(op) -> Dict[str, int]:
    """Map 'param1'..'param8' and each non-blank lower-cased column header to its cell index."""
    cells: Dict[str, int] = {}
    for n, col in enumerate(_PARAM_CELLS, start=1):
        cells[f"param{n}"] = col
        header = str(op.GetCellAt(col).Header).strip().lower()
        if header:
            cells.setdefault(header, col)
    return cells


def _write_operand_cell(op, col: int, value: float) -> float:
    cell = op.GetCellAt(col)
    return write_cell(cell, value, f"Column '{str(cell.Header).strip()}' (Param{col - 1})")


def _insert_operand(mfe, zos, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Write ONE operand from a spec dict and return its record, WITHOUT recalculating the
    merit function. Raises ValueError on any bad field. The caller owns the recalculation --
    a block of operands needs it once, not once per row, and CalculateMeritFunction() is a
    full ray trace of every operand in the editor."""
    raw_code = spec.get("type_code", spec.get("code"))
    if not isinstance(raw_code, str) or not raw_code.strip():
        raise ValueError("each operand needs a non-empty 'type_code'.")
    code_upper = raw_code.upper().strip()
    try:
        op_type = getattr(zos.Editors.MFE.MeritOperandType, code_upper)
    except AttributeError:
        raise ValueError(f"Operand code '{code_upper}' not found in ZOSAPI MeritOperandType.")

    position = spec.get("position")
    if position is not None and 1 <= int(position) <= mfe.NumberOfOperands:
        op = mfe.InsertNewOperandAt(int(position))
    else:
        op = mfe.AddOperand()

    op.ChangeType(op_type)
    op.Target = float(spec.get("target", 0.0))
    op.Weight = float(spec.get("weight", 0.0))

    written: Dict[str, float] = {}
    for n in range(1, 5):
        val = spec.get(f"param{n}", 0)
        if val:  # legacy shortcuts write only when non-zero, as before
            written[f"param{n}"] = _write_operand_cell(op, _PARAM_CELLS[n - 1], val)

    params = spec.get("params")
    if params:
        if not isinstance(params, dict):
            raise ValueError("'params' must be an object mapping column names to numbers.")
        cells = _operand_param_cells(op)
        for key, val in params.items():
            col = cells.get(str(key).strip().lower())
            if col is None:
                names = [k for k in cells if not k.startswith("param")]
                raise ValueError(f"Unknown column '{key}' for {code_upper}. "
                                 f"Use param1..param8 or one of: {', '.join(names)}.")
            written[f"param{col - 1}"] = _write_operand_cell(op, col, val)

    return {
        "operand": code_upper,
        "target": float(op.Target),
        "weight": float(op.Weight),
        "row": int(op.RowIndex),
        "parameters": written,
    }


def zemax_add_operand(
    type_code: str,
    target: float,
    weight: float,
    param1: int = 0,
    param2: int = 0,
    param3: int = 0,
    param4: int = 0,
    position: Optional[int] = None,
    params: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Insert a custom optimization operand into the Merit Function Editor (MFE).
    type_code: Zemax operand code (e.g. 'EFFL', 'TOTR', 'MNCA', 'MNCG', 'SPHA', 'COMA', 'ASTI', 'MTFT').
    param1..param4: Legacy integer shortcuts for the first four columns (written only when non-zero).
    params: Any of Param1..Param8 by name ('param5') or column header ('Hx', 'Py'); floats allowed.
            Applied after the legacy shortcuts, so it wins on overlap.

    Inserts ONE operand, then recalculates the merit function. To write a whole block -- the
    AGENTS.md barrier matrix, say -- use zemax_add_operands instead: it does one recalculation
    for the block rather than one per row.
    """
    session = ZOSSession.get_instance()
    mfe = session.system.MFE
    zos = session.ZOSAPI
    try:
        record = _insert_operand(mfe, zos, {
            "type_code": type_code, "target": target, "weight": weight,
            "param1": param1, "param2": param2, "param3": param3, "param4": param4,
            "position": position, "params": params,
        })
    except ValueError as error:
        return {"status": "error", "message": str(error)}

    # op.Value stays 0 until the merit function is recalculated.
    mfe.CalculateMeritFunction()
    record["current_value"] = float(mfe.GetOperandAt(record["row"]).Value)
    record["status"] = "success"
    return record


def zemax_add_operands(
    operands: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Insert a whole BLOCK of merit-function operands in one call, then recalculate the merit
    function ONCE. This is the batch counterpart to zemax_add_operand and the intended way to
    write the AGENTS.md barrier matrix (EFLA / REAA / REAY / RAID / MXCA / MNEG / MNEA ...).

    Why it matters: each zemax_add_operand is a separate model operation -- its own CopySystem()
    snapshot -- and ends with mfe.CalculateMeritFunction(), a full ray trace of every operand.
    An 8-15 row barrier matrix therefore costs 8-15 snapshots and 8-15 full evaluations. This
    call costs one of each.

    operands: list of objects, each {'type_code': 'REAY', 'target': 0.0, 'weight': 500.0,
              'param1': 3, 'param2': 0, 'params': {'Py': 1.0}}, in the same fields and with the
              same meaning as zemax_add_operand. 'position' is optional per entry.

    Transactional: every operand code is resolved BEFORE anything is written, so a typo rejects
    the whole block with nothing inserted. If a later row fails mid-write (an unknown 'params'
    column, say) the rows already added by this call are removed and the block is reported
    rejected, leaving the merit function exactly as it was.
    """
    if not isinstance(operands, list) or not operands:
        return {"status": "error", "code": "BAD_OPERAND_BATCH",
                "message": "operands must be a non-empty list of operand objects."}

    session = ZOSSession.get_instance()
    mfe = session.system.MFE
    zos = session.ZOSAPI

    # Phase 1 -- reject the whole block on a bad code before a single row is written.
    problems = []
    for index, spec in enumerate(operands):
        if not isinstance(spec, dict):
            problems.append({"index": index, "message": "each operand must be an object."})
            continue
        raw_code = spec.get("type_code", spec.get("code"))
        if not isinstance(raw_code, str) or not raw_code.strip():
            problems.append({"index": index, "message": "missing 'type_code'."})
        elif not hasattr(zos.Editors.MFE.MeritOperandType, raw_code.upper().strip()):
            problems.append({"index": index, "type_code": raw_code,
                             "message": f"unknown operand code '{raw_code}'."})
    if problems:
        return {"status": "error", "code": "BAD_OPERAND_BATCH",
                "message": f"{len(problems)} of {len(operands)} operands were rejected; nothing was written.",
                "problems": problems}

    # Phase 2 -- write the block. Roll the whole block back if any row fails.
    records: List[Dict[str, Any]] = []
    try:
        for spec in operands:
            records.append(_insert_operand(mfe, zos, spec))
    except Exception as error:
        rolled_back = True
        try:
            for record in reversed(records):
                mfe.RemoveOperandAt(record["row"])
        except Exception as rollback_error:
            rolled_back = False
            error = f"{error} (rollback also failed: {rollback_error})"
        return {"status": "error", "code": "OPERAND_BATCH_FAILED",
                "message": f"Block write failed after {len(records)} rows: {error}",
                "rolled_back": rolled_back, "written_rows": [r["row"] for r in records]}

    # Phase 3 -- ONE recalculation for the whole block, then read the values that were zero
    # while the rows were being written.
    mfe.CalculateMeritFunction()
    for record in records:
        row = record["row"]
        try:
            op = mfe.GetOperandAt(row)
            actual = str(op.Type).split(".")[-1]
            if actual != record["operand"] and actual != "BLNK":
                record["row_mismatch"] = f"row {row} holds {actual}, expected {record['operand']}"
            record["current_value"] = float(op.Value)
        except Exception as error:
            record["value_error"] = str(error)

    return {
        "status": "success",
        "message": f"Inserted {len(records)} operands and recalculated the merit function once.",
        "inserted": len(records),
        "rows": [r["row"] for r in records],
        "total_operands": int(mfe.NumberOfOperands),
        "operands": records,
    }


def zemax_quick_focus(
    criterion: str = "SpotSizeRadial",
    use_centroid: bool = True,
) -> Dict[str, Any]:
    """
    Run Quick Focus tool on the current optical system to adjust the back focal distance (thickness of last lens/air surface).
    criterion: 'SpotSizeRadial' or 'RMSWavefront'.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    qf = session.open_tool("OpenQuickFocus")
    try:
        if "wavefront" in criterion.lower():
            qf.Criterion = zos.Tools.General.QuickFocusCriterion.Wavefront
        else:
            qf.Criterion = zos.Tools.General.QuickFocusCriterion.SpotSizeRadial

        qf.UseCentroid = bool(use_centroid)
        timed_out = run_tool_bounded(qf, _QUICK_FOCUS_TIMEOUT_S)
    finally:
        close_tool(qf)
    if timed_out:
        # model_operation restores its snapshot on error, so no half-focused model remains.
        return {"status": "error", "code": "QUICK_FOCUS_TIMEOUT",
                "message": f"Quick Focus 超过 {_QUICK_FOCUS_TIMEOUT_S:g} s 未完成，已取消并恢复调用前的模型。"}

    # Get resulting image surface distance
    last_lens_idx = sys.LDE.NumberOfSurfaces - 2
    new_bfl = float(sys.LDE.GetSurfaceAt(last_lens_idx).Thickness)

    return {
        "status": "success",
        "message": "Quick Focus completed.",
        "criterion": criterion,
        "adjusted_surface": last_lens_idx,
        "back_focal_length_mm": round(new_bfl, 4),
    }


_ROUND_TIMEOUT_S = 120.0
_TOTAL_TIMEOUT_S = 900.0
_QUICK_FOCUS_TIMEOUT_S = 60.0

# Kept as module names for the existing call sites; the bounded logic is shared
# with every other native tool in core.analysis_runner.
_stop_tool = stop_tool
_run_bounded = run_tool_bounded
_bounded_close = close_tool


def _round_snapshot(session):
    """Copy the model before a round so a cancelled round can be undone."""
    try:
        return session.system.CopySystem()
    except Exception:
        return None


def _restore_round(session, snapshot):
    """Reload the pre-round copy. Returns (rolled_back, recovery_file, error)."""
    if snapshot is None:
        return False, None, "轮前快照不可用"
    try:
        path = session._save_snapshot(snapshot)
        session._restore_recovery(path)
        return True, path, None
    except Exception as error:
        return False, getattr(session, "last_recovery_file", None), str(error)


def _close_snapshot(snapshot):
    if snapshot is not None:
        try:
            snapshot.Close(False)
        except Exception:
            pass


def zemax_run_optimization(
    algorithm: str = "DLS",
    cycles: str = "Automatic",
    cores: int = 8,
    stagnation_threshold: float = 0.005,
    max_rounds: int = 6,
    round_timeout_seconds: float = _ROUND_TIMEOUT_S,
) -> Dict[str, Any]:
    """
    Run Local Optimization on the current optical system with pre-flight ray feasibility
    check and automatic stagnation guard (early-stopping) to prevent invalid optimization.
    algorithm: 'DLS' (Damped Least Squares) or 'OD' (Orthogonal Descent).
    cycles: 'Automatic', '1', '5', '10', '50'.
    stagnation_threshold: Minimum relative improvement required to continue (default: 0.005 = 0.5%).
    max_rounds: Maximum number of stepped cycle rounds when running Automatic (default: 6).
    round_timeout_seconds: Wall-clock budget per round before the native optimizer is
      cancelled and the run reports partial results (default: 120 s).
    """
    import math
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    mfe = sys.MFE

    # 1. Pre-flight Feasibility Check on MFE Operands
    mfe.CalculateMeritFunction()
    ray_failures = []
    for i in range(1, mfe.NumberOfOperands + 1):
        op = mfe.GetOperandAt(i)
        t_name = str(op.Type).split('.')[-1]
        if t_name in ['BLNK', 'DMFS', 'CONF']:
            continue
        try:
            val = float(op.Value)
            wt = float(op.Weight)
            if wt > 0 and (math.isnan(val) or math.isinf(val) or val > 1e6):
                ray_failures.append({"row": i, "type": t_name, "value": val, "weight": wt})
        except Exception:
            pass

    if ray_failures:
        return {
            "status": "error",
            "message": f"Pre-flight feasibility check failed! {len(ray_failures)} operands hit ray tracing failures (value >= 1e6). Optimization aborted to avoid invalid computation.",
            "ray_failures": ray_failures[:5],
        }

    # 2. Check variables
    # Keep the enum locally: ZOS-API tool objects are invalid (RemotingException) after Close()
    if "od" in algorithm.lower() or "orthogonal" in algorithm.lower():
        algo_enum = zos.Tools.Optimization.OptimizationAlgorithm.OrthogonalDescent
    else:
        algo_enum = zos.Tools.Optimization.OptimizationAlgorithm.DampedLeastSquares

    opt = session.open_tool("OpenLocalOptimization")
    try:
        opt.Algorithm = algo_enum
        opt.NumberOfCores = int(cores)
        num_vars = int(opt.Variables)
        num_targets = int(opt.Targets)
        init_mf = float(opt.InitialMeritFunction)
        algo_name = str(algo_enum)
    finally:
        opt.Close()

    if num_vars == 0:
        return {
            "status": "warning",
            "message": "No variable parameters configured in LDE/MFE! Use zemax_set_solve to mark surface thickness/radii as variable before optimizing.",
            "variables": 0,
        }

    cycles_clean = cycles.lower().strip()
    history = []
    early_stopped = False
    stop_reason = None
    timed_out = False
    started = time.monotonic()
    total_budget = _TOTAL_TIMEOUT_S
    round_budget = max(0.001, float(round_timeout_seconds))
    max_rounds = max(1, int(max_rounds))
    threshold = max(0.0, float(stagnation_threshold))

    def _finite(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    init_mf = _finite(init_mf)
    if init_mf is None:
        return {
            "status": "error",
            "code": "MERIT_FUNCTION_NON_FINITE",
            "message": "初始评价函数不是有限数值；请先检查视场/光瞳是否截光或求解是否发散。",
            "variables": num_vars,
        }

    # 3. Stepped execution. Both paths wait with a deadline: RunAndWaitForCompletion
    # has none, and a diverging DLS run or a modal dialog would hold the session lock
    # forever. The stagnation guard treats a non-finite merit function as terminal.
    cycle_map = {
        "1": zos.Tools.Optimization.OptimizationCycles.Fixed_1_Cycle,
        "5": zos.Tools.Optimization.OptimizationCycles.Fixed_5_Cycles,
        "10": zos.Tools.Optimization.OptimizationCycles.Fixed_10_Cycles,
        "50": zos.Tools.Optimization.OptimizationCycles.Fixed_50_Cycles,
    }
    automatic = cycles_clean in {"automatic", "auto"}
    planned_rounds = max_rounds if automatic else 1
    round_copy = None
    rollback = None

    for r in range(1, planned_rounds + 1):
        remaining = total_budget - (time.monotonic() - started)
        if remaining <= 0:
            stop_reason = "total_timeout"
            break
        allowance = min(round_budget, remaining)
        _close_snapshot(round_copy)
        round_copy = _round_snapshot(session)
        sub_opt = session.open_tool("OpenLocalOptimization")
        try:
            sub_opt.Algorithm = algo_enum
            sub_opt.Cycles = (zos.Tools.Optimization.OptimizationCycles.Fixed_10_Cycles if automatic
                              else cycle_map.get(cycles_clean, zos.Tools.Optimization.OptimizationCycles.Fixed_5_Cycles))
            sub_opt.NumberOfCores = int(cores)
            hit_deadline = _run_bounded(sub_opt, allowance)
            new_mf = _finite(sub_opt.CurrentMeritFunction)
        finally:
            _bounded_close(sub_opt)

        if hit_deadline or new_mf is None:
            # The cancelled or diverged round may have left half-updated variables in
            # the model; reload the copy taken before it so the reported state is real.
            rollback = _restore_round(session, round_copy)
        if hit_deadline:
            timed_out = True
            stop_reason = "total_timeout" if allowance < round_budget else "round_timeout"
            report_progress(round=r, rounds=planned_rounds, stop_reason=stop_reason, timed_out=True,
                            tool="zemax_run_optimization")
            break
        if new_mf is None:
            stop_reason = "merit_function_non_finite"
            report_progress(round=r, rounds=planned_rounds, stop_reason=stop_reason,
                            tool="zemax_run_optimization")
            break

        rel_imp = (init_mf - new_mf) / init_mf if init_mf > 0 else 0.0
        history.append({
            "round": r,
            "cycles": 10 if automatic else int(cycles_clean),
            "mf_before": round(init_mf, 6),
            "mf_after": round(new_mf, 6),
            "improvement_pct": round(rel_imp * 100.0, 2),
        })
        report_progress(round=r, rounds=planned_rounds, merit_before=round(init_mf, 6),
                        merit_after=round(new_mf, 6), improvement_pct=round(rel_imp * 100.0, 2),
                        tool="zemax_run_optimization")

        init_mf = new_mf
        if not automatic:
            stop_reason = "fixed_cycles"
            break
        if rel_imp < threshold:
            early_stopped = True
            stop_reason = "stagnated"
            break
    else:
        stop_reason = "max_rounds"
    _close_snapshot(round_copy)
    final_mf = init_mf

    improvement_pct = 0.0
    if history:
        first_mf = history[0]["mf_before"]
        if first_mf > 0:
            improvement_pct = max(0.0, (first_mf - final_mf) / first_mf * 100.0)

    status = "success"
    if stop_reason in {"round_timeout", "total_timeout"}:
        status = "partial"
    elif stop_reason == "merit_function_non_finite":
        status = "warning"
    result: Dict[str, Any] = {
        "status": status,
        "algorithm": algo_name,
        "variables_count": num_vars,
        "targets_count": num_targets,
        "initial_merit_function": round(history[0]["mf_before"] if history else final_mf, 6),
        "final_merit_function": round(final_mf, 6),
        "improvement_percentage": round(improvement_pct, 2),
        "early_stop_triggered": early_stopped,
        "stop_reason": stop_reason,
        "timed_out": timed_out,
        "round_timeout_seconds": round_budget,
        "total_timeout_seconds": total_budget,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "stagnation_guard_threshold_pct": threshold * 100.0,
        "history": history,
    }
    if rollback is not None:
        rolled_back, recovery_file, rollback_error = rollback
        result["round_rolled_back"] = rolled_back
        if recovery_file:
            result["recovery_file"] = recovery_file
        if rollback_error:
            result["rollback_error"] = rollback_error
    if timed_out:
        result["message"] = (
            ("优化在时限内未收敛，原生优化器已被取消，模型已恢复到被取消那一轮开始前的状态；"
             if result.get("round_rolled_back") else
             "优化在时限内未收敛，原生优化器已被取消；轮前快照恢复失败，模型停在取消时的状态，请重新载入核对；")
            + "请检查视场/光瞳截光或增加约束后重试。"
        )
    elif stop_reason == "merit_function_non_finite":
        result["message"] = (
            ("评价函数出现非有限值（通常由光线追迹失败引起），模型已恢复到该轮开始前的状态；"
             if result.get("round_rolled_back") else
             "评价函数出现非有限值（通常由光线追迹失败引起），轮前快照恢复失败，请重新载入核对；")
            + "请先修复截光或发散面再优化。"
        )
    if rollback is not None and not result.get("round_rolled_back"):
        # Unknown model state: fail closed so model_operation restores its own snapshot.
        result["status"] = "error"
        result["code"] = "OPTIMIZATION_ROLLBACK_FAILED"

    # A low merit function does not by itself prove a usable design. When the system
    # carries a wide off-axis field the optimizer may minimise the sum at a defocused
    # plane, trading the on-axis spot for a little off-axis relief. Measure that: the
    # on-axis RMS spot here versus the best this prescription could reach with focus
    # alone, on a private copy so the delivered model is never disturbed.
    #
    # The measurement must come from the spot diagram's RMS accessor. The RSCE operand is
    # NOT an RMS figure in this build: on a singlet whose centroid RMS is 6.3 um it reads
    # 35.3 um, which tracks the geometric (maximum) radius, not the root-mean-square one.
    try:
        on_axis_idx = 1
        primary = next(
            (w for w in range(1, sys.SystemData.Wavelengths.NumberOfWavelengths + 1)
             if sys.SystemData.Wavelengths.GetWavelength(w).IsPrimary),
            1,
        )

        def _on_axis_rms_um(system) -> Optional[float]:
            """Centroid RMS spot radius (um) for field 1 at the primary wavelength."""
            analysis = system.Analyses.New_StandardSpot()
            try:
                settings = analysis.GetSettings()
                settings = getattr(settings, "__implementation__", settings)
                settings.Field.UseAllFields()
                settings.Wavelength.UseAllWavelengths()
                data = run_analysis(analysis).SpotData
                if data is None:
                    return None
                return abs(float(data.GetRMSSpotSizeFor(on_axis_idx, primary)))
            finally:
                analysis.Close()

        achieved_um = _on_axis_rms_um(sys)

        reachable_um = None
        copy = sys.CopySystem()
        try:
            try:
                qf = copy.Tools.OpenQuickFocus()
                try:
                    qf.Criterion = zos.Tools.General.QuickFocusCriterion.SpotSizeRadial
                    qf.UseCentroid = True
                    diag_timed_out = run_tool_bounded(qf, _QUICK_FOCUS_TIMEOUT_S)
                finally:
                    close_tool(qf)
                # A timed-out refocus leaves "reachable" unknown rather than wrong.
                reachable_um = None if diag_timed_out else _on_axis_rms_um(copy)
            finally:
                copy.Close(False)
        except Exception:
            reachable_um = None

        fno = None
        try:
            ap = sys.SystemData.Aperture
            if "pupil" in str(ap.ApertureType).lower():
                fno = abs(_operand(session, "WFNO", 0, 0))
        except Exception:
            fno = None
        wave_um = None
        try:
            wave_um = float(sys.SystemData.Wavelengths.GetWavelength(primary).Wavelength)
        except Exception:
            pass

        airy_um = None
        if fno and fno > 0 and wave_um:
            airy_um = OpticalRuleCheck().calculate_airy_disk_radius_um(wave_um, fno)

        diag: Dict[str, Any] = {
            "on_axis_field": on_axis_idx,
            "primary_wavelength_index": primary,
            "on_axis_rms_spot_um": round(achieved_um, 3) if achieved_um is not None else None,
        }
        if airy_um:
            diag["airy_radius_um"] = round(airy_um, 3)
            if achieved_um:
                diag["on_axis_times_diffraction_limit"] = round(achieved_um / airy_um, 2)
        if reachable_um is not None and reachable_um > 0:
            diag["best_focus_on_axis_rms_spot_um"] = round(reachable_um, 3)
            if achieved_um:
                diag["defocus_penalty_ratio"] = round(achieved_um / reachable_um, 2)

        if achieved_um is None:
            diag["verdict"] = "unavailable"
        elif reachable_um is not None and reachable_um > 0 and achieved_um / reachable_um >= 3.0:
            # The image plane the optimizer chose is grossly defocused (the on-axis spot is
            # many times what focus alone gives) while the off-axis fields set the sum: say
            # so, rather than reporting a healthy merit function for a defocused lens.
            diag["verdict"] = "defocus_limited"
            diag["message"] = (
                f"Optimization converged, but the on-axis RMS spot is {achieved_um:.1f} um where "
                f"refocusing alone reaches {reachable_um:.1f} um ({achieved_um / reachable_um:.1f}x worse). "
                "The selected image plane is a defocused compromise for the off-axis fields, not an "
                "on-axis solution. Re-run with field_weights that favour the fields the design must "
                "actually resolve, or reduce the field extent the prescription can support."
            )
            result["warnings"] = [diag["message"]]
        elif airy_um and achieved_um / airy_um > _DIFFRACTION_LIMIT_FACTOR:
            diag["verdict"] = "aberration_limited"
            diag["message"] = (
                f"Focus is optimal, but the on-axis RMS spot ({achieved_um:.1f} um) is "
                f"{achieved_um / airy_um:.1f}x the diffraction limit ({airy_um:.2f} um). The design is "
                "limited by monochromatic aberration at this aperture, not by defocus: add degrees of "
                "freedom (an aspheric term, a second element) or stop the aperture down."
            )
            result["warnings"] = [diag["message"]]
        else:
            diag["verdict"] = "focused"

        result["focus_diagnostic"] = diag
    except Exception as diag_error:
        result["focus_diagnostic"] = {"verdict": "unavailable", "message": str(diag_error)}

    return _stage_reminder(result)


def _stage_reminder(result):
    """Attach a reminder when an optimization ran outside a recorded stage.

    The four-stage view only has data for runs wrapped in milestone_start /
    milestone_report. This never changes status or the model.
    """
    if not isinstance(result, dict) or result.get("status") == "error":
        return result
    try:
        from core.design_milestones import stage_status
        from tools.project_manager import get_active_project_name
        status = stage_status(get_active_project_name())
    except Exception:
        return result
    if status["stage"]:
        result["recorded_stage"] = status["stage"]
    elif status["handoff"]:
        handoff = status["handoff"]
        result["stage_record_hint"] = (
            "本次优化不在任何已登记阶段内，工作台“优化阶段”视图不会显示它。若这是四阶段路径的一步，"
            f"请用 zemax_design_workflow(action='milestone_start')，payload 带 request_id='{handoff['request_id']}'、"
            f"version={handoff['version']} 与确认时返回的 lease_token；结束后用 milestone_report"
            "（archive_geometry=True 并附 quality_result_id）登记。"
        )
    else:
        result["stage_record_hint"] = (
            "本次优化不在任何已登记阶段内，且当前没有有效的建模交接任务，因此无法登记阶段。"
            "阶段记录需要用户在对话或工作台确认本版提案（确认会返回 model_handoff 与 lease_token）；"
            "租约过期时需在工作台重试。"
        )
    return result


def zemax_run_hammer(timeout_seconds: int = 10) -> Dict[str, Any]:
    """
    Run Hammer Optimization to search broader parameter space for alternative local minima.
    timeout_seconds: Duration before terminating Hammer search (default: 10s).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    budget = max(1, int(timeout_seconds))
    hammer = session.open_tool("OpenHammerOptimization")
    try:
        hammer.RunAndWaitWithTimeout(budget)
        # Hammer has no natural end: it searches until stopped, so reaching the budget
        # is the normal outcome, not a partial one. WaitForCompletion has no deadline
        # of its own, so the stop is bounded (and quarantined if it fails).
        stopped_at_budget = bool(getattr(hammer, "IsRunning", False))
        stop_tool(hammer)
        final_mf = float(hammer.CurrentMeritFunction)
    finally:
        close_tool(hammer)

    final_mf = final_mf if math.isfinite(final_mf) else None
    if final_mf is None:
        # model_operation restores its snapshot on error.
        return {"status": "error", "code": "MERIT_FUNCTION_NON_FINITE",
                "message": "Hammer 结束时评价函数不是有限数值，已恢复调用前的模型。"}
    result = {
        "status": "success",
        "message": (f"Hammer optimization stopped at its {budget}s budget." if stopped_at_budget
                    else f"Hammer optimization finished within {budget}s."),
        "stopped_at_budget": stopped_at_budget,
        "timeout_seconds": budget,
        "final_merit_function": round(final_mf, 6),
        "note": "Hammer 是有限时长的全局搜索，结束不代表已达全局最优；以分析套件的门禁行判定像质。",
    }
    return _stage_reminder(result)


for _name in ("zemax_setup_merit_function", "zemax_add_operand", "zemax_add_operands",
              "zemax_quick_focus", "zemax_run_optimization", "zemax_run_hammer"):
    globals()[_name] = model_operation(globals()[_name])
