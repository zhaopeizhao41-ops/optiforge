"""Immutable stage geometry from existing trusted cache; no native execution."""
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import uuid

from core import design_workflow as flow


GEOMETRY_READER = ContextVar("milestone_geometry_reader", default=None)
MAX_ARTIFACT_BYTES = 8 * 1024 ** 2
MAX_PROJECT_BYTES = 256 * 1024 ** 2
MAX_WORKSPACE_BYTES = 512 * 1024 ** 2
MAX_PROJECT_ARTIFACTS = 64
MAX_WORKSPACE_ARTIFACTS = 256
TABLE = "milestone_geometry_artifacts"
OPTICAL_KEYS = ("service_instance_id", "model_id", "model_revision", "active_project",
                "model_project", "configuration", "source_state_version")
SEMANTIC_KEYS = ("project_name", "requirements_revision", "proposal_hash",
                 "proposal_revision", "authorization_revision")
METADATA_KEYS = frozenset({"id", "kind", "schema_version", "project_name", "task_id", "attempt_id",
                           "stage_id", "operation_id", "created_at", "sha256", "bytes", "source"})
_PRIVATE = frozenset({"token", "lease_token", "token_hash", "milestone_attempts"})
_MESSAGES = {
    "MILESTONE_ARTIFACT_SOURCE_REQUIRED": "阶段几何需要当前工作台的可信来源",
    "MILESTONE_ARTIFACT_NOT_FOUND": "阶段几何不存在、未归档或缓存已淘汰",
    "MILESTONE_ARTIFACT_INVALID": "阶段几何数据、来源或引用不一致",
    "MILESTONE_ARTIFACT_UNAVAILABLE": "阶段几何存储不可用或数据不兼容",
    "MILESTONE_ARTIFACT_TOO_LARGE": "阶段几何超过单条归档预算",
    "MILESTONE_ARTIFACT_CAPACITY": "阶段几何归档已达到工程或工作区容量上限",
    "MILESTONE_ARTIFACT_CONFLICT": "该阶段已有不同的几何归档，不能覆盖",
}


def fail(code):
    raise flow.WorkflowError(code, _MESSAGES[code])


def require(ok, code="MILESTONE_ARTIFACT_INVALID"):
    if not ok:
        fail(code)


def limits():
    return {"max_artifact_bytes": MAX_ARTIFACT_BYTES, "max_project_bytes": MAX_PROJECT_BYTES,
            "max_workspace_bytes": MAX_WORKSPACE_BYTES, "max_project_artifacts": MAX_PROJECT_ARTIFACTS,
            "max_workspace_artifacts": MAX_WORKSPACE_ARTIFACTS}


def _text(value, maximum=100):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _counter(value, minimum=0):
    return type(value) is int and minimum <= value <= 9007199254740991


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _points(value, minimum=2):
    return (isinstance(value, list) and len(value) >= minimum
            and all(isinstance(point, list) and len(point) == 2 and all(_finite(v) for v in point)
                    for point in value))


def _optical_number(value):
    return _finite(value) or isinstance(value, str) and value in {"Infinity", "-Infinity"}


def _layout(layout, indices, units):
    require(isinstance(layout, dict) and layout.get("status") in {"ready", "unsupported"}
            and layout.get("plane") == "YZ" and layout.get("units") == units
            and isinstance(layout.get("surfaces"), list) and isinstance(layout.get("elements"), list)
            and isinstance(layout.get("issues"), list)
            and isinstance(layout.get("rays"), list) and not layout["rays"])
    for key in ("issues", "validation_issues"):
        require(key not in layout or isinstance(layout[key], list)
                and all(isinstance(issue, str) for issue in layout[key]))
    for key in ("surfaces", "elements"):
        require(key not in layout or isinstance(layout[key], list))
    if layout["status"] != "ready":
        require(not layout["surfaces"] and not layout["elements"] and bool(layout["issues"]))
        return
    require(bool(layout["surfaces"]))
    seen = set()
    for surface in layout["surfaces"]:
        require(isinstance(surface, dict) and _counter(surface.get("index"))
                and surface["index"] in indices and surface["index"] not in seen
                and _finite(surface.get("vertex_z")) and type(surface.get("is_image")) is bool
                and type(surface.get("is_stop")) is bool and _points(surface.get("points")))
        seen.add(surface["index"])
    fronts = set()
    for element in layout["elements"]:
        require(isinstance(element, dict) and _counter(element.get("front")) and _counter(element.get("back"))
                and element["front"] in seen and element["back"] in seen and element["front"] not in fronts
                and isinstance(element.get("material"), str) and _points(element.get("points"), 3))
        fronts.add(element["front"])


def _timestamp(value):
    if not _text(value) or re.search(r"T.*(?:Z|[+-][0-9]{2}:[0-9]{2})$", value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (ValueError, TypeError, OverflowError):
        return False


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _constant(_value):
    raise ValueError("non-finite JSON")


def strict_json(value):
    return json.loads(value, object_pairs_hook=_object, parse_constant=_constant)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _safe(value):
    if isinstance(value, dict):
        require(all(isinstance(key, str) for key in value))
        return {key: _safe(item) for key, item in value.items()
                if key.lower() not in _PRIVATE}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return deepcopy(value)


def _json_shape(value):
    if isinstance(value, dict):
        return all(isinstance(key, str) and key.lower() not in _PRIVATE and _json_shape(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return all(_json_shape(item) for item in value)
    return value is None or type(value) in (str, bool, int) or type(value) is float and math.isfinite(value)


def validate_optical(source):
    require(isinstance(source, dict) and set(source) == set(OPTICAL_KEYS))
    require(all(_text(source[key], 512) for key in ("service_instance_id", "model_id")))
    require(_text(source["active_project"], 80) and source["active_project"] == source["model_project"])
    require(_counter(source["model_revision"], 1) and _counter(source["configuration"], 1)
            and _counter(source["source_state_version"]))
    return source


def _validate_source(source, project):
    require(isinstance(source, dict) and set(source) == set(OPTICAL_KEYS + SEMANTIC_KEYS))
    validate_optical({key: source[key] for key in OPTICAL_KEYS})
    require(source["project_name"] == source["active_project"] == project
            and _counter(source["requirements_revision"], 1) and _counter(source["authorization_revision"])
            and _counter(source["proposal_revision"], 1)
            and isinstance(source["proposal_hash"], str)
            and re.fullmatch(r"[0-9a-f]{64}", source["proposal_hash"]) is not None)


def validate_payload(payload, expected_optical=None, operation_id=None):
    require(isinstance(payload, dict) and set(payload) == {"entry", "snapshot"} and _json_shape(payload))
    entry, snapshot = payload["entry"], payload["snapshot"]
    require(isinstance(entry, dict)
            and set(entry) == {"id", "recorded_at", "operation_id", "tool", "label", "source"})
    require(_text(entry["id"]) and _timestamp(entry["recorded_at"])
            and _text(entry["operation_id"]) and _text(entry["tool"]) and _text(entry["label"], 256))
    source = validate_optical(entry["source"])
    require(expected_optical is None or source == expected_optical)
    require(operation_id is None or entry["operation_id"] == operation_id)
    require(isinstance(snapshot, dict) and snapshot.get("status") == "success"
            and type(snapshot.get("schema_version")) is int and snapshot["schema_version"] == 1)
    expected_sync = {key: source[key] for key in OPTICAL_KEYS
                     if key not in {"configuration", "source_state_version"}}
    expected_sync["state_version"] = source["source_state_version"]
    sync = snapshot.get("sync")
    require(isinstance(sync, dict) and sync == expected_sync
            and _counter(sync.get("state_version")) and _counter(sync.get("model_revision"), 1))
    model = snapshot.get("model")
    require(isinstance(model, dict) and model.get("project") == source["model_project"]
            and type(model.get("configuration")) is int and model["configuration"] == source["configuration"]
            and _text(model.get("units"), 32) and isinstance(model.get("surfaces"), list) and bool(model["surfaces"])
            and (model.get("name") is None or isinstance(model["name"], str)))
    indices = [surface.get("index") if isinstance(surface, dict) else None for surface in model["surfaces"]]
    require(all(_counter(index) for index in indices) and len(set(indices)) == len(indices))
    require(all(_optical_number(surface.get("radius")) and _optical_number(surface.get("thickness"))
                and isinstance(surface.get("material"), str) for surface in model["surfaces"]))
    layouts = [snapshot[key] for key in ("layout", "display_layout") if key in snapshot]
    require(bool(layouts))
    for layout in layouts:
        _layout(layout, set(indices), model["units"])
    return payload


def _metadata(value, project):
    require(isinstance(value, dict) and set(value) == METADATA_KEYS and _json_shape(value))
    require(isinstance(value["id"], str) and re.fullmatch(r"[0-9a-f]{32}", value["id"]) is not None
            and value["kind"] == "geometry" and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["project_name"] == project)
    require(all(_text(value[key]) for key in ("task_id", "attempt_id", "operation_id"))
            and value["stage_id"] in {"stage_1", "stage_2", "stage_3", "stage_4"}
            and _timestamp(value["created_at"]) and type(value["bytes"]) is int
            and 0 < value["bytes"] <= MAX_ARTIFACT_BYTES
            and isinstance(value["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)
    _validate_source(value["source"], project)
    return value


def check_path(project):
    from tools.project_manager import OUTPUT_BASE_DIR, _within
    flow.project_name(project)
    root = Path(OUTPUT_BASE_DIR).absolute()
    path, directory = root / ".design-workflow.sqlite3", root / project
    flow.require(os.path.normcase(str(root.resolve())) == os.path.normcase(str(root))
                 and not path.is_symlink() and not directory.is_symlink()
                 and not getattr(path, "is_junction", lambda: False)()
                 and not getattr(directory, "is_junction", lambda: False)()
                 and _within(str(path), str(root)) and _within(str(directory), str(root)),
                 "WORKFLOW_PATH_INVALID", "阶段几何位置超出工作区或使用符号链接")
    return path


_COLUMNS = [("id", "TEXT", 0, 1), ("project", "TEXT", 1, 0), ("task_id", "TEXT", 1, 0),
            ("attempt_id", "TEXT", 1, 0), ("metadata_json", "TEXT", 1, 0),
            ("payload_json", "TEXT", 1, 0), ("bytes", "INTEGER", 1, 0)]


def _schema(db, create=False):
    row = db.execute("SELECT type FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()
    if row is None:
        if not create:
            fail("MILESTONE_ARTIFACT_NOT_FOUND")
        db.execute(f"CREATE TABLE {TABLE} (id TEXT PRIMARY KEY, project TEXT NOT NULL, task_id TEXT NOT NULL, "
                   "attempt_id TEXT NOT NULL, metadata_json TEXT NOT NULL, payload_json TEXT NOT NULL, "
                   "bytes INTEGER NOT NULL, UNIQUE(project,task_id,attempt_id))")
    else:
        require(row[0] == "table", "MILESTONE_ARTIFACT_UNAVAILABLE")
    actual = [(row[1], row[2].upper(), row[3], row[5]) for row in db.execute(f"PRAGMA table_info({TABLE})")]
    require(actual == _COLUMNS, "MILESTONE_ARTIFACT_UNAVAILABLE")
    unique_bindings = []
    for row in db.execute(f"PRAGMA index_list({TABLE})"):
        if row[2] and not row[4]:
            # Table-controlled identifier; never use a client-provided SQL name.
            escaped = row[1].replace('"', '""')
            unique_bindings.append([item[2] for item in db.execute(f'PRAGMA index_info("{escaped}")')])
    require(["project", "task_id", "attempt_id"] in unique_bindings, "MILESTONE_ARTIFACT_UNAVAILABLE")


def _record(row, project):
    require(type(row["metadata_json"]) is str and len(row["metadata_json"].encode("utf-8")) <= 16384)
    metadata = _metadata(strict_json(row["metadata_json"]), project)
    require(row["id"] == metadata["id"] and row["project"] == project
            and row["task_id"] == metadata["task_id"] and row["attempt_id"] == metadata["attempt_id"]
            and type(row["bytes"]) is int and row["bytes"] == metadata["bytes"])
    payload_json = row["payload_json"]
    require(isinstance(payload_json, str))
    raw = payload_json.encode("utf-8")
    require(len(raw) == metadata["bytes"] and hashlib.sha256(raw).hexdigest() == metadata["sha256"])
    optical = {key: metadata["source"][key] for key in OPTICAL_KEYS}
    validate_payload(strict_json(payload_json), optical, metadata["operation_id"])
    return metadata, payload_json


def _load_record(db, project, *, artifact_id=None, task_id=None, attempt_id=None):
    where = "id=? AND project=?" if artifact_id is not None else "project=? AND task_id=? AND attempt_id=?"
    values = (artifact_id, project) if artifact_id is not None else (project, task_id, attempt_id)
    size = db.execute(f"SELECT length(CAST(metadata_json AS BLOB)),length(CAST(payload_json AS BLOB)) "
                      f"FROM {TABLE} WHERE {where}", values).fetchone()
    if size is None:
        return None
    require(type(size[0]) is int and 0 < size[0] <= 16384)
    require(type(size[1]) is int and 0 < size[1] <= MAX_ARTIFACT_BYTES, "MILESTONE_ARTIFACT_TOO_LARGE")
    row = db.execute(f"SELECT * FROM {TABLE} WHERE {where}", values).fetchone()
    return _record(row, project)


SHARED_BUDGET_TABLES = (TABLE, "milestone_quality_artifacts")


def shared_bytes(db, project):
    """Stored payload bytes across all stage artifact kinds (workspace, project).

    Measures payload_json itself rather than trusting the bytes column.
    """
    workspace = project_total = 0
    for table in SHARED_BUDGET_TABLES:
        row = db.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
        if row is None:
            continue
        require(row[0] == "table", "MILESTONE_ARTIFACT_UNAVAILABLE")
        totals = db.execute(f"SELECT COALESCE(SUM(length(CAST(payload_json AS BLOB))),0), "
                            f"COALESCE(SUM(CASE WHEN project=? THEN length(CAST(payload_json AS BLOB)) ELSE 0 END),0) "
                            f"FROM {table}", (project,)).fetchone()
        require(type(totals[0]) is int and type(totals[1]) is int, "MILESTONE_ARTIFACT_UNAVAILABLE")
        workspace += totals[0]
        project_total += totals[1]
    return workspace, project_total


def archive(db, project, task, attempt, operation, end_source):
    """Insert with the caller's workflow transaction; never commit or overwrite."""
    reader = GEOMETRY_READER.get()
    require(callable(reader), "MILESTONE_ARTIFACT_SOURCE_REQUIRED")
    optical = validate_optical(operation["observed_source"])
    require(isinstance(end_source, dict) and all(end_source.get(key) == optical[key]
            for key in OPTICAL_KEYS if key != "source_state_version"))
    payload = _safe(reader(operation["operation_id"], optical))
    validate_payload(payload, optical, operation["operation_id"])
    source = {**{key: end_source[key] for key in SEMANTIC_KEYS}, **optical}
    _validate_source(source, project)
    payload_json = canonical(payload)
    raw = payload_json.encode("utf-8")
    require(len(raw) <= MAX_ARTIFACT_BYTES, "MILESTONE_ARTIFACT_TOO_LARGE")
    digest = hashlib.sha256(raw).hexdigest()
    _schema(db, create=True)
    old = _load_record(db, project, task_id=task["request_id"], attempt_id=attempt["id"])
    if old is not None:
        metadata, stored = old
        require(stored == payload_json and metadata["sha256"] == digest and metadata["source"] == source
                and metadata["operation_id"] == operation["operation_id"] and metadata["stage_id"] == attempt["stage_id"],
                "MILESTONE_ARTIFACT_CONFLICT")
        return metadata
    rows = db.execute(f"SELECT project,bytes,length(CAST(payload_json AS BLOB)) AS payload_size FROM {TABLE} LIMIT ?",
                      (MAX_WORKSPACE_ARTIFACTS + 1,)).fetchall()
    require(all(_text(row["project"], 80) and type(row["bytes"]) is int
                and 0 < row["bytes"] <= MAX_ARTIFACT_BYTES and row["bytes"] == row["payload_size"]
                for row in rows), "MILESTONE_ARTIFACT_UNAVAILABLE")
    project_rows = [row for row in rows if row["project"] == project]
    # Geometry and quality artifacts share one byte budget per project/workspace.
    workspace_bytes, project_bytes = shared_bytes(db, project)
    require(len(rows) < MAX_WORKSPACE_ARTIFACTS and len(project_rows) < MAX_PROJECT_ARTIFACTS
            and workspace_bytes + len(raw) <= MAX_WORKSPACE_BYTES
            and project_bytes + len(raw) <= MAX_PROJECT_BYTES,
            "MILESTONE_ARTIFACT_CAPACITY")
    metadata = {"id": uuid.uuid4().hex, "kind": "geometry", "schema_version": 1, "project_name": project,
                "task_id": task["request_id"], "attempt_id": attempt["id"], "stage_id": attempt["stage_id"],
                "operation_id": operation["operation_id"], "created_at": datetime.now(timezone.utc).isoformat(),
                "sha256": digest, "bytes": len(raw), "source": source}
    _metadata(metadata, project)
    db.execute(f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?)", (metadata["id"], project, metadata["task_id"],
               metadata["attempt_id"], canonical(metadata), payload_json, len(raw)))
    return metadata


def _scope(project, payload, observation):
    from tools.project_manager import get_active_project_name
    flow.project_name(project)
    flow.require(isinstance(payload, dict) and set(payload) == {"source", "artifact_id"},
                 "MILESTONE_INPUT_INVALID", "阶段几何读取只接受 source 与 artifact_id")
    scope = payload["source"]
    context = observation.get("context") if isinstance(observation, dict) else None
    instance = observation.get("service_instance_id") if isinstance(observation, dict) else None
    require(isinstance(context, dict) and _text(instance, 512) and context.get("service_instance_id") == instance
            and _text(context.get("active_project"), 80), "MILESTONE_ARTIFACT_SOURCE_REQUIRED")
    require(isinstance(scope, dict) and set(scope) == {"service_instance_id", "project_name"}
            and _text(scope.get("service_instance_id"), 512) and _text(scope.get("project_name"), 80),
            "MILESTONE_ARTIFACT_SOURCE_REQUIRED")
    flow.require(scope["service_instance_id"] == instance, "WORKBENCH_INSTANCE_MISMATCH", "服务实例已变化，请重新读取")
    flow.require(project == scope["project_name"] == context["active_project"] == get_active_project_name(),
                 "WORKFLOW_PROJECT_MISMATCH", "活动工程已变化，请重新读取")
    flow.require(isinstance(payload["artifact_id"], str)
                 and re.fullmatch(r"[0-9a-f]{32}", payload["artifact_id"]) is not None,
                 "MILESTONE_INPUT_INVALID", "阶段几何 ID 必须是规范标识符")
    return dict(scope)


def _reference(db, project, metadata):
    size = db.execute("SELECT length(CAST(data AS BLOB)) FROM tasks WHERE id=? AND project=?",
                      (metadata["task_id"], project)).fetchone()
    require(size is not None and type(size[0]) is int and 0 < size[0] <= 256 * 1024)
    row = db.execute("SELECT data FROM tasks WHERE id=? AND project=?", (metadata["task_id"], project)).fetchone()
    require(row is not None)
    task = strict_json(row[0])
    require(isinstance(task, dict) and task.get("request_id") == metadata["task_id"]
            and task.get("project_name") == project and isinstance(task.get("milestone_attempts"), list))
    attempts = [attempt for attempt in task["milestone_attempts"]
                if isinstance(attempt, dict) and attempt.get("id") == metadata["attempt_id"]]
    require(len(attempts) == 1)
    attempt = attempts[0]
    reference = _metadata(attempt.get("geometry_artifact"), project)
    require(reference == metadata and attempt.get("stage_id") == metadata["stage_id"]
            and attempt.get("task_id") == metadata["task_id"] and attempt.get("declared_status") == "needs_review")
    end = attempt.get("end_source")
    _validate_source(end, project)
    require(all(end.get(key) == value for key, value in metadata["source"].items()
            if key != "source_state_version"))
    operations = attempt.get("operations")
    require(isinstance(operations, list) and bool(operations) and isinstance(operations[-1], dict)
            and operations[-1].get("operation_id") == metadata["operation_id"]
            and operations[-1].get("status") == "succeeded" and operations[-1].get("result_status") == "success"
            and not operations[-1].get("rolled_back") and not operations[-1].get("view_error"))
    observed_source = validate_optical(operations[-1].get("observed_source"))
    require(observed_source == {key: metadata["source"][key] for key in OPTICAL_KEYS})


def read(project, payload, observation):
    scope = _scope(project, payload, observation)
    try:
        path = check_path(project)
        if not path.exists():
            fail("MILESTONE_ARTIFACT_NOT_FOUND")
        require(path.is_file(), "MILESTONE_ARTIFACT_UNAVAILABLE")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            _schema(db)
            record = _load_record(db, project, artifact_id=payload["artifact_id"])
            require(record is not None, "MILESTONE_ARTIFACT_NOT_FOUND")
            metadata, payload_json = record
            _reference(db, project, metadata)
            return {"status": "success", "stage_artifact_schema_version": 1, "storage": "persistent",
                    "source": scope, "artifact": metadata, "payload_json": payload_json, "limits": limits()}
        finally:
            db.close()
    except flow.WorkflowError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        fail("MILESTONE_ARTIFACT_INVALID")
    except Exception:
        fail("MILESTONE_ARTIFACT_UNAVAILABLE")
