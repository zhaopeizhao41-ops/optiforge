"""Which workbench page should follow a finished Agent operation.

Pure mapping, no I/O. Only operations an Agent started (source "mcp") move the
page, and only after they succeeded: the user's own clicks never trigger a jump.
Auto commands are ordinary navigation commands whose request_id starts with
AUTO_PREFIX, so the browser can honour its "follow the Agent" switch.
"""

AUTO_PREFIX = "auto-"
MODEL_TOOLS = frozenset({"zemax_new_file", "zemax_load_file", "zemax_load_template",
                         "zemax_apply_prescription", "zemax_run_optimization", "zemax_run_hammer"})


def follow_target(name, arguments, result, status, source):
    """(page, focus) to show after an operation, or None."""
    if source != "mcp" or status != "succeeded":
        return None
    if isinstance(result, dict) and result.get("status") in {"error", "rejected", "failed"}:
        return None
    if name in MODEL_TOOLS:
        return "design", None
    if name == "__analysis__":
        return "analysis", {"tab": "overview"}
    if name in {"zemax_register_design_proposal", "zemax_confirm_design_proposal"}:
        return "workflow", {"tab": "proposal"}
    if name == "zemax_design_workflow" and isinstance(arguments, dict):
        action = arguments.get("action")
        if action in {"milestone_start", "milestone_report"}:
            return "workflow", {"tab": "milestones"}
    return None
