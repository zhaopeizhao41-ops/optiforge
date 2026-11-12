"""Published memory state and bounded notifications. No optical engine imports."""
from collections import deque, OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
import uuid
import json
from core.workbench_history import WorkbenchHistory

PROTOCOL_VERSION = 2
TASK_FIELDS = ("project_name", "request_id", "kind", "status", "version", "stale", "lease_expired")
TASK_STATE_LIMIT = 256
CONTEXT_KEYS = ("service_instance_id", "state_version", "model_id", "active_project", "model_project")


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkbenchState:
    def __init__(self, event_limit=256, history_limit=50, queue_limit=32):
        self.condition = threading.Condition(threading.RLock())
        self.events = deque(maxlen=event_limit)
        self.history = deque(maxlen=history_limit)
        self.task_signatures = OrderedDict()
        self.pending = {}
        self.queue_limit = queue_limit
        self.seq = 0
        self.closed = False
        self.trace_results = OrderedDict()
        self.trace_summaries = OrderedDict()
        self.analysis_results = OrderedDict()
        self.analysis_summaries = OrderedDict()
        self.analysis_history_revision = 0
        self.optical_history = WorkbenchHistory()
        self.data = {"protocol_version": PROTOCOL_VERSION, "service_instance_id": str(uuid.uuid4()),
                     "state_version": 0, "model_id": None, "model_revision": 0,
                     "active_project": None, "model_project": None, "proposal_confirmed": False,
                     "context_valid": False,
                     "validity": "initializing", "snapshot": None, "snapshot_context": None, "error": None}

    def _context(self):
        return {key: self.data[key] for key in CONTEXT_KEYS}

    def context(self):
        with self.condition:
            return self._context()

    def read(self):
        with self.condition:
            return deepcopy({**self.data, "event_seq": self.seq,
                             "tasks": {"pending": list(self.pending.values()), "recent": list(self.history)},
                             "trace_summary": list(self.trace_summaries.values()),
                             "analysis_summary": list(self.analysis_summaries.values()),
                             "analysis_history_revision": self.analysis_history_revision,
                             "history_revision": self.optical_history.revision})

    def record_history(self, operation_id, tool):
        """Retain the already observed state; no extra model or native read."""
        with self.condition:
            try:
                return self.optical_history.record(self.data, operation_id, tool)
            except Exception:
                self.optical_history.record_failure(self.data, operation_id)
                return False

    def history_list(self, instance, project):
        with self.condition:
            return self.optical_history.list(self.data, instance, project)

    def history_detail(self, instance, project, entry_id):
        with self.condition:
            return self.optical_history.detail(self.data, instance, project, entry_id)

    def geometry_for_operation(self, operation_id, expected_source):
        """Detach one exact cached observation for an explicit stage archive."""
        from core.milestone_geometry_artifacts import require, validate_optical
        validate_optical(expected_source)
        with self.condition:
            matches = [(entry, snapshot) for entry, snapshot, _ in self.optical_history.entries.values()
                       if entry.get("operation_id") == operation_id]
            require(bool(matches), "MILESTONE_ARTIFACT_NOT_FOUND")
            require(len(matches) == 1 and matches[0][0].get("source") == expected_source)
            entry, snapshot = matches[0]
            return deepcopy({"entry": entry, "snapshot": snapshot})

    def workflow_observation(self):
        """Bounded trusted metadata for workflow records; no native reads or geometry copy."""
        from core.workbench_history import source_for
        from core.workbench_effects import TOOL_EFFECTS, ToolEffect
        with self.condition:
            observed = {entry["operation_id"]: entry["source"]
                        for entry, _, _ in self.optical_history.entries.values()}
            operations = []
            for task in self.history:
                effect = TOOL_EFFECTS.get(task["tool"], ToolEffect())
                if not ((effect.native and effect.kind in {"replace", "modify", "calculate"})
                        or task["tool"] == "__analysis__"):
                    continue
                completed = task.get("completed_context", {})
                if (completed.get("service_instance_id") != self.data["service_instance_id"]
                        or completed.get("active_project") != self.data["active_project"]):
                    continue
                receipt = {key: task.get(key) for key in (
                    "operation_id", "tool", "queued_at", "finished_at", "status", "result_status")}
                receipt.update({key: task[key] for key in ("code", "rolled_back", "view_error") if key in task})
                receipt["observed_source"] = observed.get(task["operation_id"])
                receipt["_context"] = completed
                operations.append(receipt)
            return deepcopy({"service_instance_id": self.data["service_instance_id"],
                             "context": self._context(),
                             "source": source_for(self.data), "operations": operations})

    def analysis_update(self, key, operation_id, status, result=None):
        with self.condition:
            summary = {**self.analysis_summaries.get(operation_id, {}), "key": key,
                       "operation_id": operation_id, "status": status}
            if result:
                for name in ("result_id", "source", "settings", "code", "message", "elapsed_ms", "overall", "current_item", "current_field"):
                    if name in result:
                        summary[name] = deepcopy(result[name])
            self.analysis_summaries[operation_id] = summary
            self.analysis_summaries.move_to_end(operation_id)
            completed = [k for k,v in self.analysis_summaries.items() if v["status"] not in {"queued","running"}]
            for old in completed[:-8]:
                del self.analysis_summaries[old]
            self._event("analysis_changed", operation_id=operation_id, key=key, result_id=summary.get("result_id"))

    def store_analysis(self, result):
        from core.analysis_data import MAX_RESULT_BYTES, MAX_CACHE_BYTES, AnalysisError
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_RESULT_BYTES:
            raise AnalysisError("ANALYSIS_RESULT_TOO_LARGE", "分析结果超过单任务 8 MiB 预算")
        with self.condition:
            if result["result_id"] in self.analysis_results:
                return  # A cache hit retains the original identity, data, time and insertion order.
            self.analysis_results[result["result_id"]] = (deepcopy(result), len(encoded))
            self.analysis_history_revision += 1
            while len(self.analysis_results) > 8 or sum(v[1] for v in self.analysis_results.values()) > MAX_CACHE_BYTES:
                old, _ = self.analysis_results.popitem(last=False)
                self.analysis_history_revision += 1
                for s in self.analysis_summaries.values():
                    if s.get("result_id") == old:
                        s.update(status="evicted", result_id=None)
                        self._event("analysis_changed", operation_id=s["operation_id"], key=s["key"])

    def analysis_history_list(self, instance, project):
        from core.workbench_analysis_history import envelope, summary
        with self.condition:
            scope = envelope(self.data, instance, project, self.analysis_history_revision)
            entries = [entry for result_id, (result, _) in reversed(self.analysis_results.items())
                       if (entry := summary(result, instance, project, result_id)) is not None]
            return {**scope, "entries": entries}

    def analysis_history_detail(self, instance, project, result_id):
        from core.workbench_analysis_history import envelope, summary, sanitized, not_found
        with self.condition:
            scope = envelope(self.data, instance, project, self.analysis_history_revision)
            value = self.analysis_results.get(result_id)
            entry = summary(value[0], instance, project, result_id) if value else None
            if entry is None:
                raise not_found()
            return {**scope, "entry": entry, "result": sanitized(value[0])}

    def get_analysis(self, result_id):
        with self.condition:
            value = self.analysis_results.get(result_id)
            return deepcopy(value[0]) if value else None

    def cached_analysis(self, key):
        with self.condition:
            for value, _ in reversed(self.analysis_results.values()):
                if value["key"] == key:
                    return deepcopy(value)
            return None

    def trace_update(self, key, operation_id, status, result=None):
        with self.condition:
            summary = {**self.trace_summaries.get(operation_id, {}), "key": key, "operation_id": operation_id, "status": status}
            if result:
                for name in ("result_id", "counts", "code", "message", "source", "elapsed_ms", "settings"):
                    if name in result:
                        summary[name] = deepcopy(result[name])
            self.trace_summaries[operation_id] = summary
            self.trace_summaries.move_to_end(operation_id)
            completed = [k for k, v in self.trace_summaries.items() if v["status"] not in {"queued", "running"}]
            for old in completed[:-8]:
                del self.trace_summaries[old]
            self._event("trace_finished" if status in {"succeeded", "failed", "rejected"} else "trace_changed",
                        key=key, result_id=summary.get("result_id"), operation_id=operation_id)

    def store_trace(self, result):
        from core.workbench_trace import MAX_BYTES, TraceError
        encoded = json.dumps(result, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise TraceError("TRACE_RESULT_TOO_LARGE", "追迹结果超出 32 MiB 缓存预算")
        with self.condition:
            self.trace_results[result["result_id"]] = (deepcopy(result), len(encoded))
            while len(self.trace_results) > 8 or sum(v[1] for v in self.trace_results.values()) > MAX_BYTES:
                old, _ = self.trace_results.popitem(last=False)
                for summary in self.trace_summaries.values():
                    if summary.get("result_id") == old:
                        summary.update(status="evicted", result_id=None)
                        self._event("trace_changed", key=summary["key"], result_id=old)

    def get_trace(self, result_id):
        with self.condition:
            value = self.trace_results.get(result_id)
            return deepcopy(value[0]) if value else None

    def cached_trace(self, key):
        with self.condition:
            for value, _ in reversed(self.trace_results.values()):
                if value["key"] == key:
                    return deepcopy(value)
            return None

    def _event(self, kind, task=None, **extra):
        self.seq += 1
        event = {"seq": self.seq, "at": now(), "kind": kind, **self._context(), "model_revision": self.data["model_revision"], **extra}
        if task:
            event.update({key: task[key] for key in ("operation_id", "tool", "source", "status")})
        self.events.append(event)
        self.condition.notify_all()

    def enqueue(self, tool, source):
        with self.condition:
            if self.closed or len(self.pending) >= self.queue_limit:
                return None
            task = {"operation_id": str(uuid.uuid4()), "tool": tool, "source": source,
                    "status": "queued", "queued_at": now()}
            self.pending[task["operation_id"]] = task
            self._event("queued", task)
            return task["operation_id"]

    def start(self, operation_id):
        with self.condition:
            task = self.pending[operation_id]
            task.update(status="running", started_at=now())
            self._event("started", task)

    def validate_context(self, expected):
        with self.condition:
            if not isinstance(expected, dict) or any(key not in expected for key in CONTEXT_KEYS):
                return {"status": "error", "code": "WORKBENCH_CONTEXT_REQUIRED",
                        "message": "请先读取当前项目或模型，再提交操作。", "current_context": self._context()}
            if any(expected[key] != self.data[key] for key in CONTEXT_KEYS):
                return {"status": "error", "code": "WORKBENCH_CONTEXT_STALE",
                        "message": "工程状态已改变，请读取当前项目或模型后重新决定操作。", "current_context": self._context()}
            return None

    def publish(self, *, metadata, snapshot, validity, error=None, replace=False, changed=False,
                model_changed=False, operation_id=None, context_valid=True):
        """Atomically publish a complete version. A failed read has NO current snapshot."""
        with self.condition:
            old = self.data
            model_id = str(uuid.uuid4()) if replace else old["model_id"]
            revision = 1 if replace else old["model_revision"] + int(model_changed)
            values = {**metadata, "model_id": model_id, "model_revision": revision,
                      "validity": validity, "error": error, "context_valid": context_valid}
            # observed_at is deliberately ignored when deciding whether a read changed state.
            old_snapshot = {k: v for k, v in (old["snapshot"] or {}).items() if k not in {"observed_at", "sync"}}
            new_snapshot = {k: v for k, v in (snapshot or {}).items() if k not in {"observed_at", "sync"}}
            advances = changed or old_snapshot != new_snapshot or any(old.get(k) != v for k, v in values.items())
            version = old["state_version"] + int(advances)
            old.update(deepcopy(values), state_version=version)
            context = {**self._context(), "model_revision": revision}
            old["snapshot"] = deepcopy(snapshot)
            old["snapshot_context"] = context if snapshot else None
            if snapshot:
                old["snapshot"]["sync"] = context
            if advances:
                self._event("state_changed", self.pending.get(operation_id))
            return self._context()

    def publish_task_changes(self, tasks):
        """Publish bounded task_changed notifications from public task objects.

        Only whitelisted task fields cross the event boundary. Identical
        project/request/version/status tuples are deduplicated; view/list reads
        never call this method.
        """
        if isinstance(tasks, dict):
            tasks = [tasks]
        if not isinstance(tasks, (list, tuple)):
            return []
        emitted = []
        with self.condition:
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                project = task.get("project_name")
                request_id = task.get("request_id")
                if not isinstance(project, str) or not project:
                    continue
                if not isinstance(request_id, str) or not request_id:
                    continue
                design_task = {
                    key: deepcopy(task[key]) for key in TASK_FIELDS if key in task
                }
                if not all(key in design_task for key in ("kind", "status", "version")):
                    continue
                signature = tuple(
                    (key, repr(design_task.get(key))) for key in TASK_FIELDS
                )
                cache_key = (project, request_id)
                if self.task_signatures.get(cache_key) == signature:
                    continue
                self.task_signatures[cache_key] = signature
                self.task_signatures.move_to_end(cache_key)
                while len(self.task_signatures) > TASK_STATE_LIMIT:
                    self.task_signatures.popitem(last=False)
                self._event("task_changed", design_task=design_task)
                emitted.append(design_task)
        return emitted

    def task_source(self, operation_id):
        """Source label of a pending task ("mcp", "ui", ...), or None."""
        with self.condition:
            task = self.pending.get(operation_id)
            return task.get("source") if task else None

    def progress(self, operation_id, note):
        """Record one bounded progress note for a running task and notify listeners.

        Progress is informational only: it never advances state_version, never
        changes the model, and never stands in for a quality verdict.
        """
        if not isinstance(note, dict):
            return False
        with self.condition:
            task = self.pending.get(operation_id)
            if task is None:
                return False
            task["progress"] = {key: note[key] for key in (
                "round", "rounds", "merit_before", "merit_after", "improvement_pct",
                "stop_reason", "timed_out", "tool") if key in note}
            self._event("progress", task, progress=deepcopy(task["progress"]))
            return True

    def finish(self, operation_id, status, result, view_error=None):
        with self.condition:
            task = self.pending.pop(operation_id)
            # Progress is live-only; the terminal record keeps its fixed fields.
            task.pop("progress", None)
            task.update(status=status, finished_at=now(), state_version=self.data["state_version"],
                        completed_context=self._context(),
                        result_status=result.get("status") if isinstance(result, dict) else None)
            if isinstance(result, dict):
                for key in ("code", "rolled_back", "recovery_error"):
                    if key in result:
                        task[key] = result[key]
                if status != "succeeded":
                    task["message"] = str(result.get("message", "操作未完成"))[:400]
            if view_error:
                task["view_error"] = str(view_error)[:400]
            self.history.appendleft(task)
            self._event("finished", task, code=task.get("code"), message=task.get("message"), view_error=task.get("view_error"))

    def wait_events(self, instance_id, after_seq, timeout_ms=20000):
        deadline = time.monotonic() + min(20000, max(0, timeout_ms)) / 1000
        with self.condition:
            while True:
                reset = (instance_id != self.data["service_instance_id"] or after_seq > self.seq or after_seq < 0
                         or bool(self.events) and after_seq < self.events[0]["seq"] - 1)
                events = [] if reset else [event for event in self.events if event["seq"] > after_seq]
                remaining = deadline - time.monotonic()
                if reset or events or self.closed or remaining <= 0:
                    return deepcopy({"protocol_version": PROTOCOL_VERSION,
                                     "service_instance_id": self.data["service_instance_id"], "events": events,
                                     "next_seq": self.seq, "reset_required": bool(reset), "closed": self.closed})
                self.condition.wait(remaining)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
