"""
Zemax OpticStudio Optimization Operand Knowledge Base
Based on the Zemax OpticStudio User Manual (Chapter on Optimization & Operands).
"""

from typing import Any, Dict, List, Optional

OPERAND_DATABASE: Dict[str, Dict[str, Any]] = {
    # --- 1. First-Order System Operands ---
    "EFFL": {
        "name": "Effective Focal Length",
        "category": "First Order",
        "description": "System effective focal length in lens units. Usually used to constrain system focal length during optimization.",
        "params": ["Wave (Int)", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "System required focal length (e.g. 50.0, 100.0)",
    },
    "EFLA": {
        "name": "Effective Focal Length in Air",
        "category": "First Order / Modular Subsystems",
        "description": "Calculates the effective focal length in air between surface Surf1 and surface Surf2. Crucial for modular multi-group designs to lock individual subsystem focal lengths without cross-module drift.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Subsystem target focal length (e.g. 50.0 for scan lens, 100.0 for tube lens)",
    },
    "TOTR": {
        "name": "Total Track",
        "category": "First Order / Boundary",
        "description": "Total optical track length from surface 1 to the image surface.",
        "params": ["0", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Maximum allowed mechanical/optical length",
    },
    "WFNO": {
        "name": "Working F/#",
        "category": "First Order",
        "description": "Working F-number in image space, accounting for conjugate distance and aberration.",
        "params": ["Wave (Int)", "0", "0", "0"],
        "unit": "Dimensionless",
        "typical_target": "Target optical speed (e.g. 2.8, 4.0)",
    },
    "EPDI": {
        "name": "Entrance Pupil Diameter",
        "category": "First Order",
        "description": "Entrance pupil diameter of the system.",
        "params": ["0", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Target clear aperture / entrance pupil",
    },
    "EXPP": {
        "name": "Exit Pupil Position",
        "category": "First Order",
        "description": "Position of the exit pupil relative to the image surface. Important for chief ray angle (CRA) sensor matching.",
        "params": ["0", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Sensor chief ray angle match distance",
    },
    "ENPZ": {
        "name": "Entrance Pupil Position",
        "category": "First Order",
        "description": "Distance from surface 1 to the entrance pupil.",
        "params": ["0", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Front aperture position",
    },
    "ISFN": {
        "name": "Image Space F/#",
        "category": "First Order",
        "description": "Paraxial image space F-number at infinite conjugate.",
        "params": ["Wave (Int)", "0", "0", "0"],
        "unit": "Dimensionless",
        "typical_target": "Focal ratio",
    },
    "PMAG": {
        "name": "Paraxial Magnification",
        "category": "First Order",
        "description": "Paraxial transverse magnification for finite conjugate systems.",
        "params": ["Wave (Int)", "0", "0", "0"],
        "unit": "Dimensionless",
        "typical_target": "Target reduction/magnification (e.g. -0.1, -1.0)",
    },

    # --- 2. Physical & Boundary Constraints ---
    "MNCA": {
        "name": "Minimum Center Air",
        "category": "Boundary",
        "description": "Enforces a minimum center thickness for air spaces between surfaces Surf1 and Surf2.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": ">= 0.1 ~ 0.5 mm to prevent lens-to-lens collision",
    },
    "MXCA": {
        "name": "Maximum Center Air",
        "category": "Boundary",
        "description": "Enforces a maximum center thickness for air spaces between surfaces Surf1 and Surf2.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Upper limit for packaging constraints",
    },
    "MNEA": {
        "name": "Minimum Edge Air",
        "category": "Boundary",
        "description": "Enforces a minimum edge thickness for air spaces between surfaces Surf1 and Surf2.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": ">= 0.5 ~ 1.0 mm to avoid physical contact at lens rims",
    },
    "MXEA": {
        "name": "Maximum Edge Air",
        "category": "Boundary",
        "description": "Enforces a maximum edge thickness for air spaces between surfaces Surf1 and Surf2.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Upper limit for mechanical spacers",
    },
    "MNCG": {
        "name": "Minimum Center Glass",
        "category": "Boundary",
        "description": "Enforces a minimum center thickness for glass elements between surfaces Surf1 and Surf2.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": ">= 1.0 ~ 2.0 mm to allow safe optical grinding and polishing",
    },
    "MXCG": {
        "name": "Maximum Center Glass",
        "category": "Boundary",
        "description": "Enforces a maximum center thickness for glass elements between surfaces Surf1 and Surf2.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "<= 15.0 ~ 25.0 mm to control glass weight, bubble risk, and cost",
    },
    "MNEG": {
        "name": "Minimum Edge Glass",
        "category": "Boundary",
        "description": "Enforces a minimum edge thickness for glass elements between surfaces Surf1 and Surf2. Crucial to prevent knife-edge chipping.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": ">= 1.0 ~ 1.5 mm for beveling and mechanical mounting",
    },
    "MXEG": {
        "name": "Maximum Edge Glass",
        "category": "Boundary",
        "description": "Enforces a maximum edge thickness for glass elements between surfaces Surf1 and Surf2.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Upper limit for element envelope",
    },
    "CTGT": {
        "name": "Center Thickness Greater Than",
        "category": "Boundary",
        "description": "Constrains center thickness of surface Surf to be greater than Target.",
        "params": ["Surf (Int)", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Target thickness limit",
    },
    "CTLT": {
        "name": "Center Thickness Less Than",
        "category": "Boundary",
        "description": "Constrains center thickness of surface Surf to be less than Target. Expert tool to strictly pin runaway air gaps.",
        "params": ["Surf (Int)", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "<= 8.0 ~ 12.0 mm for internal air spaces to prevent barrel bloat",
    },
    "ETGT": {
        "name": "Edge Thickness Greater Than",
        "category": "Boundary",
        "description": "Constrains edge thickness of surface Surf to be greater than Target.",
        "params": ["Surf (Int)", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Target edge thickness limit",
    },
    "ETLT": {
        "name": "Edge Thickness Less Than",
        "category": "Boundary",
        "description": "Constrains edge thickness of surface Surf to be less than Target.",
        "params": ["Surf (Int)", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Upper limit for edge thickness",
    },
    "TTHI": {
        "name": "Total Thickness of Surface Range",
        "category": "Boundary / First Order",
        "description": "Measures total axial thickness from Surf1 to Surf2. Crucial for constraining the core optical barrel stack length.",
        "params": ["Surf1 (Int)", "Surf2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "<= 0.25 ~ 0.35 * EFL for compact lens barrels",
    },
    "OPLT": {
        "name": "Operand Less Than",
        "category": "Mathematical / Boundary",
        "description": "Penalizes if the operand in Row is greater than Target. Operates as an inequality upper ceiling.",
        "params": ["Row (Int)", "0", "0", "0"],
        "unit": "Same as target operand",
        "typical_target": "Upper limit value",
    },
    "OPGT": {
        "name": "Operand Greater Than",
        "category": "Mathematical / Boundary",
        "description": "Penalizes if the operand in Row is less than Target. Operates as an inequality lower floor.",
        "params": ["Row (Int)", "0", "0", "0"],
        "unit": "Same as target operand",
        "typical_target": "Lower limit value",
    },
    "DMVA": {
        "name": "Diameter Maximum Value",
        "category": "Boundary",
        "description": "Constrains the semi-diameter of surface Surf to be less than or equal to Target.",
        "params": ["Surf (Int)", "0", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "Mechanical barrel bore radius",
    },

    # --- 3. Third-Order Seidel Aberrations ---
    "SPHA": {
        "name": "Spherical Aberration (Seidel W040)",
        "category": "Aberration",
        "description": "Third-order spherical aberration contribution for surface Surf, or sum if Surf=0.",
        "params": ["Surf (Int)", "Wave (Int)", "0", "0"],
        "unit": "Waves or Lens units",
        "typical_target": "0.0",
    },
    "COMA": {
        "name": "Coma (Seidel W131)",
        "category": "Aberration",
        "description": "Third-order coma contribution for surface Surf, or sum if Surf=0.",
        "params": ["Surf (Int)", "Wave (Int)", "0", "0"],
        "unit": "Waves or Lens units",
        "typical_target": "0.0",
    },
    "ASTI": {
        "name": "Astigmatism (Seidel W222)",
        "category": "Aberration",
        "description": "Third-order astigmatism contribution for surface Surf, or sum if Surf=0.",
        "params": ["Surf (Int)", "Wave (Int)", "0", "0"],
        "unit": "Waves or Lens units",
        "typical_target": "0.0",
    },
    "FCUR": {
        "name": "Field Curvature (Petzval W220)",
        "category": "Aberration",
        "description": "Third-order field curvature contribution for surface Surf, or sum if Surf=0.",
        "params": ["Surf (Int)", "Wave (Int)", "0", "0"],
        "unit": "Waves or Lens units",
        "typical_target": "0.0",
    },
    "DIST": {
        "name": "Distortion (Seidel W311)",
        "category": "Aberration",
        "description": "Third-order distortion percentage or contribution.",
        "params": ["Surf (Int)", "Wave (Int)", "0", "0"],
        "unit": "Percent or waves",
        "typical_target": "0.0 or within acceptable tolerance (e.g. < 1%)",
    },
    "AXCL": {
        "name": "Axial Chromatic Aberration",
        "category": "Aberration",
        "description": "Axial color difference between two wavelengths Wave1 and Wave2.",
        "params": ["Wave1 (Int)", "Wave2 (Int)", "0", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "0.0",
    },
    "LACS": {
        "name": "Lateral Chromatic Aberration",
        "category": "Aberration",
        "description": "Lateral color difference at specified field.",
        "params": ["Wave1 (Int)", "Wave2 (Int)", "Field (Int)", "0"],
        "unit": "Lens units (mm)",
        "typical_target": "0.0",
    },

    # --- 4. MTF & Wavefront Operands ---
    "MTFT": {
        "name": "Tangential MTF",
        "category": "MTF",
        "description": "Tangential Modulation Transfer Function at specified spatial frequency (cycles/mm).",
        "params": ["Freq (Double)", "Wave (Int)", "Field (Int)", "Grid (Int)"],
        "unit": "Modulation (0.0 to 1.0)",
        "typical_target": "e.g. > 0.3 at Nyquist frequency",
    },
    "MTFS": {
        "name": "Sagittal MTF",
        "category": "MTF",
        "description": "Sagittal Modulation Transfer Function at specified spatial frequency (cycles/mm).",
        "params": ["Freq (Double)", "Wave (Int)", "Field (Int)", "Grid (Int)"],
        "unit": "Modulation (0.0 to 1.0)",
        "typical_target": "e.g. > 0.3 at Nyquist frequency",
    },
    "MTFA": {
        "name": "Average MTF",
        "category": "MTF",
        "description": "Average of Tangential and Sagittal MTF.",
        "params": ["Freq (Double)", "Wave (Int)", "Field (Int)", "Grid (Int)"],
        "unit": "Modulation (0.0 to 1.0)",
        "typical_target": "Target average MTF value",
    },
    "OPDX": {
        "name": "Optical Path Difference",
        "category": "Wavefront",
        "description": "Optical path difference (aberration) of ray defined by normalized pupil (Px, Py) and field (Hx, Hy).",
        "params": ["Wave (Int)", "Field (Int)", "Hx", "Hy", "Px", "Py"],
        "unit": "Waves",
        "typical_target": "0.0",
    },
    "STRH": {
        "name": "Strehl Ratio",
        "category": "Wavefront / Diffraction",
        "description": "Strehl ratio at specified field and wavelength.",
        "params": ["Wave (Int)", "Field (Int)", "0", "0"],
        "unit": "Ratio (0.0 to 1.0)",
        "typical_target": "> 0.8 for diffraction-limited systems",
    },
}


def get_operand_info(code: str) -> Optional[Dict[str, Any]]:
    """Look up full operand definition and guidelines from the knowledge base."""
    return OPERAND_DATABASE.get(code.upper().strip())


def search_operands(query: str) -> List[Dict[str, Any]]:
    """Search operands by keyword, category, or code."""
    query = query.lower().strip()
    matches = []
    for code, info in OPERAND_DATABASE.items():
        if (
            query in code.lower()
            or query in info["name"].lower()
            or query in info["description"].lower()
            or query in info["category"].lower()
        ):
            match_dict = {"code": code}
            match_dict.update(info)
            matches.append(match_dict)
    return matches
