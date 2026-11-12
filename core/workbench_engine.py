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
    def __init__(self, registry=None, *, effects=None, capture=None, metadata=None, state=None, trace=None, analysis=None, native_enabled=True):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="optiforge-engine")
        self.registry = registry
        self.native_enabled = native_enabled
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
        self.analysis_adapter = analysis
        self.analysis_enabled = registry is None or analysis is not None
        self.analysis_pending = {}
        # Set by the HTTP server: (page, focus) -> asks online frontends to follow.
        self.navigator = None
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
        if name == "zemax_run_analysis_suite" and self.analysis_enabled:
            return self._submit_mcp_analysis(arguments or {}, expected_context, source)
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
            self.trace_blocked = self.trace_blocked or getattr(ZOSSession._instance,"native_cleanup_error",None)
            self.state.start(operation_id)
            self.state.trace_update(key, operation_id, "running")
            result = None
            invoked = False
            observation_failed = False

            def observe(outcome):
                nonlocal observation_failed
                uncertainty = self.uncertainty
                try:
                    self._publish(operation_id, ToolEffect("trace", True), outcome, False)
                    current = self.state.read()
                    if current["snapshot"] is None:
                        raise RuntimeError(current["error"] or "模型快照不可用")
                    return current
                except Exception as error:
                    # Do not retry this boundary: a later successful read cannot
                    # certify the identity that failed observation in this task.
                    observation_failed = True
                    if uncertainty is not None:
                        self.uncertainty = uncertainty
                    current = self.state.read()
                    self.state.publish(metadata={k: current[k] for k in ("active_project", "model_project", "proposal_confirmed")},
                                       snapshot=None, validity="unavailable", context_valid=False,
                                       error=str(error), operation_id=operation_id)
                    raise

            try:
                conflict = self.state.validate_context(expected) or eligibility(self.state.read())
                if conflict:
                    raise TraceError(conflict["code"], conflict["message"])
                if self.trace_blocked:
                    raise TraceError("TRACE_CLEANUP_FAILED", self.trace_blocked)
                current = observe({"status": "success"})
                conflict = self.state.validate_context(expected) or eligibility(current)
                if conflict or trace_key(current, settings) != key:
                    raise TraceError((conflict or {}).get("code", "WORKBENCH_CONTEXT_STALE"),
                                     (conflict or {}).get("message", "原生模型已改变，请重新读取"))
                source = {k: current[k] for k in ("service_instance_id", "model_id", "model_revision", "active_project", "model_project")}
                source.update(source_state_version=current["state_version"], configuration=current["snapshot"]["model"]["configuration"])
                result = self.state.cached_trace(key)
                if result is not None and any(result["source"].get(k) != source[k] for k in source if k != "source_state_version"):
                    # Equal optical keys need not have equal project ownership.
                    # Recompute instead of rebinding an immutable old source.
                    result = None
                if result is None:
                    model = deepcopy(current["snapshot"]["model"])
                    model["fields"] = model.get("trace_fields", model["fields"])
                    invoked = True
                    result = (self.trace_adapter or trace_native)(model, settings)
                    result.update(key=key, result_id=str(uuid.uuid4()), source=source)
                after = observe({"status": "success"})
                conflict = self.state.validate_context(expected) or eligibility(after)
                if conflict or trace_key(after, settings) != key:
                    raise TraceError("TRACE_RESULT_STALE", "计算期间模型或项目状态改变，结果不予发布")
                self.state.store_trace(result)
            except Exception as error:
                result = {"status": "error", "code": getattr(error, "code", "TRACE_NATIVE_FAILED"), "message": str(error)}
                if result["code"] == "TRACE_CLEANUP_FAILED":
                    self.trace_blocked = str(error)
                elif invoked and not observation_failed:
                    # Failure also has an observation boundary; a tool error alone
                    # never increments the optical revision or clears uncertainty.
                    try:
                        observe(result)
                    except Exception:
                        pass  # observe already invalidated the untrusted context.
            status = "succeeded" if succeeded(result) else "failed"
            self.state.trace_update(key, operation_id, status, result)
            self.state.finish(operation_id, status, result)
            # Completed Futures must not retain another unbounded set of paths
            # outside the 8-result / 32 MiB cache. Coordinates are fetched by ID.
            return self._envelope({k: v for k, v in result.items() if k != "rays"}, operation_id)

    def _submit_mcp_analysis(self, arguments, expected, source):
        """The MCP suite uses exactly the same observed/versioned result boundary."""
        from core.analysis_data import default_fields, AnalysisError, compact_suite
        from core.input_validation import validate_arguments
        from tools.analysis_tools import zemax_run_analysis_suite
        try:
            bound = inspect.signature(zemax_run_analysis_suite).bind(**deepcopy(arguments))
            bound.apply_defaults()
            validate_arguments("zemax_run_analysis_suite",bound.arguments)
            arguments = dict(bound.arguments)
        except Exception as error:
            future = Future()
            future.set_result(self._envelope({"status":"error","code":getattr(error,"code","ANALYSIS_SETTINGS_INVALID"),"message":str(error)}))
            return None,future
        include_details = arguments.pop("include_details", False)
        config = arguments.pop("config", None)
        current = self.state.read()
        maximum = arguments.pop("max_wavefront_fields", 5)
        try:
            if type(maximum) is not int or not 1 <= maximum <= 5:
                raise ValueError("max_wavefront_fields must be 1..5")
            if config is not None and config != current["snapshot"]["model"]["configuration"]:
                raise ValueError("工作台套件仅计算当前配置；请先显式选择配置并重新读取模型")
            if type(include_details) is not bool:
                raise ValueError("include_details must be boolean")
            arguments["analyses"] = arguments.get("analyses") or ["wavefront","spot","mtf"]
            arguments["fields"] = default_fields(current["snapshot"]["model"],maximum)
            accepted = self.request_analysis(arguments, expected_context=expected, source=source)
            if accepted.get("status") != "accepted":
                raise AnalysisError(accepted.get("code","ANALYSIS_REQUEST_REJECTED"),accepted.get("message", "分析请求拒绝"))
            original = self.analysis_pending[accepted["key"]][1]
            future = Future()
            def done(f):
                try:
                    reply = f.result()
                    result = self.state.get_analysis(reply["result"].get("result_id"))
                    if result:
                        compact = compact_suite(result,include_details)
                        reply = {**reply,"result":compact}
                    future.set_result(reply)
                except Exception as error:
                    future.set_exception(error)
            original.add_done_callback(done)
            return accepted["operation_id"],future
        except Exception as error:
            future = Future()
            future.set_result(self._envelope({"status":"error","code":getattr(error,"code","ANALYSIS_REQUEST_REJECTED"),"message":str(error)}))
            return None,future

    def request_analysis(self, settings=None, *, expected_context=None, source="ui"):
        from core.analysis_data import settings_for, result_key, AnalysisError
        from core.workbench_trace import eligibility
        if not self.analysis_enabled:
            return {"status":"error","code":"ANALYSIS_UNAVAILABLE","message":"当前服务未启用分析能力"}
        with self.submit_lock:
            current = self.state.read()
            conflict = self.state.validate_context(expected_context) or eligibility(current)
            if conflict:
                return conflict
            try:
                normalized = settings_for(current["snapshot"]["model"],settings)
                key = result_key(current,normalized)
            except (AnalysisError,ValueError,KeyError,TypeError) as error:
                return {"status":"error","code":getattr(error,"code","ANALYSIS_SETTINGS_INVALID"),"message":str(error)}
            pending = self.analysis_pending.get(key)
            if pending and not pending[1].done():
                operation_id = pending[0]
            else:
                operation_id = None if self.closed else self.state.enqueue("__analysis__",source)
                if not operation_id:
                    return {"status":"error","code":"WORKBENCH_BUSY","message":"任务队列已满或正在停止"}
                identity = self._analysis_source(current)
                self.state.analysis_update(key,operation_id,"queued",{"settings":normalized,"source":identity})
                future = self.executor.submit(self._execute_analysis,operation_id,key,normalized,deepcopy(expected_context))
                self.analysis_pending = {k:v for k,v in self.analysis_pending.items() if not v[1].done()}
                self.analysis_pending[key] = operation_id,future
            return {"status":"accepted","operation_id":operation_id,"key":key,"settings":normalized}

    @staticmethod
    def _analysis_source(state):
        source = {k:state[k] for k in ("service_instance_id","model_id","model_revision","active_project","model_project")}
        return {**source,"configuration":state["snapshot"]["model"]["configuration"],"source_state_version":state["state_version"]}

    def _execute_analysis(self, operation_id, key, settings, expected):
        from core.analysis_data import AnalysisError, result_key
        from core.analysis_native import analyze_native
        from core.analysis_runner import NativeOwnershipError
        from core.workbench_trace import eligibility
        from core.zos_session import ZOSSession
        with ZOSSession.operation_lock if self.analysis_adapter is None else nullcontext():
            self.trace_blocked = self.trace_blocked or getattr(ZOSSession._instance,"native_cleanup_error",None)
            self.state.start(operation_id)
            self.state.analysis_update(key,operation_id,"running")
            observation_failed = False
            invoked = False
            def observe():
                nonlocal observation_failed
                try:
                    error = self._publish(operation_id,ToolEffect("analysis",True),{"status":"success"},False)
                    current = self.state.read()
                    if error or not current.get("snapshot") or not current.get("context_valid") or current.get("validity") != "valid":
                        raise AnalysisError("WORKBENCH_STATE_UNCERTAIN",error or "模型观察不可用")
                    return current
                except Exception:
                    observation_failed = True
                    current = self.state.read()
                    self.state.publish(metadata={k:current[k] for k in ("active_project","model_project","proposal_confirmed")},
                                       snapshot=None,validity="unavailable",context_valid=False,error="分析边界观察失败",operation_id=operation_id)
                    raise
            try:
                conflict = self.state.validate_context(expected) or eligibility(self.state.read())
                if conflict:
                    raise AnalysisError(conflict["code"],conflict["message"])
                if self.trace_blocked:
                    raise NativeOwnershipError(self.trace_blocked)
                current = observe()
                conflict = self.state.validate_context(expected)
                if conflict or result_key(current,settings) != key:
                    raise AnalysisError("WORKBENCH_CONTEXT_STALE","原生模型或授权已改变，请重新读取")
                source = self._analysis_source(current)
                result = self.state.cached_analysis(key)
                if result is None:
                    invoked = True
                    def progress(item,field):
                        self.state.analysis_update(key,operation_id,"running",{"current_item":item,"current_field":field})
                    result = (self.analysis_adapter or analyze_native)(deepcopy(current["snapshot"]["model"]),settings,progress)
                    result.update(result_id=str(uuid.uuid4()),key=key,source=source)
                    model = current["snapshot"]["model"]
                    result["comparison_context"] = {"schema_version": 1, "model": {
                        field: deepcopy(model[field]) for field in (
                            "units", "lens_units", "system_mode", "configuration", "settings",
                            "fields", "trace_fields", "wavelengths") if field in model}}
                after = observe()
                if self.state.validate_context(expected) or eligibility(after) or result_key(after,settings) != key:
                    raise AnalysisError("ANALYSIS_RESULT_STALE","分析期间模型、项目或授权改变；结果不可作为当前")
                self.state.store_analysis(result)
            except Exception as error:
                result = {"status":"error","code":getattr(error,"code","ANALYSIS_NATIVE_FAILED"),"message":str(error)}
                if isinstance(error,NativeOwnershipError):
                    self.trace_blocked = str(error)
                    self.uncertainty = str(error)
                    current = self.state.read()
                    self.state.publish(metadata={k:current[k] for k in ("active_project","model_project","proposal_confirmed")},
                                       snapshot=None,validity="uncertain",context_valid=False,error=str(error),operation_id=operation_id)
                elif invoked and not observation_failed:
                    try: observe()
                    except Exception: pass
            status = "succeeded" if result.get("status") == "success" else "partial" if result.get("status") == "partial" else "failed"
            self.state.analysis_update(key,operation_id,status,result)
            source = self.state.task_source(operation_id)
            self.state.finish(operation_id,"succeeded" if status=="partial" else status,result)
            self._follow("__analysis__", settings, result, "succeeded" if status == "partial" else status, source)
            return self._envelope({k:v for k,v in result.items() if k not in {"items","model_metadata"}},operation_id)

    def _follow(self, name, arguments, result, status, source):
        """Ask online frontends to show what an Agent operation just changed.

        Navigation is advisory: it never changes state, and any failure (no frontend,
        a pending command, the follow switch off) is ignored.
        """
        if self.navigator is None:
            return
        try:
            from core.workbench_follow import follow_target
            target = follow_target(name, arguments, result, status, source)
            if target:
                self.navigator(*target)
        except Exception:
            pass

    def _require_native_ownership(self):
        from core.analysis_runner import NativeOwnershipError
        from core.zos_session import ZOSSession
        self.trace_blocked = self.trace_blocked or getattr(ZOSSession._instance, "native_cleanup_error", None)
        if self.trace_blocked:
            raise NativeOwnershipError(self.trace_blocked)

    def _capture(self):
        self._require_native_ownership()
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
        # Metadata callbacks may inspect the native session too. Do not call
        # either callback once cleanup has left native ownership uncertain.
        self._require_native_ownership()
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
        file_only = name in {"zemax_set_project", "zemax_get_project", "get_current_design_proposal",
                             "zemax_register_design_proposal", "zemax_confirm_design_proposal"} and (
            self.registry is None or getattr(self.registry.get(name), "__module__", "").startswith("tools."))
        if file_only:
            effect = ToolEffect("workflow" if effect.protected else "read")
        artifact_read = name == "zemax_design_workflow" and arguments.get("action") in {
            "milestone_artifact", "milestone_quality_artifact"}
        if name=='zemax_design_workflow' and (arguments.get('action','view')=='view' or artifact_read):
            # Explicit observation is how a bridge recovers a stale transport
            # context. Never require the old context to read the new one.
            effect=ToolEffect()
        if not self.native_enabled and (name=='__refresh__' or effect.native or effect.observes_context or effect.kind=='context'):
            result={"status":"error","code":"NATIVE_UNAVAILABLE","message":"当前为协作模式，不提供原生模型计算"}
            self.state.finish(operation_id,"rejected",result)
            return self._envelope(result,operation_id)
        from core.zos_session import ZOSSession
        self.trace_blocked = self.trace_blocked or getattr(ZOSSession._instance,"native_cleanup_error",None)
        # Even a read can open a native analysis; context publication can also
        # capture the native model. Neither is safe after unknown tool cleanup.
        if self.trace_blocked and (effect.native or effect.observes_context or effect.kind == "context"):
            result = {"status": "error", "code": "TRACE_CLEANUP_FAILED", "message": self.trace_blocked}
            self.state.finish(operation_id, "rejected", result)
            return self._envelope(result, operation_id)
        if effect.protected:
            conflict = self.state.validate_context(expected)
            if conflict is None and not self.state.read()["context_valid"] and effect.kind not in {"replace","workflow"}:
                conflict = {"status": "error", "code": "WORKBENCH_STATE_UNCERTAIN",
                            "message": "工程身份尚未确认，请显式读取当前项目或模型；也可重新载入模型恢复。"}
            if conflict:
                self.state.finish(operation_id, "rejected", conflict)
                return self._envelope(conflict, operation_id)
        self.state.start(operation_id)
        no_write = False
        view_error = None
        progress_token = None
        if name in {"zemax_run_optimization", "zemax_run_hammer"}:
            # Optimization rounds can run for minutes. Announce each round without
            # reading the native model again: the browser refreshes on the event and
            # the agent sees progress on the same task, instead of a frozen picture.
            from core.optimization_progress import SINK
            progress_token = SINK.set(lambda note: self.state.progress(operation_id, note))
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
                        if file_only or name in {"zemax_design_workflow", "zemax_list_design_tasks", "zemax_claim_design_task", "zemax_update_design_task"}:
                            from core.design_workflow import MODEL_REFERENCE
                            cached=self.state.read()
                            model_ref=({k:cached[k] for k in ("service_instance_id","model_id","model_revision","model_project")}
                                       | {"configuration":cached["snapshot"]["model"].get("configuration")}) if cached.get("snapshot") and cached.get("context_valid") else None
                            if name=="zemax_design_workflow" and arguments.get("action")=="submit" and arguments.get("payload",{}).get("kind")=="surface_question":
                                index=arguments["payload"].get("target",{}).get("index")
                                if not model_ref or not any(s["index"]==index for s in cached["snapshot"]["model"]["surfaces"]):
                                    raise ValueError("目标表面不属于当前可用模型")
                            token=MODEL_REFERENCE.set(model_ref)
                            from core.design_milestones import OBSERVATION
                            observation_token = OBSERVATION.set(self.state.workflow_observation())
                            from core.milestone_geometry_artifacts import GEOMETRY_READER
                            archive_geometry = (name == "zemax_design_workflow"
                                and arguments.get("action") == "milestone_report"
                                and isinstance(arguments.get("payload"), dict)
                                and arguments["payload"].get("archive_geometry") is True)
                            geometry_token = GEOMETRY_READER.set(self.state.geometry_for_operation) if archive_geometry else None
                            from core.milestone_quality_artifacts import QUALITY_READER
                            archive_quality = (name == "zemax_design_workflow"
                                and arguments.get("action") == "milestone_report"
                                and isinstance(arguments.get("payload"), dict)
                                and arguments["payload"].get("quality_result_id") is not None)
                            # Only the explicitly named result is detached from the session cache.
                            quality_token = QUALITY_READER.set(self.state.get_analysis) if archive_quality else None
                            try:
                                result=json_safe(function(**arguments))
                                if (file_only or name=="zemax_design_workflow") and not artifact_read and succeeded(result):
                                    result["service_instance_id"]=cached["service_instance_id"]
                                    from tools.project_manager import get_active_project_name
                                    from tools.system_tools import get_current_design_proposal
                                    active=get_active_project_name()
                                    proposal=get_current_design_proposal()
                                    snapshot=cached.get("snapshot")
                                    self.state.publish(metadata={"active_project":active,"model_project":cached["model_project"],
                                        "proposal_confirmed":proposal.get("user_confirmed_to_simulate") is True},
                                        snapshot=snapshot,validity=cached["validity"] if snapshot else "unavailable",
                                        context_valid=cached["context_valid"],error=cached.get("error"),operation_id=operation_id)
                                if succeeded(result):
                                    action = arguments.get("action") if name == "zemax_design_workflow" else None
                                    if name == "zemax_design_workflow" and action not in {
                                            None, "view", "milestone_artifact", "milestone_quality_artifact"}:
                                        self.state.publish_task_changes(result.get("tasks", []))
                                    elif name in {"zemax_claim_design_task", "zemax_update_design_task"}:
                                        self.state.publish_task_changes(result.get("task"))
                            finally:
                                if quality_token is not None:
                                    QUALITY_READER.reset(quality_token)
                                if geometry_token is not None:
                                    GEOMETRY_READER.reset(geometry_token)
                                OBSERVATION.reset(observation_token)
                                MODEL_REFERENCE.reset(token)
                        else:
                            result = json_safe(function(**arguments))
        except Exception as error:
            result = {"status": "error", "message": str(error),
                      **({"code": error.code} if hasattr(error, "code") else {})}
        finally:
            if progress_token is not None:
                from core.optimization_progress import SINK
                SINK.reset(progress_token)
        if isinstance(result, dict):
            no_write |= result.get("status") == "rejected" or result.get("code") in {
                "PROPOSAL_NOT_CONFIRMED", "MODEL_PROJECT_MISMATCH", "PRESCRIPTION_RULE_VIOLATION", "PRESCRIPTION_WARNINGS", "QUALITY_GATE_FAILURE"}
        from core.analysis_runner import NativeOwnershipError
        cleanup_failed = isinstance(result, dict) and result.get("code") in {"ANALYSIS_CLEANUP_FAILED", "TRACE_CLEANUP_FAILED"}
        if cleanup_failed:
            self.trace_blocked = self.trace_blocked or result.get("message", "原生工具未安全释放")
        observes_native = name == "__refresh__" or effect.native or effect.kind == "context" or effect.observes_context
        observed_for_history = False
        try:
            if observes_native or cleanup_failed:
                self._require_native_ownership()
        except NativeOwnershipError as error:
            # serialized_operation returns errors as data. Check ownership
            # again before any observation, including metadata publication.
            self.uncertainty = view_error = str(error)
            current = self.state.read()
            self.state.publish(metadata={k: current[k] for k in ("active_project", "model_project", "proposal_confirmed")},
                               snapshot=None, validity="uncertain", context_valid=False,
                               error=view_error, operation_id=operation_id)
            if succeeded(result):
                result = {"status": "error", "code": error.code, "message": str(error)}
            quarantined = True
        else:
            quarantined = False
            unresolved = self._record_outcome(effect, result, no_write)
        if not quarantined and not no_write and observes_native:
            try:
                snapshot_result = (result if succeeded(result) else False) if name in {"__refresh__", "zemax_get_workbench_snapshot"} else None
                view_error = self._publish(operation_id, effect, result, no_write, snapshot_result, unresolved)
                observed_for_history = not view_error
            except Exception as error:
                # A successful write is still successful even if metadata/snapshot reads fail.
                view_error = f"操作已结束，工程状态读取失败：{error}"
                current = self.state.read()
                self.state.publish(metadata={key: current[key] for key in ("active_project", "model_project", "proposal_confirmed")},
                                   snapshot=None, validity="unavailable", error=view_error,
                                   context_valid=False,
                                   replace=succeeded(result) and effect.kind == "replace",
                                   model_changed=not no_write and effect.kind in {"modify", "calculate"}, operation_id=operation_id)
        if (observed_for_history and effect.native and effect.kind in {"replace", "modify"}
                and not no_write and succeeded(result) and not self.uncertainty
                and isinstance(result, dict) and result.get("status") == "success"
                and not (isinstance(result, dict) and result.get("rolled_back") is True)):
            # History failure must never change the outcome of the original
            # optical operation. Retain only this operation's successful view.
            try:
                self.state.record_history(operation_id, name)
            except Exception:
                pass
        status = "succeeded" if succeeded(result) else "rejected" if no_write else "failed"
        source = self.state.task_source(operation_id)
        self.state.finish(operation_id, status, result, view_error)
        self._follow(name, arguments, result, status, source)
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
