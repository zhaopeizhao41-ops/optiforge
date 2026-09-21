"""
Zemax Optimization Tools
Handles Merit Function construction, custom operand management, Quick Focus,
Local Optimization (DLS/OD), and Hammer global search.
"""

from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession


def zemax_setup_merit_function(
    criterion: str = "RMS_Spot",
    reference: str = "Centroid",
    rings: int = 4,
    arms: int = 6,
    min_air_center: float = 0.5,
    max_air_center: float = 100.0,
    min_air_edge: float = 0.5,
    min_glass_center: float = 2.0,
    max_glass_center: float = 25.0,
    min_glass_edge: float = 1.0,
    target_efl: Optional[float] = None,
    efl_weight: float = 10.0,
    max_totr: Optional[float] = None,
    totr_weight: float = 1.0,
) -> Dict[str, Any]:
    """
    Build standard Merit Function in accordance with Zemax OpticStudio User Manual guidelines.
    Automatically configures:
    - RMS Spot or Wavefront criterion with Gaussian Quadrature pupil integration
    - Mechanical fabrication boundary constraints (MNCA/MXCA/MNEA for air, MNCG/MXCG/MNEG for glass)
    - Optional first-order focal length (EFFL) and total track (TOTR) targets.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    mfe = sys.MFE

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
    # Rings mapping: index 0 -> 1 ring, 1 -> 2 rings, 2 -> 3 rings, 3 -> 4 rings, etc.
    ring_idx = max(0, min(19, rings - 1))
    wiz.Ring = ring_idx

    # Arms: 0 -> 6, 1 -> 8, 2 -> 10, 3 -> 12
    arms_map = {6: 0, 8: 1, 10: 2, 12: 3}
    wiz.Arm = arms_map.get(arms, 0)

    # Air Boundaries
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

    # Prepend or append specific first-order targets if requested
    added_operands = []
    if target_efl is not None:
        op_efl = mfe.InsertNewOperandAt(1)
        op_efl.ChangeType(zos.Editors.MFE.MeritOperandType.EFFL)
        op_efl.Target = float(target_efl)
        op_efl.Weight = float(efl_weight)
        op_efl.GetCellAt(2).IntegerValue = 1  # Primary wave
        added_operands.append(f"EFFL target={target_efl}, weight={efl_weight}")

    if max_totr is not None:
        op_totr = mfe.InsertNewOperandAt(1)
        op_totr.ChangeType(zos.Editors.MFE.MeritOperandType.TOTR)
        op_totr.Target = float(max_totr)
        op_totr.Weight = float(totr_weight)
        added_operands.append(f"TOTR target={max_totr}, weight={totr_weight}")

    return {
        "status": "success",
        "message": "Default Merit Function successfully created based on Zemax manual standards.",
        "criterion": criterion,
        "reference": reference,
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

    qf = sys.Tools.OpenQuickFocus()
    if "wavefront" in criterion.lower():
        qf.Criterion = zos.Tools.General.QuickFocusCriterion.Wavefront
    else:
        qf.Criterion = zos.Tools.General.QuickFocusCriterion.SpotSizeRadial

    qf.UseCentroid = bool(use_centroid)
    qf.RunAndWaitForCompletion()
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
) -> Dict[str, Any]:
    """
    Run Local Optimization on the current optical system.
    algorithm: 'DLS' (Damped Least Squares) or 'OD' (Orthogonal Descent).
    cycles: 'Automatic', '1', '5', '10', '50'.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    opt = sys.Tools.OpenLocalOptimization()

    # Algorithm
    if "od" in algorithm.lower() or "orthogonal" in algorithm.lower():
        opt.Algorithm = zos.Tools.Optimization.OptimizationAlgorithm.OrthogonalDescent
    else:
        opt.Algorithm = zos.Tools.Optimization.OptimizationAlgorithm.DampedLeastSquares

    # Cycles
    cycles_clean = cycles.lower().strip()
    if cycles_clean == "1":
        opt.Cycles = zos.Tools.Optimization.OptimizationCycles.Fixed_1_Cycle
    elif cycles_clean == "5":
        opt.Cycles = zos.Tools.Optimization.OptimizationCycles.Fixed_5_Cycles
    elif cycles_clean == "10":
        opt.Cycles = zos.Tools.Optimization.OptimizationCycles.Fixed_10_Cycles
    elif cycles_clean == "50":
        opt.Cycles = zos.Tools.Optimization.OptimizationCycles.Fixed_50_Cycles
    else:
        opt.Cycles = zos.Tools.Optimization.OptimizationCycles.Automatic

    opt.NumberOfCores = int(cores)
    init_mf = float(opt.InitialMeritFunction)
    num_vars = int(opt.Variables)
    num_targets = int(opt.Targets)

    if num_vars == 0:
        opt.Close()
        return {
            "status": "warning",
            "message": "No variable parameters configured in LDE/MFE! Use zemax_set_solve to mark surface thickness/radii as variable before optimizing.",
            "variables": 0,
        }

    algo_name = str(opt.Algorithm)
    opt.RunAndWaitForCompletion()
    final_mf = float(opt.CurrentMeritFunction)
    succeeded = bool(opt.Succeeded)
    opt.Close()

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
        "succeeded": succeeded,
    }


def zemax_run_hammer(timeout_seconds: int = 10) -> Dict[str, Any]:
    """
    Run Hammer Optimization to search broader parameter space for alternative local minima.
    timeout_seconds: Duration before terminating Hammer search (default: 10s).
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    hammer = sys.Tools.OpenHammerOptimization()
    hammer.RunAndWaitWithTimeout(timeout_seconds)
    hammer.Cancel()
    hammer.WaitForCompletion()
    final_mf = float(hammer.CurrentMeritFunction)
    hammer.Close()

    return {
        "status": "success",
        "message": f"Hammer optimization completed after {timeout_seconds}s.",
        "final_merit_function": round(final_mf, 6),
    }
