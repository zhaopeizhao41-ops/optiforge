"""Loopback HTTP broker for the workbench UI control tools (MCP side).

This is intentionally independent from ``core.workbench_client`` and the optical
engine: it only talks HTTP to the already running local workbench service. Each
request re-reads ``/api/session`` and verifies the same ``service_instance_id``
as the credential file and of the actual response object, so a stale or swapped
service is never silently used. Redirects are refused so the local token is
never forwarded to another URL.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from http.client import HTTPException
from types import MappingProxyType
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = 2
PAGES = frozenset({"workflow", "design", "analysis"})
# Read-only visualization access: workflow view and immutable archive reads only.
READ_ONLY_WORKFLOW_ACTIONS = frozenset({"view", "milestone_artifact", "milestone_quality_artifact"})
READ_RESULT_PATH = re.compile(r"/api/(?:traces|analyses)/[A-Za-z0-9._:-]{1,100}")
ERROR_BODY_LIMIT = 16 * 1024
ERROR_JSON_DEPTH_LIMIT = 32
# Public messages are local constants. Never use a service's message, HTTP
# reason, or exception text at the MCP boundary, even with a recognized code.
PUBLIC_ERROR_MESSAGES = MappingProxyType({
    "WORKBENCH_CONFIG_MISSING": "未配置本机工作台服务与凭据文件；请核对连接配置。",
    "WORKBENCH_URL_INVALID": "工作台服务地址必须是 loopback HTTP 地址。",
    "WORKBENCH_CREDENTIALS_INVALID": "工作台凭据无效，请核对本机服务配置。",
    "WORKBENCH_CREDENTIALS_MISMATCH": "工作台凭据与配置或当前服务不匹配。",
    "WORKBENCH_PROTOCOL_INCOMPATIBLE": "工作台协议不兼容，需要协议 v2。",
    "WORKBENCH_INSTANCE_MISMATCH": "工作台服务实例已改变，请重新读取。",
    "WORKBENCH_UNAVAILABLE": "无法连接工作台服务，请核对当前连接状态。",
    "WORKBENCH_HTTP_ERROR": "工作台请求失败，请核对服务状态；不要自动重放写入。",
    "INVALID_EVENT_CURSOR": "after_seq 必须是非负整数。",
    "INVALID_EVENT_TIMEOUT": "timeout_ms 必须在 0..20000。",
    "WORKBENCH_EVENTS_INVALID": "工作台事件响应或游标无效，请重新读取。",
    "WORKFLOW_PROJECT_INVALID": "请提供规范的明确工程名，不接受路径或隐式工程。",
    "WORKFLOW_PROJECT_MISMATCH": "活动工程已变化，请显式核对工程后重新等待。",
    "WORKFLOW_UNAVAILABLE": "当前服务未启用工程协作。",
    "WORKFLOW_SCHEMA_INCOMPATIBLE": "工程协作 schema 不兼容，需要 schema v1。",
    "WORKFLOW_RESPONSE_INVALID": "工程流程响应不完整，请重新读取。",
    "WORKFLOW_READ_FAILED": "无法读取权威工程流程；请核对状态，不要自动重放写入。",
    "SERVICE_INSTANCE_MISMATCH": "请求的服务实例不匹配，请重新读取。",
    "PAGE_UNAVAILABLE": "当前服务未启用请求的页面。",
    "INVALID_UI_SESSION_ID": "ui_session_id 格式无效。",
    "INVALID_REQUEST_ID": "request_id 格式无效。",
    "UNKNOWN_PAGE": "页面必须是 workflow/design/analysis。",
    "UI_SESSION_UNKNOWN": "前端会话不存在或已过期。",
    "UI_SESSION_OFFLINE": "前端会话已离线。",
    "COMMAND_UNKNOWN": "导航命令不存在或历史已淘汰，请核对当前会话。",
    "COMMAND_PENDING": "该前端会话已有待执行导航命令。",
    "REQUEST_ID_CONFLICT": "同一 request_id 已用于其他页面或定位。",
    "INVALID_FOCUS": "focus 与页面不匹配或格式无效：design {surface}，analysis {tab, field?}，workflow {tab, stage?}。",
})
# These are observed control/navigation failures. A
# service cannot assert a local-only configuration or credential diagnosis.
REMOTE_ERROR_CODES = frozenset({
    "WORKBENCH_PROTOCOL_INCOMPATIBLE", "SERVICE_INSTANCE_MISMATCH", "PAGE_UNAVAILABLE",
    "INVALID_UI_SESSION_ID", "INVALID_REQUEST_ID", "UNKNOWN_PAGE", "UI_SESSION_UNKNOWN",
    "UI_SESSION_OFFLINE", "COMMAND_UNKNOWN", "COMMAND_PENDING", "REQUEST_ID_CONFLICT", "INVALID_FOCUS",
})


class WorkbenchControlError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _public_error_for_code(code, *, remote=False):
    allowed = REMOTE_ERROR_CODES if remote else PUBLIC_ERROR_MESSAGES
    code = code if type(code) is str and code in allowed else "WORKBENCH_HTTP_ERROR"
    return {"status": "error", "code": code, "message": PUBLIC_ERROR_MESSAGES[code]}


def public_control_error(error):
    """Project a control failure into a fixed public envelope, without its text."""
    return _public_error_for_code(error.code)


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _invalid_json_constant(_):
    raise ValueError("Non-finite JSON value")


def _check_error_payload_depth(payload):
    pending = [(payload, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > ERROR_JSON_DEPTH_LIMIT:
            raise ValueError("Error JSON nesting limit")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)


def _http_error_projection(error):
    """Read only a bounded error body; all malformed forms use one safe error."""
    code = None
    try:
        raw = error.read(ERROR_BODY_LIMIT + 1)
        if type(raw) is bytes and len(raw) <= ERROR_BODY_LIMIT:
            payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_json_object,
                                 parse_constant=_invalid_json_constant)
            if isinstance(payload, dict):
                _check_error_payload_depth(payload)
                code = payload.get("code")
    except (OSError, HTTPException, ValueError, TypeError, RecursionError):
        pass
    finally:
        try:
            error.close()
        except (OSError, HTTPException, ValueError):
            pass
    return _public_error_for_code(code, remote=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(request.full_url, code, "redirect blocked", headers, fp)


def _strip_secrets(value):
    if isinstance(value, dict):
        return {key: _strip_secrets(item) for key, item in value.items() if key.lower() != "token"}
    if isinstance(value, list):
        return [_strip_secrets(item) for item in value]
    return value


def _loopback(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or port is None):
        return None
    return parsed


def _credentials_from_file(path, url):
    try:
        credentials = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise WorkbenchControlError("WORKBENCH_CREDENTIALS_INVALID", "无法读取工作台凭据文件")
    if not isinstance(credentials, dict):
        raise WorkbenchControlError("WORKBENCH_CREDENTIALS_INVALID", "工作台凭据文件必须是 JSON 对象")
    token = credentials.get("token")
    instance = credentials.get("service_instance_id")
    protocol = credentials.get("protocol_version")
    if not isinstance(token, str) or not token:
        raise WorkbenchControlError("WORKBENCH_CREDENTIALS_INVALID", "凭据 token 缺失或不是字符串")
    if not isinstance(instance, str) or not instance:
        raise WorkbenchControlError("WORKBENCH_CREDENTIALS_INVALID", "凭据 service_instance_id 缺失或不是字符串")
    if type(protocol) is not int or protocol != PROTOCOL_VERSION:
        raise WorkbenchControlError("WORKBENCH_PROTOCOL_INCOMPATIBLE", "需要工作台协议 v2")
    if not isinstance(credentials.get("url"), str) or credentials["url"] != url.rstrip("/"):
        raise WorkbenchControlError("WORKBENCH_CREDENTIALS_MISMATCH", "凭据地址与 OPTIFORGE_SERVICE_URL 不一致")
    return credentials


class WorkbenchControlClient:
    def __init__(self, url, credentials):
        if not _loopback(url):
            raise WorkbenchControlError("WORKBENCH_URL_INVALID", "工作台服务地址必须是 loopback HTTP 地址")
        if not isinstance(credentials, dict):
            raise WorkbenchControlError("WORKBENCH_CREDENTIALS_INVALID", "工作台凭据必须是对象")
        self.url = url.rstrip("/")
        self.token = credentials["token"]
        self.instance = credentials["service_instance_id"]
        self.protocol = credentials["protocol_version"]
        if type(self.protocol) is not int or self.protocol != PROTOCOL_VERSION:
            raise WorkbenchControlError("WORKBENCH_PROTOCOL_INCOMPATIBLE", "需要工作台协议 v2")

    @classmethod
    def from_environment(cls):
        url = os.environ.get("OPTIFORGE_SERVICE_URL")
        file = os.environ.get("OPTIFORGE_SERVICE_FILE")
        if not url or not file:
            raise WorkbenchControlError(
                "WORKBENCH_CONFIG_MISSING",
                "未配置 OPTIFORGE_SERVICE_URL / OPTIFORGE_SERVICE_FILE；"
                "请指向本机运行中的工作台服务与凭据文件（不会退回原生会话）。",
            )
        if not _loopback(url):
            raise WorkbenchControlError("WORKBENCH_URL_INVALID", "OPTIFORGE_SERVICE_URL 必须是 loopback HTTP 地址")
        credentials = _credentials_from_file(file, url)
        return cls(url, credentials)

    def _open(self, request, timeout):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            public = _http_error_projection(error)
            raise WorkbenchControlError(public["code"], public["message"]) from None
        except (urllib.error.URLError, OSError, HTTPException, ValueError, RecursionError):
            raise WorkbenchControlError("WORKBENCH_UNAVAILABLE", "无法连接工作台服务") from None

    def _verify_instance(self):
        request = urllib.request.Request(self.url + "/api/session", headers={
            "X-OptiForge-Token": self.token,
        })
        session = self._open(request, 5)
        if (not isinstance(session, dict) or session.get("protocol_version") != PROTOCOL_VERSION
                or session.get("service_instance_id") != self.instance):
            raise WorkbenchControlError("WORKBENCH_INSTANCE_MISMATCH", "工作台服务实例已改变，请重新读取")
        if session.get("token") != self.token:
            raise WorkbenchControlError("WORKBENCH_CREDENTIALS_MISMATCH", "工作台凭据与当前服务不匹配")
        return session

    def _request(self, method, path, body=None, timeout=5):
        self._verify_instance()
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "X-OptiForge-Token": self.token,
            "X-OptiForge-Protocol": str(PROTOCOL_VERSION),
            "Content-Type": "application/json",
        })
        result = self._open(request, timeout)
        if not isinstance(result, dict) or result.get("protocol_version") != PROTOCOL_VERSION:
            raise WorkbenchControlError("WORKBENCH_PROTOCOL_INCOMPATIBLE", "工作台响应协议不兼容")
        if result.get("service_instance_id") != self.instance:
            raise WorkbenchControlError("WORKBENCH_INSTANCE_MISMATCH", "工作台响应来自另一个服务实例")
        if result.get("status") == "error":
            public = _public_error_for_code(result.get("code"), remote=True)
            raise WorkbenchControlError(public["code"], public["message"]) from None
        return _strip_secrets(result)

    def read_json(self, path):
        """GET a cached state/result body. Only fixed read endpoints are allowed."""
        if not isinstance(path, str) or not (path == "/api/state" or READ_RESULT_PATH.fullmatch(path)):
            raise WorkbenchControlError("WORKBENCH_HTTP_ERROR", "只允许读取状态或缓存结果")
        self._verify_instance()
        request = urllib.request.Request(self.url + path, headers={
            "X-OptiForge-Token": self.token, "X-OptiForge-Protocol": str(PROTOCOL_VERSION)})
        result = self._open(request, 10)
        if not isinstance(result, dict) or result.get("protocol_version") != PROTOCOL_VERSION:
            raise WorkbenchControlError("WORKBENCH_PROTOCOL_INCOMPATIBLE", "工作台响应协议不兼容")
        instance = result.get("service_instance_id", (result.get("source") or {}).get("service_instance_id"))
        if instance != self.instance:
            raise WorkbenchControlError("WORKBENCH_INSTANCE_MISMATCH", "工作台响应来自另一个服务实例")
        return _strip_secrets(result)

    def read_tool(self, project_name, action, payload=None):
        """Run one whitelisted read-only workflow action through the engine queue."""
        if action not in READ_ONLY_WORKFLOW_ACTIONS:
            raise WorkbenchControlError("WORKBENCH_HTTP_ERROR", "只允许只读流程查询")
        if not isinstance(project_name, str) or not project_name:
            raise WorkbenchControlError("WORKFLOW_PROJECT_INVALID", "请提供明确工程名")
        self._verify_instance()
        arguments = {"action": action, "project_name": project_name}
        if payload is not None:
            arguments["payload"] = payload
        body = {"protocol_version": PROTOCOL_VERSION, "name": "zemax_design_workflow", "arguments": arguments}
        request = urllib.request.Request(self.url + "/api/tool", method="POST",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={
                "Content-Type": "application/json", "X-OptiForge-Token": self.token,
                "X-OptiForge-Protocol": str(PROTOCOL_VERSION)})
        envelope = self._open(request, 30)
        if not isinstance(envelope, dict) or envelope.get("protocol_version") != PROTOCOL_VERSION:
            raise WorkbenchControlError("WORKBENCH_PROTOCOL_INCOMPATIBLE", "工作台响应协议不兼容")
        if (envelope.get("context") or {}).get("service_instance_id") != self.instance:
            raise WorkbenchControlError("WORKBENCH_INSTANCE_MISMATCH", "工作台响应来自另一个服务实例")
        return _strip_secrets(envelope)

    def status(self):
        result = self._request("GET", "/api/ui/status")
        ui = result.pop("ui", {})
        result.update({
            "frontends": ui.get("frontends", []),
            "commands": ui.get("commands", []),
            "pending": ui.get("pending", []),
            "pages": ui.get("pages", []),
            "session_lease_ms": ui.get("session_lease_ms"),
            "command_ttl_ms": ui.get("command_ttl_ms"),
        })
        return _strip_secrets(result)

    def events(self, service_instance_id, after_seq=0, timeout_ms=0):
        if not isinstance(service_instance_id, str) or service_instance_id != self.instance:
            raise WorkbenchControlError("WORKBENCH_INSTANCE_MISMATCH", "service_instance_id 与当前凭据实例不一致")
        if type(after_seq) is not int or after_seq < 0:
            raise WorkbenchControlError("INVALID_EVENT_CURSOR", "after_seq 必须是非负整数")
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 20000:
            raise WorkbenchControlError("INVALID_EVENT_TIMEOUT", "timeout_ms 必须在 0..20000")
        query = urllib.parse.urlencode({"instance_id": service_instance_id, "after_seq": after_seq,
                                        "timeout_ms": timeout_ms})
        result = self._request("GET", f"/api/events?{query}", timeout=timeout_ms / 1000 + 6)
        if result.get("service_instance_id") != service_instance_id:
            raise WorkbenchControlError("WORKBENCH_INSTANCE_MISMATCH", "事件流返回了另一个服务实例")
        return result

    def navigate(self, service_instance_id, ui_session_id, page, request_id, focus=None):
        if not isinstance(service_instance_id, str) or service_instance_id != self.instance:
            raise WorkbenchControlError("WORKBENCH_INSTANCE_MISMATCH", "service_instance_id 与当前凭据实例不一致")
        if not isinstance(page, str) or page not in PAGES:
            raise WorkbenchControlError("UNKNOWN_PAGE", "page 必须是 workflow/design/analysis")
        from core.workbench_ui import valid_focus
        if not valid_focus(page, focus):
            raise WorkbenchControlError("INVALID_FOCUS", PUBLIC_ERROR_MESSAGES["INVALID_FOCUS"])
        body = {
            "protocol_version": PROTOCOL_VERSION,
            "service_instance_id": service_instance_id,
            "ui_session_id": ui_session_id,
            "page": page,
            "request_id": request_id,
        }
        if focus is not None:
            body["focus"] = focus
        return self._request("POST", "/api/ui/navigate", body)
