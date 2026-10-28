"""Opt-in stdio MCP forwarding to the single local engine service."""
from functools import wraps
import inspect
import json
import math
import os
import threading
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from core.workbench_effects import TOOL_EFFECTS, ToolEffect
from core.workbench_state import CONTEXT_KEYS, PROTOCOL_VERSION

SERVICE_FILE = Path(__file__).resolve().parents[1] / "output" / "workbench_service" / "service.json"


def encode_tool_arguments(value):
    """Encode optical infinity in strict JSON without hiding invalid NaN input.

    Numeric strings keep their original meaning; the receiving tool alone decides
    which parameters may represent infinity and performs its normal validation.
    """
    if isinstance(value, float):
        if math.isnan(value):
            raise ValueError("NaN is not a valid tool argument")
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: encode_tool_arguments(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_tool_arguments(item) for item in value]
    return value


class WorkbenchClient:
    def __init__(self, url, session_file=None):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("Workbench service must use a loopback HTTP URL")
        self.url = url.rstrip("/")
        self.session_file = Path(session_file or SERVICE_FILE)
        self.lock = threading.RLock()
        self.handshake_lock = threading.Lock()
        self.context = None
        self.credential_instance = None
        self.generation = 0

    def _request(self, path, credentials, content=None):
        request = urllib.request.Request(self.url + path, method="GET" if content is None else "POST", data=content,
            headers={"Content-Type": "application/json", "X-OptiForge-Token": credentials["token"]})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3600) as response:
            return json.load(response)

    def _adopt(self, context, generation):
        with self.lock:
            if (not isinstance(context, dict) or generation != self.generation
                    or context.get("service_instance_id") != self.credential_instance):
                return
            if (self.context is None or self.context["service_instance_id"] != context["service_instance_id"]
                    or context["state_version"] >= self.context["state_version"]):
                self.context = {key: context[key] for key in CONTEXT_KEYS}

    def call(self, name, arguments):
        try:
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ValueError("Tool name must be a string and arguments must be an object")
            encoded = encode_tool_arguments(arguments)
            # Validate before credential/network access (including recursive NaN).
            json.dumps(encoded, allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            return {"status": "error", "code": "INVALID_TOOL_ARGUMENTS",
                    "message": f"工具请求参数无法编码：{error}"}
        try:
            credentials = json.loads(self.session_file.read_text(encoding="utf-8"))
            if credentials["url"] != self.url:
                raise ValueError("Workbench address does not match the running service")
            if credentials.get("protocol_version") != PROTOCOL_VERSION or not credentials.get("service_instance_id"):
                return {"status": "error", "code": "WORKBENCH_PROTOCOL_INCOMPATIBLE", "message": "请更新并重启工程服务与 MCP 桥接器（协议 v2）。"}
            with self.lock:
                if self.credential_instance != credentials["service_instance_id"]:
                    self.credential_instance = credentials["service_instance_id"]
                    self.generation += 1
                generation = self.generation
            with self.handshake_lock:
                if self.context is None:
                    state = self._request("/api/state", credentials)
                    if state.get("protocol_version") != PROTOCOL_VERSION:
                        return {"status": "error", "code": "WORKBENCH_PROTOCOL_INCOMPATIBLE", "message": "工程状态协议不兼容。"}
                    self._adopt(state, generation)
            with self.lock:
                expected = self.context.copy() if self.context else None
            content = json.dumps({"protocol_version": PROTOCOL_VERSION, "name": name, "arguments": encoded,
                                  "expected_context": expected}, allow_nan=False).encode("utf-8")
            envelope = self._request("/api/tool", credentials, content)
            if envelope.get("protocol_version") != PROTOCOL_VERSION or "result" not in envelope:
                return {"status": "error", "code": "WORKBENCH_PROTOCOL_INCOMPATIBLE", "message": "工具响应协议不兼容。"}
            result = envelope["result"]
            effect = TOOL_EFFECTS.get(name, ToolEffect())
            success = not isinstance(result, dict) or result.get("status") not in {"error", "rejected", "failed"}
            if success and (effect.protected or effect.observes_context):
                self._adopt(envelope["context"], generation)
            return result
        except urllib.error.HTTPError as error:
            if error.code == 409:
                return {"status": "error", "code": "WORKBENCH_PROTOCOL_INCOMPATIBLE", "message": "请更新工程服务与 MCP 桥接器（协议 v2）。"}
            return {"status": "error", "code": "WORKBENCH_UNAVAILABLE",
                    "message": f"本地工程服务不可用，请核对服务状态：{error}"}
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            return {"status": "error", "code": "WORKBENCH_UNAVAILABLE",
                    "message": f"本地工程服务不可用，请运行 workbench.py：{error}"}

    def wrap(self, function):
        signature = inspect.signature(function)
        @wraps(function)
        def proxy(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            return self.call(function.__name__, bound.arguments)
        return proxy


def route_tool_aliases(namespace):
    """Keep every existing MCP handler/schema; change only its execution target."""
    url = os.environ.get("OPTIFORGE_SERVICE_URL")
    if not url:
        return
    client = WorkbenchClient(url, os.environ.get("OPTIFORGE_SERVICE_FILE"))
    for name, function in list(namespace.items()):
        if (name.startswith("_") and callable(function)
                and getattr(function, "__module__", "").startswith("tools.")):
            namespace[name] = client.wrap(function)
