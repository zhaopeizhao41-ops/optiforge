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
        self.MAX_INTERNAL_AIR_SPACE = 15.0  # mm (Internal element-to-element air gap limit)
        self.RECOMMENDED_AIR_SPACE = 12.0  # mm
        self.MAX_BARREL_ASPECT_RATIO = 2.5  # L_barrel / Max_Diameter
        self.MIN_ASPECT_RATIO = 0.08  # CT / Clear Diameter
        self.RAY_AIMING_FIELD_THRESHOLD = 20.0  # degrees

        # Fabrication limits from the shop floor (Edmund Optics DFM notes; Karow 2004 for
        # the centering factor). These decide cost and yield, not validity of the prescription.
        self.MIN_EDGE_THICKNESS_MM = 0.7  # measured at the clear aperture + 1 mm
        self.KAROW_SELF_CENTER_MIN = 0.56  # below this, bell-chuck centering fails
        self.CONCENTRICITY_MIN_MM = 2.0  # material to remove while centering
        self.HEMISPHERIC_STEEPNESS = 1.4  # |R| <= 1.4 * semi = 0.7 * diameter
        self.NEAR_FLAT_SAG_UM = 100.0  # a sag this small cannot be tested with a test plate
        self.DIA_CT_INFO = 10.0  # D:CT above this starts costing money
        self.DIA_CT_WARN = 15.0

        # Image-quality acceptance criteria.
        self.STREHL_DIFFRACTION_LIMITED = 0.8  # Maréchal; RMS = lambda/13.4
        self.MARECHAL_RMS_WAVES = 1.0 / 14.0  # lambda/14 -> Strehl ~0.82

    def calculate_airy_disk_radius_um(self, wavelength_um: float, f_number: float) -> float:
        """Calculate Airy disk radius in micrometers: r_airy = 1.22 * lambda * F/#."""
        if f_number <= 0:
            return 0.0
        return 1.22 * wavelength_um * f_number

    # ------------------------------------------------------------------
    # Pure helpers: no ZOS-API, no state. Every one of these is unit-testable offline
    # and is the single place the formula lives, so a design walkthrough and a gate
    # cannot drift apart.
    # ------------------------------------------------------------------

    def rms_for_strehl(self, strehl: float) -> float:
        """Inverse Maréchal: the RMS wavefront error (waves) that yields a given Strehl.

        S ~= exp(-(2*pi*W)^2)  =>  W = sqrt(-ln S) / (2*pi).
        S = 0.8 -> 1/13.4 waves; S = 0.82 -> 1/14 waves.
        """
        if strehl <= 0 or strehl > 1:
            raise ValueError("Strehl ratio must be in (0, 1].")
        return math.sqrt(-math.log(strehl)) / (2.0 * math.pi)

    def karow_factor(self, d_front: float, r_front: float, d_rear: float, r_rear: float) -> float:
        """Karow centering factor Z = |D1/R1 + D2/R2|.

        D is the bell-chuck (clear-aperture) diameter, R is the surface radius with the
        usual convex-positive / concave-negative sign. Z > 0.56 means the element locates
        itself in an automated bell chuck; below it the optician centers by hand, which is
        expensive and adds schedule risk.
        """
        total = 0.0
        for dia, radius in ((d_front, r_front), (d_rear, r_rear)):
            if radius and math.isfinite(radius) and abs(radius) > 1e-9:
                total += dia / radius
        return abs(total)

    def concentricity_margin(self, r1: float, r2: float, ct: float) -> float:
        """Material available for centering between two same-sign radii, in mm.

        |R1 - R2 - CT| for convex (R > 0) surfaces, |R1 - R2 + CT| for concave. Below
        ~2 mm the centering operation has to remove too much glass to correct
        surface-to-surface decentering. Returns inf when the radii are not concentric
        (opposite signs or a flat), i.e. the rule does not apply.
        """
        if not all(math.isfinite(v) for v in (r1, r2, ct)):
            return float("inf")
        if r1 == 0 or r2 == 0 or (r1 > 0) != (r2 > 0):
            return float("inf")
        return abs(r1 - r2 - ct) if r1 > 0 else abs(r1 - r2 + ct)

    def grating_diffraction_angle_deg(
        self, lines_per_um: float, wavelength_um: float, incidence_deg: float, order: int = 1
    ) -> float:
        """Diffracted angle from the grating equation d(sin i + sin theta) = m*lambda.

        d is the groove spacing in um (1 / lines_per_um). Returns nan when the order does
        not propagate.
        """
        if lines_per_um <= 0:
            raise ValueError("lines_per_um must be positive.")
        if order == 0:
            return -incidence_deg
        d = 1.0 / lines_per_um
        sin_theta = order * wavelength_um / d - math.sin(math.radians(incidence_deg))
        if abs(sin_theta) > 1.0:
            return float("nan")
        return math.degrees(math.asin(sin_theta))

    def reciprocal_linear_dispersion_nm_per_mm(
        self, lines_per_um: float, focal_length_mm: float, diffracted_angle_deg: float, order: int = 1
    ) -> float:
        """d(lambda)/dx = d*cos(theta) / (m*f), in nm of wavelength per mm on the detector.

        This is the number a spectrograph datasheet quotes; it converts slit width and
        pixel pitch into bandpass and resolution.
        """
        if lines_per_um <= 0 or focal_length_mm <= 0 or order == 0:
            raise ValueError("lines_per_um, focal_length_mm and order must be non-zero.")
        d_mm = 1.0 / (lines_per_um * 1000.0)
        return d_mm * math.cos(math.radians(diffracted_angle_deg)) / (abs(order) * focal_length_mm) * 1e6

    def spectral_bandpass_nm(self, slit_width_mm: float, dispersion_nm_per_mm: float) -> float:
        """Bandpass admitted by the entrance slit: slit width x reciprocal dispersion."""
        return abs(slit_width_mm) * abs(dispersion_nm_per_mm)

    def detector_focal_length_mm(
        self, pixels: float, pixel_size_um: float, lines_per_um: float,
        diffracted_angle_deg: float, span_nm: float, order: int = 1,
    ) -> float:
        """Focusing focal length that fits a spectral span onto a detector.

        From the grating equation, a span of `span_nm` spread over the detector's total
        length L needs f = L*d*cos(theta) / (m*span), with d the groove spacing. Solve this
        before laying out the camera, so the focusing mirror is sized by the detector
        instead of the detector being chosen to fit an arbitrary mirror.
        """
        if span_nm <= 0 or lines_per_um <= 0 or order == 0:
            raise ValueError("span_nm, lines_per_um and order must be non-zero.")
        length_mm = pixels * pixel_size_um / 1000.0
        d_mm = 1.0 / (lines_per_um * 1000.0)
        return length_mm * d_mm * math.cos(math.radians(diffracted_angle_deg)) / (abs(order) * span_nm / 1e6)

    def mirror_spherical_wavefront_waves(self, ymax_mm: float, radius_mm: float, wavelength_um: float) -> float:
        """Peak spherical-aberration wavefront of a spherical mirror: W = y^4 / (8 R^3).

        Compare against lambda/4 (Rayleigh) to see whether a spherical mirror can carry
        the aperture at all, or whether the layout needs a parabola or a longer radius.
        """
        if radius_mm == 0 or wavelength_um <= 0:
            raise ValueError("radius_mm and wavelength_um must be non-zero.")
        return abs(ymax_mm ** 4 / (8.0 * radius_mm ** 3)) / (wavelength_um * 1e-3)

    def shafer_coma_free_angle_deg(
        self, i2_deg: float, r2: float, r4: float, grating_incidence_deg: float,
        grating_diffraction_deg: float, max_iter: int = 100,
    ) -> float:
        """Shafer condition: the focusing-mirror angle I4 that zeroes coma at one wavelength.

            sin I4 / sin I2 = (r4/r2)^2 * (cos^3 I4 / cos^3 I2) * (cos^3 i / cos^3 theta)

        (Shafer, Megill & Droppleman, JOSA 54:879, 1964.) I4 appears on both sides, so the
        relation is implicit and is solved here by bisection on [0, 89.9 deg]; the residual
        is verified rather than assumed. Applied to a Czerny-Turner pair of spherical
        mirrors with r2 the collimator and r4 the focusing mirror; coma is corrected at the
        design wavelength only, at the cost of astigmatism, so it is a starting point for
        optimization and not an end point.
        """
        if r2 == 0 or r4 == 0 or abs(r2) < 1e-9 or abs(r4) < 1e-9:
            raise ValueError("r2 and r4 must be non-zero.")
        if abs(math.cos(math.radians(i2_deg))) < 1e-9:
            raise ValueError("i2_deg too close to 90 degrees.")
        k = (
            math.sin(math.radians(i2_deg))
            * (r4 / r2) ** 2
            / math.cos(math.radians(i2_deg)) ** 3
            * math.cos(math.radians(grating_incidence_deg)) ** 3
            / math.cos(math.radians(grating_diffraction_deg)) ** 3
        )

        def residual(angle_deg: float) -> float:
            return math.sin(math.radians(angle_deg)) - k * math.cos(math.radians(angle_deg)) ** 3

        lo, hi = 0.0, 89.9
        if residual(lo) * residual(hi) > 0:
            return float("nan")
        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            if residual(lo) * residual(mid) <= 0:
                hi = mid
            else:
                lo = mid
        return 0.5 * (lo + hi)

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

    def surface_sag_mm(self, radius: float, semi_dia: float) -> float:
        """Signed sagitta at the clear semi-diameter in mm (positive for R > 0).

        A hyper-hemispheric surface (|y| >= |R|) has no finite sagitta and returns 0; its
        shape is reported separately by the steepness checks.
        """
        if not math.isfinite(radius) or radius == 0 or abs(semi_dia) >= abs(radius):
            return 0.0
        sign = 1.0 if radius > 0 else -1.0
        return radius - sign * math.sqrt(radius * radius - semi_dia * semi_dia)

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
        
        # Identify glass surface indices to classify internal vs external spaces
        glass_indices = [
            i for i, s in enumerate(surfaces)
            if (s.get("material") or "").strip().upper() not in ["AIR", ""]
        ]
        first_glass = glass_indices[0] if glass_indices else None
        last_glass = glass_indices[-1] if glass_indices else None

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

                # Diameter:center-thickness. A shop quotes thin blanks at a premium because
                # the blank flexes under polishing pressure (wedge, mid-spatial ripple).
                if dia > 0 and thickness > 0:
                    dct = dia / thickness
                    if dct > self.DIA_CT_WARN:
                        findings.append({
                            "level": "WARNING",
                            "rule": "Extreme Diameter:Thickness Ratio",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} ({material}) D:CT = {dct:.1f}:1, past {self.DIA_CT_WARN:.0f}:1. Thin blanks flex under polishing pressure and come out wedged; polishing time and cost rise steeply.",
                            "fix": "Thicken the element or reduce the diameter; add MNCG/MXCG operands to hold the thickness during optimization."
                        })
                    elif dct > self.DIA_CT_INFO:
                        findings.append({
                            "level": "INFO",
                            "rule": "Diameter:Thickness Ratio",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} ({material}) D:CT = {dct:.1f}:1, above {self.DIA_CT_INFO:.0f}:1 where polishing cost starts to climb.",
                            "fix": "Acceptable for a prototype; consider a thicker blank for production quantities."
                        })

                # Karow centering factor: can the element self-center in a bell chuck?
                surf_semi_for_karow = max(surf.get("semi_diameter", 0.0), next_surf.get("semi_diameter", 0.0))
                chuck_dia = 2.0 * surf_semi_for_karow
                if chuck_dia > 0:
                    karow = self.karow_factor(chuck_dia, r1, chuck_dia, r2)
                    if karow < self.KAROW_SELF_CENTER_MIN:
                        findings.append({
                            "level": "WARNING",
                            "rule": "Karow Centering Factor",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} ({material}) Karow factor Z = |D1/R1 + D2/R2| = {karow:.2f} < {self.KAROW_SELF_CENTER_MIN}. The element will not self-center in an automated bell chuck and needs manual centering (cost and schedule risk).",
                            "fix": "Bend the element into a meniscus, or split the power so the two surfaces are not nearly concentric."
                        })

                # Concentric radii: how much glass must come off while centering?
                margin = self.concentricity_margin(r1, r2, thickness)
                if margin < self.CONCENTRICITY_MIN_MM:
                    findings.append({
                        "level": "WARNING",
                        "rule": "Concentric Radii (Centering Feasibility)",
                        "surface": surf.get("index"),
                        "message": f"Surface {surf.get('index')} ({material}) concentricity margin |R1 - R2 -/+ CT| = {margin:.2f} mm < {self.CONCENTRICITY_MIN_MM} mm. Centering has to remove a large amount of material to correct surface-to-surface decentering.",
                        "fix": "Make the radii less concentric (change one radius) or relax the centering tolerance class."
                    })

                # Test plate steepness ratio check: |R| / Semi-Diameter >= 1.2
                surf_semi = surf.get("semi_diameter", 0.0)
                if abs(r1) > 1e-4 and surf_semi > 0:
                    steepness = abs(r1) / surf_semi
                    if steepness < 1.0:
                        findings.append({
                            "level": "CRITICAL",
                            "rule": "Hyper-Hemispherical Surface (Untestable Deep Bowl)",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} radius |R| = {abs(r1):.2f} mm < Semi-Diameter ({surf_semi:.2f} mm), steepness ratio {steepness:.2f} < 1.0! Surface is an untestable hyper-hemisphere.",
                            "fix": "Increase radius of curvature |R| >= 1.2 * Semi-Diameter or split lens into two elements."
                        })
                    elif steepness < 1.2:
                        findings.append({
                            "level": "WARNING",
                            "rule": "Steep Surface Curvature",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} radius |R| = {abs(r1):.2f} mm, steepness ratio |R|/y = {steepness:.2f} < 1.2. High tooling cost and coating non-uniformity risk.",
                            "fix": "Aim for |R| >= 1.2 ~ 1.5 * Semi-Diameter using test plate fitting or power splitting."
                        })
                    elif steepness < self.HEMISPHERIC_STEEPNESS:
                        findings.append({
                            "level": "WARNING",
                            "rule": "Approaching Hemispheric Bowl",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} radius |R| = {abs(r1):.2f} mm, steepness ratio |R|/y = {steepness:.2f} < {self.HEMISPHERIC_STEEPNESS}. A surface deeper than 0.7 x diameter behaves like a hemispheric bowl: the polishing lap cannot reach the rim evenly.",
                            "fix": "Increase |R| towards 1.5 x Semi-Diameter, or split the element."
                        })

                # Near-flat surfaces: a sag this small is as hard to make as a deep curve,
                # because the radius cannot be verified against a test plate (Edmund Optics).
                for label, radius, semi_for_face in (
                    ("front", r1, surf.get("semi_diameter", 0.0)),
                    ("rear", r2, next_surf.get("semi_diameter", 0.0)),
                ):
                    if not math.isfinite(radius) or radius == 0 or semi_for_face <= 0:
                        continue
                    sag_um = abs(self.surface_sag_mm(radius, semi_for_face)) * 1000.0
                    if 0 < sag_um <= self.NEAR_FLAT_SAG_UM:
                        findings.append({
                            "level": "WARNING",
                            "rule": "Near-Flat Surface (Test Plate Ambiguity)",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} ({material}) {label} face has a sag of {sag_um:.1f} um at its semi-diameter, below the {self.NEAR_FLAT_SAG_UM:.0f} um floor. A near-flat cannot be verified against a test plate and is as hard to produce as a deep curve.",
                            "fix": "Make the surface truly flat (radius = inf) or give it real curvature."
                        })
                    elif sag_um <= self.NEAR_FLAT_SAG_UM:
                        findings.append({
                            "level": "WARNING",
                            "rule": "Undefined Near-Flat Sag",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} ({material}) {label} face has |R| = {abs(radius):.2f} mm against a semi-diameter of {semi_for_face:.2f} mm, so its sagitta at the clear aperture is not defined. Check the aperture: the surface may be hyper-hemispheric.",
                            "fix": "Confirm the semi-diameter against the ray footprint, or set the surface truly flat."
                        })
            else:
                # Air space checks
                # Check if this air space is an INTERNAL space between lens elements
                is_internal_air = (
                    first_glass is not None
                    and last_glass is not None
                    and first_glass <= idx < last_glass
                )

                comment_str = (surf.get("comment") or "").upper()
                is_inter_module_relay = any(
                    kw in comment_str
                    for kw in ["RELAY", "INTERMEDIATE", "CONJUGATE", "4F", "TUBE TO OBJ", "SCAN TO TUBE"]
                )

                if is_internal_air and not is_inter_module_relay:
                    if thickness > 20.0:
                        findings.append({
                            "level": "CRITICAL",
                            "rule": "Excessive Internal Air Space (Runaway Optimizer)",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} internal intra-lens air gap {thickness:.2f} mm > 20.0 mm! Runaway optimizer detected (excessive element separation cheating Petzval/lever arm). Severe decenter sensitivity and unmountable barrel.",
                            "fix": "Add CTLT or MXCA operand on this surface with target <= 8.0 ~ 12.0 mm, and constrain total barrel length using TTHI."
                        })
                    elif thickness > self.MAX_INTERNAL_AIR_SPACE:
                        findings.append({
                            "level": "WARNING",
                            "rule": "High Internal Air Spacing",
                            "surface": surf.get("index"),
                            "message": f"Surface {surf.get('index')} internal air gap {thickness:.2f} mm > recommended {self.MAX_INTERNAL_AIR_SPACE} mm. Element separation is too large for compact optomechanical assembly.",
                            "fix": f"Add MXCA or CTLT operand with target <= {self.RECOMMENDED_AIR_SPACE} mm."
                        })

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

        # 1.1 Lens Barrel Core Stack Aspect Ratio Check
        if first_glass is not None and last_glass is not None:
            core_stack_length = sum(
                surfaces[i].get("thickness", 0.0) for i in range(first_glass, last_glass + 1)
            )
            max_dia = max(
                (2.0 * s.get("semi_diameter", 0.0) for s in surfaces[first_glass:last_glass + 2]),
                default=0.0,
            )
            if max_dia > 0:
                barrel_ratio = core_stack_length / max_dia
                if barrel_ratio > self.MAX_BARREL_ASPECT_RATIO:
                    findings.append({
                        "level": "WARNING",
                        "rule": "Barrel Aspect Ratio (L/D)",
                        "surface": "System",
                        "message": f"Lens barrel core stack length ({core_stack_length:.2f} mm) vs diameter ({max_dia:.2f} mm) aspect ratio L/D = {barrel_ratio:.2f} > {self.MAX_BARREL_ASPECT_RATIO}. Long slender barrel is prone to boring tool chatter and decenter errors.",
                        "fix": "Constrain barrel stack length using TTHI operand in Merit Function."
                    })

        # 2. Ray Aiming Check
        field_type = general.get("field_type", "")
        max_angle = 0.0

        # Ray aiming is not optional when a CoordinateBreak sits at (or before) the stop, or
        # the stop itself is tilted/decentered: the paraxial entrance pupil is then simply the
        # wrong pupil, and every field silently gets the wrong ray bundle. OpticStudio's own
        # default is to have ray aiming on for this reason.
        stop_index = next((s.get("index") for s in surfaces if s.get("is_stop")), None)
        ray_aiming_setting = str(general.get("ray_aiming", "Off")).lower()
        ray_aiming_off = ray_aiming_setting in ["off", "none", "0"]
        break_before_stop = [
            s for s in surfaces
            if s.get("type") == "CoordinateBreak" and (stop_index is None or s.get("index") <= stop_index)
        ]
        if break_before_stop and ray_aiming_off:
            indices = ", ".join(str(s.get("index")) for s in break_before_stop[:5])
            findings.append({
                "level": "RECOMMENDATION",
                "rule": "Ray Aiming for Tilted/Decentered Stop",
                "surface": "System",
                "message": f"CoordinateBreak surface(s) {indices} sit at or before the stop, so the paraxial entrance pupil no longer describes where rays actually enter the system, but Ray Aiming is OFF.",
                "fix": "Enable Ray Aiming (Real) via zemax_set_ray_aiming; verify the illuminated footprint on each surface afterwards."
            })

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
