"""Read older terminal workflow tasks without modifying workflow persistence.

Each page has a short SQLite read transaction. The cursor fixes an insertion
boundary, not a snapshot of terminal membership across separate requests.
"""

import base64
import binascii
import json
import math
import os
from pathlib import Path
import re
import sqlite3

from core import design_workflow as flow


MAX_LIMIT = 50
MAX_RESPONSE_BYTES = 1048576
MAX_ROWID = 9223372036854775807
TERMINAL_STATUSES = frozenset({"completed", "failed", "superseded"})
TASK_KINDS = frozenset({
    "initial_proposal", "proposal_revision", "surface_question", "model_handoff",
})
_PRIVATE_KEYS = frozenset({"token", "lease_token", "token_hash", "milestone_attempts"})
_UNAVAILABLE = "任务历史存储不可用或数据不兼容，请核对后重新读取"
_CURSOR_INVALID = "历史游标无效或来源已变化，请重新读取历史"


def _require(ok, code="WORKFLOW_HISTORY_UNAVAILABLE", message=_UNAVAILABLE):
    flow.require(ok, code, message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON value")


def _json_loads(value):
    return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _json_bytes(value):
    # Match the existing server wrapper's JSON formatting, including its spaces.
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def trusted_source(observation):
    """Extract only the trusted engine identity; never return its operations."""
    context = observation.get("context") if isinstance(observation, dict) else None
    instance = observation.get("service_instance_id") if isinstance(observation, dict) else None
    _require(
        isinstance(context, dict) and isinstance(instance, str) and bool(instance)
        and context.get("service_instance_id") == instance
        and isinstance(context.get("active_project"), str) and bool(context["active_project"]),
        "WORKFLOW_HISTORY_SOURCE_REQUIRED", "历史读取需要当前工作台的可信服务与工程来源",
    )
    return {"service_instance_id": instance, "project_name": context["active_project"]}


def _options(project, history, source):
    from tools.project_manager import get_active_project_name

    _require(
        isinstance(history, dict) and set(history) <= {"source", "limit", "cursor"},
        "WORKFLOW_INPUT_INVALID", "history 只接受 source、limit 与 cursor",
    )
    supplied = history.get("source")
    _require(
        isinstance(supplied, dict) and set(supplied) == {"service_instance_id", "project_name"}
        and all(isinstance(value, str) and value for value in supplied.values()),
        "WORKFLOW_HISTORY_SOURCE_REQUIRED", "历史读取需要明确的服务与工程来源",
    )
    _require(
        supplied["service_instance_id"] == source["service_instance_id"],
        "WORKBENCH_INSTANCE_MISMATCH", "服务实例已变化，请重新读取",
    )
    _require(
        project == supplied["project_name"] == source["project_name"] == get_active_project_name(),
        "WORKFLOW_PROJECT_MISMATCH", "活动工程已变化，请重新读取",
    )
    limit = history.get("limit", 20)
    _require(type(limit) is int and 1 <= limit <= MAX_LIMIT,
             "WORKFLOW_INPUT_INVALID", "history.limit 必须是 1 到 50 的整数")
    cursor = history.get("cursor")
    if cursor is None:
        return limit, None
    try:
        if (not isinstance(cursor, str) or not 0 < len(cursor) <= 1024
                or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None):
            raise ValueError("invalid base64url")
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != cursor:
            raise ValueError("noncanonical base64url")
        position = _json_loads(raw.decode("utf-8"))
        if (not isinstance(position, dict)
                or set(position) != {"v", "project_name", "service_instance_id", "upper", "before"}
                or type(position["v"]) is not int or position["v"] != 1
                or position["project_name"] != project
                or position["service_instance_id"] != source["service_instance_id"]):
            raise ValueError("invalid cursor scope")
        for key in ("upper", "before"):
            value = position[key]
            if (not isinstance(value, str) or len(value) > 19
                    or re.fullmatch(r"[1-9][0-9]*", value) is None
                    or int(value) > MAX_ROWID):
                raise ValueError("invalid rowid")
        if int(position["before"]) > int(position["upper"]):
            raise ValueError("invalid cursor order")
        return limit, position
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        raise flow.WorkflowError("WORKFLOW_HISTORY_CURSOR_INVALID", _CURSOR_INVALID) from None


def _path(project):
    from tools.project_manager import OUTPUT_BASE_DIR, _within

    root = Path(OUTPUT_BASE_DIR).absolute()
    path = root / ".design-workflow.sqlite3"
    project_path = root / project
    # Resolving any link above the configured output root must not relocate it.
    _require(
        os.path.normcase(str(root.resolve())) == os.path.normcase(str(root))
        and not path.is_symlink() and not project_path.is_symlink()
        and not getattr(path, "is_junction", lambda: False)()
        and not getattr(project_path, "is_junction", lambda: False)()
        and _within(str(path), str(root)) and _within(str(project_path), str(root)),
        "WORKFLOW_PATH_INVALID", "任务历史位置超出工作区或使用符号链接",
    )
    return path


def _schema(db):
    expected = {
        "state": [("project", "TEXT", 0, 1), ("data", "TEXT", 1, 0)],
        "tasks": [("id", "TEXT", 0, 1), ("project", "TEXT", 1, 0), ("data", "TEXT", 1, 0)],
        "meta": [("id", "INTEGER", 0, 1), ("selection", "INTEGER", 1, 0)],
    }
    for name, columns in expected.items():
        kind = db.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
        _require(kind is not None and kind[0] == "table")
        actual = [(row[1], row[2].upper(), row[3], row[5])
                  for row in db.execute(f"PRAGMA table_info({name})")]
        _require(actual == columns)
    db.execute("SELECT rowid FROM tasks LIMIT 0")


def _counter(value, minimum=0):
    return type(value) is int and value >= minimum


def _text(value, limit, required=False):
    return isinstance(value, str) and len(value) <= limit and (not required or bool(value.strip()))


def _number(value):
    return type(value) in (int, float) and value >= 0 and math.isfinite(value)


def _task(row, project, draft, proposal, model):
    value = _json_loads(row["data"])
    _require(
        type(row["rowid"]) is int and 1 <= row["rowid"] <= MAX_ROWID
        and isinstance(value, dict) and value.get("project_name") == project
        and value.get("request_id") == row["id"] and _text(value.get("request_id"), 100, True)
        and isinstance(value.get("kind"), str) and value["kind"] in TASK_KINDS
        and isinstance(value.get("status"), str) and value["status"] in TERMINAL_STATUSES
        and _text(value.get("user_text"), 16000, True)
        and _counter(value.get("version"), 1) and _counter(value.get("requirements_revision"))
        and isinstance(value.get("requirements_snapshot"), dict)
        and _counter(value["requirements_snapshot"].get("revision"))
        and _text(value.get("progress"), 16000) and _text(value.get("result_text"), 16000)
        and _number(value.get("created_at")),
    )
    for key in ("proposal_hash", "result_proposal_hash", "worker_id"):
        _require(key in value and (value[key] is None or isinstance(value[key], str)))
    for key in ("model_reference", "target"):
        _require(key in value and (value[key] is None or isinstance(value[key], dict)))
    _require("lease_until" in value and (value["lease_until"] is None or _number(value["lease_until"])))
    if "finished_at" in value:
        _require(_number(value["finished_at"]))
    if "invalidated_reason" in value:
        _require(isinstance(value["invalidated_reason"], str))
    for key in ("stale", "lease_expired"):
        if key in value:
            _require(type(value[key]) is bool)
    public = flow.public_task(value, draft, proposal, model)
    _require(type(public.get("stale")) is bool and type(public.get("lease_expired")) is bool)
    return _safe(public)


def _safe(value):
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items() if key.lower() not in _PRIVATE_KEYS}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def _response(source, tasks=None, cursor=None):
    return {
        "status": "success", "task_history_schema_version": 1, "storage": "persistent",
        "source": dict(source), "range": "older_than_current_100_at_first_read",
        "tasks": tasks if tasks is not None else [], "has_more": cursor is not None,
        "next_cursor": cursor,
        "limits": {"max_limit": MAX_LIMIT, "max_response_bytes": MAX_RESPONSE_BYTES},
    }


def _cursor(source, upper, before):
    value = {"v": 1, **source, "upper": str(upper), "before": str(before)}
    return base64.urlsafe_b64encode(_json_bytes(value)).decode("ascii").rstrip("=")


def _page(db, project, source, limit, position, model):
    _schema(db)
    stored_draft = db.execute("SELECT data FROM state WHERE project=?", (project,)).fetchone()
    draft = _json_loads(stored_draft[0]) if stored_draft else flow._draft(db, project)
    _require(isinstance(draft, dict) and _counter(draft.get("revision")))
    proposal = flow._proposal(project)
    _require(proposal is None or isinstance(proposal, dict))
    if position is None:
        boundary = db.execute(
            "SELECT rowid FROM tasks WHERE project=? ORDER BY rowid DESC LIMIT 1 OFFSET 100",
            (project,),
        ).fetchone()
        if boundary is None:
            return _response(source)
        upper, before = boundary[0], MAX_ROWID
        _require(type(upper) is int and 1 <= upper <= MAX_ROWID)
        before_clause, arguments = "", (project, upper, limit + 1)
    else:
        upper, before = int(position["upper"]), int(position["before"])
        before_clause, arguments = " AND rowid<?", (project, upper, before, limit + 1)
    # A malformed/non-object row or impossible status is storage corruption, not
    # a non-terminal member to silently hide. CASE avoids evaluating invalid JSON.
    broken = db.execute(
        "SELECT 1 FROM tasks WHERE project=? AND rowid<=?" + before_clause + " AND CASE "
        "WHEN json_valid(data)=0 THEN 1 WHEN json_type(data)!='object' THEN 1 "
        "WHEN json_type(data,'$.status') IS NOT 'text' THEN 1 "
        "ELSE json_extract(data,'$.status') NOT IN "
        "('pending','claimed','completed','failed','superseded') END LIMIT 1",
        arguments[:-1],
    ).fetchone()
    _require(broken is None)
    rows = db.execute(
        "SELECT rowid,id,data FROM tasks WHERE project=? AND rowid<=?" + before_clause
        + " AND json_extract(data,'$.status') IN ('completed','failed','superseded') "
        "ORDER BY rowid DESC LIMIT ?", arguments,
    ).fetchall()
    tasks = [_task(row, project, draft, proposal, model) for row in rows]
    _require(len({task["request_id"] for task in tasks}) == len(tasks))
    count = min(limit, len(tasks))
    while True:
        next_cursor = _cursor(source, upper, rows[count - 1]["rowid"]) if len(tasks) > count else None
        result = _response(source, tasks[:count], next_cursor)
        if len(_json_bytes(result)) <= MAX_RESPONSE_BYTES:
            return result
        _require(count > 1, "WORKFLOW_TOO_LARGE", "单条任务历史超过完整响应读取预算")
        count -= 1


def list_history(project, history, source, model=None):
    """Use only engine-derived source; model is optional stale-projection context."""
    flow.project_name(project)
    limit, position = _options(project, history, source)
    try:
        path = _path(project)
        if not path.exists():
            return _response(source)
        _require(path.is_file())
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            return _page(db, project, source, limit, position, model)
        finally:
            # No COMMIT is needed: this connection only holds a read transaction.
            db.close()
    except flow.WorkflowError:
        raise
    except Exception:
        raise flow.WorkflowError("WORKFLOW_HISTORY_UNAVAILABLE", _UNAVAILABLE) from None
