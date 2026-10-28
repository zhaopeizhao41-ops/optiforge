"""Opt-in stdio MCP forwarding to the single local engine service."""
from functools import wraps
import inspect
import json
import math
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

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

    def call(self, name, arguments):
        try:
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ValueError("Tool name must be a string and arguments must be an object")
            content = json.dumps({"name": name, "arguments": encode_tool_arguments(arguments)},
                                 allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            return {"status": "error", "code": "INVALID_TOOL_ARGUMENTS",
                    "message": f"工具请求参数无法编码：{error}"}
        try:
            credentials = json.loads(self.session_file.read_text(encoding="utf-8"))
            if credentials["url"] != self.url:
                raise ValueError("Workbench address does not match the running service")
            request = urllib.request.Request(self.url + "/api/tool", method="POST",
                data=content,
                headers={"Content-Type": "application/json", "X-OptiForge-Token": credentials["token"]})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3600) as response:
                return json.load(response)
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
