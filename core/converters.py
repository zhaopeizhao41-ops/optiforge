"""
Zemax OpticStudio MCP - Core Converters and Utilities
Handles type conversions between .NET/ZOS-API and native Python structures.
"""

from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np


def dotnet_2d_to_python(data: Any, rows: int, cols: int, transpose: bool = False) -> List[List[float]]:
    """Convert a .NET System.Double[,] multi-dimensional array into a native Python 2D list."""
    if data is None:
        return []
    
    # Flatten or iterate
    flat = list(data)
    result = []
    for r in range(rows):
        start = r * cols
        row = flat[start : start + cols]
        result.append(row)
        
    if transpose and result:
        return [list(col) for col in zip(*result)]
    return result


def clean_zemax_text(text: str) -> str:
    """Clean Zemax exported text file content, normalizing line endings and unicode chars."""
    if not text:
        return ""
    # Replace non-breaking spaces or strange encodings if needed
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    return cleaned.strip()


def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely parse a float value."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    """Safely parse an integer value."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default
