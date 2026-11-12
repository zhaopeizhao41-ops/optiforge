"""Bounded UI-session and navigation-command state for the local workbench.

The HTTP service owns this controller. MCP tools only *request* a navigation;
the browser ACKs after React has committed the page. A service implementation
can therefore never manufacture an ACK.
"""
from __future__ import annotations

import re
import secrets
import threading
import time

PAGES = ("workflow", "design", "analysis")
PAGE_SET = frozenset(PAGES)
SESSION_LEASE_MS = 10_000
COMMAND_TTL_MS = 8_000
MAX_SESSIONS = 32
MAX_COMMANDS = 64
_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class WorkbenchUIError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _valid_page(page):
    return isinstance(page, str) and page in PAGE_SET


def _valid_id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


# Optional deep-link target inside a page. Values are plain identifiers; the
# frontend checks them against its current model/result before focusing.
ANALYSIS_TABS = frozenset({"overview", "mtf", "wavefront", "spot", "ray_fan", "field_curvature"})
WORKFLOW_TABS = frozenset({"requirements", "proposal", "tasks", "milestones"})
STAGES = frozenset({"stage_1", "stage_2", "stage_3", "stage_4"})


def _index(value, minimum):
    return type(value) is int and minimum <= value <= 100_000


def valid_focus(page, focus):
    """None, or a page-specific focus object with only known keys and values."""
    if focus is None:
        return True
    if not isinstance(focus, dict) or not focus:
        return False
    if page == "design":
        return set(focus) == {"surface"} and _index(focus["surface"], 0)
    if page == "analysis":
        return (set(focus) <= {"tab", "field"} and "tab" in focus and focus["tab"] in ANALYSIS_TABS
                and ("field" not in focus or _index(focus["field"], 1)))
    if page == "workflow":
        return (set(focus) <= {"tab", "stage"} and "tab" in focus and focus["tab"] in WORKFLOW_TABS
                and ("stage" not in focus or focus["tab"] == "milestones" and focus["stage"] in STAGES))
    return False


class WorkbenchUIController:
    def __init__(self, clock=time.monotonic, wall_clock=time.time):
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.RLock()
        self._sessions = {}
        self._commands = {}
        self._request_index = {}
        self._command_order = []

    # -- internal ---------------------------------------------------------
    def _now(self):
        return self._clock()

    def _wall_ms(self):
        return int(self._wall_clock() * 1000)

    def _prune(self, now):
        for command in self._commands.values():
            if command["status"] == "accepted" and now >= command["expires_s"]:
                command["status"] = "expired"
        for session_id, session in list(self._sessions.items()):
            if now - session["last_seen"] > SESSION_LEASE_MS / 1000 * 6:
                self._sessions.pop(session_id, None)
        # Never evict an accepted command: one pending per session is bounded by
        # MAX_SESSIONS, so only terminal history is trimmed.
        while len(self._command_order) > MAX_COMMANDS:
            victim = None
            for index, command_id in enumerate(self._command_order):
                command = self._commands.get(command_id)
                if command and command["status"] != "accepted":
                    victim = index
                    break
            if victim is None:
                break
            old = self._command_order.pop(victim)
            command = self._commands.pop(old, None)
            if command:
                self._request_index.pop((command["ui_session_id"], command["request_id"]), None)

    def _session(self, ui_session_id):
        session = self._sessions.get(ui_session_id)
        if not session:
            raise WorkbenchUIError("UI_SESSION_UNKNOWN", "前端会话不存在或已过期")
        if self._now() - session["last_seen"] > SESSION_LEASE_MS / 1000:
            raise WorkbenchUIError("UI_SESSION_OFFLINE", "前端会话已离线")
        return session

    def _pending(self, ui_session_id, now):
        for command_id in reversed(self._command_order):
            item = self._commands.get(command_id)
            if item and item["ui_session_id"] == ui_session_id and item["status"] == "accepted":
                if now >= item["expires_s"]:
                    item["status"] = "expired"
                    return None
                return self._public_command(item)
        return None

    def _public_command(self, command):
        return {
            "command_id": command["command_id"],
            "request_id": command["request_id"],
            "ui_session_id": command["ui_session_id"],
            "page": command["page"],
            "status": command["status"],
            "created_at_ms": command["created_at_ms"],
            "expires_at_ms": command["expires_at_ms"],
            "remaining_ms": max(0, int((command["expires_s"] - self._now()) * 1000)),
            "actual_page": command["actual_page"],
            "focus": command["focus"],
            "actual_focus": command["actual_focus"],
            "reason": command["reason"],
        }

    # -- frontend-facing --------------------------------------------------
    def register(self, ui_session_id, page):
        if not _valid_id(ui_session_id):
            raise WorkbenchUIError("INVALID_UI_SESSION_ID", "ui_session_id 格式无效")
        if not _valid_page(page):
            raise WorkbenchUIError("UNKNOWN_PAGE", "页面必须是 workflow/design/analysis")
        with self._lock:
            now = self._now()
            self._prune(now)
            session = self._sessions.get(ui_session_id)
            if session is None:
                if len(self._sessions) >= MAX_SESSIONS:
                    raise WorkbenchUIError("UI_SESSIONS_FULL", "在线前端会话已达上限，请先关闭空闲页面")
                session = {"ui_session_id": ui_session_id, "created_at_ms": self._wall_ms()}
                self._sessions[ui_session_id] = session
            session["page"] = page
            session["last_seen"] = now
            return {
                "protocol_version": 2,
                "ui_session_id": ui_session_id,
                "page": page,
                "online": True,
                "lease_ms": SESSION_LEASE_MS,
            }

    def heartbeat(self, ui_session_id, page):
        self.register(ui_session_id, page)
        with self._lock:
            return {
                "protocol_version": 2,
                "ui_session_id": ui_session_id,
                "page": page,
                "command": self._pending(ui_session_id, self._now()),
            }

    def ack(self, ui_session_id, command_id, status, actual_page, reason=None, actual_focus=None):
        if not _valid_id(ui_session_id):
            raise WorkbenchUIError("INVALID_UI_SESSION_ID", "ui_session_id 格式无效")
        if not isinstance(command_id, str) or not command_id:
            raise WorkbenchUIError("INVALID_COMMAND_ID", "command_id 无效")
        if not isinstance(status, str) or status not in {"applied", "rejected"}:
            raise WorkbenchUIError("INVALID_ACK_STATUS", "ACK 状态必须是 applied/rejected")
        if not _valid_page(actual_page):
            raise WorkbenchUIError("UNKNOWN_PAGE", "actual_page 必须是 workflow/design/analysis")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
            raise WorkbenchUIError("INVALID_ACK_REASON", "reason 必须是不超过 500 字的文本")
        if not valid_focus(actual_page, actual_focus):
            raise WorkbenchUIError("INVALID_FOCUS", "actual_focus 与页面不匹配或格式无效")
        with self._lock:
            now = self._now()
            self._prune(now)
            command = self._commands.get(command_id)
            if not command or command["ui_session_id"] != ui_session_id:
                raise WorkbenchUIError("COMMAND_UNKNOWN", "命令不存在或不属于该前端会话")
            if command["status"] in {"applied", "rejected"}:
                same = command["status"] == status and (status == "rejected" or
                                                        command["actual_page"] == actual_page
                                                        and command["actual_focus"] == actual_focus)
                if same:
                    return {"protocol_version": 2, "idempotent": True,
                            "receipt": self._public_command(command)}
                raise WorkbenchUIError("COMMAND_ALREADY_SETTLED", "命令已有不同的最终状态")
            if command["status"] == "expired" or now >= command["expires_s"]:
                command["status"] = "expired"
                raise WorkbenchUIError("COMMAND_EXPIRED", "命令已过期，不会补执行")
            if status == "applied" and actual_page != command["page"]:
                raise WorkbenchUIError("ACTUAL_PAGE_MISMATCH", "实际页面与命令页面不一致")
            if status == "applied":
                requested = command["focus"] or {}
                # The browser may only report a subset of what was requested, never
                # a different target; a partial focus must say why.
                if actual_focus is not None and any(requested.get(key) != value for key, value in actual_focus.items()):
                    raise WorkbenchUIError("FOCUS_MISMATCH", "实际定位与命令请求不一致")
                if (actual_focus or {}) != requested and not reason:
                    raise WorkbenchUIError("FOCUS_MISMATCH", "定位未完整执行时必须说明原因")
            elif actual_focus is not None:
                raise WorkbenchUIError("INVALID_FOCUS", "被拒绝的命令不能报告定位")
            command["status"] = status
            command["actual_page"] = actual_page
            command["actual_focus"] = actual_focus
            command["reason"] = reason
            command["settled_s"] = now
            session = self._sessions.get(ui_session_id)
            if session:
                session["page"] = actual_page
                session["last_seen"] = now
            return {"protocol_version": 2, "idempotent": False,
                    "receipt": self._public_command(command)}

    # -- MCP / operator-facing -------------------------------------------
    def navigate(self, ui_session_id, page, request_id, focus=None):
        if not _valid_id(ui_session_id):
            raise WorkbenchUIError("INVALID_UI_SESSION_ID", "ui_session_id 格式无效")
        if not _valid_id(request_id):
            raise WorkbenchUIError("INVALID_REQUEST_ID", "request_id 格式无效")
        if not _valid_page(page):
            raise WorkbenchUIError("UNKNOWN_PAGE", "page 必须是 workflow/design/analysis")
        if not valid_focus(page, focus):
            raise WorkbenchUIError("INVALID_FOCUS", "focus 与页面不匹配或格式无效")
        with self._lock:
            now = self._now()
            self._prune(now)
            session = self._session(ui_session_id)
            key = (ui_session_id, request_id)
            existing = self._request_index.get(key)
            if existing:
                command = self._commands.get(existing)
                if command is None:
                    raise WorkbenchUIError("COMMAND_UNKNOWN", "命令历史已淘汰，请使用新的 request_id")
                if command["page"] != page or command["focus"] != focus:
                    raise WorkbenchUIError("REQUEST_ID_CONFLICT", "同一 request_id 已用于其他页面或定位")
                if command["status"] == "accepted" and now >= command["expires_s"]:
                    command["status"] = "expired"
                return {"protocol_version": 2, "status": command["status"],
                        "command_id": existing, "page": command["page"],
                        "idempotent": True, "receipt": self._public_command(command)}
            if self._pending(ui_session_id, now):
                raise WorkbenchUIError("COMMAND_PENDING", "该前端会话已有待执行命令")
            command_id = "ui_" + secrets.token_hex(8)
            created_at_ms = self._wall_ms()
            command = {
                "command_id": command_id,
                "request_id": request_id,
                "ui_session_id": ui_session_id,
                "page": page,
                "status": "accepted",
                "created_s": now,
                "expires_s": now + COMMAND_TTL_MS / 1000,
                "created_at_ms": created_at_ms,
                "expires_at_ms": created_at_ms + COMMAND_TTL_MS,
                "actual_page": None,
                "focus": dict(focus) if focus else None,
                "actual_focus": None,
                "reason": None,
            }
            self._commands[command_id] = command
            self._request_index[key] = command_id
            self._command_order.append(command_id)
            self._prune(now)
            return {"protocol_version": 2, "status": "accepted", "command_id": command_id,
                    "page": page, "focus": command["focus"], "idempotent": False,
                    "expires_at_ms": command["expires_at_ms"],
                    "remaining_ms": COMMAND_TTL_MS, "session_page": session["page"]}

    def follow(self, page, focus=None):
        """Ask every online frontend to show page/focus after an Agent operation.

        Uses the ordinary navigation path (bounded TTL, browser ACK). The request_id
        carries the auto prefix so a frontend with "follow the Agent" off rejects it.
        A session already on the page with no focus, or with a pending command, is
        skipped. Returns the accepted command ids.
        """
        from core.workbench_follow import AUTO_PREFIX
        if not _valid_page(page) or not valid_focus(page, focus):
            return []
        with self._lock:
            now = self._now()
            self._prune(now)
            targets = [sid for sid, session in self._sessions.items()
                       if now - session["last_seen"] <= SESSION_LEASE_MS / 1000
                       and not (session["page"] == page and focus is None)]
        accepted = []
        for ui_session_id in targets:
            try:
                result = self.navigate(ui_session_id, page, AUTO_PREFIX + secrets.token_hex(8), focus)
                accepted.append(result["command_id"])
            except WorkbenchUIError:
                continue
        return accepted

    def status(self):
        with self._lock:
            now = self._now()
            self._prune(now)
            frontends = []
            for session in sorted(self._sessions.values(), key=lambda item: item["last_seen"], reverse=True):
                frontends.append({
                    "ui_session_id": session["ui_session_id"],
                    "page": session["page"],
                    "online": now - session["last_seen"] <= SESSION_LEASE_MS / 1000,
                    "last_seen_ms_ago": int((now - session["last_seen"]) * 1000),
                })
            commands = [self._public_command(self._commands[item])
                        for item in self._command_order if item in self._commands]
            return {
                "frontends": frontends,
                "commands": commands,
                "pending": [item for item in commands if item["status"] == "accepted"],
                "pages": list(PAGES),
                "session_lease_ms": SESSION_LEASE_MS,
                "command_ttl_ms": COMMAND_TTL_MS,
            }
