"""Explicit, persistent optimization records; no native execution or optical verdicts."""
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import time


# Only EngineWorker supplies this observation. Direct tool calls fail closed on writes.
OBSERVATION = ContextVar("workflow_milestone_observation", default=None)
STAGES = (("stage_1", "基础曲率"), ("stage_2", "厚度与边界"),
          ("stage_3", "波前与材料"), ("stage_4", "深度寻优与公差"))
LIMITS = {"max_attempts_per_task": 16, "max_operations_per_report": 12,
          "max_visible_attempts": 32}
_MODEL = ("service_instance_id", "model_id", "active_project", "model_project", "configuration")
_IDENTITY = ("service_instance_id", "model_id", "active_project", "model_project")
_OPERATION_FIELDS = ("operation_id", "tool", "queued_at", "finished_at", "status", "result_status",
                     "code", "rolled_back", "view_error", "observed_source")


def _flow():
    from core import design_workflow
    return design_workflow


def _iso():
    return datetime.fromtimestamp(time.time(), timezone.utc).isoformat()


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _semantic(project, draft, proposal):
    f = _flow()
    return {"project_name": project, "requirements_revision": draft["revision"],
            "proposal_hash": f.proposal_hash(proposal),
            "proposal_revision": proposal.get("proposal_revision") if proposal else None,
            "authorization_revision": draft.get("authorization_revision", 0)}


def _approved(draft, proposal):
    f = _flow()
    return bool(f._binding_valid(draft, proposal)
                and proposal.get("user_confirmed_to_simulate") is True
                and draft.get("approval") == f.proposal_hash(proposal))


def _source(project, draft, proposal, observation):
    optical = observation.get("source") if observation else None
    return {**_semantic(project, draft, proposal), **deepcopy(optical)} if optical else None


def stale_reasons(attempt, task, project, draft, proposal, observation, active_project):
    """Project current state without changing a persisted attempt or reviving a claim."""
    reasons = []
    source = attempt["source"]
    semantic = _semantic(project, draft, proposal)
    optical = observation.get("source") if observation else None
    current = optical or (observation.get("context", {}) if observation else {})
    if not optical or (attempt["declared_status"] != "running" and not attempt.get("end_source")):
        reasons.append("observation_unavailable")
    if active_project != project or (current and (
            current.get("active_project") != project or current.get("model_project") != project)):
        reasons.append("inactive_project")
    if not observation or observation.get("service_instance_id") != source["service_instance_id"]:
        reasons.append("service_instance_changed")
    if current and current.get("model_id") != source["model_id"]:
        reasons.append("model_changed")
    if optical:
        if optical.get("configuration") != source["configuration"]:
            reasons.append("configuration_changed")
    if source["requirements_revision"] != semantic["requirements_revision"]:
        reasons.append("requirements_changed")
    if any(source[key] != semantic[key] for key in ("proposal_hash", "proposal_revision")):
        reasons.append("proposal_changed")
    if source["authorization_revision"] != semantic["authorization_revision"]:
        reasons.append("authorization_changed")
    if not _approved(draft, proposal):
        reasons.append("approval_revoked")
    if (task.get("worker_id") != attempt["worker_id"]
            or task.get("lease_until") != attempt["lease_until"]):
        reasons.append("claim_changed")
    if task["status"] == "claimed" and (task.get("lease_until") or 0) <= time.time():
        reasons.append("lease_expired")
    if task["status"] not in {"claimed", "completed"} or (
            task["status"] == "completed" and attempt["declared_status"] == "running"):
        reasons.append("handoff_inactive")
    end = attempt.get("end_source")
    if (attempt["declared_status"] != "running" and optical and end
            and optical.get("model_revision") != end["model_revision"]):
        reasons.append("model_revision_changed")
    if (optical and attempt["declared_status"] == "running"
            and optical.get("model_revision", 0) < source["model_revision"]):
        reasons.append("model_revision_changed")
    return reasons


def view(project, draft, proposal, tasks, active_project):
    observation = OBSERVATION.get()
    attempts = [(attempt, task) for task in tasks
                for attempt in task.get("milestone_attempts", [])]
    # Stable persisted order resolves equal clocks; never use stage number as progress.
    attempts.sort(key=lambda pair: (pair[0].get("started_at", ""), pair[0].get("id", "")), reverse=True)
    attempts = attempts[:LIMITS["max_visible_attempts"]]
    stages = []
    for stage_id, label in STAGES:
        displayed = []
        for attempt, task in attempts:
            if attempt["stage_id"] != stage_id:
                continue
            reasons = stale_reasons(attempt, task, project, draft, proposal, observation, active_project)
            public = {key: deepcopy(value) for key, value in attempt.items()
                      if key not in {"lease_until"}}
            public.update(status="stale" if reasons else attempt["declared_status"], stale_reasons=reasons)
            displayed.append(public)
        stages.append({"id": stage_id, "label": label,
                       "status": displayed[0]["status"] if displayed else "not_started", "attempts": displayed})
    return {"schema_version": 1, "storage": "project",
            "service_instance_id": observation.get("service_instance_id") if observation else None,
            "project_name": project, "stages": stages, "limits": dict(LIMITS)}


def _write_context(db, project, expected, request_id, version, token):
    f = _flow()
    f.check_context(db, project, expected)
    task = f._task(db, project, request_id, version)
    f.verify_lease(task, token)
    draft, proposal = f._draft(db, project), f._proposal(project)
    f.require(task["kind"] == "model_handoff" and f._task_valid(task, draft, proposal),
              "MILESTONE_TASK_INVALID", "阶段记录需要当前已领取的建模交接任务")
    f.require(_approved(draft, proposal), "MILESTONE_AUTHORIZATION_REQUIRED", "本版提案尚未授权或授权已失效")
    observation = OBSERVATION.get()
    f.require(isinstance(observation, dict) and isinstance(observation.get("service_instance_id"), str)
              and observation["service_instance_id"],
              "MILESTONE_OBSERVATION_REQUIRED", "阶段记录必须通过当前工作台的可信观察提交")
    return task, draft, proposal, observation


def start(project, expected, request_id, version, lease_token, stage_id, attempt_id, note, model=None):
    f = _flow()
    f.require(stage_id in dict(STAGES), "MILESTONE_INPUT_INVALID", "未知优化阶段")
    f.text(attempt_id, "阶段记录ID", 100, True)
    f.text(note, "阶段说明", 2000, True)
    with f.transaction() as db:
        task, draft, proposal, observation = _write_context(db, project, expected, request_id, version, lease_token)
        source = _source(project, draft, proposal, observation)
        f.require(source and source["active_project"] == project and source["model_project"] == project,
                  "MILESTONE_OBSERVATION_REQUIRED", "请先读取属于当前工程的精确有效模型")
        duplicate = db.execute(
            "SELECT 1 FROM tasks WHERE project=? AND EXISTS (SELECT 1 FROM "
            "json_each(tasks.data,'$.milestone_attempts') WHERE json_extract(value,'$.id')=?)",
            (project, attempt_id)).fetchone()
        f.require(duplicate is None, "MILESTONE_ID_CONFLICT", "阶段记录ID已用于其他请求或旧记录")
        tasks = f._tasks(db, project)
        for old_task in tasks:
            for attempt in old_task.get("milestone_attempts", []):
                f.require(attempt["declared_status"] != "running" or stale_reasons(
                    attempt, old_task, project, draft, proposal, observation, project),
                    "MILESTONE_ALREADY_RUNNING", "当前工程已有进行中的阶段记录")
        attempts = task.setdefault("milestone_attempts", [])
        f.require(len(attempts) < LIMITS["max_attempts_per_task"], "MILESTONE_CAPACITY_REACHED", "本任务阶段记录已达到容量上限")
        attempts.append({"id": attempt_id, "stage_id": stage_id, "task_id": request_id,
                         "declared_status": "running", "started_at": _iso(), "reported_at": None,
                         "source": source, "end_source": None, "worker_id": task["worker_id"],
                         "lease_until": task["lease_until"], "operations": [], "note": note})
        task["version"] += 1
        f._put_task(db, task)
    return f.view(project, model)


def _report(project, expected, request_id, version, lease_token, attempt_id, status, operation_ids, note,
            model=None, archive_geometry=False, quality_result_id=None):
    f = _flow()
    f.text(attempt_id, "阶段记录ID", 100, True)
    f.text(note, "阶段说明", 2000, True)
    f.require(status in {"needs_review", "failed"}, "MILESTONE_INPUT_INVALID", "阶段仅可报告待核对或失败")
    f.require(isinstance(operation_ids, list) and len(operation_ids) <= LIMITS["max_operations_per_report"]
              and all(isinstance(i, str) and 0 < len(i) <= 100 for i in operation_ids)
              and len(set(operation_ids)) == len(operation_ids)
              and (status == "failed" or operation_ids),
              "MILESTONE_INPUT_INVALID", "待核对记录需要1–12条唯一操作ID；失败报告可无操作")
    with f.transaction() as db:
        task, draft, proposal, observation = _write_context(db, project, expected, request_id, version, lease_token)
        attempt = next((item for item in task.get("milestone_attempts", []) if item["id"] == attempt_id), None)
        f.require(attempt is not None, "MILESTONE_NOT_FOUND", "本任务不存在该阶段记录")
        f.require(attempt["declared_status"] == "running", "MILESTONE_ALREADY_REPORTED", "该阶段记录已经结束")
        reasons = stale_reasons(attempt, task, project, draft, proposal, observation, project)
        allowed = {"observation_unavailable"} if status == "failed" else set()
        f.require(not set(reasons) - allowed, "MILESTONE_SOURCE_STALE", "阶段来源、授权或领取状态已变化")
        evidence = {item["operation_id"]: item for item in observation.get("operations", [])}
        operations = []
        started = _timestamp(attempt["started_at"])
        for operation_id in operation_ids:
            operation = evidence.get(operation_id)
            queued = _timestamp(operation.get("queued_at")) if operation else None
            finished = _timestamp(operation.get("finished_at")) if operation else None
            f.require(operation and queued is not None and finished is not None and started is not None
                      and queued >= started and finished >= queued,
                      "MILESTONE_EVIDENCE_INVALID", "操作不存在、已淘汰或不属于该阶段开始之后")
            observed = operation.get("observed_source")
            f.require(all(operation.get("_context", {}).get(key) == attempt["source"][key]
                          for key in _IDENTITY),
                      "MILESTONE_EVIDENCE_INVALID", "操作结束时的工程、模型或服务与阶段不符")
            if observed:
                f.require(all(observed.get(key) == attempt["source"][key] for key in _MODEL)
                          and observed.get("model_revision", 0) >= attempt["source"]["model_revision"],
                          "MILESTONE_EVIDENCE_INVALID", "操作不属于本阶段的工程、模型或配置")
            if status == "needs_review":
                f.require(observed and operation["result_status"] == "success" and operation["status"] == "succeeded"
                          and not operation.get("rolled_back") and not operation.get("view_error"),
                          "MILESTONE_EVIDENCE_INVALID", "待核对记录只能引用成功且已精确观察的几何操作")
            operations.append({key: deepcopy(operation[key]) for key in _OPERATION_FIELDS if key in operation})
        operations.sort(key=lambda item: (item["finished_at"], item["operation_id"]))
        end_source = _source(project, draft, proposal, observation)
        if status == "needs_review":
            f.require(end_source and end_source["model_revision"] == operations[-1]["observed_source"]["model_revision"],
                      "MILESTONE_SOURCE_STALE", "当前模型修订与最后引用的操作不一致")
        if archive_geometry:
            from core.milestone_geometry_artifacts import archive
            attempt["geometry_artifact"] = archive(db, project, task, attempt, operations[-1], end_source)
        if quality_result_id is not None:
            from core.milestone_quality_artifacts import archive as archive_quality
            attempt["quality_artifact"] = archive_quality(db, project, task, attempt, quality_result_id, end_source)
        attempt.update(declared_status=status, reported_at=_iso(), operations=operations,
                       end_source=end_source, note=note)
        task["version"] += 1
        f._put_task(db, task)
        if archive_geometry or quality_result_id is not None:
            # Build the response inside the same transaction, so failures also
            # roll back the artifact, its reference and the task version.
            return f.view(project, model)
    return f.view(project, model)


def report(project, expected, request_id, version, lease_token, attempt_id, status, operation_ids, note,
           model=None, archive_geometry=False, quality_result_id=None):
    f = _flow()
    f.require(type(archive_geometry) is bool and (not archive_geometry or status == "needs_review"),
              "MILESTONE_INPUT_INVALID", "archive_geometry 必须是布尔值，且归档仅用于待核对报告")
    from core import milestone_quality_artifacts as quality
    f.require(quality_result_id is None or (quality.valid_result_id(quality_result_id) and status == "needs_review"),
              "MILESTONE_INPUT_INVALID", "quality_result_id 必须是显式分析结果ID，且归档仅用于待核对报告")
    if not archive_geometry and quality_result_id is None:
        return _report(project, expected, request_id, version, lease_token, attempt_id, status,
                       operation_ids, note, model)
    from core import milestone_geometry_artifacts as artifacts
    try:
        artifacts.check_path(project)
        return _report(project, expected, request_id, version, lease_token, attempt_id, status,
                       operation_ids, note, model, archive_geometry=archive_geometry,
                       quality_result_id=quality_result_id)
    except f.WorkflowError:
        raise
    except Exception:
        (artifacts if archive_geometry else quality).fail(
            "MILESTONE_ARTIFACT_UNAVAILABLE" if archive_geometry else "MILESTONE_QUALITY_UNAVAILABLE")


def stage_status(project):
    """Read-only: {"stage": open stage_id or None, "handoff": live claimed handoff or None}.

    Used only to remind an agent how its optimization relates to the stage record.
    It never writes, never claims, and any storage problem reads as nothing found.
    """
    import json
    import sqlite3
    from pathlib import Path
    from tools.project_manager import get_output_base_dir
    found = {"stage": None, "handoff": None}
    try:
        path = Path(get_output_base_dir()) / ".design-workflow.sqlite3"
        if not path.is_file() or path.is_symlink():
            return found
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            rows = db.execute(
                "SELECT data FROM tasks WHERE project=? AND json_extract(data,'$.status')='claimed'",
                (project,)).fetchall()
        finally:
            db.close()
        for (raw,) in rows:
            task = json.loads(raw)
            if (task.get("lease_until") or 0) <= time.time():
                continue  # an expired claim cannot carry an open stage
            if task.get("kind") == "model_handoff" and found["handoff"] is None:
                found["handoff"] = {"request_id": task.get("request_id"), "version": task.get("version")}
            for attempt in task.get("milestone_attempts", []):
                if attempt.get("declared_status") == "running" and found["stage"] is None:
                    found["stage"] = attempt.get("stage_id")
    except Exception:
        return {"stage": None, "handoff": None}
    return found


def running_stage(project):
    """Read-only: the stage_id of a running attempt in ``project``, else None."""
    return stage_status(project)["stage"]
