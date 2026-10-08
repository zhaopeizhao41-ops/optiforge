"""Validate complete requests before any native model write."""
import math


def _validate_path_argument(value, name, allow_none=False):
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty path.")
    from tools.project_manager import _validate_path
    _validate_path(value)


def _validate_project_argument(value):
    if value is None:
        return
    from tools.project_manager import sanitize_project_name
    sanitize_project_name(value)


def number(value, name, minimum=None, positive=False, allow_infinity=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric.")
    result = float(value)
    if math.isnan(result) or (not allow_infinity and not math.isfinite(result)):
        raise ValueError(f"{name} must be finite.")
    if positive and result <= 0 or minimum is not None and result < minimum:
        raise ValueError(f"{name} is outside its allowed range.")
    return result


def integer(value, name, minimum=1, maximum=None):
    result = number(value, name, minimum=minimum)
    if result != int(result) or maximum is not None and result > maximum:
        raise ValueError(f"{name} must be an integer in the allowed range.")
    return int(result)


def choice(value, name, options):
    if not isinstance(value, str) or value.lower().strip() not in options:
        raise ValueError(f"Unsupported {name}: {value}. Choose from {', '.join(options)}.")


def validate_arguments(name, args):
    """No session access: malformed requests must not even open OpticStudio."""
    if args.get("config") is not None:
        integer(args["config"], "config")
    if name == "zemax_load_file":
        _validate_path_argument(args.get("filepath"), "filepath")
    elif name == "zemax_save_file":
        _validate_path_argument(args.get("filepath"), "filepath", allow_none=True)
        _validate_project_argument(args.get("project_name"))
    elif name in ("zemax_set_project", "zemax_register_design_proposal"):
        _validate_project_argument(args.get("project_name"))
        if name == "zemax_register_design_proposal" and not isinstance(args["user_confirmed_to_simulate"], bool):
            raise ValueError("user_confirmed_to_simulate must be boolean.")
    elif name == "zemax_export_optical_drawing":
        _validate_path_argument(args.get("output_dir"), "output_dir", allow_none=True)
        _validate_project_argument(args.get("project_name"))
    elif name == "zemax_export_prescription_for_cad":
        _validate_path_argument(args.get("output_filepath"), "output_filepath", allow_none=True)
        _validate_project_argument(args.get("project_name"))
    elif name == "zemax_export_cad":
        _validate_path_argument(args.get("filepath"), "filepath", allow_none=True)
        _validate_project_argument(args.get("project_name"))
        choice(args["file_type"], "file_type", ("step", "stp", "iges", "igs", "sat", "stl"))
        number(args["tolerance"], "tolerance", positive=True)
        number(args["dummy_thickness"], "dummy_thickness", minimum=0)
        for key in ("num_rays", "spline_segments"):
            integer(args[key], key)
        for key in ("field_index", "wavelength_index"):
            integer(args[key], key, minimum=0)
        for key in ("first_surface", "last_surface"):
            if args[key] is not None:
                integer(args[key], key)
        if (args["first_surface"] is not None and args["last_surface"] is not None
                and int(args["first_surface"]) > int(args["last_surface"])):
            raise ValueError("first_surface must not exceed last_surface.")
    elif name == "zemax_export_spot_diagram_plot":
        _validate_path_argument(args.get("filename"), "filename")
        integer(args["rings"], "rings")
    elif name in ("zemax_run_spot_diagram", "zemax_run_ray_fan"):
        if args.get("field_index") is not None:
            integer(args["field_index"], "field_index")
    elif name == "zemax_run_wavefront_map":
        integer(args["field_index"], "field_index")
    elif name == "zemax_set_fields":
        choice(args["field_type"].replace("_", ""), "field_type", ("angle", "objectheight", "height", "paraxialimageheight", "realimageheight"))
        rows = args["fields"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("fields must be a non-empty list.")
        weights = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Each field must be an object.")
            number(row.get("x", 0), "field.x")
            number(row.get("y", 0), "field.y")
            weights.append(number(row.get("weight", 1), "field.weight", minimum=0))
        if not any(weights):
            raise ValueError("At least one field weight must be positive.")
    elif name == "zemax_set_wavelengths":
        rows = args["wavelengths"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("wavelengths must be a non-empty list.")
        weights = []
        for row in rows:
            if not isinstance(row, dict) or "wavelength_um" not in row:
                raise ValueError("Each wavelength needs wavelength_um.")
            number(row["wavelength_um"], "wavelength_um", positive=True)
            weights.append(number(row.get("weight", 1), "wavelength.weight", minimum=0))
        if not any(weights):
            raise ValueError("At least one wavelength weight must be positive.")
        integer(args["primary_index"], "primary_index", maximum=len(rows))
    elif name == "zemax_set_aperture":
        key = args["aperture_type"].lower().replace("_", "").replace("/", "").strip()
        choice(key, "aperture_type", ("epd", "entrancepupildiameter", "imagespacefnum", "fnumber", "objectspacena", "na", "floatbystopsize", "float"))
        number(args["aperture_value"], "aperture_value", positive=True)
    elif name == "zemax_surface_operations":
        integer(args["surface_index"], "surface_index", minimum=0)
        for key in ("radius", "thickness", "conic", "semi_diameter"):
            if args[key] is not None:
                number(args[key], key, minimum=0 if key == "semi_diameter" else None,
                       allow_infinity=key == "thickness" and args["surface_index"] == 0)
        if args["is_stop"] is not None and not isinstance(args["is_stop"], bool):
            raise ValueError("is_stop must be boolean.")
    elif name in ("zemax_insert_surface", "zemax_delete_surface", "zemax_set_solve"):
        integer(args["surface_index"], "surface_index", minimum=0)
        if name == "zemax_set_solve":
            params = args["params"] or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object.")
            for key, value in params.items():
                number(value, key, positive=key == "f_number")
            if "source_surface" in params:
                integer(params["source_surface"], "source_surface", minimum=0)
    elif name == "zemax_run_fft_mtf":
        choice(args["sample_size"], "sample_size", ("128x128", "256x256", "512x512"))
        number(args["max_frequency"], "max_frequency", positive=True)
    elif name == "zemax_run_optimization":
        choice(args["algorithm"], "algorithm", ("dls", "od", "dampedleastsquares", "orthogonaldescent"))
        choice(args["cycles"], "cycles", ("automatic", "auto", "1", "5", "10", "50"))
        integer(args["cores"], "cores")
        integer(args["max_rounds"], "max_rounds")
        number(args["stagnation_threshold"], "stagnation_threshold", minimum=0)
    elif name == "zemax_run_hammer":
        integer(args["timeout_seconds"], "timeout_seconds")
    elif name == "zemax_quick_focus":
        choice(args["criterion"], "criterion", ("spotsizeradial", "rmswavefront"))
    elif name == "zemax_setup_merit_function":
        choice(args["criterion"].replace("_", ""), "criterion", ("rmsspot", "rmswavefront"))
        choice(args["reference"].replace(" ", ""), "reference", ("centroid", "chiefray"))
        integer(args["rings"], "rings", maximum=20)
        if args["arms"] not in (6, 8, 10, 12):
            raise ValueError("arms must be 6, 8, 10, or 12.")
        for key in ("min_air_center", "max_air_center", "min_air_edge", "min_glass_center", "max_glass_center", "min_glass_edge", "max_internal_air", "efl_weight", "totr_weight", "barrel_weight"):
            number(args[key], key, minimum=0)
        for key in ("max_totr", "max_barrel_length"):
            if args[key] is not None:
                number(args[key], key, positive=True)
        for medium in ("air", "glass"):
            if float(args[f"min_{medium}_center"]) > float(args[f"max_{medium}_center"]):
                raise ValueError(f"Minimum {medium} thickness exceeds maximum.")
        if args["target_efl"] is not None and number(args["target_efl"], "target_efl") == 0:
            raise ValueError("target_efl must be nonzero.")
    elif name == "zemax_add_operand":
        number(args["target"], "target")
        number(args["weight"], "weight", minimum=0)
        for key in ("param1", "param2", "param3", "param4"):
            integer(args[key], key, minimum=None)
        if args["position"] is not None:
            integer(args["position"], "position")
        params = args.get("params")
        if params is not None:
            if not isinstance(params, dict):
                raise ValueError("params must be an object mapping column names to numbers.")
            for key, value in params.items():
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("params keys must be non-empty column names.")
                number(value, f"params.{key}")
    elif name == "zemax_set_surface_type":
        integer(args["surface_index"], "surface_index", minimum=0)
        if not isinstance(args["surface_type"], str) or not args["surface_type"].strip():
            raise ValueError("surface_type must be a non-empty string.")
    elif name == "zemax_set_surface_params":
        integer(args["surface_index"], "surface_index", minimum=0)
        if not isinstance(args["params"], dict):
            raise ValueError("params must be an object.")
        for key, value in args["params"].items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("params keys must be non-empty strings.")
            number(value, key)
    elif name in ("zemax_add_fold_mirror", "zemax_add_scan_mirror"):
        integer(args["surface_index"], "surface_index", minimum=0)
        number(args["reflect_angle_deg"], "reflect_angle_deg", positive=True)
        choice(args["axis"], "axis", ("x", "y"))
        if name == "zemax_add_scan_mirror":
            if not isinstance(args["scan_angles_deg"], list) or not args["scan_angles_deg"]:
                raise ValueError("scan_angles_deg must be a non-empty list.")
    elif name == "zemax_mce_setup":
        integer(args["n_configs"], "n_configs")
        if not isinstance(args["reset"], bool):
            raise ValueError("reset must be boolean.")
    elif name == "zemax_mce_set_operand":
        if not isinstance(args["operand_type"], str) or not args["operand_type"].strip():
            raise ValueError("operand_type must be a non-empty string.")
        if not isinstance(args["values"], list) or not args["values"]:
            raise ValueError("values must be a non-empty list.")
        for p in ("param1", "param2", "param3"):
            integer(args[p], p, minimum=0)
        if args["row"] is not None:
            integer(args["row"], "row")
        if not isinstance(args["variable"], bool):
            raise ValueError("variable must be boolean.")
    elif name == "zemax_setup_tissue_stack":
        integer(args["gap_surface"], "gap_surface", minimum=0)
        if not isinstance(args["tissue_layers"], list) or not args["tissue_layers"]:
            raise ValueError("tissue_layers must be a non-empty list.")
        for layer in args["tissue_layers"] + (args.get("cover_layers") or []):
            if not isinstance(layer, dict):
                raise ValueError("Each layer must be an object.")
        if not isinstance(args["depths_um"], list) or not args["depths_um"]:
            raise ValueError("depths_um must be a non-empty list.")
        for d in args["depths_um"]:
            number(d, "depth", minimum=0)
    elif name == "zemax_get_envelope":
        for key in ("first_surface", "last_surface", "frame_surface"):
            if args[key] is not None:
                integer(args[key], key, minimum=0)
        integer(args["rim_points"], "rim_points", minimum=3)
