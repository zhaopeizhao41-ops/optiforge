"""Read-only host pull for the existing design queue; never claims or executes.

The event stream is only a hint. Each call reads the authoritative workflow at
entry and at timeout, and consumes observation events without reading in a loop.
All networking uses the control client's loopback, credential and redirect checks.
"""
from __future__ import annotations

from copy import deepcopy
from collections import deque
import json
import time
import urllib.request

from core.workbench_control import PROTOCOL_VERSION, WorkbenchControlError

HTTP_TIMEOUT_SECONDS = 5
TASK_FIELDS = ("project_name", "request_id", "kind", "status", "version", "stale", "lease_expired")
TASK_KINDS = frozenset({"initial_proposal", "proposal_revision", "surface_question", "model_handoff"})
WORKFLOW_KEYS = frozenset({"project_name", "active_project", "selection_revision", "requirements_revision",
                           "proposal_hash", "proposal_revision", "authorization_revision"})
NOTICE = ("仅为当前任务候选快照，不代表领取或光学授权。请用既有 list/read 重新核对并按精确版本原子领取；"
          "模型交接执行前仍须重新核对提案授权和模型身份。事件游标不代表已领取或保证送达；本工具不会唤醒宿主。")


def _error(code, message):
    raise WorkbenchControlError(code, message)


def _counter(value):
    return type(value) is int and value >= 0


def _project(value):
    # Match the existing workflow's validation without invoking any local view.
    from core.design_workflow import WorkflowError, project_name
    try:
        return project_name(value)
    except (WorkflowError, ValueError):
        _error("WORKFLOW_PROJECT_INVALID", "请提供规范的明确工程名，不接受路径或隐式工程")


class WorkbenchTaskWaiter:
    def __init__(self, control, *, observe=None, clock=time.monotonic):
        self.control = control
        self.observe = observe or self._observe
        self.clock = clock

    def _check_identity(self, value, instance):
        if not isinstance(value, dict) or type(value.get("protocol_version")) is not int or value["protocol_version"] != PROTOCOL_VERSION:
            _error("WORKBENCH_PROTOCOL_INCOMPATIBLE", "工作台响应协议不兼容")
        if value.get("service_instance_id") != instance:
            _error("WORKBENCH_INSTANCE_MISMATCH", "工作台服务实例已改变，请重新读取")

    def _check_engine_context(self, value, instance, project):
        if not isinstance(value, dict) or value.get("service_instance_id") != instance:
            _error("WORKBENCH_INSTANCE_MISMATCH", "工作台上下文来自另一个服务实例")
        if value.get("active_project") != project:
            _error("WORKFLOW_PROJECT_MISMATCH", "活动工程已变化，请显式核对工程后重新等待")
        return value

    def _status(self, instance, project):
        status = self.control.status()
        self._check_identity(status, instance)
        self._check_engine_context(status.get("context"), instance, project)
        if not isinstance(status.get("capabilities"), dict) or status["capabilities"].get("collaboration") is not True:
            _error("WORKFLOW_UNAVAILABLE", "当前服务未启用工程协作")
        if (type(status["capabilities"].get("workflow_schema_version")) is not int
                or status["capabilities"]["workflow_schema_version"] != 1):
            _error("WORKFLOW_SCHEMA_INCOMPATIBLE", "需要工程协作 schema v1")
        return status

    def _observe(self, project, expected):
        """Only this fixed observation is permitted; no local/native fallback."""
        self.control._verify_instance()
        body = {
            "protocol_version": PROTOCOL_VERSION,
            "name": "zemax_design_workflow",
            "arguments": {"action": "view", "project_name": project},
            "expected_context": expected,
        }
        request = urllib.request.Request(self.control.url + "/api/tool", method="POST",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={
                "Content-Type": "application/json", "X-OptiForge-Token": self.control.token,
                "X-OptiForge-Protocol": str(PROTOCOL_VERSION),
            })
        return self.control._open(request, HTTP_TIMEOUT_SECONDS)

    def _read(self, instance, project):
        expected = self._status(instance, project)["context"]
        envelope = self.observe(project, expected)
        if not isinstance(envelope, dict) or type(envelope.get("protocol_version")) is not int or envelope["protocol_version"] != PROTOCOL_VERSION:
            _error("WORKBENCH_PROTOCOL_INCOMPATIBLE", "流程响应协议不兼容")
        self._check_engine_context(envelope.get("context"), instance, project)
        view = envelope.get("result")
        if not isinstance(view, dict):
            _error("WORKFLOW_RESPONSE_INVALID", "流程响应必须为对象")
        if view.get("status") != "success":
            _error(view.get("code") or "WORKFLOW_READ_FAILED", "无法读取权威工程流程；请核对状态，不要自动重放写入")
        if type(view.get("workflow_schema_version")) is not int or view["workflow_schema_version"] != 1:
            _error("WORKFLOW_SCHEMA_INCOMPATIBLE", "流程响应 schema 不兼容")
        if view.get("service_instance_id") != instance:
            _error("WORKBENCH_INSTANCE_MISMATCH", "流程响应来自另一个服务实例")
        context = view.get("context")
        if view.get("project_name") != project or not isinstance(context, dict) or context.get("project_name") != project or context.get("active_project") != project:
            _error("WORKFLOW_PROJECT_MISMATCH", "流程工程与指定活动工程不一致")
        if (set(context) != WORKFLOW_KEYS
                or not all(_counter(context[key]) for key in ("selection_revision", "requirements_revision", "authorization_revision"))
                or context["proposal_revision"] is not None and not _counter(context["proposal_revision"])
                or context["proposal_hash"] is not None and not isinstance(context["proposal_hash"], str)
                or not isinstance(view.get("draft"), dict)
                or type(view["draft"].get("revision")) is not int
                or view["draft"].get("revision") != context["requirements_revision"]
                or not isinstance(view.get("tasks"), list)):
            _error("WORKFLOW_RESPONSE_INVALID", "流程上下文、需求版本或任务列表不完整")
        return view, envelope.get("operation_id")

    def _candidates(self, view, instance, project):
        context = view["context"]
        candidates = []
        for task in view["tasks"]:
            if (not isinstance(task, dict) or task.get("project_name") != project
                    or not isinstance(task.get("request_id"), str) or not task["request_id"].strip() or len(task["request_id"]) > 100
                    or not isinstance(task.get("kind"), str) or task["kind"] not in TASK_KINDS
                    or task.get("status") != "pending" or task.get("stale") is not False
                    or task.get("lease_expired") is not False or not _counter(task.get("version")) or task["version"] < 1
                    or type(task.get("requirements_revision")) is not int
                    or task["requirements_revision"] != context["requirements_revision"]):
                continue
            reference = task.get("model_reference")
            if reference is not None and (not isinstance(reference, dict)
                    or reference != view.get("model_reference")
                    or reference.get("service_instance_id") != instance
                    or reference.get("model_project") != project):
                continue
            if task["kind"] == "surface_question" and not isinstance(reference, dict):
                continue
            if task["kind"] == "model_handoff":
                proposal = view.get("proposal")
                if (view.get("proposal_bound") is not True or not isinstance(proposal, dict)
                        or proposal.get("user_confirmed_to_simulate") is not True
                        or not context["proposal_hash"]
                        or task.get("proposal_hash") != context["proposal_hash"]
                        or proposal.get("proposal_hash") != context["proposal_hash"]
                        or proposal.get("proposal_revision") != context["proposal_revision"]
                        or proposal.get("requirements_revision") != context["requirements_revision"]
                        or view["draft"].get("approval") != context["proposal_hash"]):
                    continue
                current_model = view.get("model_reference")
                if current_model is not None and (not isinstance(current_model, dict)
                        or current_model.get("service_instance_id") != instance
                        or current_model.get("model_project") != project):
                    continue
            candidates.append({key: deepcopy(task[key]) for key in TASK_FIELDS})
            if len(candidates) >= 50:
                break
        return candidates

    def _events(self, instance, project, cursor, timeout, observed_operations, *, historical=False):
        result = self.control.events(instance, cursor, timeout)
        self._check_identity(result, instance)
        events = result.get("events")
        next_seq, reset = result.get("next_seq"), result.get("reset_required")
        if (not isinstance(events, list) or not _counter(next_seq) or type(reset) is not bool
                or not reset and next_seq < cursor):
            _error("WORKBENCH_EVENTS_INVALID", "事件响应或游标不完整")
        if result.get("closed") is True:
            _error("WORKBENCH_UNAVAILABLE", "工作台服务正在停止")
        reread = reset
        for event in events:
            if not isinstance(event, dict) or event.get("service_instance_id") != instance:
                _error("WORKBENCH_INSTANCE_MISMATCH", "事件来自另一个服务实例")
            seq = event.get("seq")
            if not _counter(seq) or seq <= cursor or seq > next_seq:
                _error("WORKBENCH_EVENTS_INVALID", "事件序号不符合当前游标")
            if not historical and "active_project" in event and event["active_project"] != project:
                _error("WORKFLOW_PROJECT_MISMATCH", "等待期间活动工程已变化")
            task = event.get("design_task")
            if event.get("kind") == "task_changed" and isinstance(task, dict) and task.get("project_name") == project:
                reread = True
            # queued/started/finished observations never cause another view.
            # A real state change (including model replacement) still does.
            if event.get("kind") == "state_changed" and event.get("operation_id") not in observed_operations:
                reread = True
        return next_seq, reset, reread

    def wait_for_task(self, service_instance_id, project_name, after_seq=0, timeout_ms=20000):
        if (not isinstance(service_instance_id, str) or not service_instance_id
                or service_instance_id != self.control.instance):
            _error("WORKBENCH_INSTANCE_MISMATCH", "service_instance_id 与当前凭据实例不一致")
        project = _project(project_name)
        if not _counter(after_seq):
            _error("INVALID_EVENT_CURSOR", "after_seq 必须是非负整数")
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 20000:
            _error("INVALID_EVENT_TIMEOUT", "timeout_ms 必须在 0..20000")
        started = self.clock()
        deadline = started + timeout_ms / 1000
        cursor, reset_seen, waited_ms = after_seq, False, 0
        operations = deque(maxlen=128)
        self._status(service_instance_id, project)
        # Older hints may describe an earlier active project. Validate the
        # stream, then read current authority rather than acting on history.
        cursor, reset_seen, _ = self._events(service_instance_id, project, cursor, 0, operations, historical=True)
        view, operation = self._read(service_instance_id, project)
        if operation:
            operations.append(operation)
        while True:
            candidates = self._candidates(view, service_instance_id, project)
            # Drain and classify every event after the snapshot. Never jump to
            # a high-water mark without noticing a task inserted in that gap.
            cursor, reset, reread = self._events(service_instance_id, project, cursor, 0, operations)
            reset_seen |= reset
            remaining = min(timeout_ms - waited_ms, max(0, int((deadline - self.clock()) * 1000)))
            if reread:
                view, operation = self._read(service_instance_id, project)
                if operation:
                    operations.append(operation)
                candidates = self._candidates(view, service_instance_id, project)
                remaining = min(timeout_ms - waited_ms, max(0, int((deadline - self.clock()) * 1000)))
            if candidates or remaining <= 0:
                break
            before = self.clock()
            cursor, reset, reread = self._events(service_instance_id, project, cursor, remaining, operations)
            elapsed = max(0, int((self.clock() - before) * 1000))
            waited_ms = min(timeout_ms, waited_ms + elapsed)
            reset_seen |= reset
            if reread:
                view, operation = self._read(service_instance_id, project)
                if operation:
                    operations.append(operation)
            # A long poll without notifications exhausted its requested wait.
            # Treat it as the deadline even for injected clocks in tests.
            elif self.clock() >= deadline:
                break
        # The final authoritative read compensates for lost/truncated hints.
        # Candidates remain snapshots: another host may claim before we return.
        if not candidates:
            view, _ = self._read(service_instance_id, project)
            candidates = self._candidates(view, service_instance_id, project)
        return {
            "protocol_version": PROTOCOL_VERSION, "status": "ready" if candidates else "idle",
            "service_instance_id": service_instance_id, "project_name": project,
            "context": deepcopy(view["context"]), "tasks": candidates,
            "next_seq": cursor, "reset_required": reset_seen, "waited_ms": waited_ms,
            "elapsed_ms": max(0, int((self.clock() - started) * 1000)), "notice": NOTICE,
        }
