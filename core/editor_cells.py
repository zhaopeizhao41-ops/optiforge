"""Typed writes to ZOS-API editor cells (LDE / MFE / MCE share the IEditorCell interface)."""

from typing import Any, Union


def write_cell(cell, value: Any, label: str) -> Union[int, float, str]:
    """Write value according to the cell's DataType and return what was stored."""
    kind = str(cell.DataType)
    if kind == "Integer":
        if float(value) != int(value):
            raise ValueError(f"{label} needs an integer, got {value}.")
        cell.IntegerValue = int(value)
        return int(value)
    if kind == "Double":
        cell.DoubleValue = float(value)
        return float(value)
    if kind == "String":
        cell.Value = str(value)
        return str(value)
    raise ValueError(f"{label} holds {kind} data and cannot be written.")


def read_cell(cell) -> Union[int, float, str]:
    kind = str(cell.DataType)
    if kind == "Integer":
        return int(cell.IntegerValue)
    if kind == "Double":
        return float(cell.DoubleValue)
    return str(cell.Value)
