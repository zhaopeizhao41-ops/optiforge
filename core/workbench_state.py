"""Published memory state and bounded notifications. No optical engine imports."""
from collections import deque, OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
import uuid
import json

PROTOCOL_VERSION = 2
CONTEXT_KEYS = ("service_instance_id", "state_version", "model_id", "active_project", "model_project")


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkbenchState:
    def __init__(self, event_limit=256, history_limit=50, queue_limit=32):
        self.condition = threading.Condition(threading.RLock())
        self.events = deque(maxlen=event_limit)
        self.history = deque(maxlen=history_limit)
        self.pending = {}
        self.queue_limit = queue_limit
        self.seq = 0
        self.closed = False
        self.trace_results = OrderedDict()
        self.trace_summaries = OrderedDict()
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
                             "trace_summary": list(self.trace_summaries.values())})

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

    def finish(self, operation_id, status, result, view_error=None):
        with self.condition:
            task = self.pending.pop(operation_id)
            task.update(status=status, finished_at=now(), state_version=self.data["state_version"])
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
