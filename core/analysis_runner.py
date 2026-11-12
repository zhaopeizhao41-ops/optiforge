"""Shared helpers for native analyses: bounded run time and per-configuration evaluation."""
from contextlib import contextmanager
from functools import wraps
import inspect
import time

from core.zos_session import ZOSSession

ANALYSIS_TIMEOUT_S = 120.0
_POLL_S = 0.05
_STOP_GRACE_S = 2.0


class NativeOwnershipError(RuntimeError):
    code = "ANALYSIS_CLEANUP_FAILED"


def quarantine(message):
    session = ZOSSession._instance
    if session is not None:
        session.native_cleanup_error = str(message)
    return NativeOwnershipError(str(message))


def require_native_ownership():
    """Check cached ownership state without touching the native application."""
    reason = getattr(ZOSSession._instance, "native_cleanup_error", None)
    if reason:
        raise NativeOwnershipError(reason)


def close_analysis(analysis):
    """Never wait without a deadline or close an analysis that may still run."""
    try:
        if analysis.IsRunning():
            analysis.Terminate()
            deadline = time.monotonic() + _STOP_GRACE_S
            while analysis.IsRunning() and time.monotonic() < deadline:
                time.sleep(_POLL_S)
            if analysis.IsRunning():
                raise RuntimeError("Terminate 后分析仍在运行")
        if analysis.Close() is False:
            raise RuntimeError("Close 返回 false")
    except Exception as error:
        raise quarantine(f"原生分析未安全释放，已阻止后续原生操作：{error}") from error


def run_analysis(analysis, timeout_s: float = ANALYSIS_TIMEOUT_S):
    """Apply an analysis without blocking forever; terminate it and raise on timeout.

    IA_.Apply() is asynchronous, so completion is polled via IsRunning().
    """
    if not 0 < float(timeout_s) <= 600:
        raise ValueError("Analysis timeout must be in (0, 600] seconds")
    analysis.Apply()
    deadline = time.monotonic() + float(timeout_s)
    while analysis.IsRunning():
        if time.monotonic() > deadline:
            analysis.Terminate()
            stop_deadline = time.monotonic() + _STOP_GRACE_S
            while analysis.IsRunning() and time.monotonic() < stop_deadline:
                time.sleep(_POLL_S)
            if analysis.IsRunning():
                raise quarantine("分析超时且无法确认停止；暂停后续原生操作")
            raise TimeoutError(f"Analysis exceeded {float(timeout_s):g} s and was terminated.")
        time.sleep(_POLL_S)
    return analysis.GetResults()


def _tool_running(tool):
    """Read IsRunning defensively: a missing member must not quarantine the session."""
    try:
        return bool(getattr(tool, "IsRunning", False))
    except Exception:
        return False


def stop_tool(tool):
    """Cancel a running ZOS tool and poll with a deadline; quarantine if it will not stop."""
    try:
        if not _tool_running(tool):
            return
        if getattr(tool, "CanCancel", True):
            try:
                tool.Cancel()
            except Exception:
                pass
        deadline = time.monotonic() + _STOP_GRACE_S
        while _tool_running(tool) and time.monotonic() < deadline:
            time.sleep(_POLL_S)
        if _tool_running(tool):
            raise RuntimeError("Cancel 后工具仍在运行")
    except Exception as error:
        raise quarantine(f"原生工具未安全停止，已阻止后续原生操作：{error}") from error


def run_tool_bounded(tool, timeout_s):
    """Run a ZOS tool with a deadline; True when the deadline was hit (tool stopped).

    RunAndWaitForCompletion has no timeout: a diverging run or a modal dialog would
    otherwise hold the session operation lock forever.
    """
    tool.RunAndWaitWithTimeout(max(0.001, float(timeout_s)))
    if not _tool_running(tool):
        return False
    stop_tool(tool)
    return True


def close_tool(tool):
    """Stop (bounded) then close a ZOS tool."""
    try:
        stop_tool(tool)
    finally:
        tool.Close()


@contextmanager
def configuration(system, config):
    """Restore the previous configuration only while native ownership is safe."""
    require_native_ownership()
    if config is None:
        yield
        return
    mce = system.MCE
    total = int(mce.NumberOfConfigurations)
    if not 1 <= int(config) <= total:
        raise ValueError(f"Invalid config {config}. Available configurations: 1 to {total}.")
    previous = int(mce.CurrentConfiguration)
    mce.SetCurrentConfiguration(int(config))
    try:
        yield
    finally:
        # Even restoring the old configuration is unsafe if an analysis may
        # still be running. Preserve the original cleanup failure instead.
        require_native_ownership()
        try:
            mce.SetCurrentConfiguration(previous)
            if int(mce.CurrentConfiguration) != previous:
                raise RuntimeError("CurrentConfiguration did not restore")
        except Exception as error:
            raise quarantine(f"配置恢复失败：{error}") from error


def with_configuration(fn):
    """Evaluate fn in the configuration given by its `config` argument and report it."""
    signature = inspect.signature(fn)

    @wraps(fn)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        config = bound.arguments.get("config")
        if config is None:
            return fn(*args, **kwargs)
        with configuration(ZOSSession.get_instance().system, config):
            result = fn(*args, **kwargs)
        if isinstance(result, dict) and result.get("status") == "success":
            result["config"] = int(config)
        return result
    return wrapped
