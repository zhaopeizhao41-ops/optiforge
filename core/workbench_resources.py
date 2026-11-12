"""MCP resources for the workbench and their change notifications.

Hosts can attach these as context (for example an @-mention) and subscribe to
updates. Every read goes through the same read-only control path as
optiforge_workbench_view: published state, cached results, hash-checked
archives. Nothing here queues optical work or changes authorization.

Change notifications: a watcher long-polls the workbench event stream and
publishes ResourceUpdated for the resources an event can affect, once per
batch. Events are hints; readers always re-read the current resource.
"""
import json
import logging

from core.workbench_control import WorkbenchControlClient, WorkbenchControlError, public_control_error
from core.workbench_view import ViewError, analysis_view, layout_view, stages_view

logger = logging.getLogger(__name__)
BASE = "optiforge://workbench/"
STATUS, MODEL, STAGES, ANALYSIS = (BASE + name for name in ("status", "model", "stages", "analysis"))
LAYOUT_PNG, STAGES_PNG, ANALYSIS_PNG = (BASE + name for name in ("layout.png", "stages.png", "analysis.png"))
ALL = (STATUS, MODEL, STAGES, ANALYSIS, LAYOUT_PNG, STAGES_PNG, ANALYSIS_PNG)
# Which resources an event kind can change. Model changes always arrive as
# state_changed; a finished operation only adds what its own tool produces.
EVENT_RESOURCES = {
    "state_changed": (STATUS, MODEL, LAYOUT_PNG),
    "queued": (STATUS,), "started": (STATUS,), "finished": (STATUS,),
    "task_changed": (STATUS, STAGES, STAGES_PNG),
    "analysis_changed": (STATUS, ANALYSIS, ANALYSIS_PNG),
    "trace_finished": (LAYOUT_PNG,), "trace_changed": (LAYOUT_PNG,),
}
FINISHED_TOOL_RESOURCES = {
    "__analysis__": (ANALYSIS, ANALYSIS_PNG), "__trace__": (LAYOUT_PNG,),
    "zemax_design_workflow": (STAGES, STAGES_PNG), "zemax_claim_design_task": (STAGES,),
    "zemax_update_design_task": (STAGES,),
}
COALESCE_S = 0.3


def uris_for_events(events, reset=False):
    """Ordered, de-duplicated resource URIs affected by one event batch."""
    if reset:
        return list(ALL)
    affected = []
    for event in events or []:
        kind = event.get("kind") if isinstance(event, dict) else None
        uris = EVENT_RESOURCES.get(kind, (STATUS,))
        if kind == "finished":
            uris = uris + FINISHED_TOOL_RESOURCES.get(event.get("tool"), ())
        for uri in uris:
            if uri not in affected:
                affected.append(uri)
    return affected


def _client():
    return WorkbenchControlClient.from_environment()


def _error(error):
    if isinstance(error, WorkbenchControlError):
        return public_control_error(error)
    return {"status": "error", "code": error.code, "message": error.message}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def status_resource():
    """Service, model, frontends, running work and the workflow's pending tasks."""
    try:
        client = _client()
        state = client.read_json("/api/state")
        status = client.status()
        snapshot = state.get("snapshot") or {}
        model = snapshot.get("model") or {}
        project = state.get("active_project")
        result = {
            "status": "success", "service_instance_id": state.get("service_instance_id"),
            "workbench_url": client.url + "/", "active_project": project,
            "model": {"name": model.get("name"), "revision": state.get("model_revision"),
                      "configuration": model.get("configuration"), "validity": state.get("validity"),
                      "proposal_confirmed": state.get("proposal_confirmed")} if snapshot else None,
            "frontends": [{"ui_session_id": f.get("ui_session_id"), "page": f.get("page"), "online": f.get("online")}
                          for f in status.get("frontends", [])],
            "running": [{"tool": task.get("tool"), "status": task.get("status")}
                        for task in (state.get("tasks") or {}).get("pending", [])],
            "workflow": None, "resources": list(ALL),
            "note": "只读状态；事件仅为提示，请以重新读取为准。确认提案只能由用户在工作台完成。",
        }
        if project and (status.get("capabilities") or {}).get("collaboration"):
            view = (client.read_tool(project, "view").get("result") or {})
            if view.get("status") == "success":
                result["workflow"] = {"stage": view.get("stage"), "tasks": [
                    {key: task.get(key) for key in ("request_id", "kind", "status", "version", "stale")}
                    for task in view.get("tasks", []) if task.get("status") in {"pending", "claimed", "failed"}]}
        return _dump(result)
    except (WorkbenchControlError, ViewError) as error:
        return _dump(_error(error))


def _summary(kind):
    try:
        client = _client()
        state = client.read_json("/api/state")
        url = client.url + "/"
        if kind == "model":
            summary, _ = layout_view(client, state, url)
        elif kind == "analysis":
            summary, _ = analysis_view(client, state, url)
        else:
            project = state.get("active_project")
            if not project:
                raise ViewError("WORKFLOW_PROJECT_INVALID", "当前没有活动工程")
            summary, _ = stages_view(client, state, url, project)
        return _dump({"status": "success", **summary})
    except (WorkbenchControlError, ViewError) as error:
        return _dump(_error(error))


def model_resource():
    return _summary("model")


def stages_resource():
    return _summary("stages")


def analysis_resource():
    return _summary("analysis")


def _placeholder(message):
    from core.workbench_view import MUTED, _figure, _png
    figure = _figure(6, 1.4)
    axis = figure.add_subplot()
    axis.axis("off")
    axis.text(0.5, 0.5, message, ha="center", va="center", fontsize=11, color=MUTED, wrap=True)
    return _png(figure)


def picture(view):
    """PNG bytes for layout / stages / analysis; a labelled placeholder when unavailable."""
    from core.workbench_view import render
    try:
        client = _client()
        summary, image = render(client, client.instance, view)
        return image or _placeholder(summary.get("message") or "暂无可显示的内容")
    except (WorkbenchControlError, ViewError) as error:
        return _placeholder(_error(error)["message"])


class ResourceWatcher:
    """Long-poll /api/events and publish ResourceUpdated for affected URIs."""

    def __init__(self, bus, client_factory=_client, timeout_ms=20000, retry_s=5.0):
        self.bus, self.client_factory = bus, client_factory
        self.timeout_ms, self.retry_s = timeout_ms, retry_s
        self.published = []  # bounded record for diagnostics/tests

    async def publish(self, uris):
        from mcp.server.subscriptions import ResourceUpdated
        for uri in uris:
            await self.bus.publish(ResourceUpdated(uri=uri))
        self.published = (self.published + list(uris))[-200:]

    async def run(self):
        import anyio
        cursor, instance, client = 0, None, None
        while True:
            try:
                if client is None:
                    client = await anyio.to_thread.run_sync(self.client_factory)
                    # Start from the current cursor: connecting is not a change.
                    start = await anyio.to_thread.run_sync(client.events, client.instance, 0, 0)
                    if instance not in (None, client.instance):
                        await self.publish(list(ALL))  # a new service: everything changed
                    instance, cursor = client.instance, start.get("next_seq", 0)
                batch = await anyio.to_thread.run_sync(
                    client.events, client.instance, cursor, self.timeout_ms, abandon_on_cancel=True)
                if batch.get("closed"):
                    client = None
                    await anyio.sleep(self.retry_s)
                    continue
                events, reset = list(batch.get("events") or []), bool(batch.get("reset_required"))
                cursor = batch.get("next_seq", cursor)
                if events and not reset:
                    # One operation emits queued/started/finished/state_changed in quick
                    # succession; gather them so each resource is announced once.
                    await anyio.sleep(COALESCE_S)
                    more = await anyio.to_thread.run_sync(client.events, client.instance, cursor, 0)
                    events += more.get("events") or []
                    reset = bool(more.get("reset_required"))
                    cursor = more.get("next_seq", cursor)
                uris = uris_for_events(events, reset)
                if uris:
                    await self.publish(uris)
            except WorkbenchControlError as error:
                # Not configured, offline or restarted: keep trying quietly; resources still read on demand.
                logger.debug("workbench watcher: %s", error.code)
                client = None
                await anyio.sleep(self.retry_s)
