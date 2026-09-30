"""
Zemax OpticStudio MCP Tools Package
"""

from tools.system_tools import (
    zemax_system_info,
    zemax_new_file,
    zemax_load_file,
    zemax_save_file,
    zemax_get_system_data,
    zemax_load_template,
    zemax_register_design_proposal,
    get_current_design_proposal,
    zemax_audit_requirements,
    zemax_set_project,
    zemax_get_project,
    zemax_list_projects,
)
from tools.optical_setup_tools import (
    zemax_set_aperture,
    zemax_set_fields,
    zemax_set_wavelengths,
    zemax_set_ray_aiming,
)
from tools.surface_tools import (
    zemax_surface_operations,
    zemax_insert_surface,
    zemax_delete_surface,
    zemax_set_solve,
    zemax_set_surface_type,
    zemax_set_surface_params,
    zemax_add_fold_mirror,
    zemax_add_scan_mirror,
)
from tools.config_tools import (
    zemax_mce_setup,
    zemax_mce_set_operand,
    zemax_mce_get,
)
from tools.confocal_tools import (
    zemax_setup_tissue_stack,
    zemax_get_envelope,
)
from tools.optimization_tools import (
    zemax_setup_merit_function,
    zemax_add_operand,
    zemax_quick_focus,
    zemax_run_optimization,
    zemax_run_hammer,
)
from tools.analysis_tools import (
    zemax_run_spot_diagram,
    zemax_run_fft_mtf,
    zemax_run_ray_fan,
    zemax_run_wavefront_map,
    zemax_run_field_curvature_distortion,
    zemax_export_spot_diagram_plot,
)
from tools.validation_tools import (
    zemax_validate_design_rules,
    zemax_lookup_manual,
)
from tools.cad_export_tools import (
    zemax_export_cad,
    zemax_export_optical_drawing,
    zemax_export_prescription_for_cad,
)

__all__ = [
    "zemax_system_info",
    "zemax_new_file",
    "zemax_load_file",
    "zemax_save_file",
    "zemax_get_system_data",
    "zemax_load_template",
    "zemax_register_design_proposal",
    "get_current_design_proposal",
    "zemax_audit_requirements",
    "zemax_set_project",
    "zemax_get_project",
    "zemax_list_projects",
    "zemax_set_aperture",
    "zemax_set_fields",
    "zemax_set_wavelengths",
    "zemax_set_ray_aiming",
    "zemax_surface_operations",
    "zemax_insert_surface",
    "zemax_delete_surface",
    "zemax_set_solve",
    "zemax_set_surface_type",
    "zemax_set_surface_params",
    "zemax_add_fold_mirror",
    "zemax_add_scan_mirror",
    "zemax_mce_setup",
    "zemax_mce_set_operand",
    "zemax_mce_get",
    "zemax_setup_tissue_stack",
    "zemax_get_envelope",
    "zemax_setup_merit_function",
    "zemax_add_operand",
    "zemax_quick_focus",
    "zemax_run_optimization",
    "zemax_run_hammer",
    "zemax_run_spot_diagram",
    "zemax_run_fft_mtf",
    "zemax_run_ray_fan",
    "zemax_run_wavefront_map",
    "zemax_run_field_curvature_distortion",
    "zemax_export_spot_diagram_plot",
    "zemax_validate_design_rules",
    "zemax_lookup_manual",
    "zemax_export_cad",
    "zemax_export_optical_drawing",
    "zemax_export_prescription_for_cad",
]
