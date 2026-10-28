"""Loopback HTTP host and a single owner thread for all ZOS-API operations."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import urllib.parse
import uuid

from core.workbench_client import SERVICE_FILE
from core.workbench_snapshot import json_safe

ROOT = Path(__file__).resolve().parents[1]


class EngineWorker:
    def __init__(self, registry=None):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="optiforge-engine")
        self.registry = registry

    def _call(self, name, arguments):
        try:
            if self.registry is None:
                import tools
                self.registry = {key: getattr(tools, key) for key in tools.__all__}
            if name not in self.registry:
                return {"status": "error", "code": "UNKNOWN_TOOL", "message": "未知工具"}
            return json_safe(self.registry[name](**arguments))
        except Exception as error:
            return {"status": "error", "message": str(error)}

    def call(self, name, arguments=None):
        return self.executor.submit(self._call, name, arguments or {}).result()

    def load_model(self, filepath):
        # Keep load + read in one queue item: a second import must not replace
        # the model between these two operations.
        def load_and_read():
            loaded = self._call("zemax_load_file", {"filepath": str(filepath)})
            return self._call("zemax_get_workbench_snapshot", {}) if loaded.get("status") == "success" else loaded
        return self.executor.submit(load_and_read).result()

    def close(self):
        def close_session():
            from core.zos_session import ZOSSession
            if ZOSSession._instance is not None:
                ZOSSession._instance.close()
        self.executor.submit(close_session).result()
        self.executor.shutdown(wait=True)


class WorkbenchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, engine, dist=None):
        self.engine = engine
        self.token = secrets.token_urlsafe(32)
        self.dist = Path(dist or ROOT / "frontend" / "dist").resolve()
        super().__init__(address, WorkbenchHandler)


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "OptiForge/0.3"

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
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
            self._json({"token": self.server.token})
        elif path == "/api/state":
            self._json(self.server.engine.call("zemax_get_workbench_snapshot"))
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
                self._json(self.server.engine.load_model(destination))
            elif path == "/api/tool":
                if content_type != "application/json":
                    raise ValueError("Expected JSON tool arguments")
                data = json.loads(content, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not isinstance(data.get("arguments", {}), dict):
                    raise ValueError("Invalid tool request")
                self._json(self.server.engine.call(data["name"], data.get("arguments", {})))
            else:
                self._json({"status": "error", "message": "Unknown endpoint"}, 404)
        except (ValueError, OSError) as error:
            self._json({"status": "error", "message": str(error)}, 400)


def write_service_file(server, path=SERVICE_FILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "token": server.token}), encoding="utf-8")
