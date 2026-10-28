"""Explicit scheduling policy for every exported tool (never infer from names)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolEffect:
    kind: str = "read"
    native: bool = False
    observes_context: bool = False

    @property
    def protected(self):
        return self.kind in {"replace", "modify", "context", "calculate", "trace"}


# Metadata/knowledge calls must not open an optical session merely to publish a task.
KNOWLEDGE = {"zemax_audit_requirements", "zemax_lookup_manual", "zemax_list_projects"}
CONTEXT_READS = {"zemax_get_project", "get_current_design_proposal"}
MODEL_READS = {"zemax_system_info", "zemax_get_system_data", "zemax_get_workbench_snapshot", "zemax_mce_get"}
NATIVE_READS = {
    "zemax_get_envelope", "zemax_audit_retroreflection", "zemax_generate_folded_drawing",
    "zemax_generate_barrel_assembly", "zemax_compute_optomech_spacing", "zemax_generate_mount_interface",
    "zemax_validate_design_rules",
}
REPLACEMENTS = {"zemax_new_file", "zemax_load_file", "zemax_load_template"}
MUTATIONS = {
    "zemax_set_aperture", "zemax_set_fields", "zemax_set_wavelengths", "zemax_set_ray_aiming",
    "zemax_surface_operations", "zemax_insert_surface", "zemax_delete_surface", "zemax_set_solve",
    "zemax_set_surface_type", "zemax_set_surface_params", "zemax_add_fold_mirror", "zemax_add_scan_mirror",
    "zemax_apply_prescription", "zemax_mce_setup", "zemax_mce_set_operand", "zemax_setup_tissue_stack",
    "zemax_setup_merit_function", "zemax_add_operand", "zemax_add_operands", "zemax_quick_focus",
    "zemax_run_optimization", "zemax_run_hammer",
}
CONTEXT_WRITES = {"zemax_set_project", "zemax_register_design_proposal", "zemax_confirm_design_proposal"}
# These use configuration contexts or temporarily change defocus; tolerance runs
# also configure analysis/compensators. Reobserve conservatively even on success.
CALCULATIONS = {
    "zemax_run_huygens_psf", "zemax_confocal_response", "zemax_fiber_coupling", "zemax_scan_pupil_check",
    "zemax_run_tolerance_analysis", "zemax_run_spot_diagram", "zemax_run_fft_mtf", "zemax_run_ray_fan",
    "zemax_run_wavefront_map", "zemax_run_field_curvature_distortion", "zemax_run_analysis_suite",
    "zemax_export_spot_diagram_plot",
}

TOOL_EFFECTS = {}
for names, effect in (
    (KNOWLEDGE, ToolEffect()), (CONTEXT_READS, ToolEffect(observes_context=True)),
    (MODEL_READS, ToolEffect(native=True, observes_context=True)), (NATIVE_READS, ToolEffect(native=True)),
    (REPLACEMENTS, ToolEffect("replace", True)), (MUTATIONS, ToolEffect("modify", True)),
    (CONTEXT_WRITES, ToolEffect("context")), (CALCULATIONS, ToolEffect("calculate", True)),
    # CAD exports optionally call set_active_project(project_name), even if
    # exporting later fails. Saving also changes path/model ownership metadata.
    ({"zemax_save_file", "zemax_export_cad", "zemax_export_optical_drawing", "zemax_export_prescription_for_cad"}, ToolEffect("context", True)),
):
    for name in names:
        if name in TOOL_EFFECTS:
            raise RuntimeError(f"Duplicate tool policy: {name}")
        TOOL_EFFECTS[name] = effect


def check_tool_coverage(names):
    if set(names) != set(TOOL_EFFECTS):
        raise RuntimeError(f"Tool policy mismatch: missing={set(names) - set(TOOL_EFFECTS)}, "
                           f"obsolete={set(TOOL_EFFECTS) - set(names)}")
