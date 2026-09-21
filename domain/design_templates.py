"""
Optical Design Templates & Starting Architectures
Standard pre-built optical configurations based on Zemax Application Notes.
"""

from typing import Any, Dict, List


def get_template(name: str) -> Dict[str, Any]:
    """Retrieve predefined optical design parameters for a classic optical archetype."""
    templates = {
        "singlet_bk7": {
            "name": "N-BK7 Plano-Convex Singlet",
            "description": "Simple positive singlet lens, focal length ~ 100mm, F/4, EPD 25mm.",
            "aperture": {"type": "EPD", "value": 25.0},
            "fields": [{"x": 0.0, "y": 0.0, "weight": 1.0}],
            "wavelengths": [0.58756],
            "surfaces": [
                {"radius": 51.68, "thickness": 5.0, "material": "N-BK7", "comment": "Front Surface"},
                {"radius": 0.0, "thickness": 96.6, "material": "", "comment": "Rear Flat Surface"},
            ],
            "stop_surface": 1,
        },
        "achromat_doublet": {
            "name": "Fraunhofer Achromatic Doublet",
            "description": "Cemented achromatic doublet (N-BK7 crown + N-SF11 flint), corrected for primary chromatic aberration, EFL ~ 100mm, F/4.",
            "aperture": {"type": "EPD", "value": 25.0},
            "fields": [
                {"x": 0.0, "y": 0.0, "weight": 1.0},
                {"x": 0.0, "y": 2.0, "weight": 1.0},
            ],
            "wavelengths": [0.58756, 0.48613, 0.65627],  # d, F, C
            "surfaces": [
                {"radius": 62.0, "thickness": 6.0, "material": "N-BK7", "comment": "Crown Front"},
                {"radius": -45.0, "thickness": 3.0, "material": "N-SF11", "comment": "Cemented Interface"},
                {"radius": -120.0, "thickness": 95.0, "material": "", "comment": "Flint Rear"},
            ],
            "stop_surface": 1,
        },
        "cooke_triplet": {
            "name": "Classic Cooke Triplet",
            "description": "Three-element anastigmat objective with central stop, EFL ~ 50mm, F/4.5, 40-degree field.",
            "aperture": {"type": "EPD", "value": 11.1},
            "fields": [
                {"x": 0.0, "y": 0.0, "weight": 1.0},
                {"x": 0.0, "y": 14.0, "weight": 1.0},
                {"x": 0.0, "y": 20.0, "weight": 1.0},
            ],
            "wavelengths": [0.58756, 0.48613, 0.65627],
            "surfaces": [
                {"radius": 22.0, "thickness": 3.2, "material": "N-SK16", "comment": "L1 Front"},
                {"radius": -435.0, "thickness": 5.5, "material": "", "comment": "Air Space 1"},
                {"radius": -22.2, "thickness": 1.1, "material": "N-F2", "comment": "L2 Diverging Front"},
                {"radius": 20.3, "thickness": 4.7, "material": "", "comment": "Air Space 2 (Stop)"},
                {"radius": 79.7, "thickness": 2.8, "material": "N-SK16", "comment": "L3 Converging Front"},
                {"radius": -18.4, "thickness": 42.0, "material": "", "comment": "L3 Rear"},
            ],
            "stop_surface": 4,
        },
    }
    key = name.lower().strip()
    if key not in templates:
        raise ValueError(f"Unknown template '{name}'. Available templates: {list(templates.keys())}")
    return templates[key]


def list_templates() -> List[Dict[str, str]]:
    """List available pre-built optical templates."""
    return [
        {"id": "singlet_bk7", "name": "N-BK7 Plano-Convex Singlet", "f_number": "F/4", "elements": "1"},
        {"id": "achromat_doublet", "name": "Fraunhofer Achromatic Doublet", "f_number": "F/4", "elements": "2"},
        {"id": "cooke_triplet", "name": "Classic Cooke Triplet", "f_number": "F/4.5", "elements": "3"},
    ]
