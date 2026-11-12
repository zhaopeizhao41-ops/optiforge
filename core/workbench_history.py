"""Bounded session-only geometry history; never invokes an optical engine."""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import json
import uuid

HISTORY_SCHEMA_VERSION = 1
MAX_ENTRIES = 12
MAX_ENTRY_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
_PRIVATE_KEYS = frozenset({"token", "lease_token", "token_hash"})
_LABELS = {
    "zemax_new_file": "新建模型", "zemax_load_file": "载入模型",
    "zemax_load_template": "载入初始结构", "zemax_run_optimization": "自动优化",
    "zemax_run_hammer": "Hammer 优化", "zemax_apply_prescription": "写入处方",
    "zemax_quick_focus": "调整焦点",
}


class HistoryError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _safe(value):
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()
                if not isinstance(key, str) or key.lower() not in _PRIVATE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def _counter(value, minimum=0):
    return type(value) is int and value >= minimum


def source_for(state):
    """Require a complete, current observed model identity before retaining it."""
    snapshot = state.get("snapshot")
    if (state.get("validity") != "valid" or state.get("context_valid") is not True
            or state.get("error") or not isinstance(snapshot, dict)
            or snapshot.get("status") != "success" or snapshot.get("schema_version") != 1
            or type(snapshot.get("schema_version")) is not int):
        return None
    model = snapshot.get("model")
    project = state.get("active_project")
    if (not isinstance(project, str) or not project or state.get("model_project") != project
            or not isinstance(state.get("service_instance_id"), str) or not state["service_instance_id"]
            or not isinstance(state.get("model_id"), str) or not state["model_id"]
            or not _counter(state.get("model_revision"), 1) or not _counter(state.get("state_version"))
            or not isinstance(model, dict) or model.get("project") != project
            or not _counter(model.get("configuration"), 1)):
        return None
    sync = {key: state[key] for key in ("service_instance_id", "state_version", "model_id",
                                       "active_project", "model_project", "model_revision")}
    if snapshot.get("sync") != sync or state.get("snapshot_context") != sync:
        return None
    return {key: state[key] for key in ("service_instance_id", "model_id", "model_revision",
                                       "active_project", "model_project")} | {
        "configuration": model["configuration"], "source_state_version": state["state_version"]}


class WorkbenchHistory:
    """Call while holding the owning WorkbenchState condition lock."""
    def __init__(self, *, max_entries=MAX_ENTRIES, max_entry_bytes=MAX_ENTRY_BYTES,
                 max_total_bytes=MAX_TOTAL_BYTES):
        if not all(_counter(value, 1) for value in (max_entries, max_entry_bytes, max_total_bytes)):
            raise ValueError("History budgets must be positive integers")
        self.max_entries, self.max_entry_bytes, self.max_total_bytes = max_entries, max_entry_bytes, max_total_bytes
        self.entries = OrderedDict()
        self.notices = OrderedDict()
        self.total_bytes = 0
        self.revision = 0

    def record(self, state, operation_id, tool):
        source = source_for(state)
        if (source is None or not isinstance(operation_id, str) or not operation_id
                or not isinstance(tool, str) or not tool):
            return False
        if any(value[0]["operation_id"] == operation_id for value in self.entries.values()):
            return False
        project = source["active_project"]
        at = datetime.now(timezone.utc).isoformat()
        entry = {"id": str(uuid.uuid4()), "recorded_at": at, "operation_id": operation_id,
                 "tool": tool, "label": _LABELS.get(tool, "调整模型"), "source": source}
        snapshot = _safe(state["snapshot"])
        try:
            encoded = json.dumps({"entry": entry, "snapshot": snapshot}, ensure_ascii=False,
                                 allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError):
            self._notice(project, "skipped", "HISTORY_SNAPSHOT_INVALID", operation_id, at)
            return False
        size = len(encoded)
        if size > self.max_entry_bytes or size > self.max_total_bytes:
            self._notice(project, "skipped", "HISTORY_ENTRY_TOO_LARGE", operation_id, at)
            return False
        self.entries[entry["id"]] = (entry, snapshot, size)
        self.total_bytes += size
        while len(self.entries) > self.max_entries or self.total_bytes > self.max_total_bytes:
            _, (_, _, removed_size) = self.entries.popitem(last=False)
            self.total_bytes -= removed_size
        self._notice(project, "recorded", None, operation_id, at)
        return True

    def _notice(self, project, status, code, operation_id, at):
        self.notices[project] = {"status": status, "code": code,
                                 "operation_id": operation_id, "recorded_at": at}
        self.notices.move_to_end(project)
        while len(self.notices) > self.max_entries:
            self.notices.popitem(last=False)
        self.revision += 1

    def record_failure(self, current, operation_id):
        project = current.get("active_project")
        if isinstance(project, str) and project:
            self._notice(project, "skipped", "HISTORY_RECORDING_FAILED", operation_id,
                         datetime.now(timezone.utc).isoformat())

    def _scope(self, current, instance, project):
        if (not isinstance(instance, str) or not instance
                or current.get("service_instance_id") != instance):
            raise HistoryError("WORKBENCH_INSTANCE_MISMATCH", "服务实例已变化，请重新读取")
        if not isinstance(project, str) or not project or current.get("active_project") != project:
            raise HistoryError("WORKFLOW_PROJECT_MISMATCH", "活动工程已变化，请重新读取")
        return {"protocol_version": 2, "history_schema_version": HISTORY_SCHEMA_VERSION,
                "storage": "session", "service_instance_id": instance, "project_name": project}

    def list(self, current, instance, project):
        scope = self._scope(current, instance, project)
        # Summaries only: do not clone snapshot payloads on every state poll.
        entries = [deepcopy(entry) for entry, _, _ in reversed(self.entries.values())
                   if entry["source"]["active_project"] == project
                   and entry["source"]["service_instance_id"] == instance]
        return {**scope, "history_revision": self.revision, "entries": entries, "limits": {
            "max_entries": self.max_entries, "max_entry_bytes": self.max_entry_bytes,
            "max_total_bytes": self.max_total_bytes, "entry_count": len(self.entries),
            "total_bytes": self.total_bytes},
            "recording": deepcopy(self.notices.get(project, {"status": "empty", "code": None,
                                  "operation_id": None, "recorded_at": None}))}

    def detail(self, current, instance, project, entry_id):
        scope = self._scope(current, instance, project)
        value = self.entries.get(entry_id)
        if (value is None or value[0]["source"]["active_project"] != project
                or value[0]["source"]["service_instance_id"] != instance):
            raise HistoryError("HISTORY_NOT_FOUND", "历史快照不存在或已淘汰", 404)
        return {**scope, "entry": deepcopy(value[0]), "snapshot": deepcopy(value[1])}
