"""
Zemax Optical Design Rules & Engineering Validation Engine
Extracted from the Zemax OpticStudio User Manual and Standard Optical Manufacturing Guidelines.
"""

import math
from typing import Any, Dict, List, Optional


class OpticalRuleCheck:
    def __init__(self):
        # Default manufacturing tolerances (in mm)
        self.MIN_GLASS_CENTER_THICKNESS = 1.0  # mm
        self.MIN_GLASS_EDGE_THICKNESS = 1.0  # mm
        self.MIN_AIR_CENTER_SPACE = 0.1  # mm
        self.MIN_AIR_EDGE_SPACE = 0.5  # mm
        self.MIN_ASPECT_RATIO = 0.08  # CT / Clear Diameter
        self.RAY_AIMING_FIELD_THRESHOLD = 20.0  # degrees

    def calculate_airy_disk_radius_um(self, wavelength_um: float, f_number: float) -> float:
        """Calculate Airy disk radius in micrometers: r_airy = 1.22 * lambda * F/#."""
        if f_number <= 0:
            return 0.0
        return 1.22 * wavelength_um * f_number

    def estimate_strehl_ratio(self, rms_wavefront_error_waves: float) -> float:
        """
        Estimate Strehl ratio from RMS wavefront error (in waves) using the Marechal approximation:
        S ~= exp( - (2 * pi * W_rms)^2 )
        """
        if rms_wavefront_error_waves < 0:
            return 0.0
        exponent = -((2.0 * math.pi * rms_wavefront_error_waves) ** 2)
        if exponent < -50:
            return 0.0
        return math.exp(exponent)

    def compute_edge_thickness(
        self,
        r1: float,
        r2: float,
        ct: float,
        semi_dia: float,
    ) -> float:
        """
        Approximate edge thickness for a spherical element given radii, center thickness, and semi-diameter.
        Sag = R - sign(R) * sqrt(R^2 - y^2) if |y| < |R| else 0.
        ET = CT - Sag1 + Sag2.
        """
        def sag(r: float, y: float) -> float:
            if abs(r) < 1e-9:
                return 0.0
            if abs(y) >= abs(r):
                # Ray exceeds hemisphere: return r (which retains correct sign: positive if r>0, negative if r<0)
                return r
            sign = 1.0 if r > 0 else -1.0
            return r - sign * math.sqrt(r * r - y * y)

        sag1 = sag(r1, semi_dia)
        sag2 = sag(r2, semi_dia)
        return ct - sag1 + sag2


    def validate_system(self, system_summary: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute comprehensive validation checks against Zemax Manual engineering guidelines.
        Returns detailed checklist with PASS, WARNING, and CRITICAL statuses.
        """
        findings: List[Dict[str, Any]] = []
        surfaces = system_summary.get("surfaces", [])
        general = system_summary.get("general", {})
        fields = system_summary.get("fields", [])
        wavelengths = system_summary.get("wavelengths", [])
        
        # 1. Glass & Air Thickness Inspection
        for idx in range(1, len(surfaces) - 1):
            surf = surfaces[idx]
            next_surf = surfaces[idx + 1]
            
            thickness = surf.get("thickness", 0.0)
            material = (surf.get("material") or "").strip()
            is_glass = bool(material and material.upper() not in ["AIR", ""])
            semi_dia = max(surf.get("semi_diameter", 0.0), next_surf.get("semi_diameter", 0.0))
            dia = 2.0 * semi_dia

            r1 = surf.get("radius", 0.0)
            r2 = next_surf.get("radius", 0.0)
            
            # Approximate edge thickness
            approx_et = self.compute_edge_thickness(r1, r2, thickness, semi_dia)

            if is_glass:
                # Glass element checks
                if thickness < self.MIN_GLASS_CENTER_THICKNESS:
                    findings.append({
                        "level": "WARNING",
                        "rule": "Glass Center Thickness",
                        "surface": surf.get("index"),
                        "message": f"Surface {surf.get('index')} ({material}) center thickness {thickness:.3f} mm < minimum {self.MIN_GLASS_CENTER_THICKNESS} mm. Risk of lens bending/cracking during polishing.",
                        "fix": f"Increase center thickness or constrain in Merit Function with MNCG/CTGT."
                    })
                
                if approx_et <= 0.0:
                    findings.append({
                        "level": "CRITICAL",
                        "rule": "Negative Edge Thickness (Knife-Edge)",
                        "surface": surf.get("index"),
                        "message": f"Surface {surf.get('index')} ({material}) has negative edge thickness ({approx_et:.3f} mm)! Surfaces self-intersect geometrically.",
                        "fix": "Increase center thickness, adjust surface curvature radii, or add MNEG/ETGT operands to merit function."
                    })
                elif approx_et < self.MIN_GLASS_EDGE_THICKNESS:
                    findings.append({
                        "level": "WARNING",
                        "rule": "Thin Edge Thickness",
                        "surface": surf.get("index"),
                        "message": f"Surface {surf.get('index')} ({material}) edge thickness {approx_et:.3f} mm < {self.MIN_GLASS_EDGE_THICKNESS} mm. Edge chipping risk during mounting.",
                        "fix": f"Increase edge thickness using MNEG operand (target >= {self.MIN_GLASS_EDGE_THICKNESS} mm)."
                    })

                if dia > 0 and (thickness / dia) < self.MIN_ASPECT_RATIO:
                    findings.append({
                        "level": "INFO",
                        "rule": "Aspect Ratio (CT/Dia)",
                        "surface": surf.get("index"),
                        "message": f"Surface {surf.get('index')} aspect ratio CT/Dia = {(thickness/dia):.3f} < {self.MIN_ASPECT_RATIO}. Flexible thin lens warning.",
                        "fix": "Verify element rigidity for optical fabrication."
                    })
            else:
                # Air space checks
                if thickness < self.MIN_AIR_CENTER_SPACE:
                    findings.append({
                        "level": "WARNING",
                        "rule": "Air Center Spacing",
                        "surface": surf.get("index"),
                        "message": f"Air space after surface {surf.get('index')} center distance {thickness:.3f} mm < {self.MIN_AIR_CENTER_SPACE} mm. Risk of collision under thermal expansion.",
                        "fix": f"Add MNCA operand with target >= {self.MIN_AIR_CENTER_SPACE} mm."
                    })
                if approx_et < self.MIN_AIR_EDGE_SPACE and thickness > 0:
                    findings.append({
                        "level": "WARNING",
                        "rule": "Air Edge Spacing",
                        "surface": surf.get("index"),
                        "message": f"Air space after surface {surf.get('index')} edge clearance {approx_et:.3f} mm < {self.MIN_AIR_EDGE_SPACE} mm.",
                        "fix": f"Add MNEA operand with target >= {self.MIN_AIR_EDGE_SPACE} mm."
                    })

        # 2. Ray Aiming Check
        field_type = general.get("field_type", "")
        max_angle = 0.0
        if "angle" in field_type.lower():
            for f in fields:
                ang = math.sqrt(f.get("x", 0.0)**2 + f.get("y", 0.0)**2)
                if ang > max_angle:
                    max_angle = ang
            
            ray_aiming = general.get("ray_aiming", "Off")
            if max_angle >= self.RAY_AIMING_FIELD_THRESHOLD and str(ray_aiming).lower() in ["off", "none", "0"]:
                findings.append({
                    "level": "RECOMMENDATION",
                    "rule": "Ray Aiming Requirement",
                    "surface": "System",
                    "message": f"Maximum field angle is {max_angle:.1f} degrees (>= {self.RAY_AIMING_FIELD_THRESHOLD} deg) but Ray Aiming is currently OFF. Paraxial entrance pupil tracing will suffer from pupil aberration.",
                    "fix": "Enable Ray Aiming (Paraxial or Real) via zemax_set_ray_aiming tool to ensure accurate ray pupil illumination."
                })

        # 3. Overall status summary
        critical_count = sum(1 for f in findings if f["level"] == "CRITICAL")
        warning_count = sum(1 for f in findings if f["level"] == "WARNING")
        
        status = "PASS"
        if critical_count > 0:
            status = "CRITICAL_ISSUES_FOUND"
        elif warning_count > 0:
            status = "WARNINGS_FOUND"

        return {
            "status": status,
            "critical_count": critical_count,
            "warning_count": warning_count,
            "total_findings": len(findings),
            "findings": findings,
        }
