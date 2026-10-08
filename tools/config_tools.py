"""
Zemax Multi-Configuration Editor (MCE) tools.
Used for scan positions, imaging depths and any other per-configuration parameter sweep.
"""

from typing import Any, Dict, List
from core.zos_session import ZOSSession
from core.operation_guard import model_operation, serialized_operation
from core.editor_cells import read_cell, write_cell


def _rows(mce) -> List[Dict[str, Any]]:
    n_cfg = int(mce.NumberOfConfigurations)
    rows = []
    for r in range(1, int(mce.NumberOfOperands) + 1):
        op = mce.GetOperandAt(r)
        rows.append({
            "row": r,
            "type": str(op.Type),
            "param1": int(op.Param1),
            "param2": int(op.Param2),
            "param3": int(op.Param3),
            "values": [read_cell(op.GetOperandCell(c)) for c in range(1, n_cfg + 1)],
        })
    return rows


def resize_configs(mce, n_configs: int) -> None:
    """Grow (copying the last configuration) or shrink (from the end) to n_configs."""
    while mce.NumberOfConfigurations < n_configs:
        mce.AddConfiguration(False)
    while mce.NumberOfConfigurations > n_configs:
        mce.DeleteConfiguration(mce.NumberOfConfigurations)


def write_mce_row(zos, mce, code: str, values: List[Any], param1: int = 0, param2: int = 0,
                  param3: int = 0, row: int = None, variable: bool = False):
    """Shared MCE row writer; raises ValueError on bad input. Returns (row, written values)."""
    try:
        op_type = getattr(zos.Editors.MCE.MultiConfigOperandType, code)
    except AttributeError:
        raise ValueError(f"MCE operand '{code}' not found in ZOSAPI MultiConfigOperandType.")

    n_cfg = int(mce.NumberOfConfigurations)
    if len(values) != n_cfg:
        raise ValueError(f"values has {len(values)} entries but the system has {n_cfg} configurations; "
                         "call zemax_mce_setup first.")

    n_rows = int(mce.NumberOfOperands)
    if row is not None:
        if row > n_rows:
            raise ValueError(f"row {row} does not exist (MCE has {n_rows} rows).")
    else:
        key = (code, int(param1), int(param2), int(param3))
        existing = [r["row"] for r in _rows(mce) if (r["type"], r["param1"], r["param2"], r["param3"]) == key]
        if existing:
            row = existing[0]
        elif n_rows == 1 and str(mce.GetOperandAt(1).Type) == "MOFF":
            row = 1
        else:
            mce.AddOperand()
            row = int(mce.NumberOfOperands)
    op = mce.GetOperandAt(row)

    op.ChangeType(op_type)
    op.Param1, op.Param2, op.Param3 = int(param1), int(param2), int(param3)
    written = []
    for c, value in enumerate(values, start=1):
        cell = op.GetOperandCell(c)
        written.append(write_cell(cell, value, f"{code} config {c}"))
        if variable:
            cell.MakeSolveVariable()

    # Re-select the current configuration so the LDE reflects the new row.
    mce.SetCurrentConfiguration(mce.CurrentConfiguration)
    return int(row), written


def zemax_mce_setup(n_configs: int, reset: bool = False) -> Dict[str, Any]:
    """
    Set the number of configurations. New configurations copy the last one; extra ones are deleted from the end.
    reset=True first clears every MCE row and collapses to a single configuration.
    """
    mce = ZOSSession.get_instance().system.MCE
    if reset:
        mce.DeleteAllRows()
        resize_configs(mce, 1)
    resize_configs(mce, n_configs)
    mce.SetCurrentConfiguration(1)
    return {
        "status": "success",
        "n_configs": int(mce.NumberOfConfigurations),
        "n_rows": int(mce.NumberOfOperands),
    }


def zemax_mce_set_operand(
    operand_type: str,
    values: List[Any],
    param1: int = 0,
    param2: int = 0,
    param3: int = 0,
    row: int = None,
    variable: bool = False,
) -> Dict[str, Any]:
    """
    Write one MCE row: operand_type (THIC, CRVT, PRAM, GLSS, WAVE, APER, SDIA, XFIE, YFIE, ...)
    with param1..3 (e.g. THIC: param1=surface; PRAM: param1=surface, param2=parameter column)
    and one value per configuration (GLSS takes glass names).
    Without row, an existing row with the same type and params is updated, a lone MOFF row is reused,
    otherwise a new row is appended. variable=True makes every configuration cell a variable.
    """
    session = ZOSSession.get_instance()
    code = operand_type.upper().strip()
    try:
        row, written = write_mce_row(session.ZOSAPI, session.system.MCE, code, values,
                                     param1, param2, param3, row, variable)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    return {
        "status": "success",
        "row": int(row),
        "type": code,
        "params": [int(param1), int(param2), int(param3)],
        "values": written,
        "variable": bool(variable),
    }


def zemax_mce_get() -> Dict[str, Any]:
    """Read every MCE row with its value in each configuration."""
    mce = ZOSSession.get_instance().system.MCE
    return {
        "status": "success",
        "n_configs": int(mce.NumberOfConfigurations),
        "current_config": int(mce.CurrentConfiguration),
        "rows": _rows(mce),
    }


for _name in ("zemax_mce_setup", "zemax_mce_set_operand"):
    globals()[_name] = model_operation(globals()[_name])
zemax_mce_get = serialized_operation(zemax_mce_get)
