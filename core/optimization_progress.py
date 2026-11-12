"""Per-round progress for long native optimization runs.

The workbench engine installs a sink for the duration of one optimization call;
direct tool calls and tests keep the no-op default. Reporting never changes an
optical outcome: a failing sink is swallowed here, and nothing reported is used
as a quality verdict.
"""
from contextvars import ContextVar

SINK = ContextVar("optimization_progress_sink", default=None)

_FIELDS = ("round", "rounds", "merit_before", "merit_after", "improvement_pct",
           "stop_reason", "timed_out", "tool")


def report(**payload):
    """Send one bounded progress note to the installed sink, if any."""
    sink = SINK.get()
    if sink is None:
        return False
    note = {key: payload[key] for key in _FIELDS if key in payload}
    try:
        sink(note)
        return True
    except Exception:
        return False
