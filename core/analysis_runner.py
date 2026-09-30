"""Shared helpers for native analyses: bounded run time and per-configuration evaluation."""
from contextlib import contextmanager
from functools import wraps
import inspect
import time

from core.zos_session import ZOSSession

ANALYSIS_TIMEOUT_S = 300.0
_POLL_S = 0.05


def run_analysis(analysis, timeout_s: float = ANALYSIS_TIMEOUT_S):
    """Apply an analysis without blocking forever; terminate it and raise on timeout.

    IA_.Apply() is asynchronous, so completion is polled via IsRunning().
    """
    analysis.Apply()
    deadline = time.monotonic() + float(timeout_s)
    while analysis.IsRunning():
        if time.monotonic() > deadline:
            analysis.Terminate()
            analysis.WaitForCompletion()
            raise TimeoutError(f"{str(analysis.GetAnalysisName)} exceeded {float(timeout_s):g} s and was terminated.")
        time.sleep(_POLL_S)
    analysis.WaitForCompletion()
    return analysis.GetResults()


@contextmanager
def configuration(system, config):
    """Temporarily make a 1-based MCE configuration current; always restore the previous one."""
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
        mce.SetCurrentConfiguration(previous)


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
