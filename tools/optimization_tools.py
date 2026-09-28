"""
Zemax Optimization Tools
Handles Merit Function construction, custom operand management, Quick Focus,
Local Optimization (DLS/OD), and Hammer global search.
"""

import math
from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession


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


def zemax_add_operand(
    type_code: str,
    target: float,
    weight: float,
    param1: int = 0,
    param2: int = 0,
    param3: int = 0,
    param4: int = 0,
    position: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Insert a custom optimization operand into the Merit Function Editor (MFE).
    type_code: Zemax operand code (e.g. 'EFFL', 'TOTR', 'MNCA', 'MNCG', 'SPHA', 'COMA', 'ASTI', 'MTFT').
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    mfe = sys.MFE

    code_upper = type_code.upper().strip()
    try:
        op_type = getattr(zos.Editors.MFE.MeritOperandType, code_upper)
    except AttributeError:
        return {
            "status": "error",
            "message": f"Operand code '{code_upper}' not found in ZOSAPI MeritOperandType.",
        }

    if position is not None and 1 <= position <= mfe.NumberOfOperands:
        op = mfe.InsertNewOperandAt(position)
    else:
        op = mfe.AddOperand()

    op.ChangeType(op_type)
    op.Target = float(target)
    op.Weight = float(weight)

    # Set parameters if non-zero
    for col_idx, val in enumerate([param1, param2, param3, param4], start=2):
        if val != 0:
            try:
                op.GetCellAt(col_idx).IntegerValue = int(val)
            except Exception:
                try:
                    op.GetCellAt(col_idx).DoubleValue = float(val)
                except Exception:
                    pass

    return {
        "status": "success",
        "operand": code_upper,
        "target": float(op.Target),
        "weight": float(op.Weight),
        "current_value": float(op.Value),
        "row": int(op.RowIndex),
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
        qf.RunAndWaitForCompletion()
    finally:
        qf.Close()

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


def zemax_run_optimization(
    algorithm: str = "DLS",
    cycles: str = "Automatic",
    cores: int = 8,
    stagnation_threshold: float = 0.005,
    max_rounds: int = 6,
) -> Dict[str, Any]:
    """
    Run Local Optimization on the current optical system with pre-flight ray feasibility
    check and automatic stagnation guard (early-stopping) to prevent invalid optimization.
    algorithm: 'DLS' (Damped Least Squares) or 'OD' (Orthogonal Descent).
    cycles: 'Automatic', '1', '5', '10', '50'.
    stagnation_threshold: Minimum relative improvement required to continue (default: 0.005 = 0.5%).
    max_rounds: Maximum number of stepped cycle rounds when running Automatic (default: 6).
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

    # 3. Stepped Execution with Stagnation Guard
    if cycles_clean in ["automatic", "auto"]:
        best_mf = init_mf
        for r in range(1, max_rounds + 1):
            sub_opt = session.open_tool("OpenLocalOptimization")
            try:
                sub_opt.Algorithm = algo_enum
                sub_opt.Cycles = zos.Tools.Optimization.OptimizationCycles.Fixed_10_Cycles
                sub_opt.NumberOfCores = int(cores)
                sub_opt.RunAndWaitForCompletion()
                new_mf = float(sub_opt.CurrentMeritFunction)
            finally:
                sub_opt.Close()

            rel_imp = (best_mf - new_mf) / best_mf if best_mf > 0 else 0.0
            history.append({
                "round": r,
                "cycles": 10,
                "mf_before": round(best_mf, 6),
                "mf_after": round(new_mf, 6),
                "improvement_pct": round(rel_imp * 100.0, 2),
            })

            if rel_imp < stagnation_threshold:
                early_stopped = True
                best_mf = new_mf
                break
            best_mf = new_mf

        final_mf = best_mf
    else:
        cycle_map = {
            "1": zos.Tools.Optimization.OptimizationCycles.Fixed_1_Cycle,
            "5": zos.Tools.Optimization.OptimizationCycles.Fixed_5_Cycles,
            "10": zos.Tools.Optimization.OptimizationCycles.Fixed_10_Cycles,
            "50": zos.Tools.Optimization.OptimizationCycles.Fixed_50_Cycles,
        }
        fixed_opt = session.open_tool("OpenLocalOptimization")
        try:
            fixed_opt.Algorithm = algo_enum
            fixed_opt.NumberOfCores = int(cores)
            fixed_opt.Cycles = cycle_map.get(
                cycles_clean, zos.Tools.Optimization.OptimizationCycles.Fixed_5_Cycles
            )
            fixed_opt.RunAndWaitForCompletion()
            final_mf = float(fixed_opt.CurrentMeritFunction)
        finally:
            fixed_opt.Close()

    improvement_pct = 0.0
    if init_mf > 0:
        improvement_pct = max(0.0, (init_mf - final_mf) / init_mf * 100.0)

    return {
        "status": "success",
        "algorithm": algo_name,
        "variables_count": num_vars,
        "targets_count": num_targets,
        "initial_merit_function": round(init_mf, 6),
        "final_merit_function": round(final_mf, 6),
        "improvement_percentage": round(improvement_pct, 2),
        "early_stop_triggered": early_stopped,
        "stagnation_guard_threshold_pct": stagnation_threshold * 100.0,
        "history": history,
    }


def zemax_run_hammer(timeout_seconds: int = 10) -> Dict[str, Any]:
    """
    Run Hammer Optimization to search broader parameter space for alternative local minima.
    timeout_seconds: Duration before terminating Hammer search (default: 10s).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    hammer = session.open_tool("OpenHammerOptimization")
    try:
        hammer.RunAndWaitWithTimeout(timeout_seconds)
        hammer.Cancel()
        hammer.WaitForCompletion()
        final_mf = float(hammer.CurrentMeritFunction)
    finally:
        hammer.Close()

    return {
        "status": "success",
        "message": f"Hammer optimization completed after {timeout_seconds}s.",
        "final_merit_function": round(final_mf, 6),
    }
