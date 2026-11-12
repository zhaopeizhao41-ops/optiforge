"""One operation boundary for MCP handlers and direct Python tool calls."""
from functools import wraps
import inspect

from core.input_validation import validate_arguments
from core.zos_session import ZOSSession


def serialized_operation(fn):
    """Serialize reads, exports, and project changes with model writes."""
    signature = inspect.signature(fn)
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with ZOSSession.operation_lock:
            try:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                validate_arguments(fn.__name__, bound.arguments)
                session = ZOSSession._instance
                from core.workbench_effects import TOOL_EFFECTS, ToolEffect
                effect = TOOL_EFFECTS.get(fn.__name__, ToolEffect(native=True))
                file_only = fn.__name__ in {"zemax_set_project", "zemax_get_project", "get_current_design_proposal",
                                             "zemax_register_design_proposal", "zemax_confirm_design_proposal"}
                if not file_only and getattr(session, "native_cleanup_error", None) and (effect.native or effect.observes_context or effect.kind == "context"):
                    from core.analysis_runner import NativeOwnershipError
                    raise NativeOwnershipError(session.native_cleanup_error)
                return fn(*args, **kwargs)
            except Exception as error:
                return {"status": "error", "message": str(error), **({"code":error.code} if hasattr(error,"code") else {})}
    return wrapped


def model_operation(fn):
    """Require project confirmation and restore a native snapshot on failure."""
    @wraps(fn)
    def guarded(*args, **kwargs):
        from tools.project_manager import get_active_project_name
        from tools.system_tools import get_current_design_proposal

        project = get_active_project_name()
        proposal = get_current_design_proposal()
        if proposal.get("user_confirmed_to_simulate") is not True:
            return {"status": "error", "code": "PROPOSAL_NOT_CONFIRMED",
                    "message": "Register a confirmed design proposal for the active project before modeling or optimization.",
                    "active_project": project}
        session = ZOSSession.get_instance()
        replacing = fn.__name__ in {"zemax_new_file", "zemax_load_template"}
        if not replacing and session.model_project != project:
            return {"status": "error", "code": "MODEL_PROJECT_MISMATCH",
                    "message": "Load or create the active project's model before editing it.",
                    "model_project": session.model_project, "active_project": project}
        tools = getattr(session.system, "Tools", None)
        if tools is not None and getattr(tools, "CurrentTool", None) is not None:
            raise RuntimeError("Another ZOS-API tool is open; its owner must close it first.")
        if replacing:
            result = fn(*args, **kwargs)
            if result.get("status") == "success":
                session.model_project = project
            return result
        snapshot = session.system.CopySystem()
        if snapshot is None:
            raise RuntimeError("Cannot snapshot the current model; operation aborted.")
        result = None
        try:
            try:
                result = fn(*args, **kwargs)
            except Exception as error:
                result = {"status": "error", "message": str(error)}
            if result.get("status") == "error":
                try:
                    path = session._save_snapshot(snapshot)
                    session._restore_recovery(path)
                    result.update(recovery_file=path, rolled_back=True)
                except Exception as error:
                    result.update(rolled_back=False, recovery_file=session.last_recovery_file,
                                  recovery_error=str(error))
            return result
        finally:
            try:
                snapshot.Close(False)
            except Exception as close_error:
                if isinstance(result, dict):
                    result.setdefault("cleanup_warning", str(close_error))
    return serialized_operation(guarded)
