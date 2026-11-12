"""Immutable stage image-quality results from the trusted analysis cache; no native execution.

Mirrors milestone_geometry_artifacts: the caller names one existing result_id,
the Engine-only reader detaches it from the session cache, and the report
transaction stores the original body. Verdicts are stored, never recomputed.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import re
import sqlite3
import uuid

from core import design_workflow as flow
from core import milestone_geometry_artifacts as geometry


QUALITY_READER = ContextVar("milestone_quality_reader", default=None)
TABLE = "milestone_quality_artifacts"
MAX_ARTIFACT_BYTES = geometry.MAX_ARTIFACT_BYTES
MAX_PROJECT_ARTIFACTS = geometry.MAX_PROJECT_ARTIFACTS
MAX_WORKSPACE_ARTIFACTS = geometry.MAX_WORKSPACE_ARTIFACTS
OPTICAL_KEYS = geometry.OPTICAL_KEYS
SEMANTIC_KEYS = geometry.SEMANTIC_KEYS
ANALYSIS_KEYS = frozenset({"status", "overall", "started_at", "finished_at"})
METADATA_KEYS = frozenset({"id", "kind", "schema_version", "project_name", "task_id", "attempt_id", "stage_id",
                           "result_id", "created_at", "sha256", "bytes", "source", "analysis"})
_RESULT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
_MESSAGES = {
    "MILESTONE_QUALITY_SOURCE_REQUIRED": "阶段像质需要当前工作台的可信分析缓存",
    "MILESTONE_QUALITY_NOT_FOUND": "阶段像质不存在、未归档或分析缓存已淘汰",
    "MILESTONE_QUALITY_INVALID": "阶段像质数据、来源或引用不一致",
    "MILESTONE_QUALITY_OUTSIDE_STAGE": "分析结果不是在本阶段开始之后计算的，不能作为本阶段证据",
    "MILESTONE_QUALITY_UNAVAILABLE": "阶段像质存储不可用或数据不兼容",
    "MILESTONE_QUALITY_TOO_LARGE": "阶段像质超过单条归档预算",
    "MILESTONE_QUALITY_CAPACITY": "阶段归档已达到工程或工作区容量上限",
    "MILESTONE_QUALITY_CONFLICT": "该阶段已有不同的像质归档，不能覆盖",
}


def fail(code):
    raise flow.WorkflowError(code, _MESSAGES[code])


def require(ok, code="MILESTONE_QUALITY_INVALID"):
    if not ok:
        fail(code)


def valid_result_id(value):
    return isinstance(value, str) and _RESULT_ID.fullmatch(value) is not None


def _instant(value):
    require(geometry._timestamp(value))
    return datetime.fromisoformat(value).timestamp()


def _checked(function, *args):
    """Reuse geometry validators but report quality-specific codes."""
    try:
        return function(*args)
    except flow.WorkflowError as error:
        if error.code.startswith("MILESTONE_ARTIFACT_"):
            fail("MILESTONE_QUALITY_INVALID")
        raise


def validate_result(result, result_id, project):
    """Validate stored metadata with the analysis-history contract; never re-judge."""
    from core.workbench_analysis_history import summary
    require(isinstance(result, dict) and isinstance(result.get("source"), dict))
    source = result["source"]
    require(set(source) == set(OPTICAL_KEYS))
    _checked(geometry.validate_optical, source)
    require(summary(result, source["service_instance_id"], project, result_id) is not None)
    require(geometry._timestamp(result.get("started_at"))
            and _instant(result["started_at"]) <= _instant(result["finished_at"]))
    require(geometry._json_shape(result))
    return result


def _metadata(value, project):
    require(isinstance(value, dict) and set(value) == METADATA_KEYS and geometry._json_shape(value))
    require(isinstance(value["id"], str) and re.fullmatch(r"[0-9a-f]{32}", value["id"]) is not None
            and value["kind"] == "quality" and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["project_name"] == project and valid_result_id(value["result_id"]))
    require(all(geometry._text(value[key]) for key in ("task_id", "attempt_id"))
            and value["stage_id"] in {"stage_1", "stage_2", "stage_3", "stage_4"}
            and geometry._timestamp(value["created_at"]) and type(value["bytes"]) is int
            and 0 < value["bytes"] <= MAX_ARTIFACT_BYTES
            and isinstance(value["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)
    _checked(geometry._validate_source, value["source"], project)
    analysis = value["analysis"]
    require(isinstance(analysis, dict) and set(analysis) == ANALYSIS_KEYS
            and analysis["status"] in {"success", "partial"}
            and analysis["overall"] in {"PASS", "WARN", "FAIL", "ERROR", "INFO"}
            and geometry._timestamp(analysis["started_at"]) and geometry._timestamp(analysis["finished_at"]))
    return value


_COLUMNS = geometry._COLUMNS


def _schema(db, create=False):
    row = db.execute("SELECT type FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()
    if row is None:
        if not create:
            fail("MILESTONE_QUALITY_NOT_FOUND")
        db.execute(f"CREATE TABLE {TABLE} (id TEXT PRIMARY KEY, project TEXT NOT NULL, task_id TEXT NOT NULL, "
                   "attempt_id TEXT NOT NULL, metadata_json TEXT NOT NULL, payload_json TEXT NOT NULL, "
                   "bytes INTEGER NOT NULL, UNIQUE(project,task_id,attempt_id))")
    else:
        require(row[0] == "table", "MILESTONE_QUALITY_UNAVAILABLE")
    actual = [(row[1], row[2].upper(), row[3], row[5]) for row in db.execute(f"PRAGMA table_info({TABLE})")]
    require(actual == _COLUMNS, "MILESTONE_QUALITY_UNAVAILABLE")
    unique_bindings = []
    for row in db.execute(f"PRAGMA index_list({TABLE})"):
        if row[2] and not row[4]:
            # Table-controlled identifier; never use a client-provided SQL name.
            escaped = row[1].replace('"', '""')
            unique_bindings.append([item[2] for item in db.execute(f'PRAGMA index_info("{escaped}")')])
    require(["project", "task_id", "attempt_id"] in unique_bindings, "MILESTONE_QUALITY_UNAVAILABLE")


def _record(row, project):
    require(type(row["metadata_json"]) is str and len(row["metadata_json"].encode("utf-8")) <= 16384)
    metadata = _metadata(geometry.strict_json(row["metadata_json"]), project)
    require(row["id"] == metadata["id"] and row["project"] == project
            and row["task_id"] == metadata["task_id"] and row["attempt_id"] == metadata["attempt_id"]
            and type(row["bytes"]) is int and row["bytes"] == metadata["bytes"])
    payload_json = row["payload_json"]
    require(isinstance(payload_json, str))
    raw = payload_json.encode("utf-8")
    require(len(raw) == metadata["bytes"] and hashlib.sha256(raw).hexdigest() == metadata["sha256"])
    result = validate_result(geometry.strict_json(payload_json), metadata["result_id"], project)
    require(result["source"] == {key: metadata["source"][key] for key in OPTICAL_KEYS}
            and metadata["analysis"] == {key: result[key] for key in ANALYSIS_KEYS})
    return metadata, payload_json


def _load_record(db, project, *, artifact_id=None, task_id=None, attempt_id=None):
    where = "id=? AND project=?" if artifact_id is not None else "project=? AND task_id=? AND attempt_id=?"
    values = (artifact_id, project) if artifact_id is not None else (project, task_id, attempt_id)
    size = db.execute(f"SELECT length(CAST(metadata_json AS BLOB)),length(CAST(payload_json AS BLOB)) "
                      f"FROM {TABLE} WHERE {where}", values).fetchone()
    if size is None:
        return None
    require(type(size[0]) is int and 0 < size[0] <= 16384)
    require(type(size[1]) is int and 0 < size[1] <= MAX_ARTIFACT_BYTES, "MILESTONE_QUALITY_TOO_LARGE")
    row = db.execute(f"SELECT * FROM {TABLE} WHERE {where}", values).fetchone()
    return _record(row, project)


def archive(db, project, task, attempt, result_id, end_source):
    """Insert with the caller's workflow transaction; never commit or overwrite."""
    reader = QUALITY_READER.get()
    require(callable(reader), "MILESTONE_QUALITY_SOURCE_REQUIRED")
    require(valid_result_id(result_id) and isinstance(end_source, dict))
    expected = {key: end_source.get(key) for key in OPTICAL_KEYS if key != "source_state_version"}
    result = reader(result_id)
    require(result is not None, "MILESTONE_QUALITY_NOT_FOUND")
    from core.workbench_analysis_history import sanitized
    result = validate_result(sanitized(result), result_id, project)
    optical = result["source"]
    # Same service/model/revision/project/configuration as the reported geometry;
    # each side keeps its own observed source_state_version.
    require(all(optical[key] == value for key, value in expected.items()))
    reported = datetime.now(timezone.utc).timestamp()
    require(_instant(result["started_at"]) >= _instant(attempt["started_at"])
            and _instant(result["finished_at"]) <= reported, "MILESTONE_QUALITY_OUTSIDE_STAGE")
    source = {**{key: end_source[key] for key in SEMANTIC_KEYS}, **optical}
    _checked(geometry._validate_source, source, project)
    payload_json = geometry.canonical(result)
    raw = payload_json.encode("utf-8")
    require(len(raw) <= MAX_ARTIFACT_BYTES, "MILESTONE_QUALITY_TOO_LARGE")
    digest = hashlib.sha256(raw).hexdigest()
    _schema(db, create=True)
    old = _load_record(db, project, task_id=task["request_id"], attempt_id=attempt["id"])
    if old is not None:
        metadata, stored = old
        require(stored == payload_json and metadata["sha256"] == digest and metadata["source"] == source
                and metadata["result_id"] == result_id and metadata["stage_id"] == attempt["stage_id"],
                "MILESTONE_QUALITY_CONFLICT")
        return metadata
    rows = db.execute(f"SELECT project FROM {TABLE} LIMIT ?", (MAX_WORKSPACE_ARTIFACTS + 1,)).fetchall()
    require(all(geometry._text(row["project"], 80) for row in rows), "MILESTONE_QUALITY_UNAVAILABLE")
    workspace_bytes, project_bytes = geometry.shared_bytes(db, project)
    require(len(rows) < MAX_WORKSPACE_ARTIFACTS
            and sum(row["project"] == project for row in rows) < MAX_PROJECT_ARTIFACTS
            and workspace_bytes + len(raw) <= geometry.MAX_WORKSPACE_BYTES
            and project_bytes + len(raw) <= geometry.MAX_PROJECT_BYTES,
            "MILESTONE_QUALITY_CAPACITY")
    metadata = {"id": uuid.uuid4().hex, "kind": "quality", "schema_version": 1, "project_name": project,
                "task_id": task["request_id"], "attempt_id": attempt["id"], "stage_id": attempt["stage_id"],
                "result_id": result_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "sha256": digest, "bytes": len(raw), "source": source,
                "analysis": {key: result[key] for key in ANALYSIS_KEYS}}
    _metadata(metadata, project)
    db.execute(f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?)", (metadata["id"], project, metadata["task_id"],
               metadata["attempt_id"], geometry.canonical(metadata), payload_json, len(raw)))
    return metadata


def _reference(db, project, metadata):
    size = db.execute("SELECT length(CAST(data AS BLOB)) FROM tasks WHERE id=? AND project=?",
                      (metadata["task_id"], project)).fetchone()
    require(size is not None and type(size[0]) is int and 0 < size[0] <= 256 * 1024)
    row = db.execute("SELECT data FROM tasks WHERE id=? AND project=?", (metadata["task_id"], project)).fetchone()
    require(row is not None)
    task = geometry.strict_json(row[0])
    require(isinstance(task, dict) and task.get("request_id") == metadata["task_id"]
            and task.get("project_name") == project and isinstance(task.get("milestone_attempts"), list))
    attempts = [attempt for attempt in task["milestone_attempts"]
                if isinstance(attempt, dict) and attempt.get("id") == metadata["attempt_id"]]
    require(len(attempts) == 1)
    attempt = attempts[0]
    reference = _metadata(attempt.get("quality_artifact"), project)
    require(reference == metadata and attempt.get("stage_id") == metadata["stage_id"]
            and attempt.get("task_id") == metadata["task_id"] and attempt.get("declared_status") == "needs_review")
    end = attempt.get("end_source")
    _checked(geometry._validate_source, end, project)
    require(all(end.get(key) == value for key, value in metadata["source"].items()
                if key != "source_state_version"))


def read(project, payload, observation):
    try:
        scope = geometry._scope(project, payload, observation)
    except flow.WorkflowError as error:
        if error.code == "MILESTONE_ARTIFACT_SOURCE_REQUIRED":
            fail("MILESTONE_QUALITY_SOURCE_REQUIRED")
        if error.code == "MILESTONE_INPUT_INVALID":
            raise flow.WorkflowError(error.code, "阶段像质读取只接受 source 与规范 artifact_id")
        raise
    try:
        path = geometry.check_path(project)
        if not path.exists():
            fail("MILESTONE_QUALITY_NOT_FOUND")
        require(path.is_file(), "MILESTONE_QUALITY_UNAVAILABLE")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            _schema(db)
            record = _load_record(db, project, artifact_id=payload["artifact_id"])
            require(record is not None, "MILESTONE_QUALITY_NOT_FOUND")
            metadata, payload_json = record
            _reference(db, project, metadata)
            return {"status": "success", "stage_artifact_schema_version": 1, "storage": "persistent",
                    "source": scope, "artifact": metadata, "payload_json": payload_json,
                    "limits": geometry.limits()}
        finally:
            db.close()
    except flow.WorkflowError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        fail("MILESTONE_QUALITY_INVALID")
    except Exception:
        fail("MILESTONE_QUALITY_UNAVAILABLE")
