"""Neutral MCP entry points for persistent visual design collaboration.

No function here opens a native session. Human permission for an actual optical
proposal is still required before a caller invokes the explicit confirm action.
"""

from typing import Any, Dict, Optional
from core import design_workflow as flow
from core.operation_guard import serialized_operation


@serialized_operation
def zemax_design_workflow(
    action: str = "view",
    project_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    expected_workflow: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """View/select project, save/audit/submit requirements, request changes or confirm.

    Always view first and pass its exact context as expected_workflow on writes.
    select: {create}; save: {system_type,specs,sources,user_prompt,constraints};
    audit: {}; submit: {request_id,kind,user_text,target?};
    confirm: {request_id} explicitly approves this version AND queues model handoff;
    retry: {request_id,version}. Confirm ONLY after explicit human approval.
    milestone_start: {request_id,version,lease_token,stage_id,attempt_id,note};
    milestone_report: {request_id,version,lease_token,attempt_id,status,operation_ids,note,
    archive_geometry?,quality_result_id?}.
    quality_result_id is an explicit existing analysis result_id, default null; it must
    be computed after the stage started on the reported model revision. Never automatic.
    archive_geometry is an explicit boolean, default false; true archives the last
    exact cached geometry for needs_review, without running native tools.
    milestone_artifact / milestone_quality_artifact: {source:{service_instance_id,
    project_name},artifact_id} read an immutable stage geometry / quality artifact;
    no proposal authorization or model is required.
    Milestone status is needs_review/failed, never optical acceptance or execution.
    submit kinds: initial_proposal, proposal_revision, surface_question.
    A pending task has not been delivered to or accepted by any Agent host.
    """
    flow.require(
        payload is None or isinstance(payload, dict),
        "WORKFLOW_INPUT_INVALID",
        "payload 必须是对象",
    )
    payload = payload or {}
    flow.encoded(payload)
    model = flow.MODEL_REFERENCE.get()
    if action == "milestone_artifact":
        from core.design_milestones import OBSERVATION
        from core.milestone_geometry_artifacts import read
        return read(project_name, payload, OBSERVATION.get())
    if action == "milestone_quality_artifact":
        from core.design_milestones import OBSERVATION
        from core.milestone_quality_artifacts import read
        return read(project_name, payload, OBSERVATION.get())
    if action == "view":
        flow.require(not payload, "WORKFLOW_INPUT_INVALID", "view 不接受写入参数")
        return flow.view(project_name, model)
    flow.project_name(project_name)
    if action == "select":
        return flow.select_project(project_name, expected_workflow, **payload)
    if action == "save":
        return flow.save_requirements(project_name, expected_workflow, **payload)
    if action == "audit":
        flow.require(not payload, "WORKFLOW_INPUT_INVALID", "audit 不接受额外参数")
        return flow.audit(project_name, expected_workflow)
    if action == "submit":
        return flow.submit_intent(
            project_name, expected_workflow, model=model, **payload
        )
    if action == "confirm":
        return flow.confirm(project_name, expected_workflow, model=model, **payload)
    if action == "retry":
        return flow.retry_task(project_name, expected_workflow, model=model, **payload)
    if action in {"milestone_start", "milestone_report"}:
        from core.design_milestones import start, report
        function = start if action == "milestone_start" else report
        return function(project_name, expected_workflow, model=model, **payload)
    raise flow.WorkflowError("WORKFLOW_INPUT_INVALID", "未知流程操作")


@serialized_operation
def zemax_list_design_tasks(
    project_name: str, history: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """List current tasks, or explicitly page older terminal tasks read-only.

    history: {source:{service_instance_id,project_name},limit?:1..50,cursor?:str}.
    Omit history for the existing current list. History needs trusted host context;
    it fixes an insertion boundary, not terminal membership across page requests.
    """
    if history is None:
        return flow.task_list(project_name, flow.MODEL_REFERENCE.get())
    from core import design_task_history
    from core.design_milestones import OBSERVATION
    source = design_task_history.trusted_source(OBSERVATION.get())
    return design_task_history.list_history(project_name, history, source, flow.MODEL_REFERENCE.get())


@serialized_operation
def zemax_claim_design_task(
    project_name: str,
    request_id: str,
    version: int,
    worker_id: str,
    lease_seconds: int = 300,
) -> Dict[str, Any]:
    """Atomically claim one pending task; returns one secret lease token.

    Never treat the claim as simulation authorization. Inspect frozen requirements,
    research sources and submit a proposal using the task_claim and workflow_context.
    Disconnected/expired claims require an explicit user retry, never blind replay.
    """
    return flow.claim(
        project_name,
        request_id,
        version,
        worker_id,
        lease_seconds,
        flow.MODEL_REFERENCE.get(),
    )


@serialized_operation
def zemax_update_design_task(
    project_name: str,
    request_id: str,
    version: int,
    lease_token: str,
    status: str,
    result_text: str = "",
    result_proposal_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """Report progress, completed or failed with the exact claim token and version.

    Research completion requires the hash of a real proposal registered for this
    task. Explanations/handoffs require text. Progress does not silently renew leases.
    """
    return flow.update_task(
        project_name,
        request_id,
        version,
        lease_token,
        status,
        result_text,
        result_proposal_hash,
        flow.MODEL_REFERENCE.get(),
    )
