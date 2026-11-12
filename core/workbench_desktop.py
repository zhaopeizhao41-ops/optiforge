"""MCP-only, read-only adapter for the trusted profile-window diagnostic CLI.

No optical imports or GUI control. Profile candidates never establish the current
URL, a frontend-to-HWND binding, or permission to act on a window.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import queue
import re
import stat
import subprocess
import sys
import threading
import time

from core.workbench_control import WorkbenchControlClient

OUTPUT_LIMIT = 256 * 1024
COLLECTOR_SECONDS = 20
COORDINATES = "GetWindowRect; DPI-virtualized screen coordinates"
CLI_OBSERVATION = "point_in_time; profile association only; no action guarantee"
BROWSERS = frozenset({"Auto", "Chrome", "Edge"})
CLI_FAILURES = frozenset({"service_unavailable", "service_changed", "browser_unavailable",
                          "browser_metadata_incomplete", "observation_incomplete",
                          "window_set_changed", "diagnostic_unavailable"})
REASONS = CLI_FAILURES | frozenset({"stable_profile_observation", "invalid_arguments",
    "unsupported_platform", "unsupported_endpoint", "unsupported_path", "configuration_changed",
    "instance_mismatch", "collector_unavailable", "collector_failed", "collector_timeout",
    "collector_output_too_large", "collector_result_invalid"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})\Z")


class _Failure(Exception):
    def __init__(self, reason):
        self.reason = reason if reason in REASONS else "diagnostic_unavailable"


@dataclass(frozen=True)
class Binding:
    url: str
    credential_path: Path
    workspace: Path
    instance: str
    token: str
    fingerprint: tuple
    configured_url: str
    configured_file: str


def _unavailable(reason):
    return {"schema_version": 1, "status": "unavailable", "reason": reason,
            "url_binding": "unverified", "action_supported": False,
            "observation": "point_in_time_profile_candidates", "observed_at": None,
            "service": None, "candidates": []}


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Failure("collector_result_invalid")
        result[key] = value
    return result


def _parse_json(data):
    def invalid_constant(_):
        raise _Failure("collector_result_invalid")
    try:
        return json.loads(data, object_pairs_hook=_json_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise _Failure("collector_result_invalid") from None


def _canonical_path(value, *, directory=False):
    """Reject aliases rather than silently normalizing the configured binding."""
    if (type(value) is not str or not re.match(r"^[A-Za-z]:\\", value)
            or "/" in value or any(ord(char) < 32 for char in value)):
        raise _Failure("unsupported_path")
    parts = value[3:].split("\\")
    if not parts or any(not part or part in {".", ".."} or part[-1:] in {".", " "}
                        or any(char in part for char in ':<>"|?*~') for part in parts):
        raise _Failure("unsupported_path")
    path = Path(value)
    try:
        for ancestor in reversed((path, *path.parents)):
            info = ancestor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise _Failure("unsupported_path")
        resolved = path.resolve(strict=True)
        if str(resolved).casefold() != value.casefold():
            raise _Failure("unsupported_path")
        if directory and not path.is_dir() or not directory and not path.is_file():
            raise _Failure("unsupported_path")
    except (OSError, ValueError, RuntimeError):
        raise _Failure("unsupported_path") from None
    return path


def _canonical_endpoint(value):
    if type(value) is not str:
        raise _Failure("unsupported_endpoint")
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/?", value)
    if not match or int(match[1]) > 65535:
        raise _Failure("unsupported_endpoint")
    return value.rstrip("/")


def _read_binding():
    configured_url = os.environ.get("OPTIFORGE_SERVICE_URL")
    configured_file = os.environ.get("OPTIFORGE_SERVICE_FILE")
    url = _canonical_endpoint(configured_url)
    path = _canonical_path(configured_file)
    if tuple(path.parts[-3:]) != ("output", "workbench_service", "service.json"):
        raise _Failure("unsupported_path")
    workspace = path.parents[2]
    try:
        before = path.stat()
        with path.open("rb") as stream:
            raw = stream.read(65537)
        after = path.stat()
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if identity(before) != identity(after) or len(raw) > 65536:
            raise _Failure("configuration_changed")
        credential = _parse_json(raw.decode("utf-8"))
        if (not isinstance(credential, dict) or type(credential.get("protocol_version")) is not int
                or credential["protocol_version"] != 2 or credential.get("url") != url
                or type(credential.get("token")) is not str or not 0 < len(credential["token"]) <= 8192
                or type(credential.get("service_instance_id")) is not str
                or not _IDENTIFIER.fullmatch(credential["service_instance_id"])):
            raise _Failure("service_unavailable")
        return Binding(url, path, workspace, credential["service_instance_id"], credential["token"],
                       (*identity(after), hashlib.sha256(raw).hexdigest()), configured_url, configured_file)
    except (OSError, UnicodeError, KeyError, TypeError):
        raise _Failure("service_unavailable") from None


def _verify_binding(binding):
    try:
        client = WorkbenchControlClient.from_environment()
        if (client.url != binding.url or client.instance != binding.instance or client.token != binding.token
                or type(client.protocol) is not int or client.protocol != 2):
            raise _Failure("configuration_changed")
        session = client._verify_instance()
        if (not isinstance(session, dict) or type(session.get("protocol_version")) is not int
                or session["protocol_version"] != 2 or session.get("service_instance_id") != binding.instance
                or session.get("token") != binding.token):
            raise _Failure("service_unavailable")
    except _Failure:
        raise
    except Exception:
        # HTTP error payloads and existing error.code/message are untrusted text.
        raise _Failure("service_unavailable") from None


def _system_directory():
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        getter = kernel.GetSystemDirectoryW
        getter.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        getter.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(32768)
        length = getter(buffer, len(buffer))
        if not 0 < length < len(buffer):
            raise _Failure("collector_unavailable")
        return _canonical_path(buffer.value, directory=True)
    except _Failure:
        raise
    except Exception:
        raise _Failure("collector_unavailable") from None


def _collector_files():
    system = _system_directory()
    executable = _canonical_path(str(system / "WindowsPowerShell" / "v1.0" / "powershell.exe"))
    # Only this installed MCP source tree supplies code; the target tree supplies
    # credentials/profile location, never a script or executable to execute.
    trusted_root = Path(__file__).parent.parent
    script = _canonical_path(str(trusted_root / "get-workbench-window.ps1"))
    _canonical_path(str(trusted_root / "start-workbench.ps1"))
    return executable, script, system


def _collect(workspace, browser):
    executable, script, system = _collector_files()
    environment = dict(os.environ)
    for key in list(environment):
        if key.upper().startswith("OPTIFORGE_LAUNCHER_TEST_BROWSER") or key.upper() in {
                "PSMODULEPATH", "PSMODULEANALYSISCACHEPATH", "PSEXECUTIONPOLICYPREFERENCE"}:
            del environment[key]
    environment["SystemRoot"] = str(system.parent)
    environment["WINDIR"] = str(system.parent)
    arguments = [str(executable), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script), "-WorkspacePath", str(workspace), "-Browser", browser]
    child = None
    readers = []
    stopped = threading.Event()
    try:
        child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, env=environment,
            cwd=str(script.parent), creationflags=subprocess.CREATE_NO_WINDOW)
        # Process creation itself cannot be interrupted by Popen's timeout API.
        # This deadline bounds the already-created collector's pipe/read lifetime.
        deadline = time.monotonic() + COLLECTOR_SECONDS
        chunks = queue.Queue(maxsize=4)

        def put_chunk(item):
            while not stopped.is_set():
                try:
                    chunks.put(item, timeout=0.05)
                    return True
                except queue.Full:
                    continue
            return False

        def read_pipe(stream, channel):
            try:
                while not stopped.is_set():
                    block = stream.read(4096)
                    if not block:
                        break
                    if not put_chunk((channel, block)):
                        break
            except Exception:
                put_chunk(("error", None))
            finally:
                put_chunk((channel, None))

        for channel, stream in (("stdout", child.stdout), ("stderr", child.stderr)):
            reader = threading.Thread(target=read_pipe, args=(stream, channel), daemon=True)
            readers.append(reader)
            reader.start()
        output = bytearray()
        total = 0
        errors = False
        done = set()
        while len(done) < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Failure("collector_timeout")
            try:
                channel, block = chunks.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if channel == "error":
                raise _Failure("collector_failed")
            if block is None:
                done.add(channel)
                continue
            total += len(block)
            if total > OUTPUT_LIMIT:
                raise _Failure("collector_output_too_large")
            if channel == "stdout":
                output.extend(block)
            elif block.strip():
                errors = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _Failure("collector_timeout")
        try:
            code = child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise _Failure("collector_timeout") from None
        if code != 0 or errors:
            raise _Failure("collector_failed")
        return bytes(output)
    except _Failure:
        raise
    except Exception:
        raise _Failure("collector_unavailable") from None
    finally:
        stopped.set()
        if child is not None:
            try:
                if child.poll() is None:
                    child.kill()  # Only the process created above; never a browser/service.
                child.wait(timeout=1)
            except Exception:
                pass
            for reader in readers:
                reader.join(timeout=1)
            for stream in (child.stdout, child.stderr):
                if stream is not None and not any(reader.is_alive() for reader in readers):
                    stream.close()


def _integer(value, minimum=0, maximum=2**32 - 1):
    return type(value) is int and minimum <= value <= maximum


def _timestamp(value):
    if type(value) is not str or not _STAMP.fullmatch(value):
        raise _Failure("collector_result_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except ValueError:
        raise _Failure("collector_result_invalid") from None


def _sanitize(raw, binding, browser):
    if type(raw) is not bytes or len(raw) > OUTPUT_LIMIT:
        raise _Failure("collector_output_too_large" if type(raw) is bytes else "collector_result_invalid")
    try:
        payload = _parse_json(raw.decode("utf-8-sig"))
    except UnicodeError:
        raise _Failure("collector_result_invalid") from None
    if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1 or payload.get("url_binding") != "unverified"
            or payload.get("observation") != CLI_OBSERVATION
            or payload.get("action_supported", False) is not False or payload.get("selected") is not None):
        raise _Failure("collector_result_invalid")
    status, reason = payload.get("status"), payload.get("reason")
    if (type(status) is not str or type(reason) is not str
            or status not in {"unique_candidate", "ambiguous", "not_found", "unavailable"}):
        raise _Failure("collector_result_invalid")
    if (status == "unavailable" and reason not in CLI_FAILURES or
            status != "unavailable" and reason != "stable_profile_observation"):
        raise _Failure("collector_result_invalid")
    stamp = _timestamp(payload.get("observed_at"))
    service = payload.get("service")
    public_service = None
    if service is not None:
        if (not isinstance(service, dict) or service.get("endpoint") != binding.url + "/"
                or service.get("instance_id") != binding.instance or not _integer(service.get("listener_pid"), 1)):
            raise _Failure("collector_result_invalid")
        public_service = {"service_instance_id": binding.instance, "listener_pid": service["listener_pid"]}
    elif status != "unavailable":
        raise _Failure("collector_result_invalid")
    profiles = payload.get("profiles")
    rows = payload.get("candidates")
    if type(profiles) is not list or len(profiles) > 2 or type(rows) is not list or len(rows) > 128:
        raise _Failure("collector_result_invalid")
    allowed = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise _Failure("collector_result_invalid")
        name = profile.get("browser")
        if (type(name) is not str or name not in {"Chrome", "Edge"} or name in allowed or browser != "Auto" and name != browser
                or type(profile.get("profile_path")) is not str
                or ntpath.normcase(profile["profile_path"]) != ntpath.normcase(str(binding.workspace / "output" / "desktop_profile" / name.lower()))):
            raise _Failure("collector_result_invalid")
        allowed.add(name)
    if (status == "unique_candidate" and len(rows) != 1 or status == "ambiguous" and len(rows) < 2
            or status in {"not_found", "unavailable"} and rows or status != "unavailable" and not allowed):
        raise _Failure("collector_result_invalid")
    candidates, handles, processes = [], set(), {}
    for row in rows:
        if not isinstance(row, dict):
            raise _Failure("collector_result_invalid")
        hwnd = row.get("hwnd")
        bounds = row.get("bounds")
        if (type(hwnd) is not str or not re.fullmatch(r"[1-9][0-9]{0,19}", hwnd) or int(hwnd) > 2**64 - 1
                or hwnd in handles or not _integer(row.get("process_id"), 1)
                or type(row.get("browser")) is not str or row["browser"] not in allowed
                or row.get("visible") is not True or type(row.get("minimized")) is not bool
                or row.get("url_binding") != "unverified" or not isinstance(bounds, dict)
                or row.get("action_supported", False) is not False or row.get("selected") is not None
                or bounds.get("coordinates") != COORDINATES
                or any(not _integer(bounds.get(key), -(2**31), 2**31 - 1) for key in ("left", "top", "right", "bottom"))
                or bounds["right"] < bounds["left"] or bounds["bottom"] < bounds["top"]
                or row.get("dpi") is not None and not _integer(row["dpi"], 1)):
            raise _Failure("collector_result_invalid")
        handles.add(hwnd)
        created_at = _timestamp(row.get("process_created_at"))
        process_key = (created_at, row["browser"])
        if row["process_id"] in processes and processes[row["process_id"]] != process_key:
            raise _Failure("collector_result_invalid")
        processes[row["process_id"]] = process_key
        candidates.append({"hwnd": hwnd, "process_id": row["process_id"],
            "process_created_at": created_at, "browser": row["browser"],
            "visible": True, "minimized": row["minimized"], "bounds": {**{key: bounds[key] for key in ("left", "top", "right", "bottom")},
            "coordinates": COORDINATES}, "dpi": row.get("dpi"), "url_binding": "unverified"})
    return {"schema_version": 1, "status": status, "reason": reason, "url_binding": "unverified",
            "action_supported": False, "observation": "point_in_time_profile_candidates", "observed_at": stamp,
            "service": public_service, "candidates": candidates}


def desktop_diagnostics(service_instance_id, browser="Auto"):
    if (type(service_instance_id) is not str or not _IDENTIFIER.fullmatch(service_instance_id)
            or type(browser) is not str or browser not in BROWSERS):
        return _unavailable("invalid_arguments")
    if sys.platform != "win32":
        return _unavailable("unsupported_platform")
    try:
        binding = _read_binding()
        if binding.instance != service_instance_id:
            raise _Failure("instance_mismatch")
        _verify_binding(binding)
        if _read_binding() != binding:
            raise _Failure("configuration_changed")
        raw = _collect(binding.workspace, browser)
        if _read_binding() != binding:
            raise _Failure("configuration_changed")
        _verify_binding(binding)
        if _read_binding() != binding:
            raise _Failure("configuration_changed")
        return _sanitize(raw, binding, browser)
    except _Failure as error:
        return _unavailable(error.reason)
    except Exception:
        return _unavailable("diagnostic_unavailable")
