"""Persistent, native-independent requirements, proposal bindings and Agent leases.

SQLite serializes cooperating processes. Authorization remains in the existing
project proposal JSON; revoke it before committing changed workflow evidence.
"""

from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
import hashlib
import inspect
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from contextvars import ContextVar

MODEL_REFERENCE = ContextVar("workflow_model_reference", default=None)


class WorkflowError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(ok, code, message):
    if not ok:
        raise WorkflowError(code, message)


def encoded(value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    require(
        len(text.encode("utf-8")) <= 256 * 1024,
        "WORKFLOW_TOO_LARGE",
        "流程数据超过 256 KiB",
    )
    return text


def text(value, name, limit=16000, required=False):
    require(
        isinstance(value, str)
        and len(value) <= limit
        and (not required or bool(value.strip())),
        "WORKFLOW_INPUT_INVALID",
        f"{name} 为空或超过长度限制",
    )
    return value


def counter(value):
    require(
        type(value) is int and value >= 0,
        "WORKFLOW_INPUT_INVALID",
        "版本必须是非负整数",
    )
    return value


def project_name(name):
    from tools.project_manager import sanitize_project_name

    require(
        isinstance(name, str)
        and 0 < len(name) <= 80
        and not re.search(r'[\\/:*?"<>|\s]', name),
        "WORKFLOW_PROJECT_INVALID",
        "工程名使用字母、数字、中文、下划线或连字符，不能包含路径",
    )
    require(
        sanitize_project_name(name) == name,
        "WORKFLOW_PROJECT_INVALID",
        "请使用规范的小写工程名",
    )
    return name


def _path(project):
    from tools.project_manager import OUTPUT_BASE_DIR, _within

    root = Path(OUTPUT_BASE_DIR)
    target = root / project_name(project)
    require(
        _within(str(target), str(root)),
        "WORKFLOW_PROJECT_INVALID",
        "工程目录超出工作区",
    )
    return target


def _proposal(project):
    p = _path(project) / "design_proposal.json"
    if not p.exists():
        return None
    from tools.project_manager import _within

    require(
        _within(str(p), str(_path(project))),
        "WORKFLOW_PATH_INVALID",
        "提案文件超出工程目录",
    )
    require(
        p.stat().st_size <= 512 * 1024, "WORKFLOW_TOO_LARGE", "提案文件超过读取预算"
    )
    data = json.loads(p.read_text(encoding="utf-8"))
    require(
        data.get("project_name") == project,
        "WORKFLOW_PROJECT_MISMATCH",
        "提案工程身份不符",
    )
    return data


def _check_project_writes(project):
    from tools.project_manager import _within

    root = _path(project)
    for child in (
        "cad",
        "drawings",
        "optomech",
        "reports",
        "project.json",
        "design_proposal.json",
    ):
        require(
            _within(str(root / child), str(root)),
            "WORKFLOW_PATH_INVALID",
            "工程写入位置超出目录",
        )


def proposal_hash(proposal):
    if not proposal:
        return None
    keys = (
        "project_name",
        "target_specs",
        "initial_structure_source",
        "optical_theory_analysis",
        "glass_selection_rationale",
        "merit_function_strategy",
        "internal_air_spacing_budget",
        "mechanical_constraints",
        "topology",
        "risks",
        "proposal_revision",
        "requirements_revision",
        "requirements_snapshot",
        "research_task_id",
    )
    content = {k: proposal.get(k) for k in keys}
    # Preserve stored hashes for proposals predating optional structure diagrams.
    # A supplied diagram is review content and must invalidate a changed approval.
    if proposal.get("structure_diagram") is not None:
        content["structure_diagram"] = proposal["structure_diagram"]
    return hashlib.sha256(encoded(content).encode()).hexdigest()


def revoke(project, reason):
    from tools.project_manager import _atomic_json

    p = _proposal(project)
    if p:
        p.update(user_confirmed_to_simulate=False, approval_invalidated=reason)
        _atomic_json(str(_path(project) / "design_proposal.json"), p)


_local = threading.local()


@contextmanager
def transaction():
    if getattr(_local, "db", None) is not None:
        yield _local.db
        return
    from tools.project_manager import get_output_base_dir

    path = Path(get_output_base_dir()) / ".design-workflow.sqlite3"
    require(not path.is_symlink(), "WORKFLOW_PATH_INVALID", "流程数据库不能是符号链接")
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS state (project TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, project TEXT NOT NULL, data TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), selection INTEGER NOT NULL)"
        )
        db.execute("INSERT OR IGNORE INTO meta VALUES (1,0)")
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        _local.db = db
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        _local.db = None
        db.close()


def _draft(db, project):
    row = db.execute("SELECT data FROM state WHERE project=?", (project,)).fetchone()
    return (
        json.loads(row[0])
        if row
        else {
            "revision": 0,
            "system_type": "imaging_lens",
            "specs": {},
            "sources": {},
            "user_prompt": "",
            "constraints": "",
            "audit": None,
            "submitted_revision": None,
        }
    )


def project_selection(fn):
    """Include legacy MCP project changes in the workflow selection revision."""

    @wraps(fn)
    def wrapped(*args, **kwargs):
        from tools.project_manager import get_active_project_name

        with transaction() as db:
            old = get_active_project_name()
            result = fn(*args, **kwargs)
            if result["project_name"] != old:
                db.execute("UPDATE meta SET selection=selection+1 WHERE id=1")
            return result

    return wrapped


def _save(db, project, draft):
    db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (project, encoded(draft)))


def _put_task(db, task):
    db.execute(
        "INSERT INTO tasks VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
        (task["request_id"], task["project_name"], encoded(task)),
    )


def _tasks(db, project):
    return [
        json.loads(r[0])
        for r in db.execute(
            "SELECT data FROM tasks WHERE project=? AND (rowid IN "
            "(SELECT rowid FROM tasks WHERE project=? ORDER BY rowid DESC LIMIT 100) "
            "OR json_extract(data,'$.status') IN ('pending','claimed')) ORDER BY rowid DESC",
            (project, project),
        )
    ]


def context(db, project):
    from tools.project_manager import get_active_project_name

    d = _draft(db, project)
    p = _proposal(project)
    return {
        "project_name": project,
        "active_project": get_active_project_name(),
        "selection_revision": db.execute(
            "SELECT selection FROM meta WHERE id=1"
        ).fetchone()[0],
        "requirements_revision": d["revision"],
        "proposal_hash": proposal_hash(p),
        "proposal_revision": p.get("proposal_revision") if p else None,
        "authorization_revision": d.get("authorization_revision", 0),
    }


def check_context(db, project, expected):
    require(
        isinstance(expected, dict) and expected == context(db, project),
        "WORKFLOW_CONTEXT_STALE",
        "工程、需求或提案已变化，请刷新核对后重试",
    )
    require(
        expected["active_project"] == project,
        "WORKFLOW_PROJECT_MISMATCH",
        "请先显式切换到该工程",
    )


def _ready(d):
    return (
        d.get("audit", {}).get("revision") == d["revision"]
        and d["audit"]["result"]["is_complete"]
        if d.get("audit")
        else False
    )


def _binding_valid(d, p):
    return bool(
        p
        and d["revision"] > 0
        and p.get("requirements_revision") == d["revision"]
        and _ready(d)
        and d.get("submitted_revision") == d["revision"]
        and p.get("proposal_hash") == proposal_hash(p)
    )


def _task_valid(task, d, p, model=None):
    if task["status"] == "superseded":
        return False
    if task["requirements_revision"] != d["revision"]:
        return False
    if task["kind"] in (
        "initial_proposal",
        "proposal_revision",
        "model_handoff",
    ) and task["proposal_hash"] != proposal_hash(p):
        if not (
            task["kind"] in ("initial_proposal", "proposal_revision")
            and p
            and p.get("research_task_id") == task["request_id"]
        ):
            return False
    if task.get("model_reference") and task["model_reference"] != model:
        return False
    if task["kind"] == "model_handoff" and (
        not _binding_valid(d, p)
        or p.get("user_confirmed_to_simulate") is not True
        or d.get("approval") != proposal_hash(p)
    ):
        return False
    return True


def _archive_invalid_tasks(db, project, d, p, model=None):
    """Retire invalid pending work under the same lock used for queue capacity.

    Missing model observation alone is not evidence of replacement: keep those
    tasks suspended until a known model or requirements/proposal change arrives.
    Historical rows/IDs remain available; archived claims lose their tokens and
    increment their version so late results cannot revive them.
    """
    rows = db.execute(
        "SELECT data FROM tasks WHERE project=? AND json_extract(data,'$.status') IN ('pending','claimed')",
        (project,),
    ).fetchall()
    for row in rows:
        task = json.loads(row[0])
        if not _task_valid(task, d, p, task.get("model_reference")):
            reason = "需求、提案或授权已变化"
        elif (
            model is not None
            and task.get("model_reference")
            and task["model_reference"] != model
        ):
            reason = "模型身份、修订或配置已变化"
        else:
            continue
        task.update(
            status="superseded",
            version=task["version"] + 1,
            finished_at=time.time(),
            invalidated_reason=reason,
            progress=f"{reason}；旧任务已归档，请按当前版本重新提交",
        )
        task.pop("token_hash", None)
        _put_task(db, task)


def public_task(task, d, p, model=None):
    data = {k: deepcopy(v) for k, v in task.items() if k not in {"token_hash", "milestone_attempts"}}
    data["stale"] = not _task_valid(task, d, p, model)
    data["lease_expired"] = (
        task["status"] == "claimed" and task["lease_until"] <= time.time()
    )
    return data


def view(project=None, model=None):
    from tools.project_manager import get_active_project_name, list_projects
    from tools.system_tools import SYSTEM_SPEC_TEMPLATES

    project = project_name(project or get_active_project_name())
    model = model if model is not None else MODEL_REFERENCE.get()
    with transaction() as db:
        d = _draft(db, project)
        p = _proposal(project)
        _archive_invalid_tasks(db, project, d, p, model)
        stored_tasks = _tasks(db, project)
        tasks = [public_task(t, d, p, model) for t in stored_tasks]
        current = [t for t in tasks if not t["stale"]]
        latest = current[0] if current else None
        bound = _binding_valid(d, p)
        if p and d["revision"] and (not bound or d.get("approval") != proposal_hash(p)):
            p = deepcopy(p)
            p["user_confirmed_to_simulate"] = False
        if latest and latest["status"] == "failed":
            stage = "handoff_failed"
        elif latest and latest["lease_expired"]:
            stage = "lease_expired"
        elif (
            latest
            and latest["kind"] == "proposal_revision"
            and latest["status"] in ("pending", "claimed")
            and (not p or p.get("research_task_id") != latest["request_id"])
        ):
            stage = "changes_requested"
        elif bound and p.get("user_confirmed_to_simulate") is True:
            stage = (
                "handoff"
                if latest and latest["kind"] == "model_handoff"
                else "confirmed"
            )
        elif bound:
            stage = "proposal_review"
        elif latest and latest["status"] == "claimed":
            stage = "researching"
        elif latest and latest["status"] == "pending":
            stage = "awaiting_agent"
        elif d.get("audit") and not _ready(d):
            stage = "needs_requirements"
        else:
            stage = "draft"
        templates = deepcopy(SYSTEM_SPEC_TEMPLATES)
        for key, t in templates.items():
            for group in ("mandatory", "recommended"):
                for f in t[group]:
                    f["recommended_value"] = recommended_value(key, f)
        from core.design_milestones import view as milestone_view
        return {
            "status": "success",
            "workflow_schema_version": 1,
            "project_name": project,
            "context": context(db, project),
            "draft": d,
            "proposal": p,
            "proposal_bound": bound,
            "stage": stage,
            "tasks": tasks,
            "templates": templates,
            "projects": list_projects(),
            "agent_mode": "explicit_claim",
            "model_reference": model,
            "optimization_milestones": milestone_view(
                project, d, p, stored_tasks, get_active_project_name()),
        }


def recommended_value(system_type, f):
    key = f["key"]
    s = f["default"]
    if key == "fov_or_sensor":
        return "像面直径 11 mm"
    if key == "wavelength_range":
        return "486.1–656.3 nm" if system_type == "imaging_lens" else "400–700 nm"
    if key in (
        "immersion_medium",
        "telecentricity_required",
        "operating_temperature_c",
    ):
        return s
    # Ranges/inequalities are guidance, not a single automatically chosen value.
    before = s.split("(")[0].split("（")[0]
    if re.search(r"[~≥≤<>]|\s/\s", before):
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", before)
    return float(match[0]) if match else None


def select_project(project, expected, create=False):
    from tools.project_manager import get_active_project_name, set_active_project

    project = project_name(project)
    with transaction() as db:
        active = get_active_project_name()
        check_context(db, active, expected)
        require(type(create) is bool, "WORKFLOW_INPUT_INVALID", "create 必须是布尔值")
        require(
            create or (_path(project) / "project.json").is_file(),
            "WORKFLOW_PROJECT_UNKNOWN",
            "工程不存在，请选择新建",
        )
        _check_project_writes(project)
        set_active_project(project)
        db.execute("UPDATE meta SET selection=selection+1 WHERE id=1")
    return view(project)


def save_requirements(
    project, expected, system_type, specs, sources, user_prompt="", constraints=""
):
    from tools.system_tools import SYSTEM_SPEC_TEMPLATES

    project_name(project)
    require(
        system_type in SYSTEM_SPEC_TEMPLATES,
        "WORKFLOW_INPUT_INVALID",
        "请选择受支持的规格模板",
    )
    require(
        isinstance(specs, dict) and isinstance(sources, dict),
        "WORKFLOW_INPUT_INVALID",
        "需求必须为对象",
    )
    text(user_prompt, "需求描述")
    text(constraints, "约束")
    fields = {
        f["key"]: f
        for g in ("mandatory", "recommended")
        for f in SYSTEM_SPEC_TEMPLATES[system_type][g]
    }
    require(
        not set(specs) - set(fields) and set(specs) == set(sources),
        "WORKFLOW_INPUT_INVALID",
        "需求字段或来源不符",
    )
    for key, value in specs.items():
        require(
            value is not None
            and not isinstance(value, (dict, list, bool))
            and value != "",
            "WORKFLOW_INPUT_INVALID",
            "空值请留空，不要填零",
        )
        require(
            sources[key] in ("user", "recommended"),
            "WORKFLOW_INPUT_INVALID",
            "需求来源无效",
        )
        if sources[key] == "recommended":
            require(
                value == recommended_value(system_type, fields[key]),
                "WORKFLOW_INPUT_INVALID",
                "推荐值必须与模板一致",
            )
    encoded(specs)
    with transaction() as db:
        check_context(db, project, expected)
        d = _draft(db, project)
        content = {
            "system_type": system_type,
            "specs": specs,
            "sources": sources,
            "user_prompt": user_prompt,
            "constraints": constraints,
        }
        if any(d.get(k) != v for k, v in content.items()):
            revoke(project, "requirements_changed")
            d.update(
                content,
                revision=d["revision"] + 1,
                audit=None,
                submitted_revision=None,
                approval=None,
            )
            # Once edited in the workbench, the draft follows the claimed-task rules.
            d.pop("origin", None)
            _save(db, project, d)
    return view(project)


def audit(project, expected):
    from tools.system_tools import zemax_audit_requirements

    with transaction() as db:
        check_context(db, project, expected)
        d = _draft(db, project)
        result = zemax_audit_requirements(
            system_type=d["system_type"], specs=d["specs"], user_prompt=d["user_prompt"]
        )
        d["audit"] = {"revision": d["revision"], "result": result, "at": time.time()}
        _save(db, project, d)
    return view(project)


def enqueue(db, project, request_id, kind, user_text, d, p, model=None, target=None):
    text(request_id, "request_id", 100, True)
    text(user_text, "任务内容", 16000, True)
    require(
        kind
        in (
            "initial_proposal",
            "proposal_revision",
            "surface_question",
            "model_handoff",
        ),
        "WORKFLOW_INPUT_INVALID",
        "未知任务意图",
    )
    row = db.execute("SELECT data FROM tasks WHERE id=?", (request_id,)).fetchone()
    identity = {
        "project_name": project,
        "kind": kind,
        "user_text": user_text,
        "requirements_revision": d["revision"],
        "proposal_hash": proposal_hash(p),
        "model_reference": model,
        "target": target,
    }
    if row:
        old = json.loads(row[0])
        require(
            all(old.get(k) == v for k, v in identity.items()),
            "WORKFLOW_ID_CONFLICT",
            "任务ID已用于另一请求",
        )
        return old
    _archive_invalid_tasks(db, project, d, p, model or MODEL_REFERENCE.get())
    pending = db.execute(
        "SELECT count(*) FROM tasks WHERE project=? AND json_extract(data,'$.status') IN ('pending','claimed')",
        (project,),
    ).fetchone()[0]
    require(pending < 50, "WORKFLOW_QUEUE_FULL", "待处理任务过多")
    task = {
        **identity,
        "request_id": request_id,
        "requirements_snapshot": deepcopy(d),
        "status": "pending",
        "version": 1,
        "created_at": time.time(),
        "worker_id": None,
        "lease_until": None,
        "progress": "",
        "result_text": "",
        "result_proposal_hash": None,
    }
    _put_task(db, task)
    return task


def submit_intent(
    project,
    expected,
    request_id,
    kind,
    user_text,
    model=None,
    target=None,
    expected_model=None,
):
    with transaction() as db:
        check_context(db, project, expected)
        d = _draft(db, project)
        p = _proposal(project)
        if kind == "initial_proposal":
            from tools.system_tools import zemax_audit_requirements

            result = zemax_audit_requirements(
                system_type=d["system_type"],
                specs=d["specs"],
                user_prompt=d["user_prompt"],
            )
            d["audit"] = {
                "revision": d["revision"],
                "result": result,
                "at": time.time(),
            }
            _save(db, project, d)
            if not result["is_complete"]:
                return view(project, model)
            d["submitted_revision"] = d["revision"]
            _save(db, project, d)
        elif kind == "proposal_revision":
            require(
                _binding_valid(d, p),
                "WORKFLOW_PROPOSAL_UNBOUND",
                "请先生成当前需求的提案",
            )
            revoke(project, "changes_requested")
            d["approval"] = None
            d["authorization_revision"] = d.get("authorization_revision", 0) + 1
            _save(db, project, d)
        elif kind == "surface_question":
            require(
                isinstance(model, dict)
                and isinstance(target, dict)
                and target.get("kind") == "surface"
                and type(target.get("index")) is int
                and target["index"] >= 0,
                "WORKFLOW_TARGET_INVALID",
                "请选中真实模型表面",
            )
            require(
                model.get("model_project") == project,
                "WORKFLOW_TARGET_INVALID",
                "当前模型属于其他工程，请核对工程后提问",
            )
            require(
                model == expected_model,
                "WORKFLOW_TARGET_STALE",
                "选中表面对应的模型已变化，请重新选择",
            )
        else:
            raise WorkflowError(
                "WORKFLOW_INPUT_INVALID", "建模交接仅可由显式确认操作创建"
            )
        enqueue(
            db,
            project,
            request_id,
            kind,
            user_text,
            d,
            p,
            model if kind == "surface_question" else None,
            target,
        )
    return view(project, model)


def task_list(project, model=None):
    project_name(project)
    with transaction() as db:
        d = _draft(db, project)
        p = _proposal(project)
        _archive_invalid_tasks(db, project, d, p, model)
        return {
            "status": "success",
            "project_name": project,
            "tasks": [public_task(t, d, p, model) for t in _tasks(db, project)],
        }


def _task(db, project, request_id, version):
    project_name(project)
    text(request_id, "任务ID", 100, True)
    counter(version)
    row = db.execute(
        "SELECT data FROM tasks WHERE id=? AND project=?", (request_id, project)
    ).fetchone()
    require(row is not None, "WORKFLOW_TASK_UNKNOWN", "任务不存在")
    task = json.loads(row[0])
    require(
        task["version"] == version,
        "WORKFLOW_TASK_CONFLICT",
        "任务版本已变化，请重新读取",
    )
    return task


def claim(project, request_id, version, worker_id, lease_seconds, model=None):
    text(worker_id, "领取者", 100, True)
    require(
        type(lease_seconds) is int and 30 <= lease_seconds <= 3600,
        "WORKFLOW_INPUT_INVALID",
        "租约为30–3600秒",
    )
    with transaction() as db:
        task = _task(db, project, request_id, version)
        d = _draft(db, project)
        p = _proposal(project)
        from tools.project_manager import get_active_project_name

        require(
            get_active_project_name() == project,
            "WORKFLOW_PROJECT_MISMATCH",
            "请先切换到任务工程",
        )
        require(
            task["status"] == "pending",
            "WORKFLOW_TASK_NOT_PENDING",
            "任务已被领取或需要显式重试",
        )
        require(
            _task_valid(task, d, p, model),
            "WORKFLOW_TASK_STALE",
            "任务引用已过期，请由用户重新提交",
        )
        token = secrets.token_urlsafe(32)
        task.update(
            status="claimed",
            worker_id=worker_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            lease_until=time.time() + lease_seconds,
            version=version + 1,
        )
        _put_task(db, task)
        return {
            "status": "success",
            "task": public_task(task, d, p, model),
            "lease_token": token,
            "instruction": (
                "领取后重新核对版本、当前共享授权和模型归属，再按既有五步流程执行；领取本身不增加授权。"
                if task["kind"] == "model_handoff"
                else (
                    "重新核对模型身份、修订及目标，仅解释或建议，不自动修改参数。"
                    if task["kind"] == "surface_question"
                    else "仅研究并提交提案；领取不授权仿真。提案登记需传 workflow_context、task_claim；完成需本任务提案hash。"
                )
            ),
        }


def verify_lease(task, token):
    text(token, "领取令牌", 200, True)
    require(
        task["status"] == "claimed"
        and task["lease_until"] > time.time()
        and secrets.compare_digest(
            task.get("token_hash", ""), hashlib.sha256(token.encode()).hexdigest()
        ),
        "WORKFLOW_LEASE_INVALID",
        "领取令牌失效或租约过期，请读取状态后显式重试",
    )


def update_task(
    project,
    request_id,
    version,
    token,
    status,
    result_text="",
    result_proposal_hash=None,
    model=None,
):
    text(result_text, "进度/结果", 16000)
    require(
        status in ("progress", "completed", "failed"),
        "WORKFLOW_INPUT_INVALID",
        "未知任务状态",
    )
    with transaction() as db:
        task = _task(db, project, request_id, version)
        verify_lease(task, token)
        from tools.project_manager import get_active_project_name

        d = _draft(db, project)
        p = _proposal(project)
        require(
            get_active_project_name() == project and _task_valid(task, d, p, model),
            "WORKFLOW_TASK_STALE",
            "工程或任务引用已变化",
        )
        if status == "completed":
            if task["kind"] in ("initial_proposal", "proposal_revision"):
                require(
                    _binding_valid(d, p)
                    and p.get("research_task_id") == request_id
                    and result_proposal_hash == proposal_hash(p),
                    "WORKFLOW_RESULT_INVALID",
                    "完成研究任务必须引用该任务登记的当前提案",
                )
            else:
                text(result_text, "完成结果", 16000, True)
        elif status == "failed":
            text(result_text, "失败原因", 16000, True)
        task.update(version=version + 1, progress=result_text)
        if status != "progress":
            task.update(
                status=status,
                result_text=result_text,
                result_proposal_hash=result_proposal_hash,
                finished_at=time.time(),
            )
        _put_task(db, task)
        return {"status": "success", "task": public_task(task, d, p, model)}


def retry_task(project, expected, request_id, version, model=None):
    with transaction() as db:
        check_context(db, project, expected)
        task = _task(db, project, request_id, version)
        require(
            task["status"] == "failed"
            or task["status"] == "claimed"
            and task["lease_until"] <= time.time(),
            "WORKFLOW_RETRY_INVALID",
            "仅失败或租约过期任务可显式重试",
        )
        require(
            _task_valid(task, _draft(db, project), _proposal(project), model),
            "WORKFLOW_TASK_STALE",
            "引用过期，请更新需求并重新提交",
        )
        _archive_invalid_tasks(
            db, project, _draft(db, project), _proposal(project), model
        )
        if task["status"] == "failed":
            pending = db.execute(
                "SELECT count(*) FROM tasks WHERE project=? AND json_extract(data,'$.status') IN ('pending','claimed')",
                (project,),
            ).fetchone()[0]
            require(
                pending < 50,
                "WORKFLOW_QUEUE_FULL",
                "待处理任务过多，请先处理当前有效任务",
            )
        task.update(
            status="pending",
            version=version + 1,
            worker_id=None,
            lease_until=None,
            progress="用户请求重试",
        )
        task.pop("token_hash", None)
        _put_task(db, task)
    return view(project, model)


CONVERSATION = "conversation"


def conversational(d):
    """A draft that never existed, or was recorded from an Agent conversation."""
    return not d["revision"] or d.get("origin") == CONVERSATION


def _template_for(specs):
    from tools.system_tools import SYSTEM_SPEC_TEMPLATES

    def overlap(item):
        return len({f["key"] for f in item[1]["mandatory"]} & set(specs))

    best = max(SYSTEM_SPEC_TEMPLATES.items(), key=overlap)
    return (best[0] if overlap(best) else "imaging_lens"), SYSTEM_SPEC_TEMPLATES


def conversation_requirements(d, target_specs):
    """Draft for a proposal registered from a conversation, or None.

    Only template keys with scalar values become requirements; other target_specs
    entries (gates, notes) stay in the proposal alone. None when the audit is not
    complete, so registration keeps its earlier unbound behaviour.
    """
    from tools.system_tools import zemax_audit_requirements

    if not isinstance(target_specs, dict):
        return None
    system_type, templates = _template_for(target_specs)
    keys = {f["key"] for g in ("mandatory", "recommended") for f in templates[system_type][g]}
    specs = {k: v for k, v in target_specs.items()
             if k in keys and v not in (None, "") and not isinstance(v, (dict, list, bool))}
    result = zemax_audit_requirements(system_type=system_type, specs=specs)
    if not isinstance(result, dict) or result.get("is_complete") is not True:
        return None
    updated = deepcopy(d)
    if not d["revision"] or d.get("system_type") != system_type or d.get("specs") != specs:
        updated.update(system_type=system_type, specs=specs, sources={k: "user" for k in specs},
                       user_prompt="", constraints="", revision=d["revision"] + 1, approval=None)
    updated.update(origin=CONVERSATION,
                   audit={"revision": updated["revision"], "result": result, "at": time.time()},
                   submitted_revision=updated["revision"])
    return updated


def live_handoff(db, project, d, p):
    """The current, valid model_handoff task for this approval, if any."""
    for task in _tasks(db, project):
        if (task["kind"] == "model_handoff" and task["status"] in ("pending", "claimed")
                and _task_valid(task, d, p)):
            return task
    return None


def proposal_registration(fn):
    """Bind the existing proposal writer to frozen requirements and a real claim."""
    signature = inspect.signature(fn)

    @wraps(fn)
    def wrapped(*args, **kwargs):
        a = signature.bind(*args, **kwargs)
        a.apply_defaults()
        params = a.arguments
        project = params["project_name"]
        from tools.project_manager import sanitize_project_name, _atomic_json

        project = sanitize_project_name(project)
        with transaction() as db:
            d = _draft(db, project)
            old = _proposal(project)
            binding = {}
            conversation = None
            _check_project_writes(project)
            if conversational(d):
                # Agent conversations have no claimed research task: record the audited
                # requirements so the workbench shows this proposal and its approval.
                conversation = conversation_requirements(d, params["target_specs"])
                if conversation is not None:
                    binding = {
                        "requirements_revision": conversation["revision"],
                        "requirements_snapshot": conversation,
                        "research_task_id": None,
                    }
            else:
                check_context(db, project, params.get("workflow_context"))
                require(
                    _ready(d) and d["submitted_revision"] == d["revision"],
                    "WORKFLOW_REQUIREMENTS_UNAUDITED",
                    "需先提交审查合格的需求",
                )
                require(
                    params["user_confirmed_to_simulate"] is False,
                    "WORKFLOW_CONFIRM_SEPARATELY",
                    "新提案须由用户单独确认",
                )
                proof = params.get("task_claim") or {}
                task = _task(db, project, proof.get("request_id"), proof.get("version"))
                verify_lease(task, proof.get("lease_token"))
                require(
                    task["kind"] in ("initial_proposal", "proposal_revision")
                    and _task_valid(task, d, old, MODEL_REFERENCE.get()),
                    "WORKFLOW_TASK_STALE",
                    "请领取当前需求的研究任务",
                )
                require(
                    params["target_specs"] == d["audit"]["result"]["recognized_specs"],
                    "WORKFLOW_SPECS_MISMATCH",
                    "提案目标必须与本次审查规格一致",
                )
                for key in (
                    "initial_structure_source",
                    "optical_theory_analysis",
                    "glass_selection_rationale",
                    "merit_function_strategy",
                    "mechanical_constraints",
                    "internal_air_spacing_budget",
                    "topology",
                    "risks",
                ):
                    text(params.get(key), key, 16000, True)
                binding = {
                    "requirements_revision": d["revision"],
                    "requirements_snapshot": d,
                    "research_task_id": task["request_id"],
                }
            result = fn(*args, **kwargs)
            if result.get("status") == "success":
                p = _proposal(project)
                p.update(
                    binding,
                    proposal_revision=(old or {}).get("proposal_revision", 0) + 1,
                )
                p["proposal_hash"] = proposal_hash(p)
                _atomic_json(str(_path(project) / "design_proposal.json"), p)
                if conversation is not None:
                    # A proposal registered as already confirmed keeps that approval.
                    approved = params["user_confirmed_to_simulate"] is True
                    conversation["approval"] = p["proposal_hash"] if approved else None
                    if approved:
                        conversation["authorization_revision"] = conversation.get("authorization_revision", 0) + 1
                    _save(db, project, conversation)
                result.update(
                    proposal_revision=p["proposal_revision"],
                    proposal_hash=p["proposal_hash"],
                    requirements_revision=p.get("requirements_revision"),
                    workbench_record=(
                        "已写入工作台工程记录（需求与本版提案）" if conversation is not None
                        else "未写入工作台工程记录：需求审查未通过，或该工程由工作台领取流程管理"),
                )
            return result

    return wrapped


def validate_confirmation(project, proposal, require_approval=False):
    with transaction() as db:
        d = _draft(db, project)
        if d["revision"]:
            require(
                _binding_valid(d, proposal),
                "WORKFLOW_PROPOSAL_STALE",
                "需求或提案已变化，需重新审查并登记提案",
            )
            if require_approval:
                require(
                    d.get("approval") == proposal_hash(proposal),
                    "WORKFLOW_APPROVAL_STALE",
                    "本版方案尚未确认",
                )
            tasks = _tasks(db, project)
            require(
                not any(
                    t["kind"] == "proposal_revision"
                    and t["status"] in ("pending", "claimed")
                    and _task_valid(t, d, proposal)
                    and proposal.get("research_task_id") != t["request_id"]
                    for t in tasks
                ),
                "WORKFLOW_CHANGES_PENDING",
                "仍有待处理的修改请求",
            )


def proposal_confirmation(fn):
    """Hold the same cross-process lock through checking AND writing approval.

    Bound approvals need committed DB evidence as well as the shared JSON flag.
    A crash/failed commit after a JSON write consequently fails closed.
    """
    signature = inspect.signature(fn)

    @wraps(fn)
    def wrapped(*args, **kwargs):
        params = signature.bind(*args, **kwargs)
        params.apply_defaults()
        from tools.project_manager import get_active_project_name

        project = params.arguments["project_name"] or get_active_project_name()
        approving = params.arguments["user_confirmed_to_simulate"]
        conversation_call = params.arguments.get("workflow_context") is None
        with transaction() as db:
            d = _draft(db, project)
            if d["revision"] and approving and not (conversational(d) and conversation_call):
                check_context(db, project, params.arguments.get("workflow_context"))
            result = fn(*args, **kwargs)
            if result.get("status") == "success" and d["revision"]:
                d["approval"] = proposal_hash(_proposal(project)) if approving else None
                d["authorization_revision"] = d.get("authorization_revision", 0) + 1
                _save(db, project, d)
                if approving and conversation_call and conversational(d):
                    result["model_handoff"] = _conversation_handoff(db, project)
            return result

    return wrapped


HANDOFF_WORKER = "conversation-agent"
HANDOFF_LEASE_S = 3600


def _conversation_handoff(db, project):
    """Create and claim the model handoff a conversation approval lacked.

    The workbench confirm button queues this task before approving; an approval
    given in a conversation did not, so milestone_start/report always failed.
    An existing valid handoff is reported, never duplicated or re-claimed.
    """
    d = _draft(db, project)
    p = _proposal(project)
    task = live_handoff(db, project, d, p)
    if task is not None:
        return {"request_id": task["request_id"], "version": task["version"], "status": task["status"],
                "note": "已有本版授权的建模交接任务；如需领取请用 zemax_claim_design_task。"}
    request_id = f"handoff-{proposal_hash(p)[:12]}-a{d.get('authorization_revision', 0)}"
    enqueue(db, project, request_id, "model_handoff",
            "对话中用户已确认本版方案；按既有五步流程建模/仿真，并用 milestone_start/report 登记四个阶段。",
            d, p)
    claimed = claim(project, request_id, 1, HANDOFF_WORKER, HANDOFF_LEASE_S)
    return {"request_id": request_id, "version": claimed["task"]["version"],
            "lease_token": claimed["lease_token"], "lease_seconds": HANDOFF_LEASE_S,
            "status": "claimed",
            "instruction": ("用 zemax_design_workflow(action='milestone_start'/'milestone_report') 登记每个阶段，"
                            "payload 带 request_id、version、lease_token；租约到期后需在工作台重试。")}


def confirm(project, expected, request_id, model=None):
    from tools.system_tools import zemax_confirm_design_proposal

    with transaction() as db:
        check_context(db, project, expected)
        d = _draft(db, project)
        p = _proposal(project)
        require(
            _binding_valid(d, p),
            "WORKFLOW_PROPOSAL_UNBOUND",
            "旧提案未绑定当前需求，不能从此界面确认",
        )
        # Validate/enqueue first, so invalid IDs or full queues cannot leave an
        # approval behind. No native session is observed by confirmation.
        enqueue(
            db,
            project,
            request_id,
            "model_handoff",
            "用户已确认本版方案；领取后重新核对版本与授权，再按既有五步流程建模/仿真。",
            d,
            p,
        )
        result = zemax_confirm_design_proposal(
            project_name=project,
            user_confirmed_to_simulate=True,
            workflow_context=expected,
        )
        require(
            result.get("status") == "success",
            result.get("code", "WORKFLOW_CONFIRM_FAILED"),
            result.get("message", "确认失败"),
        )
    return view(project, model)
