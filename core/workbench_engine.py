"""One task boundary around existing tools; native work never runs on HTTP threads."""
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import inspect
import threading
import uuid
from contextlib import nullcontext

from core.workbench_effects import TOOL_EFFECTS, ToolEffect, check_tool_coverage
from core.workbench_snapshot import json_safe
from core.workbench_state import PROTOCOL_VERSION, WorkbenchState


def native_metadata():
    # Inspect the existing singleton only. Metadata queries must not create it.
    from core.zos_session import ZOSSession
    from tools.project_manager import get_active_project_name
    from tools.system_tools import get_current_design_proposal
    session = ZOSSession._instance
    return {"active_project": get_active_project_name(),
            "model_project": session.model_project if session else None,
            "proposal_confirmed": get_current_design_proposal().get("user_confirmed_to_simulate") is True,
            "session_identity": session if session and session.is_connected else None,
            "recovery_error": session.last_recovery_error if session else None}


def succeeded(result):
    return not isinstance(result, dict) or result.get("status") not in {"error", "rejected", "failed"}


class EngineWorker:
    def __init__(self, registry=None, *, effects=None, capture=None, metadata=None, state=None, trace=None):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="optiforge-engine")
        self.registry = registry
        self.effects = effects
        self.state = state or WorkbenchState()
        self.submit_lock = threading.RLock()
        self.refresh_future = None
        self.refresh_id = None
        self.seen_session = None
        self.uncertainty = None
        self.observed_model = None
        self.closed = False
        self.capture = capture
        self.trace_adapter = trace
        self.trace_pending = {}
        self.trace_blocked = None
        fake_session = object()
        self.metadata = metadata or (native_metadata if registry is None else lambda: {
            "active_project": None, "model_project": None, "proposal_confirmed": False,
            "session_identity": fake_session, "recovery_error": None})

    def _registry(self):
        if self.registry is None:
            import tools
            check_tool_coverage(tools.__all__)
            self.registry = {name: getattr(tools, name) for name in tools.__all__}
        return self.registry

    def _effect(self, name):
        return (self.effects or {}).get(name, TOOL_EFFECTS.get(name, ToolEffect()))

    def _envelope(self, result, operation_id=None):
        return {"protocol_version": PROTOCOL_VERSION, "operation_id": operation_id,
                "result": result, "context": self.state.context()}

    def submit(self, name, arguments=None, *, expected_context=None, source="mcp"):
        # This lock orders event registration and executor submission together.
        with self.submit_lock:
            operation_id = None if self.closed else self.state.enqueue(name, source)
            if operation_id is None:
                future = Future()
                future.set_result(self._envelope({"status": "error", "code": "WORKBENCH_BUSY",
                                                 "message": "工程任务队列已满或服务正在停止，请稍后读取状态。"}))
                return None, future
            future = self.executor.submit(self._execute, operation_id, name, deepcopy(arguments or {}),
                                          deepcopy(expected_context))
            return operation_id, future

    def call_envelope(self, name, arguments=None, *, expected_context=None, source="mcp"):
        return self.submit(name, arguments, expected_context=expected_context, source=source)[1].result()

    def call(self, name, arguments=None, *, expected_context=None, source="mcp"):
        return self.call_envelope(name, arguments, expected_context=expected_context, source=source)["result"]

    def refresh(self, source="refresh"):
        with self.submit_lock:
            if self.refresh_future is not None and not self.refresh_future.done():
                return {"protocol_version": PROTOCOL_VERSION, "status": "accepted", "operation_id": self.refresh_id}
            self.refresh_id, self.refresh_future = self.submit("__refresh__", source=source)
            if self.refresh_id is None:
                return self.refresh_future.result()["result"] | {"protocol_version": PROTOCOL_VERSION}
            return {"protocol_version": PROTOCOL_VERSION, "status": "accepted", "operation_id": self.refresh_id}

    def load_model(self, filepath, *, expected_context=None, source="ui"):
        envelope = self.call_envelope("zemax_load_file", {"filepath": str(filepath)},
                                      expected_context=expected_context, source=source)
        # The response state must be the state captured at THAT operation boundary,
        # not a later cache read after another queued import has run.
        return envelope

    def request_trace(self, settings=None, *, expected_context=None, source="ui"):
        from core.workbench_trace import eligibility, settings_for, trace_key, TraceError
        with self.submit_lock:
            current = self.state.read()
            conflict = self.state.validate_context(expected_context) or eligibility(current)
            if conflict:
                return conflict
            try:
                normalized = settings_for(current["snapshot"]["model"], settings)
                key = trace_key(current, normalized)
            except (TraceError, KeyError, TypeError, ValueError) as error:
                return {"status": "error", "code": getattr(error, "code", "TRACE_SETTINGS_INVALID"), "message": str(error)}
            pending_key = (key, current["state_version"])
            pending = self.trace_pending.get(pending_key)
            if pending and not pending[1].done():
                operation_id = pending[0]
            else:
                operation_id = None if self.closed else self.state.enqueue("__trace__", source)
                if not operation_id:
                    return {"status": "error", "code": "WORKBENCH_BUSY", "message": "工程任务队列已满或正在停止"}
                source_identity = {k: current[k] for k in ("service_instance_id", "model_id", "model_revision", "active_project", "model_project")}
                source_identity.update(configuration=current["snapshot"]["model"]["configuration"], source_state_version=current["state_version"])
                self.state.trace_update(key, operation_id, "queued", {"settings": normalized, "source": source_identity})
                future = self.executor.submit(self._execute_trace, operation_id, key, normalized, deepcopy(expected_context))
                self.trace_pending = {k: v for k, v in self.trace_pending.items() if not v[1].done()}
                self.trace_pending[pending_key] = (operation_id, future)
            return {"protocol_version": PROTOCOL_VERSION, "status": "accepted", "operation_id": operation_id,
                    "key": key, "settings": normalized}

    def _execute_trace(self, operation_id, key, settings, expected):
        from core.workbench_trace import eligibility, trace_key, trace_native, TraceError
        from core.zos_session import ZOSSession
        # Observe, compute, reobserve and publish within ONE native ownership span.
        with ZOSSession.operation_lock if self.trace_adapter is None else nullcontext():
            self.state.start(operation_id)
            self.state.trace_update(key, operation_id, "running")
            result = None
            invoked = False
            try:
                conflict = self.state.validate_context(expected) or eligibility(self.state.read())
                if conflict:
                    raise TraceError(conflict["code"], conflict["message"])
                if self.trace_blocked:
                    raise TraceError("TRACE_CLEANUP_FAILED", self.trace_blocked)
                effect = ToolEffect("trace", True)
                self._publish(operation_id, effect, {"status": "success"}, False)
                current = self.state.read()
                conflict = self.state.validate_context(expected) or eligibility(current)
                if conflict or trace_key(current, settings) != key:
                    raise TraceError((conflict or {}).get("code", "WORKBENCH_CONTEXT_STALE"),
                                     (conflict or {}).get("message", "原生模型已改变，请重新读取"))
                source = {k: current[k] for k in ("service_instance_id", "model_id", "model_revision", "active_project", "model_project")}
                source.update(source_state_version=current["state_version"], configuration=current["snapshot"]["model"]["configuration"])
                result = self.state.cached_trace(key)
                if result is None:
                    model = deepcopy(current["snapshot"]["model"])
                    model["fields"] = model.get("trace_fields", model["fields"])
                    invoked = True
                    result = (self.trace_adapter or trace_native)(model, settings)
                    result.update(key=key, result_id=str(uuid.uuid4()), source=source)
                self._publish(operation_id, effect, {"status": "success"}, False)
                after = self.state.read()
                conflict = self.state.validate_context(expected) or eligibility(after)
                if conflict or trace_key(after, settings) != key:
                    raise TraceError("TRACE_RESULT_STALE", "计算期间模型或项目状态改变，结果不予发布")
                self.state.store_trace(result)
            except Exception as error:
                result = {"status": "error", "code": getattr(error, "code", "TRACE_NATIVE_FAILED"), "message": str(error)}
                if result["code"] == "TRACE_CLEANUP_FAILED":
                    self.trace_blocked = str(error)
                elif invoked:
                    # Failure also has an observation boundary; a tool error alone
                    # never increments the optical revision or clears uncertainty.
                    try:
                        self._publish(operation_id, ToolEffect("trace", True), result, False)
                    except Exception as observation_error:
                        self.state.publish(metadata={k: self.state.read()[k] for k in ("active_project", "model_project", "proposal_confirmed")},
                                           snapshot=None, validity="unavailable", context_valid=False, error=str(observation_error))
            status = "succeeded" if succeeded(result) else "failed"
            self.state.trace_update(key, operation_id, status, result)
            self.state.finish(operation_id, status, result)
            # Completed Futures must not retain another unbounded set of paths
            # outside the 8-result / 32 MiB cache. Coordinates are fetched by ID.
            return self._envelope({k: v for k, v in result.items() if k != "rays"}, operation_id)

    def _capture(self):
        if self.capture:
            result = self.capture()
        else:
            result = self._registry()["zemax_get_workbench_snapshot"]()
        if not isinstance(result, dict) or result.get("status") != "success":
            raise RuntimeError(result.get("message", "模型快照不可用") if isinstance(result, dict) else "模型快照不可用")
        return json_safe(result)

    def _record_outcome(self, effect, result, no_write):
        """Remember unresolved native changes even when observing metadata fails.

        Rolling back this operation restores only its starting model. It cannot
        certify a model that was already uncertain before the operation began.
        """
        if no_write:
            return False
        ok = succeeded(result)
        data = result if isinstance(result, dict) else {}
        if ok and effect.kind == "replace":
            self.uncertainty = None
        unresolved = (data.get("rolled_back") is False or bool(data.get("recovery_error"))
                      or not ok and effect.native and effect.protected and data.get("rolled_back") is not True)
        if unresolved:
            self.uncertainty = str(data.get("recovery_error") or "操作失败，无法确认临时修改已恢复；请重新载入模型核对。")
        return unresolved

    def _publish(self, operation_id, effect, result, no_write, snapshot_result=None, unresolved=False):
        meta = self.metadata()
        identity = meta.pop("session_identity", None)
        recovery_error = meta.pop("recovery_error", None)
        previous_session = self.seen_session
        new_session = identity is not None and identity is not self.seen_session
        if identity is not None:
            self.seen_session = identity
        ok = succeeded(result)
        rolled_back = isinstance(result, dict) and result.get("rolled_back") is True
        replacement = (ok and effect.kind == "replace") or new_session
        modified = not no_write and (ok or rolled_back or unresolved) and effect.kind in {"modify", "calculate", "replace"}
        if new_session and previous_session is not None and not unresolved:
            self.uncertainty = None
        if recovery_error:
            self.uncertainty = str(recovery_error)
        cached = self.state.read()
        snapshot = cached["snapshot"]
        view_error = None
        try:
            if snapshot_result is False:
                raise RuntimeError(result.get("message", "模型读取失败"))
            if (not no_write or new_session) and (effect.native or identity is not None and snapshot is not None):
                snapshot = snapshot_result if snapshot_result is not None else self._capture()
            if identity is None:
                snapshot = None
            validity = "uncertain" if self.uncertainty else "valid" if snapshot else "unavailable"
            view_error = self.uncertainty
        except Exception as error:
            snapshot = None
            validity = "uncertain" if self.uncertainty else "unavailable"
            view_error = f"操作已结束，视图读取失败：{error}"
        if snapshot and isinstance(snapshot.get("model"), dict):
            optical = {key: value for key, value in snapshot["model"].items() if key not in {"name", "filepath", "project"}}
            modified |= self.observed_model is not None and optical != self.observed_model
            self.observed_model = deepcopy(optical)
        self.state.publish(metadata=meta, snapshot=snapshot, validity=validity, error=view_error,
                           replace=replacement, model_changed=modified,
                           changed=not no_write and ok and effect.kind == "context", operation_id=operation_id)
        return view_error

    def _execute(self, operation_id, name, arguments, expected):
        effect = ToolEffect(native=True, observes_context=True) if name == "__refresh__" else self._effect(name)
        if effect.protected:
            if self.trace_blocked and effect.native:
                result = {"status": "error", "code": "TRACE_CLEANUP_FAILED", "message": self.trace_blocked}
                self.state.finish(operation_id, "rejected", result)
                return self._envelope(result, operation_id)
            conflict = self.state.validate_context(expected)
            if conflict is None and not self.state.read()["context_valid"] and effect.kind != "replace":
                conflict = {"status": "error", "code": "WORKBENCH_STATE_UNCERTAIN",
                            "message": "工程身份尚未确认，请显式读取当前项目或模型；也可重新载入模型恢复。"}
            if conflict:
                self.state.finish(operation_id, "rejected", conflict)
                return self._envelope(conflict, operation_id)
        self.state.start(operation_id)
        no_write = False
        view_error = None
        try:
            if name == "__refresh__":
                result = self._capture()
            else:
                registry = self._registry()
                if name not in registry:
                    no_write = True
                    result = {"status": "error", "code": "UNKNOWN_TOOL", "message": "未知工具"}
                else:
                    function = registry[name]
                    try:
                        bound = inspect.signature(function).bind(**arguments)
                        bound.apply_defaults()
                        if getattr(function, "__module__", "").startswith("tools."):
                            from core.input_validation import validate_arguments
                            validate_arguments(name, bound.arguments)
                    except (TypeError, ValueError, KeyError) as error:
                        no_write = True
                        result = {"status": "error", "message": str(error)}
                    else:
                        # Keep original decorators and their proposal/model-project gates.
                        result = json_safe(function(**arguments))
        except Exception as error:
            result = {"status": "error", "message": str(error)}
        if isinstance(result, dict):
            no_write |= result.get("status") == "rejected" or result.get("code") in {
                "PROPOSAL_NOT_CONFIRMED", "MODEL_PROJECT_MISMATCH", "PRESCRIPTION_RULE_VIOLATION", "PRESCRIPTION_WARNINGS", "QUALITY_GATE_FAILURE"}
        unresolved = self._record_outcome(effect, result, no_write)
        if not no_write and (name == "__refresh__" or effect.native or effect.kind == "context" or effect.observes_context):
            try:
                snapshot_result = (result if succeeded(result) else False) if name in {"__refresh__", "zemax_get_workbench_snapshot"} else None
                view_error = self._publish(operation_id, effect, result, no_write, snapshot_result, unresolved)
            except Exception as error:
                # A successful write is still successful even if metadata/snapshot reads fail.
                view_error = f"操作已结束，工程状态读取失败：{error}"
                current = self.state.read()
                self.state.publish(metadata={key: current[key] for key in ("active_project", "model_project", "proposal_confirmed")},
                                   snapshot=None, validity="unavailable", error=view_error,
                                   context_valid=False,
                                   replace=succeeded(result) and effect.kind == "replace",
                                   model_changed=not no_write and effect.kind in {"modify", "calculate"}, operation_id=operation_id)
        status = "succeeded" if succeeded(result) else "rejected" if no_write else "failed"
        self.state.finish(operation_id, status, result, view_error)
        state = self.state.read()
        if name == "zemax_get_workbench_snapshot" and succeeded(result) and state["snapshot"]:
            result = state["snapshot"]
        envelope = self._envelope(result, operation_id)
        if name == "zemax_load_file":
            envelope["state"] = state
        return envelope

    def close(self):
        with self.submit_lock:
            if self.closed:
                return
            self.closed = True
            self.state.close()
            def close_session():
                from core.zos_session import ZOSSession
                if ZOSSession._instance is not None:
                    ZOSSession._instance.close()
            future = self.executor.submit(close_session)
        future.result()
        self.executor.shutdown(wait=True)
