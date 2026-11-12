import { validateGeometryArtifactReference } from './stage-geometry-artifact.js';
import { validateQualityArtifactReference } from './stage-quality-artifact.js';

export const MILESTONE_STAGES = [
  { id: 'stage_1', label: '基础曲率' },
  { id: 'stage_2', label: '厚度与边界' },
  { id: 'stage_3', label: '波前与材料' },
  { id: 'stage_4', label: '深度寻优与公差' },
];
export const MILESTONE_STATUS = {
  not_started: '未开始', running: '记录中', needs_review: '待核对', failed: '失败', stale: '来源过期',
};
export const MILESTONE_REASONS = {
  observation_unavailable: '当前没有可核对的模型观察', inactive_project: '活动工程已变化',
  service_instance_changed: '服务实例已变化', model_changed: '模型已变化',
  configuration_changed: '配置已变化', requirements_changed: '需求版本已变化',
  proposal_changed: '提案版本已变化', authorization_changed: '授权版本已变化',
  approval_revoked: '本版方案授权已撤销', claim_changed: '任务领取已变化',
  lease_expired: '领取租约已过期', handoff_inactive: '交接任务已失效',
  model_revision_changed: '模型修订已变化',
};
// A finished stage whose only stale reason is a later model change is the normal
// progression of a staged optimization; label it as superseded, not broken.
export function milestoneStatusLabel(attempt) {
  if (attempt?.status === 'stale' && attempt.declared_status !== 'running'
      && attempt.stale_reasons?.length === 1 && attempt.stale_reasons[0] === 'model_revision_changed') {
    return `${MILESTONE_STATUS[attempt.declared_status]} · 已被后续修改`;
  }
  return MILESTONE_STATUS[attempt?.status];
}
const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = value => typeof value === 'string' && value.trim().length > 0;
const count = (value, min = 0) => Number.isSafeInteger(value) && value >= min;
const timestamp = value => text(value) && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const declaredStates = ['running', 'needs_review', 'failed'];
const recordedStates = [...declaredStates, 'stale'];
const nativeKeys = ['service_instance_id', 'model_id', 'model_revision', 'active_project', 'model_project'];
const workflowKeys = ['requirements_revision', 'proposal_hash', 'proposal_revision', 'authorization_revision'];

function validNativeSource(source) {
  return object(source) && text(source.service_instance_id) && text(source.model_id)
    && text(source.active_project) && source.model_project === source.active_project
    && count(source.model_revision, 1) && count(source.configuration, 1) && count(source.source_state_version);
}
function validSource(source, project) {
  return validNativeSource(source) && source.project_name === project && source.active_project === project
    && count(source.requirements_revision, 1) && text(source.proposal_hash)
    && count(source.proposal_revision, 1) && count(source.authorization_revision, 1);
}
function validOperation(operation) {
  return object(operation) && text(operation.operation_id) && text(operation.tool)
    && timestamp(operation.queued_at) && timestamp(operation.finished_at)
    && ['succeeded', 'failed', 'rejected'].includes(operation.status)
    && (operation.result_status === null || text(operation.result_status))
    && (operation.observed_source === null || validNativeSource(operation.observed_source))
    && (operation.rolled_back === undefined || typeof operation.rolled_back === 'boolean')
    && (operation.view_error == null || typeof operation.view_error === 'string')
    && (operation.code == null || typeof operation.code === 'string');
}
function sameModel(a, b) {
  return ['service_instance_id', 'model_id', 'active_project', 'model_project', 'configuration'].every(key => a[key] === b[key]);
}
function validAttempt(attempt, stage, project) {
  if (!object(attempt) || !text(attempt.id) || attempt.stage_id !== stage || !text(attempt.task_id)
      || !declaredStates.includes(attempt.declared_status) || !recordedStates.includes(attempt.status)
      || attempt.status !== 'stale' && attempt.status !== attempt.declared_status
      || !Array.isArray(attempt.stale_reasons) || attempt.stale_reasons.length > 32
      || !attempt.stale_reasons.every(text) || (attempt.status === 'stale') !== Boolean(attempt.stale_reasons.length)
      || !timestamp(attempt.started_at) || !validSource(attempt.source, project)
      || !text(attempt.worker_id) || !text(attempt.note) || Array.from(attempt.note).length > 2000
      || !Array.isArray(attempt.operations) || attempt.operations.length > 12
      || !attempt.operations.every(validOperation)
      || new Set(attempt.operations.map(operation => operation.operation_id)).size !== attempt.operations.length) return false;
  if (Object.hasOwn(attempt, 'geometry_artifact')) {
    try { validateGeometryArtifactReference(attempt.geometry_artifact, attempt, project); }
    catch { return false; }
  }
  if (Object.hasOwn(attempt, 'quality_artifact')) {
    try { validateQualityArtifactReference(attempt.quality_artifact, attempt, project); }
    catch { return false; }
  }
  if (attempt.declared_status === 'running') return attempt.reported_at === null
    && attempt.end_source === null && attempt.operations.length === 0;
  if (!timestamp(attempt.reported_at) || Date.parse(attempt.reported_at) < Date.parse(attempt.started_at)
      || attempt.end_source !== null && !validSource(attempt.end_source, project)) return false;
  if (attempt.declared_status === 'needs_review') return attempt.operations.length > 0 && attempt.end_source !== null
    && attempt.operations.every(operation => operation.status === 'succeeded' && operation.result_status === 'success'
      && !operation.rolled_back && !operation.view_error && operation.observed_source !== null
      && sameModel(operation.observed_source, attempt.source));
  return true;
}

// Reject an incomplete envelope as a whole; missing data never means planned or passed.
export function parseMilestones(value) {
  if (value == null) return { status: 'missing', stages: [] };
  if (!object(value) || value.schema_version !== 1 || value.storage !== 'project'
      || !text(value.service_instance_id) || !text(value.project_name)
      || !Array.isArray(value.stages) || value.stages.length !== 4
      || value.limits?.max_attempts_per_task !== 16 || value.limits?.max_operations_per_report !== 12
      || value.limits?.max_visible_attempts !== 32) return { status: 'invalid', stages: [] };
  const seen = new Set(), perTask = new Map();
  let total = 0;
  for (const [index, stage] of value.stages.entries()) {
    const expected = MILESTONE_STAGES[index];
    if (!object(stage) || stage.id !== expected.id || stage.label !== expected.label
        || !Array.isArray(stage.attempts) || stage.status !== (stage.attempts[0]?.status ?? 'not_started')) {
      return { status: 'invalid', stages: [] };
    }
    for (const attempt of stage.attempts) {
      if (!validAttempt(attempt, stage.id, value.project_name)) return { status: 'invalid', stages: [] };
      const key = JSON.stringify([attempt.task_id, attempt.id]);
      const taskCount = (perTask.get(attempt.task_id) || 0) + 1;
      if (seen.has(key) || taskCount > 16 || ++total > 32) return { status: 'invalid', stages: [] };
      seen.add(key); perTask.set(attempt.task_id, taskCount);
    }
  }
  return { status: 'ok', stages: value.stages };
}

function currentObservation(engine) {
  const snapshot = engine?.snapshot, model = snapshot?.model;
  if (engine?.validity !== 'valid' || engine.context_valid !== true || engine.error
      || snapshot?.status !== 'success' || snapshot.schema_version !== 1
      || model?.project !== engine.active_project || engine.model_project !== engine.active_project
      || !count(model.configuration, 1) || !count(engine.state_version)) return null;
  const keys = [...nativeKeys, 'state_version'];
  if (!keys.every(key => snapshot.sync?.[key] === engine[key] && engine.snapshot_context?.[key] === engine[key])) return null;
  const source = { ...Object.fromEntries(nativeKeys.map(key => [key, engine[key]])),
    configuration: model.configuration, source_state_version: engine.state_version };
  return validNativeSource(source) ? source : null;
}
function projectAttempt(attempt, view, observed) {
  const reasons = new Set(attempt.stale_reasons), source = attempt.source;
  const workflowReasons = ['requirements_changed', 'proposal_changed', 'proposal_changed', 'authorization_changed'];
  workflowKeys.forEach((key, index) => {
    if (source[key] !== view.context?.[key]) reasons.add(workflowReasons[index]);
  });
  if (view.proposal_bound === false || view.proposal?.user_confirmed_to_simulate === false) reasons.add('approval_revoked');
  if (!observed) reasons.add('observation_unavailable');
  else {
    if (source.service_instance_id !== observed.service_instance_id) reasons.add('service_instance_changed');
    if (source.model_id !== observed.model_id) reasons.add('model_changed');
    if (source.configuration !== observed.configuration) reasons.add('configuration_changed');
    if (source.active_project !== observed.active_project || source.model_project !== observed.model_project) reasons.add('inactive_project');
    if (attempt.declared_status === 'running' && observed.model_revision < source.model_revision) reasons.add('model_revision_changed');
    if (attempt.declared_status !== 'running') {
      if (!attempt.end_source) reasons.add('observation_unavailable');
      else if (attempt.end_source.model_revision !== observed.model_revision) reasons.add('model_revision_changed');
    }
  }
  return { ...attempt, status: reasons.size ? 'stale' : attempt.status, stale_reasons: [...reasons] };
}

// A synchronous render guard: a stale flow response cannot briefly appear under a new scope.
// State/source_state_version identifies an observation, not an optical revision, so it is
// checked only inside the current snapshot and is never compared against old attempts.
export function milestonePane(flow, sync) {
  const engine = sync?.engine, view = flow?.view;
  const nativeError = sync?.error && sync.error === engine?.error;
  if (!engine || sync.canCollaborate !== true || !['synced', 'running', 'unavailable'].includes(sync.phase)
      || flow?.error || sync.phase === 'unavailable' && sync.error && !nativeError
      || !text(engine.service_instance_id) || !text(engine.active_project)
      || view?.service_instance_id !== engine.service_instance_id || view.project_name !== engine.active_project
      || view.context?.project_name !== engine.active_project || view.context?.active_project !== engine.active_project) {
    return { status: 'offline', stages: [] };
  }
  const envelope = view.optimization_milestones;
  if (object(envelope) && (text(envelope.service_instance_id) && envelope.service_instance_id !== engine.service_instance_id
      || text(envelope.project_name) && envelope.project_name !== engine.active_project)) {
    return { status: 'offline', stages: [] };
  }
  const parsed = parseMilestones(envelope);
  if (parsed.status !== 'ok') return { status: 'unavailable', stages: [] };
  const observed = currentObservation(engine);
  return { status: 'ready', stages: parsed.stages.map(stage => {
    const attempts = stage.attempts.map(attempt => projectAttempt(attempt, view, observed));
    return { ...stage, attempts, status: attempts[0]?.status ?? 'not_started' };
  }) };
}
