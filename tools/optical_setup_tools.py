"""
Zemax Optical System Setup Tools
Handles aperture, fields of view, wavelengths, ray aiming, and environmental parameters.
"""

from typing import Any, Dict, List, Optional
from core.zos_session import ZOSSession


def zemax_set_aperture(aperture_type: str, aperture_value: float) -> Dict[str, Any]:
    """
    Set system aperture type and value.
    Supported types: 'EPD' (EntrancePupilDiameter), 'ImageSpaceFNum', 'ObjectSpaceNA', 'FloatByStopSize'.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    sd = sys.SystemData.Aperture

    type_mapping = {
        "epd": zos.SystemData.ZemaxApertureType.EntrancePupilDiameter,
        "entrancepupildiameter": zos.SystemData.ZemaxApertureType.EntrancePupilDiameter,
        "imagespacefnum": zos.SystemData.ZemaxApertureType.ImageSpaceFNum,
        "fnumber": zos.SystemData.ZemaxApertureType.ImageSpaceFNum,
        "objectspacena": zos.SystemData.ZemaxApertureType.ObjectSpaceNA,
        "na": zos.SystemData.ZemaxApertureType.ObjectSpaceNA,
        "floatbystopsize": zos.SystemData.ZemaxApertureType.FloatByStopSize,
        "float": zos.SystemData.ZemaxApertureType.FloatByStopSize,
    }

    key = aperture_type.lower().replace("_", "").replace("/", "").strip()
    if key not in type_mapping:
        return {
            "status": "error",
            "message": f"Unsupported aperture type '{aperture_type}'. Choose from: 'EPD', 'ImageSpaceFNum', 'ObjectSpaceNA', 'FloatByStopSize'.",
        }

    sd.ApertureType = type_mapping[key]
    sd.ApertureValue = float(aperture_value)

    return {
        "status": "success",
        "aperture_type": str(sd.ApertureType),
        "aperture_value": float(sd.ApertureValue),
    }


def zemax_set_fields(
    field_type: str,
    fields: List[Dict[str, float]],
) -> Dict[str, Any]:
    """
    Configure optical fields.
    field_type: 'Angle' (degrees), 'ObjectHeight' (mm), 'ParaxialImageHeight' (mm), 'RealImageHeight' (mm).
    fields: List of dicts, e.g. [{"x": 0.0, "y": 0.0, "weight": 1.0}, {"x": 0.0, "y": 14.0, "weight": 1.0}]
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI
    sd_fields = sys.SystemData.Fields

    type_mapping = {
        "angle": zos.SystemData.FieldType.Angle,
        "objectheight": zos.SystemData.FieldType.ObjectHeight,
        "height": zos.SystemData.FieldType.ObjectHeight,
        "paraxialimageheight": zos.SystemData.FieldType.ParaxialImageHeight,
        "realimageheight": zos.SystemData.FieldType.RealImageHeight,
    }

    key = field_type.lower().replace("_", "").strip()
    if key not in type_mapping:
        return {
            "status": "error",
            "message": f"Unsupported field type '{field_type}'. Choose from: 'Angle', 'ObjectHeight', 'ParaxialImageHeight', 'RealImageHeight'.",
        }

    sd_fields.SetFieldType(type_mapping[key])

    # Clear excess existing fields
    current_count = sd_fields.NumberOfFields
    while current_count > 1:
        sd_fields.DeleteField(current_count)
        current_count = sd_fields.NumberOfFields

    # Populate fields
    for idx, f in enumerate(fields):
        fx = float(f.get("x", 0.0))
        fy = float(f.get("y", 0.0))
        fw = float(f.get("weight", 1.0))
        if idx == 0:
            f1 = sd_fields.GetField(1)
            f1.X = fx
            f1.Y = fy
            f1.Weight = fw
        else:
            sd_fields.AddField(fx, fy, fw)

    return {
        "status": "success",
        "field_type": str(sd_fields.GetFieldType()),
        "number_of_fields": sd_fields.NumberOfFields,
    }


def zemax_set_wavelengths(
    wavelengths: List[Dict[str, float]],
    primary_index: int = 1,
) -> Dict[str, Any]:
    """
    Configure wavelengths.
    wavelengths: List of dicts, e.g. [{"wavelength_um": 0.58756, "weight": 1.0}, {"wavelength_um": 0.48613, "weight": 1.0}]
    primary_index: 1-based index of primary wavelength.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    sd_waves = sys.SystemData.Wavelengths

    current_count = sd_waves.NumberOfWavelengths
    # Delete excess fields from end to 1
    while current_count > 1:
        sd_waves.RemoveWavelength(current_count)
        current_count = sd_waves.NumberOfWavelengths

    for idx, w in enumerate(wavelengths):
        val = float(w.get("wavelength_um", 0.55))
        wt = float(w.get("weight", 1.0))
        if idx == 0:
            w1 = sd_waves.GetWavelength(1)
            w1.Wavelength = val
            w1.Weight = wt
        else:
            sd_waves.AddWavelength(val, wt)

    if 1 <= primary_index <= sd_waves.NumberOfWavelengths:
        sd_waves.GetWavelength(primary_index).MakePrimary()

    return {
        "status": "success",
        "number_of_wavelengths": sd_waves.NumberOfWavelengths,
        "primary_wavelength_index": primary_index,
    }


def zemax_set_ray_aiming(method: str = "Off") -> Dict[str, Any]:
    """
    Set Ray Aiming method.
    Options: 'Off', 'Paraxial', 'Real'.
    Required by Zemax manual when field angle > 20 degrees or optical speed / pupil aberrations are large.
    """
    session = ZOSSession.get_instance()
    sys = session.system
    zos = session.ZOSAPI

    mapping = {
        "off": zos.SystemData.RayAimingMethod.Off,
        "paraxial": zos.SystemData.RayAimingMethod.Paraxial,
        "real": zos.SystemData.RayAimingMethod.Real,
    }

    key = method.lower().strip()
    if key not in mapping:
        return {"status": "error", "message": f"Unknown ray aiming method '{method}'. Choose 'Off', 'Paraxial', or 'Real'."}

    sys.SystemData.RayAiming.RayAiming = mapping[key]
    return {
        "status": "success",
        "ray_aiming": str(sys.SystemData.RayAiming.RayAiming),
    }
