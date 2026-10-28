"""Loopback HTTP host and a single owner thread for all ZOS-API operations."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import urllib.parse
import uuid

from core.workbench_client import SERVICE_FILE
from core.workbench_engine import EngineWorker
from core.workbench_snapshot import json_safe
from core.workbench_state import PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[1]


class WorkbenchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, engine, dist=None):
        self.engine = engine
        self.token = secrets.token_urlsafe(32)
        self.dist = Path(dist or ROOT / "frontend" / "dist").resolve()
        super().__init__(address, WorkbenchHandler)

    def shutdown(self):
        self.engine.state.close()
        super().shutdown()

    def server_close(self):
        self.engine.state.close()
        super().server_close()


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "OptiForge/0.4"

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        payload = {"protocol_version": PROTOCOL_VERSION, **payload}
        self._send(json.dumps(json_safe(payload), ensure_ascii=False, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def _send(self, content, mime, status=200):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _allowed(self, authenticate=True):
        port = self.server.server_port
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self._json({"status": "error", "message": "Invalid local host"}, 403)
            return False
        origin = self.headers.get("Origin")
        allowed_origins = {f"http://{host}:{p}" for host in ("127.0.0.1", "localhost") for p in (port, 5173)}
        if ((origin and origin not in allowed_origins)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self._json({"status": "error", "message": "Cross-origin access denied"}, 403)
            return False
        if authenticate and not secrets.compare_digest(self.headers.get("X-OptiForge-Token", ""), self.server.token):
            self._json({"status": "error", "message": "Local session token required"}, 403)
            return False
        return True

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._allowed(authenticate=path.startswith("/api/") and path not in {"/api/health", "/api/session"}):
            return
        if path == "/api/health":
            self._json({"status": "success", "service": "optiforge-workbench", "schema_version": 1})
        elif path == "/api/session":
            self._json({"token": self.server.token, "service_instance_id": self.server.engine.state.context()["service_instance_id"]})
        elif path == "/api/state":
            self._json(self.server.engine.state.read())
        elif path.startswith("/api/traces/"):
            result = self.server.engine.state.get_trace(path.removeprefix("/api/traces/"))
            self._json(result or {"status": "error", "code": "TRACE_NOT_FOUND", "message": "结果不存在或已淘汰"}, 200 if result else 404)
        elif path == "/api/events":
            try:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                after = int(query["after_seq"][0])
                timeout = int(query.get("timeout_ms", ["20000"])[0])
                instance = query["instance_id"][0]
                if not 0 <= timeout <= 20000:
                    raise ValueError("事件等待最长 20 秒")
                self._json(self.server.engine.state.wait_events(instance, after, timeout))
            except (KeyError, ValueError) as error:
                self._json({"status": "error", "message": f"Invalid event cursor: {error}"}, 400)
        elif path.startswith("/api/"):
            self._json({"status": "error", "message": "Unknown endpoint"}, 404)
        else:
            target = (self.server.dist / urllib.parse.unquote(path).lstrip("/")).resolve()
            if not target.is_relative_to(self.server.dist):
                self._json({"status": "error", "message": "Invalid path"}, 403)
                return
            if path == "/":
                target = self.server.dist / "index.html"
            if not target.is_file():
                self._json({"status": "error", "message": "请先在 frontend 中运行 npm run build"}, 404)
                return
            mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}.get(target.suffix)
            self._send(target.read_bytes(), (mime or mimetypes.guess_type(target.name)[0] or "application/octet-stream") + "; charset=utf-8")

    def do_POST(self):
        if not self._allowed():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32 * 1024 * 1024:
                raise ValueError("文件或请求大小应在 0–32 MB 之间")
            content = self.rfile.read(length)
            if len(content) != length:
                raise ValueError("请求内容不完整，请重新打开文件")
            path = urllib.parse.urlsplit(self.path).path
            content_type = self.headers.get("Content-Type", "").split(";")[0]
            if path == "/api/model/import":
                if self.headers.get("X-OptiForge-Protocol") != str(PROTOCOL_VERSION):
                    self._json({"status": "error", "code": "WORKBENCH_PROTOCOL_INCOMPATIBLE", "message": "请更新工作台客户端（协议 v2）。"}, 409)
                    return
                expected = json.loads(urllib.parse.unquote(self.headers.get("X-Expected-Context", "null")))
                conflict = self.server.engine.state.validate_context(expected)
                if conflict:
                    self._json({"operation_id": None, "result": conflict, "context": self.server.engine.state.context()})
                    return
                if content_type != "application/octet-stream":
                    raise ValueError("Expected optical file content")
                name = urllib.parse.unquote(self.headers.get("X-File-Name", ""))
                if (not name or any(ord(char) < 32 or char in '/\\:<>"|?*' for char in name)
                        or name.rstrip(". ") != name or Path(name).stem.upper() in
                        {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
                        or Path(name).suffix.lower() not in {".zmx", ".zos"}):
                    raise ValueError("请选择 .zmx 或 .zos 文件")
                from tools.project_manager import sanitize_project_name
                project = "import_" + sanitize_project_name(Path(name).stem)[:64] + "_" + uuid.uuid4().hex[:8]
                folder = ROOT / "output" / project / "source"
                folder.mkdir(parents=True, exist_ok=False)
                destination = folder / name
                destination.write_bytes(content)
                self._json(self.server.engine.load_model(destination, expected_context=expected))
            elif path == "/api/tool":
                if content_type != "application/json":
                    raise ValueError("Expected JSON tool arguments")
                data = json.loads(content, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not isinstance(data.get("arguments", {}), dict):
                    raise ValueError("Invalid tool request")
                if data.get("protocol_version") != PROTOCOL_VERSION:
                    self._json({"status": "error", "code": "WORKBENCH_PROTOCOL_INCOMPATIBLE", "message": "请更新 MCP 桥接器（协议 v2）。"}, 409)
                    return
                self._json(self.server.engine.call_envelope(data["name"], data.get("arguments", {}),
                                                           expected_context=data.get("expected_context")))
            elif path == "/api/trace":
                if content_type != "application/json":
                    raise ValueError("Expected JSON trace settings")
                data = json.loads(content, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(data, dict) or data.get("protocol_version") != PROTOCOL_VERSION:
                    self._json({"status": "error", "code": "WORKBENCH_PROTOCOL_INCOMPATIBLE", "message": "请更新工作台客户端（协议 v2）。"}, 409)
                    return
                result = self.server.engine.request_trace(data.get("settings"), expected_context=data.get("expected_context"))
                self._json(result, 202 if result.get("status") == "accepted" else 200)
            elif path == "/api/refresh":
                data = json.loads(content)
                if not isinstance(data, dict) or data.get("protocol_version") != PROTOCOL_VERSION:
                    self._json({"status": "error", "code": "WORKBENCH_PROTOCOL_INCOMPATIBLE", "message": "请更新工作台客户端（协议 v2）。"}, 409)
                    return
                self._json(self.server.engine.refresh(), 202)
            else:
                self._json({"status": "error", "message": "Unknown endpoint"}, 404)
        except (ValueError, OSError) as error:
            self._json({"status": "error", "message": str(error)}, 400)


def write_service_file(server, path=SERVICE_FILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "token": server.token,
                                        "protocol_version": PROTOCOL_VERSION,
                                        "service_instance_id": server.engine.state.context()["service_instance_id"]}), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
