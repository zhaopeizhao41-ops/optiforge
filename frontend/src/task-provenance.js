const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = value => typeof value === 'string' && value.trim().length > 0;
const count = (value, minimum = 0) => Number.isSafeInteger(value) && value >= minimum;
const kinds = ['initial_proposal', 'proposal_revision', 'surface_question', 'model_handoff'];
const statuses = ['pending', 'claimed', 'completed', 'failed', 'superseded'];
const phases = ['synced', 'running', 'unavailable'];
const referenceKeys = ['service_instance_id', 'model_id', 'model_revision', 'model_project', 'configuration'];
const stringKeys = ['service_instance_id', 'model_id', 'model_project'];
const scopeConnected = sync => sync?.transportConnected === true && phases.includes(sync.phase)
  && text(sync.engine?.service_instance_id) && text(sync.engine?.active_project);

export function taskPane(flow, sync) {
  const hidden = (status, message) => ({ status, tasks: [], message });
  if (!scopeConnected(sync) || sync.canCollaborate !== true) {
    return hidden('offline', '连接或工程来源已变化，任务暂不显示。');
  }
  if (flow?.error) return hidden('unavailable', '无法读取任务记录，请重新读取工程流程。');
  const view = flow?.view, engine = sync.engine;
  if (!object(view)) return hidden('unavailable', '正在读取当前工程的任务记录。');
  if (view.service_instance_id !== engine.service_instance_id || view.project_name !== engine.active_project
      || view.context?.project_name !== engine.active_project || view.context?.active_project !== engine.active_project) {
    return hidden('offline', '连接或工程来源已变化，任务暂不显示。');
  }
  if (!Array.isArray(view.tasks) || new Set(view.tasks.map(task => task?.request_id)).size !== view.tasks.length
      || !view.tasks.every(task => object(task) && text(task.request_id) && task.project_name === view.project_name
        && kinds.includes(task.kind) && statuses.includes(task.status) && typeof task.stale === 'boolean'
        && typeof task.user_text === 'string')) {
    return hidden('unavailable', '无法读取任务记录，请重新读取工程流程。');
  }
  return { status: 'ready', tasks: view.tasks, message: '' };
}

function validReference(source) {
  return object(source) && stringKeys.every(key => text(source[key]))
    && count(source.model_revision, 1) && count(source.configuration, 1);
}
function recordedReference(value) {
  return Object.fromEntries(referenceKeys.map(key => [key, stringKeys.includes(key)
    ? text(value?.[key]) ? value[key] : null : count(value?.[key], 1) ? value[key] : null]));
}
function observedReference(sync) {
  const engine = sync?.engine, snapshot = engine?.snapshot, model = snapshot?.model;
  if (!scopeConnected(sync) || engine.validity !== 'valid' || engine.context_valid !== true
      || snapshot?.status !== 'success' || snapshot.schema_version !== 1 || !object(model)
      || !text(engine.model_id) || !text(engine.model_project) || !count(engine.model_revision, 1)
      || !count(engine.state_version) || !count(model.configuration, 1) || model.project !== engine.model_project) return null;
  const identity = ['service_instance_id', 'model_id', 'model_revision', 'active_project', 'model_project', 'state_version'];
  if (![engine.snapshot_context, snapshot.sync].every(context => object(context)
      && identity.every(key => context[key] === engine[key]))) return null;
  return { service_instance_id: engine.service_instance_id, model_id: engine.model_id, model_revision: engine.model_revision,
    model_project: engine.model_project, configuration: model.configuration };
}

export function taskProvenance(task, sync, project) {
  const source = recordedReference(task?.model_reference), target = task?.target;
  const recordedTarget = object(target) && target.kind === 'surface' && count(target.index);
  const noReference = task?.model_reference == null && ['initial_proposal', 'proposal_revision', 'model_handoff'].includes(task?.kind);
  const reasons = [], original = task?.model_reference;
  let status = 'unknown', label = '当前无法核对模型引用';
  if (noReference) { status = 'unbound'; label = '未绑定模型'; }
  else if (validReference(original) && (task?.kind !== 'surface_question' || recordedTarget)) {
    // Service/project identity is known with live HTTP even without native geometry.
    if (text(task?.project_name) && original.model_project !== task.project_name) reasons.push('记录的模型工程与任务工程不同');
    if (text(project) && task?.project_name !== project) reasons.push('任务工程与当前工程不同');
    if (scopeConnected(sync)) {
      if (original.service_instance_id !== sync.engine.service_instance_id) reasons.push('服务实例已变化');
      if (original.model_project !== sync.engine.active_project) reasons.push('活动工程已变化');
      const current = observedReference(sync);
      if (current) {
        if (original.model_id !== current.model_id) reasons.push('模型身份已变化');
        if (original.model_project !== current.model_project) reasons.push('模型工程已变化');
        if (original.model_revision !== current.model_revision) reasons.push('模型修订已变化');
        if (original.configuration !== current.configuration) reasons.push('模型配置已变化');
        if (!reasons.length) { status = 'matching'; label = '引用与当前模型一致'; }
      }
    }
    if (reasons.length) { status = 'historical'; label = '历史模型引用'; }
  }
  const validity = task?.status === 'superseded' ? '任务已归档' : task?.stale === true ? '任务引用已过期' : null;
  return {
    target: recordedTarget ? `S${target.index}` : task?.kind === 'surface_question' ? '目标表面未记录或无效' : '未绑定具体表面',
    project: text(task?.project_name) ? task.project_name : '工程未记录',
    revision: source.model_revision, configuration: source.configuration, source,
    relation: { status, label, reasons }, validity,
  };
}

export function taskMessage(task) {
  if (!object(task) || !statuses.includes(task.status)) return null;
  const preferred = text(task.result_text) ? task.result_text : text(task.progress) ? task.progress : null;
  const historical = task.stale === true || task.status === 'superseded';
  if (task.status === 'claimed' && text(task.progress)) return { label: 'Agent 进度', text: task.progress, historical };
  if (task.status === 'completed' && preferred) return {
    label: task.kind === 'surface_question' ? 'Agent 回复' : '任务结果', text: preferred, historical,
  };
  if (task.status === 'failed' && preferred) return { label: 'Agent 失败报告', text: preferred, historical };
  if (task.status === 'superseded' && preferred) return { label: '归档记录', text: text(task.progress) ? task.progress : preferred, historical: true };
  return null;
}
