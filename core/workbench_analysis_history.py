"""Scoped read-only projections of the existing session analysis cache.

No native imports, execution, new result store or optical acceptance criteria.
"""
from copy import deepcopy
from datetime import datetime


ANALYSIS_HISTORY_SCHEMA_VERSION = 1
MAX_ENTRIES = 8
SOURCE_KEYS = ("service_instance_id", "model_id", "model_revision", "active_project",
               "model_project", "configuration", "source_state_version")
_PRIVATE = frozenset({"token", "lease_token", "token_hash"})
_SUMMARY = ("result_id", "status", "engine", "finished_at", "settings", "overall")


class AnalysisHistoryError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def sanitized(value):
    """Return a detached result without private token fields, at any depth."""
    if isinstance(value, dict):
        return {key: sanitized(item) for key, item in value.items()
                if not isinstance(key, str) or key.lower() not in _PRIVATE}
    if isinstance(value, (tuple, list)):
        return [sanitized(item) for item in value]
    return deepcopy(value)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _counter(value, minimum=0):
    return type(value) is int and minimum <= value <= 9007199254740991


def _settings(value):
    from core.analysis_data import validate_settings, AnalysisError
    if not isinstance(value, dict) or "fields" not in value:
        return False
    try:
        return value == validate_settings(value)
    except (AnalysisError, ValueError, TypeError, KeyError, OverflowError):
        return False


def _timestamp(value):
    if not _text(value):
        return False
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (ValueError, TypeError, OverflowError):
        return False


def envelope(current, instance, project, revision):
    from core.analysis_data import MAX_RESULT_BYTES, MAX_CACHE_BYTES
    if not _text(instance) or current.get("service_instance_id") != instance:
        raise AnalysisHistoryError("WORKBENCH_INSTANCE_MISMATCH", "服务实例已变化，请重新读取")
    if not _text(project) or current.get("active_project") != project:
        raise AnalysisHistoryError("WORKFLOW_PROJECT_MISMATCH", "活动工程已变化，请重新读取")
    return {"protocol_version": 2, "analysis_history_schema_version": ANALYSIS_HISTORY_SCHEMA_VERSION,
            "storage": "session", "service_instance_id": instance, "project_name": project,
            "analysis_history_revision": revision, "limits": {"max_entries": MAX_ENTRIES,
            "max_entry_bytes": MAX_RESULT_BYTES, "max_total_bytes": MAX_CACHE_BYTES}}


def summary(result, instance, project, result_id):
    """Validate metadata and copy only a small whitelist, never analysis arrays."""
    if not isinstance(result, dict):
        return None
    source = result.get("source")
    if (type(result.get("analysis_schema_version")) is not int or result["analysis_schema_version"] != 1
            or not _text(result_id) or result.get("result_id") != result_id
            or not _text(result.get("key")) or not isinstance(result.get("status"), str)
            or result["status"] not in {"success", "partial"}
            or not _text(result.get("engine")) or not _timestamp(result.get("finished_at"))
            or not isinstance(result.get("overall"), str)
            or result["overall"] not in {"PASS", "WARN", "FAIL", "ERROR", "INFO"}
            or not _settings(result.get("settings")) or not isinstance(result.get("items"), dict)
            or not isinstance(result.get("verdicts"), list) or not isinstance(source, dict)
            or source.get("service_instance_id") != instance
            or source.get("active_project") != project or source.get("model_project") != project
            or not _text(source.get("model_id")) or not _counter(source.get("model_revision"), 1)
            or not _counter(source.get("configuration"), 1) or not _counter(source.get("source_state_version"))):
        return None
    return {"id": result_id, **{key: sanitized(result[key]) for key in _SUMMARY},
            "source": {key: deepcopy(source[key]) for key in SOURCE_KEYS}}


def not_found():
    return AnalysisHistoryError("ANALYSIS_HISTORY_NOT_FOUND", "分析历史不存在、已淘汰或不属于当前工程", 404)
