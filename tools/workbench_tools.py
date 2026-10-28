"""Read-only workbench view of the active native model."""
from core.operation_guard import serialized_operation
from core.workbench_snapshot import capture_snapshot


@serialized_operation
def zemax_get_workbench_snapshot():
    """Read prescription, units, fields, wavelengths and supported YZ contours."""
    return capture_snapshot()
